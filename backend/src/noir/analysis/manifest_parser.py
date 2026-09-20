"""Safe AndroidManifest.xml parser.

Uses defusedxml-style parsing to prevent XXE attacks.
Handles Android XML namespaces properly.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from noir.domain.models import ComponentInfo
from noir.security.xml import parse

# Android namespace
ANDROID_NS = "http://schemas.android.com/apk/res/android"
NS_MAP = {"android": ANDROID_NS}


def _safe_parse(xml_path: Path) -> ET.Element:
    """Parse XML safely with entity expansion disabled."""
    # Use iterparse to limit entity expansion
    # Disable external entities by not allowing any DTD processing
    try:
        tree = parse(xml_path)
        return tree.getroot()
    except ET.ParseError as e:
        raise ValueError(f"Failed to parse XML: {e}") from None


def _attr(element: ET.Element, name: str) -> str | None:
    """Get an android-namespaced attribute."""
    return element.get(f"{{{ANDROID_NS}}}{name}")


def _plain_attr(element: ET.Element, name: str) -> str | None:
    """Get a non-namespaced attribute."""
    return element.get(name)


def parse_manifest(manifest_path: Path) -> dict[str, Any]:
    """Parse AndroidManifest.xml and extract structured metadata.

    Returns a dict with all extracted information, tracking provenance.
    """
    root = _safe_parse(manifest_path)
    result: dict[str, Any] = {
        "package_name": "",
        "version_name": "",
        "version_code": "",
        "min_sdk": None,
        "target_sdk": None,
        "application_class": None,
        "components": [],
        "permissions": [],
        "permission_definitions": [],
        "metadata_sources": {},
    }

    # Package info
    result["package_name"] = root.get("package", "")
    result["version_name"] = _attr(root, "versionName") or ""
    result["version_code"] = _attr(root, "versionCode") or ""
    result["metadata_sources"]["package_name"] = "AndroidManifest.xml"
    result["metadata_sources"]["version_name"] = "AndroidManifest.xml"
    result["metadata_sources"]["version_code"] = "AndroidManifest.xml"

    # SDK versions
    uses_sdk = root.find("uses-sdk")
    if uses_sdk is not None:
        min_sdk = _attr(uses_sdk, "minSdkVersion")
        target_sdk = _attr(uses_sdk, "targetSdkVersion")
        if min_sdk and min_sdk.isdigit():
            result["min_sdk"] = int(min_sdk)
        if target_sdk and target_sdk.isdigit():
            result["target_sdk"] = int(target_sdk)

    # Permissions used
    for perm in root.findall("uses-permission"):
        name = _attr(perm, "name")
        if name:
            result["permissions"].append(name)

    # Permission definitions
    for perm_def in root.findall("permission"):
        entry = {}
        name = _attr(perm_def, "name")
        if name:
            entry["name"] = name
        protection = _attr(perm_def, "protectionLevel")
        if protection:
            entry["protectionLevel"] = protection
        label = _attr(perm_def, "label")
        if label:
            entry["label"] = label
        if entry:
            result["permission_definitions"].append(entry)

    # Application
    app = root.find("application")
    if app is not None:
        app_class = _attr(app, "name")
        if app_class:
            result["application_class"] = app_class

        # Components
        component_tags = {
            "activity": "activity",
            "activity-alias": "activity",
            "service": "service",
            "receiver": "receiver",
            "provider": "provider",
        }

        for tag, comp_type in component_tags.items():
            for elem in app.findall(tag):
                name = _attr(elem, "name")
                if not name:
                    continue

                exported_str = _attr(elem, "exported")
                exported: bool | None = None
                if exported_str is not None:
                    exported = exported_str.lower() == "true"

                permission = _attr(elem, "permission")

                # Intent filters
                intent_filters: list[dict[str, Any]] = []
                for if_elem in elem.findall("intent-filter"):
                    filter_info: dict[str, Any] = {"actions": [], "categories": [], "data": []}
                    for action in if_elem.findall("action"):
                        a_name = _attr(action, "name")
                        if a_name:
                            filter_info["actions"].append(a_name)
                    for cat in if_elem.findall("category"):
                        c_name = _attr(cat, "name")
                        if c_name:
                            filter_info["categories"].append(c_name)
                    for data in if_elem.findall("data"):
                        data_info: dict[str, str] = {}
                        for attr_name in ("scheme", "host", "path", "mimeType"):
                            val = _attr(data, attr_name)
                            if val:
                                data_info[attr_name] = val
                        if data_info:
                            filter_info["data"].append(data_info)
                    intent_filters.append(filter_info)

                is_launcher = any(
                    "android.intent.action.MAIN" in f.get("actions", [])
                    and "android.intent.category.LAUNCHER" in f.get("categories", [])
                    for f in intent_filters
                )

                target_act = _attr(elem, "targetActivity") if tag == "activity-alias" else None
                if target_act:
                    pkg = result.get("package_name", "")
                    if target_act.startswith("."):
                        target_act = f"{pkg}{target_act}"
                    elif "." not in target_act and pkg:
                        target_act = f"{pkg}.{target_act}"

                comp = ComponentInfo(
                    name=name,
                    component_type=comp_type,
                    exported=exported,
                    permission=permission,
                    intent_filters=intent_filters,
                    is_launcher=is_launcher,
                    is_alias=(tag == "activity-alias"),
                    target_activity=target_act,
                )
                result["components"].append(comp)

    return result
