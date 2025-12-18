#!/bin/bash

# Script to deploy the agent to Vertex AI Agent Space

# --- Go to project root ---
cd "$(dirname "$0")"/..

# --- Configuration ---
# Get Project ID from gcloud
PROJECT_ID=$(gcloud config get-value project)
if [ -z "$PROJECT_ID" ]; then
    echo "GCP Project ID not found. Please run 'gcloud config set project YOUR_PROJECT_ID'"
    exit 1
fi

# Load environment variables from .env file
if [ -f .env ]; then
    export $(cat .env | sed 's/#.*//g' | xargs)
fi

# --- Install agent_registration_tool dependencies ---
echo "Installing agent_registration_tool dependencies..."
uv pip install google-cloud-aiplatform google-auth

# Set default values
DEFAULT_LOCATION="us-central1"
DEFAULT_AGENT_NAME="rag-agent"
DEFAULT_DEPLOYMENT_ID="" # This will be the reasoning engine ID

# --- Command-line argument parsing ---
LOCATION="$DEFAULT_LOCATION"
AGENT_NAME="$DEFAULT_AGENT_NAME"
DEPLOYMENT_ID="$DEFAULT_DEPLOYMENT_ID"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --location) LOCATION="$2"; shift ;;
        --agent-name) AGENT_NAME="$2"; shift ;;
        --deployment-id) DEPLOYMENT_ID="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ -z "$DEPLOYMENT_ID" ]; then
    echo "Deployment ID is required. Please provide it with --deployment-id"
    exit 1
fi

if [ -z "$AGENT_SPACE_URL" ]; then
    echo "AGENT_SPACE_URL not found in .env file."
    exit 1
fi

# --- Build the deployment command ---
CMD="uv run .scripts/deploy_to_agent_space.py \
    --project $PROJECT_ID \
    --location $LOCATION \
    --agent-name $AGENT_NAME \
    --deployment-id $DEPLOYMENT_ID \
    --agent-space-url $AGENT_SPACE_URL"

# --- Execute the deployment ---
echo "Running deployment command:"
echo "$CMD"
eval "$CMD"