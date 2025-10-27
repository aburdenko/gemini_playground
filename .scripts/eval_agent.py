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

def generate_radar_chart(summary_metrics: dict, run_name_suffix: str) -> str:
    """Generates a radar chart from summary metrics and returns it as a base64 PNG."""
    labels = [key for key in summary_metrics.keys() if "/mean" in key]
    scores = [summary_metrics[key] for key in labels]
    clean_labels = [label.replace('/mean', '') for label in labels]

    if not clean_labels or not scores:
        return ""

    num_vars = len(clean_labels)
    angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
    scores += scores[:1]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))
    ax.plot(angles, scores, linewidth=2, linestyle='solid', label=f'Performance ({run_name_suffix.strip("-")})')
    ax.fill(angles, scores, 'b', alpha=0.1)
    ax.set_yticklabels([])
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(clean_labels)
    ax.set_title(f'Evaluation of Gemini Run ({run_name_suffix.strip("-")})', size=12, color='black', va='center')
    ax.grid(True)

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

            # --- START MODIFICATION ---
            # Select relevant columns and include session_id
            group_df = group_df_original[["response", "reference", "session_id"]]
            
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
                radar_chart_base64 = generate_radar_chart(summary_metrics, f"-{metric_type}")
                if radar_chart_base64:
                    radar_chart_filename = f"radar_chart_{session_id or 'unknown_session'}_{metric_type}_{current_time_str}.png"
                    blob = bucket.blob(f"evaluation_artifacts/{session_id or 'unknown_session'}/{radar_chart_filename}")
                    blob.upload_from_string(base64.b64decode(radar_chart_base64), content_type="image/png")
                    gcs_uri = f"gs://{bucket.name}/{blob.name}"
                    artifacts.append({
                        'id': f'{metric_type}_radar_chart',
                        'versionId': current_time_str,
                        'mimeType': 'image/png',
                        'gcsUrl': gcs_uri,
                        'data': 'data:image/png;base64,' + radar_chart_base64
                    })
                    # experiment_run.log_params({f'{metric_type}_radar_chart_gcs_uri': gcs_uri})
                    print(f"Radar chart for {metric_type} uploaded to: {gcs_uri}")

                    eval_sets_dir = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets')
                    local_radar_chart_path = os.path.join(eval_sets_dir, radar_chart_filename)
                    blob.download_to_filename(local_radar_chart_path)
                    print(f"Radar chart for {metric_type} downloaded to: {local_radar_chart_path}")

            if metrics_df is not None and 'session_id' in metrics_df.columns:
                for session_id_group, session_metrics_df in metrics_df.groupby('session_id'):
                    metrics_csv_content = generate_metrics_csv(session_metrics_df)
                    if metrics_csv_content:
                        metrics_csv_filename = f"metrics_{session_id_group}_{metric_type}_{current_time_str}.csv"
                        blob = bucket.blob(f"evaluation_artifacts/{session_id_group}/{metrics_csv_filename}")
                        blob.upload_from_string(metrics_csv_content, content_type="text/csv")
                        gcs_uri = f"gs://{bucket.name}/{blob.name}"
                        artifacts.append({
                            'id': f'{metric_type}_metrics_csv_{session_id_group}',
                            'versionId': current_time_str,
                            'mimeType': 'text/csv',
                            'gcsUrl': gcs_uri,
                            'data': 'data:text/csv;base64,' + base64.b64encode(metrics_csv_content.encode('utf-8')).decode('utf-8')
                        })
                        # experiment_run.log_params({f'{metric_type}_metrics_csv_gcs_uri_{session_id_group}': gcs_uri})
                        print(f"Metrics CSV for session {session_id_group} and {metric_type} uploaded to: {gcs_uri}")

                        eval_sets_dir = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets')
                        local_metrics_csv_path = os.path.join(eval_sets_dir, metrics_csv_filename)
                        blob.download_to_filename(local_metrics_csv_path)
                        print(f"Metrics CSV for session {session_id_group} and {metric_type} downloaded to: {local_metrics_csv_path}")
            elif metrics_df is not None:
                print("Metrics DataFrame generated, but 'session_id' column not found. Skipping artifact upload for metrics CSV.")


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
                    # Note: artifacts is a list, not a dict.
                    artifacts.append({
                        'id': 'simple_radar_chart',
                        'versionId': current_time_str,
                        'mimeType': 'image/png',
                        'data': 'data:image/png;base64,' + generate_radar_chart(summary_metrics, "-simple")
                    })
                if metrics_df is not None:
                    artifacts.append({
                        'id': 'simple_metrics_csv',
                        'versionId': current_time_str,
                        'mimeType': 'text/csv',
                        'data': 'data:text/csv;base64,' + base64.b64encode(generate_metrics_csv(metrics_df).encode('utf-8')).decode('utf-8')
                    })
            except Exception as e:
                print(f"Error during simple evaluation: {e}")

    if not all_time:
        save_current_timestamp()
    
    return artifacts

def _execute_evaluation_run_for_artifacts(
    eval_df: pd.DataFrame,
    metric_type: str,
    run_name_suffix: str,
    experiment_name: str,
    current_time_str: str
):
    run_name = f"custom-metric-{current_time_str}" if metric_type == "contains_words" else f"{metric_type}-{current_time_str}"
    full_judgement_model_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{JUDGEMENT_MODEL_NAME}"
    autorater_config = AutoraterConfig(autorater_model=full_judgement_model_name)

    metrics_to_apply = []
    if metric_type == "contains_words":
        metrics_to_apply = [CustomMetric(name="contains_words", metric_function=_contains_words_metric_function)]
    elif metric_type == "bleu":
        metrics_to_apply = ["bleu"]
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
    # The evaluate() method creates the experiment run. We can directly use its name.
    # experiment_run = evaluation_result.experiment_run # Commented out
    
    return evaluation_result.summary_metrics, evaluation_result.metrics_table, None # Return None for experiment_run

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
    args = parser.parse_args()

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


        eval_sets_dir = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets')
        all_eval_cases = []
        session_id_from_df = None
        for filename in os.listdir(eval_sets_dir):
            if filename.endswith(".json") and (".evalset." in filename or "generated_evalset" in filename):
                filepath = os.path.join(eval_sets_dir, filename)
                print(f"Processing evalset file: {filename}")
                with open(filepath, 'r') as f:
                    eval_set = json.load(f)
                    eval_set_id = eval_set.get("eval_set_id")
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
                                "session_id": eval_set_id,
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
            artifacts = run_evaluation_and_generate_artifacts(eval_df=eval_df, all_time=args.all_time, session_id=session_id_from_df)
        else:
            print("No evalset files found or no cases to evaluate.")
    else:
        run_evaluation_and_generate_artifacts(all_time=args.all_time)

if __name__ == "__main__":
    main()
