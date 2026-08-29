# NOIR app

Flutter Android/macOS client for the existing NOIR Python backend. The selected Phantom artwork is bundled as the app logo and generated Android/adaptive/macOS launcher icons. Fonts are bundled locally with their OFL licenses. The interface uses black, white and neutral grayscale.

## What runs where

The deployed EC2 backend runs FastAPI, Gemini calls, Apktool, validation, rebuilds and signing. The phone runs the Flutter client. **This is not an on-device Python/Apktool port.** The normal workflow combines exact plan/patch approval and signing consent into one review action. Advanced tools retain their separate approval controls.

## Three-step workflow (0.3.0)

1. **APK:** choose an authorized APK. Upload shows measured bytes, percentage and bytes
   remaining; decoding follows automatically. Upload completion is separate from processing.
2. **Changes:** describe the change, consent to Gemini context upload and select Preview
   changes. NOIR prepares an **unapproved, unapplied** plan/patch preview in a persistent job.
   Review the outcome, risks, permissions and file diffs, then select **Approve & make APK**.
   This explicitly approves both exact hashes and signing; no hidden approvals occur on upload
   or preview generation. Edit request returns to the same step without a rejection loop.
3. **Download:** validation, rebuild, alignment, signing and verification run automatically.
   Download shows real byte progress and verifies signature/SHA-256 before saving.

Navigation is **Home / History / Config**, with no Projects tab or recent-project section.
History retains builds and processing/preparation jobs; Resume reopens the three-step flow.
Manual editing and detailed tools remain under Advanced tools, not in the default path.
These are three screens/stages, not a promise that file picking, AI consent and approval
can safely happen in three total taps.

Only read-only status checks reconnect automatically. Processing has real stage labels,
not fabricated percentages. Interrupted work is not silently replayed. After an ordinary
signing failure, Retry remaining build steps reuses a successful recorded build and never
applies the same patch twice. Uploads are not resumable byte-range transfers; interrupted
uploads may require selecting the file again. Unknown download lengths show bytes without
an invented percentage. Android still controls its native save-file confirmation.

The backend and CLI remain usable independently. The app uses authenticated HTTP only; it never reads the backend database or calls Gemini directly.

## Private cloud app

The app defaults to `https://noir-16-171-197-228.sslip.io`. Invited users enter a
one-time invitation code under **Config → Activate invitation**. Their session is saved
in OS secure storage and reconnects automatically on startup/resume. The APK contains
the public server URL, not a bearer token or Gemini key.

The owner creates each invitation through SSH:

```sh
ssh -o IdentitiesOnly=yes -i /Users/kartik/Desktop/noir-server.pem ubuntu@16.171.197.228 \
  'sudo -u noir env NOIR_DATA_DIR=/var/lib/noir/data /opt/noir/venv/bin/noir users invite "Friend name" --code-only'
```

Share the resulting code privately with that person. `--code-only` avoids copying an ID
or surrounding terminal formatting. The app also accepts wrapped codes or copied invitation
JSON. Errors distinguish an incorrect code from an already-used, expired or revoked one.
Activating one invitation does not invalidate any other invitation. Codes expire after seven
days and can be used once. `noir users list` shows workspace IDs; issue another code
with `users invite "Friend name" --user USER_ID` for the same person's second device
or return after sign-out. Without `--user`, a new private workspace is created.
`users revoke USER_ID` disables all of that user's sessions and unused invitations;
it does not delete their builds. These are trusted server CLI commands, not public APIs.

Each user sees only their projects, files, jobs, plans, patches, builds, downloads and
signing profiles. Existing owner data stays in the `local` workspace. Accounts are
isolated by server-side ownership checks; filtering in Flutter is not the security boundary.
Changing accounts clears cached UI state and discards responses from the previous session.

**History** replaces Jobs in navigation. It lists previous signed/unsigned/failed builds
with timestamps, verified APK downloads, exact-build signing details and audit links.
Active operations remain visible and cancellable. Plan/patch history remains available
inside each project for approval recovery after an AI timeout. The old `/jobs` app route
redirects to History; the underlying job API is retained for real progress and cancellation.

The ready-to-install private-beta APK is `frontend/output/noir-private-beta.apk` (version 0.3.0+3).
It is a release-mode build using the existing development signing identity so it can
update the previously installed NOIR test app without erasing its saved session. It is
not an app-store release; use a private production signing identity before wider distribution.

```sh
adb -d install -r /Users/kartik/Documents/ChatGPT/noir/frontend/output/noir-private-beta.apk
```

