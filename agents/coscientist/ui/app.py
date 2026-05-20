import os
import time
import subprocess
import mesop as me
import mesop.labs as mel
import requests
import json

# Bypasses strict Origin vs Host header checks for Cloud Workstations
me.runtime().debug_mode = True

import google.auth
from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types

# --- Authentication and Token Helper ---
def get_gcloud_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()

# --- State Management ---
@me.stateclass
class AppState:
    project_number: str = ""
    app_id: str = ""

# Note: The underlying ADK setup handles project default configs via the environment.
_, project_id = google.auth.default() if hasattr(google.auth, "default") else (None, "kallogjeri-project-345114")
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

@me.page(
    path="/",
    title="Co-Scientist UI",
    security_policy=me.SecurityPolicy(
        dangerously_disable_trusted_types=True,
        allowed_iframe_parents=["https://*.cloudworkstations.dev", "https://*.google.com"],
        allowed_connect_srcs=["https://*.cloudworkstations.dev"]
    )
)
def app():
    state = me.state(AppState)
    
    # Pre-fill project credentials from env if missing
    if not state.project_number:
        state.project_number = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not state.app_id:
        state.app_id = os.environ.get("GEMINI_ENT_APP_ID", "")
    
    with me.box(style=me.Style(
        display="flex", 
        flex_direction="column", 
        height="100vh",
        background="#131314", # Dark theme to match Workspace
        color="#e8eaed"
    )):
        # Header
        with me.box(style=me.Style(padding=me.Padding.all(16), border=me.Border(bottom=me.BorderSide(width=1, color="#444746", style="solid")))):
            me.text("🔬 Co-Scientist Hypothesis Generator", type="headline-4", style=me.Style(color="#8ab4f8", margin=me.Margin(bottom=0)))
            me.text("Powered by Gemini Enterprise ADK", style=me.Style(color="#9aa0a6"))

        # Main Content - Chat takes full screen now
        with me.box(style=me.Style(display="flex", flex_direction="row", flex_grow=1, overflow="hidden")):
            with me.box(style=me.Style(flex_grow=1)):
                mel.chat(transform, title="Co-Scientist Agent")

def transform(message: str, history: list[mel.ChatMessage]):
    """Handles chat messages and actively streams the API response to the user."""
    state = me.state(AppState)
    
    if not state.project_number or not state.app_id:
        yield "Please ensure your GOOGLE_CLOUD_PROJECT and GEMINI_ENT_APP_ID environment variables are set."
        return

    # Yield an immediate acknowledgment
    yield "Starting Co-Scientist session..."
    time.sleep(1)
        
    try:
        token = get_gcloud_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": state.project_number
        }
        
        # Create Session
        session_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{state.project_number}/locations/global/collections/default_collection/engines/{state.app_id}/sessions"
        resp = requests.post(session_url, headers=headers, json={"state": "IN_PROGRESS"})
        resp.raise_for_status()
        
        session_name = resp.json().get("name")
        yield f"Session created: `{session_name.split('/')[-1]}`\n\nPolling API for results..."
        
        # Trigger StreamAssist 
        assist_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{state.project_number}/locations/global/collections/default_collection/engines/{state.app_id}/assistants/default_assistant:streamAssist"
        assist_payload = {
            "session": session_name,
            "query": {"text": message}
        }
        
        resp = requests.post(assist_url, headers=headers, json=assist_payload, stream=True)
        resp.raise_for_status()
        
        full_text = ""
        buffer = ""
        depth = 0
        in_string = False
        escape = False
        last_yield_time = time.time()
        current_thought = ""
        
        for chunk in resp.iter_content(chunk_size=1024, decode_unicode=True):
            if chunk:
                for char in chunk:
                    if depth == 0 and char in ['[', ']', ',', '\n', ' ']:
                        continue
                    buffer += char
                    
                    if char == '\\' and not escape:
                        escape = True
                        continue
                        
                    if not escape and char == '"':
                        in_string = not in_string
                        
                    if not in_string:
                        if char == '{':
                            depth += 1
                        elif char == '}':
                            depth -= 1
                            if depth == 0:
                                try:
                                    data = json.loads(buffer)
                                    buffer = ""
                                    if "answer" in data and "replies" in data["answer"]:
                                        for reply in data["answer"]["replies"]:
                                            if "groundedContent" in reply and "content" in reply["groundedContent"]:
                                                content_obj = reply["groundedContent"]["content"]
                                                text = content_obj.get("text", "")
                                                
                                                if content_obj.get("thought") is True:
                                                    current_thought = text.strip()
                                                    yield f"*{current_thought}*\n\n{full_text}"
                                                    continue
                                                
                                                full_text += text
                                                
                                                current_time = time.time()
                                                if current_time - last_yield_time > 0.5:
                                                    yield f"*{current_thought}*\n\n{full_text}" if current_thought else full_text
                                                    last_yield_time = current_time
                                except json.JSONDecodeError:
                                    pass
                    escape = False
        
        if full_text:
            yield full_text
        else:
            yield "API returned an empty response. It might still be processing..."
            
    except Exception as e:
        yield f"Error: {str(e)}"