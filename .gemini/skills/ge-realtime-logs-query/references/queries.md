# Querying Gemini Enterprise Real-Time Logs

This document provides optimized SQL queries and schema details for analyzing NotebookLM Enterprise / Discovery Engine Agent Builder chats that have been exported to BigQuery.

## Core Concepts

Discovery Engine logs interactions across several tables. The two most relevant for reconstructing chats are:
1.  **`discoveryengine_googleapis_com_gemini_enterprise_user_activity`**: The most efficient table for complete interactions. It logs the user's prompt and the model's final, full text response in a single row under the `StreamAssist` method.
2.  **`discoveryengine_googleapis_com_gen_ai_choice` & `_user_message`**: These tables log granular streaming chunks and internal model reasoning ("thoughts"). They require complex `STRING_AGG` and `UNION ALL` grouping by the `trace` ID (session ID) to reconstruct a readable chat.

**Best Practice:** Always default to querying the `_user_activity` table unless the user explicitly requests to see the internal model reasoning or granular streaming data.

## 1. Optimal Query: Full Chat History (No Thoughts)

Use this query to extract clean, chronological chat histories, including the user who made the request and the Notebook ID (`engine_id`).

```sql
SELECT 
    REGEXP_EXTRACT(jsonPayload.logmetadata.name, r'/engines/([^/]+)') as notebook_id,
    timestamp,
    jsonPayload.useriamprincipal AS user_principal,
    jsonPayload.request.query.parts[SAFE_OFFSET(0)].text AS user_prompt,
    jsonPayload.servicetextreply AS response
FROM 
    `{PROJECT_ID}.{DATASET}.discoveryengine_googleapis_com_gemini_enterprise_user_activity`
WHERE 
    jsonPayload.logmetadata.methodname = 'StreamAssist'
    -- AND REGEXP_EXTRACT(jsonPayload.logmetadata.name, r'/engines/([^/]+)') = 'gemini-enterprise-123456789'
ORDER BY 
    notebook_id,
    timestamp ASC;
```

## 2. Advanced Query: Chat History WITH Model Thoughts

If the user needs to debug hallucinating agents or see the "thoughts" (internal reasoning) of the model, you must use the granular `gen_ai` tables.

*Note: For the model's thoughts to be visible, `sensitiveLoggingEnabled` and `observabilityEnabled` must be set to `true` on the Project's `customerProvidedConfig`.*

```sql
WITH RawLogs AS (
    -- 1. Get the User Prompts
    SELECT 
        resource.labels.engine_id as notebook_id,
        timestamp,
        trace as session_id,
        'USER' as role,
        jsonPayload.content.parts[SAFE_OFFSET(0)].text as text,
        'false' as is_thought
    FROM 
        `{PROJECT_ID}.{DATASET}.discoveryengine_googleapis_com_gen_ai_user_message`

    UNION ALL

    -- 2. Get the Model Responses and Thoughts
    SELECT 
        resource.labels.engine_id as notebook_id,
        timestamp,
        trace as session_id,
        'MODEL' as role,
        jsonPayload.content.parts[SAFE_OFFSET(0)].text as text,
        IFNULL(CAST(jsonPayload.content.parts[SAFE_OFFSET(0)].thought AS STRING), 'false') as is_thought
    FROM 
        `{PROJECT_ID}.{DATASET}.discoveryengine_googleapis_com_gen_ai_choice`
    WHERE 
        jsonPayload.content.parts[SAFE_OFFSET(0)].text IS NOT NULL
)

-- 3. Aggregate the streaming chunks into single text blocks
SELECT 
    notebook_id,
    session_id,
    role,
    is_thought,
    STRING_AGG(text, "" ORDER BY timestamp ASC) as full_text,
    MIN(timestamp) as min_timestamp
FROM RawLogs
-- WHERE notebook_id = 'gemini-enterprise-123456789'
GROUP BY 
    notebook_id,
    session_id,
    role,
    is_thought
ORDER BY 
    notebook_id,
    session_id,
    min_timestamp ASC;
```

## 3. Identifying Notebook Display Names

The real-time logs primarily track notebooks by their system `engine_id` (e.g., `gemini-enterprise-17773115...`). Finding the human-readable display name (e.g., "Google Developers") is inconsistent. 

To attempt to map `engine_id` to `displayname`:

```sql
SELECT DISTINCT
    REGEXP_EXTRACT(jsonPayload.response.name, r'/engines/([^/]+)') as engine_id,
    jsonPayload.response.displayname as notebook_name
FROM 
    `{PROJECT_ID}.{DATASET}.discoveryengine_googleapis_com_gemini_enterprise_user_activity`
WHERE 
    jsonPayload.response.displayname IS NOT NULL
    AND jsonPayload.response.name LIKE '%/engines/%'
    AND jsonPayload.response.name NOT LIKE '%/agents/%';
```