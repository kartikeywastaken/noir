"""Round-trip tests for NOIR's bounded managed, IL2CPP, and ELF operations."""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from noir.domain.config import NoirConfig
from noir.domain.enums import PatchOperationType, Provenance
from noir.domain.models import PatchOperation, PatchSet
from noir.infrastructure.filesystem.workspace import (
    ProjectWorkspace,
    compute_file_hash,
)
from noir.infrastructure.il2cpp.metadata import (
    IL2CPP_MAGIC,
    Il2CppMetadata,
    Il2CppMetadataError,
)
from noir.infrastructure.native.adapter import (
    NativePatchError,
    apply_byte_patch,
    disassemble_range,
    inspect_elf,
    range_hash,
)
from noir.patches.engine import PatchEngine

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
    if not executable:
        pytest.skip(".NET 8 SDK is not available for the CIL integration test")
    return executable


def _cil_config(tmp_path: Path) -> NoirConfig:
    dotnet = _dotnet()
    if not CIL_TOOL.is_file():
        subprocess.run(
            [
                dotnet,
                "build",
                str(REPOSITORY / "tools" / "noir-cil-tool" / "noir-cil-tool.csproj"),
                "--configuration",
                "Release",
            ],
            check=True,
        )
    return NoirConfig(
        _env_file=None,
        data_dir=str(tmp_path / "data"),
        dotnet_tool_path=dotnet,
        noir_cil_tool_path=str(CIL_TOOL),
    )


def _workspace(tmp_path: Path, config: NoirConfig | None = None) -> ProjectWorkspace:
    workspace = ProjectWorkspace("binarytest", config or NoirConfig(data_dir=str(tmp_path)))
    workspace.create()
    return workspace


def _patch(operation: PatchOperation) -> PatchSet:
    return PatchSet(
        patch_id="binary-patch",
        plan_id="binary-plan",
        project_id="binarytest",
        workspace_revision=0,
        provenance=Provenance.MANUAL,
        operations=[operation],
    )


def _copy_native(workspace: ProjectWorkspace) -> tuple[Path, str, dict]:
    relative = "lib/x86_64/libfixture.so"
    target = workspace.safe_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURES / "native" / "libfixture.so", target)
    return target, relative, inspect_elf(target)


def _symbol(inspection: dict, name: str) -> dict:
    return next(item for item in inspection["symbol_details"] if item["name"] == name)


def test_cil_method_round_trip_changes_only_approved_method(tmp_path):
    from noir.infrastructure.dotnet.adapter import inspect_assembly, read_method_il

    config = _cil_config(tmp_path)
    workspace = _workspace(tmp_path, config)
    relative = "assets/bin/Data/Managed/Assembly-CSharp.dll"
    target = workspace.safe_path(relative)
    target.parent.mkdir(parents=True)
    shutil.copy2(FIXTURES / "mono" / "Assembly-CSharp.dll", target)
    before = inspect_assembly(config, target)
    evidence = read_method_il(
        config,
        target,
        "Game.Economy.CurrencyManager",
        "System.Boolean CanAfford(System.Int32)",
    )
    operation = PatchOperation(
        relative_path=relative,
        operation=PatchOperationType.CIL_REPLACE_METHOD_BODY,
        expected_preimage_hash=compute_file_hash(target),
        assembly_name="Assembly-CSharp.dll",
        type_full_name="Game.Economy.CurrencyManager",
        method_signature="System.Boolean CanAfford(System.Int32)",
        expected_method_il_hash=evidence["il_hash"],
        new_il_source="ldc.i4.1\nret",
        affected_scope="one method body",
    )

    PatchEngine(workspace).apply_patch(_patch(operation))
    after = inspect_assembly(config, target)

    def hashes(inspection):
        return {
            (item["full_name"], method["signature"]): method["il_hash"]
            for item in inspection["types"]
            for method in item["methods"]
        }

    before_hashes, after_hashes = hashes(before), hashes(after)
    changed = {
        key for key in before_hashes if before_hashes[key] != after_hashes[key]
    }
    assert changed == {
        (
            "Game.Economy.CurrencyManager",
            "System.Boolean CanAfford(System.Int32)",
        )
    }


