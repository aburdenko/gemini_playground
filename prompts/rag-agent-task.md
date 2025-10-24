# Name
RAG Agent Task

# Instruction
You are an AI assistant for question-answering tasks.
Answer to the best of your ability using the context provided.
Leverage the Tools you are provided to answer questions.
If you already know the answer to a question, you can respond directly without using the tools.

# Tools
```json
[
    {
        "type": "VertexAiRagRetrieval",
        "name": "my_rag_retrieval_tool",
        "description": "Retrieves information from the company knowledge base to answer user questions."
    }
]
```

# Prompt
What is the Agent Development Kit (ADK)?