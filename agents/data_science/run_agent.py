
import asyncio
import sys
print("DEBUG: sys.path:", sys.path)

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

# It's important to import the root_agent from the correct module
from agents.data_science.agent import root_agent



async def main():
    """Runs the data_science agent with a sample query."""
    session_service = InMemorySessionService()
    session_id = "test_session_data_science"
    await session_service.create_session(
        app_name="data_science", user_id="test_user", session_id=session_id
    )
    runner = Runner(
        agent=root_agent, app_name="data_science", session_service=session_service
    )
    query = "What is the average ticket price per month, and can you create a forecast for the next 3 months?"
    print(f"--- Running data_science agent with query: '{query}' ---")

    async for event in runner.run_async(
        user_id="test_user",
        session_id=session_id,
        new_message=Content(role="user", parts=[Part.from_text(query)]),
    ):
        if event.is_final_response():
            print("\n--- Final Answer ---")
            print(event.content.parts[0].text)


if __name__ == "__main__":
    asyncio.run(main())
