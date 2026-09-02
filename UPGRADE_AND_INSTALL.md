# NOIR backend upgrade and Android installation

This guide covers two separate operations:

1. upgrading the NOIR backend running on the existing EC2 instance; and
2. building the Flutter Android APK and installing it over ADB.

The production Android app connects to `https://noir-16-171-197-228.sslip.io`.
The APK must never contain the Gemini API key, an owner bearer token, the EC2 SSH
key, or invitation codes.

## Requirements

- Repository checked out on the Mac.
- EC2 private key available locally. The commands below assume
  `$HOME/Desktop/noir-server.pem`.
- Flutter, Java 21, Android SDK build tools, and ADB installed.
- The EC2 instance is reachable at `16.171.197.228`.
- For ADB installation: USB debugging enabled and this Mac authorized on the phone.

## 1. Upgrade the EC2 backend

The upgrade program refuses to continue while a backend job is queued or running.
It stops the service, backs up the SQLite database and deployed source, installs the
new source, rebuilds the CIL helper, restarts NOIR, and checks local health.

### Prepare a committed deployment archive

Run from the repository root:

```sh
cd /path/to/noir
git status --short
git log -1 --oneline
git archive --format=tar.gz --output=/tmp/backend-private.tar.gz HEAD backend tools
tar -tzf /tmp/backend-private.tar.gz | head
```

`git archive` includes only committed files. If `git status` shows backend changes
that must be deployed, review and commit them first. Otherwise the server will receive
the older `HEAD` version. This method deliberately excludes ignored `.env` files,
virtual environments, generated APKs, caches, and local credentials.

### Upload and run the upgrader

```sh
scp -o IdentitiesOnly=yes \
  -i "$HOME/Desktop/noir-server.pem" \
  /tmp/backend-private.tar.gz \
  ubuntu@16.171.197.228:/home/ubuntu/noir-deploy/backend-private.tar.gz

scp -o IdentitiesOnly=yes \
  -i "$HOME/Desktop/noir-server.pem" \
  backend/deploy/ec2/upgrade.py \
  ubuntu@16.171.197.228:/home/ubuntu/noir-deploy/upgrade.py

ssh -o IdentitiesOnly=yes \
  -i "$HOME/Desktop/noir-server.pem" \
  ubuntu@16.171.197.228 \
  'sudo python3 /home/ubuntu/noir-deploy/upgrade.py'
```

Do not interrupt the command while it is backing up the database or installing the
backend. A successful run prints `Upgrade healthy` and the backup directory.

### Verify the deployment

```sh
curl --fail --show-error \
  https://noir-16-171-197-228.sslip.io/v1/health

ssh -o IdentitiesOnly=yes \
  -i "$HOME/Desktop/noir-server.pem" \
  ubuntu@16.171.197.228 \
  'systemctl is-active noir && sudo journalctl -u noir --since "10 minutes ago" --no-pager | tail -100'
```

Expected health capabilities include `import`, `build`, and `ai`. The health response
only proves that Gemini is configured; a real provider request can still fail because
of quota or provider availability.

If the upgrader fails, it intentionally leaves NOIR stopped rather than silently
running source that may not match the database. Read the reported backup path and the
service log before taking recovery action:

```sh
ssh -o IdentitiesOnly=yes \
  -i "$HOME/Desktop/noir-server.pem" \
  ubuntu@16.171.197.228 \
  'sudo journalctl -u noir -n 200 --no-pager'
```

Do not rerun initial credential provisioning during an ordinary upgrade. Gemini remains
in the server's encrypted systemd credential store, and S3 access uses the EC2 IAM role.

## 2. Build the production Android APK

Run from the repository root:

```sh
cd frontend
export PATH="/opt/homebrew/share/flutter/bin:$PATH"
export JAVA_HOME="/opt/homebrew/opt/openjdk@21"

flutter pub get
flutter analyze
flutter test
flutter build apk --release \
  --dart-define=NOIR_BACKEND_URL=https://noir-16-171-197-228.sslip.io

mkdir -p output
cp build/app/outputs/flutter-apk/app-release.apk \
  output/noir-private-beta.apk
```

The release build currently uses the Android debug signing identity so it can update
the existing private test installation without clearing its saved session. It is not an
app-store signing configuration.

Verify the resulting APK:

```sh
"$HOME/Library/Android/sdk/build-tools/36.0.0/apksigner" \
  verify --verbose frontend/output/noir-private-beta.apk
```

When already inside `frontend`, use `output/noir-private-beta.apk` instead of the path
prefixed with `frontend/`.

## 3. Connect the phone and install with ADB

On the phone:

1. Enable Developer options and USB debugging.
2. Set the USB connection to file transfer if charging-only mode does not expose ADB.
3. Unlock the phone and accept the RSA authorization dialog for this Mac.

On the Mac:

```sh
adb kill-server
adb start-server
adb devices -l
```

The device must appear with state `device`, not `unauthorized`, `offline`, or an empty
list. With exactly one connected physical phone, install the freshly built APK:

```sh
cd /path/to/noir
adb -d install -r frontend/output/noir-private-beta.apk
adb -d shell pm path app.noir.noir_app
```

With multiple devices, use the serial printed by `adb devices -l`:

```sh
adb -s DEVICE_SERIAL install -r frontend/output/noir-private-beta.apk
adb -s DEVICE_SERIAL shell pm path app.noir.noir_app
```

`-r` updates the existing NOIR installation and preserves its data when the signing
certificate matches. If Android reports `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, the installed
app was signed with a different key. Uninstalling would erase NOIR's locally saved session,
so only do that after deciding the data loss is acceptable.

The cloud APK does not need `adb reverse`. It connects directly to HTTPS on EC2.

## Optional: build a local-backend debug APK

For laptop-only backend testing:

```sh
cd /path/to/noir/frontend
export JAVA_HOME="/opt/homebrew/opt/openjdk@21"
flutter build apk --debug \
  --dart-define=NOIR_BACKEND_URL=http://127.0.0.1:8787

adb -d reverse tcp:8787 tcp:8787
adb -d install -r build/app/outputs/flutter-apk/app-debug.apk
```

Keep the laptop backend running on `127.0.0.1:8787`. Repeat `adb reverse` after reconnecting
the cable or restarting the phone.
