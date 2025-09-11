#!/usr/bin/env python3
# Run with python3 ./.scripts/run_gemini_from_file.py suggested-prompt-2025-06-29.md

import os
import sys
import argparse
import uuid
import re
import json
import logging, hashlib
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Dict, Any, Tuple, Optional, List

# --- SDK Imports ---
# This script now uses the Vertex AI SDK for generation to support integrated RAG.
import vertexai
import pandas as pd
# The RAG features are in the 'preview' namespace.
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel, Tool
from vertexai.generative_models import Part, GenerationConfig, HarmCategory, HarmBlockThreshold, SafetySetting

# --- Define the project root as the parent directory of the .scripts folder ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- Add necessary imports ---
from prompt_manager import PromptManager

# They are kept here for reference but are no longer used in the primary RAG path.
from google.cloud import aiplatform
from google.cloud import logging as cloud_logging
from google.cloud.logging.handlers import setup_logging
from google.cloud import storage
from vertexai.language_models import TextEmbeddingModel
# We need protos for schema definition and function calling.
import google.ai.generativelanguage as glm
# --- End Add necessary imports ---
# --- Import shared evaluation utilities ---
from eval_utils import run_on_demand_evaluation

# --- Constants ---
# --- Project Configuration (from environment) ---
PROJECT_ID = os.environ.get("PROJECT_ID", "your-gcp-project-id")
LOCATION = os.environ.get("REGION", "us-central1")
BUCKET_NAME = os.environ.get("STAGING_GCS_BUCKET", "your-bucket-name")
SHORT_LOG_NAME = os.environ.get("LOG_NAME", "run_gemini_from_file")
EXPERIMENT_NAME = "gemini-playground-evaluation" # Used for GCS artifact path
# Default model is now primarily set in .scripts/configure.sh as GEMINI_MODEL_NAME
# A fallback is provided in the code where it's used.
# Regex to find sections like # System Instructions, # Prompt, etc. Handles multiple hashes,
# optional space after hashes, and hyphens in the name. It correctly limits
# the heading to a single line.
SECTION_PATTERN = re.compile(r"^\s*#+\s*([\w -]+)\s*$", re.MULTILINE)
RAG_ENGINE_SECTION_KEY = "ragengine"
SUPPORTED_ON_DEMAND_METRICS = ["fluency", "coherence", "safety", "rouge"]
# --- Define the key we expect for the schema section ---
CONTROLLED_OUTPUT_SECTION_KEY = "controlled_output_schema" # Use this constant

# --- Model Pricing (per 1,000 tokens) ---
# Prices as of mid-2024 from https://cloud.google.com/vertex-ai/generative-ai/pricing
MODEL_PRICING = {
    # Gemini 1.5 Models
    "gemini-1.5-flash": {"input": 0.000125, "output": 0.000375},
    "gemini-1.5-flash-latest": {"input": 0.000125, "output": 0.000375},
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.00375},
    "gemini-1.5-pro-latest": {"input": 0.00125, "output": 0.00375},
    # NOTE: Placeholder pricing for 2.5 models based on 1.5 series. Update when official pricing is available.
    "gemini-2.5-pro": {"input": 0.00125, "output": 0.00375},
    "gemini-2.5-flash": {"input": 0.000125, "output": 0.000375},
    # Add other models here as they are used.
}
# --- End Model Pricing ---

# --- Safety Settings ---
# Define mapping from string names (used in metadata) to HarmCategory enums
HARM_CATEGORY_MAP: Dict[str, "HarmCategory"] = {
    "harassment": HarmCategory.HARM_CATEGORY_HARASSMENT,
    "hate_speech": HarmCategory.HARM_CATEGORY_HATE_SPEECH,
    "sexually_explicit": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
    "dangerous_content": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
}
# Define mapping from string names (used in metadata) to HarmBlockThreshold enums
HARM_THRESHOLD_MAP: Dict[str, "HarmBlockThreshold"] = {
    "block_none": HarmBlockThreshold.BLOCK_NONE,
    "block_low_and_above": HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
    "block_medium_and_above": HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    "block_only_high": HarmBlockThreshold.BLOCK_ONLY_HIGH,
    # Add alias for clarity/convenience
    "none": HarmBlockThreshold.BLOCK_NONE,
    "low": HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
    "medium": HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    "high": HarmBlockThreshold.BLOCK_ONLY_HIGH,
}
# Default safety settings if not specified in the file
DEFAULT_SAFETY_SETTINGS: List[SafetySetting] = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE)
]
# --- End Safety Settings ---


# --- Logging Setup ---
# Use logging instead of print for warnings/errors for better control
# Increase level to DEBUG to see more detailed logs if needed
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s', stream=sys.stderr)
# Example: To enable debug logs, change level=logging.INFO to level=logging.DEBUG
# logging.basicConfig(level=logging.DEBUG, format='%(levelname)s:%(name)s:%(funcName)s:%(lineno)d: %(message)s', stream=sys.stderr)
logger = logging.getLogger(__name__)
# --- End Logging Setup ---

# --- Cloud Logging Setup ---
def setup_cloud_logging(project_id: str) -> Optional[Tuple[cloud_logging.Client, cloud_logging.handlers.CloudLoggingHandler]]:
    """
    Sets up a handler to send logs to Google Cloud Logging.
    This is not a fatal error; the script will continue with console logging if it fails.
    Returns the client and handler instances on success, (None, None) on failure.
    """
    if not project_id:
        logger.warning("    Project ID not provided. Skipping Google Cloud Logging setup.")
        return None, None
    try:
        # Get log name from environment variable, with a fallback.
        log_name = os.getenv("LOG_NAME", "run_gemini_from_file") # A sensible default

        logging_client = cloud_logging.Client(project=project_id)

        # Instead of client.setup_logging(), which hardcodes the log name to 'python',
        # we create the handler manually to use the name from the LOG_NAME env var.
        handler = cloud_logging.handlers.CloudLoggingHandler(logging_client, name=log_name)

        # The setup_logging helper attaches the handler to the root logger.
        setup_logging(handler)

        logger.info(f"    Successfully set up Google Cloud Logging handler for log name: '{log_name}'.")
        return logging_client, handler
    except Exception as e:
        logger.warning(f"    Could not set up Google Cloud Logging: {e}. Logs will only be sent to the console.")
        return None, None

def log_to_cloud(log_name: str, payload: Dict[str, Any]):
    """Logs a structured payload to Google Cloud Logging."""
    logger.info(log_name, extra={'json_fields': payload})
# --- End Cloud Logging Setup ---


