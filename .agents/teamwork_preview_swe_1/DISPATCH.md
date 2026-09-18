## 2026-09-18T06:22:47Z
You are the SWE Light Orchestrator for this task.

Working directory for your metadata (BRIEFING.md, progress.md, plan.md, handoff.md, etc.):
`/Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_swe_1`

Workspace directory (project root):
`/Users/kartik/Documents/ChatGPT/noir`
Git branch: `new`

Authoritative User Request is recorded in:
`/Users/kartik/Documents/ChatGPT/noir/ORIGINAL_REQUEST.md` (and `/Users/kartik/Documents/ChatGPT/noir/.agents/ORIGINAL_REQUEST.md`)

Reference Material:
Implementation Plan: `/Users/kartik/.gemini/antigravity/brain/c357e8b2-346e-4b27-8827-ea19d5626405/implementation_plan.md`

Task Description & Requirements:
Implement a deterministic intent-based file routing subsystem in NOIR that maps user requests and APK runtime types directly to relevant workspace files for AI planning, bypassing expensive AI discovery calls when intents match and falling back to AI discovery when none do.

Requirements:
R1. Intent Detection & File Routing Engine
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

R2. AI Service Integration & Discovery Bypass
Update `backend/src/noir/application/ai_service.py` in `generate_plan()`:
- Evaluate `IntentRouter().route(request, context_tools, analysis)` before AI discovery.
- If intents match: bypass AI discovery calls (0 API calls), build context from routed files via `build_discovered_context()`, set `discovery_stop_reason = "intent_matched"`, and record `detected_intents` in the planning context.
- If no intent matches: log a warning (`"No intent matched for request; falling through to AI discovery"`) and execute existing discovery provider logic unchanged.

R3. Legacy Heuristic Removal & Consolidation
Clean up `backend/src/noir/infrastructure/ai/context.py`:
- Remove `smart_preselect_smali()` method.
- Remove obsolete regex constants (`_INTENT_TOAST_FLASH`, `_INTENT_LAUNCH_REDIRECT`, `_INTENT_NETWORK_EXFIL`).
- Ensure all selection and intent routing logic resides exclusively in `intent_router.py`.

R4. Automated Unit Tests
Add `backend/tests/unit/test_intent_router.py` with pytest test cases covering:
- Positive and negative trigger pattern matches for all 12 intents.
- Runtime scoping (e.g. Flutter intent only triggers for Flutter runtime/markers).
- Union matching for composite requests (e.g. rename app + toast on tap).
- Total file count capping (never exceeding 20).
- Fallback behavior and warning log verification when no intent matches.

Acceptance Criteria:
- All unit tests in `backend/tests/unit/test_intent_router.py` pass.
- Full test suite `pytest tests/unit/` passes.
- Maintain your `progress.md` and `BRIEFING.md` regularly in your working directory.
- When finished, send a message back to the Sentinel with your completion report.

## 2026-09-18T07:51:47Z
Liveness check (Iteration 12): Please record your latest progress in progress.md as Review Round 1 concludes and Review Round 2 begins.

## 2026-09-18T08:01:08Z
Liveness check (Iteration 14): Please update progress.md and report current status (review rounds / auditor status).
