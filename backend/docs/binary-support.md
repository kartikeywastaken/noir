# Binary code support and boundaries

NOIR can modify three narrowly scoped binary-code families. They use the same plan,
human review, approval, deterministic patch, validation, rebuild, signing, and audit
pipeline as Smali and XML changes. The model only proposes structured operations; it
never improvises binary bytes while a patch is being applied.

## Supported

### Mono/.NET assemblies

Files directly under `assets/bin/Data/Managed/*.dll` can be inspected and patched by
the bundled dnlib companion tool. NOIR supports:

- replacing one existing CIL method body without changing its signature;
- inserting one static method into an existing type; and
- changing the metadata constant of an existing literal field.

Every operation binds both the complete assembly SHA-256 and the selected method-body
or literal-field hash. The patched PE/CIL file is reopened and verified before commit.
Post-patch validation compares all method and literal-field hashes against the journal
backup and rejects changes outside the approved selectors. The default assembly ceiling
is 15 MiB.

Build the companion with:

```sh
dotnet build tools/noir-cil-tool/noir-cil-tool.csproj --configuration Release
```

Set `NOIR_CIL_TOOL_PATH` only when the built DLL is not at the default repository path.

### IL2CPP applications

IL2CPP does not contain editable CIL. NOIR reads a supported `global-metadata.dat`,
confirms the requested type and method names, and correlates them to one unambiguous,
sized export in each `lib/<abi>/libil2cpp.so`. It supports only:

- a constant 32-bit integer/boolean return trampoline at the function entry; and
- NOP replacement of an explicitly bounded, complete instruction range within the
  resolved function.

Metadata versions 24, 27, 29, and 31 are recognized. This first implementation refuses
stripped or ambiguous binaries instead of guessing Unity code-registration offsets.
Every ABI containing `libil2cpp.so` must have its own operation or be named explicitly
with a reviewable skip reason.

### Native ELF libraries

Files shaped as `lib/<abi>/*.so` support:

- exact, same-length byte replacement;
- architecture-correct, same-length NOP replacement; and
- redirecting one existing branch to an exported function entry when the replacement
  instruction has exactly the original length.

Patch ranges must lie wholly inside a file-backed executable segment, be at most 4 KiB,
match both the complete-file and range preimage hashes, and disassemble completely before
and after modification. Supported ABI declarations are `arm64-v8a`, `armeabi-v7a`,
`x86_64`, and `x86`. All packaged ABIs for the same library must be addressed or have an
explicit skip reason. The default native-library ceiling is 128 MiB.

The JSON audit report records the target, ABI, complete-file pre/post hashes, range
pre/post hashes, and instruction-level before/after disassembly for IL2CPP and native
operations.

## Still unsupported

- arbitrary binary assets such as images, audio, fonts, archives, databases, and models;
- manual or generic whole-file binary replacement;
- text-file operations over the existing 1 MiB ceiling;
- split APK sets and Android App Bundles;
- native insertion/deletion, relocation rewriting, new code caves, symbol creation,
  arbitrary hooks, or general-purpose native code rewriting;
- full semantic IL2CPP rewriting, inlined IL2CPP methods, stripped/ambiguous method
  correlation, unsupported metadata versions, or functions too short for a bounded
  trampoline;
- CIL type deletion, assembly-reference changes, signature changes, arbitrary metadata
  edits, or assemblies that dnlib cannot parse (including some protected/obfuscated
  assemblies); and
- cross-ABI translation. An operation valid for one ABI is never copied to another ABI.

If a requested change cannot be represented by one of the listed operations, NOIR
returns a specific refusal. It does not fall back to searching for convenient byte
sequences or applying a best-effort mutation.
