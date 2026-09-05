"""Budget and relevant-evidence regression tests. No remote AI calls."""

import copy
import json

import pytest

from noir.domain.config import NoirConfig
from noir.domain.enums import PatchOperationType
from noir.domain.models import AnalysisResult, ChangePlan, PlanFileChange
from noir.infrastructure.ai.budget import bounded_prompt
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.ai.gemini import GeminiProvider, GeminiProviderError
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


@pytest.fixture
def ws(tmp_path):
    config = NoirConfig(_env_file=None, data_dir=str(tmp_path), gemini_api_key="test-only")
    workspace = ProjectWorkspace("testcontext", config)
    workspace.create()
    manifest = """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
<application android:label="@string/app_name">
<activity-alias android:name="Launcher" android:label="@string/alternate_name">
<intent-filter><category android:name="android.intent.category.LAUNCHER" /></intent-filter>
</activity-alias></application></manifest>"""
    (workspace.decoded_dir / "AndroidManifest.xml").write_text(manifest)
    values = workspace.decoded_dir / "res/values"
    values.mkdir(parents=True)
    (values / "strings.xml").write_text(
        '<resources><string name="app_name">Original</string>'
        '<string name="alternate_name">Alternate</string>'
        '<string name="unrelated">' + "x" * 60_000 + "</string></resources>"
    )
    (workspace.decoded_dir / "unrelated.smali").write_text("not relevant")
    return workspace


def test_label_context_prefers_actual_resources_not_alphabetical_noise(ws):
    context = AiContextTools(ws).build_context(user_request="Change app display name to a1b2c3")
    assert "unrelated.smali" not in context["file_snippets"]
    assert "Original" in context["file_snippets"]["res/values/strings.xml"]
    assert "Alternate" in context["file_snippets"]["res/values/strings.xml"]
    assert "unrelated" not in context["file_snippets"]["res/values/strings.xml"]
    assert context["file_coverage"]["res/values/strings.xml"] == "exact_label_elements_only"
    assert context["file_coverage"]["AndroidManifest.xml"] == "full"


def test_required_patch_files_are_not_silently_skipped(ws):
    target = ws.decoded_dir / "big.smali"
    target.write_text("x" * (ws.config.ai_max_request_size + 1))
    with pytest.raises(ValueError, match="Required patch file"):
        AiContextTools(ws).build_context(["big.smali"])


def test_required_file_uses_request_budget_not_planning_cap(ws):
    source = ".class public LExample;\n.super Ljava/lang/Object;\n" + "# detail\n" * 40_000
    (ws.decoded_dir / "big.smali").write_text(source)
    tools = AiContextTools(ws)
    assert len(source.encode()) > tools.MAX_FILE_SIZE
    context = tools.build_context(["big.smali"])
    assert context["file_snippets"]["big.smali"] == source
    assert context["file_coverage"]["big.smali"] == "full"
    assert "big.smali" in tools.build_context()["omitted_files"]


def test_required_reads_remain_bounded_by_patch_engine_limit(ws):
    ws.config.ai_max_request_size = 2_000_000
    (ws.decoded_dir / "huge.smali").write_text("x" * 1_000_001)
    with pytest.raises(ValueError, match="Required patch file"):
        AiContextTools(ws).build_context(["huge.smali"])


def test_large_required_file_still_counts_json_escaping_before_network(ws, monkeypatch):
    (ws.decoded_dir / "escapes.smali").write_text("\\\n" * 120_000)
    context = AiContextTools(ws).build_context(["escapes.smali"])
    plan = ChangePlan(
        project_id=ws.project_id,
        workspace_revision=0,
        user_request="Edit the file",
        file_changes=[PlanFileChange(relative_path="escapes.smali", operation="replace_block")],
    )
    provider = GeminiProvider(config=ws.config)
    monkeypatch.setattr(provider, "_get_client", lambda: pytest.fail("Must not contact Gemini"))
    with pytest.raises(GeminiProviderError, match="Required content was not truncated"):
        provider.generate_patch(plan, context)


