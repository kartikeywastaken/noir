# NOIR Backend

A local Python backend and terminal interface for APKs you own or are authorized to modify.
Apktool produces decoded resources, AndroidManifest.xml, and Smali—not original Java/Kotlin source.

## Start on this laptop

The virtual environment and Python dependencies are installed in this checkout.
Homebrew Apktool and Android SDK build-tools 36.0.0 are detected.

```bash
cd /Users/kartik/Documents/ChatGPT/noir/backend
source .venv/bin/activate
export JAVA_HOME=/opt/homebrew/opt/openjdk@21
export PATH="$JAVA_HOME/bin:/opt/homebrew/bin:$PATH"
noir init
noir doctor
```

For a fresh checkout with Python 3.12+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

The native runner requires macOS/Linux, a JDK, Apktool, zipalign, apksigner, and keytool.
The fixture builder additionally needs javac, d8, aapt2, and an Android SDK platform.
ADB is optional. Windows, containers, and running this backend natively inside Android are not implemented.

## Gemini key: where to put it

Edit **backend/.env** (already created, ignored by Git, owner-readable only):

```dotenv
GEMINI_API_KEY=your_actual_key_here
NOIR_AI_PROVIDER=gemini
NOIR_AI_MODEL=gemini-3.6-flash
```

The model above is the project's configured default; your Google account must have access to it.
Change NOIR_AI_MODEL if your account needs another supported Gemini model.

Restart a running API server after editing. Every new CLI invocation reloads configuration.
The file loads automatically; no sourcing or copying the key into Python files is necessary.
An exported GEMINI_API_KEY takes precedence over the file. If an old export is overriding
your edit, run `unset GEMINI_API_KEY NOIR_GEMINI_API_KEY` in that terminal.

Check the real API connection:

```bash
noir ai check
```

This makes one small billable/quota-consuming Gemini request, without APK contents.
Missing/invalid credentials, unavailable models, quota failures, and malformed responses fail
explicitly. There is no fake provider or offline response fallback.
Only Gemini needs an external API key. Apktool, building, signing, and manual edits do not.
The HTTP bearer token is generated locally and is separate from the Gemini key.

## Real toolchain test — no AI key needed

```bash
noir demo --offline
```

This compiles a small owned Android fixture, decodes it with real Apktool, applies a fixed
test patch, validates, rebuilds, generates a real temporary signing key, aligns/signs the APK,
verifies the actual signature and alignment, then decodes the signed output again and checks
the modified Smali. It exports the APK and JSON/Markdown audit reports. Any failure exits nonzero.

**Offline means no Gemini call. The demo patch is explicitly a deterministic test change, not
AI-generated.** No tool results or signed APKs are simulated. The temporary test key is deleted;
use a persistent signing profile for your own ongoing work.

Normal data and exports live under ~/.noir. Optional isolated testing:

```bash
NOIR_DATA_DIR="$PWD/.local-test" noir demo --offline
```

Do not mix data directories between commands: projects, tokens, and signing profiles are
scoped to the selected directory.

## AI workflow on an owned APK

First add your key, run `noir ai check`, and create a persistent local signing profile:

```bash
noir keys create-profile local-test
```

The generated keystore password goes into the OS credential store (macOS Keychain).
Allow the OS access prompt if shown. There is no plaintext .pass-file fallback.
On Linux, a working keyring backend is required for generated persistent profiles.
Old profiles whose only password was in a legacy .pass file are not read automatically.

Create a plain-text request file describing the desired change. For the owned demo fixture,
the repository includes examples/change-request.txt. After running the demo once:

```bash
noir run tests/fixtures/noir_test_v2.apk \
  --authorized \
  --request-file examples/change-request.txt \
  --allow-ai-upload \
  --signing-profile local-test
```

For your own APK, replace the APK and request-file paths.
If the APK is already imported, reuse its project without decoding it again:

```bash
noir run --project PROJECT_ID --authorized \
  --request-file examples/rename-vpn.txt --allow-ai-upload \
  --signing-profile vpn-rename
```

Use an existing signing profile (do not run create-profile again). This starts a fresh
planning round at the current recorded revision; it does not approve or replay an old patch.

The program automatically decodes and analyzes, then asks Gemini for a plan.
You review/approve the full plan, review/approve the exact patch diff, and separately confirm signing.
Rejecting an approval stops the workflow. Validation errors stop rebuilding.
The terminal prints the project ID, build ID, final signed path, and audit-report path.

Export using the IDs printed above:

```bash
noir export PROJECT_ID --build BUILD_ID --output ./output
```

