#!/bin/bash

# Script to deploy the agent to Vertex AI Agent Engine

# --- Go to project root ---
cd "$(dirname "$0")/.."

# --- Configuration ---
# Get Project ID from gcloud
PROJECT_ID=$(gcloud config get-value project)
if [ -z "$PROJECT_ID" ]; then
    echo "GCP Project ID not found. Please run 'gcloud config set project YOUR_PROJECT_ID'"
    exit 1
fi

# Set default values
DEFAULT_LOCATION="us-central1"
DEFAULT_AGENT_NAME="rag-agent"
DEFAULT_SERVICE_ACCOUNT=""
DEFAULT_ENV_VARS=""

# --- Command-line argument parsing ---
LOCATION="$DEFAULT_LOCATION"
AGENT_NAME="$DEFAULT_AGENT_NAME"
SERVICE_ACCOUNT="$DEFAULT_SERVICE_ACCOUNT"
ENV_VARS="$DEFAULT_ENV_VARS"

# Support positional agent name if first argument doesn't start with --
if [ $# -gt 0 ] && [[ ! "$1" == --* ]]; then
    AGENT_NAME="$1"
    shift
fi

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --location) LOCATION="$2"; shift ;;
        --agent-name) AGENT_NAME="$2"; shift ;;
        --service-account) SERVICE_ACCOUNT="$2"; shift ;;
        --env-vars) ENV_VARS="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

# --- Create agent if it doesn't exist ---
if [ ! -d "agents/$AGENT_NAME" ]; then
    echo "Agent '$AGENT_NAME' not found. Creating it..."
    uvx agent-starter-pack create "$AGENT_NAME" -d agent_engine -a adk@RAG --auto-approve
    (cd "agents/$AGENT_NAME" && make install)
fi

# --- Create requirements.txt ---
echo "Generating requirements.txt for '$AGENT_NAME'..."
(cd "agents/$AGENT_NAME" && uv export --no-hashes --no-header --no-dev --no-emit-project --no-annotate | grep -E '^(google-adk|google-genai|google-cloud-aiplatform|pydantic|uvicorn|fastapi|cloudpickle|google-cloud-bigquery|python-dotenv|pandas|db-dtypes)' | sed 's/==.*//' > .requirements.txt)
echo "db-dtypes" >> "agents/$AGENT_NAME/.requirements.txt"

# --- Build the deployment command ---
if [ -f "agents/$AGENT_NAME/app/agent_engine_app.py" ]; then
    CMD="uv run agents/$AGENT_NAME/app/agent_engine_app.py \
        --project $PROJECT_ID \
        --location $LOCATION \
        --agent-name $AGENT_NAME \
        --requirements-file agents/$AGENT_NAME/.requirements.txt \
        --extra-packages agents/$AGENT_NAME/app"

    if [ -n "$SERVICE_ACCOUNT" ]; then
        CMD="$CMD --service-account $SERVICE_ACCOUNT"
    fi

    if [ -n "$ENV_VARS" ]; then
        CMD="$CMD --set-env-vars $ENV_VARS"
    fi
elif [ -f "agents/$AGENT_NAME/agent_engine_app.py" ]; then
    CMD="uv run agents/$AGENT_NAME/agent_engine_app.py"
else
    # Generic fallback
    CMD="PROJECT_ID=$PROJECT_ID LOCATION=$LOCATION AGENT_NAME=$AGENT_NAME uv run python .scripts/deploy_generic.py"
fi

# --- Execute the deployment via Cloud Build ---
echo "Submitting deployment for '$AGENT_NAME' to Cloud Build..."
cat <<CB_EOF > ".scripts/cloudbuild_deploy_${AGENT_NAME}.yaml"
steps:
  - name: 'python:3.10'
    entrypoint: 'bash'
    args:
      - '-c'
      - |
        pip install uv
        uv venv
        uv pip install -r "agents/$AGENT_NAME/.requirements.txt"
        $CMD
timeout: 3600s
CB_EOF

gcloud builds submit . --config ".scripts/cloudbuild_deploy_${AGENT_NAME}.yaml" --async

echo "Deployment submitted to Cloud Build successfully!"
echo "You can safely close this session. The build will continue in the background."

rm ".scripts/cloudbuild_deploy_${AGENT_NAME}.yaml"
