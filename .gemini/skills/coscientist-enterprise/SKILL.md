---
name: coscientist-enterprise
description: Guidance and commands for interacting with Co-Scientist via the v1alpha API (Discovery Engine). Use this when starting, monitoring, or testing Co-Scientist instances via REST API endpoints.
---

# 1. Get an access token using standard gcloud
TOKEN=$(gcloud auth application-default print-access-token)

# 2. Call the Discovery Engine API endpoints directly

## Co-Scientist REST API

This document provides instructions on how to programmatically start and monitor a Co-Scientist session via the v1alpha API (Discovery Engine v1alpha endpoints) using standard HTTP requests.

To interact with these endpoints, you must have the Discovery Engine User IAM role on the GCP project and provide your `PROJECT_NUMBER` and `APP_ID`. Since Co-Scientist instances can take a while to finish, the typical flow involves starting the generation and polling the session later.

### 1. Create a Session
Before triggering a generation, you must create a new conversational session within your engine.

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: YOUR_PROJECT_NUMBER" \
  "https://discoveryengine.googleapis.com/v1alpha/projects/YOUR_PROJECT_NUMBER/locations/global/collections/default_collection/engines/YOUR_APP_ID/sessions" \
  -d '{"state": "IN_PROGRESS"}'
```
This returns a JSON object containing the `name` of the newly created session. You must extract and save this `name` for the next steps.

### 2. Trigger Generation (streamAssist)
Using the `session` name obtained above, you trigger the long-running Co-Scientist task. 

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: YOUR_PROJECT_NUMBER" \
  "https://discoveryengine.googleapis.com/v1alpha/projects/YOUR_PROJECT_NUMBER/locations/global/collections/default_collection/engines/YOUR_APP_ID/assistants/default_assistant:streamAssist" \
  -d '{
    "session": "projects/YOUR_PROJECT_NUMBER/locations/global/collections/default_collection/engines/YOUR_APP_ID/sessions/YOUR_SESSION_ID",
    "query": {
      "text": "Generate experimental hypotheses to cure the common cold using non-invasive techniques."
    }
  }'
```
**Note:** `streamAssist` is a streaming endpoint. In some contexts, it may return immediately or stream chunks (e.g. `[{"answer": {"state": "IN_PROGRESS", "replies": [...]}}]`).

### 3. Check Status and Retrieve Results
To check the status of an ongoing generation or retrieve the results, you typically poll the session or the specific conversational endpoints, extracting the `groundedContent` text chunks from the `replies` array in the JSON response once the state is `COMPLETED`.

## Implementing via Python (Requests)
When implementing agents that interact with this API, use the `requests` library to orchestrate these calls synchronously or asynchronously.

```python
import os
import json
import subprocess
import requests

def get_gcloud_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True, text=True, check=True
    )
    return result.stdout.strip()

# 1. Start Session
token = get_gcloud_token()
project_number = os.environ.get("GOOGLE_CLOUD_PROJECT")
app_id = os.environ.get("GEMINI_ENT_APP_ID")
session_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_number}/locations/global/collections/default_collection/engines/{app_id}/sessions"

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "X-Goog-User-Project": project_number
}

session_resp = requests.post(session_url, headers=headers, json={"state": "IN_PROGRESS"})
session_name = session_resp.json().get("name")

# 2. Trigger streamAssist
assist_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_number}/locations/global/collections/default_collection/engines/{app_id}/assistants/default_assistant:streamAssist"
assist_payload = {
    "session": session_name,
    "query": {"text": "Your query here"}
}

response = requests.post(assist_url, headers=headers, json=assist_payload, stream=True)
# Parse chunked JSON stream to retrieve 'groundedContent'
```