The AI sees bounded decoded-file context only after consent, never a signing key or unrestricted
host shell. The backend invokes Apktool deterministically; it does not let Gemini run arbitrary
commands. Context is bounded, so large/obfuscated apps and complex changes may need manual work.
Label requests prioritize the manifest and referenced app/launcher strings. Large resource
files are represented by marked, exact label excerpts; these permit block replacements only,
never whole-file replacement. The complete serialized prompt plus system instructions is
measured in UTF-8 bytes against NOIR_AI_MAX_REQUEST_SIZE (100,000 by default). Optional inventory
is trimmed first. Required patch evidence is never silently omitted or truncated: an oversized
required plan fails before contacting Gemini and must be narrowed or split.
Gemini currently performs plan/patch generation and optional diagnosis, not an autonomous
tool-calling repair loop. Rebuild errors are real failures, not auto-success responses.

## Individual commands and manual editing

```bash
noir import /absolute/path/to/owned.apk --authorized
noir projects list
noir files list PROJECT_ID

# AI plan and patch gates:
noir plan create PROJECT_ID --request-file request.txt --allow-ai-upload
noir plan approve PROJECT_ID PLAN_ID --hash PLAN_HASH
noir patch generate PROJECT_ID --plan PLAN_ID
noir patch show PROJECT_ID PATCH_ID
noir patch approve PROJECT_ID PATCH_ID --hash PATCH_HASH
noir patch apply PROJECT_ID PATCH_ID
noir patch undo PROJECT_ID PATCH_ID

# Or manual editing:
noir manual begin PROJECT_ID
# Edit files in the workspace path printed by the command.
noir manual record PROJECT_ID --message "Describe exactly what changed"

noir validate PROJECT_ID
noir build PROJECT_ID
noir sign PROJECT_ID --build BUILD_ID --profile local-test --confirm
noir verify PROJECT_ID --build BUILD_ID
noir audit PROJECT_ID --format markdown
noir export PROJECT_ID --build BUILD_ID --output ./output
```

Use `noir build-diagnose PROJECT_ID BUILD_ID` for persisted failure logs; add
`--allow-ai-upload` for Gemini diagnosis. This replaces the ambiguous old build/diagnose group.
Global JSON mode comes **before** the subcommand: `noir --json projects list`.
Interactive `run` does not support JSON mode.

Patches are staged and checked before application, bound to file hashes and an approved plan.
Undo restores retained original bytes and refuses to overwrite later edits.
Unrecorded manual edits or active manual sessions block builds and AI patch application.
Previously created legacy patches without retained backups cannot be safely undone.

## HTTP backend for a later client

No server is needed for CLI commands.

```bash
noir api token
noir api serve --host 127.0.0.1 --port 8787
```

Save the generated bearer token privately. Only its hash is stored.
Use one API worker per data directory. Non-loopback CLI serving is rejected.
Health: http://127.0.0.1:8787/v1/health
Local API schema: http://127.0.0.1:8787/docs

In another terminal, load the token without putting it in shell history:

```bash
read -r NOIR_API_TOKEN
export NOIR_API_TOKEN
curl -H "Authorization: Bearer $NOIR_API_TOKEN" \
  http://127.0.0.1:8787/v1/projects

curl -H "Authorization: Bearer $NOIR_API_TOKEN" \
  -H "Idempotency-Key: owned-apk-import-1" \
  -F "file=@/absolute/path/to/owned.apk" \
  "http://127.0.0.1:8787/v1/import?authorized=true"
```

Import and build return HTTP 202 with a persisted job_id. Poll /v1/jobs/JOB_ID or read
/v1/jobs/JOB_ID/events for SSE. POST /v1/jobs/JOB_ID/cancel requests cancellation.
Running Apktool process groups are stopped; queued jobs are not executed.
Queued imports/builds survive server restart; interrupted running tasks are marked interrupted
rather than silently replayed. AI and signing HTTP requests are synchronous.

```bash
noir jobs list
noir jobs show JOB_ID
noir jobs logs JOB_ID --follow
noir jobs cancel JOB_ID
```

For a phone connected by USB with debugging enabled, run
`/Users/kartik/Library/Android/sdk/platform-tools/adb reverse tcp:8787 tcp:8787`.
A future phone client can then use http://127.0.0.1:8787 with the local bearer token.
This keeps Python/Java/Apktool on the laptop; it does not deploy this backend onto Android.

## Tests

```bash
ruff check src tests
ruff format --check src tests
pytest -q
NOIR_RUN_E2E=1 pytest -q
```

The opt-in E2E tests run the real signed-APK pipeline and real queued HTTP import/rebuild.
Unit tests may inject boundary responses to test failure/approval handling; production does not.
Live Gemini and installation/runtime behavior on a phone require your credentials/device and
are not covered by offline tests. APK signature validity does not prove runtime correctness.

## Safety boundaries

This is a single-user native development tool, not a hardened multi-tenant sandbox.
Use only authorized APKs; signing again normally prevents updating an app signed by another key.
Archive/path checks, bounded subprocess logs, process-group cancellation, isolated framework
caches, XML entity rejection, revision checks, and project locks protect the local workflow.
Native tools still run with your OS user's privileges. Do not treat the runner as isolation
against malicious APKs. Split APK reassembly, arbitrary app compatibility, device behavior,
and production deployment are not guaranteed.
