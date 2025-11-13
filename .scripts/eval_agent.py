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

def get_logs_for_evaluation(last_run_timestamp: str | None, filter_session_id: str | None = None) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    logging_client = logging.Client(project=PROJECT_ID)
    base_filter = (
        f'logName="{LOG_NAME}" AND '
        f'(jsonPayload.session_id:* OR jsonPayload.request_id:*)'
    )
    if last_run_timestamp:
        log_filter = f'{base_filter} AND timestamp >= "{last_run_timestamp}"'
    else:
        log_filter = base_filter

    agent_sessions_raw_logs = {} # To store raw log entries for agent sessions
    simple_sessions_raw_logs = {} # To store raw log entries for simple ADK web logs

    for entry in logging_client.list_entries(filter_=log_filter):
        payload = {} # Initialize payload as empty dict
        if hasattr(entry, 'payload') and isinstance(entry.payload, dict):
            payload = entry.payload
        elif hasattr(entry, 'payload') and isinstance(entry.payload, str):
            payload = {"message": entry.payload}
        elif hasattr(entry, 'json_payload'): # Fallback for older versions or different entry types
            payload = entry.json_payload
        elif hasattr(entry, 'text_payload'): # Fallback for older versions or different entry types
            payload = {"message": entry.text_payload}
        else:
            payload = {"message": "Unsupported log entry type"}
        
        session_id = payload.get("session_id")
        request_id = payload.get("request_id")

        # Filter by session_id if provided
        if filter_session_id and session_id != filter_session_id and request_id != filter_session_id:
            continue

        if payload.get("log_type") in ["user_message", "final_answer"]:
            session_id = payload.get("session_id")
            if session_id:
                if session_id not in agent_sessions_raw_logs:
                    agent_sessions_raw_logs[session_id] = []
                agent_sessions_raw_logs[session_id].append({"entry": entry, "payload": payload})
        elif request_id and "prompt" in payload and "response" in payload:
            # This is a simple ADK web log, identified by prompt/response and absence of specific log_type
            if request_id not in simple_sessions_raw_logs:
                simple_sessions_raw_logs[request_id] = []
            simple_sessions_raw_logs[request_id].append({"entry": entry, "payload": payload})

    agent_sessions_data = [] # To store aggregated and processed data for agent sessions
    simple_sessions_data = [] # To store aggregated and processed data for simple sessions

    # Process agent sessions
    for session_id, raw_logs in agent_sessions_raw_logs.items():
        current_user_content = ""
        current_agent_response = ""
        current_reference = ""
        current_eval_id = ""
        turn_counter = 0
        
        # Sort logs by timestamp to process them in order
        sorted_raw_logs = sorted(raw_logs, key=lambda x: x["entry"].timestamp)

        # Iterate through logs to find the user_message and final_answer
        for log_item in sorted_raw_logs:
            entry = log_item["entry"]
            payload = log_item["payload"]

            if payload.get("log_type") == "user_message":
                current_user_content = payload.get("prompt") or payload.get("message", "").replace("ADK Web Log: Middleware triggered for prompt: ", "")
                current_eval_id = f"{session_id}-{turn_counter}"
            elif payload.get("log_type") == "final_answer":
                current_agent_response = payload.get("final_answer")
                current_reference = payload.get("ground_truth")
                current_eval_id = f"{session_id}-{turn_counter}"
            
            # If we have both user content and an agent response, it's a complete turn
            if current_user_content and current_agent_response:
                agent_sessions_data.append({
                    "eval_id": current_eval_id,
                    "session_id": session_id,
                    "user_content": current_user_content,
                    "agent_response": current_agent_response,
                    "reference": current_reference,
                    "metric_type": "", # Will be populated later if evaluation runs
                    "metric_value": "" # Will be populated later if evaluation runs
                })
                # Reset for the next turn
                current_user_content = ""
                current_agent_response = ""
                current_reference = ""
                current_eval_id = ""
                turn_counter += 1
    
    # Process simple sessions
    for request_id, raw_logs in simple_sessions_raw_logs.items():
        prompt = ""
        response = ""
        reference = ""
        
        # Sort logs by timestamp to process them in order
        sorted_raw_logs = sorted(raw_logs, key=lambda x: x["entry"].timestamp)

        for log_item in sorted_raw_logs:
            payload = log_item["payload"]
            if "prompt" in payload:
                prompt = payload["prompt"]
            if "response" in payload:
                response = payload["response"]
            if "ground_truth" in payload:
                reference = payload["ground_truth"]
        
        if prompt and response:
            simple_sessions_data.append({
                "eval_id": request_id, # Use request_id as eval_id for simple logs
                "session_id": request_id, # Use request_id as session_id for simple logs
                "user_content": prompt,
                "agent_response": response,
                "reference": reference,
                "metric_type": "simple", # Mark as simple for specific handling
                "metric_value": ""
            })

    agent_df = pd.DataFrame(agent_sessions_data) if agent_sessions_data else None
    simple_df = pd.DataFrame(simple_sessions_data) if simple_sessions_data else None

    return agent_df, simple_df

