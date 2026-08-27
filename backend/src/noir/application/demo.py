"""Offline demo — end-to-end workflow with owned test fixture."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from noir.domain.config import NoirConfig
from noir.domain.enums import PatchOperationType, Provenance
from noir.domain.models import (
    ChangePlan,
    PatchOperation,
    PatchSet,
    PlanFileChange,
)


def _build_fixture_apk(config: NoirConfig) -> Path:
    """Build the owned test fixture APK or locate a pre-built one.

    Creates a minimal APK from a simple Android project.
    """
    # Check for pre-built fixture
    fixture_dir = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures"
    prebuilt = fixture_dir / "noir_test_v2.apk"
    if prebuilt.exists():
        return prebuilt

    # Build from source using aapt2 + d8
    print("  Building test fixture APK...")
    tmp = Path(tempfile.mkdtemp(prefix="noir_fixture_"))

    # Create AndroidManifest.xml
    manifest = tmp / "AndroidManifest.xml"
    manifest.write_text("""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.noir.testfixture"
    android:versionCode="1"
    android:versionName="1.0">

    <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="34" />

    <application
        android:label="@string/app_name"
        android:allowBackup="false">

        <activity
            android:name="com.noir.testfixture.MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

    </application>
</manifest>
""")

    # Create a minimal Java source for d8
    src_dir = tmp / "src"
    src_dir.mkdir()
    java_file = src_dir / "MainActivity.java"
    java_file.write_text("""package com.noir.testfixture;

import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;

