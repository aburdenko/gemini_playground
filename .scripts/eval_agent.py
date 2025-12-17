#!/usr/bin/env python3
import argparse
import base64
import io
import json
import os
import sys
from datetime import datetime, timedelta
import glob

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import vertexai
from google.api_core import exceptions as google_exceptions
from google.cloud import aiplatform, aiplatform_v1, logging, storage
from vertexai.preview.evaluation import AutoraterConfig, CustomMetric, EvalTask

matplotlib.use("Agg")


# Add the 'rag-agent' directory to the Python path
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "agents", "rag-agent")
    ),
)


PROJECT_ID = os.environ.get("PROJECT_ID", "your-gcp-project-id")
LOCATION = os.environ.get("REGION", "us-central1")
BUCKET_NAME = os.environ.get("STAGING_GCS_BUCKET", "your-bucket-name")
SHORT_LOG_NAME = os.environ.get("LOG_NAME", "run_gemini_from_file")
EXPERIMENT_NAME = "gemini-playground-evaluation"
LOG_NAME = f"projects/{PROJECT_ID}/logs/{SHORT_LOG_NAME}"
JUDGEMENT_MODEL_NAME = os.environ.get("JUDGEMENT_MODEL_NAME", "gemini-1.5-flash")

TIMESTAMP_FILE = "last_run_timestamp.txt"


def _contains_words_metric_function(test_case: dict) -> dict:
    response = test_case.get("response", "")
    reference = test_case.get("reference", "")

    if not response or not reference:
        return {"contains_words": 0.0}

    words_to_check = [word.strip() for word in reference.split(" ") if word.strip()]
    contains_all_words = all(word in response for word in words_to_check)

    score = 1.0 if contains_all_words else 0.0
    return {"contains_words": score}


