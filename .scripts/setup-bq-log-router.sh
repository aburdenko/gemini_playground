#!/bin/bash

# --- Configuration ---
GCP_PROJECT_ID="kallogjeri-project-345114" # Replace with your GCP project ID
GCP_REGION="us-central1" # Replace if your agent is in a different region
AGENT_ENGINE_ID="3233200802893922304" # Your deployed travel-concierge agent ID
BIGQUERY_DATASET_ID="travel_concierge" # Name for your BigQuery dataset
LOG_SINK_NAME="travel-concierge-log-sink" # Name for your log sink

# --- Script Logic ---

echo "--- Starting BigQuery Log Router Setup ---"
echo "Project: $GCP_PROJECT_ID"
echo "Region: $GCP_REGION"
echo "Agent Engine ID: $AGENT_ENGINE_ID"
echo "BigQuery Dataset: $BIGQUERY_DATASET_ID"
echo "Log Sink Name: $LOG_SINK_NAME"
echo ""

# 1. Enable BigQuery API if not already enabled
echo "1. Ensuring BigQuery API is enabled..."
gcloud services enable bigquery.googleapis.com --project="$GCP_PROJECT_ID"

# 2. Create BigQuery Dataset if it doesn't exist
echo "2. Creating BigQuery dataset '$BIGQUERY_DATASET_ID' (if it doesn't exist)..."
if gcloud alpha bq datasets describe "$BIGQUERY_DATASET_ID" --project="$GCP_PROJECT_ID" &>/dev/null; then
  echo "BigQuery dataset '$BIGQUERY_DATASET_ID' already exists."
else
  gcloud alpha bq datasets create "$BIGQUERY_DATASET_ID" --project="$GCP_PROJECT_ID" --location="$GCP_REGION"
  if [ $? -ne 0 ]; then
    echo "Error creating BigQuery dataset. Exiting."
    exit 1
  fi
  echo "BigQuery dataset '$BIGQUERY_DATASET_ID' created."
fi
echo ""

# 3. Create Log Sink
echo "3. Creating Log Sink '$LOG_SINK_NAME'..."
# The filter targets logs from the specific reasoning engine (Agent Engine)
LOG_FILTER="resource.type="reasoningengine" AND resource.labels.reasoning_engine_id="$AGENT_ENGINE_ID""
DESTINATION="bigquery.googleapis.com/projects/$GCP_PROJECT_ID/datasets/$BIGQUERY_DATASET_ID"

CREATE_SINK_OUTPUT=$(gcloud logging sinks create "$LOG_SINK_NAME" "$DESTINATION" --quiet \
  --log-filter="$LOG_FILTER" \
  --project="$GCP_PROJECT_ID" \
  --format="value(writerIdentity)" 2>&1)

if [ $? -ne 0 ]; then
  echo "Error creating log sink. It might already exist or there's a permission issue."
  echo "Output: $CREATE_SINK_OUTPUT"
  # Try to get writerIdentity if sink already exists
  WRITER_IDENTITY=$(gcloud logging sinks describe "$LOG_SINK_NAME" --project="$GCP_PROJECT_ID" --format="value(writerIdentity)")
  if [ -z "$WRITER_IDENTITY" ]; then
    echo "Could not retrieve writerIdentity. Exiting."
    exit 1
  else
    echo "Log sink '$LOG_SINK_NAME' likely already exists. Proceeding to set permissions using existing writer identity."
  fi
else
  WRITER_IDENTITY="$CREATE_SINK_OUTPUT"
  echo "Log Sink '$LOG_SINK_NAME' created successfully."
fi
echo "Writer Identity: $WRITER_IDENTITY"
echo ""

# 4. Grant BigQuery Data Editor role to the Log Sink's writer identity on the dataset
echo "4. Granting BigQuery Data Editor permissions to $WRITER_IDENTITY on dataset $BIGQUERY_DATASET_ID..."
# 4. Grant BigQuery Data Editor role to the Log Sink's writer identity on the dataset
echo "4. Granting BigQuery Data Editor permissions to $WRITER_IDENTITY on dataset $BIGQUERY_DATASET_ID..."

# Install jq if not already installed
if ! command -v jq &> /dev/null
then
    echo "jq not found, installing..."
    sudo apt-get update && sudo apt-get install jq -y
    if [ $? -ne 0 ]; then
        echo "Error installing jq. Exiting."
        exit 1
    fi
fi

# Get current IAM policy
bq show --format=json --dataset "$GCP_PROJECT_ID":"$BIGQUERY_DATASET_ID" > policy.json
if [ $? -ne 0 ]; then
  echo "Error retrieving current BigQuery dataset policy. Exiting."
  exit 1
fi

# Extract the service account email
SERVICE_ACCOUNT_EMAIL=$(echo "$WRITER_IDENTITY" | sed 's/^serviceAccount://')

# Add the new binding to the JSON using jq
jq --arg member_email "$SERVICE_ACCOUNT_EMAIL" \
   --arg role_name "roles/bigquery.dataEditor" \
   '.access += [{"role": $role_name, "userByEmail": $member_email}]' \
   policy.json > policy_modified.json
if [ $? -ne 0 ]; then
  echo "Error modifying BigQuery dataset policy with jq. Exiting."
  exit 1
fi

# Update the dataset with the modified IAM policy
bq update --source policy_modified.json --dataset "$GCP_PROJECT_ID":"$BIGQUERY_DATASET_ID"
if [ $? -ne 0 ]; then
  echo "Error updating BigQuery dataset policy. Exiting."
  exit 1
fi

# Clean up temporary files
rm policy.json policy_modified.json

echo "Permissions granted successfully."
echo ""
echo "--- BigQuery Log Router Setup Complete! ---"
echo "Logs from Agent Engine '$AGENT_ENGINE_ID' will now be routed to BigQuery dataset '$BIGQUERY_DATASET_ID'."
echo "You can view logs in BigQuery under your project's '$BIGQUERY_DATASET_ID' dataset."
