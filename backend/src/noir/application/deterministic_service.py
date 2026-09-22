"""Hybrid deterministic APK strategies for NOIR's guaranteed operations."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from noir.domain.enums import PatchOperationType, Provenance
from noir.domain.models import ChangePlan, PatchOperation, PatchSet, PlanFileChange
from noir.infrastructure.filesystem.workspace import compute_file_hash
from noir.security.xml import parse

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ANDROID = f"{{{ANDROID_NS}}}"
RUNTIME_PACKAGE = "in.v0id.noir.injected"
RUNTIME_DESCRIPTOR_PREFIX = "Lin/v0id/noir/injected/"
RUNTIME_VERSION = "1"
SUPPORTED_INTENTS = {"app_name", "toast_flash", "network_ping", "permission"}
ET.register_namespace("android", ANDROID_NS)


class DeterministicWorkspace(Protocol):
    input_dir: Path
    decoded_dir: Path


@dataclass(frozen=True)
class OperationSpec:
    app_name: str | None = None
    launch_url: str | None = None
    startup_message: str | None = None
    interaction_toast: str | None = None
    permission: str | None = None
    forward_original: bool = True

    @property
    def intents(self) -> list[str]:
        values: list[str] = []
        if self.app_name:
            values.append("app_name")
        if self.launch_url:
            values.append("launch_redirect")
        if self.startup_message:
            values.append("startup_message")
        if self.interaction_toast:
            values.append("interaction_toast")
        if self.permission:
            values.append("permission")
        return values

    @property
    def strategies(self) -> list[str]:
        values: list[str] = []
        if self.app_name:
            values.append("existing_file")
        if self.permission:
            values.append("existing_file")
        if self.launch_url or self.startup_message:
            values.extend(["injected_dex", "manifest_component"])
        if self.interaction_toast:
            values.extend(["injected_dex", "manifest_component"])
        return list(dict.fromkeys(values))

    @property
    def requires_runtime(self) -> bool:
        return bool(self.launch_url or self.startup_message or self.interaction_toast)

    @property
    def requires_full_decode(self) -> bool:
        return False


def _clean(value: str) -> str:
    return value.strip().strip("'\"` ").rstrip(".,;:")


def parse_operation_spec(
    request: str, matched_intents: set[str] | None = None
) -> OperationSpec | None:
    text = " ".join(request.strip().split())
    if not text or (matched_intents and not matched_intents.issubset(SUPPORTED_INTENTS)):
        return None
    if re.search(
        r"(?i)\b(?:remove|revoke)\s+(?:the\s+)?(?:[\w.]+\s+){0,3}permission|"
        r"\b(?:layout|button\s+colou?r|native\s+code|unity|il2cpp|service|receiver)\b",
        text,
    ):
        return None

    app_name = None
    rename = re.search(
        r"(?i)\b(?:rename|name|change\s+(?:the\s+)?(?:app(?:lication)?\s+)?name)"
        r"(?:\s+(?:the\s+)?app(?:lication)?)?(?:\s+name)?\s+(?:to|as)\s+"
        r"(?P<value>.+?)(?=,\s*|\s+(?:and|then|while|plus)\s+|$)",
        text,
    )
    if rename:
        candidate = re.sub(
            r"(?i)\s+and\s+preserve\b.*$", "", _clean(rename.group("value"))
        ).strip()
        if 1 <= len(candidate) <= 80 and not candidate.lower().startswith(("http://", "https://")):
            app_name = candidate

    url_match = re.search(r"https?://[^\s'\"<>]+", text, re.IGNORECASE)
    launch_url = None
    if url_match and re.search(
        r"(?i)\b(?:open|redirect|navigate|browser|site|website)\b",
        text,
    ):
        launch_url = url_match.group(0).rstrip(".,;:)")

    startup_message = interaction_toast = None
    if re.search(r"(?i)\b(?:toast|flash|popup|pop-up|message)\b", text):
        quoted = re.search(
            r"(?i)(?:saying|message(?:\s+(?:saying|text))?|text)\s*[:=]?\s*['\"]([^'\"]+)['\"]",
            text,
        ) or re.search(r"['\"]([^'\"]+)['\"]", text)
        value = _clean(quoted.group(1))[:200] if quoted else "NOIR"
        if re.search(r"(?i)\b(?:tap|touch|click|interact|interaction)\b", text):
            interaction_toast = value
        else:
            startup_message = value

    permission = None
    explicit_permission = re.search(
        r"(?i)\b(?:add|grant)\b.*?\b(android\.permission\.[A-Za-z0-9_.]+)\b",
        text,
    )
    if explicit_permission:
        permission = explicit_permission.group(1)
    elif re.search(r"(?i)\b(?:add|grant)\b.*?\binternet\s+permission\b", text):
        permission = "android.permission.INTERNET"
    elif re.search(r"(?i)\b(?:add|grant)\b.*?\bpermission\b", text):
        return None

    spec = OperationSpec(
        app_name,
        launch_url,
        startup_message,
        interaction_toast,
        permission,
    )
    return spec if spec.intents else None


def operation_spec_from_plan(plan: ChangePlan) -> OperationSpec:
    """Restore the immutable deterministic inputs approved in a stored plan."""
    values = plan.runtime_configuration
    text_values: dict[str, str | None] = {}
    for key in (
        "app_name",
        "launch_url",
        "startup_message",
        "interaction_toast",
        "permission",
    ):
        value = values.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError("Stored deterministic operation specification is invalid")
        text_values[key] = value
    forward_original = values.get("forward_original", True)
    if not isinstance(forward_original, bool):
        raise ValueError("Stored deterministic operation specification is invalid")
    spec = OperationSpec(**text_values, forward_original=forward_original)
    if not spec.intents or spec.intents != plan.detected_intents:
        raise ValueError("Stored deterministic operation specification is invalid")
    return spec


def load_compatibility_record(config, sha256: str) -> dict | None:
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        return None
    path = Path(config.data_dir) / "compatibility" / f"{sha256}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("sha256") == sha256 else None


def save_compatibility_record(config, sha256: str, **values) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("Invalid compatibility-record SHA-256")
    directory = Path(config.data_dir) / "compatibility"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{sha256}.json"
    temporary = directory / f".{sha256}.tmp"
    temporary.write_text(
        json.dumps(
            {"sha256": sha256, "certified_at": datetime.now(UTC).isoformat(), **values},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def verify_deterministic_preflight(
    request: str,
    workspace: DeterministicWorkspace | None = None,
    analysis: Any | None = None,
    config: Any = None,
) -> Any:
    """Evaluate preflight gates G1–G4 for deterministic operations."""
    from noir.application.gates import PreflightGateEngine

    engine = PreflightGateEngine(config)
    return engine.evaluate_preflight(request, workspace=workspace, analysis=analysis)


def create_deterministic_plan(
    project_id: str,
    revision: int,
    request: str,
    spec: OperationSpec,
    workspace: DeterministicWorkspace,
) -> ChangePlan:
    manifest = workspace.decoded_dir / "AndroidManifest.xml"
    permission_present = bool(
        spec.permission and _manifest_has_permission(manifest, spec.permission)
    )
    manifest_change_required = bool(
        spec.app_name
        or spec.launch_url
        or spec.startup_message
        or spec.interaction_toast
        or (spec.permission and not permission_present)
    )
    changes = []
    if manifest_change_required:
        changes.append(
            PlanFileChange(
                relative_path="AndroidManifest.xml",
                operation=PatchOperationType.REPLACE_FILE,
                description="Update approved Android manifest declarations",
            )
        )
    if spec.requires_runtime:
        runtime_change = _runtime_dex_change(workspace, _runtime_dex())
        if runtime_change:
            changes.append(
                PlanFileChange(
                    relative_path=runtime_change[0],
                    operation=runtime_change[1],
                    description="Add verified NOIR runtime Dex",
                )
            )
    return ChangePlan(
        project_id=project_id,
        workspace_revision=revision,
        user_request=request,
        provider="local-hybrid",
        intended_outcome="; ".join(spec.intents),
        file_changes=changes,
        manifest_changes=(
            ["Literal launcher labels, permissions, and/or runtime components"]
            if manifest_change_required
            else []
        ),
        permission_changes=(
            [spec.permission] if spec.permission and not permission_present else []
        ),
        component_changes=[
            item
            for item in (
                "Proxy launcher aliases" if spec.launch_url else None,
                (
                    "Private initialization provider"
                    if spec.startup_message or spec.interaction_toast
                    else None
                ),
            )
            if item
        ],
        smali_integration_points=[],
        behavioral_changes=spec.intents,
        network_destinations=[spec.launch_url] if spec.launch_url else [],
        compatibility_concerns=[
            "Apps enforcing signature or integrity checks may reject a re-signed APK"
        ],
        unsupported_aspects=(
            [f"{spec.permission} is already declared; no change is required"]
            if spec.permission and permission_present and not changes
            else []
        ),
        validation_steps=[
            "runtime Dex hash",
            "apktool build",
            "zipalign",
            "signature verification",
        ],
        discovery_api_calls=0,
        discovery_stop_reason="hybrid_deterministic_router",
        execution_mode="deterministic",
        detected_intents=spec.intents,
        patch_strategies=spec.strategies,
        runtime_configuration={
            "app_name": spec.app_name,
            "launch_url": spec.launch_url,
            "startup_message": spec.startup_message,
            "interaction_toast": spec.interaction_toast,
            "permission": spec.permission,
            "forward_original": spec.forward_original,
        },
    )


def _runtime_dex() -> bytes:
    path = Path(__file__).resolve().parents[3] / "runtime" / "dist" / "noir-runtime-v1.dex"
    if not path.is_file():
        raise ValueError("Verified NOIR runtime Dex is missing")
    digest_file = path.with_suffix(path.suffix + ".sha256")
    expected = digest_file.read_text(encoding="utf-8").split()[0]
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError("NOIR runtime Dex integrity check failed")
    return data


def _original_apk(workspace: DeterministicWorkspace) -> Path:
    candidates = [
        path
        for path in workspace.input_dir.iterdir()
        if path.is_file() and not path.is_symlink()
    ]
    if len(candidates) != 1:
        raise ValueError("Exactly one preserved original APK is required")
    return candidates[0]


def _existing_runtime_dex(workspace: DeterministicWorkspace, payload: bytes) -> str | None:
    """Return a reusable runtime dex name or reject a namespace collision."""
    with zipfile.ZipFile(_original_apk(workspace)) as archive:
        for name in archive.namelist():
            if re.fullmatch(r"classes\d*\.dex", name):
                data = archive.read(name)
                if RUNTIME_DESCRIPTOR_PREFIX.encode() not in data:
                    continue
                if data == payload:
                    return name
                raise ValueError(
                    "APK contains a conflicting or unsupported NOIR runtime payload"
                )
    return None


def _next_dex_name(workspace: DeterministicWorkspace) -> str:
    indexes: set[int] = set()
    with zipfile.ZipFile(_original_apk(workspace)) as archive:
        for name in archive.namelist():
            match = re.fullmatch(r"classes(\d*)\.dex", name)
            if match:
                indexes.add(int(match.group(1) or "1"))
    for item in workspace.decoded_dir.iterdir():
        match = re.fullmatch(r"classes(\d*)\.dex", item.name)
        if match:
            indexes.add(int(match.group(1) or "1"))
            if RUNTIME_DESCRIPTOR_PREFIX.encode() in item.read_bytes():
                return item.name
        match = re.fullmatch(r"smali(?:_classes(\d+))?", item.name)
        if match:
            indexes.add(int(match.group(1) or "1"))
    index = max(indexes or {1}) + 1
    if index > 999:
        raise ValueError("Dex naming space is exhausted")
    return f"classes{index}.dex"


def _runtime_dex_change(
    workspace: DeterministicWorkspace, payload: bytes
) -> tuple[str, PatchOperationType] | None:
    """Resolve the exact runtime Dex change shared by planning and patching."""
    if _existing_runtime_dex(workspace, payload) is not None:
        return None
    dex_name = _next_dex_name(workspace)
    target = workspace.decoded_dir / dex_name
    if target.exists() and target.read_bytes() == payload:
        return None
    operation = (
        PatchOperationType.REPLACE_FILE if target.exists() else PatchOperationType.CREATE_FILE
    )
    return dex_name, operation


def _resolved_component(package_name: str, name: str) -> str:
    if name.startswith("."):
        return package_name + name
    if "." not in name:
        return f"{package_name}.{name}"
    return name


def _manifest_has_permission(manifest: Path, permission: str) -> bool:
    name = ANDROID + "name"
    return any(
        item.get(name) == permission
        for item in parse(manifest).getroot().findall("uses-permission")
    )


def _launcher_components(app: ET.Element) -> list[tuple[ET.Element, list[ET.Element]]]:
    result: list[tuple[ET.Element, list[ET.Element]]] = []
    for tag in ("activity", "activity-alias"):
        for component in app.findall(tag):
            if component.get(ANDROID + "enabled", "true").lower() == "false":
                continue
            filters: list[ET.Element] = []
            for item in component.findall("intent-filter"):
                actions = {x.get(ANDROID + "name") for x in item.findall("action")}
                categories = {x.get(ANDROID + "name") for x in item.findall("category")}
                if (
                    "android.intent.action.MAIN" in actions
                    and "android.intent.category.LAUNCHER" in categories
                ):
                    filters.append(item)
            if filters:
                result.append((component, filters))
    return result


def _metadata(parent: ET.Element, name: str, value: str) -> None:
    item = ET.SubElement(parent, "meta-data")
    item.set(ANDROID + "name", name)
    item.set(ANDROID + "value", value)


def _configured_manifest(manifest: Path, spec: OperationSpec, authority: str) -> str:
    tree = parse(manifest)
    root = tree.getroot()
    package_name = root.get("package", "")
    app = root.find("application")
    if not package_name or app is None:
        raise ValueError("Android manifest package/application is missing")

    if spec.app_name:
        app.set(ANDROID + "label", spec.app_name)
        for component, _ in _launcher_components(app):
            component.set(ANDROID + "label", spec.app_name)

    if spec.permission and not _manifest_has_permission(manifest, spec.permission):
        permission = ET.Element("uses-permission")
        permission.set(ANDROID + "name", spec.permission)
        root.insert(list(root).index(app), permission)

    for item in list(app):
        name = item.get(ANDROID + "name", "")
        if name.startswith(f"{RUNTIME_PACKAGE}.") or name.startswith("noir.runtime."):
            app.remove(item)

    if spec.requires_runtime:
        _metadata(app, "noir.runtime.version", RUNTIME_VERSION)
        if spec.interaction_toast:
            _metadata(app, "noir.runtime.interaction_toast", spec.interaction_toast)

    if spec.startup_message or spec.interaction_toast:
        provider = ET.SubElement(app, "provider")
        provider.set(ANDROID + "name", f"{RUNTIME_PACKAGE}.InitProvider")
        provider.set(ANDROID + "authorities", authority)
        provider.set(ANDROID + "exported", "false")
        provider.set(ANDROID + "initOrder", "1999999999")
        _metadata(app, "noir.runtime.startup_message", spec.startup_message or "")

    if spec.launch_url:
        launchers = _launcher_components(app)
        if not launchers:
            raise ValueError("No enabled launcher Activity or alias was found")
        proxy = ET.SubElement(app, "activity")
        proxy.set(ANDROID + "name", f"{RUNTIME_PACKAGE}.ProxyActivity")
        proxy.set(ANDROID + "exported", "false")
        proxy.set(ANDROID + "theme", "@android:style/Theme.Translucent.NoTitleBar")
        for index, (original, filters) in enumerate(launchers):
            original_name = original.get(ANDROID + "name", "")
            if not original_name:
                continue
            alias = ET.SubElement(app, "activity-alias")
            alias.set(ANDROID + "name", f"{RUNTIME_PACKAGE}.ProxyAlias{index}")
            alias.set(ANDROID + "targetActivity", f"{RUNTIME_PACKAGE}.ProxyActivity")
            alias.set(ANDROID + "exported", "true")
            for attribute in ("icon", "roundIcon", "banner", "logo"):
                value = original.get(ANDROID + attribute)
                if value:
                    alias.set(ANDROID + attribute, value)
            if spec.app_name:
                alias.set(ANDROID + "label", spec.app_name)
            elif original.get(ANDROID + "label"):
                alias.set(ANDROID + "label", original.get(ANDROID + "label", ""))
            _metadata(alias, "noir.runtime.launch_url", spec.launch_url)
            _metadata(
                alias,
                "noir.runtime.original_launcher",
                f"{package_name}/{_resolved_component(package_name, original_name)}",
            )
            _metadata(
                alias,
                "noir.runtime.forward_original",
                "true" if spec.forward_original else "false",
            )
            for intent_filter in filters:
                original.remove(intent_filter)
                alias.append(intent_filter)

    ET.indent(tree, space="    ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def create_deterministic_patch(
    workspace: DeterministicWorkspace,
    plan: ChangePlan,
    spec: OperationSpec,
    original_sha: str,
) -> PatchSet:
    del original_sha  # workspace input integrity is already bound to the project record
    manifest = workspace.decoded_dir / "AndroidManifest.xml"
    package_name = parse(manifest).getroot().get("package", "")
    authority = f"{package_name}.noir.runtime.v{RUNTIME_VERSION}"
    existing_authorities = {
        authority.strip()
        for item in parse(manifest).getroot().findall("application/provider")
        if not item.get(ANDROID + "name", "").startswith(f"{RUNTIME_PACKAGE}.")
        for authority in item.get(ANDROID + "authorities", "").split(";")
        if authority.strip()
    }
    if authority in existing_authorities:
        base = authority + "." + hashlib.sha256(package_name.encode()).hexdigest()[:8]
        authority = base
        suffix = 1
        while authority in existing_authorities:
            authority = f"{base}.{suffix}"
            suffix += 1

    operations: list[PatchOperation] = []
    configured = _configured_manifest(manifest, spec, authority)
    if manifest.read_text(encoding="utf-8") != configured:
        operations.append(
            PatchOperation(
                relative_path="AndroidManifest.xml",
                operation=PatchOperationType.REPLACE_FILE,
                expected_preimage_hash=compute_file_hash(manifest),
                new_content=configured,
                affected_scope="existing labels and injected manifest components",
            )
        )

    if spec.requires_runtime:
        payload = _runtime_dex()
        runtime_change = _runtime_dex_change(workspace, payload)
        if runtime_change:
            dex_name, operation = runtime_change
            target = workspace.decoded_dir / dex_name
            operations.append(
                PatchOperation(
                    relative_path=dex_name,
                    operation=operation,
                    expected_preimage_hash=compute_file_hash(target) if target.exists() else None,
                    expected_absent=not target.exists(),
                    new_content_base64=base64.b64encode(payload).decode("ascii"),
                    affected_scope="verified reusable NOIR runtime Dex",
                )
            )

    return PatchSet(
        plan_id=plan.plan_id,
        project_id=plan.project_id,
        workspace_revision=plan.workspace_revision,
        provenance=Provenance.SYSTEM_GENERATED,
        execution_mode="deterministic",
        detected_intents=spec.intents,
        patch_strategies=spec.strategies,
        runtime_configuration=plan.runtime_configuration,
        operations=operations,
    )