def get_last_run_timestamp():
    try:
        with open(TIMESTAMP_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"


def save_current_timestamp():
    with open(TIMESTAMP_FILE, "w") as f:
        f.write(datetime.utcnow().isoformat() + "Z")


def get_logs_for_evaluation(
    last_run_timestamp: str | None, filter_session_id: str | None = None
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    logging_client = logging.Client(project=PROJECT_ID)
    base_filter = (
        f'logName="{LOG_NAME}" AND '
        f"(jsonPayload.session_id:* OR jsonPayload.request_id:*)"
    )
    log_filter = (
        f'{base_filter} AND timestamp >= "{last_run_timestamp}"'
        if last_run_timestamp
        else base_filter
    )

    agent_sessions_raw_logs = {}
    simple_sessions_raw_logs = {}

    for entry in logging_client.list_entries(filter_=log_filter):
        payload = {}
        if hasattr(entry, "payload") and isinstance(entry.payload, dict):
            payload = entry.payload
        elif hasattr(entry, "payload") and isinstance(entry.payload, str):
            payload = {"message": entry.payload}
        elif hasattr(entry, "json_payload"):
            payload = entry.json_payload
        elif hasattr(entry, "text_payload"):
            payload = {"message": entry.text_payload}
        else:
            payload = {"message": "Unsupported log entry type"}

        session_id = payload.get("session_id")
        request_id = payload.get("request_id")

        if (
            filter_session_id
            and session_id != filter_session_id
            and request_id != filter_session_id
        ):
            continue

        if payload.get("log_type") in ["user_message", "final_answer"]:
            session_id = payload.get("session_id")
            if session_id:
                if session_id not in agent_sessions_raw_logs:
                    agent_sessions_raw_logs[session_id] = []
                agent_sessions_raw_logs[session_id].append(
                    {"entry": entry, "payload": payload}
                )
        elif request_id and "prompt" in payload and "response" in payload:
            if request_id not in simple_sessions_raw_logs:
                simple_sessions_raw_logs[request_id] = []
            simple_sessions_raw_logs[request_id].append(
                {"entry": entry, "payload": payload}
            )

    agent_sessions_data = []
    simple_sessions_data = []

    for session_id, raw_logs in agent_sessions_raw_logs.items():
        sorted_raw_logs = sorted(raw_logs, key=lambda x: x["entry"].timestamp)
        user_messages = [
            log["payload"]
            for log in sorted_raw_logs
            if log["payload"].get("log_type") == "user_message"
        ]
        final_answers = [
            log["payload"]
            for log in sorted_raw_logs
            if log["payload"].get("log_type") == "final_answer"
        ]

        for i in range(min(len(user_messages), len(final_answers))):
            user_payload = user_messages[i]
            final_answer_payload = final_answers[i]

            user_content = user_payload.get("prompt") or user_payload.get(
                "message", ""
            ).replace("ADK Web Log: Middleware triggered for prompt: ", "")
            agent_response = final_answer_payload.get("final_answer")
            reference = final_answer_payload.get("ground_truth")
            ground_truth_payload = final_answer_payload.get("ground_truth", {})
            metric_type_from_payload = ground_truth_payload.get(
                "metric_type", "default_agent_metric"
            )

            if user_content and agent_response:
                agent_sessions_data.append(
                    {
                        "eval_id": f"{session_id}-{i}",
                        "session_id": session_id,
                        "user_content": user_content,
                        "agent_response": agent_response,
                        "reference": reference,
                        "metric_type": metric_type_from_payload,
                        "metric_value": "",
                        "ground_truth": ground_truth_payload,
                    }
                )

    for request_id, raw_logs in simple_sessions_raw_logs.items():
        prompt, response, reference = "", "", ""
        sorted_raw_logs = sorted(raw_logs, key=lambda x: x["entry"].timestamp)

        for log_item in sorted_raw_logs:
            payload = log_item["payload"]
            if "prompt" in payload:
                prompt = payload["prompt"]
            if "response" in payload:
                response = payload["payload"]["response"]
            if "ground_truth" in payload:
                reference = payload["ground_truth"]

        if prompt and response:
            simple_sessions_data.append(
                {
                    "eval_id": request_id,
                    "session_id": request_id,
                    "user_content": prompt,
                    "agent_response": response,
                    "reference": reference,
                    "metric_type": "simple",
                    "metric_value": "",
                }
            )

    agent_df = pd.DataFrame(agent_sessions_data) if agent_sessions_data else None
    simple_df = pd.DataFrame(simple_sessions_data) if simple_sessions_data else None

    return agent_df, simple_df


def export_sessions_to_evalset(last_run_timestamp: str | None):
    """Fetches agent logs and exports each session to a separate .evalset.json file."""
    print("Fetching logs from Cloud Logging to export as eval sets...")
    
    agent_df, simple_df = get_logs_for_evaluation(last_run_timestamp)

    if (agent_df is None or agent_df.empty) and (
        simple_df is None or simple_df.empty
    ):
        print("No new logs found to export.")
        return

    output_dir = os.path.join(
        os.path.dirname(__file__), "..", "agents", "rag-agent", "eval_sets"
    )
    os.makedirs(output_dir, exist_ok=True)
    print(
        f"Found {len(agent_df) if agent_df is not None else 0} agent session(s) and "
        f"{len(simple_df) if simple_df is not None else 0} simple log(s). "
        f"Exporting to: {output_dir}"
    )

    if agent_df is not None and not agent_df.empty:
        for session_id, group_df in agent_df.groupby("session_id"):
            conversation_turns = []
            # Sort by eval_id to maintain turn order
            sorted_group_df = group_df.sort_values(by="eval_id")

            for _, row in sorted_group_df.iterrows():
                user_content = row["user_content"]
                agent_response = row["agent_response"]
                reference = row["reference"]

                turn = {
                    "user_content": {"parts": [{"text": user_content}]},
                    "final_response": {"parts": [{"text": agent_response}]}
                }
                if reference:
                    turn["expected_final_response"] = {"parts": [{"text": reference}]}
                conversation_turns.append(turn)
            
            # Assuming ground_truth is consistent across turns for a session, take the first one
            ground_truth_obj = (
                group_df["ground_truth"].iloc[0]
                if "ground_truth" in group_df.columns
                else {}
            )

            eval_set = {
                "eval_set_id": session_id, 
                "eval_cases": [{
                    "eval_id": f"case-{session_id}", 
                    "conversation": conversation_turns,
                    "ground_truth": ground_truth_obj
                }]
            }
            output_filename = os.path.join(
                output_dir, f"rag-agent.evalset.{session_id}.json"
            )
            with open(output_filename, "w") as f:
                json.dump(eval_set, f, indent=2)
            print(
                f"  - Successfully exported agent session {session_id} to {output_filename}"
            )

    if simple_df is not None and not simple_df.empty:
        for _, row in simple_df.iterrows():
            request_id = row["session_id"] # For simple logs, session_id is the request_id
            prompt = row["user_content"]
            response = row["agent_response"]
            reference = row["reference"]
            metric_type = row["metric_type"] # Should be "simple" now

            conversation_turns = [{
                "user_content": {"parts": [{"text": prompt}]},
                "final_response": {"parts": [{"text": response}]}
            }]

            if reference:
                conversation_turns[-1]["expected_final_response"] = {"parts": [{"text": reference}]}
            
            ground_truth_obj = {
                "reference": reference,
                "metric_type": metric_type
            }

            eval_set = {
                "eval_set_id": request_id, 
                "eval_cases": [{
                    "eval_id": f"case-{request_id}", 
                    "conversation": conversation_turns,
                    "ground_truth": ground_truth_obj
                }]
            }
            output_filename = os.path.join(
                output_dir, f"rag-agent.evalset.{request_id}.json"
            )
            with open(output_filename, "w") as f:
                json.dump(eval_set, f, indent=2)
            print(f"  - Successfully exported simple ADK web log to {output_filename}")


def generate_radar_chart(all_summary_metrics_data: list[tuple[dict, str]], current_time_str: str) -> str:
    """Generates a radar chart from multiple sets of summary metrics and returns it as a base64 PNG."""
    if not all_summary_metrics_data:
        return ""

    # Collect all unique labels (metrics) across all summary_metrics
    all_labels = set()
    for summary_metrics, _ in all_summary_metrics_data:
        for key in summary_metrics.keys():
            if "/mean" in key:
                all_labels.add(key.replace('/mean', ''))
    
    if not all_labels:
        return ""

    clean_labels = sorted(list(all_labels))
    num_vars = len(clean_labels)
    angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
    angles += angles[:1] # Complete the loop

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))

    for summary_metrics, run_name_suffix in all_summary_metrics_data:
        scores = []
        for label in clean_labels:
            score_key = f"{label}/mean"
            scores.append(summary_metrics.get(score_key, 0.0)) # Default to 0 if metric not present
        
        scores += scores[:1] # Complete the loop

        ax.plot(angles, scores, linewidth=2, linestyle='solid', label=f'Performance ({run_name_suffix})')
        ax.fill(angles, scores, alpha=0.1)

    ax.set_yticklabels([])
    ax.set_ylim(0, 1) # Ensure radial axis goes from 0 to 1
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(clean_labels)
    ax.set_title(f'Evaluation of Gemini Runs ({current_time_str})', size=12, color='black', va='bottom')
    ax.grid(True)
    ax.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1))

    pic_io = io.BytesIO()
    plt.savefig(pic_io, format='png', bbox_inches='tight', dpi=150)
    plt.close(fig)
    pic_io.seek(0)
    return base64.b64encode(pic_io.read()).decode('utf-8')

