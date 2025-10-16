# --- Agent Entrypoint for ADK ---
# This file makes the RAG agent discoverable by the ADK web server.
# It defines the `root_agent` variable that the ADK framework looks for.

import os
from dotenv import load_dotenv
from vertexai.preview import rag
from vertexai.generative_models import GenerativeModel, Tool
from google.adk.integrations.vertex_ai import GenerativeModelAgent

# Load environment variables from the .env file in the same directory.
# This is crucial for the ADK to pick up the agent's specific configuration.
load_dotenv()

# --- Agent Configuration ---
# Retrieve necessary configuration from environment variables.
MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash-latest")
RAG_CORPUS = os.getenv("RAG_CORPUS")

if not RAG_CORPUS:
    raise ValueError("RAG_CORPUS environment variable not set. Please check your agents/RAG/.env file.")

# --- Tool Definition ---
# Configure the RAG tool using the corpus ID from the environment.
rag_retrieval = rag.Retrieval(
    source=rag.VertexRagStore(rag_resources=[rag.RagResource(rag_corpus=RAG_CORPUS)])
)
rag_tool = Tool.from_retrieval(rag_retrieval)

# --- Root Agent Definition ---
# This is the variable the ADK framework will discover and use.
# We instantiate the GenerativeModel and provide the RAG tool to it.
model = GenerativeModel(MODEL_NAME, tools=[rag_tool])
root_agent = GenerativeModelAgent(model=model)