def test_cil_companion_output_is_deterministic_for_identical_input(tmp_path):
    from noir.infrastructure.dotnet.adapter import patch_assembly, read_method_il

    config = _cil_config(tmp_path)
    source = FIXTURES / "mono" / "Assembly-CSharp.dll"
    evidence = read_method_il(
        config,
        source,
        "Game.Economy.CurrencyManager",
        "System.Boolean CanAfford(System.Int32)",
    )
    payload = {
        "operation": PatchOperationType.CIL_REPLACE_METHOD_BODY.value,
        "type_full_name": "Game.Economy.CurrencyManager",
        "method_signature": "System.Boolean CanAfford(System.Int32)",
        "expected_method_il_hash": evidence["il_hash"],
        "new_il_source": "ldc.i4.1\nret",
    }
    first = tmp_path / "first.dll"
    second = tmp_path / "second.dll"

    patch_assembly(config, source, first, payload)
    patch_assembly(config, source, second, payload)

    assert first.read_bytes() == second.read_bytes()


def test_cil_method_preimage_mismatch_is_rejected(tmp_path):
    config = _cil_config(tmp_path)
    workspace = _workspace(tmp_path, config)
    relative = "assets/bin/Data/Managed/Assembly-CSharp.dll"
    target = workspace.safe_path(relative)
    target.parent.mkdir(parents=True)
    shutil.copy2(FIXTURES / "mono" / "Assembly-CSharp.dll", target)
    operation = PatchOperation(
        relative_path=relative,
        operation=PatchOperationType.CIL_REPLACE_METHOD_BODY,
        expected_preimage_hash=compute_file_hash(target),
        assembly_name="Assembly-CSharp.dll",
        type_full_name="Game.Economy.CurrencyManager",
        method_signature="System.Boolean CanAfford(System.Int32)",
        expected_method_il_hash="0" * 64,
        new_il_source="ldc.i4.1\nret",
    )
    errors = PatchEngine(workspace).validate_patch(_patch(operation))
    assert any("preimage hash mismatch" in error.lower() for error in errors)


def test_cil_insert_method_and_literal_field_round_trip(tmp_path):
    from noir.infrastructure.dotnet.adapter import inspect_assembly, read_method_il

    for index, operation_type in enumerate(
        (
            PatchOperationType.CIL_INSERT_METHOD,
            PatchOperationType.CIL_REPLACE_FIELD_INIT,
        )
    ):
        config = _cil_config(tmp_path / str(index))
        workspace = _workspace(tmp_path / str(index), config)
        relative = "assets/bin/Data/Managed/Assembly-CSharp.dll"
        target = workspace.safe_path(relative)
        target.parent.mkdir(parents=True)
        shutil.copy2(FIXTURES / "mono" / "Assembly-CSharp.dll", target)
        before = inspect_assembly(config, target)
        operation = PatchOperation(
            relative_path=relative,
            operation=operation_type,
            expected_preimage_hash=compute_file_hash(target),
            assembly_name="Assembly-CSharp.dll",
            type_full_name="Game.Economy.CurrencyManager",
            method_signature=(
                "System.Int32 NoirInserted()"
                if operation_type == PatchOperationType.CIL_INSERT_METHOD
                else None
            ),
            field_name=(
                "StartingCoins"
                if operation_type == PatchOperationType.CIL_REPLACE_FIELD_INIT
                else None
            ),
            expected_method_il_hash=(
                next(
                    field["constant_hash"]
                    for type_info in before["types"]
                    if type_info["full_name"] == "Game.Economy.CurrencyManager"
                    for field in type_info["fields"]
                    if field["name"] == "StartingCoins"
                )
                if operation_type == PatchOperationType.CIL_REPLACE_FIELD_INIT
                else None
            ),
            new_il_source=(
                "ldc.i4.s 11\nret"
                if operation_type == PatchOperationType.CIL_INSERT_METHOD
                else "42"
            ),
        )

        PatchEngine(workspace).apply_patch(_patch(operation))
        after = inspect_assembly(config, target)
        selected_type = next(
            item
            for item in after["types"]
            if item["full_name"] == "Game.Economy.CurrencyManager"
        )
        if operation_type == PatchOperationType.CIL_INSERT_METHOD:
            inserted = read_method_il(
                config,
                target,
                "Game.Economy.CurrencyManager",
                "System.Int32 NoirInserted()",
            )
            assert inserted["il_source"] == "ldc.i4.s 11\nret"
        else:
            field = next(
                item for item in selected_type["fields"] if item["name"] == "StartingCoins"
            )
            assert field["constant"] == 42


