"""NOIR CLI — main application and all command groups.

Complete terminal interface for APK decoding, analysis, modification,
rebuilding, signing, verification, and audit reporting.
"""

from __future__ import annotations

import getpass
import json
import os
import signal
import sys
import time
from pathlib import Path

import typer

from noir.cli.output import error, is_json_mode, output, progress, set_json_mode
from noir.domain.config import NoirConfig

app = typer.Typer(
    name="noir",
    help="NOIR — Local-first tool for authorized APK analysis and modification.",
    no_args_is_help=True,
    add_completion=False,
)

# ── Global options callback ──────────────────────────────────────────


@app.callback()
def main_callback(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output"),
):
    """NOIR — Authorized APK analysis and modification tool."""
    set_json_mode(json_output)
    # Handle Ctrl+C cleanly
    signal.signal(signal.SIGINT, lambda s, f: (progress("\nCancelled."), sys.exit(8)))


# ── Helper to init DB ────────────────────────────────────────────────


def _init() -> NoirConfig:
    from noir.domain.config import get_config
    from noir.infrastructure.database.engine import init_db

    config = get_config()
    config.ensure_directories()
    init_db(config.effective_database_url)
    return config


# ── init ─────────────────────────────────────────────────────────────


@app.command()
def init():
    """Initialize NOIR data directory and database."""
    config = _init()
    output(
        {"data_dir": config.data_dir, "database": config.effective_database_url},
        f"NOIR initialized. Data directory: {config.data_dir}",
    )


# ── config ───────────────────────────────────────────────────────────

config_app = typer.Typer(help="Configuration management")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show():
    """Show current configuration (secrets redacted)."""
    from noir.domain.config import get_config

    config = get_config()
    output(config.to_safe_dict())


# ── doctor ───────────────────────────────────────────────────────────


