# Deployment verification — 2026-08-28

Endpoint: `https://noir-16-171-197-228.sslip.io`

## Three-step workflow update — 2026-08-29

- Backend source/database backup: `/var/backups/noir/20260828T201717Z`. The updated
  service is healthy; existing users, invitations, signing keys and builds were preserved.
- Database inspection showed the two reported invitations were unused, unrevoked and
  unexpired; redeeming a different invitation had not invalidated them. A live follow-up
  activated three distinct temporary invitations independently. Those test users were
  disabled afterward without touching existing users or invitations.
- A real cloud workflow imported the NOIR-owned fixture, prepared a Gemini plan and exact
  patch preview, survived an intentional client disconnect, and proved the preview could
  not apply before explicit combined approval. Cross-workspace reads/approval were denied.
- After approval, validation/rebuild/personal-key signing/signature verification/download
  and private History passed. Independent `aapt2` inspection found label `NOIR Three Step`,
  package `com.noir.testfixture`; independent `apksigner` verification passed v1/v2/v3.
- Measured fixture timings: import 2.59 s; queued preparation submission 0.30 s;
  AI plan/patch preparation 26.81 s; apply/validate/rebuild/sign/report 4.64 s.
- Backend: 153 passed, 1 skipped with real Android E2E enabled; normal suite 150 passed,
  4 skipped. Flutter: 33 passed, 1 opt-in integration test skipped; analysis clean.
- Transfer tests cover exact upload bytes/remaining, known and unknown download lengths,
  truncated downloads, and account changes during download. Workflow tests cover no hidden
  preview approval, exact-hash confirmation, foreign-workspace denial, cancellation before
  execution and signing retry without rebuilding or applying a patch twice.
- Android app `0.3.0+3` passed release build, release lint, 16 KiB-aware zip alignment and
  independent signature inspection. APK SHA-256:
  `02ab210cf08dff41e2da94ddbf6c45f1ae044470d89e443b8e541dce32f7aaed`.

Observed slowness on the previously used large APK was real tool work: about 29 seconds
for decode/analysis and roughly 46–56 seconds for rebuild. A recent Gemini call also
returned incomplete/invalid JSON and required its configured retry. One worker deliberately
serializes memory-heavy work on the 4 GiB VM, so concurrent operations can queue. This update
removes duplicate analysis inside three-step preparation, caches expensive health probes,
makes AI work persistent rather than a fragile long phone request, and reports build failures
as failures. It cannot eliminate Gemini latency, model-format failures or Apktool build time.

## Private-workspace update

- The deployed API now enforces individual ownership for projects, files, plans,
  patches, builds, downloads, signing keys and jobs (including events/cancellation).
  Existing data and credentials remain in the owner's `local` workspace.
- Single-use, expiring invitations create independent device sessions. Tokens are
  hashed in the database; disabled users and revoked sessions cannot authenticate.
- Two invited users completed real import/edit/validate/rebuild/sign/download
  workflows over public HTTPS. User A used Gemini; user B used manual editing.
  Each saw only their own build history. Cross-user project, artifact, job and
  signing-key requests were rejected. Temporary test accounts were then disabled.
- Independently inspected both signed APKs on the Mac: application labels are
  `NOIR Private A` and `NOIR Private B`, package remains `com.noir.testfixture`,
  and the two APKs have different valid signing certificates.
- Server suite: **150 passed, 1 skipped**, including both real Android E2E tests.
  Local suite: **148 passed, 3 skipped** (real Android/large Gemini tests opt-in).
  Flutter: **28 passed, 1 opt-in integration test skipped**; analysis has no issues.
  The separate real Dart-client/local-backend smoke workflow also passed.
- Flutter defaults to this HTTPS server without embedding credentials. Activation,
  secure session restoration, account switching and stale-response rejection are
  covered by tests. History replaces the global Jobs screen, retaining active
  operation progress/cancellation and project-specific plan/patch history.
- Android release-mode build passed, including release lint. The private-beta APK
  is `frontend/output/noir-private-beta.apk`, version `0.2.0+2`. It intentionally
  retains the existing development signing identity for in-place test-app upgrades;
  this is not an app-store production-signing setup.
  Independent `apksigner` and 16-KiB-aware `zipalign` checks passed, and the signing
  certificate matches the previous NOIR debug APK. APK SHA-256:
  `7b032185b5892218990a189416423992d26de1c01cd0869d90312329fb37be86`.
