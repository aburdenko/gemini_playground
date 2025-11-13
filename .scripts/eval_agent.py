#!/usr/bin/env python3
import matplotlib
matplotlib.use('Agg')
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from google.cloud import aiplatform
import google.cloud.storage
import vertexai
from vertexai.preview.evaluation import EvalTask, AutoraterConfig, CustomMetric

from google.cloud import storage
from google.api_core import exceptions as google_exceptions
from google.cloud import logging
from datetime import datetime, timedelta
import os
from urllib.parse import urlparse
import re

import hashlib
from google.cloud import aiplatform_v1
import json
import io
import base64
import argparse
from typing import Dict, Any
import sys
import os

# Add the 'rag-agent' directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent')))

from app.evaluators import ContainsWords

PROJECT_ID = os.environ.get("PROJECT_ID", "your-gcp-project-id")
LOCATION = os.environ.get("REGION", "us-central1")
BUCKET_NAME = os.environ.get("STAGING_GCS_BUCKET", "your-bucket-name")
SHORT_LOG_NAME = os.environ.get("LOG_NAME", "run_gemini_from_file")
EXPERIMENT_NAME = "gemini-playground-evaluation"
LOG_NAME = f"projects/{PROJECT_ID}/logs/{SHORT_LOG_NAME}"
JUDGEMENT_MODEL_NAME = os.environ.get("JUDGEMENT_MODEL_NAME", "gemini-2.5-flash")

TIMESTAMP_FILE = "last_run_timestamp.txt"

def _contains_words_metric_function(test_case: Dict[str, Any]) -> Dict[str, Any]:
    response = test_case.get("response", "")
    reference = test_case.get("reference", "")

    if not response or not reference:
        return {"contains_words": 0.0}

    words_to_check = [word.strip() for word in reference.split(' ') if word.strip()]
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

def get_logs_for_evaluation(last_run_timestamp: str | None) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    logging_client = logging.Client(project=PROJECT_ID)
    base_filter = (
        f'logName="{LOG_NAME}" AND '
        f'(jsonPayload.session_id:* OR jsonPayload.request_id:*)'
    )
    if last_run_timestamp:
        log_filter = f'{base_filter} AND timestamp >= "{last_run_timestamp}"'
    else:
        log_filter = base_filter

    agent_sessions = {}
    simple_logs = []
    for entry in logging_client.list_entries(filter_=log_filter):
        payload = {} # Initialize payload as empty dict
        if hasattr(entry, 'json_payload'):
            payload = entry.json_payload
        elif hasattr(entry, 'text_payload'):
            # For text payloads, we can put the text into a 'message' field
            payload = {"message": entry.text_payload}
        else:
            # Fallback for other types of payloads
            payload = {"message": "Unsupported log entry type"}
        if "session_id" in payload:
            session_id = payload.get("session_id")
            if session_id not in agent_sessions:
                agent_sessions[session_id] = {
                    "logs": [], "instruction": payload.get("instruction"),
                    "initial_prompt": payload.get("initial_prompt"),
                    "ground_truth": payload.get("ground_truth"), "final_answer": ""
                }
            agent_sessions[session_id]["logs"].append(payload)
            if payload.get("log_type") == "final_answer":
                agent_sessions[session_id]["final_answer"] = payload.get("final_answer")
        elif "request_id" in payload:
            if all(k in payload for k in ['prompt', 'response']):
                simple_logs.append({
                    "prompt": payload['prompt'], "response": payload['response'],
                    "reference": payload.get('ground_truth') or ''
                })
    agent_df = None
    if agent_sessions:
        agent_eval_data = []
        for session_id, data in agent_sessions.items():
            sorted_logs = sorted(data["logs"], key=lambda x: x.get("step", 0))
            full_trace = "\n".join([json.dumps(log) for log in sorted_logs])
            agent_eval_data.append({
                "instruction": data["instruction"], "initial_prompt": data["initial_prompt"],
                "full_trace": full_trace, "final_answer": data["final_answer"],
                "reference": data["ground_truth"],
            })
        agent_df = pd.DataFrame(agent_eval_data)
    simple_df = None
    if simple_logs:
        simple_df = pd.DataFrame(simple_logs)
    return agent_df, simple_df

