"""Real plan-to-signed-APK coverage for the synthetic Mono fixture."""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

import pytest

from noir.domain.config import NoirConfig, reset_config
from noir.domain.enums import PatchOperationType, Provenance
from noir.domain.models import ChangePlan, PatchOperation, PatchSet, PlanFileChange
from noir.infrastructure.database.engine import init_db
from noir.infrastructure.filesystem.workspace import (
    ProjectWorkspace,
    compute_file_hash,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REPOSITORY = Path(__file__).resolve().parents[3]
CIL_TOOL = (
    REPOSITORY
    / "tools"
    / "noir-cil-tool"
    / "bin"
    / "Release"
    / "net8.0"
    / "noir-cil-tool.dll"
)


def _dotnet() -> str:
    configured = os.environ.get("NOIR_TEST_DOTNET")
    temporary = Path("/private/tmp/noir-dotnet/dotnet")
    executable = configured or shutil.which("dotnet") or (
        str(temporary) if temporary.is_file() else ""
    )
    if not executable or not CIL_TOOL.is_file():
        pytest.skip("Built NOIR CIL tool and .NET 8 are required")
    return executable


def _mono_apk(output: Path) -> None:
    with (
        zipfile.ZipFile(FIXTURES / "noir_test_v2.apk") as source,
        zipfile.ZipFile(output, "w") as destination,
    ):
        for info in source.infolist():
            destination.writestr(info, source.read(info.filename))
        destination.write(
            FIXTURES / "mono" / "Assembly-CSharp.dll",
            "assets/bin/Data/Managed/Assembly-CSharp.dll",
        )
        destination.write(
            FIXTURES / "native" / "libfixture.so",
            "lib/x86_64/libfixture.so",
        )
        destination.write(
            FIXTURES / "native" / "libfixture.so",
            "lib/x86_64/libil2cpp.so",
        )
        destination.write(
            FIXTURES / "il2cpp" / "global-metadata.dat",
            "assets/bin/Data/il2cpp_data/Metadata/global-metadata.dat",
        )


@pytest.mark.e2e
@pytest.mark.apktool
@pytest.mark.sdk
def test_all_binary_families_plan_approve_patch_validate_rebuild_sign_verify(tmp_path):
    from noir.application.build_service import BuildService
    from noir.application.doctor import run_doctor
    from noir.application.import_service import ImportService
    from noir.application.patch_service import PatchService, PlanService
    from noir.application.signing_service import SigningService
    from noir.auditing.reporter import AuditReporter
    from noir.infrastructure.android_tools.tools import (
        verify_alignment,
        verify_signature,
    )
    from noir.infrastructure.apktool.adapter import ApkToolAdapter
    from noir.infrastructure.database.repositories import (
        AnalysisRepository,
        SigningProfileRepository,
    )
    from noir.infrastructure.dotnet.adapter import read_method_il
    from noir.infrastructure.il2cpp.metadata import Il2CppMetadata
    from noir.infrastructure.native.adapter import (
        disassemble_range,
        inspect_elf,
        range_hash,
    )

    reset_config()
    config = NoirConfig(
        _env_file=None,
        data_dir=str(tmp_path / "data"),
        ai_provider="none",
        dotnet_tool_path=_dotnet(),
        noir_cil_tool_path=str(CIL_TOOL),
    )
    config.ensure_directories()
    init_db(config.effective_database_url)
    doctor = run_doctor(config)
    if not doctor.build_capable:
        pytest.skip("APK rebuild/signing tools are unavailable")

    input_apk = tmp_path / "mono-fixture.apk"
    _mono_apk(input_apk)
    imported = ImportService(config).import_apk(input_apk, authorized=True)
    project_id = imported["project_id"]
    analysis = AnalysisRepository().get(project_id)
    assert analysis is not None and analysis.runtime == "il2cpp"
    assert "assets/bin/Data/Managed/Assembly-CSharp.dll" in analysis.managed_assemblies

    workspace = ProjectWorkspace(project_id, config)
    relative = "assets/bin/Data/Managed/Assembly-CSharp.dll"
    assembly = workspace.safe_path(relative)
    method = read_method_il(
        config,
        assembly,
        "Game.Economy.CurrencyManager",
        "System.Boolean CanAfford(System.Int32)",
    )
    native_relative = "lib/x86_64/libfixture.so"
    native_target = workspace.safe_path(native_relative)
    native_inspection = inspect_elf(native_target)
    native_symbol = next(
        item
        for item in native_inspection["symbol_details"]
        if item["name"] == "NoirNopTarget"
    )
    il2cpp_relative = "lib/x86_64/libil2cpp.so"
    il2cpp_target = workspace.safe_path(il2cpp_relative)
    metadata = workspace.safe_path(
        "assets/bin/Data/il2cpp_data/Metadata/global-metadata.dat"
    )
    il2cpp_method = Il2CppMetadata(metadata, il2cpp_target).find_method(
        "Game.Economy.CurrencyManager", "System.Boolean CanAfford(System.Int32)"
    )
    plan = PlanService(config).create_plan(
        ChangePlan(
            project_id=project_id,
            workspace_revision=0,
            user_request="Make the synthetic CanAfford fixture return true",
            intended_outcome="Change one synthetic managed method",
            file_changes=[
                PlanFileChange(
                    relative_path=relative,
                    operation=PatchOperationType.CIL_REPLACE_METHOD_BODY,
                    description="Replace only CanAfford's CIL body",
                ),
                PlanFileChange(
                    relative_path=native_relative,
                    operation=PatchOperationType.NATIVE_NOP_RANGE,
                    description="NOP one complete synthetic native function range",
                ),
                PlanFileChange(
                    relative_path=il2cpp_relative,
                    operation=PatchOperationType.IL2CPP_FORCE_RETURN,
                    description="Force one correlated synthetic IL2CPP method to return one",
                ),
            ],
            native_runtime="il2cpp",
            binary_targets=[relative, native_relative, il2cpp_relative],
            binary_risks=["Fixture-only managed and native code mutations"],
        )
    )
    PlanService(config).approve_plan(project_id, plan.plan_id, plan.compute_hash())

    patch = PatchService(config).store_patch(
        PatchSet(
            plan_id=plan.plan_id,
            project_id=project_id,
            workspace_revision=0,
            provenance=Provenance.AI_GENERATED,
            operations=[
                PatchOperation(
                    relative_path=relative,
                    operation=PatchOperationType.CIL_REPLACE_METHOD_BODY,
                    expected_preimage_hash=compute_file_hash(assembly),
                    assembly_name="Assembly-CSharp.dll",
                    type_full_name="Game.Economy.CurrencyManager",
                    method_signature="System.Boolean CanAfford(System.Int32)",
                    expected_method_il_hash=method["il_hash"],
                    new_il_source="ldc.i4.1\nret",
                    affected_scope="one synthetic method body",
                ),
                PatchOperation(
                    relative_path=native_relative,
                    operation=PatchOperationType.NATIVE_NOP_RANGE,
                    expected_preimage_hash=compute_file_hash(native_target),
                    native_offset=native_symbol["file_offset"],
                    native_length=native_symbol["size"],
                    native_abi="x86_64",
                    expected_native_bytes_hash=range_hash(
                        native_target,
                        native_symbol["file_offset"],
                        native_symbol["size"],
                    ),
                    affected_scope="one complete synthetic native function",
                ),
                PatchOperation(
                    relative_path=il2cpp_relative,
                    operation=PatchOperationType.IL2CPP_FORCE_RETURN,
                    expected_preimage_hash=compute_file_hash(il2cpp_target),
                    il2cpp_type_full_name="Game.Economy.CurrencyManager",
                    il2cpp_method_signature="System.Boolean CanAfford(System.Int32)",
                    il2cpp_return_constant=1,
                    expected_function_bytes_hash=range_hash(
                        il2cpp_target,
                        il2cpp_method.file_offset,
                        il2cpp_method.size,
                    ),
                    native_offset=il2cpp_method.file_offset,
                    native_length=il2cpp_method.size,
                    native_abi="x86_64",
                    affected_scope="one correlated synthetic IL2CPP function",
                ),
            ],
        )
    )
    PatchService(config).approve_patch(
        project_id, patch.patch_id, patch.compute_hash()
    )
    applied = PatchService(config).apply_patch(project_id, patch.patch_id)
    assert applied["validation"]["passed"] is True

    build = BuildService(config).build(project_id)
    assert build.success and build.unsigned_apk_path
    signing = SigningService(config)
    profile, password, key_path = signing.create_ephemeral_profile()
    SigningProfileRepository().create(profile)
    try:
        signed = signing.sign(
            project_id,
            build.build_id,
            profile.name,
            password=password,
            confirmed=True,
        )
    finally:
        shutil.rmtree(key_path.parent)
    signed_path = Path(signed.signed_apk_path or "")
    assert verify_alignment(config, signed_path)["aligned"] is True
    assert verify_signature(config, signed_path)["verified"] is True

    decoded = tmp_path / "verified-decoded"
    ApkToolAdapter(config).decode(signed_path, decoded)
    final_method = read_method_il(
        config,
        decoded / relative,
        "Game.Economy.CurrencyManager",
        "System.Boolean CanAfford(System.Int32)",
    )
    assert final_method["il_source"] == "ldc.i4.1\nret"

    final_native = decoded / native_relative
    final_native_instructions = disassemble_range(
        final_native,
        native_symbol["file_offset"],
        native_symbol["size"],
        abi="x86_64",
    )
    assert all(item["mnemonic"] == "nop" for item in final_native_instructions)
    final_il2cpp = decoded / il2cpp_relative
    final_il2cpp_instructions = disassemble_range(
        final_il2cpp,
        il2cpp_method.file_offset,
        il2cpp_method.size,
        abi="x86_64",
    )
    assert final_il2cpp_instructions[0]["mnemonic"] == "mov"
    assert final_il2cpp_instructions[0]["operands"].endswith(", 1")

    binary_audit = AuditReporter(config).generate(project_id)["patches"][0][
        "binary_operations"
    ]
    assert len(binary_audit) == 3
    assert all(item["whole_file_preimage_hash"] for item in binary_audit)
    assert all(item["whole_file_postimage_hash"] for item in binary_audit)
    instruction_audits = [item for item in binary_audit if item.get("abi")]
    assert all(item["before_disassembly"] for item in instruction_audits)
    assert all(item["after_disassembly"] for item in instruction_audits)