- Pre-migration database/source backup: `/var/backups/noir/20260828T163552Z`.
  Server upgrade log: `/var/log/noir-private-upgrade.log`.
  Server test log: `/var/log/noir-private-tests.log`.
- Private local evidence: `private-workspaces-verification.json` and the two signed
  fixture APKs under `~/.noir/deployments/ec2-stockholm/`.
  `owner-invite.json` in that directory is a mode-0600, one-time invitation for the
  existing owner workspace; do not share it with other users.
- The final service restart preserved the owner's identity, private History and
  original signed APK byte-for-byte. The packaged mobile app was scanned for the
  actual owner token, invitation code and configured Gemini key; none were present.

No new upload quotas were introduced. Existing pipeline safety limits remain.
Physical-phone installation, large third-party APK testing, a full EC2 reboot and
automated off-instance backups remain outside this verification. The existing
IP-based hostname must be updated if the EC2 public IP changes.

## Initial deployment — passed

- Installed on the existing Ubuntu 24.04 x86_64 EC2 instance; no additional AWS
  compute, database, load balancer, or storage resource was created.
- 141 backend tests passed, including both opt-in real Android toolchain E2E tests.
  One separate opt-in large-Smali Gemini test was skipped. One dependency deprecation
  warning was emitted by Starlette's test client; it did not fail the suite.
- Real live Gemini plan and patch generation worked through the public HTTPS API.
- Reviewed and approved the exact plan/patch hashes through the existing API gates.
- Only `res/values/strings.xml` changed in the owned fixture: `NOIR Test` →
  `NOIR Cloud Verified`. No manifest, Smali, package or permissions edits.
- Validation, queued Apktool rebuild, cloud-profile signing, signature verification,
  SSE job events, audit generation and authenticated APK download passed.
- Independently inspected the downloaded APK on the Mac: compiled application label
  is `NOIR Cloud Verified`, package is still `com.noir.testfixture`, version is still 1.0.
- Re-decoded the final APK with Apktool; the launcher has no label override and inherits
  the application label resource, whose value is `NOIR Cloud Verified`.
- `zipalign -c -P 16 -v 4` passed. `apksigner verify --verbose --print-certs`
  independently confirmed valid v1, v2 and v3 signatures.
- Trusted HTTPS worked without disabling certificate verification. HTTP redirects to HTTPS.
- Missing/invalid bearer tokens receive HTTP 401; the private token successfully accesses the API.
- Service restart completed successfully. The same bearer token, signed project, signing
  profile and exact downloaded APK hash remained valid after restart.
- `noir` and `caddy` services are enabled at boot and active. NOIR runs as the
  unprivileged `noir` user, bound to localhost. SSH password authentication is disabled.
- Local credentials and remote encrypted credentials have mode 0600; plaintext upload
  credentials were removed after provisioning. The original laptop `.env` and signing keys
  were not modified or copied wholesale.
- Deployment Python files pass Ruff lint/format checks; shell scripts pass syntax checks.

## Initial deployment — evidence

- Cloud project: `d348b32d192847c6`
- Plan: `a865685763ff4cf8`
- Patch: `bbf8ca06162346de`
- Build: `b16c2c3d39284009`
- Signed APK SHA-256:
  `d44d527737f74cbf0ffd4150edb1c005b18f67664ed7de7275dcefceed63211c`
- Signing certificate SHA-256:
  `9835cd012ddc9c7f1330294b9852b0333ff132fe657ad77f196ccc578b799275`
- Local evidence directory: `~/.noir/deployments/ec2-stockholm/`.
  Contains the private connection file, saved plan/diff, live workflow JSON, SSE event logs,
  audit report, signed APK and re-decoded verification output.
- Server tests: `/var/log/noir-tests.log`.

The live patch operation took approximately 146 seconds. The server had around 3 GiB
available RAM and negligible CPU load during the slow request; no VM capacity issue was
observed. This test does not guarantee Gemini response times or third-party availability.

## Initial deployment — scope (before the private-workspace update above)

- No physical-phone install was performed during deployment.
- No large third-party APK was uploaded or modified as part of these deployment tests.
- No full EC2 reboot was performed; service restart and boot-enable configuration were checked.
- Laptop projects were not migrated; this cloud data directory is separate.
- Flutter UI, backend core functionality and pre-existing unrelated worktree changes were left alone.
- No automated off-instance backup or multi-user isolation was added.

Use `README.md` for connection details, operations, key replacement, and cost/IP caveats.
