# Usage: source .scripts/configure.sh
git config --global user.email "aburdenko@yahoo.com"
git config --global user.name "Alex Burdenko"

if ! command -v gemini &> /dev/null; then
  echo "Gemini CLI not found. Installing globally..."
  sudo npm install -g @google/gemini-cli
else
  echo "Gemini CLI is already installed."
fi


# --- Environment Configuration ---
# This script now sources its configuration from the .env file in the project root.
ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: Configuration file '$ENV_FILE' not found." >&2
    echo "Please create it by copying from '.env.example' and filling in the values." >&2
    return 1 # Use return instead of exit to allow sourcing to fail gracefully
fi

# Read variables from .env, filter out comments, and export them.
# This pipeline filters out full-line comments, then strips inline comments,
# then exports the remaining VAR=value pairs.
export $(grep -v '^#' "$ENV_FILE" | sed 's/#.*//' | xargs)

# --- Google Credentials Setup ---
# This section determines the GCP Project ID and sets up credentials.
# The order of precedence is:
# 1. Service Account specified in .env (SERVICE_ACCOUNT_KEY_FILE)
# 2. User's Application Default Credentials (ADC) via gcloud

echo "--- Configuring Google Cloud Authentication & Project ---"

# --- Step 1: Check for Service Account ---
# The path to the service account key file should be set in the .env file.
if [ -n "$SERVICE_ACCOUNT_KEY_FILE" ] && [ -f "$SERVICE_ACCOUNT_KEY_FILE" ]; then
  echo "Service Account key found at '$SERVICE_ACCOUNT_KEY_FILE'. Using it for authentication."
  export GOOGLE_APPLICATION_CREDENTIALS="$SERVICE_ACCOUNT_KEY_FILE"
  # If PROJECT_ID is not already set in .env, extract it from the SA key.
  if [ -z "$PROJECT_ID" ]; then
    PROJECT_ID=$(jq -r .project_id "$SERVICE_ACCOUNT_KEY_FILE")
    if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" == "null" ]; then
      echo "ERROR: Could not extract project_id from service account key file." >&2
      echo "Please set PROJECT_ID in your .env file." >&2
      return 1
    fi
    echo "Inferred PROJECT_ID from Service Account: $PROJECT_ID"
  fi
