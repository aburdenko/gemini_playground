---
name: ge-realtime-logs-query
description: Expert guidance on writing BigQuery SQL statements to extract, reconstruct, and analyze chat histories from Gemini Enterprise and Discovery Engine real-time logs.
---

# Gemini Enterprise Real-Time Logs Query Guide

This skill provides the required context and schemas for querying Gemini Enterprise / Discovery Engine logs exported to BigQuery.

The logging structure for Discovery Engine is complex. Depending on what information is needed, you must query different tables and use specific aggregation logic.

## Quick Reference

See [queries.md](references/queries.md) for the exact SQL required to:
- Extract full, clean chat histories using the `_user_activity` table.
- Reconstruct streaming chats and internal model "thoughts" using `STRING_AGG` on the granular `gen_ai` tables.
- Map Notebook `engine_id` to human-readable display names.