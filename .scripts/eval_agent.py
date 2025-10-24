#!/usr/bin/env python3
import matplotlib
matplotlib.use('Agg')
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from google.cloud import aiplatform
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

PROJECT_ID = os.environ.get("PROJECT_ID", "your-gcp-project-id")
LOCATION = os.environ.get("REGION", "us-central1")
BUCKET_NAME = os.environ.get("STAGING_GCS_BUCKET", "your-bucket-name")
SHORT_LOG_NAME = os.environ.get("LOG_NAME", "run_gemini_from_file")
EXPERIMENT_NAME = "gemini-playground-evaluation"
LOG_NAME = f"projects/{PROJECT_ID}/logs/{SHORT_LOG_NAME}"
JUDGEMENT_MODEL_NAME = os.environ.get("JUDGEMENT_MODEL_NAME", "gemini-1.5-flash")

TIMESTAMP_FILE = "last_run_timestamp.txt"

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
    # Broaden filter to get both agent sessions (with session_id) and simple ADK web logs (with prompt).
    # Broaden filter to get both agent sessions (with session_id) and simple ADK web logs (with prompt).
    base_filter = f'logName="{LOG_NAME}" AND (jsonPayload.session_id:* OR jsonPayload.prompt:*)'
    if last_run_timestamp:
        log_filter = f'{base_filter} AND timestamp >= "{last_run_timestamp}"'
    else:
        log_filter = base_filter

    # 1. Group logs by session_id and collect simple logs
    sessions = {}
    simple_logs = []
    for entry in logging_client.list_entries(filter_=log_filter):
        payload = {} # Initialize payload as empty dict
        if isinstance(entry.payload, dict):
            payload = entry.payload
        elif isinstance(entry.payload, str):
            # For text payloads, we can put the text into a 'message' field
            payload = {"message": entry.payload}
        else:
            # Fallback for other types of payloads
            payload = {"message": "Unsupported log entry type"}
        if "session_id" in payload:
            session_id = payload.get("session_id")
            if session_id not in sessions:
                sessions[session_id] = []
            sessions[session_id].append(payload)
        elif "prompt" in payload and "session_id" not in payload:
            # This is likely a simple log from ADK Web
            simple_logs.append(payload)

    if not sessions and not simple_logs:
        print("No new logs found to export.")
        return

    output_dir = os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent', 'eval_sets')
    os.makedirs(output_dir, exist_ok=True)
    print(f"Found {len(sessions)} agent session(s) and {len(simple_logs)} simple log(s). Exporting to: {output_dir}")

    # 2. Process and write each agent session to a file
    for session_id, logs in sessions.items():
        conversation_turns = []
        # Sort logs by step to reconstruct the conversation order
        sorted_logs = sorted(logs, key=lambda x: x.get("step", 0))
        
        for log in sorted_logs:
            if log.get("log_type") == "final_answer":
                # Find the corresponding prompt for this answer
                prompt = ""
                for prev_log in reversed(sorted_logs[:log.get("step")]):
                    if "initial_prompt" in prev_log: # This will be the first prompt
                        prompt = prev_log.get("initial_prompt")
                        break

                conversation_turns.append({
                    "user_content": {"parts": [{"text": prompt}]},
                    "final_response": {"parts": [{"text": log.get("final_answer", "")}]}
                })

        eval_set = {"eval_set_id": session_id, "eval_cases": [{"eval_id": f"case-{session_id}", "conversation": conversation_turns}]}
        output_filename = os.path.join(output_dir, f"rag-agent.evalset.{session_id}.json")
        with open(output_filename, 'w') as f:
            json.dump(eval_set, f, indent=2)
        print(f"  - Successfully exported session {session_id} to {output_filename}")

    # 3. Process and write each simple log to a file
    for i, log in enumerate(simple_logs):
        # Generate a unique ID for this simple log based on its content and timestamp
        prompt_hash = hashlib.sha256(log.get("prompt", "").encode()).hexdigest()[:8]
        eval_set_id = f"adk-web-{datetime.now().strftime('%Y%m%d%H%M%S')}-{prompt_hash}"

        conversation_turns = [{
            "user_content": {"parts": [{"text": log.get("prompt", "")}]},
            # The response is a placeholder in the log, so we reflect that.
            "final_response": {"parts": [{"text": log.get("response", "")}]}
        }]

        eval_set = {"eval_set_id": eval_set_id, "eval_cases": [{"eval_id": f"case-{eval_set_id}", "conversation": conversation_turns}]}
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

