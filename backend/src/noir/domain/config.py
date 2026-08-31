"""NOIR configuration with Pydantic Settings.

Precedence: CLI override → environment → config file → safe default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings


def _default_data_dir() -> str:
    return str(Path.home() / ".noir")


class NoirConfig(BaseSettings):
    """Complete NOIR configuration."""

    model_config = {
        "env_prefix": "NOIR_",
        "env_file": str(Path(__file__).resolve().parents[3] / ".env"),
        "extra": "ignore",
        "populate_by_name": True,
    }

    gemini_api_key: SecretStr = Field(
        default=SecretStr(""),
        exclude=True,
        validation_alias=AliasChoices("GEMINI_API_KEY", "NOIR_GEMINI_API_KEY", "gemini_api_key"),
    )

    # ── Directories ──────────────────────────────────────────────────
    data_dir: str = Field(default_factory=_default_data_dir)
    database_url: str = ""

    # ── Runner ───────────────────────────────────────────────────────
    runner_mode: Literal["native"] = "native"

    # ── Java ─────────────────────────────────────────────────────────
    java_executable: str = "java"

    # ── APKTool ──────────────────────────────────────────────────────
    apktool_path: str = "apktool"
    apktool_jar: str = ""

    # ── Android SDK ──────────────────────────────────────────────────
    android_sdk_dir: str = ""
    build_tools_version: str = "36.0.0"

    # ── Explicit tool paths (override SDK discovery) ─────────────────
    zipalign_path: str = ""
    apksigner_path: str = ""
    aapt2_path: str = ""
    adb_path: str = ""

    # ── Binary patch helpers ──────────────────────────────────────────
    dotnet_tool_path: str = "dotnet"
    noir_cil_tool_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NOIR_CIL_TOOL_PATH", "noir_cil_tool_path"),
    )
    max_assembly_size: int = 15 * 1024 * 1024
    max_native_library_size: int = 128 * 1024 * 1024
    max_il2cpp_metadata_size: int = 64 * 1024 * 1024
    cil_patch_timeout: int = 60
    native_patch_timeout: int = 60

    # ── AI ────────────────────────────────────────────────────────────
    ai_provider: str = "gemini"
    ai_model: str = "gemini-3.6-flash"
    ai_fallback_model: str = ""
    ai_timeout: int = 120
    ai_retry_limit: int = 2
    ai_response_retry_limit: int = Field(default=1, ge=0, le=2)
    ai_max_output_tokens: int = Field(default=16_384, ge=1, le=65_536)
    ai_max_request_size: int = 100_000
    ai_max_output_size: int = 50_000
    ai_max_workflow_calls: int = 10

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        def json_settings():
            path = Path.home() / ".noir" / "config.json"
            if not path.exists():
                return {}
            values = json.loads(path.read_text())
            if not isinstance(values, dict):
                raise ValueError("NOIR config.json must contain an object")
            return values

        return init_settings, env_settings, dotenv_settings, json_settings, file_secret_settings

    # ── API ───────────────────────────────────────────────────────────
    api_host: str = "127.0.0.1"
    api_port: int = 8787

    # ── Process limits ────────────────────────────────────────────────
    process_timeout: int = 300
    max_apk_size: int = 500 * 1024 * 1024  # 500 MB
    max_archive_entries: int = 50_000
    max_expanded_size: int = 2 * 1024 * 1024 * 1024  # 2 GB
    max_compression_ratio: float = 100.0
    max_upload_size: int = 500 * 1024 * 1024  # 500 MB

    @field_validator("android_sdk_dir", mode="before")
    @classmethod
    def _detect_android_sdk(cls, v: str) -> str:
        if v:
            return v
        # Check ANDROID_HOME and ANDROID_SDK_ROOT
        for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
            val = os.environ.get(env_var, "")
            if val and Path(val).is_dir():
                return val
        # Check common macOS location
        mac_path = Path.home() / "Library" / "Android" / "sdk"
        if mac_path.is_dir():
            return str(mac_path)
        # Check common Linux location
        linux_path = Path.home() / "Android" / "Sdk"
        if linux_path.is_dir():
            return str(linux_path)
        return ""

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_path = Path(self.data_dir) / "noir.db"
        return f"sqlite:///{db_path}"

    @property
    def projects_dir(self) -> Path:
        return Path(self.data_dir) / "projects"

    @property
    def keys_dir(self) -> Path:
        return Path(self.data_dir) / "keys"

    @property
    def tokens_dir(self) -> Path:
        return Path(self.data_dir) / "tokens"

    def resolve_tool_path(self, tool: str) -> str:
        """Resolve a tool path using explicit config, SDK, or PATH."""
        explicit = {
            "zipalign": self.zipalign_path,
            "apksigner": self.apksigner_path,
            "aapt2": self.aapt2_path,
            "adb": self.adb_path,
            "dotnet": self.dotnet_tool_path,
        }
        if tool in explicit and explicit[tool]:
            return explicit[tool]
        if self.android_sdk_dir and tool in ("zipalign", "apksigner", "aapt2"):
            sdk_path = Path(self.android_sdk_dir) / "build-tools" / self.build_tools_version / tool
            if sdk_path.exists():
                return str(sdk_path)
        if self.android_sdk_dir and tool == "adb":
            adb_path = Path(self.android_sdk_dir) / "platform-tools" / "adb"
            if adb_path.exists():
                return str(adb_path)
        return tool  # fall back to PATH lookup

    def to_safe_dict(self) -> dict[str, Any]:
        """Return config as dict with secrets redacted."""
        data = self.model_dump()
        # Never expose API keys or passwords
        data["gemini_api_key"] = "***REDACTED***" if self.gemini_api_key.get_secret_value() else ""
        for key in list(data.keys()):
            if data[key] and any(s in key.lower() for s in ("key", "password", "secret", "token")):
                data[key] = "***REDACTED***"
        return data

    def ensure_directories(self) -> None:
        """Create required application directories."""
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self.tokens_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls, overrides: dict[str, Any] | None = None) -> NoirConfig:
        """Load configuration with optional CLI overrides."""
        return cls(**(overrides or {}))


# Module-level singleton for convenience
_config: NoirConfig | None = None


def get_config(**overrides: Any) -> NoirConfig:
    """Get or create the global config singleton."""
    global _config
    if _config is None or overrides:
        _config = NoirConfig.load(overrides if overrides else None)
    return _config


def reset_config() -> None:
    """Reset the global config (for testing)."""
    global _config
    _config = None
