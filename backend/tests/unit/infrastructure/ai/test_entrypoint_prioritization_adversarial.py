"""Adversarial tests for multi-DEX two-digit indexes and activity-alias entrypoints."""

from pathlib import Path

from noir.analysis.smali_indexer import scan_smali_directories
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


def test_smali_directory_natural_sorting_multidex(tmp_path: Path):
    """smali_classes10, smali_classes11 must not sort before smali_classes2."""
    decoded = tmp_path / "decoded"
    decoded.mkdir()
    (decoded / "smali").mkdir()
    (decoded / "smali_classes2").mkdir()
    (decoded / "smali_classes3").mkdir()
    (decoded / "smali_classes9").mkdir()
    (decoded / "smali_classes10").mkdir()
    (decoded / "smali_classes11").mkdir()
    (decoded / "smali_classes25").mkdir()

    dirs = scan_smali_directories(decoded)
    assert dirs == [
        "smali",
        "smali_classes2",
        "smali_classes3",
        "smali_classes9",
        "smali_classes10",
        "smali_classes11",
        "smali_classes25",
    ]


def test_activity_alias_target_activity_prioritization(tmp_path: Path, monkeypatch):
    """Manifest launcher defined via <activity-alias android:targetActivity=...> is prioritized."""
    from noir.domain.config import NoirConfig

    config = NoirConfig(data_dir=str(tmp_path))
    ws = ProjectWorkspace("test_proj", config)
    ws._decoded_dir = tmp_path / "decoded"
    ws.decoded_dir.mkdir(parents=True)

    manifest_content = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.aliasapp">
    <application android:name=".AliasApplication">
        <activity android:name="com.example.aliasapp.ui.SplashGate" />
        <activity-alias
            android:name="com.example.aliasapp.LauncherAlias"
            android:targetActivity=".ui.SplashGate"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity-alias>
    </application>
</manifest>"""
    (ws.decoded_dir / "AndroidManifest.xml").write_text(manifest_content)

    # Launcher is in smali_classes10 (two-digit DEX index!)
    dex10_dir = ws.decoded_dir / "smali_classes10" / "com" / "example" / "aliasapp" / "ui"
    dex10_dir.mkdir(parents=True)
    launcher_file = dex10_dir / "SplashGate.smali"
    launcher_file.write_text(".class public Lcom/example/aliasapp/ui/SplashGate;\n")

    app_dir = ws.decoded_dir / "smali" / "com" / "example" / "aliasapp"
    app_dir.mkdir(parents=True)
    app_file = app_dir / "AliasApplication.smali"
    app_file.write_text(".class public Lcom/example/aliasapp/AliasApplication;\n")

    ctx_tools = AiContextTools(ws)
    entrypoints = ctx_tools._discover_primary_entrypoints()

    # The actual smali file SplashGate.smali must be in entrypoints
    assert "smali_classes10/com/example/aliasapp/ui/SplashGate.smali" in entrypoints

    # Under budget constraint, it must be Rank 0 and present in inventory
    inventory = ctx_tools.list_project_files(user_request="change title")
    assert "smali_classes10/com/example/aliasapp/ui/SplashGate.smali" in inventory
    assert inventory.index("smali_classes10/com/example/aliasapp/ui/SplashGate.smali") in (1, 2)
