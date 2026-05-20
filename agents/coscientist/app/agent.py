import os
import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .tools import start_coscientist_session, check_coscientist_status, download_pubmed_papers

# Note: The underlying ADK setup handles project default configs.
_, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

INSTRUCTION = """
You are the Co-Scientist orchestration agent. Your goal is to orchestrate, 
monitor, and test Co-Scientist Enterprise sessions via the v1alpha API (Discovery Engine).

You have access to three tools:
1. `start_coscientist_session`: Starts a generation session for a scientific query.
2. `check_coscientist_status`: Checks the status of the ongoing session and returns the generated hypotheses.
3. `download_pubmed_papers`: Searches and downloads abstracts natively without triggering a Co-Scientist session.

When asked to generate hypotheses via Co-Scientist:
- You must use `start_coscientist_session`.
- The tool will automatically use the `GOOGLE_CLOUD_PROJECT` and `GEMINI_ENT_APP_ID` from the environment variables, so you do not need to ask the user for them.
- Let the user know the session has started and they should ask you to check the status periodically.

When asked to check the status or get the results:
- Use the `check_coscientist_status` tool.
- If it is still running, inform the user they need to wait longer.
- If it has completed, present the final hypotheses to the user clearly.
"""

root_agent = Agent(
    name="coscientist_agent",
    model=Gemini(
        model="gemini-3-flash-preview",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=INSTRUCTION,
    tools=[start_coscientist_session, check_coscientist_status, download_pubmed_papers],
)

app = App(
    root_agent=root_agent,
    name="app",
)
