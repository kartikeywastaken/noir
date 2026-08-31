"""Thin adapter for the bundled dnlib-based NOIR CIL companion tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from noir.domain.config import NoirConfig
from noir.infrastructure.processes.runner import run_tool


class CilToolError(Exception):
    """Raised when the managed-code helper cannot safely complete an operation."""


def _default_tool_path() -> Path:
    repository = Path(__file__).resolve().parents[5]
    return (
        repository
        / "tools"
        / "noir-cil-tool"
        / "bin"
        / "Release"
        / "net8.0"
        / "noir-cil-tool.dll"
    )


def _command(config: NoirConfig) -> list[str]:
    configured = Path(config.noir_cil_tool_path).expanduser() if config.noir_cil_tool_path else None
    tool = configured or _default_tool_path()
    if not tool.is_file():
        raise CilToolError(
            "NOIR CIL tool is not built. Run `dotnet build tools/noir-cil-tool "
            "--configuration Release` or configure NOIR_CIL_TOOL_PATH."
        )
    if tool.suffix.lower() == ".dll":
        return [config.resolve_tool_path("dotnet"), str(tool)]
    return [str(tool)]


def _invoke(
    config: NoirConfig,
    subcommand: str,
    args: list[str],
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = run_tool(
        [*_command(config), subcommand, *args],
        timeout=config.cil_patch_timeout,
        tool_name="noir-cil-tool",
        input_data=json.dumps(payload, separators=(",", ":")) if payload is not None else None,
    )
    if result.exit_code != 0:
        detail = (result.stderr or result.stdout).strip()
        raise CilToolError(f"CIL tool {subcommand} failed: {detail or 'unknown error'}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CilToolError("CIL tool returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise CilToolError("CIL tool response must be a JSON object")
    if data.get("ok") is False:
        raise CilToolError(str(data.get("error") or f"CIL tool {subcommand} failed"))
    return data


def inspect_assembly(config: NoirConfig, assembly_path: Path) -> dict[str, Any]:
    """Return bounded structural metadata and canonical method-body hashes."""
    _validate_input(config, assembly_path)
    return _invoke(config, "inspect", [str(assembly_path)])


def read_method_il(
    config: NoirConfig,
    assembly_path: Path,
    type_full_name: str,
    method_signature: str,
) -> dict[str, Any]:
    """Read one unambiguously selected method body as canonical CIL text."""
    _validate_input(config, assembly_path)
    return _invoke(
        config,
        "read-il",
        [str(assembly_path), type_full_name, method_signature],
    )


def patch_assembly(
    config: NoirConfig,
    assembly_path: Path,
    output_path: Path,
    operation: dict[str, Any],
) -> dict[str, Any]:
    """Apply one structured CIL operation to a temporary output assembly."""
    _validate_input(config, assembly_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"output_path": str(output_path), **operation}
    data = _invoke(config, "patch-il", [str(assembly_path)], payload=payload)
    if not output_path.is_file():
        raise CilToolError("CIL tool produced no output assembly")
    if output_path.stat().st_size > config.max_assembly_size:
        output_path.unlink(missing_ok=True)
        raise CilToolError("Patched assembly exceeds the configured binary size ceiling")
    return data


def verify_assembly(config: NoirConfig, assembly_path: Path) -> dict[str, Any]:
    """Reopen a PE/CIL assembly and perform structural verification."""
    _validate_input(config, assembly_path)
    return _invoke(config, "verify", [str(assembly_path)])


def _validate_input(config: NoirConfig, assembly_path: Path) -> None:
    if not assembly_path.is_file():
        raise CilToolError(f"Assembly not found: {assembly_path.name}")
    if assembly_path.stat().st_size > config.max_assembly_size:
        raise CilToolError(
            f"Assembly is {assembly_path.stat().st_size:,} bytes; configured maximum is "
            f"{config.max_assembly_size:,}"
        )
