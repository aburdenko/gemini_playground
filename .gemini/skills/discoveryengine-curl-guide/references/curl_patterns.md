# Discovery Engine (Agent Builder) CURL Guide

This document provides necessary patterns and specific requirements for successfully making CURL requests (specifically `PATCH` and project-level configurations) against the Google Cloud Discovery Engine API (also known as Agent Builder or Vertex AI Search).

## Core Concepts & Requirements

### 1. API Endpoints (Regionality)

Discovery Engine API endpoints are location-specific. You **must** use the endpoint that matches the location where the Agent Builder app/engine is deployed.

- **Global/US Multiregion:** For resources in the `us` multiregion, or when the generic global endpoint is failing with routing issues, use `us-discoveryengine.googleapis.com`.
  - *Example:* `https://us-discoveryengine.googleapis.com/v1alpha/projects/...`
- **Standard Global:** If not explicitly regional, try `discoveryengine.googleapis.com`.
  - *Note:* Do NOT use `global-discoveryengine.googleapis.com` as it often causes routing or resolution failures.

### 2. Project ID vs. Project Number

This is the most critical source of `403 Permission Denied` or `USER_PROJECT_DENIED` errors when configuring project-level settings (like `customerProvidedConfig`):

- **URL Path:** The URL path **MUST** use the numeric **Project Number**.
- **Header:** The `X-Goog-User-Project` header **MUST** use the string **Project ID**.

### 3. Bash Syntax for CURL

When writing multi-line `curl` commands in bash scripts or command-line execution, ensure line continuations (`\`) are correctly formatted.
- A backslash `\` must be the absolute last character on the line.
- Avoid trailing spaces after the backslash (e.g., `\ -H` is invalid).

---

## Example: Patching Project Configuration

This example demonstrates how to enable Observability (Audit Logs) and Sensitive Logging (which captures full prompts/responses) for NotebookLM Enterprise / Discovery Engine apps.

### The Problem it Solves
By default, sensitive logging is disabled. To debug hallucinating agents or trace full payloads, you need to set `observabilityEnabled` and `sensitiveLoggingEnabled` to `true` on the project's `customerProvidedConfig`.

### The Corrected CURL Command

```bash
# Ensure you replace these variables with your actual values:
# PROJECT_NUMBER="123456789012"
# PROJECT_ID="your-project-id"
# LOCATION_PREFIX="us-" # or "" for global

curl -X PATCH \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${LOCATION_PREFIX}discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_NUMBER}?updateMask=customerProvidedConfig.notebooklmConfig.observabilityConfig" \
  -d '{
    "customerProvidedConfig": {
      "notebooklmConfig": {
        "observabilityConfig": {
          "observabilityEnabled": true,
          "sensitiveLoggingEnabled": true
        }
      }
    }
  }'
```

## Troubleshooting 403 Errors

If you receive a `403 Caller does not have required permission to use project [ID]` error containing `"reason": "USER_PROJECT_DENIED"`:

1. **Verify Project Number:** Ensure the URL uses the numeric Project Number, not the string Project ID.
2. **Verify Header:** Ensure `X-Goog-User-Project` contains the string Project ID.
3. **Verify Auth:** Ensure your `gcloud auth print-access-token` is generating a token for an account with `roles/discoveryengine.admin` or `roles/discoveryengine.agentspaceAdmin`. You can check the active account using `gcloud config get-value account`.