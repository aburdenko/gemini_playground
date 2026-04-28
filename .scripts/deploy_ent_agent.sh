#!/bin/bash

# Exit script immediately if any command fails
set -e

# ==========================================
# Configuration Variables
# ==========================================

# The ID of your deployed Agent Engine instance
AGENT_ENGINE_ID="your-agent-engine-id"

# Display details for Gemini Enterprise
AGENT_DISPLAY_NAME="My Custom ADK Agent"
AGENT_DESCRIPTION="This agent was deployed via ADK and published to Gemini Enterprise."

# Optional: The Auth ID if your agent uses OAuth to access Google services (e.g., Gmail, Calendar)
# AUTH_ID="your_auth_id"

# ==========================================
# Publish to Gemini Enterprise (Agentspace)
# ==========================================
echo "Registering Agent Engine instance ($AGENT_ENGINE_ID) with Gemini Enterprise..."

# NOTE: This command assumes you have cloned the internal agentspace-cli repository 
# and are using 'uv' to run the CLI script locally.
uv run agentspace_cli.py agent register \
    --project-number="${PROJECT_NUMBER}" \
    --app-id="${GEMINI_ENT_APP_ID}" \
    --display-name="${AGENT_DISPLAY_NAME}" \
    --description="${AGENT_DESCRIPTION}" \
    --reasoning-engine-location="${REGION}" \
    --reasoning-engine-id="${AGENT_ENGINE_ID}"
    # Uncomment and use the line below if your agent requires OAuth 
    # --auth-id="${AUTH_ID}"

echo "Agent successfully published to Gemini Enterprise!"
