from noir.application.ai_service import _apply_strategy_metadata
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