def test_exact_utf8_budget_keeps_required_json_and_full_file():
    text = '\\"\nहिन्दी🐍' * 70
    data = {
        "files": ["long/" * 50] * 500,
        "file_snippets": {"AndroidManifest.xml": text},
        "file_hashes": {"AndroidManifest.xml": "verified-host-hash"},
    }
    original = copy.deepcopy(data)
    prompt = bounded_prompt(
        lambda s: "BEGIN\n" + s + "\nEND",
        data,
        system="system" * 10,
        max_bytes=3000,
        protected_paths=["AndroidManifest.xml"],
    )
    assert len(prompt.encode()) + len(("system" * 10).encode()) <= 3000
    parsed = json.loads(prompt.removeprefix("BEGIN\n").removesuffix("\nEND"))
    assert parsed["file_snippets"]["AndroidManifest.xml"] == text
    assert parsed["context_truncated"]
    assert data == original


def test_required_file_overflow_is_actionable_not_truncated():
    with pytest.raises(ValueError, match="Required content was not truncated"):
        bounded_prompt(
            lambda s: s,
            {"file_snippets": {"required": "x" * 2000}},
            system="system",
            max_bytes=1000,
            protected_paths=["required"],
        )


def test_provider_counts_system_instruction_before_network(monkeypatch):
    provider = GeminiProvider(config=NoirConfig(_env_file=None, gemini_api_key="test-only"))
    provider.config.ai_max_request_size = 10
    monkeypatch.setattr(provider, "_get_client", lambda: pytest.fail("Network must not be called"))
    with pytest.raises(GeminiProviderError, match="No request was sent"):
        provider._call_model("123456", "abcdef")


def test_large_inventory_fits_actual_planning_prompt(ws, monkeypatch):
    context = AiContextTools(ws).build_context(user_request="Rename app")
    context["files"] = ["irrelevant/" * 80] * 500
    provider = GeminiProvider(config=ws.config)
    provider.config.ai_max_request_size = 8000

    def inspect(prompt, system, *, response_schema):
        assert len(prompt.encode()) + len(system.encode()) <= 8000
        assert response_schema["properties"]["runtime_triggers"]["type"] == "array"
        assert "@string/app_name" in prompt
        assert "unrelated.smali" not in prompt
        return json.dumps(
            {
                "intended_outcome": "Rename",
                "file_changes": [
                    {"relative_path": "AndroidManifest.xml", "operation": "manifest_update"}
                ],
            }
        )

    monkeypatch.setattr(provider, "_call_model", inspect)
    provider.generate_plan(
        "Rename app", AnalysisResult(project_id=ws.project_id), context, project_id=ws.project_id
    )


def test_inventory_ranks_real_public_entrypoints_ahead_of_localization_noise(ws, monkeypatch):
    noise = [f"assets/public/i18n/locale-{index}.json" for index in range(1200)]
    files = [*noise, "assets/public/main.js", "assets/public/variant.js", "lib/arm64/lib.so"]
    monkeypatch.setattr(ws, "list_files", lambda subdir="": sorted(files))

    inventory = AiContextTools(ws).list_project_files(user_request="swap chess king and queen")

    assert "assets/public/main.js" in inventory
    assert "assets/public/variant.js" in inventory
    assert inventory.index("assets/public/main.js") < inventory.index(noise[0])


def test_mono_discovery_reserves_context_for_primary_managed_assembly(ws, monkeypatch):
    relative = "assets/bin/Data/Managed/Assembly-CSharp.dll"
    target = ws.decoded_dir / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"MZ test assembly")
    for index in range(AiContextTools.MAX_FILES + 5):
        (ws.decoded_dir / f"noise-{index:02}.smali").write_text("noise")

    analysis = AnalysisResult(
        project_id=ws.project_id,
        runtime="mono",
        managed_assemblies=[relative],
    )
    tools = AiContextTools(ws, analysis)
    monkeypatch.setattr(
        tools,
        "_inspect_binary_path",
        lambda path, *, user_request="": {
            "format": "cil",
            "path": path,
            "request": user_request,
        },
    )

    context = tools.build_context(user_request="give me unlimited money and keys")

    assert relative in context["binary_inspection"]
    assert context["file_coverage"][relative] == "structured_binary_inspection"
    assert len(context["file_snippets"]) < AiContextTools.MAX_FILES


