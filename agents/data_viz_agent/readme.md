# Data Visualization Agent (data_viz_agent)

## 1. What This Agent Does
The Data Viz Agent is an intelligent data exploration assistant powered by the Google Agent Development Kit (ADK) and Gemini. 
It bridges the gap between raw data in BigQuery and interactive, visual insights. 

Core capabilities:
- **BigQuery Integration**: The agent securely queries your BigQuery datasets (e.g., `agentspace_demo.claim`) using native tool calling to retrieve data based on user requests.
- **Dynamic UI Generation**: When a user asks to explore data, see a timeline, or view a mindmap, the agent utilizes a custom tool (`interactive_visualization_generator`).
- **On-the-fly Deployment**: The tool dynamically generates a full-stack Python web application using **Mesop** and **PygWalker**, packages it, and deploys it live to Google Cloud Run.
- **Secure Access**: It returns a secure, single-use public URL where the user can interactively pivot, filter, and chart the queried data without writing any code.

## 2. How to Run Locally with ADK Web
You can run and test this agent locally using the ADK Web interface, which provides a rich chat environment and execution traces.

1. Ensure your dependencies are installed (`make install` from the project root) and your `uv` virtual environment is active.
2. Ensure you are authenticated with Google Cloud (`gcloud auth application-default login`).
3. Run the following command from the project root:
   ```bash
   adk web agents/data_viz_agent
   ```
4. Open the localhost link provided in the terminal (usually http://localhost:8080) to interact with the agent. Try prompting: *"Help me explore the claims data."*

## 3. How to Deploy to Vertex AI Agent Engine
Agent Engine provides a fully managed runtime for ADK agents on Google Cloud.

To deploy the agent:
```bash
adk deploy agent_engine agents/data_viz_agent \
    --project $PROJECT_ID \
    --region $REGION
```
*Note: Make sure your `.env` variables `PROJECT_ID` and `REGION` are correctly set. This deployment process will return an `AGENT_ENGINE_ID` (e.g., `projects/.../locations/.../reasoningEngines/12345`). Save this ID for the Gemini Enterprise registration step.*

## 4. How to Deploy to Google Cloud Run
If you prefer to host the agent as a standalone containerized web service (exposing a FastAPI backend), deploy to Cloud Run:

```bash
adk deploy cloud_run agents/data_viz_agent \
    --project $PROJECT_ID \
    --region $REGION
```
This will output a Cloud Run service URL that client applications can use to interact with the agent via standard HTTP requests or the ADK REST API.

## 5. How to Register in Gemini Enterprise
Once deployed to Vertex AI Agent Engine, you can register the agent as a Tool or Extension inside your Gemini Enterprise (Vertex AI Search & Conversation / Agent Builder) application.

### Option A: Using the CLI Script
If you have the `agentspace_cli.py` utility configured:
1. Open `.scripts/deploy_ent_agent.sh`.
2. Update the `AGENT_ENGINE_ID` variable with the ID you received from the Agent Engine deployment.
3. Ensure your `GEMINI_ENT_APP_ID` is defined in your `.env` file.
4. Run the script:
   ```bash
   bash .scripts/deploy_ent_agent.sh
   ```

### Option B: Manual UI Registration
1. Go to the **Google Cloud Console**.
2. Navigate to **Vertex AI Search and Conversation** (Agent Builder).
3. Open your specific Gemini Enterprise App (e.g., the app corresponding to `GEMINI_ENT_APP_ID`).
4. Go to the **Tools** or **Extensions** section.
5. Create a new Tool and select **Vertex AI Reasoning Engine** (or Agent Engine) as the backend type.
6. Provide the `AGENT_ENGINE_ID` and the region (e.g., `us-central1`).
7. Save and attach the tool to your agent flows. Your enterprise agents can now delegate visualization tasks directly to this Data Viz Agent!
