from noir.application.ai_service import (
    _apply_strategy_metadata,
    _plan_coverage_gaps,
    _request_requirements,
)
from noir.domain.enums import PatchOperationType
from noir.domain.models import ChangePlan, PlanFileChange


def test_advanced_plan_exposes_hybrid_strategy_and_detected_intents():
    plan = ChangePlan(
        project_id="project",
        workspace_revision=1,
        user_request="change an existing button",
        intended_outcome="button calls existing behavior",
        file_changes=[
            PlanFileChange(
                relative_path="smali/example/MainActivity.smali",
                operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
                description="insert a small bridge",
            )
        ],
        component_changes=["register existing component"],
    )

    _apply_strategy_metadata(plan, ["ui_layout", "ui_layout"])

    assert plan.detected_intents == ["ui_layout"]
    assert plan.patch_strategies == [
        "existing_file",
        "manifest_component",
        "minimal_smali_bridge",
        "hybrid",
    ]


def test_network_claim_requires_an_executable_plan_change():
    plan = ChangePlan(
        project_id="project",
        workspace_revision=1,
        user_request="redirect to https://example.com",
        intended_outcome="redirect on launch",
        file_changes=[
            PlanFileChange(
                relative_path="AndroidManifest.xml",
                operation=PatchOperationType.MANIFEST_UPDATE,
            ),
            PlanFileChange(
                relative_path="res/values/strings.xml",
                operation=PatchOperationType.XML_RESOURCE_UPDATE,
            ),
        ],
        network_destinations=["https://example.com"],
        runtime_triggers=["launch"],
    )

    gaps = _plan_coverage_gaps(plan, ["app_name", "network_ping"])

    assert any("network_ping" in gap for gap in gaps)
    assert any("network behavior" in gap for gap in gaps)


def test_network_claim_is_covered_by_smali_operation():
    plan = ChangePlan(
        project_id="project",
        workspace_revision=1,
        user_request="redirect to https://example.com",
        intended_outcome="redirect on launch",
        file_changes=[
            PlanFileChange(
                relative_path="smali/example/MainActivity.smali",
                operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
            )
        ],
        network_destinations=["https://example.com"],
        runtime_triggers=["launch"],
        smali_integration_points=["MainActivity.onCreate"],
    )

    assert _plan_coverage_gaps(plan, ["network_ping"]) == []


def test_compound_request_preserves_stateful_and_device_data_requirements():
    requirements = _request_requirements(
        "Redirect once on login. Also provide the Android version, service carrier, "
        "location, and battery status."
    )

    assert set(requirements) == {
        "trigger_once_after_login",
        "data_android_version",
        "data_service_carrier",
        "data_location",
        "data_battery_status",
    }


def test_compound_request_gaps_are_not_satisfied_by_generic_launch_code():
    request = (
        "Redirect once on login. Also provide the Android version, service carrier, "
        "location, and battery status."
    )
    plan = ChangePlan(
        project_id="project",
        workspace_revision=1,
        user_request=request,
        intended_outcome="redirect on launch",
        file_changes=[
            PlanFileChange(
                relative_path="smali/example/MainActivity.smali",
                operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
            )
        ],
        network_destinations=["https://example.com"],
        runtime_triggers=["application launch"],
    )

    gaps = _plan_coverage_gaps(
        plan,
        ["network_ping"],
        _request_requirements(request),
    )

    assert len([gap for gap in gaps if "unaccounted for" in gap]) == 5
