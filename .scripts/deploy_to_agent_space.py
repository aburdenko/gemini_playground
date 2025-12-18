import argparse
import os
import subprocess
import re
import sys

# Path to the agent_registration_tool
AGENT_REGISTRATION_TOOL_PATH = os.path.join(
    os.path.dirname(__file__), "agent_registration_tool"
)
AS_REGISTRY_CLIENT_SCRIPT = os.path.join(
    AGENT_REGISTRATION_TOOL_PATH, "as_registry_client.py"
)

# Import get_access_token from as_agent_registry_service
sys.path.append(AGENT_REGISTRATION_TOOL_PATH)
import as_agent_registry_service

def get_project_number(project_id):
    """Gets the project number for a given project ID."""
    try:
        result = subprocess.run(
            ["gcloud", "projects", "describe", project_id, "--format=value(projectNumber)"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error getting project number: {e.stderr}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Deploy an agent to Agent Space.")
    parser.add_argument("--project", required=True, help="GCP Project ID")
    parser.add_argument("--location", required=True, help="GCP Location")
    parser.add_argument("--agent-name", required=True, help="The name of the agent to deploy.")
    parser.add_argument("--deployment-id", required=True, help="The deployment ID (reasoning engine ID) of the agent.")
    parser.add_argument("--agent-space-url", required=True, help="The URL of the Agent Space.")

    args = parser.parse_args()

    print(f"Deploying agent '{args.agent_name}' to Agent Space...")
    print(f"Project: {args.project}")
    print(f"Location: {args.location}")
    print(f"Deployment ID: {args.deployment_id}")
    print(f"Agent Space URL: {args.agent_space_url}")

    # project_number is not directly used in the as_registry_client.py arguments but is useful for context.
    project_number = get_project_number(args.project)
    if not project_number:
        exit(1)

    # Extract App ID from the URL
    match = re.search(r"cid/([^?]+)", args.agent_space_url)
    if not match:
        print("Could not extract App ID from Agent Space URL.")
        exit(1)
    app_id = match.group(1)

    # Construct the command to run as_registry_client.py
    # Use uv run to ensure dependencies are handled correctly
    cmd = [
        "uv",
        "run",
        AS_REGISTRY_CLIENT_SCRIPT,
        "register_agent",
        f"--project_id={args.project}",
        f"--app_id={app_id}",
        f"--ars_display_name={args.agent_name}",
        f"--description=Agent for {args.agent_name}.",
        f"--adk_deployment_id={args.deployment_id}",
        f"--location={args.location}",  # This maps to the Vertex AI Agent Engine location
    ]

    print("\nExecuting agent registration command:")
    print(" ".join(cmd))

    # Run the command using subprocess, capturing output
    # Ensure the script is executable and its dependencies are installed in the uv environment
    process = subprocess.run(cmd, capture_output=True, text=True, cwd=AGENT_REGISTRATION_TOOL_PATH)


    if process.returncode == 0:
        print(f"Successfully registered agent '{args.agent_name}' with Agent Space.")
        print("Response:", process.stdout)
    else:
        print(f"Error registering agent '{args.agent_name}' with Agent Space.")
        print("Stderr:", process.stderr)
        print("Stdout:", process.stdout)
        exit(1)


if __name__ == "__main__":
    main()
