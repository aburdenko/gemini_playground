from google.adk.agent_engine import AgentEngineApp
from .agent import root_agent

app = AgentEngineApp(root_agent=root_agent)
