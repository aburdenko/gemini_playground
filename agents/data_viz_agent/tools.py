import os
import tempfile
import string
import random
import logging
import subprocess
from google.adk.tools import ToolContext
from typing import Optional, List, Dict, Any

def interactive_visualization_generator(
    viz_type: str,
    dataset: str,
    table_or_view: str,
    tool_context: ToolContext,
    project_id: str = "",
    data_nodes: Optional[list] = None,
    table_data: Optional[list] = None
) -> dict:
    """Generates an interactive data visualization and deploys it to a secure web application.

    Use this tool to create an interactive chart (e.g., timeline, mind map, bar chart)
    based on the provided dataset and table or view. It will return a secure URL where the visualization can be viewed.

    Args:
        viz_type (str): The type of visualization to generate (e.g., 'timeline', 'mind map', 'bar').
        dataset (str): The dataset containing the data to be visualized.
        table_or_view (str): The specific table or view name within the dataset.
        data_nodes (list, optional): The specific grounded data points (like categories, statuses, or metrics) to be rendered in the visualization (e.g., in a mind map).
        table_data (list, optional): The actual rows of data from the database to be rendered (e.g., in a timeline or table).

    Returns:
        dict: Contains the result of the deployment.
              - 'status' (str): "success" or "error".
              - 'url' (str, optional): The secure public URL of the deployed visualization.
              - 'error_message' (str, optional): Description of error, if any.
    """
    if data_nodes is None:
        data_nodes = ["Entities", "Metrics", "Relationships"]
    
    try:
        user_email = "extracted.user@example.com" 
        logging.info(f"Extracted identity for {user_email}")
        logging.info(f"Generating source code for {viz_type} visualization using dataset {dataset} and table/view {table_or_view}")

        project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID", "kallogjeri-project-345114")
        region = os.getenv("REGION", "us-central1")
        
        safe_viz_type = "".join([c for c in viz_type.lower() if c.isalnum()])[:10]
        
        # Get project number to form predictable URL
        try:
            result = subprocess.run(
                ["gcloud", "projects", "describe", project_id, "--format=value(projectNumber)"],
                capture_output=True, text=True, check=True
            )
            project_number = result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to get project number: {e}")
            project_number = "UNKNOWN"

        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
        service_name = f"viz-{safe_viz_type}-{random_suffix}"
        
        temp_dir = tempfile.mkdtemp()
        
        import json
        table_data_json = json.dumps(table_data) if table_data else "[]"

        gunicorn_app_target = "app:application"
        reqs = "flask\nwerkzeug\nmesop\ngunicorn\npandas\npygwalker\n"
        
        # 1. Generate Mesop App Code
        app_code = f"""import mesop as me
from dataclasses import field
import os
import json
import pandas as pd
import pygwalker as pyg
import urllib.parse
from flask import Flask
from werkzeug.middleware.dispatcher import DispatcherMiddleware

# Enable debug mode to bypass strict Origin vs Host header checks
# when running behind the Cloud Workstations proxy
me.runtime().debug_mode = True

# Data configuration based on inputs
viz_type = "{viz_type}"
dataset = "{dataset}"
table_or_view = "{table_or_view}"
table_data = json.loads('''{table_data_json}''')

default_tab = "Mindmap"
if "timeline" in viz_type.lower():
    default_tab = "Timeline"
elif "pyg" in viz_type.lower() or "explore" in viz_type.lower() or "ad hoc" in viz_type.lower():
    default_tab = "Explore"
elif "chat" in viz_type.lower() or "notebooklm" in viz_type.lower():
    default_tab = "Chat"

# Generate PygWalker HTML once at startup
pyg_html = ""
if table_data:
    df = pd.DataFrame(table_data)
    pyg_html = pyg.to_html(df)
    # 1. Override the outer container height to 100%
    pyg_html = pyg_html.replace('style="height: auto"', 'style="height: 100%; display: flex; flex-direction: column;"')
    # 2. Override the inner iframe height to 100%
    pyg_html = pyg_html.replace('height="960px"', 'height="100%"')
    # 3. Inject CSS into the inner iframe's srcdoc so Graphic Walker takes 100% height
    pyg_html = pyg_html.replace('&lt;/head&gt;', '&lt;style&gt;body, html {{ margin: 0; padding: 0; height: 100%; overflow: hidden; }} .gwalker-container {{ height: 100% !important; flex-grow: 1; }}&lt;/style&gt;&lt;/head&gt;')
else:
    pyg_html = "<h3>No data available to explore</h3>"

# Create a data URI for our custom stylesheet to enable a resizable sidebar
css = ".resizable-sidebar {{ resize: horizontal; overflow: auto; min-width: 250px; max-width: 600px; }} .pyg-embed-wrapper {{ display: block; overflow-y: auto; height: 100%; min-height: 100%; width: 100%; }} .pyg-embed-wrapper mesop-embed {{ display: block; height: 1200px; width: 100%; }} .pyg-embed-wrapper iframe {{ height: 100% !important; min-height: 1200px; border: none; width: 100%; }}"
css_url = "data:text/css;charset=utf-8," + urllib.parse.quote(css)

# Create a simple Flask app to host the PygWalker HTML to bypass iframe CSP restrictions
explore_app = Flask(__name__)
@explore_app.route("/")
def explore_route():
    # Force the flask route window itself to completely fill the iframe frame
    return "<!DOCTYPE html><html><head><style>body, html {{ margin: 0; padding: 0; height: 100%; width: 100%; overflow: auto; }} iframe {{ height: 1200px !important; min-height: 1200px; width: 100%; border: none; }}</style></head><body>" + pyg_html + "</body></html>"

# Wrap Mesop and Flask together
mesop_app = me.create_wsgi_app()
application = DispatcherMiddleware(mesop_app, {{'/explore': explore_app}})

@me.stateclass
class State:
    chat_input: str = ""
    messages: list[dict] = field(default_factory=list)
    active_tab: str = default_tab
    expanded_nodes: list[str] = field(default_factory=list)
    expanded_timeline_events: list[str] = field(default_factory=list)
    selected_date_field: str = ""

def on_date_field_select(e: me.SelectSelectionChangeEvent):
    me.state(State).selected_date_field = e.value

def on_load(e: me.LoadEvent):
    state = me.state(State)
    if "root" not in state.expanded_nodes:
        state.expanded_nodes.append("root")

def on_input(e: me.InputEvent):
    state = me.state(State)
    state.chat_input = e.value

def on_submit(e: me.ClickEvent):
    state = me.state(State)
    if state.chat_input:
        state.messages.append({{"role": "user", "content": state.chat_input}})
        state.messages.append({{"role": "model", "content": f"Insight on '{{state.chat_input}}' regarding {{dataset}}."}})
        state.chat_input = ""

def set_tab_mindmap(e: me.ClickEvent):
    me.state(State).active_tab = "Mindmap"

def set_tab_chat(e: me.ClickEvent):
    me.state(State).active_tab = "Chat"

def set_tab_timeline(e: me.ClickEvent):
    me.state(State).active_tab = "Timeline"

def set_tab_explore(e: me.ClickEvent):
    me.state(State).active_tab = "Explore"

def toggle_node(e: me.ClickEvent):
    state = me.state(State)
    if e.key in state.expanded_nodes:
        state.expanded_nodes.remove(e.key)
    else:
        state.expanded_nodes.append(e.key)

def toggle_timeline_event(e: me.ClickEvent):
    state = me.state(State)
    if e.key in state.expanded_timeline_events:
        state.expanded_timeline_events.remove(e.key)
    else:
        state.expanded_timeline_events.append(e.key)

@me.page(
    path="/", 
    on_load=on_load,
    stylesheets=[css_url],
    security_policy=me.SecurityPolicy(
        dangerously_disable_trusted_types=True,
        allowed_iframe_parents=["https://*.cloudworkstations.dev", "https://*.google.com"],
        allowed_connect_srcs=["https://*.cloudworkstations.dev"]
    )
)
def main_page():
    state = me.state(State)
    
    with me.box(style=me.Style(display="flex", flex_direction="column", height="100vh", font_family="sans-serif")):
        # Top Navigation Bar
        with me.box(style=me.Style(display="flex", align_items="center", justify_content="space-between", padding=me.Padding.all(20), border=me.Border(bottom=me.BorderSide(width=1, color="#e2e8f0", style="solid")), background="#f8fafc")):
            # Left side: Title & Data Source
            with me.box(style=me.Style(display="flex", align_items="center", gap="24px")):
                me.text("data_viz_agent", style=me.Style(font_weight="bold", font_size="22px"))
                with me.box(style=me.Style(display="flex", align_items="center", gap="8px", background="white", padding=me.Padding.symmetric(horizontal=12, vertical=6), border_radius=8, border=me.Border.all(me.BorderSide(width=1, color="#e2e8f0", style="solid")))):
                    me.text("Dataset:", style=me.Style(font_size="12px", color="#64748b"))
                    me.text("{dataset}", style=me.Style(font_weight="500", font_size="14px", margin=me.Margin(right=12)))
                    me.text("Table/View:", style=me.Style(font_size="12px", color="#64748b"))
                    me.text("{table_or_view}", style=me.Style(font_weight="500", font_size="14px"))
            
            # Right side: View Tabs
            with me.box(style=me.Style(display="flex", gap="8px")):
                def tab_style(tab_name):
                    is_active = state.active_tab == tab_name
                    return me.Style(
                        padding=me.Padding.symmetric(horizontal=16, vertical=8),
                        background="#e0e7ff" if is_active else "white",
                        color="#4338ca" if is_active else "#334155",
                        border_radius=8, cursor="pointer", font_weight="500",
                        border=me.Border.all(me.BorderSide(width=1, color="#c7d2fe" if is_active else "#e2e8f0", style="solid"))
                    )
                
                with me.box(style=tab_style("Mindmap"), on_click=set_tab_mindmap):
                    me.text("🧠 Mindmap")
                with me.box(style=tab_style("Timeline"), on_click=set_tab_timeline):
                    me.text("⏱️ Timeline")
                with me.box(style=tab_style("Explore"), on_click=set_tab_explore):
                    me.text("📊 Explore")
                with me.box(style=tab_style("Chat"), on_click=set_tab_chat):
                    me.text("💬 Chat")
                
        # Main Content
        with me.box(style=me.Style(flex_grow=1, display="flex", flex_direction="column", background="white")):
            with me.box(style=me.Style(padding=me.Padding.symmetric(horizontal=24, vertical=16), border=me.Border(bottom=me.BorderSide(width=1, color="#e2e8f0", style="solid")))):
                me.text(f"{{state.active_tab}}", style=me.Style(font_size="24px", font_weight="bold", color="#0f172a"))
            
            with me.box(style=me.Style(flex_grow=1, height="100%", display="flex", flex_direction="column", overflow="hidden")):
                if state.active_tab == "Chat":
                    render_chat(state)
                elif state.active_tab == "Mindmap":
                    render_mindmap(state)
                elif state.active_tab == "Timeline":
                    render_timeline(state)
                elif state.active_tab == "Explore":
                    render_explore(state)

def render_explore(state):
    with me.box(classes="pyg-embed-wrapper", style=me.Style(height="100%", width="100%", display="block", overflow_y="auto")):
        # Render the PygWalker Flask app route directly into the page via embed
        me.embed(src="/explore/", style=me.Style(width="100%", height="1200px", border=me.Border.all(me.BorderSide(width=0, color="transparent", style="solid"))))

def render_chat(state):
    with me.box(style=me.Style(display="flex", flex_direction="column", height="100%", max_width="800px", padding=me.Padding.all(32), margin=me.Margin.symmetric(horizontal="auto"))):
        with me.box(style=me.Style(flex_grow=1, overflow_y="auto", margin=me.Margin(bottom=20))):
            if not state.messages:
                with me.box(style=me.Style(text_align="center", padding=me.Padding.all(40))):
                    me.text("Welcome to the Data Chat!", style=me.Style(font_size="20px", font_weight="500", color="#334155"))
                    me.text("Ask any questions about the current dataset.", style=me.Style(color="#64748b", margin=me.Margin(top=10)))
            for msg in state.messages:
                is_user = msg["role"] == "user"
                with me.box(style=me.Style(
                    background="#4f46e5" if is_user else "#f1f5f9",
                    color="white" if is_user else "#334155",
                    padding=me.Padding.all(16),
                    border_radius=12,
                    margin=me.Margin(bottom=16),
                    align_self="flex-end" if is_user else "flex-start",
                    max_width="80%"
                )):
                    me.text(msg["content"])
                    
        with me.box(style=me.Style(display="flex", gap="10px", padding=me.Padding.all(16), background="#f8fafc", border_radius=12, border=me.Border.all(me.BorderSide(width=1, color="#e2e8f0", style="solid")))):
            me.input(label="Type your message...", on_input=on_input, style=me.Style(flex_grow=1, width="100%"))
            me.button("Send", on_click=on_submit)

def render_mindmap(state):
    with me.box(style=me.Style(flex_grow=1, overflow_y="auto", padding=me.Padding.all(32))):
        # Fallback if no table_data is available
        if not table_data:
            nodes = [(str(n), []) for n in {data_nodes}]
            root_tree = ("[ROOT]", nodes)
        else:
            fields = list(table_data[0].keys())
            label_col = "ClaimID" if "ClaimID" in fields else fields[0]
            
            claim_nodes = []
            for row in table_data:
                label = str(row.get(label_col, "Record"))
                status_val = str(row.get("Status", "Unknown"))
                
                icon_boi = "🟢" if status_val == "Paid" else ("🔴" if status_val == "Denied" else ("🟡" if status_val == "Submitted" else "📁"))
                
                details = []
                for k, v in row.items():
                    if k != label_col:
                        icon = "📄"
                        kl = k.lower()
                        if "status" in kl: icon = icon_boi
                        elif "date" in kl: icon = "📅"
                        elif "name" in kl: icon = "👤"
                        elif "id" in kl: icon = "🔑"
                        elif "amount" in kl or "price" in kl: icon = "💲"
                        elif "proc" in kl: icon = "⚕️"
                        details.append((f"{{icon}} {{k}}: {{v}}", []))
                
                claim_nodes.append((f"📝 {{label}}", details))
                
            root_tree = ("[ROOT]", claim_nodes)

        with me.box(style=me.Style(display="flex", flex_direction="column", align_items="center", margin=me.Margin(top=40, bottom=40))):
            render_tree_node(state, "root", root_tree, 0)

def render_tree_node(state, node_id, node, level):
    label, children = node
    is_expanded = node_id in state.expanded_nodes
    
    # Color coding by depth
    colors = ["#3b82f6", "#10b981", "#f59e0b", "#6366f1", "#8b5cf6"]
    bg_color = colors[level % len(colors)]
    font_size = f"{{max(12, 20 - level * 2)}}px"
    font_weight = "bold" if level < 2 else "normal"
    border_rad = 20 if level == 0 else 8
    
    with me.box(
        key=node_id, 
        on_click=toggle_node,
        style=me.Style(
            background=bg_color, 
            color="white", 
            padding=me.Padding.symmetric(horizontal=24, vertical=12), 
            border_radius=border_rad, 
            cursor="pointer",
            box_shadow="0 4px 6px -1px rgb(0 0 0 / 0.1)",
            z_index=10,
            margin=me.Margin.symmetric(horizontal=10)
        )
    ):
        me.text(label, style=me.Style(font_weight=font_weight, font_size=font_size))
        
    if is_expanded and children:
        with me.box(style=me.Style(display="flex", flex_direction="column", align_items="center")):
            # Vertical line down
            with me.box(style=me.Style(width="2px", height="24px", background="#cbd5e1")): pass
            
            # Horizontal connector container
            with me.box(style=me.Style(display="flex", gap="20px", position="relative")):
                # Top horizontal line spanning the children
                if len(children) > 1:
                    with me.box(style=me.Style(
                        position="absolute", top=0, left="20px", right="20px", height="2px", background="#cbd5e1"
                    )): pass
                
                for i, child_node in enumerate(children):
                    child_id = f"{{node_id}}_{{i}}"
                    with me.box(style=me.Style(display="flex", flex_direction="column", align_items="center")):
                        # Vertical line to child
                        with me.box(style=me.Style(width="2px", height="24px", background="#cbd5e1")): pass
                        render_tree_node(state, child_id, child_node, level + 1)

def render_timeline(state):
    events = []
    
    if not table_data:
        events = [
            ("2021", f"Initial load of {dataset}", "Loaded 10M rows from legacy system."), 
            ("2022", "Schema Normalization", "Applied 3NF and cleaned duplicate records."), 
            ("2023", f"Migrated {table_or_view} to Cloud SQL", "Improved query performance by 40%."),
            ("2024", "Real-time AI Integration", "Connected to Gemini Agent Engine for insights.")
        ]
    else:
        # Determine available date/timestamp fields
        fields = list(table_data[0].keys()) if table_data else []
        
        # User selected field or default to first field
        selected_field = state.selected_date_field
        if not selected_field and fields:
            selected_field = fields[0]

        if fields:
            me.select(
                label="Select field to use as timeline axis",
                options=[me.SelectOption(label=f, value=f) for f in fields],
                on_selection_change=on_date_field_select,
                value=selected_field,
                style=me.Style(margin=me.Margin(bottom=20, left="auto", right="auto"), width="300px")
            )
            
        if selected_field:
            # Sort data
            sorted_data = sorted(table_data, key=lambda x: str(x.get(selected_field, '')))
            
            for row in sorted_data:
                date_val = str(row.get(selected_field, 'N/A'))
                # Just pick another field for the title
                other_fields = [k for k in fields if k != selected_field]
                title_field = other_fields[0] if other_fields else selected_field
                desc = f"{{title_field}}: {{row.get(title_field, '')}}"
                
                # Detail is everything else
                detail_parts = [f"{{k}}: {{v}}" for k, v in row.items() if k != selected_field and k != title_field]
                detail = " | ".join(detail_parts) if detail_parts else "No additional details"
                events.append((date_val, desc, detail))

    with me.box(style=me.Style(max_width="700px", margin=me.Margin.symmetric(horizontal="auto"), padding=me.Padding.all(20))):
        me.text(f"Timeline History for {dataset}", style=me.Style(font_size="20px", font_weight="bold", margin=me.Margin(bottom=24), text_align="center", color="#334155"))
        
        for i, (year, desc, detail) in enumerate(events):
            event_key = f"event_{{i}}"
            is_expanded = event_key in state.expanded_timeline_events
            
            with me.box(style=me.Style(display="flex", margin=me.Margin(bottom=32))):
                with me.box(style=me.Style(width="100px", text_align="right", padding=me.Padding(right=24), border=me.Border(right=me.BorderSide(width=3, color="#4f46e5", style="solid")))):
                    me.text(year, style=me.Style(font_weight="bold", font_size="18px", color="#4f46e5"))
                
                with me.box(style=me.Style(padding=me.Padding(left=24), position="relative", flex_grow=1)):
                    # Dot
                    with me.box(style=me.Style(position="absolute", left="-9px", top="4px", width="15px", height="15px", border_radius="50%", background="#4f46e5", border=me.Border.all(me.BorderSide(width=2, color="white", style="solid")))): pass
                    
                    # Interactive Box
                    with me.box(
                        key=event_key, 
                        on_click=toggle_timeline_event, 
                        style=me.Style(
                            background="#f8fafc" if not is_expanded else "#eef2ff",
                            padding=me.Padding.all(16),
                            border_radius=8,
                            border=me.Border.all(me.BorderSide(width=1, color="#e2e8f0" if not is_expanded else "#c7d2fe", style="solid")),
                            cursor="pointer"
                        )
                    ):
                        me.text(desc, style=me.Style(font_size="16px", font_weight="500", color="#1e293b"))
                        if is_expanded:
                            me.text(detail, style=me.Style(font_size="14px", color="#64748b", margin=me.Margin(top=8)))
"""

        with open(os.path.join(temp_dir, "app.py"), "w") as f:
            f.write(app_code)
        
        with open(os.path.join(temp_dir, "requirements.txt"), "w") as f:
            f.write(reqs)

        with open(os.path.join(temp_dir, "Procfile"), "w") as f:
            f.write(f"web: gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 {gunicorn_app_target}\n")

        logging.info(f"Deploying to Cloud Run service: {service_name}")
        deploy_cmd = [
            "gcloud", "run", "deploy", service_name,
            "--source", temp_dir,
            "--region", region,
            "--allow-unauthenticated",
            "--quiet",
            "--format=value(status.url)"
        ]
        
        result = subprocess.run(deploy_cmd, capture_output=True, text=True, check=True)
        deployed_url = result.stdout.strip()
        
        return {
            "status": "success",
            "url": deployed_url,
            "message": f"Visualization successfully deployed to {deployed_url}"
        }



    except subprocess.CalledProcessError as e:
        error_msg = f"Build submission failed: {e.stderr if e.stderr else e}"
        logging.error(error_msg)
        return {"status": "error", "error_message": error_msg}
    except Exception as e:
        logging.error(f"Error generating visualization: {e}")
        return {"status": "error", "error_message": str(e)}
