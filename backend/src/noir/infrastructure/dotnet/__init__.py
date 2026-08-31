"""Managed .NET/CIL inspection and patching support."""

from noir.infrastructure.dotnet.adapter import (
    CilToolError,
    inspect_assembly,
    patch_assembly,
    read_method_il,
    verify_assembly,
)

__all__ = [
    "CilToolError",
    "inspect_assembly",
    "patch_assembly",
    "read_method_il",
    "verify_assembly",
]