def test_il2cpp_unknown_metadata_version_refuses_to_guess(tmp_path):
    metadata = bytearray((FIXTURES / "il2cpp" / "global-metadata.dat").read_bytes())
    struct.pack_into("<II", metadata, 0, IL2CPP_MAGIC, 999)
    path = tmp_path / "global-metadata.dat"
    path.write_bytes(metadata)
    with pytest.raises(Il2CppMetadataError, match="Unsupported IL2CPP metadata version"):
        Il2CppMetadata(path, FIXTURES / "native" / "libfixture.so")


def test_il2cpp_missing_and_ambiguous_correlations_are_rejected(tmp_path, monkeypatch):
    metadata = FIXTURES / "il2cpp" / "global-metadata.dat"
    binary = FIXTURES / "native" / "libfixture.so"
    resolver = Il2CppMetadata(metadata, binary)
    with pytest.raises(Il2CppMetadataError, match="not present in the metadata"):
        resolver.find_method("Game.Economy.NoSuchType", "System.Boolean Missing()")

    from noir.infrastructure.il2cpp import metadata as metadata_module

    monkeypatch.setattr(
        metadata_module,
        "inspect_elf",
        lambda _path: {
            "exports": [
                "CurrencyManager_CanAfford",
                "CurrencyManager_CanAfford_Alternative",
            ],
            "abi": "x86_64",
        },
    )
    ambiguous = Il2CppMetadata(metadata, binary)
    with pytest.raises(Il2CppMetadataError, match=r"ambiguous \(2 symbols\)"):
        ambiguous.find_method(
            "Game.Economy.CurrencyManager",
            "System.Boolean CanAfford(System.Int32)",
        )


def test_il2cpp_force_return_is_valid_machine_code(tmp_path):
    workspace = _workspace(tmp_path)
    target, relative, _ = _copy_native(workspace)
    metadata = workspace.safe_path("assets/bin/Data/il2cpp_data/Metadata/global-metadata.dat")
    metadata.parent.mkdir(parents=True)
    shutil.copy2(FIXTURES / "il2cpp" / "global-metadata.dat", metadata)
    reference = Il2CppMetadata(metadata, target).find_method(
        "Game.Economy.CurrencyManager", "System.Boolean CanAfford(System.Int32)"
    )
    operation = PatchOperation(
        relative_path=relative.replace("libfixture.so", "libil2cpp.so"),
        operation=PatchOperationType.IL2CPP_FORCE_RETURN,
        expected_preimage_hash=compute_file_hash(target),
        il2cpp_type_full_name="Game.Economy.CurrencyManager",
        il2cpp_method_signature="System.Boolean CanAfford(System.Int32)",
        il2cpp_return_constant=1,
        expected_function_bytes_hash=range_hash(
            target, reference.file_offset, reference.size
        ),
        native_abi="x86_64",
        native_offset=reference.file_offset,
        native_length=reference.size,
    )
    il2cpp_target = workspace.safe_path(operation.relative_path)
    target.rename(il2cpp_target)
    operation.expected_preimage_hash = compute_file_hash(il2cpp_target)

    PatchEngine(workspace).apply_patch(_patch(operation))
    instructions = disassemble_range(
        il2cpp_target, reference.file_offset, reference.size, abi="x86_64"
    )
    assert instructions[0]["mnemonic"] == "mov"
    assert instructions[0]["operands"].endswith(", 1")
    assert instructions[1]["mnemonic"] == "ret"


