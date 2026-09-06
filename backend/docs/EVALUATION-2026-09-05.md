# NOIR seven-APK production evaluation — 2026-09-05

## Follow-up remediation and expanded evaluation

The two deterministic-engine defects found by the initial run have been fixed and deployed:

- Patch response schemas now bind each exact approved path to only its approved operation type.
  The strict host-side `(path, operation)` approval check remains in place and now reports the
  mismatched pair if a provider ever violates the schema.
- `xml_resource_update`, `xml_resource_add`, and `xml_resource_remove` now mutate one named XML
  resource element inside the existing `<resources>` document. They no longer replace or delete
  the whole resource file. Manifest operations can also use exact pre-change attributes to select
  one repeated element such as an `intent-filter`.

Production reruns then completed successfully:

| APK | AI prepare | Finish/build/sign | S3 download | Result |
|---|---:|---:|---:|---|
| Markor 2.16.1 | 43.78 s | 27.88 s | 31.08 s | **Passed** |
| Acode 1.13.3 | 25.70 s | 38.97 s | 88.51 s | **Passed** |

The Acode rerun also caught the XML-resource whole-file replacement defect before rebuild. The bad
patch was undone, the orphaned backup file was removed, and the corrected rerun changed only the
targeted `app_name` resource and HTML title. The signed output verified successfully.

Three additional official open-source APKs were then imported through the public resumable-upload
API and durably stored in S3:

1. [NewPipe v0.29.1](https://github.com/TeamNewPipe/NewPipe/releases/tag/v0.29.1)
2. [Shattered Pixel Dungeon v3.3.8](https://github.com/00-Evan/shattered-pixel-dungeon/releases/tag/v3.3.8)
3. [LocalSend v1.18.2](https://github.com/localsend/localsend/releases/tag/v1.18.2)

| APK | Runtime | Requested change | Upload | AI prepare | Finish | Download | Result |
|---|---|---|---:|---:|---:|---:|---|
| NewPipe | Dalvik + native | App label plus exact Settings title | 141.71 s | 46.97 s | 34.07 s | 8.48 s | **Passed** after discovery fix |
| Shattered Pixel Dungeon | Dalvik + native | Toast in launcher `onCreate` | 222.88 s | 43.94 s | 13.99 s | 46.30 s | **Passed** |
| LocalSend ARM64 | Flutter + Dalvik + native | Android label; conditional in-app text | 326.45 s | 89.54 s | 14.33 s | interrupted | **Backend passed**; AOT edit correctly unsupported |

- NewPipe: project `adb594d303f84048`, plan `888cf01216524841`, patch
  `049f5b1a3f4f48b8`, build `ce0e78e2a8964a7e`.
- Shattered Pixel Dungeon: project `653417db11804e49`, plan `1b8fc580c5344ded`, patch
  `282c7d5b695d4b1b`, build `5aadfe3390784e31`.
- LocalSend: project `c4cc3063276743c4`, plan `4e188cdf68404e04`, patch
  `9526ca7d53fa4d72`, build `8042e5e6fb6c4988`.

NewPipe initially changed only the launcher label because discovery listed resource directories but
never read `res/values/strings.xml`. NOIR now host-seeds the default Android string table for
text/title/menu/settings requests. This costs zero provider calls. After reverting the first test
patch and rerunning from the pristine revision, Gemini produced two exact `replace_block`
operations for `app_name` and `settings`; rebuild, signing, v2/v3 verification, S3 storage, and
download all succeeded.

Shattered Pixel Dungeon produced one exact `smali_insert_at_anchor` operation against
`AndroidLauncher.onCreate(Landroid/os/Bundle;)V`. It inserted the test Toast after the existing
`Activity.onCreate` call and completed validation, rebuild, signing, verification, S3 storage, and
download.

LocalSend exposed and verified a runtime-classification defect. Detection of `libflutter.so` or
`assets/flutter_assets` now adds `flutter`, making it the primary runtime while retaining the
additive `dalvik` and `native` capabilities. NOIR changed the Android manifest label and correctly
reported the requested in-app tab text as unsupported because this release stores Dart UI strings
in the ARM64 AOT binary rather than an editable localization asset. The signed 46,787,622-byte APK
verified successfully and is present in S3. A full download to the laptop was interrupted after
the route slowed substantially; a one-byte range request independently confirmed the complete S3
object length. A server-side `aapt2 dump badging` independently reports application label
`NOIR Send`; `apksigner` reports valid v2/v3 signatures. The signed APK SHA-256 is
`ad4099647af2165594e8e5603575fdf9eb85d49fab626bc53f1f17a6b2d7d4b6`.

The first attempt for each new APK stopped at planning because EC2 had accidentally been
provisioned with the same Gemini key in both encrypted slots and that account's daily quota was
exhausted. A fresh key was validated against `gemini-3.6-flash`, installed into the encrypted
generation slot, and all three existing projects resumed without re-uploading.

The expanded run also confirms that four concurrent resumable upload ranges are accepted and
durably recorded out of order. Transfer time is still dominated by the India-to-Stockholm route;
no NOIR size or request-rate limit fired. The complete local suite passes with 293 tests passing
and 4 skipped.

## Final Compose regression run and artifact cleanup

[Read You](https://github.com/Ashinch/ReadYou), a Jetpack Compose application, was run through the
production workflow after the Android string-table discovery fix. The request changed the app
label from `Read You` to `NOIR Reader` and the default-English Settings title from `Settings` to
`Settings — NOIR TEST`. Gemini generated two exact edits in `res/values/strings.xml`; no unrelated
resource or Smali edit was made.

| APK | AI prepare | Finish/build/sign | S3 download | Total after server-local import | Result |
|---|---:|---:|---:|---:|---|
| Read You | 43.436 s | 34.632 s | 42.397 s | 129.754 s | **Passed** |

- Project `772d1d400ccb4bd1`, plan `8d9f8154d5954cd0`, patch `a1791310495447ba`,
  build `eeebe02f63264827`.
- Signed APK size: 11,795,811 bytes.
- Signed APK SHA-256:
  `7f0c854f7bfb0c09f06867a5b16eee7e5670299df6e3a19e1d05880e17170abb`.
- Independent `apksigner` verification reported valid v2 and v3 signatures. Independent Android
  resource inspection confirmed both requested values in the rebuilt artifact.

After the evaluation, all 26 project prefixes were intentionally removed from S3. AWS reported
44 objects (969.1 MB) deleted with zero failures, and the project prefix was verified empty. All
remaining project workspaces, project database rows, and stale upload sessions were then removed
from EC2. Its root filesystem changed from 2.4 GB free (87% used) to 11 GB free (43% used), and
the live NOIR data directory was reduced to 242 MB. A compressed pre-cleanup database backup
remains at
`/var/lib/noir/backups/noir-pre-eval-cleanup-20260905T1810Z.db.gz`.

## Initial four-APK result (before remediation)

Four fresh, source-backed APKs were evaluated against the deployed EC2 backend using the
same three-step prepare/approve/finish workflow used by the Flutter client. The matrix covered
ordinary Dalvik/Smali, Cordova/hybrid web, Dalvik with native ELF libraries, and Unity/Mono.

- Runtime detection, APKtool decoding, analysis, and durable S3 storage: **4/4 passed**.
- AI planning: **4/4 returned a structured plan**, including one explicit supported refusal.
- Automatic patch accepted by the deterministic engine: **1/4**.
- Accepted patch rebuilt, signed, verified, stored in S3, and downloaded: **1/1 passed**.
- Complete requested outcomes: **1/4 passed**.
- Public resumable upload attempts: **2/3 APKs completed**; the third stalled in both parallel
  and sequential modes before an 8 MiB range was acknowledged.

This is not yet a reliable four-runtime production pipeline. The core APKtool/build/sign path is
fast and worked when it received an accepted patch. The current blockers are upload request size,
plan-to-patch operation binding, and insufficient method-level evidence discovery for Mono.

No APK was installed or executed on a phone during this evaluation. “Passed” below means static
workspace validation, successful APKtool rebuild, S3 persistence, and cryptographic APK signature
verification—not runtime/UI verification on a device.

## Test environment and method

- Backend: deployed NOIR EC2 service at the current repository revision.
- AI: `gemini-3.6-flash` for plan and patch generation; OpenRouter discovery enabled with two
  discovery rounds.
- Storage: private S3 bucket; every stored object checked with `HeadObject` and reported AES-256
  server-side encryption.
- Upload protocol: server-advertised 8 MiB ranges, initially four connections. After WireGuard
  stalled, one sequential connection was also tested without changing server configuration.
- Workflow: import -> compact analysis -> `/workflow/prepare` -> exact plan/patch review hashes ->
  `/workflow/finish` -> validation -> APKtool build -> sign -> verify -> S3 download.
- Timing: wall-clock seconds measured by the evaluation client. Server-local import measurements
  exclude the failed public network upload and are labelled accordingly.

The APKs were obtained from their official project locations:

1. [Markor v2.16.1](https://github.com/gsantner/markor/releases/tag/v2.16.1)
2. [Acode v1.13.3](https://github.com/Acode-Foundation/Acode/releases/tag/v1.13.3)
3. [WireGuard official Android downloads](https://download.wireguard.com/android-client/)
4. [Escape Rouge 2.0](https://github.com/jacksonp/EscapeRouge/releases/tag/2.0)

## Summary matrix

| APK | Detected runtime | Difficulty | Upload | Import/decode/analyze/S3 | AI prepare | Finish/build/sign | Download | Outcome |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Markor 2.16.1 | Dalvik | Easy | 208.30 s | 12.16 s | 49.42 s | — | — | Failed: patch operation exceeded exact plan tuple |
| Acode 1.13.3 | Dalvik + hybrid web | Medium | 67.90 s | 14.56 s | 38.02 s; retry 40.75 s | — | — | Failed twice: same plan/patch contract mismatch |
| WireGuard 1.0.20260315 | Dalvik + native | Hard | Stalled >505 s parallel and >248 s sequential | 5.36 s server-local | 27.67 s | 13.66 s | 62.55 s | **Passed** |
| Escape Rouge 2.0 | Dalvik + Mono + native | Hard | Not repeated after confirmed upload failure | 2.50 s server-local | 35.81 s | — | — | Supported refusal: exact method IL evidence not found |

The successful WireGuard engine path took approximately **46.69 seconds** from server-local
import through signed/verified output (`5.36 + 27.67 + 13.66`). Downloading the 17.1 MB signed
artifact to the laptop added **62.55 seconds**. The evaluator’s post-import wall clock was
109.70 seconds; including the separately measured server-local import gives 115.06 seconds.

## Case 1 — Markor, ordinary Dalvik/Smali, easy

### Input

- Package: `net.gsantner.markor`
- Version: `2.16.1`
- Size: 12,065,877 bytes
- SHA-256: `e88cdcced7aa3dca25e6b9c7a9bdcfad3e3988ee545be951f42bf9441b5e46bf`
- Detected: `dalvik`, two Smali directories, no compatibility warnings.
- Project: `d79f55f2f2b54d29`

### Requested change

Change every application/launcher label to `NOIR Notes`, without changing the package, code,
permissions, icons, behavior, or unrelated strings.

### Outcome

The plan was sensible and targeted:

- `AndroidManifest.xml` with `replace_block`;
- `res/values/strings.xml` with `xml_resource_update`.

Patch generation failed with `Patch operations exceed the approved plan`. The workspace remained
pristine and no APK was built. This easy change therefore does **not** currently pass the fully
automatic workflow.

One earlier attempt also encountered a Gemini daily-quota response. A temporary diagnostic routing
to the independently stored discovery credential returned `401 ACCESS_TOKEN_TYPE_UNSUPPORTED`,
confirming that credential is not a usable Gemini API key. Normal generation routing was restored
immediately; the final comparable attempt used the normal valid generation key and reached the
operation-contract failure above.

### Time to terminal result

- Public upload: 208.30 s
- Import/decode/analyze/S3: 12.16 s
- Final AI prepare attempt: 49.42 s
- Comparable end-to-failure time: approximately **269.89 s**

## Case 2 — Acode, hybrid web, medium

### Input

- Package: `com.foxdebug.acode`
- Version: `1.13.3`
- Size: 17,253,620 bytes
- SHA-256: `0a83f21630375358c22d7027dddcd00c312756894afa1d27d0c277f421e6384a`
- Detected: `dalvik` + `hybrid_web`, three Smali directories, `assets/www/index.html`, no
  compatibility warnings.
- Project: `e11be1d15d8d476f`

### Requested change

Change the Android label to `NOIR Code Lab` and only the exact HTML title
`<title>Acode</title>` to `<title>NOIR Code Lab</title>`.

### Outcome

The plan correctly named both real files:

- `AndroidManifest.xml` using `manifest_update`;
- `assets/www/index.html` using `replace_block`.

Patch generation failed twice with `Patch operations exceed the approved plan`. Since the same
saved plan and same error recurred, this is deterministic enough to treat as a product defect, not
a one-off malformed model response. No workspace mutation or build occurred.

### Time to terminal result

- Public upload: 67.90 s
- Import/decode/analyze/S3: 14.56 s
- First AI prepare: 38.02 s
- First end-to-failure time: approximately **120.48 s**
- Retry from the already imported pristine workspace: 40.75 s, same failure

## Case 3 — WireGuard, native-bearing APK, hard

### Input

- Package: `com.wireguard.android`
- Version: `1.0.20260315`
- Size: 16,943,472 bytes
- SHA-256: `f92971bc804f4c448e8845ae97e426095eab06ec09a2473a3fa2cfe7288e3298`
- Detected: `dalvik` + `native`, two Smali directories, 16 native libraries across
  `arm64-v8a`, `armeabi-v7a`, `x86`, and `x86_64`.
- Project: `3f1dbe9f96e7445b`

### Requested change

Show `NOIR native wrapper test` once when the launcher Activity is created, using exact launcher
Smali, while preserving all VPN behavior and every `.so` library.

### Outcome

**Passed the full automatic patch/build/sign workflow.** Discovery selected:

- `smali/com/wireguard/android/activity/MainActivity.smali`
- class `Lcom/wireguard/android/activity/MainActivity;`
- method `onCreate(Landroid/os/Bundle;)V`
- a unique call to `onBackStackChanged()` as the insertion anchor.

The patch inserted a standard Android Toast. The deterministic engine bound it to the complete
file preimage hash, applied one operation, validated the workspace, rebuilt with APKtool 3.0.3,
signed it, and verified APK Signature Schemes v2 and v3. The 16 native libraries were not modified.

This proves that NOIR can make a non-trivial Smali change in an APK that also ships substantial
native code. It does **not** prove arbitrary native ELF patching; the requested and accepted change
was deliberately made in the Android wrapper layer.

- Plan: `945bcea93aca43b7`
- Patch: `f38ff1b8b95c48cd`
- Build: `40ccac532852421a`
- Signed size: 17,118,548 bytes
- Signed SHA-256: `838e5f287e37a20bc4a8e6a23ef7c4de02be85e5822d0a9952edef0e4f71fde2`
- Local evaluation copy: `/private/tmp/noir-eval-2026-09-05/wireguard-signed.apk`

### Time

- Public resumable upload, four connections: stopped after 505.39 s; no 8 MiB range acknowledged
- Public resumable upload, one connection: stopped after 248.51 s; no 8 MiB range acknowledged
- Server-local import/decode/analyze/S3 fallback: 5.36 s
- AI plan + patch preview: 27.67 s
- Apply + validate + rebuild + sign + verify + report: 13.66 s
- S3 download to laptop: 62.55 s

## Case 4 — Escape Rouge, Unity/Mono, hard

### Input

- Package: `com.agorite.escaperouge`
- Version: `2.0`
- Size: 24,574,463 bytes
- SHA-256: `83d688652c278e4ffab959bf863e8692717726948bf3815af33ed3b74b5b133c`
- Detected: `dalvik` + `mono` + `native`; `Assembly-CSharp.dll` and the Unity/Mono managed
  assembly set were found; five native libraries for `armeabi-v7a`.
- Project: `672156fde10947e7`

### Requested change

Change only the existing managed Pause-menu label `Resume` to `Resume — NOIR TEST` using an exact,
hash-bound CIL method-body replacement. Refuse instead of guessing when evidence is incomplete.

### Outcome

NOIR correctly classified the APK and inspected `Assembly-CSharp.dll`, but returned a supported
refusal after two discovery rounds. The transcript shows:

1. host-seeded `UnityPlayerActivity.smali` launcher evidence;
2. `inspect_binary` on `Assembly-CSharp.dll`, returning 14,329 bytes;
3. the same `inspect_binary` call again, served from cache with zero new evidence.

It never obtained the exact CIL body/hash of the Pause-menu rendering method, so it generated no
patch. This refusal was correct under the deterministic preimage policy, but it exposes a missing
discovery capability rather than an unmodifiable APK.

### Time to supported refusal

- Server-local import/decode/analyze/S3: 2.50 s
- AI discovery and plan: 35.81 s
- Engine time to terminal result: approximately **38.31 s**

## Confirmed S3 state

`HeadObject` succeeded for all four original APKs and for the successful WireGuard signed APK.
Every object reported `ServerSideEncryption: AES256` and the expected byte length:

- Markor original: 12,065,877 bytes
- Acode original: 17,253,620 bytes
- WireGuard original: 16,943,472 bytes
- WireGuard signed: 17,118,548 bytes
- Escape Rouge original: 24,574,463 bytes

## Root causes found

### 1. P0 — patch schema does not bind operation type to its file

The patch response schema independently enumerates all approved paths and all approved operation
types. With a two-file plan, Gemini may legally emit an approved operation type against the wrong
approved file according to the schema. `PatchService` then correctly rejects the `(path,
operation)` pair because it was not present in the plan.

- Schema construction: `backend/src/noir/infrastructure/ai/gemini.py`, `_patch_response_schema`
- Exact enforcement: `backend/src/noir/application/patch_service.py`, lines 164–166

Recommended fix: keep the strict guard, but make the generated schema a per-file discriminated
union (`oneOf`) so each path permits only its own planned operation. Also include the exact
path-to-operation map in the patch prompt and return the mismatched pair in the error/audit.
Do not broadly relax the safety check to “any approved path plus any approved operation.”

### 2. P0 — 8 MiB upload ranges are too failure-expensive

The backend advertises 8 MiB chunks. On the failing route, Caddy never delivered a complete large
request body to the application, so no large range was acknowledged even though the TCP connection
remained established. Retrying restarts the entire range. Four-way parallelism did not solve it;
one sequential connection also stalled.

- Default range size: `backend/src/noir/domain/config.py`, line 167
- HTTP body handling: `backend/src/noir/api/app.py`, upload append route
- Durable range commit: `backend/src/noir/application/upload_service.py`

Recommended fix: advertise 1–2 MiB ranges, retain bounded parallelism, add a total per-range write
deadline, and automatically fall back from parallel to sequential after repeated failures. The
better long-term design is presigned S3 multipart upload from the phone, followed by a backend
finalize call that verifies size/hash/ownership before import. That removes EC2/Caddy from the
upload data path while preserving private user prefixes.

### 3. P1 — Mono discovery cannot request method-level IL

`ContextTools` already implements `read_method_il`, but the discovery tool catalog exposes only
workspace search/list/read and `inspect_binary`. The `inspect_binary` executor also calls the
binary inspector with `user_request=""`, discarding the request text that could rank relevant
methods. The two-round agent consequently repeated the cached assembly inspection and stopped.

- Missing discovery declaration/dispatch: `backend/src/noir/infrastructure/ai/discovery.py`
- Existing unused method reader: `backend/src/noir/infrastructure/ai/context.py`,
  `read_method_il`

Recommended fix: expose a structured `read_method_il(path, type_name, method_signature)` discovery
tool, pass the original request into binary inspection, and make a cached duplicate inspection a
no-progress signal. For string-driven requests, add host-side CIL string-reference search so the
first round returns candidate methods and the second round reads the exact candidate body/hash.

### 4. P1 — AI capacity is inconsistent

The first Markor attempt received a daily-quota error from the normal generation account. Later
Acode, WireGuard, and Mono calls succeeded, so this behaved as a transient provider/quota event
rather than a persistent server outage. The separately stored “discovery” credential is not a
valid Gemini API key and returns `401 ACCESS_TOKEN_TYPE_UNSUPPORTED`; OpenRouter currently masks
that for discovery, but it is not a usable Gemini failover credential.

Recommended fix: replace that invalid credential, add a real generation-provider fallback, and
surface provider/quota status before a user spends minutes uploading. Retries should be used for
short transient errors, not deterministic daily quota exhaustion.

## What NOIR can honestly claim from this run

It can currently:

- identify all four tested runtime families correctly;
- decode and inventory all four real APKs;
- persist every successfully imported original to private encrypted S3 storage;
- make and validate a non-trivial, exact-anchor Smali insertion in a native-bearing production APK;
- rebuild, sign, verify, store, and download that result quickly once an accepted patch exists; and
- refuse an ungrounded Mono edit instead of guessing.

It cannot yet reliably claim:

- that easy label/resource edits always complete automatically;
- that hybrid HTML plus Android-resource edits survive the plan-to-patch boundary;
- that user uploads are reliable on unstable links with 8 MiB ranges;
- that a natural-language Mono request reaches exact method-level IL evidence in two rounds;
- arbitrary native ELF modification based on this test—the successful native-bearing case changed
  Smali, not `.so` bytes; or
- runtime correctness on a phone without installing and exercising the generated APK.

## Recommended order of work

1. Bind patch operation types to exact paths in the Gemini response schema and rerun Markor/Acode.
2. Reduce upload ranges or move uploads to presigned S3 multipart; rerun the same 17 MB WireGuard
   upload from the phone and laptop.
3. Expose exact method-level CIL discovery and rerun the Escape Rouge request.
4. Add a safe native-specific evaluation using an open-source fixture with known exported symbols;
   do not infer native offsets from WireGuard.
5. Install the successfully signed WireGuard evaluation APK on a disposable test device/emulator
   and verify the Toast plus unchanged tunnel behavior before claiming runtime success.