# --- Schema Conversion Function ---
def dict_to_proto_schema(schema_dict: dict) -> Optional[glm.Schema]:
    """
    Converts a Python dictionary representing a JSON schema to a glm.Schema.
    NOTE: This version has limitations and does not support $ref or complex validations.
    """
    try:
        # Type mapping dictionary for clarity
        TYPE_MAP = {
            "string": glm.Type.STRING,
            "number": glm.Type.NUMBER,
            "integer": glm.Type.INTEGER,
            "boolean": glm.Type.BOOLEAN,
            "array": glm.Type.ARRAY,
            "object": glm.Type.OBJECT,
        }

        def convert_property(prop_name, prop_details):
            if not isinstance(prop_details, dict):
                 logger.warning(f"    Expected dict for property details '{prop_name}', got {type(prop_details)}. Skipping.")
                 return None

            # Handle $ref - Basic handling: Treat as string if encountered unexpectedly
            # A proper implementation would require resolving the reference.
            if "$ref" in prop_details:
                logger.warning(f"    Schema property '{prop_name}' uses '$ref' ('{prop_details['$ref']}'). This converter has limited $ref support and will treat it as a basic type (likely STRING). The resulting schema might be incomplete.")
                # Attempt to get type from sibling keys, else default to STRING
                prop_type_str = prop_details.get("type", "string").lower()
            else:
                prop_type_str = prop_details.get("type", "string").lower()


            prop = glm.Schema(
                description=prop_details.get("description", ""),
                # title is not directly mapped in glm.Schema for properties
            )
            # Set nullable attribute only if it exists, for backward compatibility with older SDKs
            if hasattr(prop, 'nullable'):
                prop.nullable = prop_details.get("nullable", False)

            prop.type = TYPE_MAP.get(prop_type_str, glm.Type.STRING) # Default to STRING

            if prop.type == glm.Type.STRING:
                enum = prop_details.get("enum")
                if enum and isinstance(enum, list): # Ensure enum is a list
                    prop.enum.extend([str(e) for e in enum]) # Ensure values are strings
                # pattern is not directly mapped in glm.Schema

            elif prop.type == glm.Type.ARRAY:
                items = prop_details.get("items")
                if items:
                    # Handle case where items is a schema object itself
                    if isinstance(items, dict):
                        item_schema = convert_property("items", items) # Recursive call
                        if item_schema:
                            prop.items = item_schema
                        else:
                            # If recursive call failed, default to STRING array
                            logger.warning(f"    Could not determine schema for array items in '{prop_name}' (recursive conversion failed). Defaulting to STRING array.")
                            prop.items = glm.Schema(type=glm.Type.STRING)
                    else:
                        logger.warning(f"    Array property '{prop_name}' has 'items' field but it's not a dictionary. Defaulting to STRING array.")
                        prop.items = glm.Schema(type=glm.Type.STRING)
                else:
                     logger.warning(f"    Array property '{prop_name}' missing 'items' definition. Defaulting to STRING array.")
                     prop.items = glm.Schema(type=glm.Type.STRING) # Default items to STRING if not specified


            elif prop.type == glm.Type.OBJECT:
                properties = prop_details.get("properties")
                if properties and isinstance(properties, dict):
                    for name, details in properties.items():
                        converted_sub_prop = convert_property(name, details) # Recursive call
                        if converted_sub_prop:
                             prop.properties[name] = converted_sub_prop
                required = prop_details.get("required")
                if required and isinstance(required, list):
                    prop.required.extend(required)
                # propertyOrdering is ignored

            # Ignore other keys like title, $defs (at property level)

            return prop

        # Start conversion from the root of the schema dictionary
        # Need to handle $defs at the root if present
        if "$defs" in schema_dict:
             logger.warning("    Root schema contains '$defs'. This converter does not resolve references within '$defs'. Schema might be incomplete.")
             # We proceed, but $ref resolution won't happen.

        root_schema = convert_property("root", schema_dict)
        return root_schema

    except Exception as e:
        logger.error(f"    Error converting dictionary to proto schema: {e}", exc_info=True) # Log traceback
        return None
# --- End Schema Conversion Function ---

# --- Function Declaration Conversion ---
def dict_list_to_proto_tool(func_list_dict: List[Dict[str, Any]]) -> Optional[glm.Tool]:
    """Converts a list of function definition dictionaries to a glm.Tool."""
    if not isinstance(func_list_dict, list):
        logger.error(f"    Expected a list for function definitions, got {type(func_list_dict)}.")
        return None

    function_declarations = []
    for i, func_dict in enumerate(func_list_dict):
        if not isinstance(func_dict, dict):
            logger.warning(f"    Skipping item at index {i} in function list: Expected a dictionary, got {type(func_dict)}.")
            continue

        name = func_dict.get("name")
        description = func_dict.get("description")
        parameters_dict = func_dict.get("parameters")

        if not name:
            logger.warning(f"    Skipping function definition at index {i}: Missing 'name'.")
            continue

        # Parameters are optional, but if provided, they must be a valid schema dict
        parameters_schema = None
        if parameters_dict:
            if isinstance(parameters_dict, dict):
                # We need to ensure the parameters dict itself follows JSON schema structure,
                # especially the top level 'type' which should usually be 'object'.
                if 'type' not in parameters_dict:
                     logger.warning(f"    Function '{name}' parameters missing 'type'. Assuming 'object'.")
                     parameters_dict['type'] = 'object' # Assume object if not specified
                if parameters_dict.get('type') != 'object':
                     logger.warning(f"    Function '{name}' parameters 'type' is '{parameters_dict.get('type')}', expected 'object'. Conversion might be unexpected.")

                parameters_schema = dict_to_proto_schema(parameters_dict)
                if not parameters_schema:
                    logger.warning(f"    Could not convert parameters for function '{name}' to proto schema. Skipping parameters.")
            else:
                logger.warning(f"    Function '{name}' parameters field is not a dictionary. Skipping parameters.")

        # Create the FunctionDeclaration proto
        func_decl = glm.FunctionDeclaration(
            name=name,
            description=description or "", # Description is optional
        )
        # Only add parameters if they were successfully converted
        if parameters_schema:
            func_decl.parameters = parameters_schema

        function_declarations.append(func_decl)
        logger.info(f"    Parsed function declaration: '{name}'")


    if not function_declarations:
        logger.warning("    No valid function declarations found in the provided list.")
        return None

    # Wrap the declarations in a Tool
    tool = glm.Tool(function_declarations=function_declarations)
    return tool

# --- End Function Declaration Conversion ---


# --- Enhanced Metadata Parsing ---
def parse_metadata_and_body(file_content: str) -> Tuple[Dict[str, Any], str]:
    """
    Parses specific metadata keys found anywhere in the file content
    and returns them as a dictionary. Handles type conversions.
    Returns the full original content as the body for further section parsing.
    """
    metadata_dict: Dict[str, Any] = {}
    body_content = file_content.strip() # Keep original content for section parsing

    # --- Metadata Extraction Functions ---
    def find_value(key_pattern: str, content: str) -> Optional[str]:
        match = re.search(rf"^\s*{key_pattern}:\s*(.+)$", content, re.IGNORECASE | re.MULTILINE)
        return match.group(1).strip() if match else None

    def find_float(key_pattern: str, content: str) -> Optional[float]:
        value_str = find_value(key_pattern, content)
        if value_str:
            try:
                return float(value_str)
            except ValueError:
                logger.warning(f"    Could not parse float value for '{key_pattern}': {value_str}")
        return None

    def find_int(key_pattern: str, content: str) -> Optional[int]:
        value_str = find_value(key_pattern, content)
        if value_str:
            try:
                # Allow float strings like "100.0" to be parsed as int
                return int(float(value_str))
            except ValueError:
                logger.warning(f"    Could not parse integer value for '{key_pattern}': {value_str}")
        return None

    def find_string_list(key_pattern: str, content: str) -> Optional[List[str]]:
        value_str = find_value(key_pattern, content)
        if value_str:
            # Assume comma-separated, trim whitespace
            return [item.strip() for item in value_str.split(',') if item.strip()]
        return None

    def find_safety_settings(content: str) -> Optional[List[SafetySetting]]:
        """Parses safety settings like 'Safety: harassment=none, hate_speech=low'"""
        value_str = find_value("Safety(?: Settings)?", content)
        if not value_str:
            return None

        settings: List[SafetySetting] = []
        pairs = [item.strip() for item in value_str.split(',') if item.strip()]
        for pair in pairs:
            try:
                category_str, threshold_str = [p.strip().lower().replace('-', '_') for p in pair.split('=')]
                category = HARM_CATEGORY_MAP.get(category_str)
                threshold = HARM_THRESHOLD_MAP.get(threshold_str)

                if category and threshold:
                    settings.append(SafetySetting(category=category, threshold=threshold))
                else:
                    if not category:
                        logger.warning(f"    Invalid safety category '{category_str}' found in metadata. Skipping.")
                    if not threshold:
                        logger.warning(f"    Invalid safety threshold '{threshold_str}' found in metadata. Skipping.")
            except ValueError:
                logger.warning(f"    Could not parse safety setting pair: '{pair}'. Expected format 'category=threshold'. Skipping.")
            except Exception as e:
                 logger.warning(f"    Error parsing safety setting pair '{pair}': {e}. Skipping.")

        return settings if settings else None
    # --- End Metadata Extraction Functions ---


    # --- Parse Known Metadata ---
    metadata_dict['model_name'] = find_value(r"Model(?: Used)?(?: \(intended\))?", file_content)
    metadata_dict['temperature'] = find_float(r"Temperature", file_content)
    metadata_dict['top_p'] = find_float(r"Top P", file_content)
    metadata_dict['top_k'] = find_int(r"Top K", file_content)
    metadata_dict['seed'] = find_int(r"Seed", file_content)
    metadata_dict['max_output_tokens'] = find_int(r"Max(?: Output)? Tokens", file_content)
    metadata_dict['stop_sequences'] = find_string_list(r"Stop Sequences", file_content)
    metadata_dict['safety_settings'] = find_safety_settings(file_content)
    metadata_dict['logprobs'] = find_int(r"Log Probs", file_content)
    # --- End Parse Known Metadata ---

    # Filter out None values
    metadata_dict = {k: v for k, v in metadata_dict.items() if v is not None}

    return metadata_dict, body_content


