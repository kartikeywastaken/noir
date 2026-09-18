# Handoff Report — Reviewer A (Android XML & Resource Localization Audit)

## Review Summary

**Verdict**: **REQUEST_CHANGES**

Reviewer A has completed an in-depth audit and adversarial stress testing of Android XML namespaces, attribute parsing, diverse localized resource qualifiers, and obfuscated/flattened smali entries in `backend/src/noir/infrastructure/ai/intent_router.py`.

While the router baseline passes 85 existing tests and successfully handles custom namespace prefixes (e.g. `xmlns:noir="..."`) and basic BCP-47 qualifiers, deep adversarial stress-testing identified **2 Critical** and **2 Major** architectural defects that directly compromise routing accuracy and AI discovery bypass under real-world decompiled APK conditions.

Nine new adversarial test cases have been added to `backend/tests/unit/test_intent_router_adversarial.py` (4 passing, 5 xfailing repros). The entire backend unit suite passes (310 passed, 5 xfailed, 6 skipped) with zero regressions.

---

## 1. Observations

### Obs 1: Default XML Namespace Disables Manifest Extraction
- **File & Lines**: `backend/src/noir/infrastructure/ai/intent_router.py:105-146`
  ```python
  app = root.find("application")
  if app is not None:
      for tag in ("activity", "activity-alias"):
          for elem in app.findall(tag):
              ...
      for rec in app.findall("receiver"):
          ...
      for srv in app.findall("service"):
          ...
  ```
- **Observed Behavior**:
  When `AndroidManifest.xml` contains a default XML namespace (e.g. `<manifest xmlns="http://schemas.android.com/apk/res/android" ...>`), Python's `xml.etree.ElementTree` prepends the namespace URI in Clark notation to every element tag (e.g. `{http://schemas.android.com/apk/res/android}application`).
  `root.find("application")` evaluates to `None`.
  `_extract_manifest_info` fails silently and returns empty lists for `launcher_activities`, `receivers`, and `services`.
- **Direct Verification**:
  ```python
  # Manifest with default xmlns:
  info = _extract_manifest_info(tools)
  # Result: {'package_name': 'com.defaultns.app', 'launcher_activities': [], 'receivers': [], 'services': [], 'label_ref': None}
  ```

### Obs 2: Manifest-Declared Receivers/Services Starved by Length-Based Sorting
- **File & Lines**: `backend/src/noir/infrastructure/ai/intent_router.py:348-370`
  ```python
  for name in comp_names:
      ...
      if p not in results:
          results.append(p)
  ...
  for p in all_paths:
      p_lower = p.lower()
      if p_lower.endswith(".smali") and ("receiver" in p_lower or "service" in p_lower):
          if p not in results:
              results.append(p)

  # Prioritize outer classes over inner anonymous classes
  results.sort(key=lambda p: (1 if "$" in p else 0, len(p), p.lower()))
  ```
- **Observed Behavior**:
  `_find_receiver_service_smali` appends manifest-matched components and generic keyword matches (`"receiver"` / `"service"`) to the same list. It then sorts purely by `(1 if "$" in p else 0, len(p), p.lower())`.
  If an application declares a real receiver with a package path (e.g. `smali/com/mycompany/app/receivers/AppBroadcastReceiver.smali`, length 59), and the APK includes 8 short third-party SDK service classes (e.g. `smali/sdk/Service0.smali` through `Service7.smali`, length 22), the short SDK classes sort ahead of the real receiver. Because `MAX_FILES_PER_INTENT = 8`, the manifest component is dropped and never routed.

### Obs 3: Single-Letter Obfuscated Classes Cause Unanchored Substring Matching
- **File & Lines**: `backend/src/noir/infrastructure/ai/intent_router.py:208-233, 349-354`
  ```python
  for l_name in expanded_launcher_names:
      l_frag = l_name.lstrip(".").replace(".", "/").lower()
      simple_name = l_name.split(".")[-1].lower() + ".smali"
      for p in all_paths:
          p_lower = p.lower()
          if p_lower.endswith(".smali") and (
              l_frag in p_lower or p_lower.endswith("/" + simple_name) or p_lower == simple_name
          ):
              if p not in results:
                  results.append(p)
  ```
