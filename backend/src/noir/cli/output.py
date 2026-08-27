"""CLI output formatting utilities."""

from __future__ import annotations

import json
import sys
from typing import Any

_json_mode = False


def set_json_mode(enabled: bool) -> None:
    global _json_mode
    _json_mode = enabled


def is_json_mode() -> bool:
    return _json_mode


def output(data: Any, human_message: str = "") -> None:
    """Print output — JSON to stdout in json mode, human text otherwise."""
    if _json_mode:
        if isinstance(data, str):
            print(data)
        else:
            print(json.dumps(data, indent=2, default=str))
    else:
        if human_message:
            print(human_message)
        elif isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, list):
                    print(f"  {key}:")
                    for item in value[:20]:
                        print(f"    - {item}")
                    if len(value) > 20:
                        print(f"    ... and {len(value) - 20} more")
                else:
                    print(f"  {key}: {value}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    parts = [f"{k}={v}" for k, v in list(item.items())[:5]]
                    print(f"  {', '.join(parts)}")
                else:
                    print(f"  {item}")
        else:
            print(data)


def progress(message: str) -> None:
    """Print progress to stderr (visible even in json mode)."""
    print(message, file=sys.stderr)


def error(message: str) -> None:
    """Print error message."""
    if _json_mode:
        print(json.dumps({"error": message}), file=sys.stdout)
    else:
        print(f"Error: {message}", file=sys.stderr)
