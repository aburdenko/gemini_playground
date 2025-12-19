#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$(dirname "$0")"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate Python Virtual Environment
VENV_PATH="${PROJECT_ROOT}/.venv/python3.12"
if [ -d "$VENV_PATH" ]; then
  source "${VENV_PATH}/bin/activate"
else
  echo "Error: Virtual environment not found at ${VENV_PATH}. Please run 'source .scripts/configure.sh' manually first." >&2
  exit 1
fi

echo "Exporting evaluation logs to CSV..."
uv run python "${SCRIPT_DIR}/eval_agent.py" --export-to-csv

if [ $? -ne 0 ]; then
  echo "Error: Failed to export evaluation logs." >&2
  exit 1
fi

echo "Evaluation logs successfully exported to agents/rag-agent/eval_sets/eval_test_cases.csv"
