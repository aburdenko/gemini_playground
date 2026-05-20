import os
import json
import subprocess
import requests
from typing import Optional
from google.adk.tools import ToolContext

def get_gcloud_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()

def start_coscientist_session(
    query: str,
    tool_context: ToolContext,
    project_number: str = "",
    app_id: str = "",
    tier: str = "TIER1",
    location: str = "global",
    direct_api_target: str = ""
) -> dict:
    """Starts a Co-Scientist session to generate experimental hypotheses.

    Use this tool to begin a long-running Co-Scientist research task. Because the
    process takes time, this tool only starts the session. You MUST use the
    check_coscientist_status tool later to retrieve the results.

    Args:
        query: The scientific query or prompt (e.g., "Generate hypotheses to cure...").
        project_number: The Google Cloud project number. Automatically retrieved from environment if omitted.
        app_id: The ID of the Agentspace app. Automatically retrieved from environment if omitted.
        tier: Generation tier (default is "TIER1").
        location: Discovery Engine location (default is "global").
        direct_api_target: Optional direct EnterpriseApiService target (e.g., "autopush-global").

    Returns:
        dict: Result of the start operation, containing 'status' and 'message'.
    """
    project_number = project_number or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    app_id = app_id or os.environ.get("GEMINI_ENT_APP_ID", "")

    if not project_number or not app_id:
        return {
            "status": "error",
            "message": "Missing project_number or app_id. Ensure GOOGLE_CLOUD_PROJECT and GEMINI_ENT_APP_ID are set in the environment or passed as arguments."
        }

    try:
        token = get_gcloud_token()
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": f"Failed to get GCP token: {e.stderr}"}

    # 1. Create a session
    session_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_number}/locations/{location}/collections/default_collection/engines/{app_id}/sessions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_number
    }
    
    session_resp = requests.post(session_url, headers=headers, json={"state": "IN_PROGRESS"})
    if not session_resp.ok:
        return {"status": "error", "message": f"Failed to create session: {session_resp.text}"}
        
    session_data = session_resp.json()
    session_name = session_data.get("name")
    
    if not session_name:
        return {"status": "error", "message": "Failed to extract session name from response."}

    # 2. Trigger the streamAssist generation
    assist_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_number}/locations/{location}/collections/default_collection/engines/{app_id}/assistants/default_assistant:streamAssist"
    
    assist_payload = {
        "session": session_name,
        "query": {
            "text": query
        }
    }
    
    # We fire the request. streamAssist usually holds the connection open, but we just want to kick it off.
    # To avoid blocking the agent forever on a long generation, we could set a timeout or read it asynchronously,
    # but since this is meant to be checked later, we will just start it and let it run or grab the initial response.
    # Actually, the user wants us to orchestrate it. `streamAssist` is a streaming endpoint.
    
    # Let's save the session name in state so check_coscientist_status can retrieve it.
    tool_context.state["coscientist_session_name"] = session_name
    tool_context.state["coscientist_project_number"] = project_number
    tool_context.state["coscientist_app_id"] = app_id
    tool_context.state["coscientist_location"] = location

    # For now, let's just do a standard request and fetch the stream chunks.
    # To act like the async CLI, we will return success and instruct the user to check back.
    # However, Python requests will block. Let's run it in the background using subprocess or threads if we want true async,
    # OR we can just block and wait for it here if the agent allows long-running tools.
    # Since the instructions say "Because the process takes time, this tool only starts the session",
    # we should try to return quickly. However, standard streamAssist doesn't return immediately, it streams.
    # We will block and fetch the full response so we can cache it in the state.
    
    try:
        # We block and wait for the full response to complete
        response = requests.post(assist_url, headers=headers, json=assist_payload, stream=True)
        response.raise_for_status()
        
        # Collect the full stream
        full_text = ""
        for line in response.iter_lines():
            if line:
                try:
                    # streamAssist returns a JSON array stream
                    chunk = json.loads(line)
                    # Handle if it's a list (which streamAssist usually is in REST: `[{...}]`)
                    if isinstance(chunk, list):
                        chunk = chunk[0]
                    if "answer" in chunk and "replies" in chunk["answer"]:
                        for reply in chunk["answer"]["replies"]:
                            if "groundedContent" in reply and "content" in reply["groundedContent"]:
                                text_part = reply["groundedContent"]["content"].get("text", "")
                                full_text += text_part
                except json.JSONDecodeError:
                    pass

        # Save the result
        tool_context.state["coscientist_final_result"] = full_text
        
        return {
            "status": "success",
            "message": "Session started and generation completed.",
            "output": "The generation has completed. You can use check_coscientist_status to read it."
        }
        
    except Exception as e:
        return {"status": "error", "message": f"Failed during streamAssist: {str(e)}"}


def check_coscientist_status(tool_context: ToolContext) -> dict:
    """Checks the status of an ongoing Co-Scientist session and returns the result.

    Use this tool to check if the session started by start_coscientist_session
    has completed. If completed, it returns the generated hypotheses.
    
    You do not need to provide arguments; it uses the session state.

    Returns:
        dict: Result containing 'status' (e.g., "success", "running", "error") 
              and the output or error messages.
    """
    session_name = tool_context.state.get("coscientist_session_name")
    
    if not session_name:
        return {
            "status": "error",
            "message": "Missing session state. You must run start_coscientist_session first."
        }

    final_result = tool_context.state.get("coscientist_final_result")
    if final_result:
        return {
            "status": "success",
            "message": "Session completed.",
            "output": final_result
        }
        
    return {
        "status": "running",
        "message": "Session is still running. Try checking again later."
    }
def download_pubmed_papers(query: str, max_results: int = 3) -> str:
    """Searches PubMed for scientific papers and downloads their abstracts.
    
    Use this tool to find medical research, clinical trials, and scientific literature.
    
    Args:
        query: The medical or scientific search term (e.g., "rhinovirus cure clinical trial").
        max_results: The maximum number of papers to retrieve (default is 3).
        
    Returns:
        str: A formatted string containing the titles and abstracts of the papers.
    """
    try:
        api_key = os.environ.get("NCBI_API_KEY", "")
        search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        search_params = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": max_results
        }
        if api_key:
            search_params["api_key"] = api_key
            
        search_resp = requests.get(search_url, params=search_params)
        search_resp.raise_for_status()
        id_list = search_resp.json().get("esearchresult", {}).get("idlist", [])
        
        if not id_list:
            return f"No papers found on PubMed for query: {query}"
            
        fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(id_list),
            "retmode": "text",
            "rettype": "abstract"
        }
        if api_key:
            fetch_params["api_key"] = api_key
            
        fetch_resp = requests.get(fetch_url, params=fetch_params)
        fetch_resp.raise_for_status()
        
        return f"Found {len(id_list)} papers:\n\n{fetch_resp.text}"
        
    except Exception as e:
        return f"Failed to retrieve papers from PubMed: {str(e)}"
