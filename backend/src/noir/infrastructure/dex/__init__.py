"""DEX infrastructure package for Dalvik Executable parsing,
integrity, verification, and patching.
"""

from noir.infrastructure.dex.integrity import (
    compute_dex_checksum,
    compute_dex_signature,
    recalculate_dex_header,
    verify_dex_file,
    verify_dex_header,
    verify_dex_header_fields,
)
from noir.infrastructure.dex.patcher import (
    DexPatchError,
    DexStringOrderingError,
    patch_dex_bytes,
    patch_dex_file_string,
    patch_dex_string_in_place,
    validate_string_ordering,
)
from noir.infrastructure.dex.reader import (
    BRANCH_OPS,
    IF_TEST,
    IF_TESTZ,
    INVOKE_OPS,
    MOVE_RESULT_OPS,
    OP_NAMES,
    OP_UNITS,
    PRODUCER_OPS,
    Dex,
    load_dex,
)
from noir.infrastructure.dex.verifier import (
    DexVerifierViolation,
    verify_dex_bytecode,
    verify_dex_file_bytecode,
    verify_method_bytecode,
)

__all__ = [
    "BRANCH_OPS",
    "Dex",
    "DexPatchError",
    "DexStringOrderingError",
    "DexVerifierViolation",
    "IF_TEST",
    "IF_TESTZ",
    "INVOKE_OPS",
    "MOVE_RESULT_OPS",
    "OP_NAMES",
    "OP_UNITS",
    "PRODUCER_OPS",
    "compute_dex_checksum",
    "compute_dex_signature",
    "load_dex",
    "patch_dex_bytes",
    "patch_dex_file_string",
    "patch_dex_string_in_place",
    "recalculate_dex_header",
    "validate_string_ordering",
    "verify_dex_bytecode",
    "verify_dex_file",
    "verify_dex_file_bytecode",
    "verify_dex_header",
    "verify_dex_header_fields",
    "verify_method_bytecode",
]