def export_sessions_to_evalset(last_run_timestamp: str | None):
    """
    Fetches agent logs and exports each session to a separate .evalset.json file.
    """
    print("Fetching logs from Cloud Logging to export as eval sets...")
    logging_client = logging.Client(project=PROJECT_ID)
    # Broaden filter to get both agent sessions (with session_id) and simple ADK web logs (with request_id).
    base_filter = f'logName="{LOG_NAME}" AND (jsonPayload.session_id:* OR jsonPayload.request_id:*)'
    if last_run_timestamp:
        log_filter = f'{base_filter} AND timestamp >= "{last_run_timestamp}"'
    else:
        log_filter = base_filter

    # 1. Group logs by session_id and request_id
    sessions = {}
    simple_sessions = {}
    for entry in logging_client.list_entries(filter_=log_filter):
        payload = {}  # Initialize payload as empty dict
        if isinstance(entry.payload, dict):
            payload = entry.payload
        elif isinstance(entry.payload, str):
            payload = {"message": entry.payload}
        else:
            payload = {"message": "Unsupported log entry type"}

        if "session_id" in payload:
            session_id = payload.get("session_id")
            if session_id not in sessions:
                sessions[session_id] = []
            sessions[session_id].append(payload)
        elif "request_id" in payload:
            request_id = payload.get("request_id")
            if request_id not in simple_sessions:
                simple_sessions[request_id] = []
            simple_sessions[request_id].append(payload)

    if not sessions and not simple_sessions:
        print("No new logs found to export.")
        return

    output_dir = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets')
    os.makedirs(output_dir, exist_ok=True)
    print(f"Found {len(sessions)} agent session(s) and {len(simple_sessions)} simple log(s). Exporting to: {output_dir}")

    # 2. Process and write each agent session to a file
    for session_id, logs in sessions.items():
        conversation_turns = []
        # Sort logs by step to reconstruct the conversation order
        sorted_logs = sorted(logs, key=lambda x: x.get("step", 0))
        
        # Reconstruct ground_truth from the session
        session_ground_truth = None
        for log in sorted_logs:
            if log.get("ground_truth"):
                session_ground_truth = log.get("ground_truth")
                break # Found it

        for log in sorted_logs:
            if log.get("log_type") == "user_message":
                conversation_turns.append({
                    "user_content": {"parts": [{"text": log.get("message", "")}]}
                })
            elif log.get("log_type") == "final_answer":
                conversation_turn = {
                    "final_response": {"parts": [{"text": log.get("final_answer", "")}]}
                }
                if session_ground_truth:
                    conversation_turn["expected_final_response"] = {"parts": [{"text": session_ground_truth if isinstance(session_ground_truth, str) else session_ground_truth.get("reference")}]}
                conversation_turns.append(conversation_turn)

        # Prepare ground_truth for the eval case
        ground_truth_obj = {}
        if session_ground_truth:
            if isinstance(session_ground_truth, dict):
                ground_truth_obj = session_ground_truth
            else:
                # Assuming string ground_truth implies a 'reference' for a default metric
                ground_truth_obj = {"reference": session_ground_truth, "metric_type": "bleu"} # Defaulting metric_type

        eval_set = {
            "eval_set_id": session_id, 
            "eval_cases": [{
                "eval_id": f"case-{session_id}", 
                "conversation": conversation_turns,
                "ground_truth": ground_truth_obj
            }]
        }
        output_filename = os.path.join(output_dir, f"rag-agent.evalset.{session_id}.json")
        with open(output_filename, 'w') as f:
            json.dump(eval_set, f, indent=2)
        print(f"  - Successfully exported session {session_id} to {output_filename}")

    # 3. Process and write each simple session to a file
    for request_id, logs in simple_sessions.items():
        prompt = ""
        response = ""
        ground_truth_str = ""
        for log in logs:
            if "prompt" in log and log["prompt"]:
                prompt = log["prompt"]
            if "response" in log and log["response"]:
                response = log["response"]
            if "ground_truth" in log and log["ground_truth"]:
                ground_truth_str = log["ground_truth"]

        if not prompt:
            continue  # Skip if no prompt found

        # Generate a unique ID for this simple log based on its content and timestamp
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:8]
        eval_set_id = f"adk-web-{datetime.now().strftime('%Y%m%d%H%M%S')}-{prompt_hash}"

        conversation_turns = [{
            "user_content": {"parts": [{"text": prompt}]},
            "final_response": {"parts": [{"text": response}]}
        }]

        if ground_truth_str:
            conversation_turns[-1]["expected_final_response"] = {"parts": [{"text": ground_truth_str}]}
        
        ground_truth_obj = {
            "reference": ground_truth_str,
            "metric_type": "bleu" # Defaulting metric_type
        }

        eval_set = {
            "eval_set_id": eval_set_id, 
            "eval_cases": [{
                "eval_id": f"case-{eval_set_id}", 
                "conversation": conversation_turns,
                "ground_truth": ground_truth_obj
            }]
        }
        output_filename = os.path.join(output_dir, f"rag-agent.evalset.{eval_set_id}.json")
        with open(output_filename, 'w') as f:
            json.dump(eval_set, f, indent=2)
        print(f"  - Successfully exported ADK web log to {output_filename}")

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

