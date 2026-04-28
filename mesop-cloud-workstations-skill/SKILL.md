---
name: mesop-cloud-workstations-skill
description: Guide for deploying and accessing Mesop UI applications within Google Cloud Workstations, including workarounds for Identity-Aware Proxy (IAP), CORS, and port binding.
---

# Mesop on Cloud Workstations

When developing and running [Mesop](https://google.github.io/mesop/) applications inside Google Cloud Workstations, you will often encounter "spinning forever" or `405 Method Not Allowed` / `403 Forbidden` errors. This happens because Cloud Workstations uses an Identity-Aware Proxy (IAP) and forwards traffic via reverse-proxied domains (e.g., `https://8080-<web-host>.cloudworkstations.dev`), which conflicts with Mesop's default security policies and CORS settings.

Use this skill when asked to deploy, run, or fix a Mesop application running on Google Cloud Workstations.

## Core Fixes Required

To make a Mesop app accessible via a Cloud Workstations preview URL, you MUST implement the following three fixes:

### 1. Bind to 0.0.0.0
The Mesop app (typically run via `gunicorn` in production or background setups) MUST bind to all interfaces (`0.0.0.0`), not just localhost (`127.0.0.1`). The Cloud Workstations proxy cannot route external traffic to localhost bindings.

**Example (Gunicorn):**
```bash
gunicorn --bind 0.0.0.0:8080 app:me
```

### 2. Disable Internal CSRF/Websockets Check
Mesop's internal security checks will reject requests coming from the `cloudworkstations.dev` domain if it expects standard localhost. You MUST pass environment variables to disable this check and force memory-based sessions.

**Required Environment Variables:**
```env
MESOP_WEBSOCKETS_ENABLED=false
MESOP_STATE_SESSION_BACKEND=memory
```
*Note: If spawning the app via Python `subprocess.Popen`, pass these explicitly in the `env` dictionary parameter rather than relying on `os.environ` before the fork.*

### 3. Bypass Origin Validation with Debug Mode
By default, Mesop enforces a strict `is_same_site` check comparing the HTTP `Origin` header against the `Host`. In Cloud Workstations, the `Host` will be `0.0.0.0:<port>` or `localhost:<port>`, while the `Origin` is the external `cloudworkstations.dev` domain. This results in a persistent `403 Forbidden` error on `POST /__ui__`.

You MUST bypass this by explicitly enabling debug mode in the script before any pages are declared.

**Required Python Code:**
```python
import mesop as me

# Bypasses strict Origin vs Host header checks
me.runtime().debug_mode = True
```

### 4. Configure Mesop SecurityPolicy
You MUST explicitly configure the Mesop `@me.page` decorator with a custom `SecurityPolicy` to allow iframe embedding and cross-origin connections from the Cloud Workstations domain. Without this, the browser will block the UI from loading or connecting to its own backend.

**Required Python Code:**
```python
import mesop as me

@me.page(
    path="/",
    security_policy=me.SecurityPolicy(
        dangerously_disable_trusted_types=True,
        allowed_iframe_parents=["https://*.cloudworkstations.dev", "https://*.google.com"],
        allowed_connect_srcs=["https://*.cloudworkstations.dev"]
    )
)
def app():
    # Your app logic here
    pass
```

## Workstation Authentication Loop (User Guidance)

Even with the code perfectly configured, the user's browser may still get stuck in an authentication redirect loop (a `302` redirect to `forwardAuthCookie`).

If a user complains that the Mesop app is "spinning forever", you MUST instruct them to do the following:

1. Close ALL Incognito/Private browser windows.
2. Open a **brand new** Incognito/Private window.
3. Paste the direct URL to the Mesop port (e.g., `https://8080-w-aburdenko...cloudworkstations.dev/`) directly into the address bar. Do not try to load it inside an iframe first.
4. Log in with their Google Account when prompted by the proxy.
5. (Optional) Ensure their browser is not strictly blocking third-party cookies for the domain.
