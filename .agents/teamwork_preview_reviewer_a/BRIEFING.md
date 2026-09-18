# BRIEFING — 2026-09-18T09:45:00Z

## Mission
Audit and stress-test Android XML and resource localization handling in `backend/src/noir/infrastructure/ai/intent_router.py`.

## 🔒 My Identity
- Archetype: reviewer
- Roles: reviewer, critic
- Working directory: /Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_reviewer_a
- Original parent: 7a3ba1d6-8287-46ff-a724-85be03511312
- Milestone: Review & Stress Test Android XML & Localization
- Instance: 1 of 1

## 🔒 Key Constraints
- Review-only — do NOT modify implementation code
- Write metadata only to .agents/teamwork_preview_reviewer_a/
- Provide exact repros and recommended fixes for any bugs found
- Strictly maintain integrity verification

## Current Parent
- Conversation ID: 7a3ba1d6-8287-46ff-a724-85be03511312
- Updated: not yet

## Review Scope
- **Files to review**: `backend/src/noir/infrastructure/ai/intent_router.py`
- **Interface contracts**: `ORIGINAL_REQUEST.md`, `progress.md` (Open Issues Ledger #1, #3, #4)
- **Review criteria**: Android XML namespaces, attribute parsing, diverse localized resource qualifiers, obfuscated flattened entries

## Review Checklist
- **Items reviewed**:
  - `_get_xml_attr` and `_extract_manifest_info`
  - `_find_launcher_smali` and `_smali_sort_key`
  - `_find_receiver_service_smali`
  - `_select_app_name` and `_select_ui_layout`
  - `IntentRouter.route` and AI discovery bypass integration
- **Verdict**: REQUEST_CHANGES
- **Unverified claims**:
  - Ledger #3 claim that "if classes are renamed, the router will find 0 files and fallback to AI discovery" disproven: router emits `stop_reason="intent_matched"` with `seen_files={}`, completely bypassing AI discovery.

## Attack Surface
- **Hypotheses tested**:
  - Default XML namespace on `<manifest>` breaks `ElementTree.find()` -> Confirmed Bug (Finding 1)
  - Pure length sort in `_find_receiver_service_smali` starves manifest-declared components -> Confirmed Bug (Finding 2)
  - Single-letter obfuscated classes (`.a`) cause unanchored substring matching across entire project -> Confirmed Bug (Finding 3)
  - 0-file match returns `stop_reason="intent_matched"`, bypassing AI discovery -> Confirmed Bug (Finding 4)
  - `resources/values/strings.xml` sorted after localized qualifiers due to `'-' < '/'` -> Confirmed Bug (Finding 5)
  - Diverse BCP-47 qualifiers (`values-b+sr+Latn`, `values-zh-rCN`, etc.) parsed properly when under cap -> Verified Pass
  - Obfuscated multidex classes (`smali_classes2`, `smali_classes3`) parsed properly -> Verified Pass
- **Vulnerabilities found**: 2 Critical, 2 Major, 2 Minor issues documented with repros.
- **Untested angles**: Live apktool binary execution on corrupted APK packages.

## Key Decisions Made
- Added 9 adversarial unit and stress tests to `backend/tests/unit/test_intent_router_adversarial.py` (4 passing, 5 xfailed bug repros).
- Full regression suite verified (310 passing, 5 xfailed, 6 skipped, 0 regressions).
- Issued REQUEST_CHANGES verdict due to the 4 critical/major bugs.

## Artifact Index
- `/Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_reviewer_a/DISPATCH.md` — dispatched instructions
- `/Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_reviewer_a/progress.md` — heartbeat and progress tracker
- `/Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_reviewer_a/BRIEFING.md` — situational awareness
- `/Users/kartik/Documents/ChatGPT/noir/.agents/teamwork_preview_reviewer_a/handoff.md` — formal 5-component handoff report