def generate_metrics_csv(metrics_df: pd.DataFrame) -> str:
    """Generates a CSV string from a metrics DataFrame."""
    return metrics_df.to_csv(index=False)

def run_evaluation_and_generate_artifacts(eval_df: pd.DataFrame | None = None, all_time: bool = False, session_id: str | None = None, eval_set_id: str | None = None):
    """
    Runs the main evaluation flow.
    Args:
        all_time: If True, fetches all logs. Otherwise, fetches logs since the last run.
    """
    current_time_str = datetime.now().strftime('%Y%m%d%H%M%S')
    experiment_name = EXPERIMENT_NAME
    aiplatform.init(project=PROJECT_ID, location=LOCATION, experiment=experiment_name)

    last_run = None
    if not all_time:
        last_run = get_last_run_timestamp()

    if eval_df is not None:
        agent_df = eval_df[eval_df['ground_truth'].apply(lambda x: x.get('metric_type') != 'simple')] if not eval_df.empty else pd.DataFrame()
        simple_df = eval_df[eval_df['ground_truth'].apply(lambda x: x.get('metric_type') == 'simple')] if not eval_df.empty else pd.DataFrame()
        if agent_df.empty:
            agent_df = None
        if simple_df.empty:
            simple_df = None
    else:
        agent_df, simple_df = get_logs_for_evaluation(last_run)
    
    artifacts = []
    all_metrics_dfs = []
    all_summary_metrics_data = []
    processed_dfs_for_concat = []

    # Initialize GCS client once
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME.replace("gs://", ""))

    SUPPORTED_METRICS = ["bleu", "rouge", "contains_words", "simple", "MANUAL"]
    if agent_df is not None and not agent_df.empty:
        def get_metric_type(gt):
            if isinstance(gt, dict):
                return gt.get("metric_type")
            return None
        
        agent_df['metric_type'] = agent_df["ground_truth"].apply(get_metric_type)
        
        for metric_type, group_df_original in agent_df.groupby('metric_type'):
            if metric_type not in SUPPORTED_METRICS:
                error_message = f"Invalid metric_type: '{metric_type}'. Supported types: {', '.join(SUPPORTED_METRICS)}"
                print(error_message)
                invalid_df = group_df_original.copy()
                invalid_df['metric_value'] = error_message
                processed_dfs_for_concat.append(invalid_df)
                continue

            if not metric_type:
                print(f"Skipping evaluation for {len(group_df_original)} rows with no metric_type.")
                continue
            if metric_type == "MANUAL":
                print(f"Skipping automatic metric calculation for {len(group_df_original)} rows with MANUAL metric_type.")
                continue


            # Select relevant columns and include session_id
            group_df = group_df_original[["response", "reference", "session_id", "prompt"]]
            
            # Replace empty strings with NaN so dropna works on them
            group_df = group_df.replace("", np.nan)

            # Drop rows where *either* response or reference is missing
            group_df = group_df.dropna(subset=["response", "reference"], how='any')


            if group_df.empty:
                print(f"Skipping evaluation for {metric_type} due to empty DataFrame after dropping NaNs/empty strings.")
                continue
                
            # CRITICAL: The evaluation SDK fails if the DataFrame index is not
            # a standard 0-based sequential index. reset_index fixes this.
            group_df = group_df.reset_index(drop=True)

            try:
                summary_metrics, metrics_df, experiment_run = _execute_evaluation_run_for_artifacts(
                    group_df,
                    metric_type=metric_type,
                    run_name_suffix=f"-{metric_type}",
                    experiment_name=experiment_name,
                    current_time_str=current_time_str
                )
            except Exception as e:
                print(f"Error during evaluation for metric_type {metric_type}: {e}")
                error_df = group_df_original[['session_id', 'metric_type']].copy()
                error_df['metric_value'] = f"Error during evaluation: {e}"
                processed_dfs_for_concat.append(error_df)
                continue


            if summary_metrics:
                all_summary_metrics_data.append((summary_metrics, metric_type))


            if metrics_df is not None and 'session_id' in metrics_df.columns:
                # Add a 'metric_type' column to distinguish metrics when concatenating
                metrics_df['metric_type'] = metric_type
                all_metrics_dfs.append(metrics_df)
            elif metrics_df is not None:
                print(f"Metrics DataFrame generated for {metric_type}, but 'session_id' column not found. Skipping for combined CSV.")
    
    if all_metrics_dfs:
        for df in all_metrics_dfs:
            if df.empty or 'metric_type' not in df.columns:
                continue
            current_metric_type = df['metric_type'].iloc[0]
            
            if current_metric_type == 'simple':
                # Handle multiple scores for 'simple' metric
                score_cols = [col for col in df.columns if '/score' in col]
                if score_cols:
                    temp_df = df[['session_id']].copy()
                    # Concatenate all scores into a single string
                    temp_df['metric_value'] = df[score_cols].apply(
                        lambda row: ', '.join([f'{col}: {val}' for col, val in row.items()]), axis=1
                    )
                    temp_df['metric_type'] = current_metric_type
                    processed_dfs_for_concat.append(temp_df)

            else:
                score_col = None
                if f'{current_metric_type}/score' in df.columns:
                    score_col = f'{current_metric_type}/score'
                elif current_metric_type in df.columns:
                    score_col = current_metric_type
                else: # Find first score column
                    for col in df.columns:
                        if '/score' in col:
                            score_col = col
                            break
                
                if score_col:
                    temp_df = df[['session_id']].copy()
                    temp_df['metric_value'] = df[score_col]
                    temp_df['metric_type'] = current_metric_type
                    processed_dfs_for_concat.append(temp_df)

    if processed_dfs_for_concat:
        final_combined_df = pd.concat(processed_dfs_for_concat, ignore_index=True)
        if not final_combined_df.empty:
            final_combined_df = final_combined_df[['session_id', 'metric_type', 'metric_value']]
    else:
        final_combined_df = pd.DataFrame(columns=['session_id', 'metric_type', 'metric_value'])

    if all_summary_metrics_data:
        combined_radar_chart_base64 = generate_radar_chart(all_summary_metrics_data, current_time_str)
        if combined_radar_chart_base64:
            combined_radar_chart_filename = f"all_metrics_radar_chart_{current_time_str}.png"
            blob = bucket.blob(f"evaluation_artifacts/combined/{combined_radar_chart_filename}")
            blob.upload_from_string(base64.b64decode(combined_radar_chart_base64), content_type="image/png")
            gcs_uri = f"gs://{bucket.name}/{blob.name}"
            artifacts.append({
                'id': 'all_metrics_radar_chart',
                'versionId': current_time_str,
                'mimeType': 'image/png',
                'gcsUrl': gcs_uri,
                'data': 'data:image/png;base64,' + combined_radar_chart_base64
            })
            print(f"Combined radar chart uploaded to: {gcs_uri}")


            eval_sets_dir = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets')
            local_combined_radar_chart_path = os.path.join(eval_sets_dir, combined_radar_chart_filename)
            blob.download_to_filename(local_combined_radar_chart_path)
            print(f"Combined radar chart downloaded to: {local_combined_radar_chart_path}")



    if session_id:
        try:
            # Need to get the experiment run object if it was created
            # This logic might need refinement if multiple metric types create multiple runs
            # For now, assume last 'experiment_run' is the one to log to.
            # if 'experiment_run' in locals():
            #     experiment_run.log_params({'session_id': session_id})
            pass # Added pass to keep block valid
        except Exception as e:
            print(f"Could not log session_id to experiment run: {e}")



    if simple_df is not None and not simple_df.empty:
        simple_metrics = ["fluency", "coherence", "safety", "rouge", "bleu"]
        
        # Select relevant columns and include session_id
        simple_df_cleaned = simple_df[["response", "reference", "prompt", "session_id"]]
        
        # Replace empty strings with NaN so dropna works on them
        # The SDK requires non-empty strings for both fields for metrics like 'bleu'
        simple_df_cleaned = simple_df_cleaned.replace("", np.nan)

        # Drop rows where *either* response or reference is missing (NaN)
        simple_df_cleaned = simple_df_cleaned.dropna(subset=["response"], how='any')

        if simple_df_cleaned.empty:
            print("Skipping simple evaluation due to empty DataFrame after dropping NaNs/empty strings.")
        else:
            # CRITICAL: The evaluation SDK fails if the DataFrame index is not
            # a standard 0-based sequential index. reset_index fixes this.
            simple_df_cleaned = simple_df_cleaned.reset_index(drop=True)

            try:
                summary_metrics, metrics_df, experiment_run = _execute_evaluation_run_for_artifacts(
                    eval_df=simple_df_cleaned,
                    metric_type="simple",
                    run_name_suffix="-simple",
                    experiment_name=experiment_name,
                    current_time_str=current_time_str
                )
                if summary_metrics:
                    all_summary_metrics_data.append((summary_metrics, "simple"))
                if metrics_df is not None and 'session_id' in metrics_df.columns:
                    # Add a 'metric_type' column to distinguish metrics when concatenating
                    metrics_df['metric_type'] = "simple"
                    all_metrics_dfs.append(metrics_df)
                elif metrics_df is not None:
                    print(f"Metrics DataFrame generated for simple, but 'session_id' column not found. Skipping for combined CSV.")
            except Exception as e:
                print(f"Error during simple evaluation: {e}")


    if not all_time:
        save_current_timestamp()
    
    return artifacts, final_combined_df

