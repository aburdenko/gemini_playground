#!/bin/bash
# Fetch the latest Cloud Run URL for a given visualization type and save it to .env
VIZ_TYPE=${1:-mindmap}
# Find the latest service matching viz-$VIZ_TYPE that has a valid URL
URL=$(gcloud run services list --filter="metadata.name:viz-${VIZ_TYPE}* AND status.url:*" --sort-by="~metadata.creationTimestamp" --limit=1 --format="value(status.url)")

if [ -n "$URL" ]; then
    ENV_VAR="VIZ_$(echo $VIZ_TYPE | tr '[:lower:]' '[:upper:]')_URL"
    # Remove existing entry if it exists
    sed -i "/^${ENV_VAR}=/d" .env
    # Append the new URL
    echo "${ENV_VAR}=${URL}" >> .env
    echo "Saved ${ENV_VAR}=${URL} to .env"
else
    echo "No Cloud Run service found for visualization type: $VIZ_TYPE"
fi