- **Observed Behavior**:
  In obfuscated APKs, classes are frequently renamed to single letters like `.a` or `a.smali`.
  When `l_name = ".a"`, `l_frag = "a"`.
  The condition `l_frag in p_lower` evaluates `"a" in p_lower`.
  Because every smali path begins with `"smali/"` (which contains the character `'a'`), **every single smali file in the workspace matches**.
  Furthermore, `expanded_launcher_names` inserts unqualified `".a"` before `"com.example.a"`, causing `primary_launcher = ".a"`. This gives third-party library classes like `smali/androidx/core/a.smali` equal tier-0 priority to the application class, causing `androidx` to sort ahead of `com` alphabetically.

### Obs 4: Intent Router Bypasses AI Discovery When 0 Files Are Routed
- **File & Lines**: `backend/src/noir/infrastructure/ai/intent_router.py:954-1011`, `backend/src/noir/application/ai_service.py:226-234`
  ```python
  # intent_router.py:
  return IntentRouteResult(
      matched_intents=matched_intents,
      seen_files=seen_files,
      binary_inspections=binary_inspections,
      stop_reason="intent_matched",
  )

  # ai_service.py:
  route_result = IntentRouter().route(request, context_tools, analysis)
  if route_result and route_result.matched_intents:
      discovery_stop_reason = "intent_matched"
      discovery_api_calls = 0
      context = build_discovered_context(context_tools, route_result, user_request=request)
  ```
- **Observed Behavior**:
  Open Issues Ledger #3 stated: *"if those are also renamed, the router will find 0 files and fallback to AI discovery."*
  In reality, when an intent triggers from the prompt (e.g. `toast_flash`), but no candidate files are found (`merged_paths == []`), `IntentRouter.route` returns `matched_intents=['toast_flash']`, `seen_files={}`, and `stop_reason='intent_matched'`.
  `ai_service.py` evaluates `if route_result and route_result.matched_intents:` (which is truthy), sets `discovery_api_calls = 0`, and passes an empty context (`seen_files={}`) to the planner. **AI discovery fallback never executes**.

### Obs 5: `resources/values/strings.xml` Inversion vs Localized Qualifiers
- **File & Lines**: `backend/src/noir/infrastructure/ai/intent_router.py:384-401`
- **Observed Behavior**:
  `_select_app_name` explicitly checks `res/values/strings.xml`. If a workspace uses `resources/values/strings.xml`, it is caught only by the secondary regex `^(?:res|resources)/values[^/]*/strings\.xml$`.
  In `_strings_sort_key`:
  Hyphen `'-'` is ASCII 45 and slash `'/'` is ASCII 47.
  Therefore, `"resources/values-af/strings.xml"` sorts before `"resources/values/strings.xml"`.
  With 8 or more language qualifiers, the base `resources/values/strings.xml` is sorted to the end and truncated by the 8-file cap.

---

## 2. Logic Chain

1. **Premise**: Intent routing exists to deterministically identify and supply the relevant files to the AI planning context, bypassing AI discovery only when the relevant files are actually located.
2. **From Obs 1**: Real-world decompiled APK manifests frequently use default namespace attributes or XML root namespaces. When a default XML namespace is present, `_extract_manifest_info` fails completely, yielding zero activities, receivers, or services.
3. **From Obs 2**: Even when manifest parsing succeeds, `_find_receiver_service_smali` lacks priority tiers. Short framework/SDK classes crowd out real manifest-declared components within the 8-file limit.
4. **From Obs 3**: For obfuscated APKs with single-letter class names, unanchored substring matching causes catastrophic false-positive explosion, matching all smali files in the project.
5. **From Obs 4**: When file matching yields 0 files, the router returns `stop_reason="intent_matched"` with non-empty `matched_intents`. `ai_service.py` bypasses AI discovery and feeds an empty context to the planner. This directly violates the intended fallback contract documented in Open Issues Ledger #3.
6. **Conclusion**: The implementation must be updated before approval to fix default XML namespace parsing, enforce component-tier prioritization in receiver/service selection, anchor obfuscated class name matching, and ensure fallback to AI discovery when zero files are routed.