def _execute_evaluation_run_for_artifacts(
    eval_df: pd.DataFrame,
    metric_type: str,
    run_name_suffix: str,
    experiment_name: str,
    current_time_str: str
):
    run_name = f"custom-metric-{current_time_str}{run_name_suffix}" if metric_type == "contains_words" else f"{metric_type.lower().replace('_', '-')}-{current_time_str}{run_name_suffix}"
    full_judgement_model_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{JUDGEMENT_MODEL_NAME}"
    autorater_config = AutoraterConfig(autorater_model=full_judgement_model_name)

    if metric_type == "contains_words":
        metrics_to_apply = [CustomMetric(name="contains_words", metric_function=_contains_words_metric_function)]
    elif metric_type == "bleu":
        metrics_to_apply = ["bleu"]
    elif metric_type == "rouge":
        metrics_to_apply = ["rouge"]
    elif metric_type == "simple":
        metrics_to_apply = ["fluency", "coherence", "safety"]
        if not eval_df["reference"].replace('', np.nan).dropna().empty:
            metrics_to_apply.extend(["rouge", "bleu"])
    else:
        # Default or unrecognized metrics can be handled here
        # For now, we'll assume the metric_type is a valid, single metric string.
        metrics_to_apply = [metric_type]



    eval_task = EvalTask(
        dataset=eval_df,
        metrics=metrics_to_apply,
        autorater_config=autorater_config,
    )

    evaluation_result = eval_task.evaluate(
        experiment_run_name=run_name
    )
    
    return evaluation_result.summary_metrics, evaluation_result.metrics_table, None