def run_evaluation_and_generate_artifacts(eval_df: pd.DataFrame | None = None, all_time: bool = False, session_id: str | None = None):
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
        agent_df = eval_df
        simple_df = None # Assuming evalset files are for agentic evaluations
    else:
        agent_df, simple_df = get_logs_for_evaluation(last_run)
    
    artifacts = []
    all_metrics_dfs = []
    all_summary_metrics_data = []
    final_combined_df = None

    if agent_df is not None and not agent_df.empty:
        # Initialize GCS client
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME.replace("gs://", ""))

        # Group by metric_type and run evaluation for each group
        def get_metric_type(gt):
            if isinstance(gt, dict):
                return gt.get("metric_type")
            return None
        
        agent_df['metric_type'] = agent_df["ground_truth"].apply(get_metric_type)
        
        for metric_type, group_df_original in agent_df.groupby('metric_type'):
            if not metric_type:
                print(f"Skipping evaluation for {len(group_df_original)} rows with no metric_type.")
                continue
            if metric_type == "MANUAL":
                print(f"Skipping automatic metric calculation for {len(group_df_original)} rows with MANUAL metric_type.")
                continue

            # --- START MODIFICATION ---
            # Select relevant columns and include session_id
            group_df = group_df_original[["response", "reference", "session_id", "prompt"]]
            
            # Replace empty strings with NaN so dropna works on them
            # The SDK requires non-empty strings for both fields for metrics like 'bleu'
            group_df = group_df.replace("", np.nan)

            # Drop rows where *either* response or reference is missing (NaN)
            group_df = group_df.dropna(subset=["response", "reference"], how='any')

            if group_df.empty:
                print(f"Skipping evaluation for {metric_type} due to empty DataFrame after dropping NaNs/empty strings.")
                continue
                
            # CRITICAL: The evaluation SDK fails if the DataFrame index is not
            # a standard 0-based sequential index. reset_index fixes this.
            group_df = group_df.reset_index(drop=True)
            # --- END MODIFICATION ---

            if metric_type == "contains_words":
                metrics_to_apply = [CustomMetric(name="contains_words", metric_function=_contains_words_metric_function)]
            elif metric_type == "bleu":
                metrics_to_apply = ["bleu"]
            else:
                metrics_to_apply = ["fluency", "rouge"]

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
                print("Continuing to next metric type...")
                continue # Skip to the next metric

            # session_id is already in metrics_df if it was in group_df_original
            # No need to re-add it here.

            if summary_metrics:
                all_summary_metrics_data.append((summary_metrics, metric_type))

            if metrics_df is not None and 'session_id' in metrics_df.columns:
                # Add a 'metric_type' column to distinguish metrics when concatenating
                metrics_df['metric_type'] = metric_type
                all_metrics_dfs.append(metrics_df)
            elif metrics_df is not None:
                print(f"Metrics DataFrame generated for {metric_type}, but 'session_id' column not found. Skipping for combined CSV.")


        if all_metrics_dfs:
            # Create a list to hold processed dataframes for concatenation
            processed_dfs_for_concat = []
            for df in all_metrics_dfs:
                if df.empty or 'metric_type' not in df.columns:
                    continue
                current_metric_type = df['metric_type'].iloc[0]
                score_cols = [col for col in df.columns if col not in ['session_id', 'response', 'reference', 'metric_type', 'prompt', 'conversation', 'ground_truth']]

                if score_cols:
                    primary_score_col = score_cols[0]
                    # Create a new DataFrame with session_id, metric_type, and metric_value
                    temp_df = df[['session_id', primary_score_col]].copy()
                    temp_df.rename(columns={primary_score_col: 'metric_value'}, inplace=True)
                    temp_df['metric_type'] = current_metric_type
                    processed_dfs_for_concat.append(temp_df)

            if processed_dfs_for_concat:
                # Concatenate all processed DataFrames vertically
                final_combined_df = pd.concat(processed_dfs_for_concat, ignore_index=True)
                # Reorder columns for clarity
                final_combined_df = final_combined_df[['session_id', 'metric_type', 'metric_value']]
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
        
        # --- Apply same fix for simple_df ---
        simple_df_cleaned = simple_df[["response", "reference"]]
        simple_df_cleaned = simple_df_cleaned.replace("", np.nan)
        simple_df_cleaned = simple_df_cleaned.dropna(subset=["response", "reference"], how='any')
        
        if simple_df_cleaned.empty:
            print("Skipping simple evaluation due to empty DataFrame after dropping NaNs/empty strings.")
        else:
            simple_df_cleaned = simple_df_cleaned.reset_index(drop=True)
            # --- End fix ---

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
                if metrics_df is not None:
                    all_metrics_dfs.append(metrics_df)
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
    metric_type = metric_type.strip()
    run_name = f"custom-metric-{current_time_str}" if metric_type == "contains_words" else f"{metric_type.lower().replace('_', '-')}-{current_time_str}"
    full_judgement_model_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{JUDGEMENT_MODEL_NAME}"
    autorater_config = AutoraterConfig(autorater_model=full_judgement_model_name)

    metrics_to_apply = []
    if metric_type == "contains_words":
        metrics_to_apply = [CustomMetric(name="contains_words", metric_function=_contains_words_metric_function)]
    elif metric_type == "bleu":
        metrics_to_apply = ["bleu"]
    elif metric_type == "rouge":
        metrics_to_apply = ["rouge"]
    elif metric_type == "simple": # Handle the 'simple' case
        metrics_to_apply = ["fluency", "coherence", "safety", "rouge", "bleu"]
    else:
        metrics_to_apply = ["fluency", "rouge"]

    print(f"Debugging evaluation for metric_type: {metric_type}")
    print(f"Eval DataFrame head (post-cleaning):\n{eval_df.head()}")
    print(f"Metrics to apply: {metrics_to_apply}")
    eval_task = EvalTask(
        dataset=eval_df,
        metrics=metrics_to_apply,
        autorater_config=autorater_config,
    )

    evaluation_result = eval_task.evaluate(
        experiment_run_name=run_name
    )
    
    # Add metric_type to the metrics_table before returning
    metrics_table = evaluation_result.metrics_table
    if metrics_table is not None:
        metrics_table['metric_type'] = metric_type

    return evaluation_result.summary_metrics, metrics_table, None

