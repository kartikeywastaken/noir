"""Operation catalog structural tests — no AI calls."""

from noir.domain.enums import PatchOperationType
from noir.infrastructure.ai.gemini import _PLAN_RESPONSE_SCHEMA, _allowed_operations_line


def test_every_patch_operation_type_appears_in_allowed_operations_line():
    """Fail loudly when someone adds a PatchOperationType but forgets to expose it."""
    rendered = _allowed_operations_line()
    for op in PatchOperationType:
        assert op.value in rendered, (
            f"PatchOperationType.{op.name} ({op.value}) is missing from the AI's "
            f"allowed-operations catalog. The model will never propose this operation."
        )


def test_plan_response_schema_enumerates_all_operation_types():
    """The structured-output enum must stay in sync with PatchOperationType too."""
    schema_enum = _PLAN_RESPONSE_SCHEMA["properties"]["file_changes"]["items"]["properties"][
        "operation"
    ]["enum"]
    for op in PatchOperationType:
        assert op.value in schema_enum, (
            f"PatchOperationType.{op.name} ({op.value}) is missing from "
            f"_PLAN_RESPONSE_SCHEMA's operation enum."
        )


def test_xml_resource_operations_are_present():
    """Regression test for the original bug: xml_resource_* were silently omitted."""
    rendered = _allowed_operations_line()
    assert "xml_resource_add" in rendered
    assert "xml_resource_update" in rendered
    assert "xml_resource_remove" in rendered
