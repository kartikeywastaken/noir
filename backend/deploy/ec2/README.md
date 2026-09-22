# NOIR EC2 deployment

This is deployment configuration for the existing backend, not a replacement engine.
The existing core tools remain in use; the app now also offers the queued three-step workflow.

## Connect from the NOIR app

The updated app connects to this backend by default. Invited users activate a one-time
code and then reconnect automatically. No URL or shared bearer token is distributed
with the APK. The following advanced settings are for the existing owner workspace:

- Backend URL: `https://noir-16-171-197-228.sslip.io`
- Bearer token: the `bearer_token` value in the private local file
  `~/.noir/deployments/ec2-stockholm/connection.json`.
- Signing profile: `cloud-test`.
- Tap **Save & Test Connection**. No laptop server, ADB reverse, or tunnel is needed.

The Gemini key is configured on the server. Do not enter it as the app's bearer token.
The ADK evaluation profile routes both evidence discovery and structured plan/patch
generation through Google ADK while retaining the same Gemini credentials and model.
The cloud test signer is independent of the laptop's keys. APKs signed with different
certificates normally cannot update one another; use a clean test install when necessary.

The EC2 profile currently uses Gemini 3.6 Flash as its only model because repeated live probes
on 2026-08-31 found Gemini 3.7 Flash unavailable/slow. There is no automatic model fallback;
Gemini 3.7 can be reconsidered after a successful live probe.

## What runs on the instance

- Ubuntu 24.04, Python 3.12 venv, Java 21, and .NET 8.
- The same Apktool 3.0.3 JAR used on the laptop, checked against SHA-256.
- The bundled dnlib CIL companion is built from `tools/noir-cil-tool` during deployment.
- LIEF, Capstone, and Keystone provide bounded ELF inspection/disassembly/assembly.
- Official Android SDK build-tools 36.0.0 and platform android-36.
- One NOIR worker under the unprivileged `noir` account.
- Caddy HTTPS with automatic certificate renewal and HTTP-to-HTTPS redirection.
- The API listens only on `127.0.0.1:8787`; Caddy exposes ports 80/443.
- Service credentials use systemd host-key encryption at rest, not a plaintext `.env`.
- Projects, decoded workspaces, SQLite data, and signing keys persist in
  `/var/lib/noir/data`. Immutable original APKs and verified signed APKs are also
  mirrored to the private `noir-private-artifacts-865675170355-eu-north-1` S3 bucket.
  Downloads use short-lived, owner-authorized presigned URLs; the bucket remains private.
- Android clients upload private, checksum-bound 8 MiB multipart ranges directly to
  S3 with four concurrent connections. EC2 authorizes each range and records resumable
  progress, but the APK bytes no longer take the phone-to-EC2-to-S3 detour. The existing
  EC2 proxy upload remains available for local deployments and compatibility fallback.
- The service restarts on failure and is enabled at boot. No active SSH session is required.
- Each Apktool JVM is capped at 2 GiB; the API service has a 3.4 GB memory ceiling.

This is an invite-only deployment with owner-scoped projects, jobs, build history and
signing keys. Existing records belong to the local owner; new users cannot access them.
It is not an unrestricted public APK service. ADB/device installation is intentionally
not installed or enabled on this cloud server; download APKs to the phone for installation.

## Invite a person

Run on the server (or wrap in the SSH command below):

```sh
sudo -u noir env NOIR_DATA_DIR=/var/lib/noir/data /opt/noir/venv/bin/noir users invite "Friend name" --code-only
sudo -u noir env NOIR_DATA_DIR=/var/lib/noir/data /opt/noir/venv/bin/noir users list
```

Send each person their own `invite_code`. It is single-use and expires in seven days.
Use `users invite "Friend name" --user USER_ID` for an additional device or recovery
of an existing workspace. `users revoke USER_ID` disables that user's access without
deleting their builds. A fresh invitation without `--user` creates a different workspace.
Invited workspaces get independent keys when the app first prepares signing; the
`cloud-test` profile remains visible only to the original owner.

`/v1/history` returns only the authenticated user's previous builds. All project routes
and job streams/cancellation enforce ownership before accessing data. Upload sessions and
idempotency keys are namespaced per user. Authentication codes and session tokens are stored hashed;
the existing owner's bootstrap token remains encrypted by systemd as described below.