---

## 3. Caveats

- Live decompilation via apktool on raw `.apk` binaries was not executed (consistent with unit test environment design; all tests use decoded workspaces).
- Diverse BCP-47 qualifiers (`values-b+sr+Latn`, `values-zh-rCN`, `values-night`, etc.) pass when total qualifiers are <= 6, but requests explicitly targeting specific locales (e.g. "update Chinese strings") are not prioritized by locale keyword.

---

## 4. Conclusion & Recommended Fixes

### Verdict: REQUEST_CHANGES

### Required Fixes for Implementer:

#### Fix 1: XML Wildcard Namespace Tag Matching in `_extract_manifest_info`
Use ElementTree's `{*}tag` wildcard (Python 3.8+):
```python
app = root.find("{*}application")
if app is not None:
    label = _get_xml_attr(app, "label")
    if label and label.startswith("@string/"):
        info["label_ref"] = label.split("/", 1)[1]

    for tag in ("activity", "activity-alias"):
        for elem in app.findall(f"{{*}}{tag}"):
            ...
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
```

#### Fix 2: Priority-Tiered Sorting in `_find_receiver_service_smali`
Track manifest-matched files and sort them with tier-0 priority:
```python
manifest_matches: set[str] = set()
for name in comp_names:
    ...
    for p in all_paths:
        ...
        manifest_matches.add(p)
        if p not in results:
            results.append(p)

for p in all_paths:
    ...
    if p not in results:
        results.append(p)

# Tier 0: manifest component matches, Tier 1: keyword matches
results.sort(key=lambda p: (0 if p in manifest_matches else 1, 1 if "$" in p else 0, len(p), p.lower()))
```

#### Fix 3: Path-Anchored Matching & Package Prioritization in `_find_launcher_smali`
Ensure fully qualified package name is prioritized and single-token fragments are anchored:
```python
# 1. Prioritize fully qualified class names
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

# 2. Anchor fragment matches
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
            if matches_frag or p_lower.endswith("/" + simple_name) or p_lower == simple_name:
                if p not in results:
                    results.append(p)
```

#### Fix 4: Fall Back to AI Discovery When 0 Files Routed
In `intent_router.py`:
```python
if not merged_paths:
    # If no files could be resolved for the matched intents, fall through to AI discovery
    logger.warning("Intents %s matched but no candidate files found; falling through to AI discovery", matched_intents)
    return IntentRouteResult(
        matched_intents=[],
        seen_files={},
        binary_inspections={},
        stop_reason="no_intent_matched",
    )
```
And in `backend/src/noir/application/ai_service.py:226`:
```python
if route_result and route_result.matched_intents and route_result.seen_files:
```

#### Fix 5: Prioritize `resources/values/strings.xml` in `_select_app_name`
```python
if _path_exists(context_tools, all_set, "res/values/strings.xml"):
    candidates.append("res/values/strings.xml")
elif _path_exists(context_tools, all_set, "resources/values/strings.xml"):
    candidates.append("resources/values/strings.xml")
```

---

## 5. Verification Method

### Test Suite Execution
Run the unit and adversarial test suites:
```bash
./backend/.venv/bin/pytest -v backend/tests/unit/test_intent_router.py backend/tests/unit/test_intent_router_adversarial.py
```
Expected baseline result:
- 89 passed
- 5 xfailed (documenting the exact repros for default xmlns, receiver starvation, obfuscated greedy match, 0-file fallback, and resources/values sort inversion)

### Full Backend Verification
```bash
./backend/.venv/bin/pytest backend/tests/unit/
```
Result:
- 310 passed, 6 skipped, 5 xfailed in ~6.0s (0 regressions).

### Invalidation Conditions
- If `test_xml_default_namespace_handling_bug` fails with an unexpected exception other than `AssertionError`.
- If fixing the 4 issues causes any of the 85 existing intent router unit tests to fail.
