"""Adversarial tests for PatchEngine Smali bytecode validation.

Verifies that PatchEngine.validate_patch validates Smali bytecode across all
patch operations targeting .smali files:
- CREATE_FILE
- REPLACE_FILE
- REPLACE_BLOCK
"""

import pytest
from pathlib import Path
from types import SimpleNamespace

from noir.domain.enums import PatchOperationType, Provenance
from noir.domain.models import PatchOperation, PatchSet
from noir.patches.engine import PatchEngine


class MockWorkspace:
    def __init__(self, tmp_path: Path):
        self.project_id = "test_project"
        self.decoded_dir = tmp_path
        self.project_dir = tmp_path
        self.changes_dir = tmp_path / "changes"
        self.changes_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_revision = 0


def test_create_file_with_invalid_smali_rejected(tmp_path):
    ws = MockWorkspace(tmp_path)
    engine = PatchEngine(ws)

    invalid_smali = """
    .class public Lcom/example/Bad;
    .super Ljava/lang/Object;
    .method public test()V
        .registers 2
        bad_opcode v0
        return-void
    .end method
    """
    patch = PatchSet(
        plan_id="p1",
        project_id="test_project",
        workspace_revision=0,
        provenance=Provenance.AI_GENERATED,
        operations=[
            PatchOperation(
                relative_path="smali/com/example/Bad.smali",
                operation=PatchOperationType.CREATE_FILE,
                new_content=invalid_smali,
            )
        ],
    )
    errors = engine.validate_patch(patch)
    assert len(errors) > 0
    assert any("UNKNOWN_OPCODE" in e for e in errors)


def test_replace_file_with_invalid_smali_rejected(tmp_path):
    ws = MockWorkspace(tmp_path)
    smali_file = tmp_path / "smali" / "com" / "example" / "Target.smali"
    smali_file.parent.mkdir(parents=True, exist_ok=True)
    smali_file.write_text(".class public Lcom/example/Target;\n.super Ljava/lang/Object;\n")

    engine = PatchEngine(ws)

    invalid_smali = """
    .class public Lcom/example/Target;
    .super Ljava/lang/Object;
    .method public test()V
        .registers 2
        invoke-static {"https://example.com"}, Landroid/net/Uri;->parse(Ljava/lang/String;)Landroid/net/Uri;
        return-void
    .end method
    """
    patch = PatchSet(
        plan_id="p1",
        project_id="test_project",
        workspace_revision=0,
        provenance=Provenance.AI_GENERATED,
        operations=[
            PatchOperation(
                relative_path="smali/com/example/Target.smali",
                operation=PatchOperationType.REPLACE_FILE,
                new_content=invalid_smali,
            )
        ],
    )
    errors = engine.validate_patch(patch)
    assert len(errors) > 0
    assert any("LITERAL_IN_REGISTER_LIST" in e for e in errors)


def test_replace_block_with_invalid_smali_rejected(tmp_path):
    ws = MockWorkspace(tmp_path)
    smali_file = tmp_path / "smali" / "com" / "example" / "Target.smali"
    smali_file.parent.mkdir(parents=True, exist_ok=True)
    smali_file.write_text(
        ".class public Lcom/example/Target;\n"
        ".super Ljava/lang/Object;\n"
        ".method public test()V\n"
        "    .registers 2\n"
        "    nop\n"
        "    return-void\n"
        ".end method\n"
    )

    engine = PatchEngine(ws)

    invalid_block = "    add-int/lit8 v0, v1, 500\n"  # 500 exceeds 8-bit signed range [-128, 127]
    patch = PatchSet(
        plan_id="p1",
        project_id="test_project",
        workspace_revision=0,
        provenance=Provenance.AI_GENERATED,
        operations=[
            PatchOperation(
                relative_path="smali/com/example/Target.smali",
                operation=PatchOperationType.REPLACE_BLOCK,
                match_content="    nop\n",
                new_content=invalid_block,
            )
        ],
    )
    errors = engine.validate_patch(patch)
    assert len(errors) > 0
    assert any("LITERAL_OUT_OF_RANGE" in e for e in errors)
