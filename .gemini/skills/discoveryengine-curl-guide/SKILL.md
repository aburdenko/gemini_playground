---
name: discoveryengine-curl-guide
description: Guide for making successful CURL requests to the Google Cloud Discovery Engine (Agent Builder) API. Use this when you encounter 403 errors, need to patch project-level configs like notebooklmConfig, or have issues targeting regional endpoints.
---
# Discovery Engine (Agent Builder) CURL Guide

This skill provides expert guidance for interacting with the Google Cloud Discovery Engine API via CURL, specifically addressing common authentication, endpoint routing, and formatting errors.

## Quick Reference

If you are attempting to make a `curl` request to the Discovery Engine API (e.g., to patch `customerProvidedConfig` or `notebooklmConfig`) and it is failing, please consult the detailed patterns guide.

See [curl_patterns.md](references/curl_patterns.md) for complete instructions on:
- **Project ID vs. Project Number** usage to resolve `USER_PROJECT_DENIED` (403) errors.
- **Regional API endpoints** (e.g., `us-discoveryengine.googleapis.com` vs `global-discoveryengine`).
- **Bash syntax requirements** for line continuations.
- **Example Payload** for enabling `sensitiveLoggingEnabled`.