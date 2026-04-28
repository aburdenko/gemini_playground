import logging
from vertexai.agent_engines.templates.adk import AdkApp
from agent import app as adk_app

class AgentEngineApp(AdkApp):
    def set_up(self) -> None:
        super().set_up()
        logging.basicConfig(level=logging.INFO)

agent_engine = AgentEngineApp(
    app=adk_app
)
