"""Unit tests for guaranteed entrypoint & launcher context prioritization."""

from pathlib import Path
import pytest
from noir.domain.config import NoirConfig
from noir.domain.models import AnalysisResult, ComponentInfo, SmaliClassInfo
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


def test_multidex_launcher_activity_guaranteed_rank0(tmp_path):
    """Ensure launcher in secondary DEX is prioritized (Rank 0) and never dropped by 150 KB cap."""
    cfg = NoirConfig(data_dir=str(tmp_path))
    ws = ProjectWorkspace("test_proj", cfg)
    ws.decoded_dir.mkdir(parents=True, exist_ok=True)

    # Create AndroidManifest.xml specifying launcher activity
    manifest = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.app">
    <application android:name=".MyApplication">
        <activity android:name="com.example.app.MainActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""
    (ws.decoded_dir / "AndroidManifest.xml").write_text(manifest)

    # Put launcher in secondary DEX: smali_classes2/
    launcher_dir = ws.decoded_dir / "smali_classes2" / "com" / "example" / "app"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher_file = launcher_dir / "MainActivity.smali"
    launcher_file.write_text(".class public Lcom/example/app/MainActivity;\n.super Landroid/app/Activity;\n")

    # Put 5,000 noise files in primary DEX smali/ that far exceed 150 KB budget
    noise_dir = ws.decoded_dir / "smali" / "com" / "noise"
    noise_dir.mkdir(parents=True, exist_ok=True)
    all_paths = ["AndroidManifest.xml", "smali_classes2/com/example/app/MainActivity.smali"]
    for i in range(5000):
        noise_path = f"smali/com/noise/Class{i:05d}.smali"
        all_paths.append(noise_path)

    # Monkeypatch _workspace_paths to avoid creating 5000 real files on disk
    tools = AiContextTools(ws)
    tools._workspace_paths = lambda subdir="": all_paths

    inventory = tools.list_project_files("", user_request="fix network timeout")

    # Verify MainActivity.smali is present in bounded inventory
    assert "smali_classes2/com/example/app/MainActivity.smali" in inventory
    # Verify it is ranked at index 1 (right after AndroidManifest.xml)
    assert inventory.index("AndroidManifest.xml") == 0
    assert inventory.index("smali_classes2/com/example/app/MainActivity.smali") == 1


def test_vpn_apk_multidex_structure_prioritizes_mainactivity(tmp_path):
    """Test against ProtonVPN vpn.apk structure: MainActivity in smali_classes2 is never dropped."""
    cfg = NoirConfig(data_dir=str(tmp_path))
    ws = ProjectWorkspace("vpn_test", cfg)
    ws.decoded_dir.mkdir(parents=True, exist_ok=True)

    # vpn.apk has MainActivity at com/protonvpn/android/redesign/app/ui/MainActivity
    vpn_launcher_path = "smali_classes2/com/protonvpn/android/redesign/app/ui/MainActivity.smali"

    # Analysis result reflecting vpn.apk metadata
    analysis = AnalysisResult(
        project_id=ws.project_id,
        package_name="ch.protonvpn.android",
        components=[
            ComponentInfo(
                name="com.protonvpn.android.redesign.app.ui.MainActivity",
                component_type="activity",
                is_launcher=True,
            )
        ],
        smali_classes=[
            SmaliClassInfo(
                descriptor="Lcom/protonvpn/android/redesign/app/ui/MainActivity;",
                file_path=vpn_launcher_path,
            )
        ],
    )

    tools = AiContextTools(ws, analysis)

    # Simulate 8,000 files in DEX 1
    simulated_files = [
        "AndroidManifest.xml",
        *[f"smali/org/bouncycastle/crypto/digest/SHA{i:04d}.smali" for i in range(4000)],
        vpn_launcher_path,
        *[f"smali/com/google/android/gms/internal/Zz{i:04d}.smali" for i in range(4000)],
    ]
    tools._workspace_paths = lambda subdir="": simulated_files

    # Request unrelated to 'main' or 'activity'
    inventory = tools.list_project_files("", user_request="disable analytics logging")

    # Guaranteed priority: MainActivity must be preserved
    assert vpn_launcher_path in inventory
    # And must be in the top 3 items
    assert inventory.index(vpn_launcher_path) < 3
