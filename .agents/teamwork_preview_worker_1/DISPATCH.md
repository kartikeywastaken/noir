## 2026-09-18T09:49:00Z

You are the Intent Router Refinement Worker (teamwork_preview_worker_1) for the NOIR project.
Your assigned working directory for metadata: `/Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_worker_1`

Read the original request at `/Users/kartik/Documents/ChatGPT/noir/ORIGINAL_REQUEST.md` before starting work.
Also read the detailed findings and suggested fixes from the three specialized reviewers:
- Reviewer A (XML & localization): `/Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_reviewer_a/handoff.md`
- Reviewer B (Composite multi-intent stress testing): `/Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_reviewer_b/handoff.md`
- Reviewer C (Cross-framework runtime edge cases): `/Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_reviewer_c/handoff.md`

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

### File Ownership:
You have exclusive write ownership of:
- `backend/src/noir/infrastructure/ai/intent_router.py`
- `backend/src/noir/application/ai_service.py`
- `backend/tests/unit/test_intent_router_adversarial.py` (to remove xfail markers once fixes are verified)

### Required Tasks & Refinements:
1. **Fix Default XML Namespace in Manifest Extraction (`_extract_manifest_info`)**:
   In `backend/src/noir/infrastructure/ai/intent_router.py`:
   Use ElementTree `{*}tag` wildcard notation (e.g. `root.find("{*}application")`, `app.findall("{*}activity")`, `app.findall("{*}activity-alias")`, `app.findall("{*}receiver")`, `app.findall("{*}service")`, `elem.findall("{*}intent-filter")`, `if_elem.findall("{*}action")`, `if_elem.findall("{*}category")`) so that manifests with default `xmlns="..."` or Clark-notation tags parse correctly.

2. **Priority-Tiered Sorting in Receiver/Service Selection (`_find_receiver_service_smali`)**:
   Track manifest-matched components in `manifest_matches: set[str] = set()`.
   Sort so manifest component matches have tier-0 priority, and generic keyword matches have tier-1 priority:
   `results.sort(key=lambda p: (0 if p in manifest_matches else 1, 1 if "$" in p else 0, len(p), p.lower()))`.

3. **Anchor Single-Token / Obfuscated Class Fragments in `_find_launcher_smali`**:
   Prioritize fully qualified names (`f"{package_name}.{l_name}"` or `f"{package_name}{l_name}"`) in `expanded_launcher_names`.
   Anchor fragment matching so that single-letter names like `"a"` do not match all files under `"smali/"`:
   `matches_frag = ("/" + l_frag + ".smali") in p_lower or ("/" + l_frag + "/") in p_lower or (("/" in l_frag) and l_frag in p_lower)`.

4. **Fall Back to AI Discovery When 0 Files Routed**:
   In `IntentRouter.route()`:
   If `not merged_paths`:
   log warning: `logger.warning("Intents %s matched but no candidate files found; falling through to AI discovery", matched_intents)`
   return `IntentRouteResult(matched_intents=[], seen_files={}, binary_inspections={}, stop_reason="no_intent_matched")`.
   In `backend/src/noir/application/ai_service.py:226`:
   require `if route_result and route_result.matched_intents and route_result.seen_files:`.

5. **Sort Base `resources/values/strings.xml` Before Localized Qualifiers**:
   In `_select_app_name`, check `resources/values/strings.xml` and ensure base strings files sort before localized `values-*/strings.xml` so base strings are never truncated by the 8-file limit.

6. **Fix Premature Round-Robin Loop Termination in Composite Multi-Intent Merging**:
   In `IntentRouter.route()` (around lines 968-981):
   Do not break on `not added_in_round`. A round may encounter only already-added duplicate files across rules, while subsequent rounds still have unadded unique files.
   Iterate while `any(round_idx < len(rc) for rc in candidates_by_rule)` and break when `len(merged_paths) >= MAX_FILES_TOTAL` or all candidate lists are exhausted.

7. **Fix Unity IL2CPP Raw Marker Scan False-Positive Mono Detection**:
   In `scan_directory_markers`:
   Check that `assets/bin/Data/Managed` actually contains `.dll` files (`any(managed_dir.glob("*.dll"))` or `"assets/bin/data/managed" in p_lower and p_lower.endswith(".dll")`).
   Exclude `libmonodroid` when detecting `libmono`: `("libmono" in p_lower or "libmonosgen" in p_lower) and "libmonodroid" not in p_lower and p_lower.endswith(".so")`.

8. **Fix Xamarin `libmonodroid.so` Substring Collision**:
   In `_select_unity_mono`: check `"libmonodroid" not in p_lower`.
   In `_select_xamarin_dotnet`: include `libmonodroid.so` and `libxamarin-app.so`.

9. **Fix React Native Marker Scan Missing `main.jsbundle` / `index.bundle`**:
   In `scan_directory_markers`: include `p_lower.endswith("index.bundle")` and `p_lower.endswith("main.jsbundle")`.

10. **Test Verification & XFail Removal**:
    Remove `@pytest.mark.xfail` from all defect reproduction tests in `backend/tests/unit/test_intent_router_adversarial.py` so they are asserted as standard passing tests.
    Run:
    - `./backend/.venv/bin/pytest backend/tests/unit/test_intent_router.py backend/tests/unit/test_intent_router_adversarial.py -v` (must be 100% PASS, 0 failures, 0 xfails)
    - `./backend/.venv/bin/pytest backend/tests/unit/` (must pass with 0 regressions)
    - `./backend/.venv/bin/pytest backend/tests/integration/` (must pass with 0 regressions)

Document everything in `/Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_worker_1/handoff.md` and send a message when complete.

## 2026-09-18T10:11:33Z
**Context**: Status check on Refinement Worker 1
**Content**: Checking in on your progress with the 10 refinement tasks and test suite runs. What is your current status?
**Action**: Please report current status and which tasks you are currently executing.