# --- Modified parse_sections Function ---
def parse_sections(text_content: str) -> Tuple[Dict[str, str], Optional[glm.Schema], Optional[Dict[str, Any]], bool, Optional[glm.Tool], bool, Optional[str], Optional[List[str]]]:
    """
    Parses the text content into sections based on headings.
    Checks for '# Controlled Output Schema', '# Functions', and '# RagEngine'.
    Returns:
        - sections: Dictionary of section names to content.
        - proto_schema: Parsed proto schema from '# Controlled Output Schema' (or None if parsing fails).
        - schema_dict: The raw parsed dictionary of the schema (or None).
        - controlled_output_section_found: Flag indicating if '# Controlled Output Schema' was found.
        - proto_tool: Parsed proto tool from '# Functions' (or None).
        - functions_section_found: Flag indicating if '# Functions' was found.
        - rag_engine_endpoint: The display name of the Vector Search endpoint from '# RagEngine' section.
        - eval_metrics_list: A list of metrics from the '# Eval Metrics' section.
    """
    sections: Dict[str, str] = {}
    last_pos = 0
    current_section_name = "initial_content" # Content before first heading

    for match in SECTION_PATTERN.finditer(text_content):
        # Convert heading to key: lowercase, replace spaces with underscores
        section_name = match.group(1).strip().lower().replace(" ", "_")
        start, end = match.span()
        section_content = text_content[last_pos:start].strip()
        # Always add the section for the previous heading. This ensures that
        # even empty sections are recorded in the dictionary.
        sections[current_section_name] = section_content

        current_section_name = section_name
        last_pos = end

    section_content = text_content[last_pos:].strip()
    sections[current_section_name] = section_content

    # After parsing, strip code fences from all section values for robustness.
    for key, value in sections.items():
        # Use \w* to match any language identifier (json, plaintext, etc.) or none.
        stripped_value = re.sub(r"^\s*```\w*\s*", "", value, flags=re.IGNORECASE | re.MULTILINE)
        stripped_value = re.sub(r"\s*```\s*$", "", stripped_value, flags=re.MULTILINE).strip()
        sections[key] = stripped_value

    # --- Schema Extraction ---
    schema_dict = None
    proto_schema = None # Initialize to None
    controlled_output_section_found = CONTROLLED_OUTPUT_SECTION_KEY in sections

    if controlled_output_section_found:
        logger.info(f"    Found '# {CONTROLLED_OUTPUT_SECTION_KEY.replace('_', ' ').title()}' section.")
        schema_json_str = sections[CONTROLLED_OUTPUT_SECTION_KEY]
        # Remove potential ```json ``` wrappers
        schema_json_str = re.sub(r"^\s*```(?:json)?\s*", "", schema_json_str, flags=re.IGNORECASE | re.MULTILINE)
        schema_json_str = re.sub(r"\s*```\s*$", "", schema_json_str, flags=re.MULTILINE)
        schema_json_str = schema_json_str.strip()

        if schema_json_str:
            try:
                schema_dict = json.loads(schema_json_str)
                logger.info("    JSON schema content parsed successfully.")
                # Attempt conversion
                proto_schema = dict_to_proto_schema(schema_dict) # Assign result here
                if proto_schema:
                    logger.info("    Successfully converted schema to proto format (basic validation).")
                else:
                     # This path is taken if dict_to_proto_schema explicitly returns None (e.g., due to exception)
                     logger.warning("    Conversion to proto schema failed (returned None). Check previous errors.")
            except json.JSONDecodeError as e:
                logger.warning(f"    Failed to parse JSON content in '# {CONTROLLED_OUTPUT_SECTION_KEY.replace('_', ' ').title()}' section: {e}")
                # proto_schema remains None
            except Exception as e:
                logger.warning(f"    Error processing schema from '# {CONTROLLED_OUTPUT_SECTION_KEY.replace('_', ' ').title()}' section: {e}", exc_info=True)
                # proto_schema remains None
        else:
             logger.warning(f"    '# {CONTROLLED_OUTPUT_SECTION_KEY.replace('_', ' ').title()}' section is empty.")
             # proto_schema remains None

    # --- Function Declaration Extraction ---
    proto_tool = None
    functions_section_found = 'functions' in sections # This key is derived from "# Functions"

    if functions_section_found:
        logger.info("    Found '# Functions' section.")
        functions_json_str = sections['functions']
        # Remove potential ```json ``` wrappers
        functions_json_str = re.sub(r"^\s*```\w*\s*", "", functions_json_str, flags=re.IGNORECASE | re.MULTILINE)
        functions_json_str = re.sub(r"\s*```\s*$", "", functions_json_str, flags=re.MULTILINE)
        functions_json_str = functions_json_str.strip()

        if functions_json_str:
            try:
                functions_list = json.loads(functions_json_str)
                logger.info("    Function definitions JSON parsed successfully.")
                proto_tool = dict_list_to_proto_tool(functions_list)
                if proto_tool:
                    logger.info("    Successfully converted function definitions to proto Tool format.")
                else:
                    logger.warning("    Failed to convert parsed JSON list to proto Tool (see previous errors).")
            except json.JSONDecodeError as e:
                logger.warning(f"    Failed to parse JSON content in '# Functions' section: {e}")
            except Exception as e:
                logger.warning(f"    Error processing functions from '# Functions' section: {e}", exc_info=True)
        else:
            logger.warning("    '# Functions' section is empty.")

    # --- RAG Engine Extraction ---
    rag_engine_endpoint = sections.get(RAG_ENGINE_SECTION_KEY)
    if rag_engine_endpoint:
        rag_engine_endpoint = rag_engine_endpoint.strip()
        logger.info(f"    Found '# RagEngine' section. RAG resource specified: '{rag_engine_endpoint}'")

    # --- Eval Metrics Extraction ---
    eval_metrics_list = None
    eval_metrics_content = None

    if "eval_metrics" in sections:
        eval_metrics_content = sections["eval_metrics"]
    elif "evall_metrics" in sections:
        eval_metrics_content = sections["evall_metrics"]
        logger.warning("    Found section with typo '# Evall Metrics'. Processing it as '# Eval Metrics'.")

    if eval_metrics_content is not None:
        logger.info("    Found '# Eval Metrics' section.")
        metrics_str = eval_metrics_content.strip()
        if metrics_str:
            # Normalize metric names to be lowercase and correct common errors.
            raw_metrics = [metric.strip().lower().replace('\\', '') for metric in metrics_str.split(',') if metric.strip()]
            validated_metrics = []
            for metric in raw_metrics:
                if metric == 'rogue':
                    metric = 'rouge' # Correct common misspelling
                    logger.info("    Corrected user-provided metric 'rogue' to 'rouge'.")
                
                if metric in SUPPORTED_ON_DEMAND_METRICS:
                    validated_metrics.append(metric)
                else:
                    logger.warning(f"    Unsupported metric '{metric}' found in '# Eval Metrics' and will be ignored. Supported metrics are: {SUPPORTED_ON_DEMAND_METRICS}")

            eval_metrics_list = validated_metrics
            logger.info(f"    Normalized and validated metrics for on-demand evaluation: {eval_metrics_list}")

    return sections, proto_schema, schema_dict, controlled_output_section_found, proto_tool, functions_section_found, rag_engine_endpoint, eval_metrics_list
# --- End Modified parse_sections Function ---

def _generate_and_log_radar_chart(summary_metrics: dict, run_name: str, resumed_run: "aiplatform.ExperimentRun") -> Tuple[Optional[str], Optional[str]]:
    """DEPRECATED: This function is now in eval_utils.py. This stub is for backward compatibility."""
    from eval_utils import _generate_and_log_radar_chart as new_func
    logger.warning("Using deprecated _generate_and_log_radar_chart from run_gemini_from_file.py. Please update calls to use eval_utils.")
    return new_func(summary_metrics, run_name, resumed_run)

