#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
build_dir="$root/build"
classes_dir="$build_dir/classes"
output_dir="$build_dir/dex"

if [[ -n "${ANDROID_JAR:-}" ]]; then
    android_jar="$ANDROID_JAR"
else
    sdk_root="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}}"
    android_jar=""
    if [[ -d "$sdk_root/platforms" ]]; then
        android_jar="$(find "$sdk_root/platforms" -mindepth 2 -maxdepth 2 -name android.jar -print | sort -V | tail -n 1)"
    fi
fi

if [[ ! -f "$android_jar" ]]; then
    echo "Android platform android.jar not found; set ANDROID_JAR or ANDROID_SDK_ROOT" >&2
    exit 1
fi

if command -v d8 >/dev/null 2>&1; then
    d8_bin="$(command -v d8)"
else
    sdk_root="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}}"
    d8_bin=""
    if [[ -d "$sdk_root/build-tools" ]]; then
        d8_bin="$(find "$sdk_root/build-tools" -mindepth 2 -maxdepth 2 -name d8 -type f -print | sort -V | tail -n 1)"
    fi
fi

if [[ ! -x "$d8_bin" ]]; then
    echo "Android d8 not found; install Android build-tools or add d8 to PATH" >&2
    exit 1
fi

mkdir -p "$classes_dir" "$output_dir" "$root/dist"
find "$classes_dir" -type f -delete
find "$output_dir" -type f -delete
sources=()
while IFS= read -r source; do
    sources+=("$source")
done < <(find "$root/src" -name '*.java' -print)
javac --release 8 -classpath "$android_jar" -d "$classes_dir" "${sources[@]}"
classes=()
while IFS= read -r class_file; do
    classes+=("$class_file")
done < <(find "$classes_dir" -name '*.class' -print)
"$d8_bin" --min-api 14 --lib "$android_jar" --output "$output_dir" "${classes[@]}"
cp "$output_dir/classes.dex" "$root/dist/noir-runtime-v1.dex"
(cd "$root/dist" && shasum -a 256 noir-runtime-v1.dex > noir-runtime-v1.dex.sha256)
