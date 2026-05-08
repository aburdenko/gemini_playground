---
name: coscientist-enterprise
description: Guidance and commands for interacting with the Co-Scientist Enterprise CLI via the v1alpha API (Discovery Engine). Use this when starting, monitoring, testing Co-Scientist instances, or working with EnterpriseApiService direct targets.
---

# 1. Get an access token using standard gcloud
TOKEN=$(gcloud auth application-default print-access-token)

# 2. Call the Discovery Engine API endpoint directly via curl
curl -X GET \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-Goog-User-Project: YOUR_PROJECT_NUMBER" \
    "https://discoveryengine.googleapis.com/v1alpha/projects/YOUR_PROJECT_NUMBER/locations/global/collections/default_collection/engines"

## Co-Scientist Enterprise CLI

This directory contains a script to programmatically start and monitor a Co-Scientist session via the v1alpha API (Discovery Engine v1alpha endpoints).

To run it, you must have the Discovery Engine User IAM role on the GCP project and provide your PROJECT_NUMBER and APP_ID. Since Co-Scientist instances can take a while to finish, this script behaves as a two-stage CLI tool so you don't need to leave a local process running.

### 1. Start a Session
To start an instance, run the client with the `--command=start` flag (or omit the command flag, as start is the default). Make sure you specify where to save the state file.

```bash
blaze run //cloud/ai/science/coscientist/dev:coscientist_enterprise_cli -- \
    --project_number=YOUR_PROJECT_NUMBER \
    --app_id=YOUR_APP_ID \
    --query="Generate experimental hypotheses to cure the common cold using non-invasive techniques." \
    --command=start \
    --state_file=/tmp/coscientist_state.json
```
This will run to get the initial `session_id` and `instance_id`, save them locally to your state file, and then immediately exit, letting the instance generate remotely.

### 2. Check the Status
Run the script anytime via the `--command=status` flag and provide the exact same state file where you stored the initial instance parameters.

```bash
blaze run //cloud/ai/science/coscientist/dev:coscientist_enterprise_cli -- \
    --project_number=YOUR_PROJECT_NUMBER \
    --app_id=YOUR_APP_ID \
    --command=status \
    --state_file=/tmp/coscientist_state.json
```
It will reload the IDs, hit the endpoint to check the current generation state, and:
- If the instance state is not `SUCCEEDED`, inform you that the instance is still unready.
- If it has successfully finished, it will output the overall generated summary of the session as well as fetch and log the fully detailed data models returned for each idea successfully constructed.

## Direct EnterpriseApiService mode
By default the CLI drives runs through OnePlatform / IdeaForgeService in prod. Use `--direct_api_target` to bypass IdeaForge and stubby-call `EnterpriseApiService` directly. Useful for testing changes in the CoScientist enterprise stack (API, worker, queue receivers, wrapper factory) against environments that have no IdeaForge deployment (e.g. autopush, or your own boq run).

The CLI still creates the parent DE session via the prod OnePlatform (`createSession` + `streamAssist`), then synthesizes a fresh `coscientistInstances/<id>` resource name and stubby-calls `StartCoscientistInstance` / `GetCoscientistInstance` / `GetHypothesis` against the configured target. The session lives in prod DE Spanner regardless of the direct-stubby target, which is what `EnterpriseApiService.VerifyUserAccessToSession` reads.

`--direct_api_target` accepts:
- `autopush-global` -- aliased to `blade:cloud_ai_coscientist.enterpriseapiservice-autopush-global`.
- `localhost:<port>` -- for `boq run --env=dev` workflows.
- Any raw `blade:...` BNS for ad-hoc targets (e.g. `blade:cloud_ai_coscientist.enterpriseapiservice-staging-qual-us`).

The `--direct_api_target` value used at start is recorded indirectly via the synthesized `coscientist_instance_name` in the state file; the same flag must be passed on subsequent status invocations against the same instance. To check status without a state file, pass `--coscientist_instance_name=<full resource name>` (the direct-mode equivalent of `--session_id`); `--session_id` itself is only useful in OnePlatform mode where IdeaForge can resolve the instance from the session.

