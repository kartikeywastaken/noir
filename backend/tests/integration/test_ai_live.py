"""Opt-in live Gemini check using only synthetic code, never a user's APK contents."""

import os

import pytest

from noir.domain.config import NoirConfig
from noir.domain.enums import PatchOperationType
from noir.domain.models import ChangePlan, PlanFileChange
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.ai.gemini import GeminiProvider
from noir.infrastructure.filesystem.workspace import ProjectWorkspace
from noir.patches.engine import PatchEngine


@pytest.mark.ai
@pytest.mark.skipif(os.environ.get("NOIR_RUN_AI") != "1", reason="Opt-in billable Gemini request")
def test_live_large_smali_new_method(tmp_path):
    config = NoirConfig(data_dir=str(tmp_path))
    if not config.gemini_api_key.get_secret_value():
        pytest.skip("Gemini key is not configured")
    workspace = ProjectWorkspace("synthetic_smali_check", config)
    workspace.create()
    path = workspace.decoded_dir / "SyntheticActivity.smali"
    source = (
        ".class public Lapp/noir/test/SyntheticActivity;\n.super Landroid/app/Activity;\n"
        + "# Synthetic non-sensitive test padding.\n" * 2100
        + "# virtual methods\n"
    )
    assert 80_000 < len(source.encode()) < 90_000
    path.write_text(source)
    plan = ChangePlan(
        project_id=workspace.project_id,
        workspace_revision=0,
        user_request=(
            "For this synthetic test class, add public "
            "dispatchTouchEvent(Landroid/view/MotionEvent;)Z. "
            "On ACTION_DOWN show a short Toast saying hello im working; always call the superclass "
            "dispatchTouchEvent and return its result. Do not change any other behavior."
        ),
        intended_outcome="One new method in a synthetic class for a live API smoke test",
        file_changes=[
            PlanFileChange(
                relative_path=path.name,
                operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
                description="Add the override after the unique # virtual methods comment",
            )
        ],
    )
    context = AiContextTools(workspace).build_context([path.name], user_request=plan.user_request)
    patch = GeminiProvider(config=config).generate_patch(plan, context)
    assert len(patch.operations) == 1
    operation = patch.operations[0]
    assert operation.relative_path == path.name
    assert operation.operation == PatchOperationType.SMALI_INSERT_AT_ANCHOR
    assert operation.method_signature == "dispatchTouchEvent(Landroid/view/MotionEvent;)Z"
    assert "hello im working" in (operation.new_content or "")
    assert "invoke-super" in (operation.new_content or "")
    diff = PatchEngine(workspace).generate_diff(patch)
    assert "dispatchTouchEvent" in diff[0]["preview"]
    assert path.read_text() == source  # Generation/preview must never apply the patch.
