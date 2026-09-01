using System.Globalization;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using dnlib.DotNet;
using dnlib.DotNet.Emit;

namespace Noir.CilTool;

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = false,
    };

    private static readonly Dictionary<string, OpCode> OpCodesByName = typeof(OpCodes)
        .GetFields(BindingFlags.Public | BindingFlags.Static)
        .Where(field => field.FieldType == typeof(OpCode))
        .Select(field => (OpCode)field.GetValue(null)!)
        .ToDictionary(opcode => opcode.Name, StringComparer.OrdinalIgnoreCase);

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length < 2)
                throw new CilToolException("Usage: noir-cil-tool <inspect|read-il|patch-il|verify> <assembly> [...]");
            object result = args[0] switch
            {
                "inspect" when args.Length == 2 => Inspect(args[1]),
                "read-il" when args.Length == 4 => ReadIl(args[1], args[2], args[3]),
                "patch-il" when args.Length == 2 => PatchIl(args[1], ReadPayload()),
                "verify" when args.Length == 2 => Verify(args[1]),
                _ => throw new CilToolException("Invalid command or argument count"),
            };
            Console.Write(JsonSerializer.Serialize(result, JsonOptions));
            return 0;
        }
        catch (Exception exception)
        {
            Console.Error.Write(exception is CilToolException ? exception.Message : $"{exception.GetType().Name}: {exception.Message}");
            return 1;
        }
    }

    private static PatchRequest ReadPayload()
    {
        string input = Console.In.ReadToEnd();
        if (string.IsNullOrWhiteSpace(input))
            throw new CilToolException("patch-il requires a JSON object on stdin");
        return JsonSerializer.Deserialize<PatchRequest>(input, JsonOptions)
            ?? throw new CilToolException("Invalid patch JSON");
    }

    private static object Inspect(string path)
    {
        using ModuleDefMD module = Load(path);
        var types = module.GetTypes()
            .OrderBy(type => type.FullName, StringComparer.Ordinal)
            .Select(type => new
            {
                full_name = type.FullName,
                fields = type.Fields.OrderBy(field => field.Name.String, StringComparer.Ordinal).Select(field => new
                {
                    name = field.Name.String,
                    field_type = field.FieldType.FullName,
                    is_literal = field.IsLiteral,
                    constant = field.Constant?.Value,
                    constant_hash = field.IsLiteral
                        ? Hash(JsonSerializer.Serialize(field.Constant?.Value, JsonOptions))
                        : null,
                }).ToArray(),
                methods = type.Methods.OrderBy(MethodSignature, StringComparer.Ordinal).Select(method => new
                {
                    signature = MethodSignature(method),
                    token = method.MDToken.Raw,
                    has_body = method.HasBody,
                    il_size = method.Body?.Instructions.Sum(instruction => instruction.GetSize()) ?? 0,
                    il_hash = method.HasBody ? MethodHash(method) : null,
                }).ToArray(),
            }).ToArray();
        return new
        {
            ok = true,
            assembly_name = module.Assembly?.Name.String ?? module.Name.String,
            module_name = module.Name.String,
            mvid = module.Mvid.ToString(),
            types,
        };
    }

    private static object ReadIl(string path, string typeName, string signature)
    {
        using ModuleDefMD module = Load(path);
        MethodDef method = FindMethod(module, typeName, signature, mustExist: true)!;
        if (!method.HasBody)
            throw new CilToolException("Selected method has no CIL body");
        string source = CanonicalIl(method);
        return new
        {
            ok = true,
            type_full_name = typeName,
            method_signature = MethodSignature(method),
            method_token = method.MDToken.Raw,
            il_hash = Hash(source),
            il_source = source,
        };
    }

    private static object PatchIl(string path, PatchRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.OutputPath))
            throw new CilToolException("output_path is required");
        using ModuleDefMD module = Load(path);
        string operation = request.Operation ?? throw new CilToolException("operation is required");
        string typeName = request.TypeFullName ?? throw new CilToolException("type_full_name is required");
        TypeDef type = FindType(module, typeName);
        string? beforeHash = null;
        string affected;

        switch (operation)
        {
            case "cil_replace_method_body":
            {
                string signature = Required(request.MethodSignature, "method_signature");
                MethodDef method = FindMethod(type, signature, mustExist: true)!;
                beforeHash = MethodHash(method);
                RequireHash(request.ExpectedMethodIlHash, beforeHash);
                ReplaceBody(module, method, Required(request.NewIlSource, "new_il_source"));
                affected = $"{type.FullName}::{MethodSignature(method)}";
                break;
            }
            case "cil_insert_method":
            {
                string signature = Required(request.MethodSignature, "method_signature");
                if (FindMethod(type, signature, mustExist: false) is not null)
                    throw new CilToolException("Method already exists");
                MethodDef method = CreateStaticMethod(module, type, signature);
                ReplaceBody(module, method, Required(request.NewIlSource, "new_il_source"));
                type.Methods.Add(method);
                affected = $"{type.FullName}::{MethodSignature(method)}";
                break;
            }
            case "cil_replace_field_init":
            {
                string fieldName = Required(request.FieldName, "field_name");
                FieldDef[] fields = type.Fields.Where(field => field.Name == fieldName).ToArray();
                if (fields.Length != 1)
                    throw new CilToolException($"Field selector matched {fields.Length} fields; exactly one is required");
                FieldDef field = fields[0];
                if (!field.IsLiteral)
                    throw new CilToolException("Only literal CIL fields have a deterministic metadata initializer");
                string canonical = JsonSerializer.Serialize(field.Constant?.Value, JsonOptions);
                beforeHash = Hash(canonical);
                RequireHash(request.ExpectedMethodIlHash, beforeHash);
                field.Constant = new ConstantUser(ParseConstant(field.FieldType, Required(request.NewIlSource, "new_il_source")));
                affected = $"{type.FullName}::{field.Name}";
                break;
            }
            default:
                throw new CilToolException($"Unsupported CIL operation: {operation}");
        }

        string output = Path.GetFullPath(request.OutputPath);
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        module.Write(output);
        Verify(output);
        return new
        {
            ok = true,
            operation,
            affected,
            before_hash = beforeHash,
            output_path = output,
            output_sha256 = FileHash(output),
        };
    }

    private static object Verify(string path)
    {
        using ModuleDefMD module = Load(path);
        int methods = 0;
        foreach (MethodDef method in module.GetTypes().SelectMany(type => type.Methods))
        {
            if (!method.HasBody)
                continue;
            methods++;
            HashSet<Instruction> instructions = method.Body.Instructions.ToHashSet();
            foreach (Instruction instruction in method.Body.Instructions)
            {
                if (instruction.Operand is Instruction target && !instructions.Contains(target))
                    throw new CilToolException($"Method {method.FullName} has a branch outside its body");
                if (instruction.Operand is IList<Instruction> targets && targets.Any(target => !instructions.Contains(target)))
                    throw new CilToolException($"Method {method.FullName} has a switch target outside its body");
            }
        }
        return new
        {
            ok = true,
            assembly_name = module.Assembly?.Name.String ?? module.Name.String,
            mvid = module.Mvid.ToString(),
            types = module.GetTypes().Count(),
            methods,
            sha256 = FileHash(path),
        };
    }

    private static ModuleDefMD Load(string path)
    {
        if (!File.Exists(path))
            throw new CilToolException("Assembly was not found");
        try
        {
            return ModuleDefMD.Load(path, new ModuleCreationOptions { TryToLoadPdbFromDisk = false });
        }
        catch (BadImageFormatException exception)
        {
            throw new CilToolException($"File is not a valid managed PE/CIL assembly: {exception.Message}");
        }
    }

    private static TypeDef FindType(ModuleDef module, string typeName)
    {
        TypeDef[] matches = module.GetTypes().Where(type => type.FullName == typeName).ToArray();
        if (matches.Length != 1)
            throw new CilToolException($"Type selector matched {matches.Length} types; exactly one is required");
        return matches[0];
    }

    private static MethodDef? FindMethod(ModuleDef module, string typeName, string signature, bool mustExist) =>
        FindMethod(FindType(module, typeName), signature, mustExist);

    private static MethodDef? FindMethod(TypeDef type, string signature, bool mustExist)
    {
        MethodDef[] matches = type.Methods.Where(method => MethodSignature(method) == signature).ToArray();
        if (matches.Length == 1)
            return matches[0];
        if (matches.Length == 0 && !mustExist)
            return null;
        throw new CilToolException($"Method selector matched {matches.Length} methods; exactly one is required");
    }

    private static string MethodSignature(MethodDef method)
    {
        string parameters = string.Join(",", method.MethodSig.Params.Select(parameter => parameter.FullName));
        return $"{method.MethodSig.RetType.FullName} {method.Name}({parameters})";
    }

    private static string CanonicalIl(MethodDef method)
    {
        if (!method.HasBody)
            return string.Empty;
        Dictionary<Instruction, int> indices = method.Body.Instructions
            .Select((instruction, index) => (instruction, index))
            .ToDictionary(pair => pair.instruction, pair => pair.index);
        return string.Join("\n", method.Body.Instructions.Select(instruction =>
            $"{instruction.OpCode.Name}{FormatOperand(instruction.Operand, indices)}"));
    }

    private static string FormatOperand(object? operand, Dictionary<Instruction, int> indices)
    {
        if (operand is null)
            return string.Empty;
        if (operand is Instruction target)
            return $" IL_{indices[target]:D4}";
        if (operand is IList<Instruction> targets)
            return " " + string.Join(",", targets.Select(target => $"IL_{indices[target]:D4}"));
        if (operand is string text)
            return " " + JsonSerializer.Serialize(text);
        // Metadata row numbers are serialization details and dnlib may renumber
        // them when writing an otherwise semantically unchanged assembly. Hash
        // the stable member/type identity so post-patch scope validation does
        // not report every token-referencing method as modified.
        if (operand is IFullName fullName)
            return $" ref:{fullName.FullName}";
        if (operand is IMDTokenProvider token)
            return $" ref:{token.GetType().FullName}:{token}";
        if (operand is Local local)
            return $" local:{local.Index}";
        if (operand is Parameter parameter)
            return $" arg:{parameter.Index}";
        return " " + Convert.ToString(operand, CultureInfo.InvariantCulture);
    }

    private static string MethodHash(MethodDef method) => Hash(CanonicalIl(method));

    private static string Hash(string source) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(source))).ToLowerInvariant();

    private static string FileHash(string path) =>
        Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();

    private static void RequireHash(string? expected, string actual)
    {
        if (string.IsNullOrWhiteSpace(expected))
            throw new CilToolException("expected_method_il_hash is mandatory");
        if (!CryptographicOperations.FixedTimeEquals(Convert.FromHexString(expected), Convert.FromHexString(actual)))
            throw new CilToolException("Method/field CIL preimage hash mismatch");
    }

    private static void ReplaceBody(ModuleDef module, MethodDef method, string source)
    {
        CilBody body = new() { InitLocals = method.Body?.InitLocals ?? false };
        if (method.Body is not null)
            foreach (Local local in method.Body.Variables)
                body.Variables.Add(new Local(local.Type, local.Name));
        Dictionary<string, Instruction> labels = new(StringComparer.OrdinalIgnoreCase);
        List<(Instruction instruction, string label)> branches = new();
        foreach (string raw in source.Replace("\r", string.Empty).Split('\n'))
        {
            string line = raw.Split("//", 2)[0].Trim();
            if (line.Length == 0)
                continue;
            if (line.EndsWith(':'))
            {
                string label = line[..^1];
                if (!labels.TryAdd(label, Instruction.Create(OpCodes.Nop)))
                    throw new CilToolException($"Duplicate CIL label: {label}");
                body.Instructions.Add(labels[label]);
                continue;
            }
            string[] pieces = line.Split((char[]?)null, 2, StringSplitOptions.RemoveEmptyEntries);
            if (!OpCodesByName.TryGetValue(pieces[0], out OpCode? opcode) || opcode is null)
                throw new CilToolException($"Unsupported CIL opcode: {pieces[0]}");
            string? operand = pieces.Length == 2 ? pieces[1].Trim() : null;
            Instruction instruction = CreateInstruction(module, body, opcode, operand, branches);
            body.Instructions.Add(instruction);
        }
        foreach ((Instruction instruction, string label) in branches)
        {
            if (!labels.TryGetValue(label, out Instruction? target))
                throw new CilToolException($"Unknown CIL branch label: {label}");
            instruction.Operand = target;
        }
        if (body.Instructions.Count == 0)
            throw new CilToolException("CIL method body cannot be empty");
        method.Body = body;
    }

    private static Instruction CreateInstruction(
        ModuleDef module,
        CilBody body,
        OpCode opcode,
        string? operand,
        List<(Instruction instruction, string label)> branches)
    {
        switch (opcode.OperandType)
        {
            case OperandType.InlineNone:
                if (operand is not null)
                    throw new CilToolException($"Opcode {opcode.Name} takes no operand");
                return Instruction.Create(opcode);
            case OperandType.ShortInlineI:
                return Instruction.Create(opcode, sbyte.Parse(Required(operand, "operand"), CultureInfo.InvariantCulture));
            case OperandType.InlineI:
                return Instruction.Create(opcode, int.Parse(Required(operand, "operand"), CultureInfo.InvariantCulture));
            case OperandType.InlineI8:
                return Instruction.Create(opcode, long.Parse(Required(operand, "operand"), CultureInfo.InvariantCulture));
            case OperandType.ShortInlineR:
                return Instruction.Create(opcode, float.Parse(Required(operand, "operand"), CultureInfo.InvariantCulture));
            case OperandType.InlineR:
                return Instruction.Create(opcode, double.Parse(Required(operand, "operand"), CultureInfo.InvariantCulture));
            case OperandType.InlineString:
                return Instruction.Create(opcode, JsonSerializer.Deserialize<string>(Required(operand, "operand"))
                    ?? throw new CilToolException("String operand cannot be null"));
            case OperandType.ShortInlineBrTarget:
            case OperandType.InlineBrTarget:
            {
                Instruction instruction = Instruction.Create(opcode, Instruction.Create(OpCodes.Nop));
                branches.Add((instruction, Required(operand, "branch label")));
                return instruction;
            }
            case OperandType.InlineType:
            case OperandType.InlineMethod:
            case OperandType.InlineField:
            case OperandType.InlineTok:
            {
                string tokenText = Required(operand, "metadata token").Replace("token:", string.Empty, StringComparison.OrdinalIgnoreCase);
                uint token = uint.Parse(tokenText.Replace("0x", string.Empty, StringComparison.OrdinalIgnoreCase), NumberStyles.HexNumber, CultureInfo.InvariantCulture);
                IMDTokenProvider resolved = module.ResolveToken(token)
                    ?? throw new CilToolException($"Metadata token 0x{token:x8} does not resolve");
                return opcode.OperandType switch
                {
                    OperandType.InlineType when resolved is ITypeDefOrRef type =>
                        Instruction.Create(opcode, type),
                    OperandType.InlineMethod when resolved is IMethod method =>
                        Instruction.Create(opcode, method),
                    OperandType.InlineField when resolved is IField field =>
                        Instruction.Create(opcode, field),
                    OperandType.InlineTok when resolved is ITokenOperand tokenOperand =>
                        Instruction.Create(opcode, tokenOperand),
                    _ => throw new CilToolException(
                        $"Metadata token 0x{token:x8} is incompatible with {opcode.Name}"),
                };
            }
            case OperandType.ShortInlineVar:
            case OperandType.InlineVar:
            {
                string value = Required(operand, "variable operand");
                if (value.StartsWith("local:", StringComparison.OrdinalIgnoreCase))
                {
                    int index = int.Parse(value[6..], CultureInfo.InvariantCulture);
                    if (index < 0 || index >= body.Variables.Count)
                        throw new CilToolException("Local variable index is outside the preserved local table");
                    return Instruction.Create(opcode, body.Variables[index]);
                }
                throw new CilToolException("Variable operands must use local:<index>; prefer ldarg.0-style opcodes for arguments");
            }
            default:
                throw new CilToolException($"Operand type {opcode.OperandType} is not supported by the bounded CIL assembler");
        }
    }

    private static MethodDef CreateStaticMethod(ModuleDef module, TypeDef type, string signature)
    {
        Match match = System.Text.RegularExpressions.Regex.Match(signature, @"^(?<ret>\S+)\s+(?<name>[^\s(]+)\((?<args>[^)]*)\)$");
        if (!match.Success)
            throw new CilToolException("New method signature must be '<return-type> <name>(<parameter-types>)'");
        TypeSig returnType = ResolveType(module, match.Groups["ret"].Value);
        TypeSig[] parameters = match.Groups["args"].Value.Length == 0
            ? Array.Empty<TypeSig>()
            : match.Groups["args"].Value.Split(',').Select(value => ResolveType(module, value.Trim())).ToArray();
        return new MethodDefUser(
            match.Groups["name"].Value,
            MethodSig.CreateStatic(returnType, parameters),
            dnlib.DotNet.MethodImplAttributes.IL | dnlib.DotNet.MethodImplAttributes.Managed,
            dnlib.DotNet.MethodAttributes.Public | dnlib.DotNet.MethodAttributes.Static |
                dnlib.DotNet.MethodAttributes.HideBySig);
    }

    private static TypeSig ResolveType(ModuleDef module, string name) => name switch
    {
        "System.Void" => module.CorLibTypes.Void,
        "System.Boolean" => module.CorLibTypes.Boolean,
        "System.Byte" => module.CorLibTypes.Byte,
        "System.SByte" => module.CorLibTypes.SByte,
        "System.Int16" => module.CorLibTypes.Int16,
        "System.UInt16" => module.CorLibTypes.UInt16,
        "System.Int32" => module.CorLibTypes.Int32,
        "System.UInt32" => module.CorLibTypes.UInt32,
        "System.Int64" => module.CorLibTypes.Int64,
        "System.UInt64" => module.CorLibTypes.UInt64,
        "System.Single" => module.CorLibTypes.Single,
        "System.Double" => module.CorLibTypes.Double,
        "System.String" => module.CorLibTypes.String,
        "System.Object" => module.CorLibTypes.Object,
        _ => module.GetTypes().SingleOrDefault(type => type.FullName == name)?.ToTypeSig()
            ?? throw new CilToolException($"Unsupported or unresolved CIL type: {name}"),
    };

    private static object? ParseConstant(TypeSig type, string json)
    {
        using JsonDocument document = JsonDocument.Parse(json);
        JsonElement value = document.RootElement;
        return type.ElementType switch
        {
            ElementType.Boolean => value.GetBoolean(),
            ElementType.Char => (char)value.GetInt32(),
            ElementType.I1 => value.GetSByte(),
            ElementType.U1 => value.GetByte(),
            ElementType.I2 => value.GetInt16(),
            ElementType.U2 => value.GetUInt16(),
            ElementType.I4 => value.GetInt32(),
            ElementType.U4 => value.GetUInt32(),
            ElementType.I8 => value.GetInt64(),
            ElementType.U8 => value.GetUInt64(),
            ElementType.R4 => value.GetSingle(),
            ElementType.R8 => value.GetDouble(),
            ElementType.String => value.ValueKind == JsonValueKind.Null ? null : value.GetString(),
            _ => throw new CilToolException($"Literal field type {type.FullName} is unsupported"),
        };
    }

    private static string Required(string? value, string name) =>
        !string.IsNullOrWhiteSpace(value) ? value : throw new CilToolException($"{name} is required");

    private sealed class PatchRequest
    {
        public string? OutputPath { get; set; }
        public string? Operation { get; set; }
        public string? TypeFullName { get; set; }
        public string? MethodSignature { get; set; }
        public string? NewIlSource { get; set; }
        public string? ExpectedMethodIlHash { get; set; }
        public string? FieldName { get; set; }
    }

    private sealed class CilToolException(string message) : Exception(message);
}
