"""Thread-safe API key pool and round-robin rotator with rate-limit cooldowns.

Supports distributing requests across multiple Gemini API keys, skipping keys
that have hit 429/quota limits for a cooldown window (default 60s), and providing
clean failover when keys are temporarily exhausted.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noir.domain.config import NoirConfig

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 60.0


class ApiKeyRotator:
    """Thread-safe round-robin API key rotator with cooldown management."""

    def __init__(self, keys: Sequence[str] | None = None) -> None:
        self._lock = threading.Lock()
        self._index: int = 0
        self._cooldowns: dict[str, float] = {}  # key -> monotonic expiry timestamp

        # Clean and deduplicate keys preserving order
        cleaned: list[str] = []
        if keys:
            for k in keys:
                if not isinstance(k, str):
                    continue
                stripped = k.strip()
                if stripped and stripped not in cleaned:
                    cleaned.append(stripped)
        self._keys: list[str] = cleaned

    @property
    def keys(self) -> list[str]:
        """Return a copy of all configured keys in this rotator."""
        with self._lock:
            return list(self._keys)

    def has_keys(self) -> bool:
        """Check if any keys are configured."""
        return bool(self._keys)

    def get_all_keys(self) -> list[str]:
        """Return a copy of all configured keys."""
        return self.keys

    def is_in_cooldown(self, key: str) -> bool:
        """Check if a specific key is currently in cooldown."""
        with self._lock:
            expiry = self._cooldowns.get(key, 0.0)
            return time.monotonic() < expiry

    def mark_rate_limited(
        self, key: str, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    ) -> None:
        """Mark a key as rate-limited, placing it into cooldown for the specified duration."""
        if not key:
            return
        with self._lock:
            now = time.monotonic()
            self._cooldowns[key] = now + max(cooldown_seconds, 0.0)
            redacted = key[:4] + "..." + key[-4:] if len(key) > 8 else "***"
            logger.warning(
                "API key [%s] marked rate-limited (429/RESOURCE_EXHAUSTED). Cooldown active for %.1fs",
                redacted,
                cooldown_seconds,
            )

    def get_available_keys(self, exclude: set[str] | None = None) -> list[str]:
        """Return list of keys not currently in cooldown and not in exclude set."""
        now = time.monotonic()
        excluded = exclude or set()
        with self._lock:
            return [
                k for k in self._keys if k not in excluded and self._cooldowns.get(k, 0.0) <= now
            ]

    def get_next_key(self) -> str:
        """Get the next available key in round-robin order.

        Skips any key currently in cooldown. If all keys are cooling down,
        returns an empty string so callers can honor the provider retry delay
        instead of immediately burning the same quota-limited project again.
        Returns an empty string if no keys are configured.
        """
        with self._lock:
            if not self._keys:
                return ""
            now = time.monotonic()
            n = len(self._keys)

            # Check all keys starting from current index for an available one
            for _ in range(n):
                candidate = self._keys[self._index % n]
                self._index = (self._index + 1) % n
                if self._cooldowns.get(candidate, 0.0) <= now:
                    return candidate

            retry_after = min(self._cooldowns.get(k, now) for k in self._keys) - now
            logger.warning(
                "All %d API keys are in cooldown; next key is available in %.1fs",
                n,
                max(retry_after, 0.0),
            )
            return ""

    def retry_after_seconds(self) -> float:
        """Seconds until any configured key becomes healthy, or zero now."""
        with self._lock:
            if not self._keys:
                return 0.0
            now = time.monotonic()
            return max(0.0, min(self._cooldowns.get(k, 0.0) for k in self._keys) - now)

    def reset_cooldowns(self) -> None:
        """Clear all cooldowns."""
        with self._lock:
            self._cooldowns.clear()


_ROTATORS_CACHE: dict[tuple[str, ...], ApiKeyRotator] = {}
_CACHE_LOCK = threading.Lock()


def get_gemini_rotator(config: NoirConfig) -> ApiKeyRotator:
    """Get or create the singleton ApiKeyRotator for the configured Gemini keys."""
    keys = tuple(config.get_gemini_generation_keys())
    with _CACHE_LOCK:
        if keys not in _ROTATORS_CACHE:
            _ROTATORS_CACHE[keys] = ApiKeyRotator(list(keys))
        return _ROTATORS_CACHE[keys]
