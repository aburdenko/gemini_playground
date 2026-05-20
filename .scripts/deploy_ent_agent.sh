#!/bin/bash

# Exit script immediately if any command fails
set -e

# ==========================================
# 1. Load configuration and parameters
# ==========================================
if [ -f .env ]; then
  echo "Loading configuration from .env..."
  source .env
else
  echo "Error: .env file not found."
  exit 1
fi

AGENT_NAME="$1"

if [ -z "$AGENT_NAME" ]; then
    echo "Usage: $0 <AGENT_NAME>"
    exit 1
fi

AGENT_SOURCE_DIR="agents/$AGENT_NAME"

if [ ! -d "$AGENT_SOURCE_DIR" ]; then
    echo "Error: Directory $AGENT_SOURCE_DIR does not exist."
    exit 1
fi

# Display details for Gemini Enterprise
AGENT_DISPLAY_NAME="$AGENT_NAME"
AGENT_DESCRIPTION="This agent was deployed via ADK to Agent Engine and published to Gemini Enterprise."

# ==========================================
# 2. Authenticate
# ==========================================
echo "Authenticating gcloud with Service Account: $SA_EMAIL..."
gcloud auth activate-service-account "$SA_EMAIL" --key-file="$SERVICE_ACCOUNT_KEY_FILE"
gcloud config set project "$PROJECT_ID"
export GOOGLE_APPLICATION_CREDENTIALS="$SERVICE_ACCOUNT_KEY_FILE"

if [[ "$STAGING_GCS_BUCKET" != gs://* ]]; then
    STAGING_GCS_BUCKET="gs://$STAGING_GCS_BUCKET"
fi

# ==========================================
# 3. Deploy ADK Agent to Agent Engine
# ==========================================
echo "Initiating ADK Agent deployment to Agent Engine for $AGENT_NAME..."
echo "Target Region: $REGION | Staging Bucket: $STAGING_GCS_BUCKET"

# Create a clean .env file without empty variables, comments, or reserved variables (like GOOGLE_APPLICATION_CREDENTIALS)
grep -v '^#' .env | grep -v '^\s*$' | awk -F '=' 'length($2) > 0' | grep -v 'GOOGLE_APPLICATION_CREDENTIALS' > .env.deploy

# Run deployment and capture output to extract the Agent Engine ID
DEPLOY_OUTPUT=$(adk deploy agent_engine \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --staging_bucket="$STAGING_GCS_BUCKET" \
  --env_file=".env.deploy" \
  "$AGENT_SOURCE_DIR" 2>&1 | tee /dev/tty)

# Clean up the temporary env file
rm -f .env.deploy

# Extract the Reasoning Engine ID
if [[ "$DEPLOY_OUTPUT" =~ Resource\ name:\ (projects/[0-9]+/locations/[a-zA-Z0-9-]+/reasoningEngines/[0-9]+) ]]; then
    AGENT_ENGINE_ID="${BASH_REMATCH[1]}"
    echo "Successfully extracted Agent Engine ID: $AGENT_ENGINE_ID"
else
    echo "Error: Could not automatically extract the Agent Engine ID from the deployment output."
    exit 1
fi

# ==========================================
# 4. Deploy FastMCP Server to Cloud Run (if present)
# ==========================================
if [ -f "$AGENT_SOURCE_DIR/mcp_server.py" ]; then
    echo "Deploying FastMCP Server to Cloud Run..."
    MCP_SERVICE_NAME="${AGENT_NAME}-mcp"
    
    gcloud run deploy "$MCP_SERVICE_NAME" \
        --source "$AGENT_SOURCE_DIR" \
        --region "$REGION" \
        --project "$PROJECT_ID" \
        --allow-unauthenticated \
        --set-env-vars "NCBI_API_KEY=${NCBI_API_KEY:-}" \
        --command="python3,mcp_server.py" \
        --format="value(status.url)" > /tmp/mcp_url.txt
    
    MCP_URL=$(cat /tmp/mcp_url.txt)
    echo "=========================================="
    echo "MCP Server successfully deployed at: $MCP_URL/sse"
    echo "Register this URL in the Gemini Enterprise Extensions UI!"
    echo "=========================================="
fi

# ==========================================
# 5. Publish to Gemini Enterprise (Discovery Engine)
# ==========================================
echo "Registering Agent Engine instance ($AGENT_ENGINE_ID) with Gemini Enterprise..."

# Using the custom deployment script to register to Gemini Enterprise directly via Discovery Engine v1alpha
python3 .scripts/deploy_generic_agent.py \
    "${PROJECT_NUMBER}" \
    "${GEMINI_ENT_APP_ID}" \
    "${AGENT_DISPLAY_NAME}" \
    "${AGENT_ENGINE_ID}"

echo "Agent '$AGENT_NAME' successfully deployed to Agent Engine and published to Gemini Enterprise!"