## Three-step jobs

`POST /v1/projects/{id}/workflow/prepare` queues plan and unapplied patch preparation.
It requires `user_request`, `revision`, `allow_ai_upload: true` and an Idempotency-Key.
No approval is recorded. `POST /v1/projects/{id}/workflow/finish` accepts the reviewed
plan/patch IDs, their exact hashes, original revision and `confirm: true`. It queues
approval/application, validation, rebuild and personal-key signing/verification.
Both routes enforce workspace ownership. Existing advanced routes remain compatible.
Check `/v1/jobs/{job_id}` and events; do not hold one HTTP request open for AI generation.
Explicit finish retries reuse safe completed checkpoints. Interrupted jobs require review
and an explicit retry, never automatic mutation replay.

One worker serializes these tasks to fit the existing small VM. Heavy work can queue;
this change does not claim to make Gemini or Apktool instantaneous. Preparation shares
one static analysis between the two AI calls, and health capability probes are cached for
60 seconds to avoid repeatedly launching tool/version processes on phone resume.

## SSH, status, logs, restart

On the Mac:

```sh
ssh -o IdentitiesOnly=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -i ~/.ssh/noir-server.pem ubuntu@16.171.197.228
```

On the instance:

```sh
sudo systemctl status noir caddy --no-pager
sudo journalctl -u noir -n 100 --no-pager
sudo journalctl -u caddy -n 50 --no-pager
```

Only restart when no import/build/AI operation is active:

```sh
sudo systemctl restart noir
```

From either device or laptop, the health endpoint is public and contains no credentials:

```sh
curl --fail https://noir-16-171-197-228.sslip.io/v1/health
```

## Configuration and AI credential replacement

Non-secret server settings are in `/etc/noir/backend.env` (root-only). The initial
deployment preserves the laptop's effective Gemini model and context limits.
The discovery and generation keys are encrypted separately in
`/etc/credstore.encrypted/noir-gemini-discovery-api-key` and
`/etc/credstore.encrypted/noir-gemini-generation-api-key`.
The round-robin generation pool is encrypted as
`/etc/credstore.encrypted/noir-gemini-api-keys`; the value is a comma-separated list.
The OpenRouter discovery key is encrypted at
`/etc/credstore.encrypted/noir-openrouter-api-key`; it must never be placed in
`backend.env` or committed to Git.
Runtime credentials are read from
systemd's private credentials directory. The bearer token's database record is a SHA-256
hash; an encrypted copy lets this deployment preserve it across restarts.

To replace both Gemini keys, use this in the Ubuntu SSH shell. It prompts without echo
and does not put either key in shell history. Do this while no AI request is active.

```bash
read -rsp 'New discovery Gemini API key: ' NOIR_NEW_GEMINI_DISCOVERY_KEY
printf '\n'
read -rsp 'New plan/patch Gemini API key: ' NOIR_NEW_GEMINI_GENERATION_KEY
printf '\n'
if [ -n "$NOIR_NEW_GEMINI_DISCOVERY_KEY" ] && [ -n "$NOIR_NEW_GEMINI_GENERATION_KEY" ]; then
  printf '%s' "$NOIR_NEW_GEMINI_DISCOVERY_KEY" | sudo systemd-creds encrypt \
    --with-key=host --name=gemini-discovery-api-key - \
    /etc/credstore.encrypted/noir-gemini-discovery-api-key.new &&
  printf '%s' "$NOIR_NEW_GEMINI_GENERATION_KEY" | sudo systemd-creds encrypt \
    --with-key=host --name=gemini-generation-api-key - \
    /etc/credstore.encrypted/noir-gemini-generation-api-key.new &&
  sudo chmod 600 /etc/credstore.encrypted/noir-gemini-discovery-api-key.new &&
  sudo chmod 600 /etc/credstore.encrypted/noir-gemini-generation-api-key.new &&
  sudo mv /etc/credstore.encrypted/noir-gemini-discovery-api-key.new \
    /etc/credstore.encrypted/noir-gemini-discovery-api-key &&
  sudo mv /etc/credstore.encrypted/noir-gemini-generation-api-key.new \
    /etc/credstore.encrypted/noir-gemini-generation-api-key &&
  sudo systemctl restart noir
fi
unset NOIR_NEW_GEMINI_DISCOVERY_KEY NOIR_NEW_GEMINI_GENERATION_KEY
```

