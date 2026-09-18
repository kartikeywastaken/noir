# Handoff Report — Reviewer B: Multi-Intent Composite Stress Testing

## 1. Observation

### 1.1 Test Execution Commands and Results
- Baseline run:
  ```bash
  .venv/bin/pytest tests/unit/test_intent_router.py tests/unit/test_intent_router_adversarial.py
  ```
  Result: `110 passed, 9 xfailed in 3.23s`
- Full backend unit suite run:
  ```bash
  .venv/bin/pytest tests/unit/
  ```
  Result: `331 passed, 6 skipped, 9 xfailed in 6.42s`
- Dedicated Reviewer B stress suite execution:
  ```bash
  .venv/bin/pytest tests/unit/test_intent_router_adversarial.py -k "test_composite_ or test_deterministic_ or test_boundary_ or test_deduplication_ or test_empty_ or test_massive_ or test_round_robin_"
  ```
  Result: `13 passed, 1 xfailed in 0.93s`

### 1.2 Tested Implementation File & Line References
- File under review: `backend/src/noir/infrastructure/ai/intent_router.py`
- Relevant code chunk in `IntentRouter.route()` (lines 967-981):
  ```python
  # Fair round-robin merge across rules up to MAX_FILES_TOTAL
  merged_paths: list[str] = []
  for round_idx in range(MAX_FILES_PER_INTENT):
      added_in_round = False
      for rule_candidates in candidates_by_rule:
          if round_idx < len(rule_candidates):
              path = rule_candidates[round_idx]
              if path not in merged_paths:
                  merged_paths.append(path)
                  added_in_round = True
                  if len(merged_paths) >= MAX_FILES_TOTAL:
                      break
      if len(merged_paths) >= MAX_FILES_TOTAL or not added_in_round:
          break
  ```
- Premature termination bug in line 979:
  `if len(merged_paths) >= MAX_FILES_TOTAL or not added_in_round: break`

### 1.3 Verbatim Empirical Failure
When testing `test_round_robin_merge_does_not_starve_candidates_on_duplicate_rounds` without xfail:
```
FAILED tests/unit/test_intent_router_adversarial.py::test_round_robin_merge_does_not_starve_candidates_on_duplicate_rounds
AssertionError: layout_5.xml was starved by duplicate round termination bug
assert 'res/layout/layout_5.xml' in {'AndroidManifest.xml': '...', 'res/layout/layout_1.xml': '...', 'res/values/strings.xml': '...', 'res/layout/layout_2.xml': '...', 'res/values-en/strings.xml': '...', 'res/layout/layout_3.xml': '...', 'res/values-es/strings.xml': '...', 'res/layout/layout_4.xml': '...'}
```
In this scenario:
- Matched intents: `app_name`, `ui_layout`, `permission` (3 concurrent broad intents)
- Total candidate pool: 11 unique files (well below the 20-file cap)
- Actual returned `seen_files` count: 8 files
- Files dropped: `res/layout/layout_5.xml`, `res/layout/layout_6.xml`, `res/layout/layout_7.xml`

---

## 2. Logic Chain

1. **Focus Area 1 (Multi-Intent Composite Requests with 3, 4, 5, 6, 7 broad intents)**:
   - Evaluated composite requests combining:
     - 3 intents (`app_name` + `toast_flash` + `permission`)
     - 4 intents (`app_name` + `toast_flash` + `network_ping` + `ui_layout`)
     - 5 intents (`app_name` + `toast_flash` + `network_ping` + `ui_layout` + `permission`)
     - 6 intents (`app_name` + `toast_flash` + `network_ping` + `ui_layout` + `permission` + `receiver_service`)
     - 7 intents across Dalvik + Native (`app_name` + `toast_flash` + `network_ping` + `ui_layout` + `permission` + `receiver_service` + `native_elf`).
   - Observations confirm that trigger pattern matching cleanly detects all 3, 4, 5, 6, 7 intents without regex collisions or mutual exclusions.

2. **Focus Area 2 (Boundary Cap Enforcement: max 8 files per intent, max 20 total)**:
   - Line 964 caps each rule's candidates at `MAX_FILES_PER_INTENT` (8 files):
     `capped_candidates = unique_candidates[:MAX_FILES_PER_INTENT]`.
   - Lines 977 and 979 break when `len(merged_paths) >= MAX_FILES_TOTAL` (20 files).
   - In `test_boundary_cap_max_8_files_per_intent`, a single intent with 25 layouts returns exactly 8 files.
   - In `test_boundary_cap_max_20_files_total`, 5 intents with 50+ candidates return exactly 20 files.