## Auth and policy
The CLI mints a LOAS-derived GaiaMint EUC via `auth_utils.GaiaMintFromLoas` and attaches it on every RPC, so any corp engineer can target a deployed `cloud-ai-coscientist-enterprise-api` job. The bundle's existing `UNPRIVILEGED_USER { all_normal_users {} }` binding accepts the self-presented EUC; `axt_level: AXT_L3` is satisfied because LOAS-derived self-EUC presentation is allowed via `USE_LOAS` (go/rpcsp2-binding-evaluation#permissions-that-are-always-granted). Per-instance data access is still gated by the GCP project ACL on the parent DE session.

rpcStudio remains a useful alternative if you want to inspect or replay a call interactively.

## Testing against autopush-global
`enterpriseapiservice-autopush-global` only serves global-region traffic; the handler returns `APP_ERROR(3) Incorrect API endpoint used`. The current endpoint can only serve traffic from "global" region if you hit it with a `us` or `eu` session. So you need a project + engine in the global location, not a CMEK engine (CMEK requires a regional location).

You also need the Discovery Engine User IAM role on the project so the prod OnePlatform `createSession` + `streamAssist` calls succeed. Verify with the curl command at the top of this guide.

Once that returns a non-empty engine list, start an instance:

```bash
blaze run //cloud/ai/science/coscientist/engine/enterprise/dev:coscientist_enterprise_cli -- \
    --project_number=$PROJECT_NUMBER \
    --app_id=$APP_ID \
    --location=global \
    --command=start \
    --query="Generate experimental hypotheses to cure the common cold using non-invasive techniques." \
    --tier=TIER0 \
    --state_file=/tmp/coscientist_enterprise_state_autopush.json \
    --direct_api_target=autopush-global
```
A successful run logs "Instance started. Run with `--command=status` to check progress." and writes the synthesized `coscientist_instance_name` to the state file. Then poll status:

```bash
blaze run //cloud/ai/science/coscientist/engine/enterprise/dev:coscientist_enterprise_cli -- \
    --project_number=$PROJECT_NUMBER \
    --app_id=$APP_ID \
    --location=global \
    --command=status \
    --state_file=/tmp/coscientist_enterprise_state_autopush.json \
    --direct_api_target=autopush-global
```
Expected progression: `STATE_RUNNING` → `STATE_COMPLETED` (the overview and fetched ideas print on completion) or `STATE_FAILED`.

## Testing against a local boq run
For purely local development, run a `boq run --env=dev` instance and target it via `--direct_api_target=localhost:<port>`. The dev selector binds `SYSTEM=self{}` so the LOAS-only call from the local binary is accepted with no additional setup.

```bash
blaze run //cloud/ai/science/coscientist/engine/enterprise/dev:coscientist_enterprise_cli -- \
    --project_number=$PROJECT_NUMBER \
    --app_id=$APP_ID \
    --query="..." \
    --command=start \
    --state_file=/tmp/coscientist_state.json \
    --direct_api_target=localhost:9876
```

## Usage Flags

| Flag | Default | Description |
|---|---|---|
| `--command` | `start` | The command to run: 'start' to start an instance and 'status' to retrieve results. |
| `--project_number` | None | The numeric identifier for your GCP project (required). |
| `--app_id` | None | The ID of the Agentspace app that you want to query (required). |
| `--query` | see below | The prompt you want to run (used in start only). |
| `--tier` | `TIER1` | Maps to `predefinedGenerationConfig.type` in the request. |
| `--state_file` | `/tmp/coscientist_state.json` | Path to a local file to save/load instance state. |
| `--location` | `global` | Discovery Engine location (e.g. global, us, eu). Use us or eu for CMEK-enabled engines. |
| `--session_id` | None | Session ID to check status for, bypassing the state file (OnePlatform mode only). |
| `--direct_api_target` | `""` | If set, bypass IdeaForgeService.StartInstance and stubby-call EnterpriseApiService directly. |
| `--coscientist_instance_name` | None | Direct-mode equivalent of `--session_id`: a full `coscientistInstances/<id>` resource name to check status for, bypassing the state file. Requires `--direct_api_target`. |