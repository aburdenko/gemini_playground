import sys
import logging
from google.cloud import aiplatform
from google.auth import default
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def register_to_gemini_enterprise(project_number, app_id, agent_name, agent_engine_id):
    try:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        import google.auth.transport.requests
        auth_req = google.auth.transport.requests.Request()
        credentials.refresh(auth_req)
        
        # Using global since we found the engine there
        location = "global"
        
        url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_number}/locations/{location}/collections/default_collection/engines/{app_id}/assistants/default_assistant/agents"
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": "kallogjeri-project-345114"
        }
        
        # Using the correct adkAgentDefinition payload based on what works in this environment
        payload = {
            "displayName": agent_name,
            "description": "ADK Agent deployed to Agent Engine",
            "state": "ENABLED",
            "adkAgentDefinition": {
                "toolSettings": {
                  "toolDescription": f"{agent_name} tool"
                },
                "provisionedReasoningEngine": {
                  "reasoningEngine": agent_engine_id
                }
            }
        }
        
        logging.info(f"Registering Agent '{agent_name}' to Gemini Enterprise App '{app_id}'...")
        resp = requests.post(url, headers=headers, json=payload)
        
        if resp.status_code == 409:
            logging.info(f"Agent '{agent_name}' already exists. Skipping creation.")
        else:
            resp.raise_for_status()
            logging.info(f"Agent '{agent_name}' successfully registered to Gemini Enterprise.")
            
    except Exception as e:
        logging.error(f"Failed to register agent to Gemini Enterprise: {e}")
        if hasattr(e, 'response') and e.response is not None:
             logging.error(f"Response: {e.response.text}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("Usage: python deploy_generic_agent.py <PROJECT_NUMBER> <APP_ID> <AGENT_NAME> <AGENT_ENGINE_ID>")
        sys.exit(1)
        
    project_number = sys.argv[1]
    app_id = sys.argv[2]
    agent_name = sys.argv[3]
    agent_engine_id = sys.argv[4]
    
    register_to_gemini_enterprise(project_number, app_id, agent_name, agent_engine_id)
