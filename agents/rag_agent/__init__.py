import os
import vertexai
from vertexai.preview.generative_models import GenerativeModel
from .tools import rag_tool

# Load project ID and location from environment variables
PROJECT_ID = os.environ.get("PROJECT_ID")
LOCATION = os.environ.get("LOCATION") or os.environ.get("REGION")

if not PROJECT_ID or not LOCATION:
    raise ValueError("PROJECT_ID and LOCATION/REGION environment variables must be set.")

vertexai.init(project=PROJECT_ID, location=LOCATION)

# Initialize the generative model
root_agent = GenerativeModel(
    "gemini-2.5-pro",
    tools=[rag_tool],
)

__all__ = ["root_agent"]
