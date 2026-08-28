# NOIR app

Flutter Android/macOS client for the existing NOIR Python backend. The selected Phantom artwork is bundled as the app logo and generated Android/adaptive/macOS launcher icons. Fonts are bundled locally with their OFL licenses. The interface uses black, white and neutral grayscale.

## What runs where

The laptop runs FastAPI, Gemini calls, Apktool, validation, rebuilds and signing. The phone runs the Flutter client. **This is not an on-device Python/Apktool port.** Nothing is installed, signed or approved automatically by the UI.

The backend and CLI remain usable independently. The app uses authenticated HTTP only; it never reads the backend database or calls Gemini directly.

## Start the backend

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

Paste that output in the app's **Config → NOIR bearer token**, not into source code. Credentials are saved in the OS keychain/keystore. Leave the token field blank to retain the saved token at the same address. A changed backend address requires explicitly providing its token.

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

The ready-to-install `output/noir-debug.apk` is the locally built and signature-verified **NOIR development app** (Android 7.0/API 24 or newer). It is not the repackaged VPN APK. The universal debug build includes multiple CPU architectures and is about 155 MB; this is not a release-size estimate.

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

## Workflow

1. Connect, select an APK and acknowledge authorization. The file streams from the native picker to the backend; decoding and analysis run as a real job. Jobs can be reopened after leaving the import dialog.
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

Existing profiles, including `vpn-rename`, are listed automatically; their secrets never enter the app. Local re-signing changes the certificate and may prevent installation over the publisher's version. NOIR never auto-uninstalls or auto-installs another app.

History reloads persisted plans/patches and their approval/revision state. After a timeout, refresh History or Jobs before retrying: a synchronous backend request may have finished even if its response was lost. The client never automatically replays a mutation.

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

Verification on this machine: 118 backend tests and 20 Flutter unit/widget tests pass; the isolated real-HTTP integration test passes separately. Flutter analysis and backend Ruff checks pass. The Android debug APK builds and passes `apksigner verify`. The macOS build remains unverified because full Xcode is missing.

## Implementation notes

- Flutter sources: `lib/features`, `lib/core`, `lib/data`; route-scoped workspace/build state avoids cross-project reuse.
- Backend review reads add persisted approval/stale/applied state. Mutation services still enforce hashes, project ownership, revisions, locks and consent.
- A compact analysis response avoids sending the entire Smali index to the phone; the original full API response remains available.
- API imports preserve the uploaded filename. Audit reports now include recorded manual sessions.
- Signing/export verifies before saving. An unsigned rebuilt APK is never labelled as signed.
- The bundled Phantom master is `assets/branding/phantom.png`. Regenerate launchers with `dart run flutter_launcher_icons`.
- Debug signing is for local development, not store distribution. Configure a private release signing identity and production transport before distributing NOIR.

Setup references: [Flutter Android setup](https://docs.flutter.dev/platform-integration/android/setup), [secure-storage platform configuration](https://pub.dev/packages/flutter_secure_storage/versions/10.3.1), [native file picker](https://pub.dev/packages/file_picker).
