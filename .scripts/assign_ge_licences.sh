#!/bin/bash

# ==============================================================================
# Script to find an available Discovery Engine license and assign it to a user.
#
# It retracts the user's license from the dev (source) project first to free
# capacity, then finds an unassigned license from the billing account and
# assigns it to the user in the target project.
#
# Usage:
# ./assign_license.sh <SOURCE_PROJECT_ID> <TARGET_PROJECT_ID> <USER_EMAIL> [LOCATION]
# ==============================================================================

set -e # Exit immediately if a command exits with a non-zero status.

if [ "$#" -lt 3 ]; then
    echo "Usage: $0 <SOURCE_PROJECT_ID> <TARGET_PROJECT_ID> <USER_EMAIL> [LOCATION]"
    exit 1
fi

SOURCE_PROJECT_ID="$1"
TARGET_PROJECT_ID="$2"
USER_EMAIL="$3"
LOCATION="${4:-us}" 

if ! command -v jq &> /dev/null; then
    echo "Error: 'jq' is not installed."
    exit 1
fi

echo "--- Script Parameters ---"
echo "Source Project: ${SOURCE_PROJECT_ID}"
echo "Target Project: ${TARGET_PROJECT_ID}"
echo "User Email:     ${USER_EMAIL}"
echo "Location:       ${LOCATION}"
echo "-------------------------"
echo

# Helper function for authenticated curl requests
api_request() {
    local method="$1"
    local url="$2"
    local data="$3"
    local proj="$4" # Add explicit project ID for quotas
    
    local headers=(-H "Authorization: Bearer $(gcloud auth print-access-token)" -H "Content-Type: application/json" -H "X-Goog-User-Project: ${proj}")
    
    if [ -n "$data" ]; then
        curl --silent --show-error -X "$method" "${headers[@]}" -d "$data" "$url"
    else
        curl --silent --show-error -X "$method" "${headers[@]}" "$url"
    fi
}

echo "Step 0: Enabling necessary billing APIs..."
gcloud services enable cloudbilling.googleapis.com --project "${SOURCE_PROJECT_ID}"

echo "Step 1: Retracting license for ${USER_EMAIL} from source project ${SOURCE_PROJECT_ID}..."
RETRACT_PAYLOAD=$(jq -n \
  --arg user_email "$USER_EMAIL" \
  '{
    "inlineSource": {
      "userLicenses": [
        {
          "userPrincipal": $user_email
        }
      ],
      "updateMask": {
        "paths": [
          "userPrincipal",
          "licenseConfig"
        ]
      }
    },
    "deleteUnassignedUserLicenses": true
  }')

RETRACT_API_URL="https://${LOCATION}-discoveryengine.googleapis.com/v1/projects/${SOURCE_PROJECT_ID}/locations/${LOCATION}/userStores/default_user_store:batchUpdateUserLicenses"
RETRACT_RESULT=$(api_request "POST" "$RETRACT_API_URL" "$RETRACT_PAYLOAD" "${SOURCE_PROJECT_ID}")
echo "Retraction API Response:"
echo "$RETRACT_RESULT" | jq . || echo "$RETRACT_RESULT"

echo "Step 2: Resolving billing account for source project ${SOURCE_PROJECT_ID}..."
BILLING_INFO=$(gcloud beta billing projects describe "${SOURCE_PROJECT_ID}" --format="json")
BILLING_ACCOUNT_NAME=$(echo "$BILLING_INFO" | jq -r '.billingAccountName')

if [ -z "$BILLING_ACCOUNT_NAME" ] || [ "$BILLING_ACCOUNT_NAME" == "null" ]; then
    echo "Error: Could not find billing account for project ${SOURCE_PROJECT_ID}."
    exit 1
fi

echo "Billing Account: ${BILLING_ACCOUNT_NAME}"

echo "Step 3: Fetching all available license configurations from billing account ${BILLING_ACCOUNT_NAME}..."
LICENSES_RESPONSE=$(api_request "GET" "https://discoveryengine.googleapis.com/v1alpha/${BILLING_ACCOUNT_NAME}/billingAccountLicenseConfigs" "" "${TARGET_PROJECT_ID}")

if echo "$LICENSES_RESPONSE" | jq -e '.error' > /dev/null; then
    echo "Error fetching license configurations:"
    echo "$LICENSES_RESPONSE" | jq .
    exit 1
fi

ALL_LICENSES=$(echo "$LICENSES_RESPONSE" | jq -r '.billingAccountLicenseConfigs[]?.name')

if [ -z "$ALL_LICENSES" ]; then
    echo "Error: No license configurations found in billing account."
    exit 1
fi

echo "Step 4: Fetching all assigned licenses in target project ${TARGET_PROJECT_ID}..."
ASSIGNED_RESPONSE=$(api_request "GET" "https://${LOCATION}-discoveryengine.googleapis.com/v1/projects/${TARGET_PROJECT_ID}/locations/${LOCATION}/userStores/default_user_store/userLicenses" "" "${TARGET_PROJECT_ID}")

ASSIGNED_LICENSES=$(echo "$ASSIGNED_RESPONSE" | jq -r '.userLicenses[]?.licenseConfig')

echo "Step 5: Comparing lists to find an unassigned license..."
FREE_LICENSE=""

TARGET_PROJECT_NUMBER=$(gcloud projects describe "${TARGET_PROJECT_ID}" --format="value(projectNumber)")

for license in $ALL_LICENSES; do
    LICENSE_ID=$(basename "$license")
    # Using 'global' location for license configs as observed in GUI behavior
    PROJECT_LICENSE_CONFIG="projects/${TARGET_PROJECT_NUMBER}/locations/global/licenseConfigs/${LICENSE_ID}"
    
    if ! echo "$ASSIGNED_LICENSES" | grep -q -w "$PROJECT_LICENSE_CONFIG"; then
        FREE_LICENSE="$PROJECT_LICENSE_CONFIG"
        break
    fi
done

if [ -n "$FREE_LICENSE" ]; then
    echo "Success! Found a free license: ${FREE_LICENSE}"
    echo
    echo "Step 6: Assigning license to ${USER_EMAIL} in target project..."

    # API limitation: updateMask breaks the reassignment payload, so we omit it here and rely on inlineSource
    JSON_PAYLOAD=$(jq -n \
                  --arg user_email "$USER_EMAIL" \
                  --arg license_config "$FREE_LICENSE" \
                  '{
                    "inlineSource": {
                      "userLicenses": [
                        {
                          "userPrincipal": $user_email,
                          "licenseConfig": $license_config
                        }
                      ]
                    }
                  }')

    API_URL="https://${LOCATION}-discoveryengine.googleapis.com/v1/projects/${TARGET_PROJECT_ID}/locations/${LOCATION}/userStores/default_user_store:batchUpdateUserLicenses"
    ASSIGN_RESULT=$(api_request "POST" "$API_URL" "$JSON_PAYLOAD" "${TARGET_PROJECT_ID}")

    echo "✅ License successfully assigned to ${USER_EMAIL}."
    echo "API Response:"
    echo "$ASSIGN_RESULT" | jq . || echo "$ASSIGN_RESULT"

else
    echo "❌ Error: No unassigned licenses found."
    exit 1
fi
