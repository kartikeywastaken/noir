# NOIR CIL Tool

Small deterministic companion used by the NOIR backend to inspect and rewrite
managed Unity/Mono assemblies with dnlib. It is not invoked directly by the AI.

Build:

```bash
dotnet build tools/noir-cil-tool --configuration Release
```

The backend automatically discovers
`tools/noir-cil-tool/bin/Release/net8.0/noir-cil-tool.dll`. A deployment may set
`NOIR_CIL_TOOL_PATH` to a different built DLL or executable.
