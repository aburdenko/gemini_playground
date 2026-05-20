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
if [ -z "$SERVICE_ACCOUNT_KEY_FILE" ] || [ ! -f "$SERVICE_ACCOUNT_KEY_FILE" ]; then
  echo "WARNING: Service Account key file ('$SERVICE_ACCOUNT_KEY_FILE') not found or invalid."
  echo "Falling back to active gcloud credentials (Application Default Credentials)."
  unset GOOGLE_APPLICATION_CREDENTIALS
else
  echo "Authenticating gcloud with Service Account: $SA_EMAIL..."
  gcloud auth activate-service-account "$SA_EMAIL" --key-file="$SERVICE_ACCOUNT_KEY_FILE"
  export GOOGLE_APPLICATION_CREDENTIALS="$SERVICE_ACCOUNT_KEY_FILE"
fi

echo "Setting target Google Cloud project to: $PROJECT_ID..."
gcloud config set project "$PROJECT_ID"


# ==========================================
# 3. Deploy ADK Agent to Agent Engine
# ==========================================
echo "Initiating ADK Agent deployment to Agent Engine..."
echo "Target Region: $REGION | Staging Bucket: $STAGING_GCS_BUCKET"

# Run deployment and capture output to extract the Agent Engine ID
DEPLOY_OUTPUT=$(adk deploy agent_engine \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --staging_bucket="$STAGING_GCS_BUCKET" \
  "$@" \
  "$AGENT_SOURCE_DIR" 2>&1 | tee /dev/tty)

# Extract the Reasoning Engine ID
if [[ "$DEPLOY_OUTPUT" =~ Resource\ name:\ (projects/[0-9]+/locations/[a-zA-Z0-9-]+/reasoningEngines/[0-9]+) ]]; then
    NEW_AE_ID="${BASH_REMATCH[1]}"
    echo "Extracted Agent Engine ID: $NEW_AE_ID"
    
    # Automatically update the registration script
    sed -i "s|AGENT_ENGINE_ID=.*|AGENT_ENGINE_ID=\"$NEW_AE_ID\"|" .scripts/deploy_ent_agent.sh
    echo "Updated .scripts/deploy_ent_agent.sh with new Agent Engine ID."
else
    echo "Warning: Could not automatically extract the Agent Engine ID from the deployment output."
fi

echo "Agent Engine deployment workflow completed!"

# ==========================================
# 4. Deploy Visualization App to Cloud Run
# ==========================================
echo "Deploying Agent to Cloud Run (Standalone with UI)..."

adk deploy cloud_run "$AGENT_SOURCE_DIR" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --with_ui \
    -- \
    --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=True,PROJECT_ID=$PROJECT_ID,REGION=$REGION,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,DATASET=$DATASET,TABLE_OR_VIEW=$TABLE_OR_VIEW,VIZ_EXPLORE_URL=$VIZ_EXPLORE_URL,VIZ_TIMELINE_URL=$VIZ_TIMELINE_URL,VIZ_MINDMAP_URL=$VIZ_MINDMAP_URL"

echo "Cloud Run deployment completed successfully!"
