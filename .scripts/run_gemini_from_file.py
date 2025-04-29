#!/usr/bin/env python3
# Run with python3 ./.scripts/run_gemini_from_file.py suggested-prompt-2025-06-29.md

import google.generativeai as genai
import os
import sys
import argparse
import re
import json
from datetime import datetime
from pathlib import Path

# --- Add necessary imports ---
from google.generativeai.types import GenerationConfig, HarmCategory, HarmBlockThreshold
# We need protos for schema definition
import google.ai.generativelanguage as glm
# --- End Add necessary imports ---

# --- Constants ---
DEFAULT_MODEL = "gemini-1.5-flash-latest" # Default model
OUTPUT_SUFFIX = ".output.md"
# Regex to find sections like # System Instructions, # Prompt, etc.
SECTION_PATTERN = re.compile(r"^\s*#\s+([\w\s]+)\s*$", re.MULTILINE)
# --- REMOVED Schema Detection Regex ---
# SCHEMA_EXTRACTION_PATTERN = re.compile( ... )
# --- End REMOVED Schema Detection Regex ---


# --- Schema Conversion Function (remains the same) ---
def dict_to_proto_schema(schema_dict: dict) -> glm.Schema | None:
    """Converts a Python dictionary representing a JSON schema to a glm.Schema."""
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
                 print(f"    Warning: Expected dict for property details '{prop_name}', got {type(prop_details)}. Skipping.", file=sys.stderr)
                 return None

            prop_type_str = prop_details.get("type", "string").lower()
            prop = glm.Schema(
                description=prop_details.get("description", ""),
                nullable=prop_details.get("nullable", False)
            )

            prop.type = TYPE_MAP.get(prop_type_str, glm.Type.STRING) # Default to STRING

            if prop.type == glm.Type.STRING:
                enum = prop_details.get("enum")
                if enum:
                    prop.enum.values.extend(enum)

            elif prop.type == glm.Type.ARRAY:
                items = prop_details.get("items")
                if items:
                    item_schema = convert_property("items", items)
                    if item_schema:
                         prop.items = item_schema
                    else:
                         print(f"    Warning: Could not determine schema for array items in '{prop_name}'. Defaulting to STRING array.", file=sys.stderr)
                         prop.items = glm.Schema(type=glm.Type.STRING)
                else:
                     prop.items = glm.Schema(type=glm.Type.STRING)


            elif prop.type == glm.Type.OBJECT:
                properties = prop_details.get("properties")
                if properties and isinstance(properties, dict):
                    for name, details in properties.items():
                        converted_sub_prop = convert_property(name, details)
                        if converted_sub_prop:
                             prop.properties[name] = converted_sub_prop
                required = prop_details.get("required")
                if required and isinstance(required, list):
                    prop.required.extend(required)

            return prop

        # Start conversion from the root of the schema dictionary
        root_schema = convert_property("root", schema_dict)
        return root_schema

    except Exception as e:
        print(f"    Error converting dictionary to proto schema: {e}", file=sys.stderr)
        return None
# --- End Schema Conversion Function ---


def parse_metadata_and_body(file_content):
    """
    Parses specific metadata keys (like Model Used, Temperature) found anywhere
    in the file content and returns them as a dictionary.
    Returns the full original content as the body for further section parsing.
    """
    metadata_dict = {}
    body_content = file_content.strip()

    model_match = re.search(r"Model(?: Used)?(?: \(intended\))?:\s*([a-zA-Z0-9\-\.]+)", file_content, re.IGNORECASE)
    if model_match:
        metadata_dict['model_name'] = model_match.group(1).strip()

    temp_match = re.search(r"Temperature:\s*([0-9\.]+)", file_content, re.IGNORECASE)
    if temp_match:
        try:
            metadata_dict['temperature'] = float(temp_match.group(1).strip())
        except ValueError:
            print(f"    Warning: Could not parse temperature value: {temp_match.group(1)}", file=sys.stderr)

    return metadata_dict, body_content


