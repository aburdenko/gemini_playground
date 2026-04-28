import os
import sys
import google.auth
import vertexai
from google.adk.artifacts import GcsArtifactService
from vertexai.agent_engines.templates.adk import AdkApp

try:
    from vertexai.preview import agent_engines
except ImportError:
    try:
        from vertexai.preview import reasoning_engines as agent_engines
    except ImportError:
        import vertexai.reasoning_engines as agent_engines

project = os.environ.get("PROJECT_ID")
location = os.environ.get("LOCATION", "us-central1")
agent_name = os.environ.get("AGENT_NAME")

if not project:
    _, project = google.auth.default()

vertexai.init(project=project, location=location)

agent_dir = os.path.abspath(f"agents/{agent_name}")
sys.path.insert(0, agent_dir)

# Try importing the agent
root_agent = None
try:
    from agent import root_agent
except ImportError:
    try:
        from app.agent import root_agent
    except ImportError as e:
        print(f"Could not import root_agent from agent.py or app/agent.py: {e}")
        sys.exit(1)

if root_agent is None:
    print("root_agent is None after import.")
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

print(f"Deploying {agent_name} to Agent Engine in {location}...")
try:
    remote_app = agent_engines.create(
        agent_engine=agent_engine_app,
        requirements=requirements,
        display_name=agent_name,
        extra_packages=[agent_dir],
        staging_bucket=staging_bucket
    )
    print(f"Deployed successfully: {remote_app.resource_name}")
except Exception as e:
    print(f"Deployment failed: {e}")
    sys.exit(1)