When changing the round-robin pool, encrypt the full comma-separated value under the
credential name `gemini-api-keys`, atomically replace
`/etc/credstore.encrypted/noir-gemini-api-keys`, and restart NOIR only after the new file exists.
The upgrade script checks this credential before stopping the running service.

To replace the OpenRouter key, use this in the Ubuntu SSH shell while no AI request is
active. The prompt does not echo the key or place it in shell history:

```bash
read -rsp 'New OpenRouter API key: ' NOIR_NEW_OPENROUTER_KEY
printf '\n'
if [ -n "$NOIR_NEW_OPENROUTER_KEY" ]; then
  printf '%s' "$NOIR_NEW_OPENROUTER_KEY" | sudo systemd-creds encrypt \
    --with-key=host --name=openrouter-api-key - \
    /etc/credstore.encrypted/noir-openrouter-api-key.new &&
  sudo chmod 600 /etc/credstore.encrypted/noir-openrouter-api-key.new &&
  sudo mv /etc/credstore.encrypted/noir-openrouter-api-key.new \
    /etc/credstore.encrypted/noir-openrouter-api-key &&
  sudo systemctl restart noir
fi
unset NOIR_NEW_OPENROUTER_KEY
```

The laptop's original `backend/.env` remains unchanged. Do not rerun
`prepare_credentials.py` on an existing deployment to rotate one key: it provisions
all credentials together for initial setup.

## Reproducibility and verification

Files in this directory:

- `bootstrap.sh`: OS, Python, Java, Android SDK, Apktool, Caddy installation.
- `apktool.sha256`: checksum for the uploaded known-working JAR.
- `prepare_credentials.py`: creates a private first-deployment bundle using local NOIR settings.
- `provision.py`: encrypts that bundle, removes its remote plaintext copy, installs service config.
- `service.py`: loads systemd credentials, initializes a stable API token and cloud test signer,
  then starts the unchanged API. It does not bypass plan/patch approval.
- `java-wrapper`: predictable JVM heap cap without changing the backend's subprocess runner.
- `noir.service`, `backend.env`, `Caddyfile`: service and proxy configuration.
- `smoke.py`: real HTTPS API workflow against a generated, owned test APK, never the VPN APK.

Deployment archives now contain both top-level `backend/` and `tools/` directories so the
bundled CIL tool is reproducible on the server. For an upgrade, create the private archive
from the repository root with:

```sh
git archive --format=tar.gz --output=backend-private.tar.gz HEAD backend tools
```

This intentionally archives committed source only, excluding local virtual environments,
API keys, generated build output, and ignored test data.

Local test output and connection details are kept outside Git in
`~/.noir/deployments/ec2-stockholm/`. The `plan`, `patch`, and `finish` smoke
stages create a project and make live Gemini calls; review the saved plan and diff between
stages. The `check` stage is read-only:

```sh
cd noir
backend/.venv/bin/python backend/deploy/ec2/smoke.py check
```

Server bootstrap/test logs are `/var/log/noir-bootstrap.log`, `/var/log/noir-fixture.log`,
and `/var/log/noir-tests.log`. Credentials are never intentionally printed in these logs.

## Limitations and cost

- The existing EC2 instance remains running. This is not a guarantee of free hosting;
  AWS charges or account credits apply. No extra VM, load balancer, database or paid
  domain was created by this deployment.
- The free auto-DNS hostname embeds the instance's current public IP. Stopping and
  starting EC2 can change that IP; then update DNS/hostname, Caddy configuration, and
  the app URL. A service restart does not change the IP.
- S3 protects immutable original and final APK artifacts from loss with the VM, but it is
  not a complete server backup. Before terminating this VM, back up `/var/lib/noir/data`,
  `/etc/credstore.encrypted/noir-*`, `/var/lib/systemd/credential.secret`, and `/etc/noir`
  securely. Encrypted credentials depend on the host key; copying the encrypted files alone
  to a new VM is insufficient. Keep any backup private.
- The security group remains user-managed. Restrict SSH port 22 to your own IP in AWS;
  keep 80/443 available for HTTPS and certificate renewal. Port 8787 must not be public.
- This deployment verifies the backend and APK artifacts; it does not prove arbitrary
  third-party APKs work after modification or signing, nor replace on-device testing.