def export_sessions_to_evalset(last_run_timestamp: str | None):
    """
    Fetches agent logs and exports each session to a separate .evalset.json file.
    """
    print("Fetching logs from Cloud Logging to export as eval sets...")
    
    agent_df, simple_df = get_logs_for_evaluation(last_run_timestamp)

    if (agent_df is None or agent_df.empty) and (simple_df is None or simple_df.empty):
        print("No new logs found to export.")
        return

    output_dir = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets')
    os.makedirs(output_dir, exist_ok=True)
    print(f"Found {len(agent_df) if agent_df is not None else 0} agent session(s) and {len(simple_df) if simple_df is not None else 0} simple log(s). Exporting to: {output_dir}")

    # 2. Process and write each agent session to a file
    if agent_df is not None and not agent_df.empty:
        for session_id, group_df in agent_df.groupby('session_id'):
            conversation_turns = []
            # Sort by eval_id to maintain turn order
            sorted_group_df = group_df.sort_values(by='eval_id')

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
            ground_truth_obj = group_df["ground_truth"].iloc[0] if "ground_truth" in group_df.columns else {}

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
            print(f"  - Successfully exported agent session {session_id} to {output_filename}")

    # 3. Process and write each simple session to a file
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
            output_filename = os.path.join(output_dir, f"rag-agent.evalset.{request_id}.json")
            with open(output_filename, 'w') as f:
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
                # Create a new DataFrame with session_id and all score columns
                temp_df = df[['session_id'] + score_cols].copy()
                
                # Melt the DataFrame to convert score columns into metric_type and metric_value
                melted_df = temp_df.melt(id_vars=['session_id'], var_name='metric_type', value_name='metric_value')
                
                # Clean up metric_type names (e.g., 'fluency_score' -> 'fluency')
                melted_df['metric_type'] = melted_df['metric_type'].str.replace('_score', '')
                
                processed_dfs_for_concat.append(melted_df)

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
        simple_df_cleaned = simple_df[["response", "reference", "prompt"]]
        simple_df_cleaned = simple_df_cleaned.replace("", np.nan)
        simple_df_cleaned = simple_df_cleaned.dropna(subset=["response"], how='any')
        
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
        metrics_to_apply = ["fluency", "coherence", "safety"]
        # Only add ROUGE and BLEU if there are non-empty references
        if not eval_df["reference"].replace('', np.nan).dropna().empty:
            metrics_to_apply.extend(["rouge", "bleu"])
    else:
        metrics_to_apply = ["fluency", "rouge"]


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
        combined_df = get_logs_for_evaluation(None, filter_session_id=args.session_id) # This now returns a single DataFrame
        
        if combined_df is not None and not combined_df.empty:
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

                if args.output_csv_path:
                    original_df = None
                    try:
                        import csv
                        original_df = pd.read_csv(args.output_csv_path, sep=',', quotechar='"', doublequote=True, engine='python', on_bad_lines='skip')
                        original_df.drop_duplicates(subset=['eval_id'], inplace=True)
                    except FileNotFoundError:
                        print(f"Error: Output CSV file not found at {args.output_csv_path}. Cannot save results to the CSV.")
                        original_df = pd.DataFrame() # Create empty df to avoid further errors

                    if not original_df.empty: # Only proceed if original_df was successfully loaded or created
                        # Pivot final_combined_df to wide format
                        pivoted_metrics_df = final_combined_df.pivot(index='session_id', columns='metric_type', values='metric_value')
                        pivoted_metrics_df = pivoted_metrics_df.add_suffix('_score').reset_index()
                        pivoted_metrics_df.rename(columns={'session_id': 'eval_id'}, inplace=True)

                        # Merge original_df with the pivoted metrics
                        merged_output_df = pd.merge(original_df, pivoted_metrics_df, on='eval_id', how='left')
                        
                        # Drop the old metric_type and metric_value columns from the original_df if they exist
                        cols_to_drop_from_original = [col for col in ['metric_type', 'metric_value'] if col in merged_output_df.columns]
                        if cols_to_drop_from_original:
                            merged_output_df.drop(columns=cols_to_drop_from_original, inplace=True)
                    else:
                        # If original_df was empty, then merged_output_df is just the pivoted metrics
                        merged_output_df = pivoted_metrics_df.rename(columns={'session_id': 'eval_id'}) # Ensure eval_id is consistent
                        # If original_df was empty, we still need to ensure the eval_id is correctly set for the merge.
                        # However, if original_df is empty, it means the input CSV was empty, which is an edge case.
                        # For now, let's assume original_df is not empty if args.output_csv_path is provided.
                        # If original_df is empty, we should just save the pivoted_metrics_df.
                        merged_output_df = pivoted_metrics_df.rename(columns={'session_id': 'eval_id'}) # Ensure eval_id is consistent
                        merged_output_df.columns = merged_output_df.columns.str.replace('_score', '') # Remove suffix for consistency if no original_df
                        merged_output_df = merged_output_df.add_suffix('_score') # Add suffix back for consistency
                        merged_output_df.rename(columns={'eval_id_score': 'eval_id'}, inplace=True) # Rename eval_id back


                    
                    try:
                        merged_output_df.to_csv(args.output_csv_path, index=False)
                        print(f"Combined evaluation results saved to CSV: {args.output_csv_path}")
                    except Exception as e:
                        print(f"Error saving combined evaluation results to CSV: {e}")
    else:
        artifacts, final_combined_df = run_evaluation_and_generate_artifacts(all_time=args.all_time)

if __name__ == "__main__":
    main()