@app.command()
def doctor(
    json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Check toolchain availability and configuration."""
    if json_out:
        set_json_mode(True)
    from noir.application.doctor import run_doctor
    from noir.domain.config import get_config

    config = get_config()
    report = run_doctor(config)

    if is_json_mode():
        output(report.model_dump())
    else:
        print("\nNOIR Doctor Report")
        print(f"{'=' * 50}")
        for check in report.checks:
            status = "✅" if check.available else "❌"
            print(f"  {status} {check.name}: {check.message}")
            if check.path:
                print(f"      Path: {check.path}")
            if check.version:
                print(f"      Version: {check.version}")
        print(f"\n{report.summary}")


# ── import ───────────────────────────────────────────────────────────


@app.command("import")
def import_apk(
    apk_path: str = typer.Argument(..., help="Path to APK file"),
    authorized: bool = typer.Option(False, "--authorized", help="Acknowledge authorization"),
):
    """Import and decode an APK."""
    config = _init()
    from noir.application.import_service import ApkImportError, ImportService

    svc = ImportService(config)
    try:
        result = svc.import_apk(apk_path, authorized=authorized)
        output(result, f"Project {result['project_id']} created: {result['package_name']}")
    except ApkImportError as e:
        error(str(e))
        raise typer.Exit(code=1) from None


# ── projects ─────────────────────────────────────────────────────────

projects_app = typer.Typer(help="Project management")
app.add_typer(projects_app, name="projects")


@projects_app.command("list")
def projects_list():
    """List all projects."""
    _init()
    from noir.infrastructure.database.repositories import ProjectRepository

    projects = ProjectRepository().list_all()
    data = [
        {
            "id": p.id,
            "package": p.package_name,
            "version": p.version_name,
            "status": p.status.value,
            "created": p.created_at.isoformat() if p.created_at else "",
        }
        for p in projects
    ]
    if is_json_mode():
        output(data)
    else:
        if not data:
            print("No projects found.")
        else:
            for p in data:
                print(f"  {p['id']}  {p['package']}  {p['version']}  [{p['status']}]")


@projects_app.command("show")
def projects_show(project_id: str = typer.Argument(..., help="Project ID")):
    """Show project details."""
    _init()
    from noir.infrastructure.database.repositories import ProjectRepository

    project = ProjectRepository().get(project_id)
    if not project:
        error(f"Project not found: {project_id}")
        raise typer.Exit(code=1)
    output(project.model_dump(mode="json"))


@projects_app.command("path")
def projects_path(project_id: str = typer.Argument(..., help="Project ID")):
    """Show project workspace path."""
    config = _init()
    from noir.infrastructure.filesystem.workspace import ProjectWorkspace

    ws = ProjectWorkspace(project_id, config)
    output({"decoded_path": str(ws.decoded_dir)}, str(ws.decoded_dir))


# ── analyze ──────────────────────────────────────────────────────────


@app.command()
def analyze(project_id: str = typer.Argument(..., help="Project ID")):
    """Run static analysis on a project."""
    config = _init()
    from noir.analysis.analyzer import AnalysisService
    from noir.infrastructure.filesystem.workspace import ProjectWorkspace

    svc = AnalysisService(config)
    ws = ProjectWorkspace(project_id, config)
    result = svc.analyze(project_id, ws)
    output(result.model_dump(mode="json"))


# ── files ────────────────────────────────────────────────────────────

files_app = typer.Typer(help="File inspection")
app.add_typer(files_app, name="files")


@files_app.command("list")
def files_list(
    project_id: str = typer.Argument(..., help="Project ID"),
    subdir: str = typer.Option("", help="Subdirectory filter"),
):
    """List files in decoded workspace."""
    config = _init()
    from noir.application.file_service import FileService

    files = FileService(config).list_files(project_id, subdir)
    output(files)


@files_app.command("read")
def files_read(
    project_id: str = typer.Argument(..., help="Project ID"),
    path: str = typer.Argument(..., help="Relative file path"),
):
    """Read file content from decoded workspace."""
    config = _init()
    from noir.application.file_service import FileService

    try:
        content = FileService(config).read_file(project_id, path)
        if is_json_mode():
            output({"path": path, "content": content})
        else:
            print(content)
    except (FileNotFoundError, ValueError) as e:
        error(str(e))
        raise typer.Exit(code=1) from None


@files_app.command("search")
def files_search(
    project_id: str = typer.Argument(..., help="Project ID"),
    query: str = typer.Argument(..., help="Search query"),
):
    """Search for text in decoded workspace."""
    config = _init()
    from noir.application.file_service import FileService

    results = FileService(config).search(project_id, query)
    output(results)


# ── plan ─────────────────────────────────────────────────────────────

plan_app = typer.Typer(help="Change plans")
app.add_typer(plan_app, name="plan")


@plan_app.command("create")
def plan_create(
    project_id: str = typer.Argument(..., help="Project ID"),
    request_file: str = typer.Option(..., "--request-file", help="File containing change request"),
    allow_ai_upload: bool = typer.Option(
        False, "--allow-ai-upload", help="Consent to send data to AI"
    ),
):
    """Create a change plan using AI."""
    config = _init()
    if not allow_ai_upload:
        error("You must use --allow-ai-upload to consent to sending APK data to the AI provider.")
        raise typer.Exit(code=4)

    from noir.application.ai_service import generate_plan

    plan = generate_plan(config, project_id, Path(request_file).read_text(), allow_ai_upload)

    plan_data = plan.model_dump(mode="json")
    plan_data["plan_hash"] = plan.compute_hash()
    output(plan_data)

    if not is_json_mode():
        print(f"\nPlan ID: {plan.plan_id}")
        print(f"Hash:    {plan.compute_hash()}")
        print(
            f"\nTo approve: noir plan approve {project_id} {plan.plan_id} "
            f"--hash {plan.compute_hash()}"
        )


@plan_app.command("show")
def plan_show(
    project_id: str = typer.Argument(..., help="Project ID"),
    plan_id: str = typer.Argument(..., help="Plan ID"),
):
    """Show a change plan."""
    config = _init()
    from noir.application.patch_service import PlanService

    plan = PlanService(config).get_plan(plan_id)
    if not plan:
        error(f"Plan not found: {plan_id}")
        raise typer.Exit(code=1)
    data = plan.model_dump(mode="json")
    data["plan_hash"] = plan.compute_hash()
    output(data)


@plan_app.command("approve")
def plan_approve(
    project_id: str = typer.Argument(..., help="Project ID"),
    plan_id: str = typer.Argument(..., help="Plan ID"),
    hash: str = typer.Option(..., "--hash", help="Expected plan hash"),
):
    """Approve a change plan."""
    config = _init()
    from noir.application.patch_service import PlanService, PlanServiceError

    try:
        approval = PlanService(config).approve_plan(project_id, plan_id, hash)
        output(
            approval.model_dump(mode="json"),
            f"Plan approved. Approval ID: {approval.approval_id}",
        )
    except PlanServiceError as e:
        error(str(e))
        raise typer.Exit(code=3) from None


@plan_app.command("reject")
def plan_reject(
    project_id: str = typer.Argument(..., help="Project ID"),
    plan_id: str = typer.Argument(..., help="Plan ID"),
):
    """Reject a change plan."""
    config = _init()
    from noir.application.patch_service import PlanService

    PlanService(config).reject_plan(project_id, plan_id)
    output({"rejected": plan_id}, f"Plan {plan_id} rejected.")


# ── patch ────────────────────────────────────────────────────────────

patch_app = typer.Typer(help="Patch operations")
app.add_typer(patch_app, name="patch")


@patch_app.command("generate")
def patch_generate(
    project_id: str = typer.Argument(..., help="Project ID"),
    plan: str = typer.Option(..., "--plan", help="Approved plan ID"),
):
    """Generate a patch from an approved plan."""
    config = _init()
    from noir.application.ai_service import generate_patch
    from noir.application.patch_service import PlanServiceError
    from noir.infrastructure.ai.gemini import GeminiProviderError
    from noir.infrastructure.filesystem.workspace import WorkspaceError
    from noir.patches.engine import PatchError

    try:
        patch = generate_patch(config, project_id, plan)
    except (GeminiProviderError, PlanServiceError, WorkspaceError, PatchError, ValueError) as exc:
        error(str(exc))
        raise typer.Exit(code=2) from None

    patch_data = patch.model_dump(mode="json")
    patch_data["patch_hash"] = patch.compute_hash()
    output(patch_data)

    if not is_json_mode():
        print(f"\nPatch ID: {patch.patch_id}")
        print(f"Hash:     {patch.compute_hash()}")
        print(
            f"\nTo approve: noir patch approve {project_id} {patch.patch_id} "
            f"--hash {patch.compute_hash()}"
        )


@patch_app.command("show")
def patch_show(
    project_id: str = typer.Argument(..., help="Project ID"),
    patch_id: str = typer.Argument(..., help="Patch ID"),
):
    """Show a patch set."""
    config = _init()
    from noir.application.patch_service import PatchService

    svc = PatchService(config)
    diff = svc.show_diff(project_id, patch_id)
    output(diff)


@patch_app.command("approve")
def patch_approve(
    project_id: str = typer.Argument(..., help="Project ID"),
    patch_id: str = typer.Argument(..., help="Patch ID"),
    hash: str = typer.Option(..., "--hash", help="Expected patch hash"),
):
    """Approve a patch for application."""
    config = _init()
    from noir.application.patch_service import PatchService, PlanServiceError

    try:
        approval = PatchService(config).approve_patch(project_id, patch_id, hash)
        output(
            approval.model_dump(mode="json"),
            f"Patch approved. Approval ID: {approval.approval_id}",
        )
    except PlanServiceError as e:
        error(str(e))
        raise typer.Exit(code=3) from None


@patch_app.command("apply")
def patch_apply(
    project_id: str = typer.Argument(..., help="Project ID"),
    patch_id: str = typer.Argument(..., help="Patch ID"),
):
    """Apply an approved patch."""
    config = _init()
    from noir.application.patch_service import PatchService, PlanServiceError

    try:
        result = PatchService(config).apply_patch(project_id, patch_id)
        output(result, f"Patch applied: {result.get('operations_applied', 0)} operations")
    except PlanServiceError as e:
        error(str(e))
        raise typer.Exit(code=3) from None


@patch_app.command("undo")
def patch_undo(
    project_id: str = typer.Argument(..., help="Project ID"),
    patch_id: str = typer.Argument(..., help="Patch ID"),
):
    """Undo a previously applied patch."""
    config = _init()
    from noir.application.patch_service import PatchService, PlanServiceError

    try:
        result = PatchService(config).undo_patch(project_id, patch_id)
        output(result, "Patch undone.")
    except PlanServiceError as e:
        error(str(e))
        raise typer.Exit(code=7) from None


# ── manual ───────────────────────────────────────────────────────────

manual_app = typer.Typer(help="Manual editing")
app.add_typer(manual_app, name="manual")


@manual_app.command("begin")
def manual_begin(project_id: str = typer.Argument(..., help="Project ID")):
    """Start a manual edit session."""
    config = _init()
    from noir.application.manual_service import ManualEditError, ManualService

    try:
        session = ManualService(config).begin_session(project_id)
        from noir.infrastructure.filesystem.workspace import ProjectWorkspace

        ws = ProjectWorkspace(project_id, config)
        output(
            {"session_id": session.session_id, "workspace_path": str(ws.decoded_dir)},
            f"Manual edit session started.\nWorkspace: {ws.decoded_dir}\n"
            f"Edit files, then run: noir manual record {project_id}",
        )
    except ManualEditError as e:
        error(str(e))
        raise typer.Exit(code=7) from None


@manual_app.command("record")
def manual_record(
    project_id: str = typer.Argument(..., help="Project ID"),
    message: str = typer.Option("", "--message", "-m", help="Change description"),
):
    """Record changes from a manual edit session."""
    config = _init()
    from noir.application.manual_service import ManualEditError, ManualService

    try:
        result = ManualService(config).record_changes(project_id, message)
        output(result)
    except ManualEditError as e:
        error(str(e))
        raise typer.Exit(code=1) from None


@manual_app.command("replace")
def manual_replace(
    project_id: str = typer.Argument(..., help="Project ID"),
    path: str = typer.Argument(..., help="Relative path in workspace"),
    from_file: str = typer.Option(..., "--from", help="Source file to copy from"),
):
    """Replace a file in the workspace from a local file."""
    config = _init()
    from noir.application.manual_service import ManualEditError, ManualService

    try:
        result = ManualService(config).replace_file(project_id, path, from_file)
        output(result, f"Replaced {path}")
    except ManualEditError as e:
        error(str(e))
        raise typer.Exit(code=1) from None


# ── diff ─────────────────────────────────────────────────────────────


@app.command()
def diff(project_id: str = typer.Argument(..., help="Project ID")):
    """Show workspace changes since last recorded revision."""
    config = _init()
    from noir.infrastructure.database.repositories import FileManifestRepository
    from noir.infrastructure.filesystem.workspace import ProjectWorkspace

    ws = ProjectWorkspace(project_id, config)
    baseline = FileManifestRepository().get_latest(project_id) or []
    changes = ws.detect_changes(baseline)
    output(changes)


# ── validate ─────────────────────────────────────────────────────────


@app.command()
def validate(project_id: str = typer.Argument(..., help="Project ID")):
    """Run workspace validation."""
    config = _init()
    from noir.validation.workspace_validator import ValidationService

    result = ValidationService(config).validate(project_id)
    data = result.model_dump(mode="json")
    output(data)
    if not result.passed:
        print(f"\n❌ Validation failed with {result.error_count} error(s)")
        raise typer.Exit(code=5)


# ── build ────────────────────────────────────────────────────────────


@app.command("build")
def build_default(
    project_id: str = typer.Argument(..., help="Project ID"),
):
    """Build APK from decoded workspace."""
    config = _init()
    from noir.application.build_service import BuildService, BuildServiceError

    try:
        build = BuildService(config).build(project_id)
        output(
            build.model_dump(mode="json"),
            f"Build {build.build_id}: {'✅ success' if build.success else '❌ failed'}",
        )
    except BuildServiceError as e:
        error(str(e))
        raise typer.Exit(code=6) from None


@app.command("build-diagnose")
def build_diagnose(
    project_id: str = typer.Argument(..., help="Project ID"),
    build_id: str = typer.Argument(..., help="Build ID"),
    allow_ai_upload: bool = typer.Option(False, "--allow-ai-upload", help="Use AI for diagnosis"),
):
    """Diagnose a build failure."""
    config = _init()
    from noir.application.build_service import BuildService

    result = BuildService(config).diagnose_failure(project_id, build_id, allow_ai=allow_ai_upload)
    output(result)


# ── keys ─────────────────────────────────────────────────────────────

keys_app = typer.Typer(help="Signing key management")
app.add_typer(keys_app, name="keys")


@keys_app.command("create-profile")
def keys_create(name: str = typer.Argument(..., help="Profile name")):
    """Create a new signing profile with generated keys."""
    config = _init()
    from noir.application.signing_service import SigningService, SigningServiceError

    try:
        profile = SigningService(config).create_debug_profile(name)
        output(
            profile.model_dump(mode="json"),
            f"Signing profile '{name}' created.\n"
            f"Fingerprint: {profile.certificate_fingerprint_sha256}",
        )
    except SigningServiceError as e:
        error(str(e))
        raise typer.Exit(code=6) from None


@keys_app.command("add-profile")
def keys_add(
    name: str = typer.Argument(..., help="Profile name"),
    keystore: str = typer.Option(..., "--keystore", help="Path to keystore file"),
    alias: str = typer.Option(..., "--alias", help="Key alias"),
):
    """Register a user-supplied keystore."""
    config = _init()
    from noir.application.signing_service import SigningService, SigningServiceError

    try:
        password = os.environ.get("NOIR_KEYSTORE_PASSWORD") or getpass.getpass(
            "Keystore password: "
        )
        profile = SigningService(config).add_user_profile(name, keystore, alias, password=password)
        output(profile.model_dump(mode="json"), f"Profile '{name}' registered.")
    except SigningServiceError as e:
        error(str(e))
        raise typer.Exit(code=1) from None


@keys_app.command("list")
def keys_list():
    """List signing profiles."""
    config = _init()
    from noir.application.signing_service import SigningService

    profiles = SigningService(config).list_profiles()
    data = [
        {
            "name": p.name,
            "type": p.profile_type.value,
            "fingerprint": p.certificate_fingerprint_sha256 or "N/A",
        }
        for p in profiles
    ]
    output(data)


# ── sign ─────────────────────────────────────────────────────────────


@app.command()
def sign(
    project_id: str = typer.Argument(..., help="Project ID"),
    build: str = typer.Option(..., "--build", help="Build ID"),
    profile: str = typer.Option(..., "--profile", help="Signing profile name"),
    confirm: bool = typer.Option(False, "--confirm", help="Confirm signing"),
):
    """Sign a built APK."""
    if not confirm:
        error("Signing requires --confirm flag.")
        raise typer.Exit(code=3)

    config = _init()
    from noir.application.signing_service import SigningService, SigningServiceError

    try:
        svc = SigningService(config)
        selected = svc.get_profile(profile)
        password = os.environ.get("NOIR_KEYSTORE_PASSWORD")
        if selected and selected.profile_type.value == "user_supplied" and not password:
            import keyring

            password = keyring.get_password(f"noir:{Path(config.data_dir).resolve()}", profile)
            if not password:
                password = getpass.getpass("Keystore password: ")
        result = svc.sign(project_id, build, profile, password=password, confirmed=confirm)
        output(
            result.model_dump(mode="json"),
            f"APK signed. Hash: {result.signed_apk_hash}",
        )
    except SigningServiceError as e:
        error(str(e))
        raise typer.Exit(code=6) from None


# ── verify ───────────────────────────────────────────────────────────


@app.command()
def verify(
    project_id: str = typer.Argument(..., help="Project ID"),
    build: str = typer.Option(..., "--build", help="Build ID"),
):
    """Verify APK signature."""
    config = _init()
    from noir.infrastructure.database.repositories import BuildRepository

    build_obj = BuildRepository().get(build)
    if not build_obj or build_obj.project_id != project_id or not build_obj.signed_apk_path:
        error("No signed APK found for this build.")
        raise typer.Exit(code=1)

    from noir.infrastructure.android_tools.tools import verify_signature

    result = verify_signature(config, Path(build_obj.signed_apk_path))
    output(result)
    if not result["verified"]:
        raise typer.Exit(code=6)


# ── devices ──────────────────────────────────────────────────────────

devices_app = typer.Typer(help="Device operations")
app.add_typer(devices_app, name="devices")


@devices_app.command("list")
def devices_list():
    """List connected devices."""
    config = _init()
    from noir.infrastructure.adb.adapter import AdbError, DeviceService

    try:
        devices = DeviceService(config).list_devices()
        output(devices)
    except AdbError as e:
        error(str(e))
        raise typer.Exit(code=2) from None


@devices_app.command("packages")
def devices_packages(serial: str = typer.Option(..., "--serial", help="Device serial")):
    """List packages on a device."""
    config = _init()
    from noir.infrastructure.adb.adapter import AdbError, DeviceService

    try:
        packages = DeviceService(config).list_packages(serial)
        output(packages)
    except AdbError as e:
        error(str(e))
        raise typer.Exit(code=6) from None


# ── import-device ────────────────────────────────────────────────────


@app.command("import-device")
def import_device(
    package: str = typer.Argument(..., help="Package name"),
    serial: str = typer.Option(..., "--serial", help="Device serial"),
    authorized: bool = typer.Option(False, "--authorized", help="Authorization acknowledgment"),
):
    """Import an APK from a connected device."""
    if not authorized:
        error("Use --authorized to acknowledge you have the right to access this package.")
        raise typer.Exit(code=1)

    config = _init()
    import tempfile

    from noir.infrastructure.adb.adapter import AdbError, DeviceService

    try:
        tmp = Path(tempfile.mkdtemp(prefix="noir_pull_"))
        ds = DeviceService(config)
        pull_result = ds.import_from_device(package, serial, tmp)

        if not pull_result.get("repackaging_supported", True):
            output(pull_result)
            if not is_json_mode():
                print(f"\n⚠️ {pull_result.get('warning', 'Split APK detected')}")
            return

        # Import the pulled APK
        apk_files = pull_result["files"]
        if apk_files:
            from noir.application.import_service import ImportService

            svc = ImportService(config)
            result = svc.import_apk(apk_files[0], authorized=True)
            output(result)
    except (AdbError, Exception) as e:
        error(str(e))
        raise typer.Exit(code=6) from None


# ── install ──────────────────────────────────────────────────────────


@app.command()
def install(
    project_id: str = typer.Argument(..., help="Project ID"),
    build: str = typer.Option(..., "--build", help="Build ID"),
    serial: str = typer.Option(..., "--serial", help="Device serial"),
    confirm: bool = typer.Option(False, "--confirm", help="Confirm installation"),
):
    """Install a signed APK on a device."""
    if not confirm:
        error("Installation requires --confirm flag.")
        raise typer.Exit(code=3)

    config = _init()
    from noir.infrastructure.adb.adapter import AdbError, DeviceService
    from noir.infrastructure.database.repositories import BuildRepository

    build_obj = BuildRepository().get(build)
    if not build_obj or build_obj.project_id != project_id or not build_obj.signed_apk_path:
        error("No signed APK found.")
        raise typer.Exit(code=1)

    try:
        result = DeviceService(config).install_apk(serial, Path(build_obj.signed_apk_path))
        output(result)
        if not result.get("success"):
            raise typer.Exit(code=6)
    except AdbError as e:
        error(str(e))
        raise typer.Exit(code=6) from None


# ── smoke-test ───────────────────────────────────────────────────────


@app.command("smoke-test")
def smoke_test(
    project_id: str = typer.Argument(..., help="Project ID"),
    build: str = typer.Option(..., "--build", help="Build ID"),
    serial: str = typer.Option(..., "--serial", help="Device serial"),
    confirm: bool = typer.Option(False, "--confirm", help="Confirm smoke test"),
):
    """Run a basic smoke test on a device."""
    if not confirm:
        error("Smoke test requires --confirm flag.")
        raise typer.Exit(code=3)

    config = _init()
    from noir.analysis.analyzer import AnalysisService
    from noir.infrastructure.adb.adapter import AdbError, DeviceService
    from noir.infrastructure.database.repositories import BuildRepository, ProjectRepository

    build_obj = BuildRepository().get(build)
    if not build_obj or build_obj.project_id != project_id or not build_obj.signed_apk_path:
        error("No signed APK found.")
        raise typer.Exit(code=1)

    ProjectRepository().get(project_id)
    analysis = AnalysisService(config).get_analysis(project_id)

    launcher = ""
    if analysis:
        for comp in analysis.components:
            if comp.is_launcher:
                launcher = f"{analysis.package_name}/{comp.name}"
                break

    if not launcher:
        error("No launcher activity found.")
        raise typer.Exit(code=1)

    try:
        result = DeviceService(config).smoke_test(
            serial,
            Path(build_obj.signed_apk_path),
            analysis.package_name if analysis else "",
            launcher,
        )
        output(result)
    except AdbError as e:
        error(str(e))
        raise typer.Exit(code=6) from None


# ── audit ────────────────────────────────────────────────────────────


@app.command()
def audit(
    project_id: str = typer.Argument(..., help="Project ID"),
    format: str = typer.Option("markdown", "--format", help="Output format: json or markdown"),
):
    """Generate an audit report."""
    config = _init()
    from noir.auditing.reporter import AuditReporter

    reporter = AuditReporter(config)

    if format == "json":
        print(reporter.generate_json(project_id))
    else:
        print(reporter.generate_markdown(project_id))


# ── export ───────────────────────────────────────────────────────────


@app.command()
def export(
    project_id: str = typer.Argument(..., help="Project ID"),
    build: str = typer.Option(..., "--build", help="Build ID"),
    output_dir: str = typer.Option(..., "--output", help="Output directory"),
):
    """Export signed APK and reports."""
    config = _init()
    from noir.application.export_service import ExportService, ExportServiceError

    try:
        result = ExportService(config).export(project_id, build, output_dir)
        output(result)
    except ExportServiceError as e:
        error(str(e))
        raise typer.Exit(code=1) from None


# ── jobs ─────────────────────────────────────────────────────────────

jobs_app = typer.Typer(help="Job management")
app.add_typer(jobs_app, name="jobs")


@jobs_app.command("list")
def jobs_list():
    """List all jobs."""
    _init()
    from noir.infrastructure.database.repositories import JobRepository

    jobs = JobRepository().list_all()
    data = [
        {"id": j.job_id, "project": j.project_id, "stage": j.stage.value, "state": j.state.value}
        for j in jobs
    ]
    output(data)


@jobs_app.command("show")
def jobs_show(job_id: str = typer.Argument(..., help="Job ID")):
    """Show job details."""
    _init()
    from noir.infrastructure.database.repositories import JobRepository

    job = JobRepository().get(job_id)
    if not job:
        error(f"Job not found: {job_id}")
        raise typer.Exit(code=1)
    output(job.model_dump(mode="json"))


@jobs_app.command("logs")
def jobs_logs(
    job_id: str = typer.Argument(..., help="Job ID"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
):
    """Show job event logs."""
    _init()
    from noir.application.jobs import TERMINAL
    from noir.infrastructure.database.repositories import EventRepository, JobRepository

    seen = set()
    while True:
        job = JobRepository().get(job_id)
        if not job:
            error("Job not found")
            raise typer.Exit(code=1)
        events = EventRepository().list_by_job(job_id)
        for event in events:
            if event.event_id not in seen:
                output(
                    {
                        "event_id": event.event_id,
                        "message": event.message,
                        "time": event.timestamp.isoformat(),
                    }
                )
                seen.add(event.event_id)
        if not follow or job.state in TERMINAL:
            return
        time.sleep(0.2)


@jobs_app.command("cancel")
def jobs_cancel(job_id: str = typer.Argument(..., help="Job ID")):
    """Cancel a running job."""
    _init()
    from noir.infrastructure.database.repositories import JobRepository

    job = JobRepository().get(job_id)
    if not job:
        error(f"Job not found: {job_id}")
        raise typer.Exit(code=1)
    JobRepository().request_cancel(job_id)
    output({"cancelled": job_id}, f"Cancellation requested for job {job_id}.")


# ── api ──────────────────────────────────────────────────────────────

users_app = typer.Typer(help="Private workspace invitations (trusted server/local administration)")
app.add_typer(users_app, name="users")


@users_app.command("invite")
def users_invite(
    name: str = typer.Argument(..., help="Recipient's display name"),
    user_id: str | None = typer.Option(
        None, "--user", help="Invite another device to an existing workspace"
    ),
    hours: int = typer.Option(168, min=1, max=720, help="One-time invitation expiry"),
    code_only: bool = typer.Option(False, "--code-only", help="Print only the shareable code"),
):
    """Create one private workspace invite; share the code only with its recipient."""
    _init()
    from noir.application.access_service import AccessService

    invitation = AccessService().invite(name, user_id=user_id, hours=hours)
    if code_only:
        typer.echo(invitation["invite_code"])
    else:
        output(invitation)


@users_app.command("list")
def users_list():
    """List identities without displaying tokens or invite codes."""
    _init()
    from noir.application.access_service import AccessService

    output({"users": AccessService().list_users()})


@users_app.command("revoke")
def users_revoke(user_id: str):
    """Disable a user's sessions and unused invites without deleting their builds."""
    _init()
    from noir.application.access_service import AccessService

    AccessService().revoke_user(user_id)
    output({"revoked": user_id})


api_app = typer.Typer(help="API management")
app.add_typer(api_app, name="api")


@api_app.command("token")
def api_token_create():
    """Create a new API token."""
    _init()
    import hashlib
    import secrets

    from noir.domain.models import ApiToken
    from noir.infrastructure.database.repositories import TokenRepository

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    token = ApiToken(token_hash=token_hash)
    TokenRepository().create(token)

    # Only show the raw token once
    output(
        {"token": raw_token, "token_id": token.token_id},
        f"API Token (save this — it won't be shown again):\n  {raw_token}",
    )


@api_app.command("serve")
def api_serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    port: int = typer.Option(8787, "--port", help="Port number"),
):
    """Start the HTTP API server."""
    if host not in ("127.0.0.1", "::1", "localhost"):
        error("Non-loopback serving is disabled; use an authenticated TLS proxy or USB forwarding.")
        raise typer.Exit(code=4)
    _init()
    import uvicorn

    progress(f"Starting NOIR API server on {host}:{port}")
    progress("API bound to loopback only. Use ADB reverse for phone client development.")
    uvicorn.run("noir.api.app:create_app", host=host, port=port, factory=True)


