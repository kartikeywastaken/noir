"""Deterministic Android operations that never call an AI provider.

The payload is isolated in a new dex and is bootstrapped by a private provider.
No existing application class or method is edited, which makes the path stable
across obfuscators, custom Application classes, Views, and Compose.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from datetime import UTC, datetime

from noir.domain.enums import PatchOperationType, Provenance
from noir.domain.models import ChangePlan, PatchOperation, PatchSet, PlanFileChange
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, compute_file_hash
from noir.security.xml import parse

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ANDROID = f"{{{ANDROID_NS}}}"
PAYLOAD_VERSION = "1"
SUPPORTED_INTENTS = {"app_name", "toast_flash", "network_ping"}
ET.register_namespace("android", ANDROID_NS)


@dataclass(frozen=True)
class OperationSpec:
    app_name: str | None = None
    launch_url: str | None = None
    toast_message: str | None = None

    @property
    def intents(self) -> list[str]:
        result: list[str] = []
        if self.app_name:
            result.append("app_name")
        if self.launch_url:
            result.append("launch_redirect")
        if self.toast_message:
            result.append("every_tap_toast")
        return result


def _clean_value(value: str) -> str:
    return value.strip().strip("'\"` ").rstrip(".,;:")


def parse_operation_spec(request: str, matched_intents: set[str] | None = None) -> OperationSpec | None:
    """Parse the three guaranteed operations without probabilistic inference."""
    text = " ".join(request.strip().split())
    if not text:
        return None
    if matched_intents and not matched_intents.issubset(SUPPORTED_INTENTS):
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
        candidate = _clean_value(rename.group("value"))
        candidate = re.sub(r"(?i)\s+and\s+preserve\b.*$", "", candidate).strip()
        if 1 <= len(candidate) <= 80 and not candidate.lower().startswith(("http://", "https://")):
            app_name = candidate

    url_match = re.search(r"https?://[^\s'\"<>]+", text, re.IGNORECASE)
    launch_url = None
    if url_match and re.search(
        r"(?i)\b(?:launch|start(?:up)?|open|redirect|navigate|browser|site|website)\b", text
    ):
        launch_url = url_match.group(0).rstrip(".,;:)")

    toast_message = None
    if re.search(r"(?i)\b(?:toast|flash|popup|pop-up)\b", text):
        quoted = re.search(
            r"(?i)(?:saying|message(?:\s+(?:saying|text))?|text)\s*[:=]?\s*['\"]([^'\"]+)['\"]",
            text,
        ) or re.search(r"['\"]([^'\"]+)['\"]", text)
        if quoted:
            toast_message = _clean_value(quoted.group(1))[:200]
        else:
            unquoted = re.search(
                r"(?i)\b(?:toast|flash)\s+(?:message\s+)?(?:saying\s+)?(.+?)"
                r"(?=\s+(?:when|whenever|on|and|then)\b|$)",
                text,
            )
            toast_message = _clean_value(unquoted.group(1))[:200] if unquoted else "NOIR"

    spec = OperationSpec(app_name, launch_url, toast_message)
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
            {
                "sha256": sha256,
                "certified_at": datetime.now(UTC).isoformat(),
                **values,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def create_deterministic_plan(project_id: str, revision: int, request: str, spec: OperationSpec) -> ChangePlan:
    files = [PlanFileChange(relative_path="AndroidManifest.xml", operation=PatchOperationType.REPLACE_FILE, description="Update literal labels and NOIR payload metadata")]
    if spec.launch_url or spec.toast_message:
        files.extend(
            PlanFileChange(
                relative_path=f"<next-dex>/noir/payload/{name}.smali",
                operation=PatchOperationType.CREATE_FILE,
                description="Inject isolated deterministic Android payload",
            )
            for name in ("StartupProvider", "Lifecycle", "CallbackProxy")
        )
    return ChangePlan(
        project_id=project_id,
        workspace_revision=revision,
        user_request=request,
        provider="local-deterministic",
        model=None,
        intended_outcome="; ".join(spec.intents),
        file_changes=files,
        manifest_changes=["Literal application/launcher labels", "Private NOIR startup provider and metadata"],
        component_changes=["Private, non-exported startup provider"] if (spec.launch_url or spec.toast_message) else [],
        behavioral_changes=spec.intents,
        network_destinations=[spec.launch_url] if spec.launch_url else [],
        runtime_triggers=["first foreground activity"] if spec.launch_url else [],
        compatibility_concerns=["Apps enforcing signature or integrity checks may reject a re-signed APK"],
        validation_steps=["apktool build", "zipalign", "signature verification"],
        discovery_api_calls=0,
        discovery_stop_reason="deterministic_operation_spec",
        execution_mode="deterministic",
        detected_intents=spec.intents,
    )


def _next_dex_dir(decoded_dir: Path) -> str:
    indexes: set[int] = set()
    for item in decoded_dir.iterdir():
        match = re.fullmatch(r"classes(\d*)\.dex", item.name)
        if match:
            indexes.add(int(match.group(1) or "1"))
        match = re.fullmatch(r"smali(?:_classes(\d+))?", item.name)
        if match:
            indexes.add(int(match.group(1) or "1"))
    next_index = max(indexes or {1}) + 1
    if next_index > 999:
        raise ValueError("Dex naming space is exhausted")
    return f"smali_classes{next_index}"


def _descriptor_collides(apk_path: Path, descriptor: str) -> bool:
    needle = descriptor.encode()
    with zipfile.ZipFile(apk_path) as archive:
        for name in archive.namelist():
            if re.fullmatch(r"classes\d*\.dex", name):
                with archive.open(name) as handle:
                    if needle in handle.read():
                        return True
    return False


def _payload_identity(workspace: ProjectWorkspace, package_name: str, original_sha: str) -> tuple[str, str]:
    apk_path = next(workspace.input_dir.glob("*.apk"), None)
    if apk_path is None:
        raise ValueError("Original APK is unavailable")
    for salt in range(100):
        token = hashlib.sha256(f"{package_name}:{original_sha}:{salt}".encode()).hexdigest()[:12]
        prefix = f"noir/inject/v{PAYLOAD_VERSION}_{token}"
        if not _descriptor_collides(apk_path, f"L{prefix}/StartupProvider;"):
            return prefix, f"{package_name}.noir.{token}"
    raise ValueError("Unable to resolve a collision-free NOIR payload namespace")


def _is_launcher(component: ET.Element) -> bool:
    for intent_filter in component.findall("intent-filter"):
        actions = {item.get(ANDROID + "name", "") for item in intent_filter.findall("action")}
        categories = {item.get(ANDROID + "name", "") for item in intent_filter.findall("category")}
        if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in categories:
            return True
    return False


def _metadata(app: ET.Element, name: str, value: str) -> None:
    element = ET.SubElement(app, "meta-data")
    element.set(ANDROID + "name", name)
    element.set(ANDROID + "value", value)


def _manifest_content(
    manifest_path: Path,
    spec: OperationSpec,
    provider_class: str | None,
    authority: str | None,
) -> str:
    tree = parse(manifest_path)
    root = tree.getroot()
    app = root.find("application")
    if app is None:
        raise ValueError("Android manifest has no application element")

    if spec.app_name:
        app.set(ANDROID + "label", spec.app_name)
        for tag in ("activity", "activity-alias"):
            for component in app.findall(tag):
                if component.get(ANDROID + "enabled", "true").lower() != "false" and _is_launcher(component):
                    component.set(ANDROID + "label", spec.app_name)

    if provider_class and authority:
        for item in list(app.findall("meta-data")):
            if item.get(ANDROID + "name", "").startswith("noir.payload."):
                app.remove(item)
        for item in list(app.findall("provider")):
            if item.get(ANDROID + "name", "").startswith("noir.inject."):
                app.remove(item)
        provider = ET.SubElement(app, "provider")
        provider.set(ANDROID + "name", provider_class)
        provider.set(ANDROID + "authorities", authority)
        provider.set(ANDROID + "exported", "false")
        provider.set(ANDROID + "initOrder", "1999999999")
        _metadata(app, "noir.payload.version", PAYLOAD_VERSION)
        _metadata(app, "noir.payload.redirect.enabled", "true" if spec.launch_url else "false")
        _metadata(app, "noir.payload.redirect.url", spec.launch_url or "")
        _metadata(app, "noir.payload.toast.enabled", "true" if spec.toast_message else "false")
        _metadata(app, "noir.payload.toast.message", spec.toast_message or "")

    ET.indent(tree, space="    ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _startup_provider(prefix: str) -> str:
    cls = f"L{prefix}/StartupProvider;"
    lifecycle = f"L{prefix}/Lifecycle;"
    return f""".class public final {cls}