No ADB reverse, laptop backend, or custom URL is required for cloud use. The current
hostname embeds the EC2 IP; if that IP changes, update the deployment and app origin.
Build-time origin override: `flutter build apk --release --dart-define=NOIR_BACKEND_URL=https://your-server.example`.

## Optional local backend / Advanced settings

In terminal 1:

```sh
cd /Users/kartik/Documents/ChatGPT/noir/backend
source .venv/bin/activate
export JAVA_HOME=/opt/homebrew/opt/openjdk@21
export PATH="$JAVA_HOME/bin:$PATH"
noir api serve --host 127.0.0.1 --port 8787
```

If an older server is already running on 8787, stop it in its terminal first and restart it to load the updated API. Do not run two workers against the same data directory.

Generate a NOIR bearer token once in another terminal:

```sh
cd /Users/kartik/Documents/ChatGPT/noir/backend
source .venv/bin/activate
noir api token
```

Paste that output in the app's **Config → Advanced → Custom backend / owner access**, not into source code. Credentials are saved in the OS keychain/keystore. Leave the token field blank to retain the saved token at the same address. A changed backend address requires explicitly providing its token.

The Gemini key stays in the ignored file `backend/.env`:

```dotenv
GEMINI_API_KEY=your-gemini-key
NOIR_AI_PROVIDER=gemini
NOIR_AI_MODEL=gemini-3.6-flash
```

Preserve your other settings. Restart the backend after editing the file. `noir ai check` performs a real provider check; the app's “AI configured” status only checks configuration. Do not put the Gemini key in Flutter, Dart defines, Git, or the mobile token field.

## Android phone over USB

Enable USB debugging and authorize the laptop on the phone. With one physical Android device attached:

```sh
adb -d install -r /Users/kartik/Documents/ChatGPT/noir/frontend/output/noir-debug.apk
adb -d reverse tcp:8787 tcp:8787
```

The ready-to-install `frontend/output/noir-debug.apk` is the locally built and signature-verified **NOIR development app** (Android 7.0/API 24 or newer). It is not the repackaged VPN APK. The universal debug build includes multiple CPU architectures and is about 155 MB; this is not a release-size estimate.

Use `http://127.0.0.1:8787` in the app and tap **Save & test connection**. Keep the backend terminal running. Repeat the reverse command after reconnecting USB.

Build and launch from terminal 2:

```sh
cd /Users/kartik/Documents/ChatGPT/noir/frontend
export PATH="/opt/homebrew/share/flutter/bin:$PATH"
flutter pub get
flutter devices
flutter run -d YOUR_ANDROID_DEVICE_ID
```

Or build an installable development APK:

```sh
cd /Users/kartik/Documents/ChatGPT/noir/frontend
export JAVA_HOME=/opt/homebrew/opt/openjdk@21
flutter build apk --debug
adb -d install -r build/app/outputs/flutter-apk/app-debug.apk
```

The NOIR app ID is `app.noir.noir_app`, distinct from the APKs it edits. Installing NOIR does not modify Proton VPN or your renamed APK.

Android emulator: use `http://10.0.2.2:8787`. Local HTTP exceptions are limited to loopback/emulator hosts; arbitrary remote HTTP, credential-bearing URLs and redirects are rejected. Remote deployments require HTTPS and an explicitly configured backend deployment. CORS/auth and the backend CLI's loopback binding are not relaxed.

The Android build uses the dependency lockfile. Secure storage 10.3.1 is selected for its Android SDK 36 compatibility; version 11 requires SDK 37. Android builds may download Gradle, SDK/NDK and plugin dependencies on first use.

## macOS laptop UI

A full Xcode installation and its command-line tool selection are required. Install CocoaPods if Flutter's plugin build requests it. This machine currently has only an incomplete Xcode setup; use the terminal tests below until it is configured.

```sh
cd /Users/kartik/Documents/ChatGPT/noir/frontend
export PATH="/opt/homebrew/share/flutter/bin:$PATH"
flutter doctor -v
flutter run -d macos
```

Connect to `http://127.0.0.1:8787` using the same backend token. Network-client, keychain and user-selected-file permissions are declared in the macOS entitlements. No browser build is provided.

## Advanced workflow (optional)

