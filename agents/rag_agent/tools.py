import os
from vertexai import rag
from vertexai.generative_models import Tool

# Get RAG corpus name from environment variable
rag_corpus_name = os.environ.get("RAG_CORPUS_NAME")
if not rag_corpus_name:
    raise ValueError("RAG_CORPUS_NAME environment variable must be set.")

# Get credentials from environment and make path absolute
credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
if not credentials_path:
    raise ValueError("GOOGLE_APPLICATION_CREDENTIALS environment variable must be set.")

if not os.path.isabs(credentials_path):
    # Assuming the path is relative to the project root, which is two levels up
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    credentials_path = os.path.join(project_root, credentials_path)
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials_path

# Create a RAG retrieval tool
rag_tool = Tool.from_retrieval(
    retrieval=rag.Retrieval(
        source=rag.VertexRagStore(
            rag_resources=[
                rag.RagResource(rag_corpus=rag_corpus_name)
            ],
            rag_retrieval_config=rag.RagRetrievalConfig(top_k=3),
        )
    )
)