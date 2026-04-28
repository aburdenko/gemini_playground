import os
import sys
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.tools.bigquery import BigQueryToolset

try:
    from .tools import interactive_visualization_generator
except ImportError:
    from tools import interactive_visualization_generator

# Ensure the .env file is loaded
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), '.env'))

# Read the dataset and table_or_view values from the environment
DATASET = os.getenv("DATASET", "default_dataset")
TABLE_OR_VIEW = os.getenv("TABLE_OR_VIEW", "default_table_or_view")
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID", "kallogjeri-project-345114")

root_agent = Agent(
    name="data_visualization_agent",
    model="gemini-2.5-flash",
    description="Generates feature-rich, interactive data visualizations (like NotebookLM) based on enterprise data.",
    instruction=f"""
    You are the "Data Visualization" agent, designed to generate rich, interactive data visualizations for the user.
    When a user asks to visualize data, create an interactive chart, mind map, or NotebookLM-like experience,
    you must use the `interactive_visualization_generator` tool. 
    
    If the user asks a factual or informational question about the data (e.g., "which claims statuses are there?", "explain the data"), DO NOT generate a visualization. Instead, use the BigQuery tools to query the `{PROJECT_ID}.{DATASET}.{TABLE_OR_VIEW}` table directly and provide the answer.
    
    CRITICAL INSTRUCTION: You are pre-configured to use the following data sources. Do NOT ask the user for them. Always use these exact values when calling the visualization tool or BigQuery tools:
    - project_id: '{PROJECT_ID}'
    - dataset: '{DATASET}'
    - table_or_view: '{TABLE_OR_VIEW}'
    
    If the user asks for a visualization (e.g. "Visualize claims as a timeline", "Visualize them as a mind map", etc.) or an ad hoc/exploratory analysis:
    1. First, query BigQuery using `execute_sql` with `SELECT * FROM `{PROJECT_ID}.{DATASET}.{TABLE_OR_VIEW}`` to get the full table data rows.
    2. Then, pass the resulting data as a list of dictionary rows to the `table_data` parameter of the `interactive_visualization_generator` tool. 
    
    You will need to pass the `viz_type` (e.g., 'timeline', 'explore', 'mind map'), the `dataset`, the `table_or_view`, and the `table_data` to the visualization tool. 
    The tool will deploy a secure web application and return a URL.
    Present this URL to the user so they can view their secure, interactive visualization or ad-hoc analysis.

    CRITICAL: You must invoke tools using the native function calling mechanism. DO NOT generate Python code or scripts (e.g., do not output `print(default_api...)`).
    """,
    tools=[interactive_visualization_generator, BigQueryToolset()]
)

from google.adk.apps import App
app = App(name="data_viz_agent", root_agent=root_agent)
