import os
import sys
from pathlib import Path

# This path correction is essential. It ensures that when this file is run from
# its symlinked location (e.g., agents/rag_agent/agent.py), it can still find
# the 'agents' package at the project root, preventing module loading conflicts.
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from agents.agent_base import BaseAgent

agent_name = Path(__file__).parent.name
PROJECT_ID = os.environ.get("PROJECT_ID")
LOCATION = os.environ.get("REGION", "us-central1")

root_agent = BaseAgent(agent_name=agent_name, project=PROJECT_ID, location=LOCATION)
root_agent.setup()