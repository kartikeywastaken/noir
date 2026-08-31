#!/usr/bin/env bash
# Run as root on the dedicated Ubuntu 24.04 NOIR instance.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
apt-get -o DPkg::Lock::Timeout=180 update
apt-get -o DPkg::Lock::Timeout=180 install -y --no-install-recommends \
  python3.12-venv openjdk-21-jdk-headless curl unzip zip git ca-certificates \
  gnupg debian-keyring debian-archive-keyring apt-transport-https dotnet-sdk-8.0

if ! id noir >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/noir --shell /usr/sbin/nologin noir
fi
install -d -m 755 /opt/noir /opt/noir/tools /opt/noir/deployment
install -d -o noir -g noir -m 700 /var/lib/noir /var/lib/noir/data /var/lib/noir/verification
install -d -m 700 /etc/noir /etc/credstore.encrypted

# Download the official SDK tools, pinned and SHA-256 checked.
sdk_archive=/home/ubuntu/noir-deploy/commandlinetools.zip
curl --fail --location --retry 3 --connect-timeout 20 --max-time 900 \
  https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip \
  --output "$sdk_archive"
printf '%s  %s\n' 4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583 "$sdk_archive" | sha256sum -c -
install -d -m 755 /opt/noir/android-sdk/cmdline-tools
unzip -q -o "$sdk_archive" -d /opt/noir/android-sdk/cmdline-tools
if [ ! -d /opt/noir/android-sdk/cmdline-tools/latest ]; then
  mv /opt/noir/android-sdk/cmdline-tools/cmdline-tools /opt/noir/android-sdk/cmdline-tools/latest
fi
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
# Setup is authorized to install the SDK required by this existing project.
set +o pipefail
yes | /opt/noir/android-sdk/cmdline-tools/latest/bin/sdkmanager \
  --sdk_root=/opt/noir/android-sdk --licenses
license_status=${PIPESTATUS[1]}
set -o pipefail
test "$license_status" -eq 0
/opt/noir/android-sdk/cmdline-tools/latest/bin/sdkmanager \
  --sdk_root=/opt/noir/android-sdk 'build-tools;36.0.0' 'platforms;android-36'

# The supplied jar is the exact known-working Homebrew Apktool installation.
install -m 644 /home/ubuntu/noir-deploy/apktool_3.0.3.jar /opt/noir/tools/apktool_3.0.3.jar
cd /home/ubuntu/noir-deploy
sha256sum -c apktool.sha256
tar -xzf backend.tar.gz -C /opt/noir
dotnet build /opt/noir/tools/noir-cil-tool/noir-cil-tool.csproj \
  --configuration Release
python3.12 -m venv /opt/noir/venv
/opt/noir/venv/bin/python -m pip install --disable-pip-version-check --upgrade pip
/opt/noir/venv/bin/python -m pip install --disable-pip-version-check -e '/opt/noir/backend[dev]'

# Official Caddy packages include the maintained systemd unit and ACME client.
curl --fail --silent --show-error --location --retry 3 \
  https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
  --output /home/ubuntu/noir-deploy/caddy-key.asc
gpg --batch --yes --dearmor --output /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
  /home/ubuntu/noir-deploy/caddy-key.asc
curl --fail --silent --show-error --location --retry 3 \
  https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
  --output /etc/apt/sources.list.d/caddy-stable.list
chmod 644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
apt-get -o DPkg::Lock::Timeout=180 update
apt-get -o DPkg::Lock::Timeout=180 install -y caddy
printf 'NOIR bootstrap completed\n'
