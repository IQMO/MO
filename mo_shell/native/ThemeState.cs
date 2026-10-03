using System.Text.Json;

namespace MoShell.Native;

internal sealed record ThemeFont(string Family, float Size);

internal sealed class ThemeState
{
    public static readonly ThemeState Empty = new();

    public Color Background { get; private init; } = Color.Empty;
    public Color Surface { get; private init; } = Color.Empty;
    public Color Input { get; private init; } = Color.Empty;
    public Color Brand { get; private init; } = Color.Empty;
    public Color Glow { get; private init; } = Color.Empty;
    public Color Text { get; private init; } = Color.Empty;
    public Color Muted { get; private init; } = Color.Empty;
    public Color Border { get; private init; } = Color.Empty;
    public Color Error { get; private init; } = Color.Empty;
    public int PanelPadding { get; private init; }
    public int PanelCornerRadius { get; private init; }
    public int ButtonPadding { get; private init; }
    public int ButtonCornerRadius { get; private init; }
    public int ChromeButton { get; private init; }
    public int ChromeIcon { get; private init; }
    public int ChromeGap { get; private init; }
    public int ChromeHoverMix { get; private init; }
    public int ChromeCloseMix { get; private init; }
    public int ChromeHoverMs { get; private init; }
    public ThemeFont BodyFont { get; private init; } = new("", 0);
    public ThemeFont TitleFont { get; private init; } = new("", 0);
    public ThemeFont MonoFont { get; private init; } = new("", 0);
    public Color CubeColor { get; private init; } = Color.Empty;
    public int CubeSize { get; private init; }
    public float CubeEdgeRatio { get; private init; }
    public float CubeGlow { get; private init; }
    public float CubeCornerRadius { get; private init; }

    public static bool TryFromPayload(JsonElement payload, out ThemeState theme)
    {
        theme = Empty;
        if (!payload.TryGetProperty("tokens", out var tokens) ||
            !payload.TryGetProperty("panel", out var panel) ||
            !payload.TryGetProperty("chrome", out var chrome) ||
            !payload.TryGetProperty("typography", out var typography) ||
            !payload.TryGetProperty("character", out var character))
        {
            return false;
        }

        var brand = ParseColor(tokens, "brand");
        var colorMode = ReadString(character, "color_mode");
        var candidate = new ThemeState
        {
            Background = ParseColor(tokens, "background"),
            Surface = ParseColor(tokens, "surface"),
            Input = ParseColor(tokens, "input"),
            Brand = brand,
            Glow = ParseColor(tokens, "glow"),
            Text = ParseColor(tokens, "text"),
            Muted = ParseColor(tokens, "muted"),
            Border = ParseColor(tokens, "border"),
            Error = ParseColor(tokens, "error"),
            PanelPadding = ReadInteger(panel, "padding"),
            PanelCornerRadius = ReadInteger(panel, "corner_radius"),
            ButtonPadding = ReadInteger(panel, "button_padding"),
            ButtonCornerRadius = ReadInteger(panel, "button_corner_radius"),
            ChromeButton = ReadInteger(chrome, "button"),
            ChromeIcon = ReadInteger(chrome, "icon"),
            ChromeGap = ReadInteger(chrome, "gap"),
            ChromeHoverMix = ReadInteger(chrome, "hover_mix"),
            ChromeCloseMix = ReadInteger(chrome, "close_mix"),
            ChromeHoverMs = ReadInteger(chrome, "hover_ms"),
            BodyFont = ParseFont(typography, "body"),
            TitleFont = ParseFont(typography, "title"),
            MonoFont = ParseFont(typography, "mono"),
            CubeColor = colorMode == "skin" ? brand : ParseColorValue(colorMode),
            CubeSize = ReadInteger(character, "size"),
            CubeEdgeRatio = ReadFloat(character, "edge_ratio"),
            CubeGlow = ReadFloat(character, "glow"),
            CubeCornerRadius = ReadFloat(character, "corner_radius"),
        };
        if (candidate.RequiredColors().Any(color => color.IsEmpty) ||
            candidate.PanelPadding <= 0 || candidate.PanelCornerRadius < 0 ||
            candidate.ButtonPadding <= 0 || candidate.ButtonCornerRadius < 0 ||
            candidate.ChromeButton <= 0 || candidate.ChromeIcon <= 0 ||
            candidate.ChromeIcon > candidate.ChromeButton || candidate.ChromeGap < 0 ||
            candidate.ChromeHoverMs <= 0 ||
            candidate.ChromeHoverMix is < 0 or > 100 || candidate.ChromeCloseMix is < 0 or > 100 ||
            !ValidFont(candidate.BodyFont) ||
            !ValidFont(candidate.TitleFont) ||
            !ValidFont(candidate.MonoFont) || candidate.CubeColor.IsEmpty ||
            candidate.CubeSize <= 0 || candidate.CubeEdgeRatio is <= 0 or > 0.5f ||
            candidate.CubeGlow is < 0 or > 1 ||
            candidate.CubeCornerRadius is < 0 or > 0.5f)
        {
            return false;
        }
        theme = candidate;
        return true;
    }

    private IEnumerable<Color> RequiredColors()
    {
        yield return Background;
        yield return Surface;
        yield return Input;
        yield return Brand;
        yield return Glow;
        yield return Text;
        yield return Muted;
        yield return Border;
        yield return Error;
    }

    private static bool ValidFont(ThemeFont font) =>
        font.Family.Length > 0 && font.Size is >= 6 and <= 72;

    private static ThemeFont ParseFont(JsonElement typography, string name)
    {
        if (!typography.TryGetProperty(name, out var value)) return new("", 0);
        return new ThemeFont(ReadString(value, "family"), ReadFloat(value, "size"));
    }

    private static Color ParseColor(JsonElement values, string name) =>
        values.TryGetProperty(name, out var value)
            ? ParseColorValue(value.GetString())
            : Color.Empty;

    private static Color ParseColorValue(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return Color.Empty;
        try { return ColorTranslator.FromHtml(value); }
        catch (ArgumentException) { return Color.Empty; }
    }

    private static string ReadString(JsonElement values, string name) =>
        values.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()?.Trim() ?? ""
            : "";

    private static int ReadInteger(JsonElement values, string name) =>
        values.TryGetProperty(name, out var value) && value.TryGetInt32(out var number)
            ? number
            : -1;

    private static float ReadFloat(JsonElement values, string name) =>
        values.TryGetProperty(name, out var value) && value.TryGetSingle(out var number)
            ? number
            : -1;
}
