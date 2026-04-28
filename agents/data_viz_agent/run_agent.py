import asyncio
import os
import sys
import importlib.util
from dotenv import load_dotenv

# Force load the .env file and override any stuck environment variables in the user's terminal
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), '.env'), override=True)

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

# Dynamically import the agent module to handle the dash in the directory name
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)
module_name = os.path.basename(current_dir).replace('-', '_')

spec = importlib.util.spec_from_file_location(module_name, os.path.join(current_dir, "__init__.py"))
agent_package = importlib.util.module_from_spec(spec)
sys.modules[module_name] = agent_package
spec.loader.exec_module(agent_package)

spec_agent = importlib.util.spec_from_file_location(f"{module_name}.agent", os.path.join(current_dir, "agent.py"))
agent_module = importlib.util.module_from_spec(spec_agent)
sys.modules[f"{module_name}.agent"] = agent_module
spec_agent.loader.exec_module(agent_module)

spec_tools = importlib.util.spec_from_file_location(f"{module_name}.tools", os.path.join(current_dir, "tools.py"))
tools_module = importlib.util.module_from_spec(spec_tools)
sys.modules[f"{module_name}.tools"] = tools_module
spec_tools.loader.exec_module(tools_module)

root_agent = agent_module.root_agent

async def main():
    """Runs the data visualization agent with a sample query."""
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name="data_viz_agent", user_id="test_user", session_id="test_session"
    )
    runner = Runner(
        agent=root_agent, app_name="data_viz_agent", session_service=session_service
    )
    
    query = "Create an interactive explore visualization. Assume my auth token is 'ey-mock-token'."
    print(f"User: {query}")
    
    async for event in runner.run_async(
        user_id="test_user",
        session_id="test_session",
        new_message=genai_types.Content(
            role="user", 
            parts=[genai_types.Part.from_text(text=query)]
        ),
    ):
        if event.is_final_response():
            print(f"Agent: {event.content.parts[0].text}")
        elif event.get_function_calls():
            for call in event.get_function_calls():
                print(f"[Tool Call]: {call.name} with args: {call.args}")

if __name__ == "__main__":
    asyncio.run(main())
