import os
import re
import json
import importlib
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable

import vertexai
from vertexai.generative_models import GenerativeModel, Tool, Part

logger = logging.getLogger(__name__)

# Regex to find sections like # Name, # Instruction, etc.
SECTION_PATTERN = re.compile(r"^\s*#+\s*([\w -]+?)\s*$", re.MULTILINE)


def parse_agent_md(agent_name: str) -> Dict[str, Any]:
    """
    Parses the agent.md file for a given agent.
    The agent.md file is expected in the agent's directory.
    e.g., agents/{agent_name}/agent.md
    """
    # Assuming the script is run from the project root.
    # The agent directory is relative to this script's location.
    script_dir = Path(__file__).parent.absolute()
    md_path = script_dir / agent_name / "agent.md"

    if not md_path.exists():
        raise FileNotFoundError(f"agent.md not found for agent '{agent_name}' at {md_path}")

    text_content = md_path.read_text()

    sections: Dict[str, str] = {}
    last_pos = 0
    current_section_name = "initial_content"

    for match in SECTION_PATTERN.finditer(text_content):
        section_name = match.group(1).strip().lower().replace(" ", "_")
        start, end = match.span()
        section_content = text_content[last_pos:start].strip()
        sections[current_section_name] = section_content
        current_section_name = section_name
        last_pos = end

    sections[current_section_name] = text_content[last_pos:].strip()

    # Strip code fences
    for key, value in sections.items():
        stripped_value = re.sub(r"^\s*```\w*\s*", "", value, flags=re.IGNORECASE | re.MULTILINE)
        stripped_value = re.sub(r"\s*```\s*$", "", stripped_value, flags=re.MULTILINE).strip()
        sections[key] = stripped_value

    return {
        "name": sections.get("name", agent_name),
        "model": sections.get("model", "gemini-1.5-flash-latest"),
        "instruction": sections.get("instruction", "You are a helpful assistant."),
        "tools_json": sections.get("tools", "[]"),
    }


class BaseAgent:
    """
    A base class for creating agents that are configured via an agent.md file.
    """

    def __init__(self, agent_name: str, project: str, location: str, tools_registry: Optional[Dict[str, Callable]] = None):
        """
        Initializes the agent by loading its configuration.

        Args:
            agent_name: The name of the agent, corresponding to its directory.
            project: The Google Cloud project ID.
            location: The Google Cloud location/region.
            tools_registry: A dictionary mapping tool names to their factory functions.
        """
        self.agent_name = agent_name
        self.project = project
        self.location = location

        config = parse_agent_md(agent_name)
        self.name = config["name"]
        self.model_name = config["model"]
        self.instruction = config["instruction"]
        self.tools_json_str = config["tools_json"]

        self.model: Optional[GenerativeModel] = None
        self.chat: Optional[vertexai.generative_models._generative_models.ChatSession] = None
        self.tools: List[Tool] = []

    def setup(self):
        """
        Sets up the Vertex AI client, GenerativeModel, and tools.
        This should be called before querying the agent.
        """
        vertexai.init(project=self.project, location=self.location)

        # Dynamically load tools based on the agent's config
        all_tools = []
        if self.tools_json_str:
            try:
                tool_configs = json.loads(self.tools_json_str)
                for config in tool_configs:
                    tool_type = config.get("type")
                    if tool_type == "VertexAiRagRetrieval":
                        # The RAG tool is special and defined within the agent's package
                        try:
                            tool_module = importlib.import_module(f"agents.{self.agent_name}.tools")
                            rag_tool = getattr(tool_module, "rag_tool", None)
                            if rag_tool:
                                all_tools.append(rag_tool)
                                logger.info(f"Loaded RAG tool for agent '{self.agent_name}'.")
                            else:
                                logger.warning(f"RAG tool configured but not found in agents.{self.agent_name}.tools")
                        except ImportError:
                            logger.error(f"Could not import tools for agent '{self.agent_name}'.")

            except json.JSONDecodeError as e:
                logger.error(f"Error parsing tools JSON for agent '{self.agent_name}': {e}")

        self.tools = all_tools
        self.model = GenerativeModel(
            self.model_name,
            system_instruction=[self.instruction],
            tools=self.tools,
        )
        logger.info(f"Agent '{self.name}' setup complete with model '{self.model_name}'.")

    def query(self, user_prompt: str, **kwargs):
        """
        Sends a prompt to the agent and returns the response.
        Maintains a chat session.
        """
        if not self.model:
            raise RuntimeError("Agent is not set up. Please call .setup() first.")

        if not self.chat:
            self.chat = self.model.start_chat()

        logger.info(f"Sending prompt to agent '{self.name}': '{user_prompt[:100]}...'")
        response = self.chat.send_message(user_prompt, **kwargs)

        # The response can contain function calls or text.
        # For ADK compatibility, we return the raw response object.
        return response