def main():
    parser = argparse.ArgumentParser(description="Run evaluation or export agent sessions from logs.")
    parser.add_argument(
        "--export-sessions",
        action="store_true",
        help="Export agent sessions from logs to .evalset.json files instead of running evaluation."
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
        "--original-csv-path",
        type=str,
        help="Path to the original eval_test_cases.csv file to merge metrics into."
    )
    parser.add_argument(
        "--export-to-csv",
        action="store_true",
        help="Export logs to eval_test_cases.csv."
    )
    args = parser.parse_args()

    if args.export_to_csv:
        agent_df, simple_df = get_logs_for_evaluation(None)
        if agent_df is not None or simple_df is not None:
            df = pd.concat([agent_df, simple_df])
            df = df[["instruction", "initial_prompt", "reference"]]
            df = df.rename(columns={"reference": "ground_truth"})
            df["metric_type"] = ""
            output_path = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets', 'eval_test_cases.csv')
            df.to_csv(output_path, index=False)
            print(f"Successfully exported logs to {output_path}")
        else:
            print("No logs found to export.")
        return


    if args.use_evalset_files and not args.original_csv_path:
        # Infer default original_csv_path based on eval_sets_dir
        eval_sets_dir = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets')
        default_csv_path = os.path.join(os.path.dirname(eval_sets_dir), "eval_test_cases.csv")
        args.original_csv_path = default_csv_path
        print(f"No original CSV path provided. Defaulting to: {args.original_csv_path}")

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
        if args.evalset_file:
            filepath = args.evalset_file
            print(f"Processing specific evalset file: {os.path.basename(filepath)}")
            with open(filepath, 'r') as f:
                eval_set = json.load(f)
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
            for filename in os.listdir(eval_sets_dir):
                if filename.endswith(".json") and (".evalset." in filename or "generated_evalset" in filename):
                    filepath = os.path.join(eval_sets_dir, filename)
                    print(f"Processing evalset file: {filename}")
                    with open(filepath, 'r') as f:
                        eval_set = json.load(f)
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
            eval_df.drop_duplicates(subset=['session_id'], inplace=True)
            # Assuming all eval cases come from a single session for now
            # If multiple sessions are processed, this needs to be handled differently
            session_id_from_df = eval_df["session_id"].iloc[0] if not eval_df.empty else None
            artifacts, final_combined_df = run_evaluation_and_generate_artifacts(eval_df=eval_df, all_time=args.all_time, session_id=session_id_from_df)

            if final_combined_df is not None and not final_combined_df.empty:
                if args.original_csv_path:
                    original_df = None
                    try:
                        import csv
                        original_df = pd.read_csv(args.original_csv_path, sep=',', quotechar='"', doublequote=True, engine='python', on_bad_lines='skip')
                        original_df.drop_duplicates(subset=['eval_id'], inplace=True)
                    except FileNotFoundError:
                        print(f"Error: Original CSV file not found at {args.original_csv_path}. Cannot save results to the original CSV.")
                        original_df = pd.DataFrame() # Create empty df to avoid further errors

                    if not original_df.empty: # Only proceed if original_df was successfully loaded or created
                        # Merge original_df with final_combined_df (which is already in long format)
                        merged_output_df = pd.merge(original_df, final_combined_df, left_on='eval_id', right_on='session_id', how='left')

                        # Drop the redundant 'session_id' column from the merged DataFrame
                        if 'session_id' in merged_output_df.columns:
                            merged_output_df.drop(columns=['session_id'], inplace=True)

                        # Rename '_y' columns to their base names
                        if 'metric_type_y' in merged_output_df.columns:
                            merged_output_df.rename(columns={'metric_type_y': 'metric_type'}, inplace=True)
                        if 'metric_value_y' in merged_output_df.columns:
                            merged_output_df.rename(columns={'metric_value_y': 'metric_value'}, inplace=True)

                        # Identify and drop old metric columns from original_df and the temporary '_x' columns
                        # This list should be comprehensive for all possible old metric columns and their '_x' versions
                        old_metric_cols_to_drop = [
                            'metric_type_x', 'metric_value_x', # From original_df
                            'bleu', 'contains_words', 'rouge', 'GROUNDING_x', 'GROUNDING_y', 'GROUNDING' # Other potential old metric columns
                        ]
                        cols_to_drop = [col for col in old_metric_cols_to_drop if col in merged_output_df.columns]
                        if cols_to_drop:
                            merged_output_df.drop(columns=cols_to_drop, inplace=True)

                        # Ensure the desired columns are present and in order
                        current_cols = merged_output_df.columns.tolist()
                        # Filter out 'metric_type' and 'metric_value' if they are already in current_cols (to avoid duplication)
                        final_cols = [col for col in current_cols if col not in ['metric_type', 'metric_value']] + ['metric_type', 'metric_value']
                        merged_output_df = merged_output_df[final_cols]

                        try:
                            merged_output_df.to_csv(args.original_csv_path, index=False)
                            print(f"Combined evaluation results saved to original CSV: {args.original_csv_path}")
                        except Exception as e:
                            print(f"Error saving combined evaluation results to CSV: {e}")
                    else:
                        print("Warning: Original CSV could not be loaded or was empty. Metrics will not be saved to a CSV file.")
    else:
        artifacts, final_combined_df = run_evaluation_and_generate_artifacts(all_time=args.all_time)

if __name__ == "__main__":
    main()
