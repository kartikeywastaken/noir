"""Deterministic intent-based file routing subsystem.

Maps user requests and APK runtime types directly to relevant workspace files for
AI planning, bypassing expensive AI discovery calls when intents match and falling
back to AI discovery when none do.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Callable

from noir.domain.models import AnalysisResult
from noir.infrastructure.ai.context import AiContextTools

logger = logging.getLogger(__name__)

MAX_FILES_PER_INTENT = 8
MAX_FILES_TOTAL = 20
ANDROID_NS = "http://schemas.android.com/apk/res/android"


@dataclass
class IntentRouteResult:
    """Result of intent routing."""

    matched_intents: list[str] = field(default_factory=list)
    seen_files: dict[str, str] = field(default_factory=dict)
    binary_inspections: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = ""


@dataclass
class IntentRule:
    """One intent routing rule."""

    intent_id: str
    trigger_patterns: list[re.Pattern]
    apk_types: set[str]
    file_selector: Callable[[AiContextTools, AnalysisResult | None], list[str]]


# ── File selection helpers ──────────────────────────────────────────


def _get_workspace_paths(context_tools: AiContextTools) -> list[str]:
    """Return all relative paths in the workspace, normalized with forward slashes."""
    try:
        paths = context_tools._workspace_paths()
        return [p.replace("\\", "/") for p in paths]
    except Exception:
        return []


def _path_exists(context_tools: AiContextTools, all_paths_set: set[str], rel_path: str) -> bool:
    """Check if relative path exists either in indexed paths or in workspace directory."""
    if rel_path in all_paths_set:
        return True
    try:
        return context_tools.workspace.safe_path(rel_path).is_file()
    except Exception:
        return False


def _get_xml_attr(element: ET.Element, attr_name: str) -> str | None:
    """Retrieve attribute value handling standard Android namespace, unprefixed, or arbitrary namespace prefix."""
    # 1. Standard Android namespace
    val = element.get(f"{{{ANDROID_NS}}}{attr_name}")
    if val is not None:
        return val
    # 2. Plain attribute without namespace
    val = element.get(attr_name)
    if val is not None:
        return val
    # 3. Any namespace ending in }attr_name
    for key, v in element.attrib.items():
        if key.endswith(f"}}{attr_name}"):
            return v
    return None


def _extract_manifest_info(context_tools: AiContextTools) -> dict[str, Any]:
    """Safely extract package, launcher activities, receivers, services, and label from AndroidManifest.xml."""
    info: dict[str, Any] = {
        "package_name": "",
        "launcher_activities": [],
        "receivers": [],
        "services": [],
        "label_ref": None,
    }
    try:
        manifest_file = context_tools.workspace.safe_path("AndroidManifest.xml")
        if not manifest_file.is_file():
            return info
        from noir.security.xml import fromstring, parse

        try:
            tree = parse(manifest_file)
            root = tree.getroot()
        except Exception:
            # Fallback for manifests with undeclared namespace prefixes (e.g. android: without xmlns:android)
            raw = manifest_file.read_text(encoding="utf-8", errors="replace")
            prefixes = set(re.findall(r"<\s*([a-zA-Z0-9_-]+):", raw))
            prefixes.update(re.findall(r"\s+([a-zA-Z0-9_-]+):[a-zA-Z0-9_-]+\s*=", raw))
            declared = set(re.findall(r"xmlns:([a-zA-Z0-9_-]+)\s*=", raw))
            fixed = raw
            for prefix in prefixes - declared:
                uri = "http://schemas.android.com/apk/res/android" if prefix == "android" else f"urn:unknown:{prefix}"
                fixed = re.sub(r"(<\s*[a-zA-Z0-9_:-]+)", f'\\1 xmlns:{prefix}="{uri}"', fixed, count=1)
            root = fromstring(fixed)

        pkg = root.get("package") or _get_xml_attr(root, "package") or ""
        info["package_name"] = pkg

        app = root.find("{*}application")
        if app is not None:
            label = _get_xml_attr(app, "label")
            if label and label.startswith("@string/"):
                info["label_ref"] = label.split("/", 1)[1]

            # Parse activities and activity-aliases
            for tag in ("activity", "activity-alias"):
                for elem in app.findall(f"{{*}}{tag}"):
                    name = _get_xml_attr(elem, "name") or ""
                    target = _get_xml_attr(elem, "targetActivity") or ""

                    is_launcher = False
                    for if_elem in elem.findall("{*}intent-filter"):
                        has_main = any(
                            (_get_xml_attr(act, "name") or "").strip().endswith(".MAIN")
                            or (_get_xml_attr(act, "name") or "").strip() == "MAIN"
                            for act in if_elem.findall("{*}action")
                        )
                        has_launcher = any(
                            (_get_xml_attr(cat, "name") or "").strip().endswith(".LAUNCHER")
                            or (_get_xml_attr(cat, "name") or "").strip() == "LAUNCHER"
                            for cat in if_elem.findall("{*}category")
                        )
                        if has_main and has_launcher:
                            is_launcher = True
                            break

                    if is_launcher:
                        # If alias, targetActivity is the actual class
                        if target and target not in info["launcher_activities"]:
                            info["launcher_activities"].append(target)
                        if name and name not in info["launcher_activities"]:
                            info["launcher_activities"].append(name)

            for rec in app.findall("{*}receiver"):
                name = _get_xml_attr(rec, "name")
                if name and name not in info["receivers"]:
                    info["receivers"].append(name)

            for srv in app.findall("{*}service"):
                name = _get_xml_attr(srv, "name")
                if name and name not in info["services"]:
                    info["services"].append(name)
    except Exception:
        pass
    return info


def _smali_sort_key(target_name: str | None, path: str) -> tuple[int, int, str]:
    """Sort smali paths prioritizing primary/outer classes over anonymous inner classes ($)."""
    p_lower = path.lower()
    is_inner = "$" in path
    if target_name:
        simple = target_name.split(".")[-1].lower() + ".smali"
        target_path = target_name.lstrip(".").replace(".", "/").lower() + ".smali"
        target_base = target_name.lstrip(".").replace(".", "/").lower()
        simple_base = target_name.split(".")[-1].lower()

        # Exact full package match (non-inner) -> highest priority 0, 0
        if (p_lower.endswith("/" + target_path) or p_lower == target_path or target_path in p_lower) and not is_inner:
            return (0, 0, p_lower)
        # Simple name match (non-inner) -> priority 0, 1
        if (p_lower.endswith("/" + simple) or p_lower == simple) and not is_inner:
            return (0, 1, p_lower)
        # Inner class of target activity -> priority 1
        if (target_base in p_lower or f"/{simple_base}$" in p_lower or p_lower.startswith(f"{simple_base}$")) and is_inner:
            return (1, len(path), p_lower)

    if (p_lower.endswith("/mainactivity.smali") or p_lower == "mainactivity.smali") and not is_inner:
        return (2, 0, p_lower)
    if not is_inner:
        return (3, 0, p_lower)
    return (4, len(path), p_lower)


def _find_launcher_smali(
    context_tools: AiContextTools,
    analysis: AnalysisResult | None,
    all_paths: list[str],
) -> list[str]:
    """Find launcher activity smali files, MainActivity fallbacks, and base activities."""
    results: list[str] = []
    launcher_names: list[str] = []
    package_name = ""

    if analysis:
        package_name = analysis.package_name or ""
        if analysis.components:
            # Prioritize non-alias launcher activities first
            for component in sorted(analysis.components, key=lambda c: (c.is_alias, not c.is_launcher)):
                if component.is_launcher and component.component_type == "activity":
                    if component.name not in launcher_names:
                        launcher_names.append(component.name)

    # Fallback to AndroidManifest.xml if no launcher found from analysis
    manifest_info = _extract_manifest_info(context_tools)
    if not package_name and manifest_info.get("package_name"):
        package_name = manifest_info["package_name"]
    if not launcher_names:
        for l_act in manifest_info.get("launcher_activities", []):
            if l_act not in launcher_names:
                launcher_names.append(l_act)

    # Resolve relative class names with package_name (e.g. .MainActivity -> com.example.MainActivity)
    expanded_launcher_names: list[str] = []
    for l_name in launcher_names:
        if l_name.startswith(".") and package_name:
            full = f"{package_name}{l_name}"
            if full not in expanded_launcher_names:
                expanded_launcher_names.append(full)
        elif "." not in l_name and package_name:
            full = f"{package_name}.{l_name}"
            if full not in expanded_launcher_names:
                expanded_launcher_names.append(full)
        if l_name not in expanded_launcher_names:
            expanded_launcher_names.append(l_name)

    primary_launcher = expanded_launcher_names[0] if expanded_launcher_names else (launcher_names[0] if launcher_names else None)

    # 1. Match by launcher activity names
    for l_name in expanded_launcher_names:
        l_frag = l_name.lstrip(".").replace(".", "/").lower()
        simple_name = l_name.split(".")[-1].lower() + ".smali"
        for p in all_paths:
            p_lower = p.lower()
            if p_lower.endswith(".smali"):
                matches_frag = (
                    ("/" + l_frag + ".smali") in p_lower
                    or ("/" + l_frag + "/") in p_lower
                    or (("/" in l_frag) and l_frag in p_lower)
                )
                if (
                    matches_frag
                    or p_lower.endswith("/" + simple_name)
                    or p_lower == simple_name
                ):
                    if p not in results:
                        results.append(p)
        if analysis and analysis.smali_classes:
            for cls in analysis.smali_classes:
                cls_lower = cls.file_path.lower()
                if cls_lower.endswith(".smali"):
                    matches_frag = (
                        ("/" + l_frag + ".smali") in cls_lower
                        or ("/" + l_frag + "/") in cls_lower
                        or (("/" in l_frag) and l_frag in cls_lower)
                    )
                    if (
                        matches_frag
                        or cls_lower.endswith("/" + simple_name)
                        or cls_lower == simple_name
                    ) and cls.file_path not in results:
                        results.append(cls.file_path)

    # 2. Match exact or path-ending MainActivity.smali
    for p in all_paths:
        p_lower = p.lower()
        if p_lower.endswith("/mainactivity.smali") or p_lower == "mainactivity.smali":
            if p not in results:
                results.append(p)
    if analysis and analysis.smali_classes:
        for cls in analysis.smali_classes:
            cls_lower = cls.file_path.lower()
            if (cls_lower.endswith("/mainactivity.smali") or cls_lower == "mainactivity.smali") and cls.file_path not in results:
                results.append(cls.file_path)

    # 3. Known activity fallbacks
    for p in all_paths:
        p_lower = p.lower()
        if p_lower.endswith(".smali") and any(
            kw in p_lower
            for kw in (
                "mainactivity",
                "baseactivity",
                "homeactivity",
                "splashactivity",
                "launchactivity",
            )
        ):
            if p not in results:
                results.append(p)

    # Sort results prioritizing primary outer classes over inner anonymous classes
    results.sort(key=lambda p: _smali_sort_key(primary_launcher, p))
    return results


def _find_network_smali(
    context_tools: AiContextTools,
    analysis: AnalysisResult | None,
    all_paths: list[str],
) -> list[str]:
    """Find network client and HTTP smali files."""
    results: list[str] = []
    keywords = (
        "okhttp",
        "retrofit",
        "httpclient",
        "httpurlconnection",
        "networkclient",
        "apiservice",
        "apiclient",
        "webviewclient",
        "urlutil",
        "network",
        "http",
    )
    package_prefix = ""
    if analysis and analysis.package_name:
        package_prefix = analysis.package_name.replace(".", "/").lower()

    for p in all_paths:
        p_lower = p.lower()
        if p_lower.endswith(".smali") and any(kw in p_lower for kw in keywords):
            if p not in results:
                results.append(p)

    if analysis and analysis.smali_classes:
        for cls in analysis.smali_classes:
            desc_lower = cls.descriptor.lower()
            if any(kw in desc_lower for kw in keywords) and cls.file_path not in results:
                results.append(cls.file_path)

    # Prioritize: 0. Application package smali, 1. Entry clients, 2. Other smali, 3. Inner classes
    def _network_sort_key(p: str) -> tuple[int, int, str]:
        p_lower = p.lower()
        is_inner = 1 if "$" in p else 0
        is_app = 0 if package_prefix and package_prefix in p_lower else 1
        is_client = 0 if any(k in p_lower for k in ("client", "service", "retrofit", "httpurl")) else 1
        return (is_inner, is_app, is_client, len(p), p_lower)

    results.sort(key=_network_sort_key)
    return results


def _find_receiver_service_smali(
    context_tools: AiContextTools,
    analysis: AnalysisResult | None,
    all_paths: list[str],
) -> list[str]:
    """Find smali files corresponding to broadcast receivers and background services."""
    results: list[str] = []
    comp_names: list[str] = []

    if analysis and analysis.components:
        for comp in analysis.components:
            if comp.component_type in ("receiver", "service"):
                comp_names.append(comp.name)

    if not comp_names:
        manifest_info = _extract_manifest_info(context_tools)
        for r_name in manifest_info.get("receivers", []):
            if r_name not in comp_names:
                comp_names.append(r_name)
        for s_name in manifest_info.get("services", []):
            if s_name not in comp_names:
                comp_names.append(s_name)

    manifest_matches: set[str] = set()
    for name in comp_names:
        frag = name.lstrip(".").replace(".", "/").lower()
        simple = name.split(".")[-1].lower() + ".smali"
        for p in all_paths:
            p_lower = p.lower()
            if p_lower.endswith(".smali") and (frag in p_lower or p_lower.endswith("/" + simple) or p_lower == simple):
                manifest_matches.add(p)
                if p not in results:
                    results.append(p)
        if analysis and analysis.smali_classes:
            for cls in analysis.smali_classes:
                cls_lower = cls.file_path.lower()
                if (frag in cls_lower or cls_lower.endswith("/" + simple) or cls_lower == simple) and cls.file_path not in results:
                    manifest_matches.add(cls.file_path)
                    results.append(cls.file_path)

    for p in all_paths:
        p_lower = p.lower()
        if p_lower.endswith(".smali") and ("receiver" in p_lower or "service" in p_lower):
            if p not in results:
                results.append(p)

    # Prioritize manifest-matched components over generic keyword matches, then outer classes over inner anonymous classes
    results.sort(key=lambda p: (0 if p in manifest_matches else 1, 1 if "$" in p else 0, len(p), p.lower()))
    return results


# ── Intent File Selectors ───────────────────────────────────────────


def _select_app_name(context_tools: AiContextTools, analysis: AnalysisResult | None) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    all_set = set(all_paths)
    candidates: list[str] = []
    if _path_exists(context_tools, all_set, "AndroidManifest.xml"):
        candidates.append("AndroidManifest.xml")

    # Primary strings.xml
    if _path_exists(context_tools, all_set, "res/values/strings.xml"):
        candidates.append("res/values/strings.xml")
    if _path_exists(context_tools, all_set, "resources/values/strings.xml") and "resources/values/strings.xml" not in candidates:
        candidates.append("resources/values/strings.xml")

    # Additional res/values*/strings.xml and resources/values*/strings.xml
    other_strings: list[str] = []
    for p in all_paths:
        if re.match(r"^(?:res|resources)/values[^/]*/strings\.xml$", p, re.IGNORECASE) and p not in candidates:
            other_strings.append(p)

    def _strings_sort_key(p: str) -> tuple[int, str]:
        p_lower = p.lower()
        # Base values/strings.xml without qualifiers has highest priority
        if p_lower.endswith("values/strings.xml"):
            return (0, p_lower)
        # English qualifiers prioritized right after default
        if "/values-en" in p_lower:
            return (1, p_lower)
        # Complex BCP-47 / language qualifiers
        return (2, p_lower)

    other_strings.sort(key=_strings_sort_key)
    for p in other_strings:
        if p not in candidates:
            candidates.append(p)
    return candidates


def _select_toast_flash(
    context_tools: AiContextTools, analysis: AnalysisResult | None
) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    return _find_launcher_smali(context_tools, analysis, all_paths)


def _select_network_ping(
    context_tools: AiContextTools, analysis: AnalysisResult | None
) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    all_set = set(all_paths)
    candidates: list[str] = []
    if _path_exists(context_tools, all_set, "AndroidManifest.xml"):
        candidates.append("AndroidManifest.xml")

    launcher_files = _find_launcher_smali(context_tools, analysis, all_paths)
    network_files = _find_network_smali(context_tools, analysis, all_paths)

    # Add primary launcher activity first (non-inner, top 1-2)
    primary_launchers = [p for p in launcher_files if "$" not in p][:2]
    for p in primary_launchers:
        if p not in candidates:
            candidates.append(p)

    # Crucial: add network client smali files before inner launcher classes
    for p in network_files:
        if p not in candidates:
            candidates.append(p)

    # Add remaining launcher files (e.g. inner classes) if space permits
    for p in launcher_files:
        if p not in candidates:
            candidates.append(p)

    return candidates


def _select_ui_layout(context_tools: AiContextTools, analysis: AnalysisResult | None) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    all_set = set(all_paths)
    candidates: list[str] = []

    layouts = [p for p in all_paths if re.match(r"^(?:res|resources)/layout[^/]*/.*\.xml$", p, re.IGNORECASE)]

    def layout_sort_key(p: str) -> tuple[int, str]:
        p_lower = p.lower()
        if "activity_main" in p_lower:
            return (0, p)
        if "main" in p_lower:
            return (1, p)
        return (2, p)

    sorted_layouts = sorted(layouts, key=layout_sort_key)

    # Add primary layouts first (up to 4)
    for p in sorted_layouts[:4]:
        if p not in candidates:
            candidates.append(p)

    # Strings and colors are crucial for UI layout (button text, theme colors)
    if (
        _path_exists(context_tools, all_set, "res/values/strings.xml")
        and "res/values/strings.xml" not in candidates
    ):
        candidates.append("res/values/strings.xml")
    if (
        _path_exists(context_tools, all_set, "res/values/colors.xml")
        and "res/values/colors.xml" not in candidates
    ):
        candidates.append("res/values/colors.xml")

    # Add remaining layouts
    for p in sorted_layouts[4:]:
        if p not in candidates:
            candidates.append(p)

    for p in sorted(all_paths):
        if re.match(r"^(?:res|resources)/values[^/]*/strings\.xml$", p, re.IGNORECASE) and p not in candidates:
            candidates.append(p)

    return candidates


def _select_permission(context_tools: AiContextTools, analysis: AnalysisResult | None) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    all_set = set(all_paths)
    candidates: list[str] = []
    if _path_exists(context_tools, all_set, "AndroidManifest.xml"):
        candidates.append("AndroidManifest.xml")
    return candidates


def _select_receiver_service(
    context_tools: AiContextTools, analysis: AnalysisResult | None
) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    all_set = set(all_paths)
    candidates: list[str] = []
    if _path_exists(context_tools, all_set, "AndroidManifest.xml"):
        candidates.append("AndroidManifest.xml")
    for p in _find_receiver_service_smali(context_tools, analysis, all_paths):
        if p not in candidates:
            candidates.append(p)
    return candidates


def _select_react_native_js(
    context_tools: AiContextTools, analysis: AnalysisResult | None
) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    all_set = set(all_paths)
    candidates: list[str] = []
    for p in all_paths:
        p_lower = p.lower()
        if (
            p_lower.endswith("index.android.bundle")
            or "index.android.bundle" in p_lower
            or p_lower.endswith("index.bundle")
            or p_lower.endswith("main.jsbundle")
        ):
            if p not in candidates:
                candidates.append(p)
    for p in all_paths:
        if "libhermes" in p.lower() and p.lower().endswith(".so"):
            if p not in candidates:
                candidates.append(p)
    if (
        _path_exists(context_tools, all_set, "AndroidManifest.xml")
        and "AndroidManifest.xml" not in candidates
    ):
        candidates.append("AndroidManifest.xml")
    return candidates


def _select_flutter_dart(
    context_tools: AiContextTools, analysis: AnalysisResult | None
) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    candidates: list[str] = []
    for p in all_paths:
        if p.lower().endswith("libapp.so"):
            if p not in candidates:
                candidates.append(p)
    for p in all_paths:
        if p.lower().endswith("libflutter.so"):
            if p not in candidates:
                candidates.append(p)
    for p in all_paths:
        if p.lower().startswith("assets/flutter_assets/"):
            if p not in candidates:
                candidates.append(p)
    return candidates


def _select_unity_mono(context_tools: AiContextTools, analysis: AnalysisResult | None) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    candidates: list[str] = []
    for p in all_paths:
        if p.lower().endswith("assembly-csharp.dll"):
            if p not in candidates:
                candidates.append(p)
    if analysis and analysis.managed_assemblies:
        for p in analysis.managed_assemblies:
            if p not in candidates:
                candidates.append(p)
    for p in all_paths:
        if "assets/" in p.lower() and p.lower().endswith(".dll"):
            if p not in candidates:
                candidates.append(p)
    for p in all_paths:
        p_lower = p.lower()
        if (
            ("libmono" in p_lower or "libmonosgen" in p_lower)
            and "libmonodroid" not in p_lower
            and p_lower.endswith(".so")
        ):
            if p not in candidates:
                candidates.append(p)
    return candidates



def _select_unity_il2cpp(
    context_tools: AiContextTools, analysis: AnalysisResult | None
) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    candidates: list[str] = []
    for p in all_paths:
        if p.lower().endswith("libil2cpp.so"):
            if p not in candidates:
                candidates.append(p)
    for p in all_paths:
        if p.lower().endswith("global-metadata.dat"):
            if p not in candidates:
                candidates.append(p)
    if analysis and analysis.il2cpp_metadata_files:
        for p in analysis.il2cpp_metadata_files:
            if p not in candidates:
                candidates.append(p)
    return candidates


def _select_native_elf(context_tools: AiContextTools, analysis: AnalysisResult | None) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    candidates: list[str] = []
    if analysis and analysis.native_libs:
        for abi in analysis.native_libs:
            for lib in abi.libraries:
                rel = f"lib/{abi.abi}/{lib}"
                if rel in all_paths and rel not in candidates:
                    candidates.append(rel)
    for p in all_paths:
        if p.lower().startswith("lib/") and p.lower().endswith(".so"):
            if p not in candidates:
                candidates.append(p)
    return candidates


def _select_xamarin_dotnet(
    context_tools: AiContextTools, analysis: AnalysisResult | None
) -> list[str]:
    all_paths = _get_workspace_paths(context_tools)
    candidates: list[str] = []
    for p in all_paths:
        p_lower = p.lower()
        if (
            (
                "assemblies/" in p_lower
                or p_lower.endswith("mono.android.dll")
                or (analysis and analysis.managed_assemblies and p in analysis.managed_assemblies)
            )
            and p_lower.endswith(".dll")
        ) or (
            p_lower.endswith("libmonodroid.so")
            or p_lower.endswith("libxamarin-app.so")
        ):
            if p not in candidates:
                candidates.append(p)
    return candidates


# ── Runtime Detection ───────────────────────────────────────────────


def scan_directory_markers(context_tools: AiContextTools) -> set[str]:
    """Scan workspace paths and directory structure to infer APK runtime types."""
    paths = _get_workspace_paths(context_tools)
    detected: set[str] = set()
    for path in paths:
        p_lower = path.lower()
        if p_lower.endswith(".smali") or p_lower == "classes.dex" or p_lower.startswith("smali/"):
            detected.add("dalvik")
        if (
            p_lower.endswith("libflutter.so")
            or p_lower.endswith("libapp.so")
            or "assets/flutter_assets/" in p_lower
        ):
            detected.add("flutter")
        if (
            p_lower.endswith("index.android.bundle")
            or p_lower.endswith("index.bundle")
            or p_lower.endswith("main.jsbundle")
            or "libreactnativejni" in p_lower
        ):
            detected.add("react_native")
        if "libhermes" in p_lower:
            detected.add("hermes")
            detected.add("react_native")
        if (
            p_lower.endswith("assembly-csharp.dll")
            or ("assets/bin/data/managed" in p_lower and p_lower.endswith(".dll"))
            or (
                ("libmono" in p_lower or "libmonosgen" in p_lower)
                and "libmonodroid" not in p_lower
                and p_lower.endswith(".so")
            )
        ):
            detected.add("mono")
        if p_lower.endswith("libil2cpp.so") or p_lower.endswith("global-metadata.dat"):
            detected.add("il2cpp")
        if (
            "assemblies/" in p_lower
            or "libmonodroid" in p_lower
            or "libxamarin" in p_lower
            or p_lower.endswith("mono.android.dll")
        ):
            detected.add("xamarin")
        if p_lower.startswith("lib/") and p_lower.endswith(".so"):
            detected.add("native")

    try:
        decoded = context_tools.workspace.decoded_dir
        if decoded.is_dir():
            if (decoded / "assets" / "flutter_assets").is_dir():
                detected.add("flutter")
            if (decoded / "assemblies").is_dir():
                detected.add("xamarin")
            managed_dir = decoded / "assets" / "bin" / "Data" / "Managed"
            if managed_dir.is_dir() and any(managed_dir.glob("*.dll")):
                detected.add("mono")
            if (decoded / "smali").is_dir():
                detected.add("dalvik")
            if (decoded / "lib").is_dir() and any((decoded / "lib").glob("*/*.so")):
                detected.add("native")
    except Exception:
        pass

    return detected


def detect_runtimes(
    context_tools: AiContextTools, analysis: AnalysisResult | None = None
) -> set[str]:
    """Detect APK runtimes using analysis metadata first, falling back to directory markers."""
    if analysis and analysis.runtimes:
        return {r.lower() for r in analysis.runtimes}
    if analysis and analysis.runtime and analysis.runtime not in ("", "unknown"):
        return {analysis.runtime.lower()}
    return scan_directory_markers(context_tools)


# ── Registry of 12 Intent Rules ─────────────────────────────────────


INTENT_RULES: list[IntentRule] = [
    IntentRule(
        intent_id="app_name",
        trigger_patterns=[
            re.compile(
                r"\b(label|rename|app['’]?s?[ -]?name|application['’]?s?[ -]?name|display[ -]?name|app['’]?s?[ -]?title|application['’]?s?[ -]?title)\b",
                re.IGNORECASE,
            ),
            re.compile(r"\b(?:name|title|label) of (?:the )?(?:app|application)\b", re.IGNORECASE),
            re.compile(
                r"\b(?:change|update|modify|set) (?:the )?(?:app|application)(?:'s|’s)? (?:name|title|label)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:change|update|modify|set) (?:the )?(?:name|title|label)\b",
                re.IGNORECASE,
            ),
        ],
        apk_types=set(),
        file_selector=_select_app_name,
    ),
    IntentRule(
        intent_id="toast_flash",
        trigger_patterns=[
            re.compile(r"\b(toast|flash)\b", re.IGNORECASE),
            re.compile(
                r"\b(message|popup|snackbar|banner|alert)\b.*?\b(interact|interaction|click|tap|touch|press|button|ui|screen|every|whenever)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"\b(interact|interaction|click|tap|touch|press|button|ui|screen|every|whenever)\b.*?\b(message|popup|snackbar|banner|alert)\b",
                re.IGNORECASE | re.DOTALL,
            ),
        ],
        apk_types={"dalvik"},
        file_selector=_select_toast_flash,
    ),
    IntentRule(
        intent_id="network_ping",
        trigger_patterns=[
            re.compile(r"\bping\b", re.IGNORECASE),
            re.compile(
                r"\b(send|post|get|fetch|connect|request|http|https|endpoint|api|telemetry|exfil|transmit)\b.*?\b(launch|start|startup|open|create|init|server|endpoint|url|backend)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"\b(launch|start|startup|open|boot)\b.*?\b(send|post|get|fetch|connect|request|http|https|endpoint|ping|api|call)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(r"https?://\S+", re.IGNORECASE),
        ],
        apk_types={"dalvik"},
        file_selector=_select_network_ping,
    ),
    IntentRule(
        intent_id="ui_layout",
        trigger_patterns=[
            re.compile(r"\b(ui[ -]?layout|layout[ -]?xml)\b", re.IGNORECASE),
            re.compile(
                r"\b(button|text|color|layout|view|screen)\b.*?\b(change|modify|add|update|edit|style|set|replace|hide|show|customize|remove|delete)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"\b(change|modify|add|update|edit|style|set|replace|hide|show|customize|remove|delete)\b.*?\b(button|text|color|layout|view|screen)\b",
                re.IGNORECASE | re.DOTALL,
            ),
        ],
        apk_types={"dalvik"},
        file_selector=_select_ui_layout,
    ),
    IntentRule(
        intent_id="permission",
        trigger_patterns=[
            re.compile(r"\bpermissions?\b", re.IGNORECASE),
            re.compile(
                r"\b(add|remove|grant|revoke|allow|deny|request|check)\b.*?\bpermissions?\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"\bpermissions?\b.*?\b(add|remove|grant|revoke|allow|deny|request|check)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(r"\bandroid\.permission\.[A-Z0-9_]+\b", re.IGNORECASE),
        ],
        apk_types=set(),
        file_selector=_select_permission,
    ),
    IntentRule(
        intent_id="receiver_service",
        trigger_patterns=[
            re.compile(
                r"\b(broadcast[ -]?receiver|background[ -]?service|intent[ -]?service|job[ -]?service)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(receiver|service|broadcast)\b.*?\b(add|modify|register|create|update|change|start|hook|implement|remove|delete|unregister)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"\b(add|modify|register|create|update|change|start|hook|implement|remove|delete|unregister)\b.*?\b(receiver|service|broadcast)\b",
                re.IGNORECASE | re.DOTALL,
            ),
        ],
        apk_types={"dalvik"},
        file_selector=_select_receiver_service,
    ),
    IntentRule(
        intent_id="react_native_js",
        trigger_patterns=[
            re.compile(r"\b(react[ -]?native|hermes|js[ -]?bundle|javascript)\b", re.IGNORECASE),
            re.compile(r"\b(index\.android\.bundle|libhermes\.so)\b", re.IGNORECASE),
        ],
        apk_types={"react_native", "hermes", "reactnative"},
        file_selector=_select_react_native_js,
    ),
    IntentRule(
        intent_id="flutter_dart",
        trigger_patterns=[
            re.compile(r"\b(flutter|dart|libapp|libflutter)\b", re.IGNORECASE),
        ],
        apk_types={"flutter", "flutter_dart", "dart"},
        file_selector=_select_flutter_dart,
    ),
    IntentRule(
        intent_id="unity_mono",
        trigger_patterns=[
            re.compile(
                r"\b(assembly-csharp(?:\.dll)?|csharp|libmono|managed\s+(?:dll|assembl(?:y|ies)))\b|(?:\bc#(?!\w))",
                re.IGNORECASE,
            ),
            re.compile(
                r"\bunity\b.*?(?:\b(mono|csharp|assembly|script|dll)\b|(?:\bc#(?!\w)))",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"(?:\b(mono|csharp|assembly|script|dll)\b|(?:\bc#(?!\w))).*?\bunity\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(r"\bunity\b", re.IGNORECASE),
        ],
        apk_types={"mono", "unity_mono", "unity"},
        file_selector=_select_unity_mono,
    ),
    IntentRule(
        intent_id="unity_il2cpp",
        trigger_patterns=[
            re.compile(
                r"\b(il2cpp|libil2cpp(?:\.so)?|global-metadata(?:\.dat)?)\b",
                re.IGNORECASE,
            ),
            re.compile(r"\bunity\b.*?\b(il2cpp|libil2cpp|metadata)\b", re.IGNORECASE | re.DOTALL),
            re.compile(r"\b(il2cpp|libil2cpp|metadata)\b.*?\bunity\b", re.IGNORECASE | re.DOTALL),
            re.compile(r"\bunity\b", re.IGNORECASE),
        ],
        apk_types={"il2cpp", "unity_il2cpp", "unity"},
        file_selector=_select_unity_il2cpp,
    ),
    IntentRule(
        intent_id="native_elf",
        trigger_patterns=[
            re.compile(
                r"\b(native[ -]?lib(?:rary)?|jni|elf|shared library|shared object)\b|(?<!\w)\.so\b|(?:\bc\+\+(?!\w))",
                re.IGNORECASE,
            ),
            re.compile(
                r"\bnative\b.*?\b(code|method|function|call|library|binary|hook|patch|implementation)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(r"\b(hook|patch|call)\b.*?\bnative\b", re.IGNORECASE | re.DOTALL),
        ],
        apk_types={"native", "native_only", "native_elf"},
        file_selector=_select_native_elf,
    ),
    IntentRule(
        intent_id="xamarin_dotnet",
        trigger_patterns=[
            re.compile(r"\b(xamarin|dotnet)\b|(?<!\w)\.net\b", re.IGNORECASE),
            re.compile(
                r"\bxamarin\b.*?(?:\b(dll|assembly|mono|dotnet)\b|(?<!\w)\.net\b)",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(r"\b(assemblies|assembly|mono\.android(?:\.dll)?|assemblies/\S+\.dll)\b", re.IGNORECASE),
        ],
        apk_types={"xamarin", "xamarin_dotnet", "dotnet"},
        file_selector=_select_xamarin_dotnet,
    ),
]


# ── Main IntentRouter Class ─────────────────────────────────────────


class IntentRouter:
    """Deterministic intent-based file routing engine."""

    def __init__(self, rules: list[IntentRule] | None = None):
        self.rules = rules or INTENT_RULES

    def detect_runtimes(
        self, context_tools: AiContextTools, analysis: AnalysisResult | None = None
    ) -> set[str]:
        return detect_runtimes(context_tools, analysis)

    def route(
        self,
        user_request: str,
        context_tools: AiContextTools,
        analysis: AnalysisResult | None = None,
    ) -> IntentRouteResult:
        """Route user request to workspace files based on detected intents and APK runtimes."""
        if not user_request or not isinstance(user_request, str):
            logger.warning("No intent matched for empty request; falling through to AI discovery")
            return IntentRouteResult(
                matched_intents=[],
                seen_files={},
                binary_inspections={},
                stop_reason="no_intent_matched",
            )

        app_runtimes = self.detect_runtimes(context_tools, analysis)

        matched_rules: list[IntentRule] = []
        for rule in self.rules:
            # Check APK type scoping: empty apk_types matches all types
            if rule.apk_types and not bool(rule.apk_types & app_runtimes):
                continue
            # Check trigger patterns
            if any(pattern.search(user_request) for pattern in rule.trigger_patterns):
                matched_rules.append(rule)

        if not matched_rules:
            logger.warning("No intent matched for request; falling through to AI discovery")
            return IntentRouteResult(
                matched_intents=[],
                seen_files={},
                binary_inspections={},
                stop_reason="no_intent_matched",
            )

        matched_intents = [rule.intent_id for rule in matched_rules]

        # Collect unique candidates per matched rule, capped at MAX_FILES_PER_INTENT
        candidates_by_rule: list[list[str]] = []
        for rule in matched_rules:
            try:
                candidate_paths = rule.file_selector(context_tools, analysis)
            except Exception as exc:
                logger.debug("File selector for %s raised: %s", rule.intent_id, exc)
                candidate_paths = []

            unique_candidates = list(dict.fromkeys(candidate_paths))
            capped_candidates = unique_candidates[:MAX_FILES_PER_INTENT]
            candidates_by_rule.append(capped_candidates)

        # Fair round-robin merge across rules up to MAX_FILES_TOTAL
        merged_paths: list[str] = []
        for round_idx in range(MAX_FILES_PER_INTENT):
            if not any(round_idx < len(rc) for rc in candidates_by_rule):
                break
            for rule_candidates in candidates_by_rule:
                if round_idx < len(rule_candidates):
                    path = rule_candidates[round_idx]
                    if path not in merged_paths:
                        merged_paths.append(path)
                        if len(merged_paths) >= MAX_FILES_TOTAL:
                            break
            if len(merged_paths) >= MAX_FILES_TOTAL:
                break

        all_workspace_paths = _get_workspace_paths(context_tools)
        if not merged_paths and all_workspace_paths:
            logger.warning(
                "Intents %s matched but no candidate files found; falling through to AI discovery",
                matched_intents,
            )
            return IntentRouteResult(
                matched_intents=[],
                seen_files={},
                binary_inspections={},
                stop_reason="no_intent_matched",
            )

        # Read file contents and binary inspections
        seen_files: dict[str, str] = {}
        binary_inspections: dict[str, Any] = {}

        for path in merged_paths:
            try:
                content = context_tools.read_file_range(path)
            except Exception:
                content = ""
            seen_files[path] = content

            # Structured binary inspection if applicable
            try:
                target = context_tools.workspace.safe_path(path)
                if (
                    target.suffix.lower() in {".dll", ".so"}
                    or target.name == "global-metadata.dat"
                ):
                    inspection = context_tools._inspect_binary_path(path, user_request=user_request)
                    if inspection:
                        binary_inspections[path] = inspection
            except Exception:
                pass

        return IntentRouteResult(
            matched_intents=matched_intents,
            seen_files=seen_files,
            binary_inspections=binary_inspections,
            stop_reason="intent_matched",
        )

