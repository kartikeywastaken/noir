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
    gemini_discovery_api_key: SecretStr = Field(
        default=SecretStr(""),
        exclude=True,
        validation_alias=AliasChoices(
            "GEMINI_API_KEY_1",
            "NOIR_GEMINI_DISCOVERY_API_KEY",
            "gemini_discovery_api_key",
        ),
    )
    gemini_generation_api_key: SecretStr = Field(
        default=SecretStr(""),
        exclude=True,
        validation_alias=AliasChoices(
            "GEMINI_API_KEY_2",
            "NOIR_GEMINI_GENERATION_API_KEY",
            "gemini_generation_api_key",
        ),
    )
    gemini_api_keys: SecretStr = Field(
        default=SecretStr(""),
        exclude=True,
        validation_alias=AliasChoices(
            "GEMINI_API_KEYS",
            "NOIR_GEMINI_API_KEYS",
            "gemini_api_keys",
        ),
    )

    openrouter_api_key: SecretStr = Field(
        default=SecretStr(""),
        exclude=True,
        validation_alias=AliasChoices(
            "NOIR_OPENROUTER_API_KEY",
            "GLM_5.2",
            "openrouter_api_key",
        ),
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
    ai_provider: Literal["gemini", "adk", "none"] = "gemini"
    ai_model: str = "gemini-3.6-flash"
    # Comma-separated ordered list of fallback models to try on capacity/quota failures.
    # Example: "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-flash-latest"
    # Legacy single-model usage (e.g. NOIR_AI_FALLBACK_MODEL=gemini-3.5-flash) still works.
    ai_fallback_model: str = ""
    ai_timeout: int = 120
    ai_retry_limit: int = 2
    ai_response_retry_limit: int = Field(default=1, ge=0, le=2)
    ai_max_output_tokens: int = Field(default=16_384, ge=1, le=65_536)
    ai_max_request_size: int = 400_000
    ai_max_output_size: int = 50_000
    ai_max_workflow_calls: int = 10
    # Discovery model calls made before the final plan call. Two turns support
    # the common search → read/inspect flow; set three for unusually broad work.
    discovery_max_rounds: int = Field(default=2, ge=1, le=3)
    discovery_enabled: bool = True  # kill switch — falls back to static selection if False
    discovery_provider: Literal["openrouter", "gemini", "adk", "local"] = "openrouter"
    discovery_timeout: int = 30  # per-request timeout for discovery provider calls
    openrouter_discovery_model: str = "minimax/minimax-m3:free"
    openrouter_discovery_fallback_model: str = "z-ai/glm-5.2:free"
    # Maximum wall-clock seconds for any single AI provider call before forced failure.
    # Prevents nested retries from blocking a worker for minutes.
    ai_stall_timeout: int = 90

    def get_gemini_generation_keys(self) -> list[str]:
        """Return a list of Gemini API keys configured for generation/patching.

        Sources (in priority order, stripped, deduplicated, non-empty):
        1. Comma-separated keys in GEMINI_API_KEYS / NOIR_GEMINI_API_KEYS
        2. Comma-separated keys in GEMINI_API_KEY_2 / NOIR_GEMINI_GENERATION_API_KEY
        3. Comma-separated keys in GEMINI_API_KEY / NOIR_GEMINI_API_KEY
        """
        raw_candidates: list[str] = []
        for val in (
            self.gemini_api_keys.get_secret_value(),
            self.gemini_generation_api_key.get_secret_value(),
            self.gemini_api_key.get_secret_value(),
        ):
            if val:
                raw_candidates.extend(val.split(","))

        cleaned: list[str] = []
        for k in raw_candidates:
            stripped = k.strip()
            if stripped and stripped not in cleaned:
                cleaned.append(stripped)
        return cleaned

    def gemini_key_for(self, purpose: Literal["default", "discovery", "generation"]) -> str:
        """Select a Gemini credential without exposing it through normal config output.

        Dual-key installations route discovery to key 1 and plan/patch generation to
        key 2 or the configured round-robin key pool. The legacy single-key variable
        remains a supported fallback so existing local and EC2 deployments keep working.
        """
        legacy = self.gemini_api_key.get_secret_value()
        discovery = self.gemini_discovery_api_key.get_secret_value()
        generation = self.gemini_generation_api_key.get_secret_value()
        gen_keys = self.get_gemini_generation_keys()
        primary_gen = gen_keys[0] if gen_keys else (generation or legacy or discovery)
        if purpose == "discovery":
            return discovery or legacy or primary_gen
        if purpose == "generation":
            return primary_gen
        return primary_gen

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
    compression_ratio_min_expanded_size: int = 64 * 1024 * 1024  # 64 MiB
    max_upload_size: int = 500 * 1024 * 1024  # 500 MB
    upload_chunk_size: int = 2 * 1024 * 1024  # 2 MiB, acknowledged independently
    max_upload_chunk_size: int = 16 * 1024 * 1024  # hard server-side request cap
    upload_session_ttl: int = 24 * 60 * 60
    upload_fsync_interval_ms: int = 75  # max hold time for group-commit window
    upload_fsync_batch_max: int = 8  # max chunks per commit batch

    # ── Durable artifact storage ────────────────────────────────────
    # APKTool still works on local EBS. S3 stores only durable originals
    # and signed outputs, using the EC2 instance role (never static keys).
    artifact_store: Literal["local", "s3"] = "local"
    s3_bucket: str = ""
    s3_region: str = ""
    s3_prefix: str = "noir"
    s3_presign_expiry: int = Field(default=900, ge=60, le=3600)
    # S3 requires every multipart part except the last to be at least 5 MiB.
    # Eight MiB balances mobile retry cost with request overhead.
    s3_upload_part_size: int = Field(
        default=8 * 1024 * 1024,
        ge=5 * 1024 * 1024,
        le=64 * 1024 * 1024,
    )

    @field_validator("android_sdk_dir", mode="before")
    @classmethod
    def _detect_android_sdk(cls, v: str) -> str:
        if v:
            return v
        # Check explicit NOIR_ANDROID_SDK_DIR, ANDROID_HOME and ANDROID_SDK_ROOT
        for env_var in ("NOIR_ANDROID_SDK_DIR", "ANDROID_HOME", "ANDROID_SDK_ROOT"):
            val = os.environ.get(env_var, "")
            if val and Path(val).is_dir():
                return val
        # Check Homebrew android-commandlinetools on macOS
        brew_cmdline = Path("/opt/homebrew/share/android-commandlinetools")
        if brew_cmdline.is_dir():
            return str(brew_cmdline)
        # Check common macOS location
        mac_path = Path.home() / "Library" / "Android" / "sdk"
        if mac_path.is_dir():
            return str(mac_path)
        # Check /usr/local/share standard locations
        for p in (
            Path("/usr/local/share/android-commandlinetools"),
            Path("/usr/local/share/android-sdk"),
        ):
            if p.is_dir():
                return str(p)
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
            # Scan all versions in build-tools
            build_tools_dir = Path(self.android_sdk_dir) / "build-tools"
            if build_tools_dir.is_dir():
                for version_dir in sorted(build_tools_dir.iterdir(), reverse=True):
                    candidate = version_dir / tool
                    if candidate.is_file() and os.access(candidate, os.X_OK):
                        return str(candidate)
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
        data["gemini_discovery_api_key"] = (
            "***REDACTED***" if self.gemini_discovery_api_key.get_secret_value() else ""
        )
        data["gemini_generation_api_key"] = (
            "***REDACTED***" if self.gemini_generation_api_key.get_secret_value() else ""
        )
        data["gemini_api_keys"] = (
            "***REDACTED***" if self.gemini_api_keys.get_secret_value() else ""
        )
        data["openrouter_api_key"] = (
            "***REDACTED***" if self.openrouter_api_key.get_secret_value() else ""
        )
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
