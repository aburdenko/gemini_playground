#!/usr/bin/env python3

import os
import sys
import uvicorn
import logging as python_logging

# Add rag-agent to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'agents', 'rag-agent')))

import google.auth
from starlette.middleware.base import BaseHTTPMiddleware
import json
# --- Configuration & Custom Logger ---
PROJECT_ID = os.environ.get("PROJECT_ID")
if not PROJECT_ID:
    _, PROJECT_ID = google.auth.default()

if not PROJECT_ID:
    raise ValueError("Could not determine GCP project ID.")

LOCATION = os.environ.get("REGION", "us-central1")
SHORT_LOG_NAME = os.environ.get("LOG_NAME", "run_gemini_from_file")
from starlette.requests import Request

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        print(f"DEBUG: LoggingMiddleware entered for path: {request.url.path}")
        
        if "/invoke" in request.url.path or "/run_sse" in request.url.path:
            # Read the request body
            body = await request.body()
            print(f"DEBUG: LoggingMiddleware received body: {body.decode()}")
            
            # Let the request proceed to get the response
            response = await call_next(request)
            
            # Now, log the relevant information
            try:
                request_data = json.loads(body)
                print(f"DEBUG: LoggingMiddleware parsed request_data: {request_data}")
                prompt = request_data.get("newMessage", {}).get("parts", [{}])[0].get("text", "")
                if prompt:
                    print(f"DEBUG: LoggingMiddleware triggered for prompt: {prompt}")
                    python_logging.info("ADK Web Log: Middleware triggered for prompt: %s" % prompt, extra={'json_fields': {'prompt': prompt}})
            except (json.JSONDecodeError, KeyError):
                # Ignore if body is not JSON or doesn't have the expected structure.
                print(f"DEBUG: LoggingMiddleware: Failed to parse JSON body or find 'prompt' for path: {request.url.path}")
                pass
            return response
        else:
            # If the path does not match, just pass the request through
            return await call_next(request)

# --- App Initialization ---
# We no longer need to set up logging here; it will be passed to uvicorn.run()
from google.adk.cli.fast_api import get_fast_api_app
from fastapi import FastAPI # Import FastAPI for type hinting in lifespan
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup event: Initialize CloudLoggingHandler
    import google.auth
    from google.cloud import logging as google_cloud_logging
    from google.cloud.logging.handlers import CloudLoggingHandler # Use CloudLoggingHandler

    current_project_id = PROJECT_ID
    if not current_project_id:
        _, current_project_id = google.auth.default()

    if current_project_id:
        client = google_cloud_logging.Client(project=current_project_id)
        cloud_handler = CloudLoggingHandler(client=client, name=SHORT_LOG_NAME) # Use CloudLoggingHandler
        root_logger = python_logging.getLogger()
        root_logger.addHandler(cloud_handler)
        root_logger.setLevel(python_logging.DEBUG)
        cloud_handler.setLevel(python_logging.INFO)
        python_logging.info("CloudLoggingHandler attached in worker process.")
    else:
        python_logging.warning("PROJECT_ID not found, CloudLoggingHandler not attached in worker process.")

    yield

    # Shutdown event: Close CloudLoggingHandler
    root_logger = python_logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, CloudLoggingHandler): # Check for CloudLoggingHandler
            handler.close()
            root_logger.removeHandler(handler)
            python_logging.info("CloudLoggingHandler closed in worker process.")

app = get_fast_api_app(
    agents_dir=os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'agents')),
    web=True,
    lifespan=lifespan,
)
app.add_middleware(LoggingMiddleware)

if __name__ == "__main__":
    print("--- Starting ADK Web Server with custom logging ---")
    print("ADK will manage its own internal OpenTelemetry tracing.")
    uvicorn.run(
        "run_adk_web_with_logging:app",
        host="127.0.0.1",
        port=8001,
        reload=True, # This enables auto-reloading
    )