# ── demo ─────────────────────────────────────────────────────────────


@app.command()
def demo(
    offline: bool = typer.Option(False, "--offline", help="Run offline demo (no AI)"),
):
    """Run the real toolchain with an owned fixture and a fixed, non-AI test patch."""
    if not offline:
        error("Use noir demo --offline for the real toolchain test; noir run for Gemini.")
        raise typer.Exit(code=2)
    config = _init()
    from noir.application.demo import run_offline_demo

    try:
        run_offline_demo(config)
    except Exception as e:
        error(str(e))
        raise typer.Exit(code=9) from None


# ── run ──────────────────────────────────────────────────────────────


@app.command()
def run(
    apk_path: str | None = typer.Argument(None, help="Path to APK file (omit with --project)"),
    authorized: bool = typer.Option(False, "--authorized", help="Authorization acknowledgment"),
    request_file: str | None = typer.Option(None, "--request-file", help="Change request file"),
    allow_ai_upload: bool = typer.Option(False, "--allow-ai-upload", help="AI upload consent"),
    signing_profile: str | None = typer.Option(None, "--signing-profile", help="Signing profile"),
    existing_project: str | None = typer.Option(
        None, "--project", help="Use an imported workspace; start a new plan without decoding again"
    ),
):
    """End-to-end interactive workflow.

    Automatically advances through import, decode, analyze, plan, patch,
    validate, build, align, sign, verify, and report.
    Pauses at approval gates.
    """
    config = _init()
    from noir.application.build_service import BuildService
    from noir.application.import_service import ImportService
    from noir.application.signing_service import SigningService
    from noir.auditing.reporter import AuditReporter
    from noir.validation.workspace_validator import ValidationService

    if is_json_mode():
        error(
            "Interactive run requires a terminal; "
            "use individual hash-approved commands for automation."
        )
        raise typer.Exit(code=3)
    if (apk_path is None) == (existing_project is None):
        error("Provide either an APK path or --project PROJECT_ID, not both.")
        raise typer.Exit(code=2)
    if not authorized:
        error("Use --authorized to acknowledge your right to modify this APK.")
        raise typer.Exit(code=4)
    if request_file is not None and not allow_ai_upload:
        error("A change request requires --allow-ai-upload. No APK was imported.")
        raise typer.Exit(code=4)
    project_id = None
    try:
        if request_file is not None:
            from noir.infrastructure.ai.factory import create_ai_provider

            create_ai_provider(config)
        if existing_project is not None:
            from noir.infrastructure.database.repositories import ProjectRepository
            from noir.security.locking import require_clean_workspace

            project = ProjectRepository().get(existing_project)
            if not project or not project.authorization_acknowledged:
                raise ValueError("Authorized imported project not found")
            require_clean_workspace(config, existing_project)
            project_id = existing_project
            progress(f"Using existing project: {project_id}")
        elif apk_path is not None:
            result = ImportService(config).import_apk(apk_path, authorized=authorized)
            project_id = result["project_id"]
            progress(f"Imported project: {project_id}")
        if project_id is None:
            raise ValueError("No project was selected")
        if request_file is not None:
            from noir.application.ai_service import generate_patch, generate_plan
            from noir.application.patch_service import PatchService, PlanService

            plan = generate_plan(config, project_id, Path(request_file).read_text(), True)
            print(plan.model_dump_json(indent=2))
            plan_hash = plan.compute_hash()
            progress(f"Plan hash: {plan_hash}")
            if input("Approve this exact plan? [y/N]: ").strip().lower() != "y":
                PlanService(config).reject_plan(project_id, plan.plan_id)
                raise typer.Exit(code=3)
            PlanService(config).approve_plan(project_id, plan.plan_id, plan_hash)
            patch = generate_patch(config, project_id, plan.plan_id)
            svc = PatchService(config)
            print(json.dumps(svc.show_diff(project_id, patch.patch_id), indent=2))
            progress(f"Patch hash: {patch.compute_hash()}")
            if input("Approve this exact patch? [y/N]: ").strip().lower() != "y":
                raise typer.Exit(code=3)
            svc.approve_patch(project_id, patch.patch_id, patch.compute_hash())
            svc.apply_patch(project_id, patch.patch_id)
        validation = ValidationService(config).validate(project_id)
        if not validation.passed:
            error(f"Validation failed: {validation.error_count} errors. Rebuild blocked.")
            raise typer.Exit(code=5)
        build = BuildService(config).build(project_id)
        progress(f"Build ID: {build.build_id}")
        if signing_profile:
            progress(f"Unsigned SHA-256: {build.unsigned_apk_hash}")
            if input(f"Sign this build with '{signing_profile}'? [y/N]: ").strip().lower() != "y":
                raise typer.Exit(code=3)
            svc = SigningService(config)
            profile = svc.get_profile(signing_profile)
            password = os.environ.get("NOIR_KEYSTORE_PASSWORD")
            if profile and profile.profile_type.value == "user_supplied" and not password:
                password = getpass.getpass("Keystore password: ")
            signed = svc.sign(
                project_id, build.build_id, signing_profile, password=password, confirmed=True
            )
            progress(f"Verified signed APK: {signed.signed_apk_path}")
        else:
            progress("Unsigned build only; signing was not requested.")
    except typer.Exit:
        raise
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=9) from exc
    finally:
        if project_id:
            paths = AuditReporter(config).save_reports(project_id)
            progress(f"Audit report: {paths['markdown']}")
    progress(f"Workflow completed for {project_id}")


ai_app = typer.Typer(help="Real AI connectivity checks")
app.add_typer(ai_app, name="ai")


@ai_app.command("check")
def ai_check():
    """Make a small real AI-provider request (no APK contents)."""
    from noir.domain.config import get_config
    from noir.infrastructure.ai.factory import create_ai_provider
    from noir.infrastructure.ai.gemini import GeminiProviderError

    try:
        provider = create_ai_provider(get_config())
        result = provider._parse_json_response(
            provider._call_model(
                '{"status":"ok"}',
                "Return exactly the requested connectivity JSON and nothing else.",
                response_schema={
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["ok"]}},
                    "required": ["status"],
                    "additionalProperties": False,
                },
            )
        )
        if result.get("status") != "ok":
            raise GeminiProviderError("Unexpected Gemini connectivity response")
        output(
            {
                "provider": provider.provider_name,
                "model": provider.last_model_name,
                "primary_model": provider.model_name,
                "fallback_used": provider.last_model_name != provider.model_name,
                "connected": True,
            }
        )
    except GeminiProviderError as exc:
        error(str(exc))
        raise typer.Exit(code=2) from exc


# ── Entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
