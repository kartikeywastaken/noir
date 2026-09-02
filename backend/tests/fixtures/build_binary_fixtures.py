"""Build NOIR's tiny, synthetic managed/native/IL2CPP test fixtures.

This script deliberately contains no application-derived code or metadata.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build_mono() -> None:
    dotnet = os.environ.get("NOIR_FIXTURE_DOTNET", "dotnet")
    intermediate = Path(tempfile.gettempdir()) / "noir-fixture-obj"
    output = Path(tempfile.gettempdir()) / "noir-fixture-output"
    environment = os.environ.copy()
    environment.setdefault("DOTNET_CLI_HOME", str(Path(tempfile.gettempdir()) / "noir-dotnet-home"))
    environment.setdefault("NUGET_PACKAGES", str(Path(tempfile.gettempdir()) / "noir-nuget"))
    subprocess.run(
        [
            dotnet,
            "build",
            str(ROOT / "mono" / "Fixture.csproj"),
            "--configuration",
            "Release",
            "--no-incremental",
            "--output",
            str(output),
            f"--property:BaseIntermediateOutputPath={intermediate}{os.sep}",
        ],
        check=True,
        env=environment,
    )
    shutil.copy2(output / "Assembly-CSharp.dll", ROOT / "mono" / "Assembly-CSharp.dll")


def build_native() -> None:
    environment = os.environ.copy()
    environment.setdefault(
        "ZIG_GLOBAL_CACHE_DIR", str(Path(tempfile.gettempdir()) / "noir-zig-global")
    )
    environment.setdefault(
        "ZIG_LOCAL_CACHE_DIR", str(Path(tempfile.gettempdir()) / "noir-zig-local")
    )
    for target, source, output in (
        ("x86_64-linux-musl", "fixture-x86_64.S", "libfixture.so"),
        ("aarch64-linux-musl", "fixture-arm64.S", "libfixture-arm64.so"),
    ):
        subprocess.run(
            [
                os.environ.get("NOIR_FIXTURE_ZIG", "zig"),
                "cc",
                "-target",
                target,
                "-shared",
                "-nostdlib",
                "-Wl,--build-id=none",
                str(ROOT / "native" / source),
                "-o",
                str(ROOT / "native" / output),
            ],
            check=True,
            env=environment,
        )


def build_il2cpp_metadata() -> None:
    strings = b"Game\0Economy\0CurrencyManager\0CanAfford\0System.Boolean\0System.Int32\0"
    header = bytearray(32)
    struct.pack_into("<II", header, 0, 0xFAB11BAF, 29)
    struct.pack_into("<II", header, 24, len(header), len(strings))
    (ROOT / "il2cpp").mkdir(exist_ok=True)
    (ROOT / "il2cpp" / "global-metadata.dat").write_bytes(bytes(header) + strings)


if __name__ == "__main__":
    build_mono()
    build_native()
    build_il2cpp_metadata()