1. Activate an invitation (or configure a local backend under Advanced), select an APK and acknowledge authorization. The file streams from the native picker to the backend; decoding and analysis run as a real job. Progress can be reopened in History after leaving the import dialog.
2. Browse the decoded manifest, resources and Smali, or search file contents. The inventory shows SDK, permissions and compatibility warnings. These are decoded artifacts, not original Java/Kotlin source.
3. **Ask AI:** explicitly consent to context upload, describe the change, review every plan field and its full hash, then approve. Patch generation is a separate action. Review the backend's full deterministic diff, approve the exact patch hash, then apply it. Missing/failed diffs cannot be approved.
4. **Manual edit:** begin/resume a session, open a text file, edit and save to the backend. Discard only resets an unsaved buffer; it does not undo saved files. Record the session before AI/build operations. Stale saves preserve the buffer and report a conflict.
5. Validate, inspect findings, then rebuild. Progress is persisted job state and real events, never estimated percentages. Cancellation requests are distinct from worker-confirmed termination.
6. Select a successful current-revision build and local signing profile, then explicitly confirm signing. Use **Verify & save signed APK** to verify its signature and compare downloaded SHA-256 with the recorded build before the native save dialog writes the file.
7. Read/export the backend audit as Markdown or JSON. The report includes manual sessions as well as plans, approvals, validation and build history.

Create a signing profile on the laptop if needed:

```sh
noir keys create-profile local-test
```

Existing profiles are listed only in their owning workspace; their secrets never enter the app. Re-signing changes the certificate and may prevent installation over the publisher's version. NOIR never auto-uninstalls or auto-installs another app.

Project-specific plan/patch history reloads persisted approval/revision state. After an AI timeout, refresh that history before retrying: a synchronous backend request may have finished even if its response was lost. Global History shows builds and running operations. The client never automatically replays a mutation.

Installed-application extraction remains a backend CLI/device feature. This client imports APK files through the native picker; it does not request broad installed-package visibility. Split APK installation is not added.

## Tests without a phone

```sh
cd /Users/kartik/Documents/ChatGPT/noir/frontend
export PATH="/opt/homebrew/share/flutter/bin:$PATH"
flutter analyze
flutter test
```

The unit/widget tests use explicit test-only HTTP doubles. There are no simulated runtime providers, projects, patches, jobs or progress.

Run the real Dart client against an isolated local backend and the owned small fixture APK:

```sh
cd /Users/kartik/Documents/ChatGPT/noir
export JAVA_HOME=/opt/homebrew/opt/openjdk@21
export PATH="$JAVA_HOME/bin:/opt/homebrew/share/flutter/bin:$PATH"
backend/.venv/bin/python backend/scripts/flutter_smoke.py
```

This exercises real upload, Apktool decode, manual changes, revision checks, validation, rebuild, hash-checked download and audit over HTTP. It uses a temporary database and random in-memory test token; it does not use your normal backend, VPN project, signing profiles or Gemini key. The corresponding Dart test is skipped in the default suite unless this harness supplies its isolated configuration.

Full backend tests, including real build/sign/verify/re-decode of the owned fixture:

```sh
cd /Users/kartik/Documents/ChatGPT/noir/backend
NOIR_RUN_E2E=1 JAVA_HOME=/opt/homebrew/opt/openjdk@21 PATH=/opt/homebrew/opt/openjdk@21/bin:/opt/homebrew/bin:/usr/bin:/bin .venv/bin/pytest -q -p no:cacheprovider
```

No live Gemini request or installation on your phone is performed by these tests.

See `backend/deploy/ec2/VERIFICATION.md` for deployment evidence. The suites include owner isolation, invite replay/revocation, private history, account-switch response protection, and the real Dart HTTP integration. The macOS app build still requires a complete Xcode installation.

## Implementation notes

- Flutter sources: `frontend/lib/features`, `frontend/lib/core`, `frontend/lib/data`; route-scoped workspace/build state avoids cross-project reuse.
- Backend review reads add persisted approval/stale/applied state. Mutation services still enforce hashes, project ownership, revisions, locks and consent.
- A compact analysis response avoids sending the entire Smali index to the phone; the original full API response remains available.
- API imports preserve the uploaded filename. Audit reports now include recorded manual sessions.
- Signing/export verifies before saving. An unsigned rebuilt APK is never labelled as signed.
- The bundled Phantom master is `frontend/assets/branding/phantom.png`. Regenerate launchers from `frontend/` with `dart run flutter_launcher_icons`.
- Debug signing is for local development, not store distribution. Configure a private release signing identity and production transport before distributing NOIR.

Setup references: [Flutter Android setup](https://docs.flutter.dev/platform-integration/android/setup), [secure-storage platform configuration](https://pub.dev/packages/flutter_secure_storage/versions/10.3.1), [native file picker](https://pub.dev/packages/file_picker).
