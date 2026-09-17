# NOIR Backend — Operations Guide

## Architecture Overview

The backend runs on EC2 (`ubuntu@16.171.197.228`) as a `systemd` service (`noir.service`). The venv lives at `/opt/noir/venv`, the source at `/opt/noir/backend`. All secrets are stored in systemd's encrypted credential store.

---

## Deploying / Updating the Backend

### 1. Package and upload your local changes

```bash
# From the repo root:
deploy_tmp=$(mktemp -d /tmp/noir-backend.XXXXXX)
git archive --format=tar.gz --output="$deploy_tmp/backend-private.tar.gz" HEAD backend
cp backend/deploy/ec2/upgrade.py "$deploy_tmp/upgrade.py"

scp -i ~/.ssh/noir-server.pem \
    "$deploy_tmp/backend-private.tar.gz" \
    "$deploy_tmp/upgrade.py" \
    ubuntu@16.171.197.228:/home/ubuntu/noir-deploy/
```

### 2. Run the upgrade script on EC2

```bash
ssh -i ~/.ssh/noir-server.pem ubuntu@16.171.197.228 \
  "sudo /opt/noir/venv/bin/python /home/ubuntu/noir-deploy/upgrade.py"
```

The script:
- Extracts the new `backend/` tree to `/opt/noir/backend`
- Reinstalls Python dependencies in the venv
- Copies the `noir.service` unit file and reloads systemd
- Restarts the `noir.service`
- Writes a timestamped backup of the previous deployment to `/opt/noir/backups/`

### 3. Verify the service is running

```bash
ssh -i ~/.ssh/noir-server.pem ubuntu@16.171.197.228 \
  "systemctl status noir.service && curl -s http://localhost:8787/v1/health"
```

---

## Clearing Existing Data

> **CAUTION: This is irreversible. All projects, plans, patches, and uploads will be deleted.**

```bash
ssh -i ~/.ssh/noir-server.pem ubuntu@16.171.197.228 "
  sudo systemctl stop noir.service
  sudo rm -rf /opt/noir/data/*
  sudo systemctl start noir.service
"
```

To clear only the SQLite database (keeps decoded workspaces):

```bash
ssh -i ~/.ssh/noir-server.pem ubuntu@16.171.197.228 "
  sudo systemctl stop noir.service
  sudo rm /opt/noir/data/noir.db
  sudo systemctl start noir.service
"
```

---

## Environment Variables / API Keys

Keys are stored in systemd's encrypted credential store on EC2. To update a key:

```bash
ssh -i ~/.ssh/noir-server.pem ubuntu@16.171.197.228 "
  sudo systemd-creds encrypt --with-key=host --name=gemini-api-key-1 - /etc/credstore.encrypted/noir-gemini-api-key-1 <<< 'YOUR_NEW_KEY'
  sudo systemctl restart noir.service
"
```

| Credential name | Purpose |
|---|---|
| `noir-gemini-api-key-1` | Gemini discovery key |
| `noir-gemini-api-key-2` | Gemini generation key |
| `noir-openrouter-api-key` | OpenRouter (Nemotron etc.) key |

To update non-secret config, edit `/opt/noir/backend/deploy/ec2/backend.env` on EC2 and restart:

```bash
ssh -i ~/.ssh/noir-server.pem ubuntu@16.171.197.228 "
  sudo nano /opt/noir/backend/deploy/ec2/backend.env
  sudo systemctl restart noir.service
"
```

---

## Smoke Testing After Deploy

```bash
# Health check
curl -s https://noir.v0id.in/v1/health | python3 -m json.tool

# Check AI connectivity (Gemini + OpenRouter)
ssh -i ~/.ssh/noir-server.pem ubuntu@16.171.197.228 "
  cd /opt/noir/backend
  sudo -u noir /opt/noir/venv/bin/noir ai check
"
```

---

## Local Development

```bash
cd backend
export $(grep -v '^#' .env | xargs)

# Start the API server
.venv/bin/uvicorn "noir.api.app:create_app" --factory --host 127.0.0.1 --port 8787 --reload

# Create a test user invite
.venv/bin/noir users invite my-name

# Redeem the invite for an auth token
curl -s -X POST http://127.0.0.1:8787/v1/auth/redeem \
  -H "Content-Type: application/json" \
  -d '{"code": "PASTE_INVITE_CODE_HERE"}'
```

---

## How the Nemotron / OpenRouter Model Pipeline Works

When the frontend sends `model: "openrouter:nvidia/nemotron-3.5-lightning:free"`:

1. `ai_service.py` → `_create_generation_provider()` strips the `openrouter:` prefix and instantiates `OpenRouterGenerationProvider("nvidia/nemotron-3.5-lightning:free", config)`.
2. `OpenRouterGenerationProvider` is a subclass of `GeminiProvider`. It **inherits** `generate_plan()` and `generate_patch()` — the same prompt building, JSON parsing, schema validation, and plan/patch object construction as Gemini.
3. It **overrides only `_call_model()`**, which posts to `https://openrouter.ai/api/v1/chat/completions` instead of the Google GenAI SDK.
4. The response schema is embedded as a textual suffix in the prompt (OpenRouter's `json_object` mode enforces JSON but not a specific schema).
5. Discovery phase uses the separate `OpenRouterDiscoveryProvider` (tool-calling loop), not the generation provider.

This means any model available on OpenRouter can be used end-to-end for APK modification.