3. **Focus Area 3 (Deterministic Priority Ordering)**:
   - `INTENT_RULES` registry order defines fixed precedence: `app_name`, `toast_flash`, `network_ping`, `ui_layout`, `permission`, `receiver_service`, `react_native_js`, `flutter_dart`, `unity_mono`, `unity_il2cpp`, `native_elf`, `xamarin_dotnet`.
   - In `test_deterministic_priority_ordering_across_permutations`, 6 distinct permutations of the user request prompt were tested:
     `matched_intents` and the exact key sequence of `seen_files` remained bit-for-bit identical across all permutations.

4. **Focus Area 4 (Deduplication)**:
   - `merged_paths` enforces `if path not in merged_paths: merged_paths.append(path)` and `seen_files` is a dictionary keyed by path.
   - In `test_deduplication_zero_duplicates_across_overlapping_intents`, overlapping files shared by up to 5 intents (`AndroidManifest.xml`, `strings.xml`, `MainActivity.smali`) appear exactly once in `seen_files`.

5. **Focus Area 5 (Edge Cases: Sparse & Massive Workspaces)**:
   - Empty workspace (0 files): returns `matched_intents`, `seen_files == {}`, `stop_reason == "intent_matched"`, 0 exceptions raised.
   - Sparse workspace (`AndroidManifest.xml` only): safely returns manifest only, 0 exceptions raised.
   - Massive workspace (600+ files across 6 categories): 7 concurrent intents routed in 0.003s (< 0.5s requirement), returning exactly 20 files.

6. **Root Cause Analysis of the Premature Round-Robin Termination Bug**:
   - In `IntentRouter.route()` lines 968-981, the round-robin loop iterates `for round_idx in range(MAX_FILES_PER_INTENT):`.
   - Line 979 contains: `if len(merged_paths) >= MAX_FILES_TOTAL or not added_in_round: break`.
   - The assumption `not added_in_round => no more candidates can ever be added` is **flawed**:
     If at index `round_idx`, every active rule with candidates at that index happens to point to a file that was *already added* in an earlier round (e.g. `res/values/strings.xml` or `MainActivity.smali`), then `added_in_round` remains `False`.
     The loop breaks immediately, abandoning all subsequent candidates at `round_idx + 1 .. 7` across all rules, even if those subsequent candidates are unique, unvisited files and `len(merged_paths) < MAX_FILES_TOTAL`.
   - This starves valid files (e.g., `layout_5.xml`, `layout_6.xml`, `layout_7.xml`, `NetworkExtra.smali`) from the routed context.

---

## 3. Caveats

- Testing evaluated Python 3.13 on macOS; while dict insertion ordering and set operations are standard in Python 3.7+, filesystem traversal relies on `sorted(base.rglob("*"))` in `workspace.py`, ensuring consistent cross-platform path sorting.
- The 9 xfailed tests currently present in `test_intent_router_adversarial.py` include Reviewer A's XML/resource findings (e.g., default xmlns handling, inverted hyphen sort, receiver priority inversion, 0-file fallback) as well as Reviewer B's round-robin starvation test.

---

## 4. Conclusion & Recommended Action

**Verdict: REQUEST_CHANGES**

The intent routing engine demonstrates excellent trigger matching, strict determinism, perfect deduplication, and sub-millisecond throughput under massive workspaces. However, the round-robin candidate starvation bug in `intent_router.py:979` violates the requirement that multi-intent composite requests fairly allocate budget up to 20 files without starving candidates.

### Actionable Fix for SWE / Implementer:
In `backend/src/noir/infrastructure/ai/intent_router.py`, replace lines 968-981 with:
```python
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
```
This ensures:
1. If all candidate lists are exhausted (`not any(round_idx < len(rc) ...)`), it exits early.
2. If `len(merged_paths) >= MAX_FILES_TOTAL`, it exits early.
3. If an intermediate round contains only duplicate files, it does **NOT** abort; it proceeds to subsequent rounds, preserving valid candidates.

---

## 5. Verification Method

1. **Execute Reviewer B Stress Test Suite**:
   ```bash
   cd /Users/kartik/Documents/ChatGPT/noir/backend
   .venv/bin/pytest tests/unit/test_intent_router_adversarial.py -k "test_composite_ or test_deterministic_ or test_boundary_ or test_deduplication_ or test_empty_ or test_massive_ or test_round_robin_" -v
   ```
2. **Execute Full Adversarial Suite**:
   ```bash
   cd /Users/kartik/Documents/ChatGPT/noir/backend
   .venv/bin/pytest tests/unit/test_intent_router.py tests/unit/test_intent_router_adversarial.py -v
   ```
3. **Verify Bug Invalidation Condition**:
   Apply the recommended fix to `intent_router.py:979`, remove `@pytest.mark.xfail` from `test_round_robin_merge_does_not_starve_candidates_on_duplicate_rounds`, and re-run pytest. The test must turn from XFAIL to PASS.
