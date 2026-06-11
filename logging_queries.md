I've reviewed the requirements in your `Log Design - Highmark Requirements.csv` and the new real-time BigQuery tables that were just automatically created (`discoveryengine_googleapis_com_gemini_enterprise_user_activity`, `discoveryengine_googleapis_com_gen_ai_choice`, etc.).

Here is the data dictionary and mapping you need to construct the SQL queries to answer each of your Highmark business questions:

### Core Tables to Use
*   **`discoveryengine_googleapis_com_gemini_enterprise_user_activity`**: Use this for all conversational metrics, session tracking, and user search behavior.
*   **`cloudaudit_googleapis_com_activity`**: Use this Admin Audit Log table for tracking asset creation (creating agents, updating agents).

---

### Data Dictionary & Query Mapping by Requirement

**1. Unique interactions with AI assistant (OOTB) by day**
*   **Table:** `discoveryengine_googleapis_com_gemini_enterprise_user_activity`
*   **Fields Needed:**
    *   `DATE(timestamp)` as interaction_day
    *   `JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.logmetadata.methodname')` -> Filter to `'assist'`, `'stream assist'`, or `'search'`.
    *   `JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.request.userevent.agentspaceinfo.agentinfo.agentid')` -> Must be `NULL` (this proves it's the base assistant, not a custom agent).
    *   `insertId` -> Use `COUNT(DISTINCT insertId)` for the unique interaction count.
*   **Note:** To exclude backend clicks/page views, filter out methods like `'UpdateEngine'` or page load events if they appear.

**2. Unique users who submitted a prompt to the assistant by day**
*   **Table:** `discoveryengine_googleapis_com_gemini_enterprise_user_activity`
*   **Fields Needed:**
    *   Same filters as #1 (`agentid IS NULL` and conversational method names).
    *   `JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.useriamprincipal')` as username.
    *   Metric: `COUNT(DISTINCT JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.useriamprincipal'))`.

**3. Unique users who submitted a prompt to a Custom Agent by day**
*   **Table:** `discoveryengine_googleapis_com_gemini_enterprise_user_activity`
*   **Fields Needed:**
    *   Same filters as #2, EXCEPT:
    *   `JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.request.userevent.agentspaceinfo.agentinfo.agentid')` -> Must be `IS NOT NULL`.

**4 & 4a. Number of agents created by day (Total and By User)**
*   **Table:** `cloudaudit_googleapis_com_activity`
*   **Fields Needed:**
    *   `DATE(timestamp)`
    *   `protopayload_auditlog.methodName` -> Filter where this `LIKE '%CreateEngine'` or `%CreateAgent`.
    *   `protopayload_auditlog.authenticationInfo.principalEmail` -> The user who created the agent.
    *   Metric: `COUNT(DISTINCT protopayload_auditlog.resourceName)`.

**5. User interactions with agents (OOTB vs No-Code vs Full-Code)**
*   **Tables:** UNION between `gemini_enterprise_user_activity` (for OOTB & No-Code) and your `adk_live_telemetry` or `travel_concierge` logs (for Full-Code).
*   **Fields Needed (from GE Activity):**
    *   `DATE(timestamp)`
    *   Use a `CASE` statement on `JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.request.userevent.agentspaceinfo.agentinfo.agentid')`. If `NULL`, label as "OOTB". If `NOT NULL`, label as "No-Code".
*   **Fields Needed (from ADK logs):**
    *   Use the `adk_live_telemetry` sink and label all entries as "Full-Code".

**6. Agents Updated By User Per Day**
*   **Table:** `cloudaudit_googleapis_com_activity` (or `gemini_enterprise_user_activity` depending on if the method propagates down).
*   **Fields Needed:**
    *   `DATE(timestamp)`
    *   `protopayload_auditlog.methodName` -> Filter where `LIKE '%UpdateEngine%'` or `%UpdateAgent%`. (We just saw `UpdateEngine` in your live logs during testing).
    *   `protopayload_auditlog.authenticationInfo.principalEmail` -> Username.

**7 & 8. Number of Notebooks created by day (Total and By User)**
*   **Table:** `cloudaudit_googleapis_com_activity`
*   **Fields Needed:**
    *   `DATE(timestamp)`
    *   `protopayload_auditlog.methodName` -> Filter for `%CreateSession` or notebook-specific metadata creation.
    *   `protopayload_auditlog.authenticationInfo.principalEmail`.

**9. Chat interactions with a Notebook**
*   **Table:** `discoveryengine_googleapis_com_gemini_enterprise_user_activity`
*   **Fields Needed:**
    *   `JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.request.userevent.agentspaceinfo.agentspacepagetype')` -> Filter for `NOTEBOOK_LM` or equivalent enum.
    *   `insertId` -> Count distinct.

### A SQL Tip for the Unmasked Payloads
When you are querying the `discoveryengine_googleapis_com_gemini_enterprise_user_activity` table to extract the actual text the user typed (which is now unmasked thanks to the engine config change!), the payload structure changes depending on the API method used. You should use a `CASE` statement like this in your queries:

```sql
CASE 
  WHEN LOWER(JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.logmetadata.methodname')) IN ('assist', 'chat', 'stream assist') 
    THEN JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.request.query.text')
  WHEN LOWER(JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.logmetadata.methodname')) = 'search' 
    THEN JSON_EXTRACT_SCALAR(TO_JSON_STRING(jsonPayload), '$.request.query')
END AS user_text_prompt
```