def run_evaluation_and_generate_artifacts(all_time: bool = False):
    """
    Runs the main evaluation flow.
    
    Args:
        all_time: If True, fetches all logs. Otherwise, fetches logs since the last run.
    """
    current_time_str = datetime.now().strftime('%Y%m%d-%H%M%S')
    experiment_name = EXPERIMENT_NAME
    aiplatform.init(project=PROJECT_ID, location=LOCATION, experiment=experiment_name)

    last_run = None
    if not all_time:
        last_run = get_last_run_timestamp()

    agent_df, simple_df = get_logs_for_evaluation(last_run)
    
    artifacts = {}

    if agent_df is not None and not agent_df.empty:
        task_quality_metric = CustomMetric(
            name="agent_task_quality",
            prompt=(
                "You are an expert evaluator. Given the agent's instruction, the ground truth, the final answer, and the full execution trace, "
                "please evaluate the agent's overall performance on a scale of 1-5. Consider if the agent achieved the goal, "
                "if it used tools correctly, and if its final answer is accurate and complete.\n\n"
                "Instruction: {instruction}\n"
                "Ground Truth: {reference}\n"
                "Final Answer: {final_answer}\n"
                "Execution Trace:\n{full_trace}"
            ),
            result_parser="rating_reason"
        )
        agentic_metrics = [
            task_quality_metric, "tool_call_validity", "tool_name_match",
            "tool_parameter_match", "rouge"
        ]
        
        summary_metrics, metrics_df = _execute_evaluation_run_for_artifacts(
            eval_df=agent_df,
            metrics=agentic_metrics,
            run_name_suffix="-agentic",
            experiment_name=experiment_name,
            current_time_str=current_time_str
        )
        if summary_metrics:
            artifacts['agentic_radar_chart'] = generate_radar_chart(summary_metrics, "-agentic")
        if metrics_df is not None:
            artifacts['agentic_metrics_csv'] = generate_metrics_csv(metrics_df)


    if simple_df is not None and not simple_df.empty:
        simple_metrics = ["fluency", "coherence", "safety", "rouge"]
        
        summary_metrics, metrics_df = _execute_evaluation_run_for_artifacts(
            eval_df=simple_df,
            metrics=simple_metrics,
            run_name_suffix="-simple",
            experiment_name=experiment_name,
            current_time_str=current_time_str
        )
        if summary_metrics:
            artifacts['simple_radar_chart'] = generate_radar_chart(summary_metrics, "-simple")
        if metrics_df is not None:
            artifacts['simple_metrics_csv'] = generate_metrics_csv(metrics_df)

    if not all_time:
        save_current_timestamp()
    
    return artifacts

def _execute_evaluation_run_for_artifacts(
    eval_df: pd.DataFrame,
    metrics: list,
    run_name_suffix: str,
    experiment_name: str,
    current_time_str: str
):
    run_name = f"eval-run-{current_time_str}{run_name_suffix}"
    full_judgement_model_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{JUDGEMENT_MODEL_NAME}"
    autorater_config = AutoraterConfig(autorater_model=full_judgement_model_name)

    eval_task = EvalTask(
        dataset=eval_df,
        metrics=metrics,
        autorater_config=autorater_config,
    )

    evaluation_result = eval_task.evaluate(
        experiment_run_name=run_name
    )
    
    return evaluation_result.summary_metrics, evaluation_result.metrics_table

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
    args = parser.parse_args()

    if args.export_sessions:
        export_sessions_to_evalset(get_last_run_timestamp() if not args.all_time else None)
        if not args.all_time:
            save_current_timestamp()
    else:
        run_evaluation_and_generate_artifacts(all_time=args.all_time)

if __name__ == "__main__":
    main()