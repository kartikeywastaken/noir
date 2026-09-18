# Scope: NOIR Deterministic Intent File Routing Hardening

## Architecture
- `backend/src/noir/infrastructure/ai/intent_router.py`: Intent routing engine with 12 intent categories, runtime markers, union merging, file caps, XML wildcard handling, priority sorting, obfuscated class anchoring, and 0-file fallback.
- `backend/src/noir/application/ai_service.py`: Intent routing bypass before AI discovery with clean fallback.
- `backend/src/noir/infrastructure/ai/context.py`: Cleaned context builder (smart_preselect_smali excised).
- `backend/tests/unit/test_intent_router.py` & `backend/tests/unit/test_intent_router_adversarial.py`: 119 comprehensive unit and adversarial tests.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Specialized Reviews & Stress Testing | Reviewers A, B, C audit XML/qualifiers, multi-intent composite caps, and cross-framework runtimes | None | DONE |
| M2 | Refinements & Bug Fixes | Fix all 9 edge cases and defects uncovered by reviews | M1 | DONE |
| M3 | Comprehensive Verification | Run router tests (119/119 pass), full unit suite (340/340 pass), integration suite (79/79 pass) | M2 | DONE |
| M4 | Forensic Audit | Validate implementation integrity with zero hardcoded cheats or bypasses (7/7 checks CLEAN) | M3 | DONE |
| M5 | Git Commit & Push | Commit with message "changed ai discovery to intent discovery" on branch `new` (fe57a8b) and push | M4 | DONE |
| M6 | Sentinel Completion Report | Send final report back to Sentinel conversation bf3870e7-0614-41c7-8d66-de1bc1af8f98 | M5 | IN_PROGRESS |

## Interface Contracts
- `IntentRouter.route(request: str, context_tools: ContextTools, analysis: Optional[AnalysisResult]) -> IntentRouteResult`
- `IntentRouteResult`: `matched_intents: list[str]`, `seen_files: dict[str, str]`, `binary_inspections: dict[str, Any]`, `stop_reason: str`
- Total file cap: <= 20 files total, <= 8 files per intent.
- Discovery bypass: `plan.discovery_api_calls = 0` and `plan.discovery_stop_reason = "intent_matched"` when matching intents and non-empty files; otherwise log warning and fall back to discovery.