def test_managed_symbol_ranking_favors_exact_currency_and_key_terms(ws):
    noisy_fields = [
        {"name": f"ONLINE_SETTING_KEY_{index}", "field_type": "System.String"}
        for index in range(100)
    ]
    inspection = {
        "assembly_name": "Assembly-CSharp",
        "types": [
            {
                "full_name": "SocialManager",
                "fields": noisy_fields,
                "methods": [{"signature": "System.Void StartGame()", "has_body": True}],
            },
            {
                "full_name": "PlayerInfo",
                "fields": [
                    {"name": "_amountOfCoins", "field_type": "System.Int32"},
                    {"name": "_amountOfKeys", "field_type": "System.Int32"},
                ],
                "methods": [
                    {
                        "signature": "System.Void set_amountOfCoins(System.Int32)",
                        "has_body": True,
                    },
                    {
                        "signature": "System.Void set_amountOfKeys(System.Int32)",
                        "has_body": True,
                    },
                ],
            },
        ],
    }

    compact = AiContextTools(ws)._compact_assembly_inspection(
        inspection,
        "gimme unlimited money and keys; inspect the codebase first",
        max_bytes=4000,
    )

    assert compact["types"][0]["full_name"] == "PlayerInfo"
    selectors = json.dumps(compact["types"][0])
    assert "amountOfCoins" in selectors
    assert "amountOfKeys" in selectors
    assert len(compact["types"][1]["fields"]) <= 32


def test_patch_budget_preserves_required_files(ws, monkeypatch):
    context = AiContextTools(ws).build_context(["AndroidManifest.xml"])
    context["files"] = ["irrelevant/" * 80] * 500
    provider = GeminiProvider(config=ws.config)
    provider.config.ai_max_request_size = 8000
    plan = ChangePlan(
        project_id=ws.project_id,
        workspace_revision=0,
        user_request="Rename app",
        file_changes=[
            PlanFileChange(
                relative_path="AndroidManifest.xml", operation=PatchOperationType.MANIFEST_UPDATE
            )
        ],
    )

    def inspect(prompt, system, *, response_schema):
        schema_bytes = len(json.dumps(response_schema, separators=(",", ":")).encode())
        assert len(prompt.encode()) + len(system.encode()) + schema_bytes <= 8000
        assert "@string/app_name" in prompt
        return json.dumps(
            {
                "operations": [
                    {
                        "relative_path": "AndroidManifest.xml",
                        "operation": "manifest_update",
                        "xml_element": "application",
                        "xml_attributes": {"android:label": "a1b2c3"},
                    }
                ]
            }
        )

    monkeypatch.setattr(provider, "_call_model", inspect)
    patch = provider.generate_patch(plan, context)
    assert (
        patch.operations[0].expected_preimage_hash == context["file_hashes"]["AndroidManifest.xml"]
    )


def test_excerpt_cannot_be_used_to_replace_whole_resource(ws, monkeypatch):
    context = AiContextTools(ws).build_context(
        ["res/values/strings.xml"], user_request="Rename app"
    )
    provider = GeminiProvider(config=ws.config)
    plan = ChangePlan(
        project_id=ws.project_id,
        workspace_revision=0,
        user_request="Rename app",
        file_changes=[
            PlanFileChange(
                relative_path="res/values/strings.xml", operation=PatchOperationType.REPLACE_BLOCK
            )
        ],
    )
    monkeypatch.setattr(
        provider,
        "_call_model",
        lambda *args, **kwargs: json.dumps(
            {
                "operations": [
                    {
                        "relative_path": "res/values/strings.xml",
                        "operation": "replace_file",
                        "new_content": "bad",
                    }
                ]
            }
        ),
    )
    with pytest.raises(GeminiProviderError, match="Excerpt-only"):
        provider.generate_patch(plan, context)


def test_missing_required_patch_context_fails_before_network(ws, monkeypatch):
    provider = GeminiProvider(config=ws.config)
    plan = ChangePlan(
        project_id=ws.project_id,
        workspace_revision=0,
        user_request="Rename app",
        file_changes=[
            PlanFileChange(
                relative_path="AndroidManifest.xml", operation=PatchOperationType.MANIFEST_UPDATE
            )
        ],
    )
    monkeypatch.setattr(provider, "_call_model", lambda *args: pytest.fail("Must not call AI"))
    with pytest.raises(GeminiProviderError, match="Required patch context is missing"):
        provider.generate_patch(plan, {"file_snippets": {}})
