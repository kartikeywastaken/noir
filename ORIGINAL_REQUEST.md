# Original User Request

## 2026-09-18T06:17:11Z

This is a single self-contained fix; keep it small and focused. Implement a deterministic intent-based file routing subsystem in NOIR that maps user requests and APK runtime types directly to relevant workspace files for AI planning, bypassing expensive AI discovery calls when intents match and falling back to AI discovery when none do.

Working directory: `/Users/kartik/Documents/ChatGPT/noir`
Git branch: `new`
Integrity mode: development

## Reference Material
- Implementation Plan: `/Users/kartik/.gemini/antigravity/brain/c357e8b2-346e-4b27-8827-ea19d5626405/implementation_plan.md`
- Relevant existing files:
  - `backend/src/noir/application/ai_service.py` (lines 220-275 for discovery flow)
  - `backend/src/noir/infrastructure/ai/context.py` (contains `smart_preselect_smali` to be removed and replaced)
  - `backend/src/noir/domain/models.py` (`AnalysisResult` runtime fields)
  - `backend/src/noir/analysis/analyzer.py` (runtime detection logic)

## Requirements

### R1. Intent Detection & File Routing Engine
Create `backend/src/noir/infrastructure/ai/intent_router.py` with:
- `IntentRouteResult` dataclass: `matched_intents: list[str]`, `seen_files: dict[str, str]`, `binary_inspections: dict[str, Any]`, `stop_reason: str`.
- `IntentRule` dataclass: `intent_id`, `trigger_patterns: list[re.Pattern]`, `apk_types: set[str]`, `file_selector: Callable`.
- Registry of 12 intent categories:
  1. `app_name`: label / rename / display name / app name (`AndroidManifest.xml`, `res/values*/strings.xml`).
  2. `toast_flash`: toast / flash / message on UI interaction / click / tap (launcher activity smali, `MainActivity.smali`).
  3. `network_ping`: ping / send / post / http / endpoint on launch or start (`AndroidManifest.xml`, launcher activity smali, network client smali).
  4. `ui_layout`: button / text / color / layout / view / screen modification (`res/layout/*.xml`, `res/values/strings.xml`).
  5. `permission`: add / remove / grant / revoke permission (`AndroidManifest.xml`).
  6. `receiver_service`: broadcast receiver / background service add / modify (`AndroidManifest.xml`, matching receiver/service smali).
  7. `react_native_js`: React Native / JS bundle / Hermes (`index.android.bundle`, `libhermes.so`, `AndroidManifest.xml`).
  8. `flutter_dart`: Flutter / Dart / libapp (`libapp.so`, `libflutter.so`, Flutter assets).
  9. `unity_mono`: Unity / Mono / C# (`Assembly-CSharp.dll`, managed DLLs in `assets/`, `libmono*.so`).
  10. `unity_il2cpp`: Unity / IL2CPP (`libil2cpp.so`, `global-metadata.dat`).
  11. `native_elf`: Native / ELF / JNI (.so files under `lib/`).
  12. `xamarin_dotnet`: Xamarin / .NET / Mono (`assemblies/*.dll`).
- Runtime detection using analysis metadata first (`analysis.runtimes`), falling back to decoded directory marker scanning.
- Union matching across all matching intents.
- File caps: maximum 8 files per intent, maximum 20 files total.

### R2. AI Service Integration & Discovery Bypass
Update `backend/src/noir/application/ai_service.py` in `generate_plan()`:
- Evaluate `IntentRouter().route(request, context_tools, analysis)` before AI discovery.
- If intents match: bypass AI discovery calls (0 API calls), build context from routed files via `build_discovered_context()`, set `discovery_stop_reason = "intent_matched"`, and record `detected_intents` in the planning context.
- If no intent matches: log a warning (`"No intent matched for request; falling through to AI discovery"`) and execute existing discovery provider logic unchanged.

### R3. Legacy Heuristic Removal & Consolidation
Clean up `backend/src/noir/infrastructure/ai/context.py`:
- Remove `smart_preselect_smali()` method.
- Remove obsolete regex constants (`_INTENT_TOAST_FLASH`, `_INTENT_LAUNCH_REDIRECT`, `_INTENT_NETWORK_EXFIL`).
- Ensure all selection and intent routing logic resides exclusively in `intent_router.py`.

### R4. Automated Unit Tests
Add `backend/tests/unit/test_intent_router.py` with pytest test cases covering:
- Positive and negative trigger pattern matches for all 12 intents.
- Runtime scoping (e.g. Flutter intent only triggers for Flutter runtime/markers).
- Union matching for composite requests (e.g. rename app + toast on tap).
- Total file count capping (never exceeding 20).
- Fallback behavior and warning log verification when no intent matches.

## Acceptance Criteria

