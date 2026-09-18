# Reviewer Round 1 Briefing

## Target
Intent-based file router subsystem in NOIR (`intent_router.py`, `context.py`, `ai_service.py`, `test_intent_router.py`).

## Executive Summary
Prior attempt implemented the core architecture (R1-R4) but introduced multiple subtle functional defects in regex boundaries (`c#`, `c++`, `.net`), keyword omissions (`text` in `ui_layout`), and smali candidate ranking where anonymous inner classes (`$1`, `$2`, etc.) crowded out the primary activity smali file (`MainActivity.smali`) under the 8-file cap.
All issues were root-caused, repaired, and confirmed with 4 new adversarial test cases and full unit/integration suite verification.

## Key Changes
- `backend/src/noir/infrastructure/ai/intent_router.py`:
  - Repaired regex boundary patterns for symbols/punctuation (`c#`, `c++`, `.net`, `.so`).
  - Added `text` to `ui_layout` verb-leading pattern.
  - Implemented `_smali_sort_key` prioritizing outer classes over inner anonymous classes.
  - Added support for unconventional assembly directories and `Mono.Android.dll` markers.
- `backend/tests/unit/test_intent_router_adversarial.py`:
  - Added 4 test suites attacking boundaries, inner class crowding, hybrid framework deduplication, and unconventional directory markers.

## Test Results
- Unit Suite: 295 passed, 6 skipped.
- Integration Suite: 79 passed, 4 skipped.
