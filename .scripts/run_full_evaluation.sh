#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$(dirname "$0")"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")" # Assuming .scripts is directly under project root

# --- Source .env file ---
ENV_FILE="${PROJECT_ROOT}/.env"
if [ -f "$ENV_FILE" ]; then
  echo "Sourcing environment variables from ${ENV_FILE}"
  export $(grep -v '^#' "$ENV_FILE" | sed 's/#.*//' | xargs)
else
  echo "Warning: .env file not found at ${ENV_FILE}. Proceeding without it."
fi

# --- Activate Python Virtual Environment ---
VENV_PATH="${PROJECT_ROOT}/.venv/python3.12"
if [ -d "$VENV_PATH" ]; then
  echo "Activating Python virtual environment at ${VENV_PATH}"
  source "${VENV_PATH}/bin/activate"
else
  echo "Error: Virtual environment not found at ${VENV_PATH}. Please run 'source .scripts/configure.sh' manually first." >&2
  exit 1
fi

# Define relative paths based on PROJECT_ROOT
CREATE_EVALSET_SCRIPT="${SCRIPT_DIR}/create_evalset_from_csv.py"
EVAL_AGENT_SCRIPT="${SCRIPT_DIR}/eval_agent.py"
EVALSET_DIR="${PROJECT_ROOT}/agents/rag-agent/eval_sets"


# Delete old evaluation files
echo "Deleting old evaluation files..."
find "${EVALSET_DIR}" -name "generated_evalset_*.evalset.json" -delete
find "${EVALSET_DIR}" -name "*_radar_chart_*.png" -delete

# Set CSV_PATH, using argument if provided, otherwise find first .csv in directory
if [ -n "$1" ]; then
  CSV_PATH="$1"
else
  CSV_PATH=$(find "$EVALSET_DIR" -name "*.csv" -print -quit)
fi

if [ -z "$CSV_PATH" ]; then
  echo "Error: No CSV file found in $EVALSET_DIR" >&2
  exit 1
fi

TIMESTAMP=$(date +%Y%m%d%H%M%S)
OUTPUT_JSON="${EVALSET_DIR}/generated_evalset_${TIMESTAMP}.evalset.json"

echo "Step 1: Generating .evalset.json from CSV: $CSV_PATH"
python3 "$CREATE_EVALSET_SCRIPT" "$CSV_PATH" "$OUTPUT_JSON"

if [ $? -ne 0 ]; then
  echo "Error: Failed to generate .evalset.json from CSV." >&2
  exit 1
fi

echo "Step 2: Running evaluation with generated .evalset.json..."
python3 "$EVAL_AGENT_SCRIPT" --use-evalset-files --all-time --output-csv-path "$CSV_PATH" --evalset-file "$OUTPUT_JSON"

if [ $? -ne 0 ]; then
  echo "Error: Evaluation failed." >&2
  exit 1
fi

echo "Full evaluation workflow completed successfully."
echo "Generated evalset: $OUTPUT_JSON"