else
  # --- Step 2: Fallback to Application Default Credentials (ADC) ---
  echo "Service Account key not found or not specified. Falling back to gcloud Application Default Credentials."
  unset GOOGLE_APPLICATION_CREDENTIALS

  # Ensure user is logged in for ADC. This avoids re-prompting on every `source`.
  if ! gcloud auth application-default print-access-token &>/dev/null; then
    echo "User is not logged in for ADC. Running 'gcloud auth application-default login'..."
    if ! gcloud auth application-default login --no-launch-browser --scopes=openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform; then
      echo "ERROR: gcloud auth application-default login failed." >&2
      return 1
    fi
  else
    echo "User already logged in with Application Default Credentials."
  fi

  # If PROJECT_ID is not set from .env, try to get it from gcloud config.
  if [ -z "$PROJECT_ID" ]; then
    PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
    if [ -n "$PROJECT_ID" ]; then
      echo "Using configured gcloud project: $PROJECT_ID"
    else
      # If still no PROJECT_ID, prompt the user to select one.
      echo "Could not determine gcloud project. Fetching available projects..."
      mapfile -t projects < <(gcloud projects list --format="value(projectId,name)" --sort-by=projectId)

      if [ ${#projects[@]} -eq 0 ]; then
        echo "No projects found. Please enter your Google Cloud Project ID manually:"
        read -p "Project ID: " PROJECT_ID
        if [ -z "$PROJECT_ID" ]; then
          echo "ERROR: Project ID is required." >&2
          return 1
        fi
      else
        echo "Please select a project:"
        for i in "${!projects[@]}"; do
          printf "%3d) %s\n" "$((i+1))" "${projects[$i]}"
        done
        read -p "Enter number: " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#projects[@]}" ]; then
          PROJECT_ID=$(echo "${projects[$((choice-1))]}" | awk '{print $1}')
        else
          echo "ERROR: Invalid selection." >&2
          return 1
        fi
      fi
    fi
  fi
fi

# --- Step 3: Finalize Project Configuration ---
if [ -z "$PROJECT_ID" ]; then
  echo "ERROR: Project ID could not be determined. Please check your configuration." >&2
  return 1
fi

echo "Setting active gcloud project to: $PROJECT_ID"
gcloud config set project "$PROJECT_ID"

# Get project number, which is needed for some service agent roles
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

# --- Virtual Environment Setup ---
if [ ! -d ".venv/python3.12" ]; then
  echo "Python virtual environment '.python3.12' not found."
  echo "Attempting to install python3-venv..."
  sudo apt update && sudo apt install -y python3-venv
  echo "Creating Python virtual environment '.venv/python3.12'..."
  /usr/bin/python3 -m venv .venv/python3.12
  echo "Installing dependencies into .venv/python3.12 from requirements.txt..."
  
  # Grant the Vertex AI Service Agent the necessary role on your staging bucket
  gcloud storage buckets add-iam-policy-binding gs://$SOURCE_GCS_BUCKET \
    --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-aiplatform.iam.gserviceaccount.com" \
    --role="roles/storage.objectViewer"

    # Grant the Vertex AI Service Agent the necessary role on your staging bucket
  gcloud storage buckets add-iam-policy-binding gs://$STAGING_GCS_BUCKET \
    --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-aiplatform.iam.gserviceaccount.com" \
    --role="roles/storage.objectViewer"
    
  # --- Ensure 'unzip' is installed for VSIX validation ---
  if ! command -v unzip &> /dev/null; then
    echo "'unzip' command not found. Attempting to install..."
    sudo apt-get update && sudo apt-get install -y unzip
  fi

  # --- Ensure 'jq' is installed for robust JSON parsing ---
  if ! command -v jq &> /dev/null; then
    echo "'jq' command not found. Attempting to install..."
    sudo apt-get update && sudo apt-get install -y jq
  fi

  # --- VS Code Extension Setup (One-time) ---
  echo "Checking for 'emeraldwalk.runonsave' VS Code extension..."
  # Use the full path to the executable, which we know from the environment
  CODE_OSS_EXEC="/opt/code-oss/bin/codeoss-cloudworkstations"

  if ! $CODE_OSS_EXEC --list-extensions | grep -q "emeraldwalk.runonsave"; then
    echo "Extension not found. Installing 'emeraldwalk.runonsave'..."

    # Using the static URL as requested. Note: This points to an older version (0.3.2)
    # and replaces the logic that dynamically finds the latest version.
    VSIX_URL="https://www.vsixhub.com/go.php?post_id=519&app_id=65a449f8-c656-4725-a000-afd74758c7e6&s=v5O4xJdDsfDYE&link=https%3A%2F%2Fmarketplace.visualstudio.com%2F_apis%2Fpublic%2Fgallery%2Fpublishers%2Femeraldwalk%2Fvsextensions%2FRunOnSave%2F0.3.2%2Fvspackage"
    VSIX_FILE="/tmp/emeraldwalk.runonsave.vsix" # Use /tmp for the download

    echo "Downloading extension from specified static URL..."
    # Use curl with -L to follow redirects and -o to specify output file
    # Add --fail to error out on HTTP failure and -A to specify a browser User-Agent
    if curl --fail -L -A 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36' -o "$VSIX_FILE" "$VSIX_URL"; then
      echo "Download complete. Installing..."
      # Add a check to ensure the downloaded file is a valid zip archive (.vsix)
      if unzip -t "$VSIX_FILE" &> /dev/null; then
        if $CODE_OSS_EXEC --install-extension "$VSIX_FILE"; then
          echo "Extension 'emeraldwalk.runonsave' installed successfully."
          echo "IMPORTANT: Please reload the VS Code window to activate the extension."
        else
          echo "Error: Failed to install the extension from '$VSIX_FILE'." >&2
        fi
      else
        echo "Error: Downloaded file is not a valid VSIX package. It may be an HTML page." >&2
        echo "Please check the VSIX_URL in the script or your network connection." >&2
      fi
      # Clean up the downloaded file
      rm -f "$VSIX_FILE" # This will run regardless of install success/failure
    else
      echo "Error: Failed to download the extension from '$VSIX_URL'." >&2
    fi
  else
    echo "Extension 'emeraldwalk.runonsave' is already installed."
  fi
else
  echo "Virtual environment '.python3.12' already exists."
fi

echo "Activating environment './venv/python3.12'..."
 . .venv/python3.12/bin/activate

# Ensure dependencies are installed/updated every time the script is sourced.
# This prevents ModuleNotFoundError if requirements.txt changes after the
# virtual environment has been created.
echo "Ensuring dependencies from requirements.txt are installed..."
 # Use the full path to the venv pip to ensure we're installing in the correct environment.
./.venv/python3.12/bin/pip install --quiet -r requirements.txt &> /dev/null

# --- Google Agent Development Kit Check ---
# This ensures the necessary libraries for agent development (including RAG and LangChain support) are installed.
AGENT_PKG_INSTALL="google-cloud-aiplatform[rag,langchain]"
AGENT_PKG_CHECK="google-cloud-aiplatform" # pip show works on the base package name

# This POSIX-compliant check ensures the script is sourced, not executed.
# (return 0 2>/dev/null) will succeed if sourced and fail if executed.
if ! (return 0 2>/dev/null); then
  echo "-------------------------------------------------------------------"
  echo "ERROR: This script must be sourced, not executed."
  echo "Usage: source .scripts/configure.sh"
  echo "-------------------------------------------------------------------"
  exit 1
fi

# Define a function to start the ADK web server.
# This function checks for the correct authenticated user before launching.
adkweb() {
  # Check if GCP_USER_ACCOUNT is set from the .env file
  if [ -z "$GCP_USER_ACCOUNT" ]; then
    echo "Error: GCP_USER_ACCOUNT is not set in your .env file." >&2
    return 1
  fi

  # Get the currently active gcloud account
  local current_user
  current_user=$(gcloud auth list --filter=status:ACTIVE --format="value(account)")

  if [ "$current_user" != "$GCP_USER_ACCOUNT" ]; then
    echo "WARNING: You are currently authenticated as '$current_user'."
    echo "The ADK web server requires you to be '$GCP_USER_ACCOUNT'."

    # Use 'application-default login' to set the credentials that libraries like ADK use.
    echo "Updating Application Default Credentials. Please log in as '$GCP_USER_ACCOUNT' in the browser."
    gcloud auth application-default login --project="$PROJECT_ID" --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/userinfo.email,openid" || return 1
  fi

  # Display the browser identity warning BEFORE starting the blocking server process.
  echo
  echo "-------------------------------------------------------------------"
  echo "IMPORTANT BROWSER NOTE (to avoid 401 errors):"
  echo "If you see a '401: ... does not have access' error in the browser,"
  echo "it means your BROWSER is signed into the wrong Google account."
  echo
  echo "The most reliable solution is to use an Incognito/Private window:"
  echo "1. Copy the server URL (e.g., http://127.0.0.1:8001)."
  echo "2. Open a new Incognito/Private browser window."
  echo "3. Paste the URL and you will be prompted to log in with the correct"
  echo "   account: '$GCP_USER_ACCOUNT'."
  echo
  echo "(Switching accounts in the main browser window can be unreliable.)"
  echo "-------------------------------------------------------------------"
  echo

  echo "Stopping any existing ADK web server..."
  local pid=$(lsof -t -i :8001)
  if [ -n "$pid" ]; then
    echo "Attempting graceful shutdown of process $pid on port 8001..."
    kill "$pid" # Send SIGTERM to allow graceful shutdown
    sleep 2   # Wait for the process to terminate
    if ps -p "$pid" > /dev/null; then
      echo "Process $pid did not terminate gracefully, forcing shutdown..."
      kill -9 "$pid" # Force kill if it's still running
      sleep 1
    fi
  else
    echo "No process found listening on port 8001."
  fi
  echo "Starting ADK web server on port 8001..."
  # Determine the absolute path to the project root directory, which is one level
  # up from the directory containing this script.
  local script_dir=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
  local project_root=$(dirname "$script_dir")/..

  # Use an absolute path to the activate script to ensure it works regardless of
  # the current working directory.
  # Run the server in the foreground for easier debugging and control.
  # We must explicitly pass the environment variables to the new bash shell.
  # We also set PYTHONPATH to the project root so that Python can find the 'agents' module.
  PROJECT_ID="$PROJECT_ID" REGION="$REGION" GOOGLE_APPLICATION_CREDENTIALS="$GOOGLE_APPLICATION_CREDENTIALS" \
  PYTHONPATH="$project_root" \
  bash -c ". '$project_root/.venv/python3.12/bin/activate' && adk web --port 8001 '$project_root/agents'"
}

export PATH=$PATH:.scripts