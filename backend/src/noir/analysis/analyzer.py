"""Static APK analysis service.

Builds a structured inventory from the decoded APK workspace
without executing any application code.
"""

from __future__ import annotations

import yaml

from noir.analysis.manifest_parser import parse_manifest
from noir.analysis.smali_indexer import (
    detect_obfuscation,
    index_smali_classes,
    scan_smali_directories,
)
from noir.domain.config import NoirConfig, get_config
from noir.domain.models import AnalysisResult, NativeLibInfo
from noir.infrastructure.database.repositories import AnalysisRepository
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


def _has_hybrid_web_evidence(decoded, assets_dir, result) -> bool:
    """Detect hybrid/web apps (Cordova, React Native, WebView-heavy).

    Reuses the already-indexed smali classes rather than re-walking the tree.
    """
    if assets_dir is not None:
        # Cordova / PhoneGap
        www_index = assets_dir / "www" / "index.html"
        if www_index.is_file():
            return True
        # Cordova config
        cordova_config = assets_dir / "config.xml"
        if cordova_config.is_file():
            return True
        # React Native
        rn_bundle = assets_dir / "index.android.bundle"
        if rn_bundle.is_file():
            return True

    # A WebView reference by itself is weak evidence: ad/analytics SDKs embed one
    # in otherwise ordinary native apps. Only classify a WebView wrapper when it
    # also ships a meaningful bundle of editable web assets.
    web_asset_count = 0
    if assets_dir is not None:
        for path in assets_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".html", ".htm", ".js", ".css"}:
                web_asset_count += 1
                if web_asset_count >= 3:
                    break
    if web_asset_count < 3:
        return False

    # Bounded smali scan: look for WebView references in already-indexed classes.
    # This avoids another full walk of the decoded tree.
    webview_markers = {"android/webkit/WebView", "addJavascriptInterface"}
    for cls in result.smali_classes[:5000]:
        for method in cls.methods:
            if any(marker in method for marker in webview_markers):
                return True

    return False


