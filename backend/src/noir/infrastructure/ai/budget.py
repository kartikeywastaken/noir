"""Budget complete UTF-8 prompts without cutting JSON or required patch content."""

import copy
import json
from collections.abc import Callable, Iterable
from typing import Any


def bounded_prompt(
    render: Callable[[str], str],
    context: dict[str, Any],
    *,
    system: str,
    max_bytes: int,
    protected_paths: Iterable[str] = (),
) -> str:
    """Trim optional inventory first; preserve instructions and required file evidence."""
    data = copy.deepcopy(context)
    protected = set(protected_paths)

    def prompt() -> str:
        return render(json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    def fits(value: str) -> bool:
        return len(value.encode("utf-8")) + len(system.encode("utf-8")) <= max_bytes

    result = prompt()
    if fits(result):
        return result
    data["context_truncated"] = True
    # These are optional discovery inventories, never the approved plan or file contents.
    lists = [data.get(key) for key in ("files", "smali_classes", "components", "omitted_files")]
    manifest = data.get("manifest", {})
    lists += [manifest.get("components"), manifest.get("permissions")]
    for values in lists:
        while isinstance(values, list) and values:
            del values[len(values) // 2 :]
            result = prompt()
            if fits(result):
                return result
    for path in reversed(list(data.get("file_snippets", {}))):
        if path in protected:
            continue
        for key in ("file_snippets", "file_hashes", "file_coverage"):
            data.get(key, {}).pop(path, None)
        result = prompt()
        if fits(result):
            return result
    actual = len(result.encode("utf-8")) + len(system.encode("utf-8"))
    raise ValueError(
        f"Required AI instructions and file context need {actual:,} bytes; "
        f"the configured request limit is {max_bytes:,}. Narrow the request or split the plan. "
        "Required content was not truncated and no request was sent."
    )
