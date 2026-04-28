#!/bin/bash

set -e

# Source .env file if it exists
if [ -f .env ]; then
  source .env
fi

# ==========================================
# Configuration Variables
# ==========================================
AGENT_SOURCE_DIR="agents/$1"
AGENT_NAME="$1"
shift

if [ ! -d "$AGENT_SOURCE_DIR" ]; then
    echo "Error: Directory $AGENT_SOURCE_DIR does not exist."
    exit 1
fi

if [[ "$STAGING_GCS_BUCKET" != gs://* ]]; then
    STAGING_GCS_BUCKET="gs://$STAGING_GCS_BUCKET"
fi

# ==========================================
# 1. Authenticate via gcloud
# ==========================================
echo "Authenticating gcloud with Service Account: $SA_EMAIL..."
gcloud auth activate-service-account "$SA_EMAIL" --key-file="$SERVICE_ACCOUNT_KEY_FILE"

echo "Setting target Google Cloud project to: $PROJECT_ID..."
gcloud config set project "$PROJECT_ID"

# ==========================================
# 2. Set Application Default Credentials (ADC)
# ==========================================
export GOOGLE_APPLICATION_CREDENTIALS="$SERVICE_ACCOUNT_KEY_FILE"

# ==========================================
# 3. Deploy ADK Agent to Agent Engine
# ==========================================
echo "Initiating ADK Agent deployment to Agent Engine..."
echo "Target Region: $REGION | Staging Bucket: $STAGING_GCS_BUCKET"

adk deploy agent_engine \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --staging_bucket="$STAGING_GCS_BUCKET" \
  "$@" \
  "$AGENT_SOURCE_DIR"

echo "Deployment workflow completed!"

# ==========================================
# 4. Deploy Visualization App to Cloud Run using Buildpacks
# ==========================================

# Clean up the service name so it only contains valid characters (no underscores)
CLOUD_RUN_SERVICE_NAME=$(echo "${AGENT_NAME}-viz" | tr '_' '-')

if [ -d "$AGENT_SOURCE_DIR/viz_app" ]; then
    echo "Deploying Visualization App to Cloud Run using buildpacks..."
    gcloud run deploy "$CLOUD_RUN_SERVICE_NAME" \
      --source "$AGENT_SOURCE_DIR/viz_app" \
      --project "$PROJECT_ID" \
      --region "$REGION" \
      --allow-unauthenticated
else
    echo "Deploying Agent Web UI to Cloud Run using buildpacks..."
    
    # We must wrap the agent in a folder so ADK's AgentLoader can find it.
    DEPLOY_TMP=".deploy_tmp"
    rm -rf "$DEPLOY_TMP"
    mkdir -p "$DEPLOY_TMP/$AGENT_NAME"
    cp -r "$AGENT_SOURCE_DIR"/* "$DEPLOY_TMP/$AGENT_NAME/"
    
    # Ensure requirements.txt exists in the root of the deployment
    if [ -f "$DEPLOY_TMP/$AGENT_NAME/requirements.txt" ]; then
        cp "$DEPLOY_TMP/$AGENT_NAME/requirements.txt" "$DEPLOY_TMP/requirements.txt"
    fi

    # Explicitly add dependencies that might be missing from the agent's minimal requirements.txt
    if ! grep -q "google-adk" "$DEPLOY_TMP/requirements.txt"; then
        echo "google-adk>=0.1.0" >> "$DEPLOY_TMP/requirements.txt"
    fi
    if ! grep -q "uvicorn" "$DEPLOY_TMP/requirements.txt"; then
        echo "uvicorn" >> "$DEPLOY_TMP/requirements.txt"
    fi
    if ! grep -q "fastapi" "$DEPLOY_TMP/requirements.txt"; then
        echo "fastapi" >> "$DEPLOY_TMP/requirements.txt"
    fi
    if ! grep -q "google-cloud-bigquery" "$DEPLOY_TMP/requirements.txt"; then
        echo "google-cloud-bigquery" >> "$DEPLOY_TMP/requirements.txt"
    fi
    if ! grep -q "pandas" "$DEPLOY_TMP/requirements.txt"; then
        echo "pandas" >> "$DEPLOY_TMP/requirements.txt"
    fi
    if ! grep -q "python-dotenv" "$DEPLOY_TMP/requirements.txt"; then
        echo "python-dotenv" >> "$DEPLOY_TMP/requirements.txt"
    fi

    cat << 'MAINEOF' > "$DEPLOY_TMP/main.py"
import os
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app

app: FastAPI = get_fast_api_app(
    agents_dir=os.path.dirname(os.path.abspath(__file__)),
    allow_origins=["*"],
    web=True
)
MAINEOF

    cat << 'PROCFILEEOF' > "$DEPLOY_TMP/Procfile"
web: uvicorn main:app --host 0.0.0.0 --port $PORT
PROCFILEEOF

    # Ignore .git and .gitignore but allow everything else
    cat << 'GCLOUDIGNOREEOF' > "$DEPLOY_TMP/.gcloudignore"
.git
.gitignore
GCLOUDIGNOREEOF

    gcloud run deploy "$CLOUD_RUN_SERVICE_NAME" \
      --source "$DEPLOY_TMP" \
      --project "$PROJECT_ID" \
      --region "$REGION" \
      --allow-unauthenticated

    # Clean up the temporary folder
    rm -rf "$DEPLOY_TMP"
    echo "Cloud Run deployment completed successfully!"
fi