class AnalysisService:
    """Performs static analysis on decoded APK workspaces."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.analysis_repo = AnalysisRepository()

    def analyze(
        self,
        project_id: str,
        workspace: ProjectWorkspace,
        *,
        persist: bool = True,
    ) -> AnalysisResult:
        """Run full static analysis on a decoded workspace.

        Args:
            project_id: The project ID.
            workspace: The project workspace.

        Returns:
            AnalysisResult with full inventory.
        """
        decoded = workspace.decoded_dir
        result = AnalysisResult(project_id=project_id)
        result.metadata_sources = {}

        # Parse AndroidManifest.xml
        manifest_path = decoded / "AndroidManifest.xml"
        if manifest_path.exists():
            try:
                manifest_data = parse_manifest(manifest_path)
                result.package_name = manifest_data.get("package_name", "")
                result.version_name = manifest_data.get("version_name", "")
                result.version_code = manifest_data.get("version_code", "")
                result.min_sdk = manifest_data.get("min_sdk")
                result.target_sdk = manifest_data.get("target_sdk")
                result.application_class = manifest_data.get("application_class")
                result.components = manifest_data.get("components", [])
                result.permissions = manifest_data.get("permissions", [])
                result.permission_definitions = manifest_data.get("permission_definitions", [])
                result.metadata_sources.update(manifest_data.get("metadata_sources", {}))
            except (ValueError, OSError) as e:
                result.compatibility_warnings.append(f"Manifest parse error: {e}")

        # Read apktool.yml for additional metadata
        apktool_yml = decoded / "apktool.yml"
        if apktool_yml.exists():
            try:
                with open(apktool_yml) as f:
                    apktool_data = yaml.safe_load(f)
                if isinstance(apktool_data, dict):
                    result.apktool_metadata = apktool_data

                    # Override SDK info from apktool.yml if missing from manifest
                    sdk_info = apktool_data.get("sdkInfo", {})
                    if isinstance(sdk_info, dict):
                        if result.min_sdk is None and "minSdkVersion" in sdk_info:
                            try:
                                result.min_sdk = int(sdk_info["minSdkVersion"])
                                result.metadata_sources["min_sdk"] = "apktool.yml"
                            except (ValueError, TypeError):
                                pass
                        if result.target_sdk is None and "targetSdkVersion" in sdk_info:
                            try:
                                result.target_sdk = int(sdk_info["targetSdkVersion"])
                                result.metadata_sources["target_sdk"] = "apktool.yml"
                            except (ValueError, TypeError):
                                pass

                    # Version info from apktool.yml
                    version_info = apktool_data.get("versionInfo", {})
                    if isinstance(version_info, dict):
                        if not result.version_name and "versionName" in version_info:
                            result.version_name = str(version_info["versionName"])
                            result.metadata_sources["version_name"] = "apktool.yml"
                        if not result.version_code and "versionCode" in version_info:
                            result.version_code = str(version_info["versionCode"])
                            result.metadata_sources["version_code"] = "apktool.yml"

                    # Check for split APK indicator
                    if apktool_data.get("isSplitApk", False):
                        result.is_split_apk = True
                        result.compatibility_warnings.append(
                            "This appears to be a split APK. Repackaging may not produce "
                            "a complete standalone application."
                        )
            except (yaml.YAMLError, OSError) as e:
                result.compatibility_warnings.append(f"apktool.yml parse error: {e}")

        # Smali analysis
        result.smali_directories = scan_smali_directories(decoded)
        result.multidex = len(result.smali_directories) > 1

        smali_classes = index_smali_classes(decoded)
        result.smali_classes = smali_classes
        result.obfuscation_indicators = detect_obfuscation(smali_classes)

        # Resource directories
        res_dir = decoded / "res"
        if res_dir.exists():
            result.resource_directories = sorted(d.name for d in res_dir.iterdir() if d.is_dir())

        # Assets
        assets_dir = decoded / "assets"
        if assets_dir.exists():
            result.assets = sorted(
                str(f.relative_to(assets_dir)) for f in assets_dir.rglob("*") if f.is_file()
            )[:1000]  # Limit

            managed_root = assets_dir / "bin" / "Data" / "Managed"
            if managed_root.exists():
                result.managed_assemblies = sorted(
                    path.relative_to(decoded).as_posix()
                    for path in managed_root.glob("*.dll")
                    if path.is_file()
                )
            result.il2cpp_metadata_files = sorted(
                path.relative_to(decoded).as_posix()
                for path in assets_dir.rglob("global-metadata.dat")
                if path.is_file()
            )

        # Native libraries
        lib_dir = decoded / "lib"
        if lib_dir.exists():
            for abi_dir in sorted(lib_dir.iterdir()):
                if abi_dir.is_dir():
                    libs = sorted(f.name for f in abi_dir.iterdir() if f.is_file())
                    if libs:
                        result.native_libs.append(NativeLibInfo(abi=abi_dir.name, libraries=libs))
                        if abi_dir.name not in result.native_abis:
                            result.native_abis.append(abi_dir.name)

        native_names = {
            library.lower()
            for abi in result.native_libs
            for library in abi.libraries
        }

        # Additive capability detection — an app can have multiple runtimes.
        if result.smali_classes:
            result.runtimes.add("dalvik")
        if result.il2cpp_metadata_files and "libil2cpp.so" in native_names:
            result.runtimes.add("il2cpp")
        if result.managed_assemblies or any(name.startswith("libmono") for name in native_names):
            result.runtimes.add("mono")
        if result.native_libs:
            result.runtimes.add("native")
        if not result.smali_classes and result.native_libs:
            result.runtimes.add("native_only")
        if _has_hybrid_web_evidence(decoded, assets_dir if assets_dir.exists() else None, result):
            result.runtimes.add("hybrid_web")

        # Backward-compatible single value from the set.
        result.runtime = result.primary_runtime

        # Input certificate info (from META-INF if available)
        meta_inf = decoded / "original" / "META-INF"
        if meta_inf.exists():
            cert_files = (
                list(meta_inf.glob("*.RSA"))
                + list(meta_inf.glob("*.DSA"))
                + list(meta_inf.glob("*.EC"))
            )
            if cert_files:
                result.input_cert_info["cert_files"] = [f.name for f in cert_files]

        # Compatibility warnings
        if result.target_sdk and result.target_sdk >= 31:
            # Check for missing explicit exported attributes
            for comp in result.components:
                if comp.exported is None and comp.intent_filters:
                    result.compatibility_warnings.append(
                        f"Component '{comp.name}' has intent-filters but no explicit "
                        f"android:exported attribute. Android 12+ (SDK 31) requires "
                        f"explicit exported for components with intent-filters."
                    )

        # Persist
        if persist:
            self.analysis_repo.save(result)

        return result

    def get_analysis(self, project_id: str) -> AnalysisResult | None:
        """Retrieve stored analysis results."""
        return self.analysis_repo.get(project_id)
