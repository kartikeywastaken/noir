# Reviewer Round 1 Progress

## Status: COMPLETE

### Findings Identified & Fixed:
1. **Regex Word Boundary Trailing Defect in `unity_mono` (`\bc#\b`)**:
   - `c#` followed by `\b` fails because `#` is non-word (`\W`). Trailing word boundary required a word character after `#`. Prompts like "modify c# code" failed to trigger `unity_mono`.
   - Fixed by matching `(?:\bc#(?!\w))`.
2. **Regex Word Boundary Trailing Defect in `native_elf` (`\bc\+\+\b` and `\.so\b`)**:
   - `c++` followed by `\b` fails because `+` is non-word. Leading `\b` in `\.so` failed when preceded by whitespace. Prompts like "modify c++ code" or "patch .so library" failed to trigger `native_elf`.
   - Fixed by matching `(?:\bc\+\+(?!\w))` and `(?<!\w)\.so\b`.
3. **Regex Word Boundary Leading Defect in `xamarin_dotnet` (`\b\.net\b`)**:
   - `.` preceded by `\b` requires a word character before `.`. Prompts like "modify .net assemblies" failed to trigger `xamarin_dotnet`.
   - Fixed by matching `(?<!\w)\.net\b`.
4. **Missing Keyword in `ui_layout` pattern 3**:
   - `text` was present in pattern 2 but omitted in pattern 3 (`\b(change|modify...)\b.*?\b(button|color|layout|view|screen)\b`). Prompts like "modify text in layout" failed to trigger `ui_layout`.
   - Fixed by adding `text` to pattern 3.
5. **Inner Class Crowding Out Launcher Activity Smali**:
   - `MainActivity$1.smali`, `MainActivity$2.smali`, etc. sort before `MainActivity.smali` in ASCII order. For activities with >= 8 inner classes, `MainActivity.smali` was dropped due to `MAX_FILES_PER_INTENT = 8`.
   - Fixed by introducing `_smali_sort_key` prioritizing outer classes (`"$" not in path` or exact class name match) over inner classes (`"$" in path`).
6. **Unconventional Directory Layouts for Assemblies (Ledger #2)**:
   - Marker scan and Xamarin file selector now scan any directory containing `assemblies/` or `mono.android.dll` rather than assuming root `assemblies/`.
7. **Hybrid Frameworks Concurrency (Ledger #4)**:
   - Added adversarial test confirming `react_native_js` and `native_elf` trigger together on hybrid apps, deduplicating `.so` files cleanly without collisions.

### Verification:
- `backend/.venv/bin/pytest -p no:cacheprovider tests/unit/test_intent_router.py tests/unit/test_intent_router_adversarial.py -v`: 74 passed in 2.16s
- `backend/.venv/bin/pytest -p no:cacheprovider tests/unit/ -v`: 295 passed, 6 skipped in 5.11s
- `backend/.venv/bin/pytest -p no:cacheprovider tests/integration/ -v`: 79 passed, 4 skipped in 8.67s