def test_native_byte_nop_and_branch_operations_round_trip(tmp_path):
    operations = (
        (PatchOperationType.NATIVE_BYTE_PATCH, "CurrencyManager_CanAfford"),
        (PatchOperationType.NATIVE_NOP_RANGE, "NoirNopTarget"),
        (PatchOperationType.NATIVE_BRANCH_REDIRECT, "NoirBranch"),
    )
    for index, (operation_type, symbol_name) in enumerate(operations):
        workspace = _workspace(tmp_path / str(index))
        target, relative, inspection = _copy_native(workspace)
        selected = _symbol(inspection, symbol_name)
        operation = PatchOperation(
            relative_path=relative,
            operation=operation_type,
            expected_preimage_hash=compute_file_hash(target),
            native_offset=selected["file_offset"],
            native_length=selected["size"],
            native_abi="x86_64",
            expected_native_bytes_hash=range_hash(
                target, selected["file_offset"], selected["size"]
            ),
        )
        if operation_type == PatchOperationType.NATIVE_BYTE_PATCH:
            operation.native_new_bytes_hex = "b801000000c3"
        elif operation_type == PatchOperationType.NATIVE_BRANCH_REDIRECT:
            operation.native_redirect_target_offset = _symbol(
                inspection, "CurrencyManager_CanAfford"
            )["file_offset"]

        PatchEngine(workspace).apply_patch(_patch(operation))
        assert disassemble_range(
            target, selected["file_offset"], selected["size"], abi="x86_64"
        )


def test_native_wrong_length_preimage_and_invalid_postimage_are_rejected(tmp_path):
    workspace = _workspace(tmp_path)
    target, relative, inspection = _copy_native(workspace)
    selected = _symbol(inspection, "CurrencyManager_CanAfford")
    base = dict(
        relative_path=relative,
        operation=PatchOperationType.NATIVE_BYTE_PATCH,
        expected_preimage_hash=compute_file_hash(target),
        native_offset=selected["file_offset"],
        native_length=selected["size"],
        native_abi="x86_64",
        expected_native_bytes_hash=range_hash(
            target, selected["file_offset"], selected["size"]
        ),
    )
    wrong_length = PatchOperation(**base, native_new_bytes_hex="90")
    assert any(
        "exactly" in error.lower()
        for error in PatchEngine(workspace).validate_patch(_patch(wrong_length))
    )
    wrong_hash = PatchOperation(
        **{**base, "expected_native_bytes_hash": "0" * 64},
        native_new_bytes_hex="b801000000c3",
    )
    assert any(
        "range preimage hash mismatch" in error.lower()
        for error in PatchEngine(workspace).validate_patch(_patch(wrong_hash))
    )

    nop = _symbol(inspection, "NoirNopTarget")
    original = target.read_bytes()
    malformed = PatchOperation(
        relative_path=relative,
        operation=PatchOperationType.NATIVE_BYTE_PATCH,
        expected_preimage_hash=compute_file_hash(target),
        native_offset=nop["file_offset"],
        native_length=1,
        native_abi="x86_64",
        expected_native_bytes_hash=range_hash(target, nop["file_offset"], 1),
        native_new_bytes_hex="0f",
    )
    with pytest.raises(NativePatchError, match="valid instruction"):
        PatchEngine(workspace).apply_patch(_patch(malformed))
    assert target.read_bytes() == original

    with pytest.raises(NativePatchError, match="same-length"):
        apply_byte_patch(target, nop["file_offset"], b"\x90", b"\x90\x90")