def _log_metrics_csv_artifact(metrics_df: pd.DataFrame, run_name: str, resumed_run: "aiplatform.ExperimentRun"):
    """DEPRECATED: This function is now in eval_utils.py. This stub is for backward compatibility."""
    from eval_utils import _log_metrics_csv_artifact as new_func
    logger.warning("Using deprecated _log_metrics_csv_artifact from run_gemini_from_file.py. Please update calls to use eval_utils.")
    new_func(metrics_df, run_name, resumed_run)

def run_evaluation_on_dataframe(eval_df: pd.DataFrame, metrics_to_run: List[str]) -> Optional[pd.DataFrame]:
    """Runs the Vertex Evaluation service on a given DataFrame."""
    try:
        logger.info(f"    Running evaluation for {len(eval_df)} rows with metrics: {metrics_to_run}")
        full_judgement_model_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{JUDGEMENT_MODEL_NAME}"
        autorater_config = AutoraterConfig(autorater_model=full_judgement_model_name)
        
        eval_task = EvalTask(
            dataset=eval_df,
            metrics=metrics_to_run,
            autorater_config=autorater_config,
        )
        # Running evaluate() without an experiment_run_name performs the evaluation without creating an experiment run.
        evaluation_result = eval_task.evaluate()
        logger.info("    Evaluation task completed.")
        return evaluation_result.metrics_table
    except Exception as e:
        logger.error(f"    Error during on-demand evaluation: {e}", exc_info=True)
        return None


