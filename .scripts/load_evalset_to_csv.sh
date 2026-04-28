#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$(dirname "$0")"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate Python Virtual Environment
VENV_PATH="${PROJECT_ROOT}/.venv/python3.12"
if [ -d "$VENV_PATH" ]; then
  source "${VENV_PATH}/bin/activate"
else
  echo "Error: Virtual environment not found at ${VENV_PATH}." >&2
  exit 1
fi

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <input_json_path> <output_csv_path>"
    exit 1
fi

INPUT_JSON="$1"
OUTPUT_CSV="$2"

echo "Loading data from ${INPUT_JSON} into ${OUTPUT_CSV}..."
uv run python "${SCRIPT_DIR}/convert_evalset_to_csv.py" "${INPUT_JSON}" "${OUTPUT_CSV}"
echo "Done."