.super Landroid/content/ContentProvider;

.method public constructor <init>()V
    .locals 0
    invoke-direct {{p0}}, Landroid/content/ContentProvider;-><init>()V
    return-void
.end method

.method public onCreate()Z
    .locals 3
    invoke-virtual {{p0}}, Landroid/content/ContentProvider;->getContext()Landroid/content/Context;
    move-result-object v0
    invoke-virtual {{v0}}, Landroid/content/Context;->getApplicationContext()Landroid/content/Context;
    move-result-object v1
    instance-of v2, v1, Landroid/app/Application;
    if-eqz v2, :noir_done
    check-cast v1, Landroid/app/Application;
    new-instance v2, {lifecycle}
    invoke-direct {{v2, v0}}, {lifecycle}-><init>(Landroid/content/Context;)V
    invoke-virtual {{v1, v2}}, Landroid/app/Application;->registerActivityLifecycleCallbacks(Landroid/app/Application$ActivityLifecycleCallbacks;)V
    :noir_done
    const/4 v0, 0x1
    return v0
.end method

.method public query(Landroid/net/Uri;[Ljava/lang/String;Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;)Landroid/database/Cursor;
    .locals 1
    const/4 v0, 0x0
    return-object v0
.end method
.method public getType(Landroid/net/Uri;)Ljava/lang/String;
    .locals 1
    const/4 v0, 0x0
    return-object v0
.end method
.method public insert(Landroid/net/Uri;Landroid/content/ContentValues;)Landroid/net/Uri;
    .locals 1
    const/4 v0, 0x0
    return-object v0
.end method
.method public delete(Landroid/net/Uri;Ljava/lang/String;[Ljava/lang/String;)I
    .locals 1
    const/4 v0, 0x0
    return v0
.end method
.method public update(Landroid/net/Uri;Landroid/content/ContentValues;Ljava/lang/String;[Ljava/lang/String;)I
    .locals 1
    const/4 v0, 0x0
    return v0
.end method
"""


def _lifecycle(prefix: str) -> str:
    cls = f"L{prefix}/Lifecycle;"
    proxy = f"L{prefix}/CallbackProxy;"
    return f""".class public final {cls}
.super Ljava/lang/Object;
.implements Landroid/app/Application$ActivityLifecycleCallbacks;

.field private static redirected:Z
.field private final context:Landroid/content/Context;
.field private final wrapped:Ljava/util/WeakHashMap;

.method public constructor <init>(Landroid/content/Context;)V
    .locals 1
    invoke-direct {{p0}}, Ljava/lang/Object;-><init>()V
    iput-object p1, p0, {cls}->context:Landroid/content/Context;
    new-instance v0, Ljava/util/WeakHashMap;
    invoke-direct {{v0}}, Ljava/util/WeakHashMap;-><init>()V
    iput-object v0, p0, {cls}->wrapped:Ljava/util/WeakHashMap;
    return-void
.end method

.method private meta()Landroid/os/Bundle;
    .locals 4
    :try_start
    iget-object v0, p0, {cls}->context:Landroid/content/Context;
    invoke-virtual {{v0}}, Landroid/content/Context;->getPackageManager()Landroid/content/pm/PackageManager;
    move-result-object v1
    invoke-virtual {{v0}}, Landroid/content/Context;->getPackageName()Ljava/lang/String;
    move-result-object v2
    const/16 v3, 0x80
    invoke-virtual {{v1, v2, v3}}, Landroid/content/pm/PackageManager;->getApplicationInfo(Ljava/lang/String;I)Landroid/content/pm/ApplicationInfo;
    move-result-object v0
    iget-object v0, v0, Landroid/content/pm/ApplicationInfo;->metaData:Landroid/os/Bundle;
    return-object v0
    :try_end
    .catch Ljava/lang/Exception; {{:try_start .. :try_end}} :catch_all
    :catch_all
    const/4 v0, 0x0
    return-object v0
.end method

.method public onActivityResumed(Landroid/app/Activity;)V
    .locals 9
    invoke-direct {{p0}}, {cls}->meta()Landroid/os/Bundle;
    move-result-object v0
    if-eqz v0, :done
    sget-boolean v1, {cls}->redirected:Z
    if-nez v1, :toast
    const-string v1, "noir.payload.redirect.enabled"
    const/4 v2, 0x0
    invoke-virtual {{v0, v1, v2}}, Landroid/os/Bundle;->getBoolean(Ljava/lang/String;Z)Z
    move-result v1
    if-eqz v1, :toast
    const/4 v1, 0x1
    sput-boolean v1, {cls}->redirected:Z
    const-string v1, "noir.payload.redirect.url"
    invoke-virtual {{v0, v1}}, Landroid/os/Bundle;->getString(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v1
    if-eqz v1, :toast
    new-instance v2, Landroid/content/Intent;
    const-string v3, "android.intent.action.VIEW"
    invoke-static {{v1}}, Landroid/net/Uri;->parse(Ljava/lang/String;)Landroid/net/Uri;
    move-result-object v1
    invoke-direct {{v2, v3, v1}}, Landroid/content/Intent;-><init>(Ljava/lang/String;Landroid/net/Uri;)V
    invoke-virtual {{p1, v2}}, Landroid/app/Activity;->startActivity(Landroid/content/Intent;)V
    :toast
    const-string v1, "noir.payload.toast.enabled"
    const/4 v2, 0x0
    invoke-virtual {{v0, v1, v2}}, Landroid/os/Bundle;->getBoolean(Ljava/lang/String;Z)Z
    move-result v1
    if-eqz v1, :done
    iget-object v1, p0, {cls}->wrapped:Ljava/util/WeakHashMap;
    invoke-virtual {{v1, p1}}, Ljava/util/WeakHashMap;->containsKey(Ljava/lang/Object;)Z
    move-result v2
    if-nez v2, :done
    invoke-virtual {{p1}}, Landroid/app/Activity;->getWindow()Landroid/view/Window;
    move-result-object v2
    invoke-virtual {{v2}}, Landroid/view/Window;->getCallback()Landroid/view/Window$Callback;
    move-result-object v3
    if-eqz v3, :done
    const-string v4, "noir.payload.toast.message"
    invoke-virtual {{v0, v4}}, Landroid/os/Bundle;->getString(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v4
    new-instance v5, {proxy}
    invoke-direct {{v5, p1, v3, v4}}, {proxy}-><init>(Landroid/app/Activity;Landroid/view/Window$Callback;Ljava/lang/String;)V
    invoke-virtual {{v3}}, Ljava/lang/Object;->getClass()Ljava/lang/Class;
    move-result-object v6
    invoke-virtual {{v6}}, Ljava/lang/Class;->getClassLoader()Ljava/lang/ClassLoader;
    move-result-object v6
    const/4 v7, 0x1
    new-array v7, v7, [Ljava/lang/Class;
    const/4 v8, 0x0
    const-class v0, Landroid/view/Window$Callback;
    aput-object v0, v7, v8
    invoke-static {{v6, v7, v5}}, Ljava/lang/reflect/Proxy;->newProxyInstance(Ljava/lang/ClassLoader;[Ljava/lang/Class;Ljava/lang/reflect/InvocationHandler;)Ljava/lang/Object;
    move-result-object v0
    check-cast v0, Landroid/view/Window$Callback;
    invoke-virtual {{v2, v0}}, Landroid/view/Window;->setCallback(Landroid/view/Window$Callback;)V
    sget-object v0, Ljava/lang/Boolean;->TRUE:Ljava/lang/Boolean;
    invoke-virtual {{v1, p1, v0}}, Ljava/util/WeakHashMap;->put(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;
    :done
    return-void
.end method

.method public onActivityCreated(Landroid/app/Activity;Landroid/os/Bundle;)V
    .locals 0
    return-void
.end method
.method public onActivityStarted(Landroid/app/Activity;)V
    .locals 0
    return-void
.end method
.method public onActivityPaused(Landroid/app/Activity;)V
    .locals 0
    return-void
.end method
.method public onActivityStopped(Landroid/app/Activity;)V
    .locals 0
    return-void
.end method
.method public onActivitySaveInstanceState(Landroid/app/Activity;Landroid/os/Bundle;)V
    .locals 0
    return-void
.end method
.method public onActivityDestroyed(Landroid/app/Activity;)V
    .locals 0
    iget-object v0, p0, {cls}->wrapped:Ljava/util/WeakHashMap;
    invoke-virtual {{v0, p1}}, Ljava/util/WeakHashMap;->remove(Ljava/lang/Object;)Ljava/lang/Object;
    return-void
.end method
"""


def _callback_proxy(prefix: str) -> str:
    cls = f"L{prefix}/CallbackProxy;"
    return f""".class public final {cls}
.super Ljava/lang/Object;
.implements Ljava/lang/reflect/InvocationHandler;

.field private final activity:Landroid/app/Activity;
.field private final delegate:Landroid/view/Window$Callback;
.field private final message:Ljava/lang/String;

.method public constructor <init>(Landroid/app/Activity;Landroid/view/Window$Callback;Ljava/lang/String;)V
    .locals 0
    invoke-direct {{p0}}, Ljava/lang/Object;-><init>()V
    iput-object p1, p0, {cls}->activity:Landroid/app/Activity;
    iput-object p2, p0, {cls}->delegate:Landroid/view/Window$Callback;
    iput-object p3, p0, {cls}->message:Ljava/lang/String;
    return-void
.end method

.method public invoke(Ljava/lang/Object;Ljava/lang/reflect/Method;[Ljava/lang/Object;)Ljava/lang/Object;
    .locals 6
    invoke-virtual {{p2}}, Ljava/lang/reflect/Method;->getName()Ljava/lang/String;
    move-result-object v0
    const-string v1, "dispatchTouchEvent"
    invoke-virtual {{v1, v0}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :delegate
    if-eqz p3, :delegate
    array-length v0, p3
    if-eqz v0, :delegate
    const/4 v0, 0x0
    aget-object v0, p3, v0
    instance-of v1, v0, Landroid/view/MotionEvent;
    if-eqz v1, :delegate
    check-cast v0, Landroid/view/MotionEvent;
    invoke-virtual {{v0}}, Landroid/view/MotionEvent;->getActionMasked()I
    move-result v0
    const/4 v1, 0x1
    if-ne v0, v1, :delegate
    iget-object v2, p0, {cls}->activity:Landroid/app/Activity;
    iget-object v3, p0, {cls}->message:Ljava/lang/String;
    const/4 v4, 0x0
    invoke-static {{v2, v3, v4}}, Landroid/widget/Toast;->makeText(Landroid/content/Context;Ljava/lang/CharSequence;I)Landroid/widget/Toast;
    move-result-object v5
    invoke-virtual {{v5}}, Landroid/widget/Toast;->show()V
    :delegate
    iget-object v0, p0, {cls}->delegate:Landroid/view/Window$Callback;
    invoke-virtual {{p2, v0, p3}}, Ljava/lang/reflect/Method;->invoke(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;
    move-result-object v0
    return-object v0
.end method
"""


def create_deterministic_patch(
    workspace: ProjectWorkspace,
    plan: ChangePlan,
    spec: OperationSpec,
    original_sha: str,
) -> PatchSet:
    manifest_path = workspace.decoded_dir / "AndroidManifest.xml"
    tree = parse(manifest_path)
    root = tree.getroot()
    package_name = root.get("package", "")
    if not package_name:
        raise ValueError("Android manifest package is missing")

    operations: list[PatchOperation] = []
    provider_class = authority = prefix = None
    if spec.launch_url or spec.toast_message:
        app = root.find("application")
        existing = next(
            (
                item
                for item in (app.findall("provider") if app is not None else [])
                if item.get(ANDROID + "name", "").startswith("noir.inject.")
            ),
            None,
        )
        if existing is not None:
            provider_class = existing.get(ANDROID + "name")
            authority = existing.get(ANDROID + "authorities")
            prefix = provider_class.removesuffix(".StartupProvider").replace(".", "/")
        else:
            prefix, authority = _payload_identity(workspace, package_name, original_sha)
            provider_class = prefix.replace("/", ".") + ".StartupProvider"
    updated_manifest = _manifest_content(manifest_path, spec, provider_class, authority)
    if manifest_path.read_text(encoding="utf-8") != updated_manifest:
        operations.append(
            PatchOperation(
                relative_path="AndroidManifest.xml",
                operation=PatchOperationType.REPLACE_FILE,
                expected_preimage_hash=compute_file_hash(manifest_path),
                new_content=updated_manifest,
                affected_scope="application manifest",
            )
        )

    if prefix:
        existing_provider = next(
            workspace.decoded_dir.glob(f"smali*/{prefix}/StartupProvider.smali"), None
        )
        dex_dir = (
            str(existing_provider.relative_to(workspace.decoded_dir)).split("/", 1)[0]
            if existing_provider
            else _next_dex_dir(workspace.decoded_dir)
        )
        for name, content in (
            ("StartupProvider", _startup_provider(prefix)),
            ("Lifecycle", _lifecycle(prefix)),
            ("CallbackProxy", _callback_proxy(prefix)),
        ):
            relative = f"{dex_dir}/{prefix}/{name}.smali"
            target = workspace.decoded_dir / relative
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                operations.append(
                    PatchOperation(
                        relative_path=relative,
                        operation=(PatchOperationType.REPLACE_FILE if target.exists() else PatchOperationType.CREATE_FILE),
                        expected_preimage_hash=compute_file_hash(target) if target.exists() else None,
                        expected_absent=not target.exists(),
                        new_content=content,
                        affected_scope="isolated NOIR payload",
                    )
                )

    return PatchSet(
        plan_id=plan.plan_id,
        project_id=plan.project_id,
        workspace_revision=plan.workspace_revision,
        provenance=Provenance.SYSTEM_GENERATED,
        execution_mode="deterministic",
        detected_intents=spec.intents,
        operations=operations,
    )