# --- Retry Helper for API calls ---
def generate_with_retry(model: "GenerativeModel", *args, **kwargs) -> Any:
    """Calls model.generate_content with retry logic for transient API errors."""
    from google.api_core import exceptions
    import random

    max_retries = 5
    base_delay = 2  # seconds
    for attempt in range(max_retries):
        try:
            return model.generate_content(*args, **kwargs)
        except (exceptions.ResourceExhausted, exceptions.ServiceUnavailable) as e:
            if attempt < max_retries - 1:
                # Exponential backoff with jitter
                wait_time = (base_delay ** attempt) + (random.uniform(0, 1))
                error_type = type(e).__name__
                status_code = getattr(e, 'code', 'N/A')
                logger.warning(f"    API returned {error_type} (Status: {status_code}). Retrying in {wait_time:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
            else:
                logger.error(f"    API call failed after {max_retries} retries due to a persistent transient error.")
                raise e # Re-raise the exception after the final attempt
# --- End Retry Helper ---

def setup_rag_tool(rag_engine_endpoint: str, metadata: Dict[str, Any]) -> Optional[Tool]: # noqa: E501
    """
    Sets up and returns a RAG tool based on the provided resource string.

    Args:
        rag_engine_endpoint: The resource name or display name for the RAG source.
        metadata: The parsed metadata from the prompt file, used to get the model name for the ranker.

    Returns:
        A configured Vertex AI Tool for RAG, or None if setup fails.
    """
    logger.info("--- RAG Engine Processing ---")
    try:
        project_id = os.getenv("PROJECT_ID")
        region = os.getenv("REGION", "us-central1")
        if not project_id or not region:
            raise ValueError("PROJECT_ID and REGION must be set for RAG Engine.")

        # Initialize Vertex AI SDK (if not already done)
        vertexai.init(project=project_id, location=region)

        # Use the same model for the LLM Ranker as the main model for consistency
        model_name_for_rag = metadata.get('model_name', os.getenv('GEMINI_MODEL_NAME', 'gemini-1.5-flash-latest'))
        logger.info(f"    Configuring RAG with LLM Ranker using model: {model_name_for_rag}")
        rag_retrieval_config = rag.RagRetrievalConfig(
            top_k=10, # A sensible default
            ranking=rag.Ranking(
                llm_ranker=rag.LlmRanker(
                    model_name=model_name_for_rag
                )
            )
        )

        rag_resource_string = rag_engine_endpoint.strip()
        rag_store = None

        # The new RAG API uses a single source type: VertexRagStore
        # which can point to either a corpus or a vector search index.
        
        # Case 1: It's a full RagCorpus resource name
        if "/ragCorpora/" in rag_resource_string or "/corpora/" in rag_resource_string:
            logger.info(f"    Interpreting '{rag_resource_string}' as a RagCorpus resource name.")
            rag_store = rag.VertexRagStore(
                rag_resources=[
                    rag.RagResource(
                        rag_corpus=rag_resource_string,
                    )
                ],
                rag_retrieval_config=rag_retrieval_config
            )
        
        # Case 2: It's a full Vector Search Index resource name
        elif "/indexes/" in rag_resource_string:
            logger.info(f"    Interpreting '{rag_resource_string}' as a Vector Search Index resource name.")
            rag_store = rag.VertexRagStore(
                vector_search_index=rag_resource_string,
                rag_retrieval_config=rag_retrieval_config
            )

        # Case 3: It's a display name for an Endpoint or Index
        else:
            logger.info(f"    Interpreting '{rag_resource_string}' as a display name. Searching for a matching Vector Search Endpoint in region '{region}'...")
            aiplatform.init(project=project_id, location=region)
            endpoints = aiplatform.MatchingEngineIndexEndpoint.list(
                filter=f'display_name="{rag_resource_string}"'
            )

            if endpoints:
                endpoint = endpoints[0]
                if len(endpoints) > 1:
                    logger.warning(f"    Found multiple endpoints with the same name. Using the first one: {endpoint.resource_name}")
                logger.info(f"    Found endpoint: {endpoint.resource_name}")
                if not endpoint.deployed_indexes:
                    raise ValueError(f"Endpoint '{endpoint.resource_name}' has no deployed indexes.")
                index_resource_name = endpoint.deployed_indexes[0].index
                logger.info(f"    Using underlying index: {index_resource_name}")
                rag_store = rag.VertexRagStore(vector_search_index=index_resource_name, rag_retrieval_config=rag_retrieval_config)
            else:
                raise ValueError(f"Could not find a RagCorpus or Vector Search resource matching '{rag_resource_string}' in region '{region}'.")

        if rag_store:
            logger.info("    Creating RAG retrieval tool...")
            retrieval = rag.Retrieval(source=rag_store)
            logger.info("--- End RAG Engine Processing ---")
            return Tool.from_retrieval(retrieval)
        
    except Exception as e:
        logger.error(f"    Error during RAG processing: {e}", exc_info=True)
        logger.info("--- End RAG Engine Processing ---")
    return None

def call_gemini_with_prompt_file(prompt_filepath: str, cloud_logging_enabled: bool, dynamic_data_filepath: Optional[str] = None):
    """Processes a single prompt file and calls the Gemini API."""
    model_name = None  # Initialize to ensure it's available in the except block
    try:
        total_cost = 0.0 # Initialize total cost
        # Generate a unique ID for this entire request (primary + potential explanation call)
        request_id = f"req-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        logger.info(f"--- Starting processing for {Path(prompt_filepath).name} ---")
        filepath = Path(prompt_filepath)
        print(f"[{datetime.now()}] Processing prompt from: {filepath.name}") # Use print for top-level status

        file_content = ""
        # --- New logic to handle YAML use case files ---
        if filepath.suffix.lower() in ['.yaml', '.yml']:
            logger.info(f"    Processing as YAML use case file: {filepath.name}")
            try:
                # Use the robustly defined PROJECT_ROOT to find the prompts directory
                prompts_dir = PROJECT_ROOT / 'prompts'
                if not prompts_dir.is_dir():
                    # This is a fatal error if the project structure is incorrect.
                    raise FileNotFoundError(f"The 'prompts' directory was not found at the expected location: {prompts_dir}")
                
                prompt_manager = PromptManager(template_dir=str(prompts_dir))
                
                runtime_data = None
                if dynamic_data_filepath:
                    logger.info(f"    Loading dynamic data from: {dynamic_data_filepath}")
                    with open(dynamic_data_filepath, 'r') as f:
                        # Load the raw JSON data. The PromptManager will now handle
                        # stringifying any complex types.
                        runtime_data = json.load(f)
                
                # The use_case_config_path must be relative to the `prompts` dir.
                relative_yaml_path = Path(prompt_filepath).resolve().relative_to(prompts_dir.resolve())
                
                file_content = prompt_manager.create_prompt_from_use_case(
                    use_case_config_path=str(relative_yaml_path),
                    dynamic_data=runtime_data
                )
                logger.info("    Successfully generated prompt content from YAML template.")
            except (ValueError, FileNotFoundError, ImportError, TypeError) as e:
                logger.error(f"    Failed to process YAML use case file: {e}", exc_info=True)
                # Create an error output file
                model_name_for_error = os.getenv('GEMINI_MODEL_NAME', 'unknown-model')
                output_filename = filepath.with_name(f"{filepath.stem}.{model_name_for_error}.output.md")
                output_filename.write_text(f"# Gemini Output for: {filepath.name}\n\n---\n\nPROCESSING ERROR\nDetails: Failed to process YAML use case file. Error: {e}")
                return
        else:
            # --- Existing logic for Markdown files ---
            file_content = filepath.read_text()

        # 1. Parse Metadata and Body
        metadata, body = parse_metadata_and_body(file_content)
        logger.info(f"    Parsed Metadata: {metadata}")

        # 2. Parse Sections, Schema, and Functions
        sections, proto_schema, schema_dict, controlled_output_section_found, proto_tool, functions_section_found, rag_engine_endpoint, eval_metrics_list = parse_sections(body)

        system_instructions = sections.get("system_instructions")
        user_prompt = sections.get("prompt")
        ground_truth = sections.get("ground_truth") # Get the new ground_truth section
        intent_id = None # Initialize intent_id
        if ground_truth:
            logger.info("    Found '# Ground Truth' section.")
            # Create a stable hash of the ground truth to use as an intent identifier
            intent_id = hashlib.sha256(ground_truth.encode('utf-8')).hexdigest()
            logger.info(f"    Generated Intent ID (from ground_truth hash): {intent_id[:12]}...")
        else:
            logger.info("    No '# Ground Truth' section found. ROUGE metric will not be applicable for this run.")

        if not user_prompt:
            # Fallback logic for finding the prompt (same as before)
            if 'initial_content' in sections and not system_instructions and sections['initial_content']:
                 logger.warning("    No '# Prompt' section found. Using initial content before first heading as prompt.")
                 user_prompt = sections.get('initial_content')
            elif 'initial_content' in sections and system_instructions and sections['initial_content']:
                 logger.warning("    No '# Prompt' section found. Using content between '# System Instructions' and next heading (or EOF) as prompt.")
                 user_prompt = sections.get('initial_content')
            else:
                 logger.error("    Could not find a '# Prompt' section or suitable fallback content.")
                 # model_name is not yet determined, so use a fallback for the error file.
                 model_name_for_error = metadata.get('model_name') or os.getenv('GEMINI_MODEL_NAME', 'unknown-model')
                 output_filename = filepath.with_name(f"{filepath.stem}.{model_name_for_error}.output.md")
                 output_filename.write_text(f"# Gemini Output for: {filepath.name}\n\n---\n\nPROCESSING ERROR\nDetails: No '# Prompt' section found and no fallback content available.")
                 return


        # --- RAG Engine Logic ---
        # This new approach uses Vertex AI's integrated RAG.
        # It creates a tool from the specified RAG corpus and passes it to the model.
        # The model then handles the retrieval and grounding automatically.
        rag_tool = None
        if rag_engine_endpoint:
            rag_tool = setup_rag_tool(rag_engine_endpoint, metadata)
        
        
        
        logger.info(f"    System Instructions Provided: {'Yes' if system_instructions else 'No'}")
        logger.info(f"    User Prompt (first 50 chars): '{user_prompt[:50]}...'")
        logger.info(f"    Function Declarations Provided: {'Yes' if proto_tool else 'No'}")
        logger.info(f"    Rag Tool Provided: {bool(rag_tool)}")
        

        # 3. Determine Model and Generation Config Parameters
        model_name = metadata.get('model_name', os.getenv('GEMINI_MODEL_NAME', 'gemini-1.5-flash-latest')) # Prioritizes metadata, then env var, then fallback
        logger.info(f"    Using Model: {model_name}")

        generation_config_args: Dict[str, Any] = {}
        # Populate from metadata if present
        if 'temperature' in metadata:
            generation_config_args['temperature'] = metadata['temperature']
            logger.info(f"    Temperature: {metadata['temperature']}")
        if 'top_p' in metadata:
            generation_config_args['top_p'] = metadata['top_p']
            logger.info(f"    Top P: {metadata['top_p']}")
        if 'top_k' in metadata:
            generation_config_args['top_k'] = metadata['top_k']
            logger.info(f"    Top K: {metadata['top_k']}")
        if 'seed' in metadata:
            generation_config_args['seed'] = metadata['seed']
            logger.info(f"    Seed: {metadata['seed']}")
        if 'max_output_tokens' in metadata:
            generation_config_args['max_output_tokens'] = metadata['max_output_tokens']
            logger.info(f"    Max Output Tokens: {metadata['max_output_tokens']}")
        if 'stop_sequences' in metadata:
            generation_config_args['stop_sequences'] = metadata['stop_sequences']
            logger.info(f"    Stop Sequences: {metadata['stop_sequences']}")
        if 'logprobs' in metadata and metadata['logprobs'] > 0:
            # The API requires response_logprobs to be true if logprobs (the count) is set.
            # We pass these as a dictionary directly to generate_content,
            # bypassing the GenerationConfig class which doesn't have this param.
            generation_config_args['logprobs'] = metadata['logprobs']
            generation_config_args['response_logprobs'] = True
            logger.info(f"    Log Probs: {metadata['logprobs']} (response_logprobs enabled)")
        elif 'logprobs' in metadata:
            logger.info(f"    Log Probs set to {metadata['logprobs']}. Not requesting log probabilities from API.")

        # Configure JSON mode *only* if requested AND function calling is NOT active
        activate_json_mode = controlled_output_section_found and not proto_tool
        if proto_tool and controlled_output_section_found:
            logger.warning(f"    Both '# Functions' and '# {CONTROLLED_OUTPUT_SECTION_KEY.replace('_', ' ').title()}' sections found. Function calling takes precedence; ignoring '# {CONTROLLED_OUTPUT_SECTION_KEY.replace('_', ' ').title()}' for response_mime_type setting.")

        logger.info(f"    JSON Output Mode Active (via mime_type): {activate_json_mode}")

        if activate_json_mode:
            logger.info("    Configuring model for JSON output (mime type: application/json).")
            generation_config_args['response_mime_type'] = "application/json"
            # Check if proto_schema is a valid object (not None)
            if proto_schema:
                logger.info("    Applying parsed schema to generation config.")
                # When passing a raw dictionary for generation_config, the schema
                # must also be a dictionary, not a proto object.
                # We convert the glm.Schema object back to a dictionary here.
                generation_config_args['response_schema'] = type(proto_schema).to_dict(proto_schema)
            else:
                # This log indicates why the schema wasn't applied
                logger.warning("    JSON mode activated, but no valid schema was parsed/converted. Requesting generic JSON.")

        # We pass the generation_config_args dictionary directly to the API call
        # instead of creating a GenerationConfig object. This is necessary to include
        # 'response_logprobs=True' which is not a parameter in the GenerationConfig
        # class constructor but is required by the backend API when requesting logprobs.
        generation_config = generation_config_args if generation_config_args else None

        # Determine Safety Settings
        safety_settings = metadata.get('safety_settings', DEFAULT_SAFETY_SETTINGS)
        logger.info(f"    Using Safety Settings: {safety_settings}")

        # 4. Configure Credentials
        # The Vertex AI SDK primarily uses Application Default Credentials (ADC).
        # The `vertexai.init()` call handles this. We'll check for the env var for logging.
        google_creds_env = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        if google_creds_env:
             logger.info(f"    Using GOOGLE_APPLICATION_CREDENTIALS: {google_creds_env}")
        else:
            logger.info("    Using Application Default Credentials (ADC) from the environment.")


        # 5. Prepare Model
        model = GenerativeModel(
            model_name=model_name,
            system_instruction=system_instructions,
            safety_settings=safety_settings # Apply safety settings here
        )

        # 6. Call Gemini API (Primary Call)
        logger.info("    Calling Gemini API (Primary Call)...")
        # Combine function calling tools and the RAG tool
        all_tools = []
        if proto_tool:
            all_tools.append(proto_tool)
        if rag_tool:
            all_tools.append(rag_tool)

        start_time_primary = time.monotonic()
        response = generate_with_retry(
            model,
            user_prompt,
            generation_config=generation_config,
            tools=all_tools if all_tools else None
        )
        duration_primary = time.monotonic() - start_time_primary
        logger.info(f"    Primary API call complete in {duration_primary:.2f} seconds.")

        # 7. Process and Prepare Output Content
        output_filename = filepath.with_name(f"{filepath.stem}.{model_name}.output.md")
        output_content = f"# Gemini Output for: {filepath.name}\n"
        output_content += f"## Request Configuration\n"
        output_content += f"- **Model:** {model_name}\n"
        output_content += f"- **System Instructions Provided:** {'Yes' if system_instructions else 'No'}\n"
        # Add details from generation config if used
        if generation_config_args:
             if 'temperature' in generation_config_args:
                  output_content += f"- **Temperature:** {generation_config_args['temperature']}\n"
             if 'top_p' in generation_config_args:
                  output_content += f"- **Top P:** {generation_config_args['top_p']}\n"
             if 'top_k' in generation_config_args:
                  output_content += f"- **Top K:** {generation_config_args['top_k']}\n"
             if 'seed' in generation_config_args:
                  output_content += f"- **Seed:** {generation_config_args['seed']}\n"
             if 'max_output_tokens' in generation_config_args:
                  output_content += f"- **Max Output Tokens:** {generation_config_args['max_output_tokens']}\n"
             if 'stop_sequences' in generation_config_args:
                  output_content += f"- **Stop Sequences:** {generation_config_args['stop_sequences']}\n"

        # Reflect function/JSON mode status accurately
        output_content += f"- **'# Ground Truth' Section Found:** {'Yes' if ground_truth else 'No'}\n"
        output_content += f"- **'# RagEngine' Section Found:** {'Yes' if rag_engine_endpoint else 'No'}\n"
        output_content += f"- **RAG Tool Provided to Model:** {'Yes' if rag_tool else 'No'}\n"
        output_content += f"- **'# {CONTROLLED_OUTPUT_SECTION_KEY.replace('_', ' ').title()}' Section Found:** {'Yes' if controlled_output_section_found else 'No'}\n"
        output_content += f"- **'# Functions' Section Found:** {'Yes' if functions_section_found else 'No'}\n"
        output_content += f"- **Function Calling Active (Tools Provided):** {'Yes' if proto_tool else 'No'}\n"
        output_content += f"- **JSON Output Mode Active (MIME Type):** {activate_json_mode}\n"
        # Clarify schema status: Was it found? Was it successfully parsed and applied?
        output_content += f"- **Schema Parsed & Applied (for JSON Mode):** {'Yes' if activate_json_mode and proto_schema else 'No'}\n"

        # Add safety settings used
        output_content += f"- **Safety Settings Applied:** {safety_settings}\n"
        output_content += f"- **Timestamp:** {datetime.now()}\n\n"

        # Add Usage Metadata if available from primary call
        usage_metadata = getattr(response, 'usage_metadata', None)
        if usage_metadata:
            output_content += f"## Usage Metadata (Primary Call)\n"
            prompt_tokens = getattr(usage_metadata, 'prompt_token_count', 0)
            candidates_tokens = getattr(usage_metadata, 'candidates_token_count', 0)
            total_tokens = getattr(usage_metadata, 'total_token_count', 0)

            output_content += f"- **Prompt Token Count:** {prompt_tokens}\n"
            output_content += f"- **Candidates Token Count:** {candidates_tokens}\n"
            output_content += f"- **Total Token Count:** {total_tokens}\n"
            output_content += f"- **Time Taken:** {duration_primary:.2f} seconds\n"

            # Calculate cost for the primary call
            prices = MODEL_PRICING.get(model_name, MODEL_PRICING.get(model_name.replace('-latest', ''), {}))
            if prices:
                input_cost = (prompt_tokens / 1000) * prices.get("input", 0)
                output_cost = (candidates_tokens / 1000) * prices.get("output", 0)
                call_cost = input_cost + output_cost
                total_cost += call_cost
                output_content += f"- **Estimated Cost:** ${call_cost:.6f}\n"
            else:
                logger.warning(f"    Pricing not found for model '{model_name}'. Cost will not be estimated for this call.")

            output_content += "\n"


        # --- Process Response Content (Text or Function Call) ---
        # Check for grounding metadata to display the retrieved context
        try:
            grounding_metadata = getattr(response.candidates[0], 'grounding_metadata', None)
        except IndexError:
            grounding_metadata = None # Handle cases with no candidates

        if grounding_metadata and hasattr(grounding_metadata, 'retrieval_queries'):
            retrieved_context = ""
            for query in getattr(grounding_metadata, 'retrieval_queries', []):
                for chunk in getattr(query, 'retrieved_chunks', []):
                     # The source attribute contains the GCS URI
                     source_uri = getattr(chunk, 'source', 'N/A')
                     content = getattr(chunk, 'content', 'N/A')
                     retrieved_context += f"Source: {source_uri}\n"
                     retrieved_context += f"Content: {content}\n---\n"
            if retrieved_context:
                 output_content += f"## RAG CONTEXT\n\n"
                 output_content += f"```text\n{retrieved_context}\n```\n\n"

        output_content += f"## RAW OUTPUT\n\n"
        function_call_requested = False
        response_text = ""
        function_call_payload = None # For logging
        raw_json_output_for_explanation = None # Store successfully parsed JSON here

        try:
            # Check for function call first
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        fc = part.function_call
                        logger.info(f"    Model requested function call: {fc.name}")
                        # Prepare payload for logging
                        function_call_payload = {
                            "name": fc.name,
                            "args": type(fc.args).to_dict(fc.args) if fc.args else {}
                        }
                        output_content += f"**Function Call Requested:**\n"
                        output_content += f"- **Name:** `{fc.name}`\n"
                        # Pretty print args if they exist
                        args_dict = type(fc.args).to_dict(fc.args) # Convert proto Struct to dict
                        if args_dict:
                             pretty_args = json.dumps(args_dict, indent=2, ensure_ascii=False, sort_keys=True)
                             output_content += f"- **Arguments:**\n```json\n{pretty_args}\n```\n"
                        else:
                             output_content += f"- **Arguments:** (None provided)\n"
                        function_call_requested = True
                        break # Stop after finding the first function call

            # If no function call was requested, try to get text
            if not function_call_requested:
                try:
                    response_text = response.text
                    logger.info("    Model returned text content.")
                except ValueError as e:
                    # Handle cases where accessing response.text fails (e.g., blocked content)
                    logger.warning(f"    Could not access response text directly (may be blocked or empty): {e}")
                    response_text = "" # Ensure response_text is empty string
                except Exception as e:
                     logger.error(f"    Unexpected error accessing response text: {e}")
                     response_text = f"Error accessing response text: {e}"

        except Exception as e:
            logger.error(f"    Error processing response parts/text: {e}", exc_info=True)
            output_content += f"Error processing response content: {e}\n"

        # --- Structured Logging for Primary Call (Consolidated) ---
        if cloud_logging_enabled:
            safety_ratings_list = []
            logprobs_dict = None

            if response.candidates and response.candidates[0]:
                candidate = response.candidates[0]
                # Extract safety ratings
                if candidate.safety_ratings:
                    safety_ratings_list = [{"category": r.category.name, "probability": r.probability.name, "blocked": r.blocked} for r in candidate.safety_ratings]
                # Extract logprobs and calculate average for evaluation
                if hasattr(candidate, 'logprobs') and candidate.logprobs:
                    logprobs = candidate.logprobs
                    logprobs_data = {}
                    if hasattr(logprobs, 'token_log_probs') and logprobs.token_log_probs:
                        logprobs_data['token_log_probs'] = list(logprobs.token_log_probs)
                    if hasattr(logprobs, 'top_log_probs') and logprobs.top_log_probs:
                        logprobs_data['top_log_probs'] = [dict(item) for item in logprobs.top_log_probs]
                    if logprobs_data:
                        logprobs_dict = logprobs_data

            # Convert usage metadata to dict
            usage_metadata_dict = {}
            if getattr(response, 'usage_metadata', None):
                usage_metadata = response.usage_metadata
                usage_metadata_dict = {
                    "prompt_token_count": usage_metadata.prompt_token_count,
                    "candidates_token_count": usage_metadata.candidates_token_count,
                    "total_token_count": usage_metadata.total_token_count,
                }

            primary_log_payload = {
                "request_id": request_id,
                "user_id": os.getenv("USER", "unknown_user"),
                "prompt_file": filepath.name,
                "model_name": model_name,
                "call_type": "primary_generation",
                "system_instructions": system_instructions,
                "prompt": user_prompt,
                "function_call": function_call_payload, # Populated during response processing
                "response": response_text, # Use 'response' key for eval.py
                "ground_truth": ground_truth, # Add ground_truth to the log payload
                "intent_id": intent_id, # Add the intent ID for grouping
                "usage_metadata": usage_metadata_dict,
                "safety_ratings": safety_ratings_list,
                "generation_config": generation_config_args,
                "logprobs": logprobs_dict,
            }

            log_to_cloud("Gemini API Call", primary_log_payload)
        # --- End Structured Logging ---

        # --- Format Text Output (if applicable) ---
        if not function_call_requested and response_text:
            # Attempt to pretty-print if it looks like JSON, even if JSON mode wasn't forced
            is_likely_json = response_text.strip().startswith('{') and response_text.strip().endswith('}') \
                          or response_text.strip().startswith('[') and response_text.strip().endswith(']')
            parsed_successfully = False
            pretty_json_output = ""

            if is_likely_json:
                try:
                    # Attempt 1: Direct parse
                    logger.info("    Attempting direct JSON parsing of text response...")
                    parsed_json = json.loads(response_text)
                    pretty_json_output = json.dumps(parsed_json, indent=2, ensure_ascii=False, sort_keys=True)
                    output_content += f"```json\n{pretty_json_output}\n```\n" # Wrap in json code block
                    parsed_successfully = True
                    logger.info("    Successfully parsed and pretty-printed JSON text response (Attempt 1).")
                except json.JSONDecodeError as e1:
                    logger.warning(f"    Direct JSON parsing failed: {e1}. Checking for double encoding...")
                    # Attempt 2: Double parse (string literal containing JSON)
                    is_string_literal = response_text.startswith('"') and response_text.endswith('"')
                    if is_string_literal:
                        logger.info("    Response appears to be a string literal. Attempting double parsing...")
                        try:
                            decoded_string = json.loads(response_text)
                            if isinstance(decoded_string, str):
                                logger.info("    Result is a string. Attempting to parse inner JSON...")
                                parsed_inner_json = json.loads(decoded_string)
                                pretty_json_output = json.dumps(parsed_inner_json, indent=2, ensure_ascii=False, sort_keys=True)
                                output_content += f"```json\n{pretty_json_output}\n```\n" # Wrap in json code block
                                parsed_successfully = True
                                logger.info("    Successfully parsed and pretty-printed double-encoded JSON string (Attempt 2).")
                            else:
                                logger.warning(f"    Decoded string literal resulted in non-string type '{type(decoded_string)}'. Treating as parse failure.")
                        except json.JSONDecodeError as e2:
                            logger.warning(f"    Parsing the inner JSON string failed: {e2}.")
                        except Exception as e_inner:
                            logger.warning(f"    Error during inner JSON parsing attempt: {e_inner}.")
                    else:
                        logger.warning("    Response text is not a string literal containing JSON.")

            # Fallback: If not likely JSON or parsing failed, save raw text.
            if not parsed_successfully:
                if is_likely_json: # Add warning if we expected JSON but failed
                     logger.warning("    Could not parse model output as valid JSON. Saving raw text.")
                # Keep fences for the raw text fallback for clarity
                output_content += f"```text\n{response_text}\n```\n"
            elif activate_json_mode: # Store the pretty JSON if parsing succeeded AND JSON mode was active
                raw_json_output_for_explanation = pretty_json_output


        elif not function_call_requested and not response_text:
             # Handle cases with no text output and no function call (e.g., blocked by safety)
             try:
                  finish_reason = "N/A"
                  safety_ratings_str = "N/A"
                  if response.candidates:
                      candidate = response.candidates[0]
                      finish_reason = getattr(candidate.finish_reason, 'name', 'UNKNOWN')
                      safety_ratings = getattr(candidate, 'safety_ratings', [])
                      safety_ratings_str = ', '.join([f"{r.category.name}={r.probability.name}" for r in safety_ratings]) if safety_ratings else "None"

                  output_content += f"(No text content or function call in response. Finish Reason: {finish_reason}, Safety Ratings: [{safety_ratings_str}])"
                  # Also log the prompt feedback if available
                  prompt_feedback = getattr(response, 'prompt_feedback', None)
                  if prompt_feedback:
                      block_reason = getattr(prompt_feedback, 'block_reason', None)
                      block_reason_msg = getattr(prompt_feedback, 'block_reason_message', '')
                      pf_safety_ratings = getattr(prompt_feedback, 'safety_ratings', [])
                      pf_safety_ratings_str = ', '.join([f"{r.category.name}={r.probability.name}" for r in pf_safety_ratings]) if pf_safety_ratings else "None"
                      logger.warning(f"    Prompt Feedback: BlockReason={block_reason}, Message='{block_reason_msg}', SafetyRatings=[{pf_safety_ratings_str}]")
                      output_content += f"\nPrompt Feedback: BlockReason={block_reason}, Message='{block_reason_msg}', SafetyRatings=[{pf_safety_ratings_str}]"

             except Exception as e:
                  # Fallback if response structure is unexpected
                  logger.error(f"    Error extracting details from empty/blocked response: {e}", exc_info=True)
                  output_content += f"(No text content or function call in response. Response details might be incomplete: {response})"


        # --- 8. Make Second Call for Human-Readable Explanation (if applicable) ---
        # Check if JSON mode was active AND if a schema object was successfully created
        if activate_json_mode and raw_json_output_for_explanation and proto_schema:
            logger.info("    Controlled output JSON generated *with schema*. Making second API call for human-readable explanation...")
            explanation_text = ""
            explanation_usage_metadata = None
            try:
                # Use the same model instance, default generation config (no JSON mode, schema, etc.)
                explanation_prompt = (
                    f"The following JSON data was generated based on a specific request and schema.\n" # Mention schema
                    f"Please explain this JSON data in a clear, human-readable format. Focus on the meaning, structure, and key information contained within it, considering the constraints imposed by the original schema.\n\n"
                    f"```json\n{raw_json_output_for_explanation}\n```"
                )
                start_time_explanation = time.monotonic()
                explanation_response = model.generate_content(
                    explanation_prompt
                    # Use default safety settings from the model
                    # Use default generation config (no temp, top_k etc specified here)
                )
                duration_explanation = time.monotonic() - start_time_explanation
                explanation_text = explanation_response.text
                explanation_usage_metadata = getattr(explanation_response, 'usage_metadata', None)
                logger.info(f"    Explanation call successful in {duration_explanation:.2f} seconds.")

                # --- Structured Logging for Explanation Call ---
                if cloud_logging_enabled:
                    explanation_usage_dict = {}
                    if explanation_usage_metadata:
                        explanation_usage_dict = {
                            "prompt_token_count": explanation_usage_metadata.prompt_token_count,
                            "candidates_token_count": explanation_usage_metadata.candidates_token_count,
                            "total_token_count": explanation_usage_metadata.total_token_count,
                        }
                    explanation_safety_ratings = [{"category": r.category.name, "probability": r.probability.name, "blocked": r.blocked} for r in explanation_response.candidates[0].safety_ratings] if explanation_response.candidates else []

                    explanation_log_payload = {
                        "request_id": request_id, # Use same request_id
                        "user_id": os.getenv("USER", "unknown_user"),
                        "prompt_file": filepath.name,
                        "model_name": model_name,
                        "call_type": "explanation_generation",
                        "prompt": explanation_prompt,
                        "response_text": explanation_text,
                        "usage_metadata": explanation_usage_dict,
                        "safety_ratings": explanation_safety_ratings,
                    }
                    log_to_cloud("Gemini API Call", explanation_log_payload)
                # --- End Structured Logging ---
                output_content += f"\n\n## Human-Readable Explanation\n\n{explanation_text}\n" # No code block needed for explanation

                # Add usage metadata for the second call
                if explanation_usage_metadata:
                    output_content += f"\n\n## Usage Metadata (Explanation Call)\n"
                    prompt_tokens = getattr(explanation_usage_metadata, 'prompt_token_count', 0)
                    candidates_tokens = getattr(explanation_usage_metadata, 'candidates_token_count', 0)
                    total_tokens = getattr(explanation_usage_metadata, 'total_token_count', 0)

                    output_content += f"- **Prompt Token Count:** {prompt_tokens}\n"
                    output_content += f"- **Candidates Token Count:** {candidates_tokens}\n"
                    output_content += f"- **Total Token Count:** {total_tokens}\n"
                    output_content += f"- **Time Taken:** {duration_explanation:.2f} seconds\n"

                    # Calculate cost for the explanation call
                    prices = MODEL_PRICING.get(model_name, MODEL_PRICING.get(model_name.replace('-latest', ''), {}))
                    if prices:
                        input_cost = (prompt_tokens / 1000) * prices.get("input", 0)
                        output_cost = (candidates_tokens / 1000) * prices.get("output", 0)
                        call_cost = input_cost + output_cost
                        total_cost += call_cost
                        output_content += f"- **Estimated Cost:** ${call_cost:.6f}\n"

            except Exception as e:
                logger.warning(f"    Failed to get human-readable explanation from second API call: {e}", exc_info=True)
                output_content += f"\n\n## Human-Readable Explanation\n\n(Failed to generate explanation: {e})\n"
        elif activate_json_mode and raw_json_output_for_explanation and not proto_schema:
             logger.info("    Controlled output JSON generated *without a successfully parsed schema*. Skipping explanation call.")
             # Optionally add a note in the output file
             # output_content += f"\n\n## Human-Readable Explanation\n\n(Skipped: JSON generated without a successfully parsed schema being applied.)\n"


        # --- 9. On-Demand Evaluation ---
        eval_output_section = ""
        if eval_metrics_list:
            eval_output_section = run_on_demand_evaluation(
                initial_prompt=user_prompt,
                final_answer=response_text,
                ground_truth=ground_truth,
                eval_metrics_list=eval_metrics_list,
                filepath_stem=filepath.stem,
                run_type="prompt-run"
            )

        # --- 9. Save Final Output ---
        if total_cost > 0:
            output_content += f"\n\n## Total Estimated Cost\n\n**Total:** ${total_cost:.6f}\n"
        
        output_content += eval_output_section

        output_filename.write_text(output_content)
        logger.info(f"--- Finished processing for {filepath.name} ---")
        print(f"    Output saved to: {output_filename}") # Use print for final status

    except FileNotFoundError:
        logger.error(f"Error processing '{prompt_filepath}': File not found.")
    except Exception as e:
        # Enhanced error logging to provide a full traceback in the console
        logger.error(f"An unexpected error occurred during processing of '{prompt_filepath}'.", exc_info=True)
        filepath = Path(prompt_filepath)
        # Use the model_name determined in the try block if available, otherwise fall back.
        model_name_for_error = model_name or os.getenv('GEMINI_MODEL_NAME', 'unknown-model')
        output_filename = filepath.with_name(f"{filepath.stem}.{model_name_for_error}.output.md")
        # Write a more informative error message to the output file
        error_content = (
            f"# Gemini Output for: {filepath.name}\n\n"
            f"---\n\n"
            f"FATAL PROCESSING ERROR\n"
            f"Type: {type(e).__name__}\n"
            f"Details: {e}\n\n"
            f"Please check the application logs for a full traceback."
        )
        output_filename.write_text(error_content)
        logger.info(f"Error details saved to {output_filename}")


def main():
    """Main function to parse arguments and process files."""
    parser = argparse.ArgumentParser(
        description="Process prompt files using the Gemini API, supporting metadata, system instructions, controlled output (JSON schema), and function calling.",
        formatter_class=argparse.RawTextHelpFormatter # Keep formatting in help
        )
    parser.add_argument("prompt_files",
                        metavar="PROMPT_FILE",
                        type=str,
                        nargs='+',
                        help="Path to one or more prompt files to process.\n\n"
                             "Prompt files can contain metadata lines (e.g., 'Model: model-name', 'Temperature: 0.5'),\n"
                             "and sections like '# System Instructions', '# Prompt', '# Controlled Output Schema', '# Functions'.\n\n"
                             "Supported Metadata:\n"
                             "  # RagEngine: <rag_corpus_resource_name> (e.g., projects/.../ragCorpora/123...)\n"
                             "  Model: <model_name> (e.g., gemini-1.5-flash-latest)\n"
                             "  Temperature: <float> (e.g., 0.7)\n"
                             "  Top P: <float> (e.g., 0.95)\n"
                             "  Top K: <int> (e.g., 40)\n"
                             "  Max Output Tokens: <int> (e.g., 1024)\n"
                             "  Stop Sequences: <comma-separated strings> (e.g., 'stop:,end:')\n"
                             "  Safety Settings: <comma-separated pairs> (e.g., 'harassment=none, hate_speech=block_medium_and_above')\n"
                             "    - Categories: harassment, hate_speech, sexually_explicit, dangerous_content\n"
                             "    - Thresholds: block_none (or none), block_low_and_above (or low),\n"
                             "                  block_medium_and_above (or medium), block_only_high (or high)\n\n"
                             "Sections:\n"
                             "  # System Instructions: Optional instructions for the model.\n"
                             "  # RagEngine: The resource name or display name of a Vertex AI RAG Corpus or Vector Search Index/Endpoint to use for RAG.\n"
                             "  # Prompt: The main user prompt.\n"
                             "  # Controlled Output Schema: Optional JSON schema for structured output.\n"
                             "    - Presence triggers JSON mode *if* '# Functions' is not present.\n"
                             "    - If JSON mode is active and the schema is successfully parsed, a second API call\n"
                             "      is made to generate a human-readable explanation of the JSON output.\n"
                             "  # Functions: Optional JSON list of function declarations for the model to call.\n"
                             "    - Presence enables function calling mode; overrides '# Controlled Output Schema' for response type.\n"
                             "    - Example Format:\n"
                             "    ```json\n"
                             "    [\n"
                             "      {\n"
                             "        \"name\": \"find_theaters\",\n"
                             "        \"description\": \"Find theaters showing movies near a location\",\n"
                             "        \"parameters\": {\n"
                             "          \"type\": \"object\",\n"
                             "          \"properties\": {\n"
                             "            \"location\": {\n"
                             "              \"type\": \"string\",\n"
                             "              \"description\": \"The city and state, e.g. San Francisco, CA\"\n"
                             "            },\n"
                             "            \"movie\": {\n"
                             "              \"type\": \"string\",\n"
                             "              \"description\": \"The name of the movie\"\n"
                             "            }\n"
                             "          },\n"
                             "          \"required\": [\"location\", \"movie\"]\n"
                             "        }\n"
                             "      }\n"
                             "    ]\n"
                             "    ```\n\n"
                             "YAML Use Cases:\n"
                             "  You can also provide a path to a .yaml use case configuration file.\n"
                             "  This requires the `prompts/` directory structure to be in place.\n"
                             "  Example: ./.scripts/run_gemini_from_file.py prompts/use_cases/exclusions_340b_rebate.yaml --dynamic-data prompts/use_cases/exclusions_data.json"
                        )
    parser.add_argument("--dynamic-data", type=str, default=None,
                        help="Path to a JSON file containing dynamic data for YAML-based prompt templates."
                        )

    args = parser.parse_args()

    logging_client = None  # Initialize to None
    cloud_logging_handler = None # Initialize to None
    try:
        # --- Setup Cloud Logging once at the start ---
        project_id = os.getenv("PROJECT_ID")
        logging_client, cloud_logging_handler = setup_cloud_logging(project_id)
        cloud_logging_enabled = logging_client is not None
        # --- End Setup ---

        # Use print for overall progress, logger for details/warnings/errors
        print(f"Starting processing for {len(args.prompt_files)} file(s)...")
        for prompt_file in args.prompt_files:
            # Pass the logging status and dynamic data path to the processing function
            call_gemini_with_prompt_file(prompt_file, cloud_logging_enabled, args.dynamic_data)
            print("-" * 30) # Separator between files
    finally:
        # Explicitly flush the handler before closing the client to ensure all
        # logs are sent, addressing potential race conditions at shutdown.
        if cloud_logging_handler:
            try:
                logger.info("Flushing Cloud Logging handler to send pending logs...")
                cloud_logging_handler.transport.flush()
            except Exception as e:
                print(f"Error flushing Cloud Logging handler: {e}", file=sys.stderr)

        # The logging_client object does not have a close() method. The transport on the handler is what needs to be managed, which is done above.

    print("Processing complete.")

if __name__ == "__main__":
    main()