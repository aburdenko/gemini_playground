import asyncio
import os
import sys

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from travel_concierge.agent import root_agent
from google.genai import types as genai_types

async def main():
    """Runs the agent with a sample query."""
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name="travel_concierge", user_id="test_user", session_id="test_session"
    )
    runner = Runner(
        agent=root_agent, app_name="travel_concierge", session_service=session_service
    )
    
    while True:
        query = input("You: ")
        if query.lower() == "exit":
            break

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

if __name__ == "__main__":
    asyncio.run(main())