def main():
    parser = argparse.ArgumentParser(description="Run evaluation or export agent sessions from logs.")
    parser.add_argument(
        "--export-sessions",
        action="store_true",
        help="Export agent sessions from logs to .evalset.json files."
    )
    parser.add_argument(
        "--all-time",
        action="store_true",
        help="Process all logs, ignoring the last run timestamp. Applies to both evaluation and export."
    )
    parser.add_argument(
        "--use-evalset-files",
        action="store_true",
        help="Run evaluation using local .evalset.json files instead of fetching logs from Cloud Logging."
    )
    parser.add_argument(
        "--evalset-file",
        type=str,
        help="Path to a specific .evalset.json file to use for evaluation."
    )
    parser.add_argument(
        "--output-csv-path",
        type=str,
        help="Path to the CSV file where combined evaluation results will be saved."
    )
    parser.add_argument(
        "--session-id",
        type=str,
        help="Optional: Filter logs by a specific session ID when exporting to CSV."
    )
    parser.add_argument(
        "--export-to-csv",
        action="store_true",
        help="Export logs to eval_test_cases.csv."
    )
    args = parser.parse_args()

    if args.export_to_csv:
        agent_df, simple_df = get_logs_for_evaluation(None, filter_session_id=args.session_id)
        
        combined_df_list = []
        if agent_df is not None and not agent_df.empty:
            combined_df_list.append(agent_df)
        if simple_df is not None and not simple_df.empty:
            combined_df_list.append(simple_df)

        if combined_df_list:
            combined_df = pd.concat(combined_df_list, ignore_index=True)
            # Ensure all required columns exist, initializing if missing
            required_columns = ["eval_id", "session_id", "user_content", "agent_response", "reference", "metric_type", "metric_value"]
            for col in required_columns:
                if col not in combined_df.columns:
                    combined_df[col] = "" # Initialize missing columns as empty strings

            # Select and reorder columns to match the desired format
            df_to_export = combined_df[required_columns].copy()

            # Filter out rows where user_content or agent_response are empty
            df_to_export = df_to_export[df_to_export["user_content"].astype(bool) & df_to_export["agent_response"].astype(bool)]
            
            # Drop any rows where all essential columns are empty, just in case
            df_to_export.dropna(subset=["user_content", "agent_response"], how='all', inplace=True)

            output_path = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets', 'eval_test_cases.csv')
            df_to_export.to_csv(output_path, index=False)
            print(f"Successfully exported logs to {output_path}")
        else:
            print("No logs found to export.")
        return




    if args.export_sessions:
        export_sessions_to_evalset(get_last_run_timestamp() if not args.all_time else None)
        if not args.all_time:
            save_current_timestamp()
    elif args.use_evalset_files:
        # Move any evalset files generated by adk web app to the eval_sets folder
        print("Moving evalset files to the eval_sets folder...")
        script_dir = os.path.dirname(__file__)
        move_script_path = os.path.join(script_dir, "move_evalsets.sh")
        if not os.path.exists(move_script_path):
             # Fallback path assuming .scripts directory
             move_script_path = "/home/user/gemini_playground/.scripts/move_evalsets.sh"
        
        if os.path.exists(move_script_path):
            os.system(move_script_path)
        else:
            print(f"Warning: move_evalsets.sh not found at {move_script_path} or fallback.")

        all_eval_cases = []
        # Store eval_set_id outside the loop for global access if original_df is empty
        # Initialize it to a default or None
        current_eval_set_id = None 

        if args.evalset_file:
            filepath = args.evalset_file
            print(f"Processing specific evalset file: {os.path.basename(filepath)}")
            with open(filepath, 'r') as f:
                eval_set = json.load(f)
                current_eval_set_id = eval_set.get("eval_set_id", os.path.basename(filepath).replace(".json", ""))
                for case in eval_set.get("eval_cases", []):
                    conversation = case.get("conversation", [])
                    ground_truth = case.get("ground_truth", {})
                    
                    # Determine the reference for the evaluation
                    case_reference = ground_truth.get("reference", "")
                    if not case_reference:
                         case_reference = conversation[-1].get("expected_final_response", {}).get("parts", [{}])[0].get("text", "")
                    
                    if conversation:
                        all_eval_cases.append({
                            "conversation": conversation,
                            "session_id": case.get("eval_id"),
                            "response": conversation[-1].get("final_response", {}).get("parts", [{}])[0].get("text", ""),
                            "prompt": conversation[0].get("user_content", {}).get("parts", [{}])[0].get("text", ""),
                            "reference": case_reference,
                            "ground_truth": ground_truth
                        })
        else:
            eval_sets_dir = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets')
            for filepath in glob.glob(os.path.join(eval_sets_dir, "**", "*.evalset.json"), recursive=True):
                if not os.path.isfile(filepath): # Filter out directories
                    print(f"Skipping directory: {os.path.basename(filepath)}")
                    continue
                if not (filepath.endswith(".json") and (".evalset." in filepath or "generated_evalset" in filepath)):
                    continue

                
                print(f"Processing evalset file: {os.path.basename(filepath)}")
                with open(filepath, 'r') as f:
                    eval_set = json.load(f)
                    current_eval_set_id = eval_set.get("eval_set_id", os.path.basename(filepath).replace(".json", ""))
                    for case in eval_set.get("eval_cases", []):
                        conversation = case.get("conversation", [])
                        ground_truth = case.get("ground_truth", {})
                        
                        # Determine the reference for the evaluation
                        case_reference = ground_truth.get("reference", "")
                        if not case_reference:
                             case_reference = conversation[-1].get("expected_final_response", {}).get("parts", [{}])[0].get("text", "")
                        
                        if conversation:
                            all_eval_cases.append({
                                "conversation": conversation,
                                "session_id": case.get("eval_id"),
                                "response": conversation[-1].get("final_response", {}).get("parts", [{}])[0].get("text", ""),
                                "prompt": conversation[0].get("user_content", {}).get("parts", [{}])[0].get("text", ""),
                                "reference": case_reference,
                                "ground_truth": ground_truth
                            })

        if all_eval_cases:
            eval_df = pd.DataFrame(all_eval_cases)
            # Assuming all eval cases come from a single session for now
            # If multiple sessions are processed, this needs to be handled differently
            session_id_from_df = eval_df["session_id"].iloc[0] if not eval_df.empty else None
            artifacts, final_combined_df = run_evaluation_and_generate_artifacts(eval_df=eval_df, all_time=args.all_time, session_id=session_id_from_df, eval_set_id=current_eval_set_id)

            if final_combined_df is not None and not final_combined_df.empty:

                if args.output_csv_path:
                    original_df_from_csv = pd.DataFrame()
                    try:
                        import csv
                        original_df_from_csv = pd.read_csv(args.output_csv_path, sep=',', quotechar='"', doublequote=True, engine='python', on_bad_lines='skip')
                    except FileNotFoundError:
                        print(f"Output CSV file not found at {args.output_csv_path}. A new one will be created.")
                    except pd.errors.EmptyDataError:
                        print(f"Output CSV file at {args.output_csv_path} is empty. A new one will be created.")


                    
                    # Prepare new evaluation results (final_combined_df)
                    final_results = final_combined_df.rename(columns={'session_id': 'eval_id'})
                    if 'eval_set_id' not in final_results.columns:
                        final_results['eval_set_id'] = current_eval_set_id
                    
                    # Also include content columns from eval_df to merge properly
                    # content_cols_to_merge should be unique per eval_id and eval_set_id
                    content_cols_to_merge = eval_df[['session_id', 'prompt', 'response', 'reference']].drop_duplicates(subset=['session_id'])
                    content_cols_to_merge = content_cols_to_merge.rename(columns={'session_id': 'eval_id', 'prompt': 'user_content', 'response': 'agent_response'})
                    if 'eval_set_id' not in content_cols_to_merge.columns:
                         content_cols_to_merge['eval_set_id'] = current_eval_set_id # Add eval_set_id to content data


                    # Merge the content with the new metrics
                    # This creates a DataFrame where each row has content and metric_value
                    # Use a left merge to ensure all metric results are included
                    new_results_with_content = pd.merge(
                        final_results,
                        content_cols_to_merge,
                        on=['eval_id', 'eval_set_id'],
                        how='left',
                        suffixes=('', '_content')
                    )
                    # For columns from content_cols_to_merge that are not in final_results,
                    # fill them in merged_output_df.
                    for col_name in ['user_content', 'agent_response', 'reference']:
                        if f'{col_name}_content' in new_results_with_content.columns:
                            new_results_with_content[col_name] = new_results_with_content[f'{col_name}_content'].fillna(new_results_with_content[col_name])
                            new_results_with_content.drop(columns=[f'{col_name}_content'], inplace=True)
                        elif col_name not in new_results_with_content.columns:
                            new_results_with_content[col_name] = '' # Ensure content column exists

                    
                    # Consolidate existing data with new results
                    if not original_df_from_csv.empty:
                        merge_keys = ['eval_id', 'metric_type']
                        if 'eval_set_id' in original_df_from_csv.columns and 'eval_set_id' in new_results_with_content.columns:
                            merge_keys.append('eval_set_id')

                        # Drop duplicates before setting index
                        original_df_from_csv.drop_duplicates(subset=merge_keys, keep='last', inplace=True)
                        new_results_with_content.drop_duplicates(subset=merge_keys, keep='last', inplace=True)

                        # Set index for both dataframes for easy update
                        original_df_from_csv.set_index(merge_keys, inplace=True)
                        new_results_with_content.set_index(merge_keys, inplace=True)

                        # Replace empty string with NaN so that update works
                        original_df_from_csv['metric_value'] = original_df_from_csv['metric_value'].replace('', np.nan)

                        # Update original_df with new results. This will overwrite existing rows.
                        original_df_from_csv.update(new_results_with_content)

                        # Identify new rows that were not in the original DataFrame
                        new_rows = new_results_with_content[~new_results_with_content.index.isin(original_df_from_csv.index)]

                        # Reset index to get columns back
                        original_df_from_csv.reset_index(inplace=True)
                        
                        # Concatenate updated original data with brand new rows
                        merged_output_df = pd.concat([original_df_from_csv, new_rows.reset_index()], ignore_index=True)

                    else:
                        # If original CSV was empty, just use the new results
                        merged_output_df = new_results_with_content

                    # Ensure all required columns are present and in final order
                    ordered_cols = ['eval_id', 'eval_set_id', 'metric_type', 'user_content', 'agent_response', 'metric_value', 'reference']
                    for col in ordered_cols:
                        if col not in merged_output_df.columns:
                            merged_output_df[col] = ''
                    
                    merged_output_df = merged_output_df[ordered_cols].fillna('')

                    try:
                        merged_output_df.to_csv(args.output_csv_path, index=False)
                        print(f"Combined evaluation results saved to CSV: {args.output_csv_path}")
                    except Exception as e:
                        print(f"Error saving combined evaluation results to CSV: {e}")
    else:
        artifacts, final_combined_df = run_evaluation_and_generate_artifacts(all_time=args.all_time)

if __name__ == "__main__":
    main()