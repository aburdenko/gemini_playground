# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# mypy: disable-error-code="arg-type"
import os

import google
import vertexai
from google.adk.agents import Agent
from google.adk.tools.retrieval.vertex_ai_rag_retrieval import VertexAiRagRetrieval
from vertexai.preview import rag

LLM_LOCATION = "global"
LOCATION = "us-central1"
LLM = "gemini-2.5-flash"
DATA_STORE_ID = os.environ.get("DATA_STORE_ID")

credentials, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LLM_LOCATION)
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

vertexai.init(project=project_id, location=LOCATION)

# Create a Vertex AI RAG Retrieval tool
vertex_rag_tool = VertexAiRagRetrieval(
    name="my_rag_retrieval_tool",
    description="Retrieves information from the company knowledge base to answer user questions.",
    rag_resources=[
        rag.RagResource(rag_corpus=DATA_STORE_ID)
    ],
)

instruction = '''You are an AI assistant for question-answering tasks.
Answer to the best of your ability using the context provided.
Leverage the Tools you are provided to answer questions.
If you already know the answer to a question, you can respond directly without using the tools.'''

root_agent = Agent(
    name="root_agent",
    model=LLM,
    instruction=instruction,
    tools=[vertex_rag_tool],
)