# --- Modified parse_sections Function ---
def parse_sections(text_content):
    """
    Parses the text content into sections based on headings.
    Looks for a '# Controlled Output' section to extract the JSON schema.
    """
    sections = {}
    last_pos = 0
    # Use a generic name for content before the first heading, or handle it differently if needed
    current_section_name = "initial_content"

    # Iterate through headings to split content into sections
    for match in SECTION_PATTERN.finditer(text_content):
        # Normalize the heading name (lowercase, underscores)
        section_name = match.group(1).strip().lower().replace(" ", "_")
        start, end = match.span()

        # Capture content between the last heading (or start) and the current one
        section_content = text_content[last_pos:start].strip()
        if section_content:
            sections[current_section_name] = section_content

        # Update for the next iteration
        current_section_name = section_name
        last_pos = end

    # Capture the content after the last heading
    section_content = text_content[last_pos:].strip()
    if section_content:
        sections[current_section_name] = section_content

    # --- Schema Extraction from '# Controlled Output' section ---
    schema_dict = None
    proto_schema = None
    # Check if the 'controlled_output' section exists
    if 'controlled_output' in sections:
        schema_json_str = sections['controlled_output']
        # Remove potential ```json ``` wrappers if they still exist for compatibility
        schema_json_str = re.sub(r"^\s*```json\s*", "", schema_json_str, flags=re.IGNORECASE)
        schema_json_str = re.sub(r"\s*```\s*$", "", schema_json_str)
        schema_json_str = schema_json_str.strip() # Clean up whitespace

        if schema_json_str: # Ensure there's content to parse
            try:
                schema_dict = json.loads(schema_json_str)
                print("    JSON schema found in '# Controlled Output' section and parsed.")
                proto_schema = dict_to_proto_schema(schema_dict)
                if proto_schema:
                    print("    Successfully converted schema to proto format.")
            except json.JSONDecodeError as e:
                print(f"    Warning: Found '# Controlled Output' section but failed to parse JSON: {e}", file=sys.stderr)
            except Exception as e:
                print(f"    Warning: Error processing schema from '# Controlled Output' section: {e}", file=sys.stderr)
        else:
             print("    Warning: Found '# Controlled Output' section, but it was empty.", file=sys.stderr)

    # Return the parsed sections and the proto_schema (which will be None if not found/parsed)
    return sections, proto_schema
# --- End Modified parse_sections Function ---