### Routing Accuracy & Bounds
- [ ] Prompts matching any of the 12 intents correctly resolve the expected candidate files.
- [ ] Prompts combining multiple intents merge files without duplicates.
- [ ] Output `seen_files` never exceeds 20 files total and 8 files per intent.
- [ ] Runtime-specific intents (Flutter, Unity, React Native, etc.) are skipped when runtime markers are absent.

### Pipeline Integration
- [ ] Plan generation with matching intent sets `plan.discovery_api_calls = 0` and `plan.discovery_stop_reason = "intent_matched"`.
- [ ] Plan generation with no matching intent logs a warning and executes regular discovery.
- [ ] `smart_preselect_smali` is completely excised from `context.py` with zero regressions in existing tests.

### Verification
- [ ] All tests in `backend/tests/unit/test_intent_router.py` pass.
- [ ] Full test suite `pytest tests/unit/` passes.

## 2026-09-18T09:11:46Z

The user requested: "use multiple agents". Use a full team of autonomous agents to continue the deterministic intent-based file routing subsystem in NOIR from its current state, conduct comprehensive multi-agent reviews and stress testing, complete the victory audit, and push the verified changes.

Working directory: `/Users/kartik/Documents/ChatGPT/noir`
Git branch: `new`
Integrity mode: development

## Current State & Context
The core implementation and initial review round are already complete and verified green on branch `new`:
- `backend/src/noir/infrastructure/ai/intent_router.py` (R1: 12 intent categories, runtime markers, union merging, file caps, lookaround regex fixes, outer-class prioritization, dynamic assembly scanning).
- `backend/src/noir/application/ai_service.py` (R2: intent routing bypass before AI discovery).
- `backend/src/noir/infrastructure/ai/context.py` (R3: legacy `smart_preselect_smali` completely excised).
- `backend/tests/unit/test_intent_router.py` and `test_intent_router_adversarial.py` (R4: 85 passing tests).

Previous review progress is logged in `.agents/teamwork_preview_swe_1/progress.md` with the following Open Issues Ledger to resolve:
1. XML namespaces and localized resource qualifiers in `AndroidManifest.xml` and `strings.xml` (verify robust extraction across non-standard namespace prefixes, complex qualifiers like `res/values-b+sr+Latn/strings.xml`, etc.).
2. Composite multi-intent requests with 3+ simultaneous broad intents to confirm deterministic ordering and file capping without duplicates.
3. Heavily obfuscated APK fallback behavior when class names and manifest entries are flattened.
4. Independent multi-agent adversarial reviews and victory audit.

## Requirements

### R1. Deep Review & Edge-Case Hardening
Deploy multiple specialized reviewer agents to independently audit and stress-test the intent routing subsystem:
- Reviewer A: Android XML and resource localization auditing (custom namespaces, attribute parsing, diverse `res/values*` qualifiers).
- Reviewer B: Multi-intent composite stress testing (3+ concurrent intents, boundary cap enforcement at 20 files total and 8 per intent, priority ordering).
- Reviewer C: Cross-framework runtime detection edge cases (Unity Mono vs IL2CPP, React Native vs Hermes, Xamarin .NET assemblies, native ELF).

### R2. Refinement & Bug Fixes
Implement any edge-case fixes or optimizations identified by the reviewer agents in `backend/src/noir/infrastructure/ai/intent_router.py` or `backend/src/noir/application/ai_service.py`.

### R3. Comprehensive Multi-Agent Verification & Audit
Run automated tests across all modified and existing test suites:
- All unit and adversarial tests in `backend/tests/unit/test_intent_router*.py`.
- Full backend unit suite (`pytest tests/unit/`).
- Backend integration suite (`pytest tests/integration/`).
Perform a final victory audit ensuring zero open defects and 100% test pass.

### R4. Final Git Commit & Push
Once all criteria and the final victory audit have passed:
- Stage and commit the verified changes on branch `new`.
- Use the exact commit message: `changed ai discovery to intent discovery`
- Push the commit to the remote repository.

## Acceptance Criteria

### Robustness & Coverage
- [ ] Intent router handles complex Android XML namespaces and localized resource qualifiers without error.
- [ ] Composite requests matching 3+ broad intents deterministically enforce the 20-file total cap and 8-file per-intent cap with zero duplicated file paths.
- [ ] Runtime detection correctly discriminates between all 12 supported APK runtime categories under both analysis metadata and raw directory marker scanning.

### Pipeline Integration & Regressions
- [ ] Discovery bypass in `ai_service.py` functions seamlessly for all intent-matched requests with 0 external discovery API calls.
- [ ] Non-matching requests log a clear warning and cleanly fall through to regular AI discovery.
- [ ] Zero regressions in the full unit test suite and integration test suite.

### Verification & Delivery
- [ ] All unit, adversarial, and integration tests pass with 100% success.
- [ ] Formal multi-agent completion audit confirms all ledger items resolved.
- [ ] Changes are committed on branch `new` with message `"changed ai discovery to intent discovery"` and pushed.

