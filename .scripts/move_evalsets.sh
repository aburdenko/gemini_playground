#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_DIR="${REPO_ROOT}/agents/rag-agent"
DEST_DIR="${REPO_ROOT}/agents/rag-agent/eval_sets"

mkdir -p "$DEST_DIR"

find "$SOURCE_DIR" -maxdepth 1 -type f -name "*.evalset.json" -exec mv {} "$DEST_DIR" \;

if [ $? -eq 0 ]; then
    echo "Successfully moved .evalset.json files from $SOURCE_DIR to $DEST_DIR"
else
    echo "Error moving files."
    exit 1
fi
