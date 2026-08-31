# Synthetic binary fixtures

- `mono/Assembly-CSharp.dll` is built from `mono/Fixture.cs` and contains two small
  managed methods plus one literal field.
- `native/libfixture.so` and `native/libfixture-arm64.so` are x86-64 and arm64 Linux
  ELFs built from the short assembly sources next to them. Their exported functions
  provide deterministic patch ranges and real multi-ABI coverage.
- `il2cpp/global-metadata.dat` is a minimal version-29 header and string table whose
  names correlate with the synthetic ELF export. It is not copied from a game.

Rebuild them with `NOIR_FIXTURE_DOTNET=/path/to/dotnet python
backend/tests/fixtures/build_binary_fixtures.py` from the repository root. The script
also accepts `NOIR_FIXTURE_ZIG` when Zig is not on `PATH`.