public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        TextView tv = new TextView(this);
        tv.setText(getGreeting());
        setContentView(tv);
    }

    public String getGreeting() {
        return "NOIR_TEST_FIXTURE_OK";
    }
}
""")

    # Use aapt2 to compile and link
    aapt2 = config.resolve_tool_path("aapt2")

    # Find android.jar
    android_jar = ""
    if config.android_sdk_dir:
        platforms = Path(config.android_sdk_dir) / "platforms"
        if platforms.exists():
            for p in sorted(platforms.iterdir(), reverse=True):
                jar = p / "android.jar"
                if jar.exists():
                    android_jar = str(jar)
                    break

    if not android_jar:
        raise RuntimeError(
            "Android SDK platforms not found. Install with: sdkmanager 'platforms;android-34'"
        )

    from noir.infrastructure.processes.runner import run_tool

    resources = tmp / "res" / "values"
    resources.mkdir(parents=True)
    (resources / "strings.xml").write_text(
        '<resources><string name="app_name">NOIR Test</string></resources>'
    )
    resource_zip = tmp / "resources.zip"
    compiled = run_tool(
        [aapt2, "compile", "--dir", str(tmp / "res"), "-o", str(resource_zip)],
        timeout=30,
        tool_name="aapt2",
    )
    if compiled.exit_code != 0:
        raise RuntimeError(f"aapt2 compile failed: {compiled.stderr}")

    # Compile manifest
    compiled_dir = tmp / "compiled"
    compiled_dir.mkdir()

    # Compile Java to class files
    classes_dir = tmp / "classes"
    classes_dir.mkdir()

    javac_result = run_tool(
        [
            "javac",
            "-source",
            "11",
            "-target",
            "11",
            "-cp",
            android_jar,
            "-d",
            str(classes_dir),
            str(java_file),
        ],
        timeout=30,
        tool_name="javac",
    )

    if javac_result.exit_code != 0:
        raise RuntimeError(f"javac failed: {javac_result.stderr}")

    # Convert to DEX
    d8_path = str(Path(config.android_sdk_dir) / "build-tools" / config.build_tools_version / "d8")
    dex_dir = tmp / "dex"
    dex_dir.mkdir()

    # Find class files
    class_files = list(classes_dir.rglob("*.class"))

    d8_result = run_tool(
        [d8_path, "--output", str(dex_dir)] + [str(f) for f in class_files],
        timeout=30,
        tool_name="d8",
    )

    if d8_result.exit_code != 0:
        raise RuntimeError(f"d8 failed: {d8_result.stderr}")

    # Build APK using aapt2
    apk_path = tmp / "noir_test_v2.apk"

    # Link with aapt2
    link_result = run_tool(
        [
            aapt2,
            "link",
            "-I",
            android_jar,
            "--manifest",
            str(manifest),
            "-o",
            str(apk_path),
            "--auto-add-overlay",
            str(resource_zip),
        ],
        timeout=30,
        tool_name="aapt2",
    )

    if link_result.exit_code != 0:
        raise RuntimeError(f"aapt2 link failed: {link_result.stderr}")

    # Add DEX to APK
    import zipfile

    dex_file = dex_dir / "classes.dex"
    if dex_file.exists():
        with zipfile.ZipFile(apk_path, "a") as zf:
            zf.write(dex_file, "classes.dex")

    # Save a copy in fixtures
    fixture_dir.mkdir(parents=True, exist_ok=True)
    saved = fixture_dir / "noir_test_v2.apk"
    shutil.copy2(apk_path, saved)
    shutil.rmtree(tmp)

    return saved


def run_offline_demo(config: NoirConfig) -> dict:
    """Run the complete offline demo.

    1. Check tooling
    2. Build/locate fixture APK
    3. Import and decode
    4. Analyze
    5. Apply deterministic test modification
    6. Validate
    7. Rebuild
    8. Sign with temp key
    9. Verify
    10. Generate reports
    11. Export
    12. Print results
    """
    from noir.application.doctor import run_doctor

    print("\n🔍 NOIR Offline Demo")
    print("=" * 50)

    # Step 1: Check tooling
    print("\n1. Checking required tools...")
    doctor = run_doctor(config)
    if not doctor.all_required_available:
        print("\n❌ Missing required tools:")
        for check in doctor.checks:
            if not check.available and "required" in check.required_for:
                print(f"   - {check.name}: {check.message}")
        raise RuntimeError("Required tools missing. Run 'noir doctor' for details.")
    if not doctor.build_capable:
        raise RuntimeError("Build/signing tools missing. Run noir doctor.")

    # Step 2: Build/locate fixture
    print("\n2. Building test fixture APK...")
    try:
        fixture_apk = _build_fixture_apk(config)
        print(f"   Fixture: {fixture_apk}")
    except RuntimeError as e:
        print(f"   ⚠️ Could not build fixture: {e}")
        print("   Looking for pre-built fixture...")
        fixture_dir = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures"
        fixture_apk = fixture_dir / "noir_test_v2.apk"
        if not fixture_apk.exists():
            raise RuntimeError(
                "No test fixture available. Ensure Android SDK platforms are installed."
            ) from None

    # Step 3: Import
    print("\n3. Importing and decoding...")
    from noir.application.import_service import ImportService

    svc = ImportService(config)
    result = svc.import_apk(fixture_apk, authorized=True)
    project_id = result["project_id"]
    print(f"   Project: {project_id}")
    print(f"   Package: {result['package_name']}")
    print(f"   SHA-256: {result['sha256'][:32]}...")

    # Step 4: Analysis (already done during import)
    print("\n4. Analysis complete.")
    print(f"   Files: {result['file_count']}")

    # Step 5: Apply deterministic test modification
    print("\n5. Applying test modification...")
    from noir.application.patch_service import PatchService, PlanService
    from noir.infrastructure.filesystem.workspace import ProjectWorkspace

    ws = ProjectWorkspace(project_id, config)

    # Find the greeting method in Smali and modify it
    smali_rel = None
    for f in ws.list_files():
        if f.endswith("MainActivity.smali"):
            smali_rel = f
            break

    if smali_rel is None:
        raise RuntimeError("Test fixture MainActivity.smali was not decoded")

    plan_svc = PlanService(config)
    patch_svc = PatchService(config)

    # Create a deterministic plan
    plan = ChangePlan(
        project_id=project_id,
        workspace_revision=0,
        user_request="Change greeting from NOIR_TEST_FIXTURE_OK to NOIR_DEMO_MODIFIED",
        intended_outcome=("Display NOIR_DEMO_MODIFIED instead of NOIR_TEST_FIXTURE_OK"),
        file_changes=[
            PlanFileChange(
                relative_path=smali_rel or "smali/com/noir/testfixture/MainActivity.smali",
                operation=PatchOperationType.REPLACE_BLOCK,
                description="Replace greeting string constant",
            ),
        ],
        behavioral_changes=[
            "Display text changes from 'NOIR_TEST_FIXTURE_OK' to 'NOIR_DEMO_MODIFIED'"
        ],
        risks=["None — cosmetic text change only"],
    )
    plan = plan_svc.create_plan(plan)
    plan_hash = plan.compute_hash()
    plan_svc.approve_plan(project_id, plan.plan_id, plan_hash)
    print(f"   Plan: {plan.plan_id} (approved)")

    # Create and apply deterministic patch
    patch = PatchSet(
        plan_id=plan.plan_id,
        project_id=project_id,
        workspace_revision=0,
        provenance=Provenance.SYSTEM_GENERATED,
        operations=[
            PatchOperation(
                relative_path=smali_rel or "smali/com/noir/testfixture/MainActivity.smali",
                operation=PatchOperationType.REPLACE_BLOCK,
                match_content='"NOIR_TEST_FIXTURE_OK"',
                new_content='"NOIR_DEMO_MODIFIED"',
            ),
        ],
    )
    patch = patch_svc.store_patch(patch)
    patch_hash = patch.compute_hash()
    patch_svc.approve_patch(project_id, patch.patch_id, patch_hash)
    result = patch_svc.apply_patch(project_id, patch.patch_id)
    print(f"   Patch: {patch.patch_id} (applied)")

    # Step 6: Validate
    print("\n6. Validating workspace...")
    from noir.validation.workspace_validator import ValidationService

    val = ValidationService(config).validate(project_id)
    print(f"   Passed: {val.passed} ({val.error_count} errors, {val.warning_count} warnings)")

    if not val.passed:
        raise RuntimeError("Demo validation failed; refusing to build")

    # Step 7: Build
    print("\n7. Rebuilding APK...")
    from noir.application.build_service import BuildService

    build = BuildService(config).build(project_id)
    if not build.success:
        raise RuntimeError("Real Apktool rebuild failed")
    print(f"   Build: {build.build_id}")

    print("\n8. Signing with a real temporary test key...")
    from noir.application.signing_service import SigningService
    from noir.infrastructure.database.repositories import SigningProfileRepository

    signing_svc = SigningService(config)
    ephemeral, password, ks_path = signing_svc.create_ephemeral_profile()
    SigningProfileRepository().create(ephemeral)
    try:
        build = signing_svc.sign(
            project_id, build.build_id, ephemeral.name, password=password, confirmed=True
        )
    finally:
        shutil.rmtree(ks_path.parent)
    print(f"   Signed SHA-256: {build.signed_apk_hash}")

    print("\n9. Verifying signature, alignment, and rebuilt bytecode...")
    from noir.infrastructure.android_tools.tools import verify_alignment, verify_signature
    from noir.infrastructure.apktool.adapter import ApkToolAdapter

    signed = Path(build.signed_apk_path)
    signature = verify_signature(config, signed)
    if not signature["verified"] or not verify_alignment(config, signed)["aligned"]:
        raise RuntimeError("Signed APK verification failed")
    with tempfile.TemporaryDirectory(prefix="noir-verify-") as temporary:
        decoded = Path(temporary) / "decoded"
        ApkToolAdapter(config).decode(signed, decoded)
        content = (decoded / smali_rel).read_text()
        if '"NOIR_DEMO_MODIFIED"' not in content or '"NOIR_TEST_FIXTURE_OK"' in content:
            raise RuntimeError("Re-decoded APK does not contain the intended modification")
    print("   Real signature/alignment checks and bytecode re-decode passed")

    print("\n10. Generating human-readable and JSON audit reports...")
    from noir.auditing.reporter import AuditReporter

    paths = AuditReporter(config).save_reports(project_id)

    print("\n11. Exporting verified artifacts...")
    from noir.application.export_service import ExportService

    export_dir = Path(config.data_dir) / "exports" / project_id
    exported = ExportService(config).export(
        project_id, build.build_id, str(export_dir), overwrite=True
    )
    print("\nNOIR offline toolchain demo PASSED (no AI was called).")
    print(f"   Signed APK: {signed}")
    print(f"   Audit: {paths['markdown']}")
    print(f"   Export: {export_dir}")
    return {
        "project_id": project_id,
        "build_id": build.build_id,
        "signed_apk": str(signed),
        "reports": paths,
        "export": exported,
    }
