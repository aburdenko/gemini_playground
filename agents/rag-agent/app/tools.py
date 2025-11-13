# agents/rag-agent/app/tools.py

from google.adk.tools import ToolContext

def read_file(file_path: str, tool_context: ToolContext) -> dict:
    """Reads the content of a specified file.

    Args:
        file_path (str): The path to the file to read.
        tool_context (ToolContext): The tool context object.

    Returns:
        dict: A dictionary containing the file content or an error message.
    """
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        return {"status": "success", "content": content}
    except FileNotFoundError:
        return {"status": "error", "message": f"File not found: {file_path}"}
    except Exception as e:
        return {"status": "error", "message": f"Error reading file: {e}"}