def test_multi_abi_library_cannot_be_partially_patched(tmp_path):
    workspace = _workspace(tmp_path)
    target, relative, inspection = _copy_native(workspace)
    other = workspace.safe_path("lib/arm64-v8a/libfixture.so")
    other.parent.mkdir(parents=True)
    shutil.copy2(target, other)
    selected = _symbol(inspection, "NoirNopTarget")
    operation = PatchOperation(
        relative_path=relative,
        operation=PatchOperationType.NATIVE_NOP_RANGE,
        expected_preimage_hash=compute_file_hash(target),
        native_offset=selected["file_offset"],
        native_length=selected["size"],
        native_abi="x86_64",
        expected_native_bytes_hash=range_hash(
            target, selected["file_offset"], selected["size"]
        ),
    )
    errors = PatchEngine(workspace).validate_patch(_patch(operation))
    assert any("unaddressed ABIs: arm64-v8a" in error for error in errors)


def test_complete_multi_abi_patch_applies_both_architectures(tmp_path):
    workspace = _workspace(tmp_path)
    targets = []
    for abi, fixture in (
        ("x86_64", "libfixture.so"),
        ("arm64-v8a", "libfixture-arm64.so"),
    ):
        relative = f"lib/{abi}/libfixture.so"
        target = workspace.safe_path(relative)
        target.parent.mkdir(parents=True)
        shutil.copy2(FIXTURES / "native" / fixture, target)
        selected = _symbol(inspect_elf(target), "NoirNopTarget")
        targets.append((abi, relative, target, selected))
    patch = PatchSet(
        patch_id="multi-abi-patch",
        plan_id="binary-plan",
        project_id="binarytest",
        workspace_revision=0,
        provenance=Provenance.MANUAL,
        operations=[
            PatchOperation(
                relative_path=relative,
                operation=PatchOperationType.NATIVE_NOP_RANGE,
                expected_preimage_hash=compute_file_hash(target),
                native_offset=selected["file_offset"],
                native_length=selected["size"],
                native_abi=abi,
                expected_native_bytes_hash=range_hash(
                    target, selected["file_offset"], selected["size"]
                ),
            )
            for abi, relative, target, selected in targets
        ],
    )

    PatchEngine(workspace).apply_patch(patch)
    for abi, _relative, target, selected in targets:
        instructions = disassemble_range(
            target, selected["file_offset"], selected["size"], abi=abi
        )
        assert all(item["mnemonic"] == "nop" for item in instructions)


def test_native_range_hash_is_exact(tmp_path):
    target = tmp_path / "range.bin"
    target.write_bytes(b"abcdef")
    assert range_hash(target, 1, 3) == hashlib.sha256(b"bcd").hexdigest()


def test_ai_binary_context_is_structured_and_bounded(tmp_path):
    from noir.infrastructure.ai.context import AiContextTools

    workspace = _workspace(tmp_path)
    target, relative, _inspection = _copy_native(workspace)
    tools = AiContextTools(workspace)

    context = tools.build_context([relative], user_request="inspect NoirNopTarget")

    assert relative not in context["file_snippets"]
    assert context["file_coverage"][relative] == "structured_binary_inspection"
    import json

    inspection = context["binary_inspection"][relative]
    assert inspection["selected_disassembly"]
    assert len(json.dumps(inspection).encode()) <= (
        tools.MAX_BINARY_INSPECTION_BYTES
    )
    with pytest.raises(ValueError, match="256-byte context ceiling"):
        tools.disassemble_native(relative, 0, tools.MAX_DISASSEMBLY_BYTES + 1)
    assert target.is_file()


def test_manual_replace_cannot_bypass_structured_binary_operations(tmp_path):
    from noir.application.manual_service import ManualEditError, ManualService

    config = NoirConfig(_env_file=None, data_dir=str(tmp_path / "data"))
    workspace = _workspace(tmp_path, config)
    source = tmp_path / "replacement.dll"
    source.write_bytes(b"MZ\0\0synthetic-binary")
    service = ManualService(config)
    service.project_repo = SimpleNamespace(get=lambda _project_id: object())
    service.session_repo = SimpleNamespace(get_active=lambda _project_id: object())

    with pytest.raises(ManualEditError, match="Manual binary replacement is unsupported"):
        service.replace_file(
            workspace.project_id,
            "assets/bin/Data/Managed/Assembly-CSharp.dll",
            str(source),
        )