def call_gemini_with_prompt_file(prompt_filepath):
    """Processes a single prompt file and calls the Gemini API."""
    try:
        filepath = Path(prompt_filepath)
        print(f"[{datetime.now()}] Processing prompt from: {filepath.name}")

        file_content = filepath.read_text()

        # 1. Parse Metadata and Body
        metadata, body = parse_metadata_and_body(file_content)

        # 2. Parse Sections from Body and Extract Schema (using new logic)
        sections, proto_schema = parse_sections(body) # Returns schema if found

        system_instructions = sections.get("system_instructions")
        user_prompt = sections.get("prompt")

        if not user_prompt:
            # If no # Prompt, maybe use 'initial_content' or the whole body?
            # For now, let's stick to requiring # Prompt for clarity.
            # user_prompt = sections.get("initial_content") # Alternative
            if 'initial_content' in sections and not system_instructions:
                 # If there's only initial content and no system instructions, maybe treat it as the prompt?
                 # This logic can be complex. Sticking to requiring # Prompt is safer.
                 print("    Warning: No '# Prompt' section found. Attempting to use initial content.", file=sys.stderr)
                 user_prompt = sections.get('initial_content') # Be cautious with this fallback
            if not user_prompt:
                 print("    Error: Could not find a '# Prompt' section or suitable fallback.", file=sys.stderr)
                 return


        print(f"    System Instructions (parsed): '{system_instructions[:50]}...' " if system_instructions else "    System Instructions: None")
        print(f"    User Prompt (parsed): '{user_prompt[:50]}...'")

        # 3. Determine Model and Temperature
        model_name = metadata.get('model_name', os.getenv('GEMINI_MODEL_NAME', DEFAULT_MODEL))
        temperature = metadata.get('temperature')
        print(f"    Using Model: {model_name}")
        if temperature is not None:
            print(f"    Temperature: {temperature}")

        # --- Implicit Controlled Output Determination ---
        activate_controlled_output = proto_schema is not None
        print(f"    JSON Schema Found (in # Controlled Output): {activate_controlled_output}")
        print(f"    Controlled Output Active (JSON Mode): {activate_controlled_output}")
        # --- End Implicit Controlled Output Determination ---


        # 4. Configure API Key / Credentials (remains the same)
        api_key = os.getenv('GEMINI_API_KEY')
        google_creds_env = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

        if api_key:
            print("    Using GEMINI_API_KEY.")
            genai.configure(api_key=api_key)
        elif google_creds_env:
             print(f"    Using GOOGLE_APPLICATION_CREDENTIALS: {google_creds_env}")
             pass # Library handles automatically
        else:
            try:
                import google.auth
                credentials, project_id = google.auth.default()
                if not credentials:
                     raise ValueError("No credentials found")
                print("    Using Application Default Credentials (discovered).")
            except Exception as e:
                print(f"    Error: No GEMINI_API_KEY, GOOGLE_APPLICATION_CREDENTIALS, or discoverable ADC found: {e}", file=sys.stderr)
                print("    Please set credentials.", file=sys.stderr)
                return


        # 5. Prepare Model and Generation Config (remains the same)
        model = genai.GenerativeModel(model_name=model_name, system_instruction=system_instructions)

        generation_config_args = {}
        if temperature is not None:
            generation_config_args['temperature'] = temperature

        if activate_controlled_output: # Check if schema was successfully processed
            print("    Configuring model for JSON output with schema.")
            generation_config_args['response_mime_type'] = "application/json"
            generation_config_args['response_schema'] = proto_schema

        generation_config = GenerationConfig(**generation_config_args) if generation_config_args else None


        # 6. Call Gemini API (remains the same)
        print("    Calling Gemini API...")
        response = model.generate_content(
            user_prompt,
            generation_config=generation_config,
            safety_settings={
                 HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                 HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                 HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                 HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
        )
        print("    API call complete.")

        # 7. Process and Save Response (remains the same)
        output_filename = filepath.with_suffix(OUTPUT_SUFFIX)
        output_content = f"# Gemini Output for: {filepath.name}\n"
        output_content += f"## Model: {model_name}\n"
        output_content += f"## System Instructions Provided: {'Yes' if system_instructions else 'No'}\n"
        if temperature is not None:
             output_content += f"## Temperature: {temperature}\n"
        output_content += f"## Controlled Output Active (JSON Mode): {activate_controlled_output}\n"
        output_content += f"## Timestamp: {datetime.now()}\n\n"
        output_content += f"## RAW OUTPUT: \n\n"

        if activate_controlled_output and response.text:
             try:
                  # Attempt to pretty-print the JSON for readability in the output file
                  parsed_json = json.loads(response.text)
                  pretty_json = json.dumps(parsed_json, indent=2)
                  output_content += f"```json\n{pretty_json}\n```\n"
             except json.JSONDecodeError:
                  print("    Warning: Model output was not valid JSON despite schema request.", file=sys.stderr)
                  output_content += f"```text\n{response.text}\n```\n" # Save as text if not valid JSON
        elif response.text:
             output_content += response.text
        else:
             try:
                  finish_reason = response.candidates[0].finish_reason.name
                  output_content += f"(No text content in response. Finish Reason: {finish_reason})"
             except (IndexError, AttributeError):
                  output_content += "(No text content in response)"


        output_filename.write_text(output_content)
        print(f"    Output saved to: {output_filename}")

    except FileNotFoundError:
        print(f"[{datetime.now()}] Error processing '{prompt_filepath}': File not found.", file=sys.stderr)
    except genai.types.generation_types.BlockedPromptException as e:
         print(f"[{datetime.now()}] Error processing '{prompt_filepath}': Prompt blocked by API. {e}", file=sys.stderr)
         output_filename = Path(prompt_filepath).with_suffix(OUTPUT_SUFFIX)
         output_filename.write_text(f"# Gemini Output for: {Path(prompt_filepath).name}\n\n---\n\nPROMPT BLOCKED\nReason: {e}")
    except Exception as e:
        print(f"[{datetime.now()}] Error processing '{prompt_filepath}': {type(e).__name__} - {e}", file=sys.stderr)


def main():
    """Main function to parse arguments and process files."""
    parser = argparse.ArgumentParser(description="Process prompt files using the Gemini API.")
    parser.add_argument("prompt_files",
                        metavar="PROMPT_FILE",
                        type=str,
                        nargs='+',
                        help="Path to one or more prompt files to process.")

    args = parser.parse_args()

    for prompt_file in args.prompt_files:
        call_gemini_with_prompt_file(prompt_file)
        print("-" * 20)

if __name__ == "__main__":
    main()
