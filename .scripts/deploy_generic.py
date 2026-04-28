import os
import sys
import google.auth
import vertexai
from google.adk.artifacts import GcsArtifactService
from vertexai.agent_engines.templates.adk import AdkApp
from vertexai._genai.types import AgentEngineConfig

project = os.environ.get("PROJECT_ID")
location = os.environ.get("LOCATION", "us-central1")
agent_name = os.environ.get("AGENT_NAME")

if not project:
    _, project = google.auth.default()

client = vertexai.Client(project=project, location=location)
vertexai.init(project=project, location=location)

agent_dir = os.path.abspath(f"agents/{agent_name}")
sys.path.insert(0, agent_dir)

try:
    from agent import root_agent
except ImportError as e:
    try:
        from app.agent import root_agent
    except ImportError:
        print(f"Could not import root_agent from agent.py or app/agent.py: {e}")
        sys.exit(1)

staging_bucket = f"gs://{project}-agent-engine"
agent_engine_app = AdkApp(
    agent=root_agent,
    artifact_service_builder=lambda: GcsArtifactService(
        bucket_name=staging_bucket
    )
)

requirements_file = f"agents/{agent_name}/.requirements.txt"
requirements = []
if os.path.exists(requirements_file):
    with open(requirements_file) as f:
        requirements = [line.strip() for line in f if line.strip()]

# Load .env variables to pass to Agent Engine
env_vars = {}
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                if k in ["GOOGLE_CLOUD_PROJECT", "PORT", "K_SERVICE", "GOOGLE_APPLICATION_CREDENTIALS", "SERVICE_ACCOUNT_KEY_FILE"]:
                    continue
                env_vars[k] = v.strip().strip('"').strip("'")

config = AgentEngineConfig(
    display_name=agent_name,
    extra_packages=[agent_dir],
    requirements=requirements,
    staging_bucket=staging_bucket,
    env_vars=env_vars,
)

print(f"Deploying {agent_name} to Agent Engine in {location}...")
try:
    remote_app = client.agent_engines.create(
        agent=agent_engine_app,
        config=config
    )
    print(f"Deployed successfully!")
except Exception as e:
    print(f"Deployment failed: {e}")
    sys.exit(1)
