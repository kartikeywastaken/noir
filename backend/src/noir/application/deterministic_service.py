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

from noir.domain.enums import PatchOperationType, Provenance
from noir.domain.models import ChangePlan, PatchOperation, PatchSet, PlanFileChange
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, compute_file_hash
from noir.security.xml import parse

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ANDROID = f"{{{ANDROID_NS}}}"
RUNTIME_PACKAGE = "in.v0id.noir.injected"
RUNTIME_DESCRIPTOR_PREFIX = "Lin/v0id/noir/injected/"
RUNTIME_VERSION = "1"
SUPPORTED_INTENTS = {"app_name", "toast_flash", "network_ping"}
ET.register_namespace("android", ANDROID_NS)


@dataclass(frozen=True)
class OperationSpec:
    app_name: str | None = None
    launch_url: str | None = None
    startup_message: str | None = None
    interaction_toast: str | None = None
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
        return values

    @property
    def strategies(self) -> list[str]:
        values: list[str] = []
        if self.app_name:
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
        r"(?i)\b(?:add|grant|remove|revoke)\s+(?:the\s+)?(?:[\w.]+\s+){0,3}permission|"
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

    spec = OperationSpec(app_name, launch_url, startup_message, interaction_toast)
    return spec if spec.intents else None


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


def create_deterministic_plan(
    project_id: str, revision: int, request: str, spec: OperationSpec
) -> ChangePlan:
    changes = [
        PlanFileChange(
            relative_path="AndroidManifest.xml",
            operation=PatchOperationType.REPLACE_FILE,
            description="Update existing labels and/or register injected components",
        )
    ]
    if spec.requires_runtime:
        changes.append(
            PlanFileChange(
                relative_path="<next-unused>/classesN.dex",
                operation=PatchOperationType.CREATE_FILE,
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
        manifest_changes=["Literal launcher labels and runtime metadata/components"],
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
            "launch_url": spec.launch_url,
            "startup_message": spec.startup_message,
            "interaction_toast": spec.interaction_toast,
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


def _original_apk(workspace: ProjectWorkspace) -> Path:
    candidates = [
        path
        for path in workspace.input_dir.iterdir()
        if path.is_file() and not path.is_symlink()
    ]
    if len(candidates) != 1:
        raise ValueError("Exactly one preserved original APK is required")
    return candidates[0]


def _existing_runtime_dex(workspace: ProjectWorkspace, payload: bytes) -> str | None:
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


def _next_dex_name(workspace: ProjectWorkspace) -> str:
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


def _resolved_component(package_name: str, name: str) -> str:
    if name.startswith("."):
        return package_name + name
    if "." not in name:
        return f"{package_name}.{name}"
    return name


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
    workspace: ProjectWorkspace,
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
        reusable_dex = _existing_runtime_dex(workspace, payload)
        dex_name = reusable_dex or _next_dex_name(workspace)
        target = workspace.decoded_dir / dex_name
        if reusable_dex is None and (not target.exists() or target.read_bytes() != payload):
            operations.append(
                PatchOperation(
                    relative_path=dex_name,
                    operation=(
                        PatchOperationType.REPLACE_FILE
                        if target.exists()
                        else PatchOperationType.CREATE_FILE
                    ),
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
