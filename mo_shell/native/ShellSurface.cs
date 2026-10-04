using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;

namespace MoShell.Native;

internal sealed record TerminalFragment(
    string Text,
    int Columns,
    Color? Foreground,
    bool Bold,
    bool Dim,
    bool Italic,
    bool Underline,
    bool Strike,
    bool Emoji);

internal readonly record struct TerminalPoint(int Row, int Column);

internal sealed partial class ShellSurface : Control
{
    private const double WorkingStepsPerSecond = 2.4;
    private const float IdentityFadeStep = 0.34f;
    private const double DropReactionSeconds = 0.7;
    private const double DragLeaveGraceSeconds = 0.20;
    private const TextFormatFlags TerminalTextFlags =
        TextFormatFlags.NoPadding |
        TextFormatFlags.NoPrefix |
        TextFormatFlags.SingleLine |
        TextFormatFlags.PreserveGraphicsClipping;
    private readonly Dictionary<FontStyle, Font> _terminalFonts = new();
    private readonly Dictionary<FontStyle, Font> _emojiFonts = new();
    private readonly ToolTip _toolTip = new()
    {
        InitialDelay = 450,
        ReshowDelay = 100,
        AutoPopDelay = 5000,
        ShowAlways = true,
    };
    // Display-synchronous: a WM_TIMER request rounds to 15.6 ms quanta and beats against the refresh grid.
    private readonly DisplayTimer _identityAnimation = new() { Interval = 31 };
    private long _identityFrameAt;
    private bool _visualMotion;
    public event Action? VisualFrame;

    public void SetVisualMotion(bool active)
    {
        _visualMotion = active;
        UpdateIdentityAnimation();
    }
    private TerminalFragment[][] _screen = Array.Empty<TerminalFragment[]>();
    private ThemeState _theme = ThemeState.Empty;
    private int _screenRows = 1;
    private Font _bodyFont;
    private Font _titleFont;
    private Font _identityFont;
    private float _terminalScale = 1f;
    private int _cursorX;
    private int _cursorY;
    private bool _cursorVisible;
    private float _split = 0.5f;
    private bool _collapsed = true;
    private bool _maximized;
    private bool _draggingCube;
    private bool _draggingTitleBar;
    private bool _windowDragStarted;
    private bool _resizing;
    private Point _cursorDown;
    private Point _formDown;
    private bool _attachmentActive;
    private Size _attachmentMinimumContentSize;
    private Icon? _attachmentIcon;
    private AttachmentMode _selectedMode = AttachmentMode.Managed;
    private string _status = "";
    private bool _statusError;
    private bool _pickerOpen;
    private IReadOnlyList<WindowCandidate> _candidates = Array.Empty<WindowCandidate>();
    private int _pickerOffset;
    private int _hoverEntry = -1;
    private ControlAction? _hoverAction;
    private readonly Dictionary<ControlAction, float> _controlHover = new();
    private long _controlHoverTick;
    private ControlAction? _keyboardAction;
    private string _attachmentTitle = "";
    private bool _working;
    private bool _identityHovered;
    private float _identityLabelOpacity;
    private long _workingStarted;
    private TerminalPoint? _selectionAnchor;
    private TerminalPoint? _selectionEnd;
    private bool _selecting;
    private bool _applicationDrag;
    private float _dragMouth;
    private float _dragMouthTarget;
    private bool _applicationDragArmed;
    private long _applicationDragLeaveStarted;
    private long _dropReactionStarted;

    public event Action<string>? InputRequested;
    public event Action<int, int>? ResizeRequested;
    public event Action? ToggleCollapsedRequested;
    public event Action? ToggleMaximizedRequested;
    public event Action? CloseRequested;
    public event Action<WindowCandidate, AttachmentMode>? AttachRequested;
    public event Action? DetachRequested;
    public event Action? AttachmentGeometryChanged;
    public event Action<bool>? PickerVisibilityChanged;
    public event Action? CompactPresentationChanged;
    public Func<IReadOnlyList<WindowCandidate>>? WindowCandidatesRequested { get; set; }

    public AttachmentMode SelectedMode => _selectedMode;
    public double ApplicationDropReactionSeconds => DropReactionSeconds;
    public Rectangle AttachmentContentBounds => InsetAttachment(CalculateLayout().Attachment);
    public Rectangle BodyBounds => CalculateLayout().Body;
    public int BodyCornerRadius => PanelCornerRadius;
    public Rectangle IdentityBounds
    {
        get
        {
            if (HasCompactGroup) return GroupBounds;
            var bounds = CubeBounds();
            return Rectangle.Inflate(bounds, Scale(4), Scale(4));
        }
    }
    public int IdentityCornerRadius => HasCompactGroup ? PanelCornerRadius : IdentityBounds.Height / 2;
    public Size CompactSize => HasCompactGroup ? GroupCompactSize : SingleCompactSize;
    private Size SingleCompactSize => new(
        Math.Max(Scale(72), CubePairWidth + Scale(8)),
        Math.Max(Scale(72), TopInset + CubeEdge + Scale(4)));
    public int MinimumExpandedWidth
    {
        get
        {
            var inset = Math.Max(Scale(1), PanelPadding / 2);
            return inset * 2 + Math.Max(
                MinimumPaneWidth + DividerWidth + AttachmentMinimumPaneWidth,
                CubePairWidth + ControlStripWidth + ControlGroupGap * 2 +
                ControlBarPadding * 2 + ResizeGrip * 2);
        }
    }

    public int MinimumExpandedHeight =>
        Math.Max(Scale(1), PanelPadding / 2) * 2 +
        _attachmentMinimumContentSize.Height + TitleBarHeight + ResizeGrip * 2;

    public int CompactWidth(int minimum) =>
        !_collapsed || (!_identityHovered && _identityLabelOpacity <= 0)
            ? minimum
            : Math.Max(minimum, IdentityLabelBounds().Right + IdentityMargin);

    private int ResizeHitTest(Point point)
    {
        const int htClient = 1;
        if (_collapsed || _maximized) return htClient;
        var body = CalculateLayout().Body;
        var grip = ResizeGrip;
        var left = point.X >= body.Left && point.X < body.Left + grip;
        var right = point.X < body.Right && point.X >= body.Right - grip;
        var top = point.Y >= body.Top && point.Y < body.Top + grip;
        var bottom = point.Y < body.Bottom && point.Y >= body.Bottom - grip;
        return top && left ? 13 :
            top && right ? 14 :
            bottom && left ? 16 :
            bottom && right ? 17 :
            left ? 10 :
            right ? 11 :
            top ? 12 :
            bottom ? 15 :
            htClient;
    }

    public ShellSurface()
    {
        Dock = DockStyle.Fill;
        TabStop = true;
        DoubleBuffered = true;
        SetStyle(
            ControlStyles.UserPaint |
            ControlStyles.AllPaintingInWmPaint |
            ControlStyles.OptimizedDoubleBuffer |
            ControlStyles.ResizeRedraw,
            true);
        var systemFont = DefaultFont;
        _bodyFont = new Font(systemFont.FontFamily, systemFont.Size, FontStyle.Regular);
        _titleFont = new Font(systemFont.FontFamily, systemFont.Size, FontStyle.Bold);
        _identityFont = new Font(systemFont.FontFamily, systemFont.Size, FontStyle.Regular);
        RebuildTerminalFonts(new ThemeFont(FontFamily.GenericMonospace.Name, systemFont.Size));
        MouseDown += OnMouseDown;
        MouseMove += OnMouseMove;
        MouseUp += OnMouseUp;
        MouseCaptureChanged += (_, _) => GroupCaptureChanged();
        MouseLeave += (_, _) =>
        {
            Cursor = Cursors.Default;
            UpdateHover(Point.Empty, outside: true);
        };
        MouseWheel += OnMouseWheel;
        KeyPress += OnKeyPress;
        KeyDown += OnKeyDown;
        GotFocus += (_, _) => { Invalidate(); NotifyKeyboardFocus(); };
        LostFocus += (_, _) => Invalidate();
        _identityAnimation.Tick += (_, _) => TickIdentityAnimation();
    }

    public void SetScreen(
        TerminalFragment[][] rows,
        int screenRows,
        int cursorX,
        int cursorY,
        bool cursorVisible,
        bool busy)
    {
        _screen = rows;
        _screenRows = Math.Max(1, screenRows);
        _cursorX = Math.Max(0, cursorX);
        _cursorY = Math.Max(0, cursorY);
        _cursorVisible = cursorVisible;
        SetWorking(busy);
        if (!_collapsed) Invalidate(CalculateLayout().Terminal);
    }

    private void SetWorking(bool working)
    {
        if (working == _working) return;
        _working = working;
        _workingStarted = working ? Stopwatch.GetTimestamp() : 0;
        if (working)
        {
            _identityAnimation.Start();
        }
        else UpdateIdentityAnimation();
        Invalidate(CubeBounds());
    }

    private void TickIdentityAnimation()
    {
        var frameAt = Stopwatch.GetTimestamp();
        var elapsed = Math.Clamp((frameAt - _identityFrameAt) / (double)Stopwatch.Frequency, 0, .25);
        _identityFrameAt = frameAt;
        if (ControlsNeedFrames)
        {
            var now = Stopwatch.GetTimestamp();
            var step = (float)((now - _controlHoverTick) * 1000d / Stopwatch.Frequency / _theme.ChromeHoverMs);
            _controlHoverTick = now;
            foreach (var action in _controlHover.Keys.ToArray())
                _controlHover[action] = action == _hoverAction
                    ? Math.Min(1, _controlHover[action] + step)
                    : Math.Max(0, _controlHover[action] - step);
            Invalidate(CalculateControls().Bar);
        }
        if (_visualMotion) VisualFrame?.Invoke();
        TickGroupAnimation();
        var target = _collapsed && _identityHovered ? 1f : 0f;
        var previous = _identityLabelOpacity;
        var identityStep = (float)(IdentityFadeStep * elapsed / .033);
        _identityLabelOpacity = target > previous
            ? Math.Min(target, previous + identityStep)
            : Math.Max(target, previous - identityStep);
        var identityChanged = Math.Abs(previous - _identityLabelOpacity) > 0.001f;
        var previousMouth = _dragMouth;
        var mouthEase = 1f - (float)Math.Pow(
            1f - 0.28f,
            elapsed * 30);
        _dragMouth = Math.Abs(_dragMouthTarget - _dragMouth) < 0.002f
            ? _dragMouthTarget
            : _dragMouth + (_dragMouthTarget - _dragMouth) * mouthEase;
        if (_applicationDragLeaveStarted != 0 &&
            (Stopwatch.GetTimestamp() - _applicationDragLeaveStarted) /
                (double)Stopwatch.Frequency >= DragLeaveGraceSeconds)
        {
            _applicationDrag = false;
            _dragMouthTarget = 0;
            _applicationDragLeaveStarted = 0;
        }
        if (_dropReactionStarted != 0 && DropElapsed() >= DropReactionSeconds)
        {
            _dropReactionStarted = 0;
        }
        var cubeMotion = _applicationDrag || _dragMouth > 0.002f || _dropReactionStarted != 0;
        if (identityChanged || cubeMotion)
        {
            Invalidate();
        }
        else if (_working) Invalidate(CubeBounds());
        if (_collapsed &&
            (identityChanged || cubeMotion || Math.Abs(previousMouth - _dragMouth) > 0.001f))
        {
            CompactPresentationChanged?.Invoke();
        }
        UpdateIdentityAnimation();
    }

    private void UpdateIdentityAnimation()
    {
        var target = _collapsed && _identityHovered ? 1f : 0f;
        var interactive = _visualMotion || GroupNeedsFrames || ControlsNeedFrames || _applicationDrag || _applicationDragLeaveStarted != 0 ||
            _dragMouth > 0.002f ||
            _dropReactionStarted != 0 ||
            Math.Abs(target - _identityLabelOpacity) > 0.001f;
        if (interactive || _working || HasCompactGroup && GroupWorking)
        {
            _identityAnimation.Interval = interactive ? 15 : 31;
            if (!_identityAnimation.Enabled) _identityFrameAt = Stopwatch.GetTimestamp();
            _identityAnimation.Start();
        }
        else
        {
            _identityAnimation.Stop();
        }
    }

    private bool ControlsNeedFrames => _controlHover.Any(pair => pair.Value != (pair.Key == _hoverAction ? 1f : 0f));

    private double WorkingElapsed() => _workingStarted == 0
        ? 0
        : (Stopwatch.GetTimestamp() - _workingStarted) / (double)Stopwatch.Frequency;

    private double DropElapsed() => _dropReactionStarted == 0
        ? DropReactionSeconds
        : (Stopwatch.GetTimestamp() - _dropReactionStarted) / (double)Stopwatch.Frequency;

    public float ApplicationDragNearness(Point screenPoint)
    {
        var point = PointToClient(screenPoint);
        var area = CubeBounds();
        var dx = point.X - (area.Left + area.Width / 2f);
        var dy = point.Y - (area.Top + area.Height / 2f);
        var reach = Height / 2f;
        return reach <= 0 ? 0 : 1f - Math.Min(1f, MathF.Sqrt(dx * dx + dy * dy) / reach);
    }

    public void PollApplicationDragArm()
    {
        var area = CubeBounds();
        var point = PointToClient(Cursor.Position);
        var dx = point.X - (area.Left + area.Width / 2f);
        var dy = point.Y - (area.Top + area.Height / 2f);
        var reach = Height / 2f;
        var releaseReach = _applicationDragArmed ? reach * 1.35f : reach;
        var armed = _collapsed &&
            (Control.MouseButtons & MouseButtons.Left) != 0 &&
            MathF.Sqrt(dx * dx + dy * dy) <= releaseReach;
        if (armed == _applicationDragArmed) return;
        _applicationDragArmed = armed;
        CompactPresentationChanged?.Invoke();
    }

    public void SetApplicationDrag(bool active, float nearness = 0)
    {
        _applicationDrag = active && _collapsed;
        _dragMouthTarget = _applicationDrag ? Math.Clamp(nearness, 0, 1) : 0;
        _applicationDragLeaveStarted = 0;
        UpdateIdentityAnimation();
    }

    public void BeginApplicationDragLeave()
    {
        if (!_applicationDrag)
        {
            SetApplicationDrag(false);
            return;
        }
        _applicationDragLeaveStarted = Stopwatch.GetTimestamp();
        UpdateIdentityAnimation();
    }

    public void PlayApplicationDrop()
    {
        _applicationDrag = false;
        _dragMouthTarget = 0;
        _applicationDragLeaveStarted = 0;
        _dropReactionStarted = Stopwatch.GetTimestamp();
        UpdateIdentityAnimation();
    }

    public void SetTheme(ThemeState theme)
    {
        _theme = theme;
        ReplaceFont(ref _bodyFont, CreateFont(theme.BodyFont, FontStyle.Regular));
        ReplaceFont(ref _titleFont, CreateFont(theme.TitleFont, FontStyle.Bold));
        ReplaceFont(ref _identityFont, CreateFont(theme.BodyFont, FontStyle.Regular));
        RebuildTerminalFonts(theme.MonoFont);
        Invalidate();
        NotifyLayoutChanged();
        if (_collapsed) CompactPresentationChanged?.Invoke();
    }

    public void SetCollapsed(bool collapsed)
    {
        _collapsed = collapsed;
        ClearTerminalSelection();
        _hoverAction = null;
        _controlHover.Clear();
        _keyboardAction = null;
        _identityHovered = false;
        _identityLabelOpacity = 0;
        if (!collapsed)
        {
            _applicationDrag = false;
            _dragMouth = 0;
            _dragMouthTarget = 0;
            _applicationDragArmed = false;
            _applicationDragLeaveStarted = 0;
            _dropReactionStarted = 0;
        }
        _toolTip.SetToolTip(this, "");
        UpdateIdentityAnimation();
        ClosePicker();
        Invalidate();
        CompactPresentationChanged?.Invoke();
        if (!collapsed) NotifyLayoutChanged();
    }

    public void SetMaximized(bool maximized)
    {
        if (_maximized == maximized) return;
        _maximized = maximized;
        _toolTip.SetToolTip(
            this,
            _hoverAction == ControlAction.Maximize
                ? ToolTipText(ControlAction.Maximize)
                : "");
        Invalidate(CalculateControls().Bar);
    }

    public void SetAttachment(
        bool attached,
        AttachmentMode mode,
        Icon? icon = null,
        string? title = null)
    {
        _attachmentIcon?.Dispose();
        _attachmentIcon = icon;
        _attachmentActive = attached;
        _attachmentMinimumContentSize = Size.Empty;
        _split = 0.5f;
        _attachmentTitle = attached ? title?.Trim() ?? "" : "";
        _selectedMode = mode;
        ClosePicker();
        Invalidate();
        if (_collapsed) CompactPresentationChanged?.Invoke();
        NotifyLayoutChanged();
    }

    public bool EnsureAttachmentContentSize(Size requested, Size actual)
    {
        if (!_attachmentActive) return false;
        var minimum = new Size(
            actual.Width > requested.Width
                ? Math.Max(_attachmentMinimumContentSize.Width, actual.Width)
                : _attachmentMinimumContentSize.Width,
            actual.Height > requested.Height
                ? Math.Max(_attachmentMinimumContentSize.Height, actual.Height)
                : _attachmentMinimumContentSize.Height);
        if (minimum == _attachmentMinimumContentSize) return false;
        _attachmentMinimumContentSize = minimum;
        Invalidate(CalculateLayout().Body);
        return true;
    }

    public void SetStatus(string message, bool isError = false)
    {
        _status = message.Trim();
        _statusError = isError;
        if (!_collapsed) Invalidate(CalculateLayout().Terminal);
    }

    public void ClearStatus(string message)
    {
        if (!string.Equals(_status, message.Trim(), StringComparison.Ordinal)) return;
        _status = "";
        _statusError = false;
        if (!_collapsed) Invalidate(CalculateLayout().Terminal);
    }

    public void NotifyLayoutChanged()
    {
        if (_collapsed) return;
        var terminal = CalculateLayout().Terminal;
        var cell = TerminalCellSize();
        ResizeRequested?.Invoke(
            Math.Max(20, (terminal.Width - PanelPadding) / Math.Max(1, cell.Width)),
            Math.Max(3, (terminal.Height - TerminalVerticalPadding * 2) / Math.Max(1, cell.Height)));
        AttachmentGeometryChanged?.Invoke();
    }

    public Region CreateWindowRegion()
    {
        var region = new Region();
        region.MakeEmpty();
        if (HasCompactGroup)
        {
            region.Union(GroupBounds);
            if (_identityLabelOpacity > 0)
            {
                region.Union(IdentityLabelBounds());
            }
            return region;
        }
        if (!_collapsed)
        {
            using var bodyPath = GraphicsExtensions.RoundedPath(CalculateLayout().Body, PanelCornerRadius);
            region.Union(bodyPath);
            return region;
        }
        foreach (var cube in CubeRectangles())
        {
            region.Union(cube);
        }
        if (_collapsed && (_applicationDragArmed || _applicationDrag))
        {
            region.Union(DragCatchBounds());
        }
        if (_collapsed && _identityLabelOpacity > 0)
        {
            region.Union(IdentityLabelBounds());
        }
        return region;
    }

    protected override void OnSizeChanged(EventArgs e)
    {
        base.OnSizeChanged(e);
        NotifyLayoutChanged();
    }

    protected override bool IsInputKey(Keys keyData) =>
        (keyData & Keys.KeyCode) is Keys.Left or Keys.Right or Keys.Up or Keys.Down
            or Keys.Home or Keys.End or Keys.Tab ||
        base.IsInputKey(keyData);

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        var background = Pick(_theme.Background, SystemColors.Window);
        var surface = Pick(_theme.Surface, SystemColors.Control);
        var input = Pick(_theme.Input, surface);
        var text = Pick(_theme.Text, SystemColors.ControlText);
        var muted = Pick(_theme.Muted, text);
        var border = Pick(_theme.Border, SystemColors.ControlDark);
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        e.Graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
        e.Graphics.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
        e.Graphics.Clear(background);

        if (_collapsed)
        {
            DrawCompact(e.Graphics);
            return;
        }

        var layout = CalculateLayout();
        using var bodyPath = GraphicsExtensions.RoundedPath(layout.Body, PanelCornerRadius);
        using (var terminalBrush = new SolidBrush(background))
        {
            e.Graphics.FillPath(terminalBrush, bodyPath);
        }
        if (!layout.Divider.IsEmpty)
        {
            using var dividerBrush = new SolidBrush(Color.FromArgb(40, border));
            e.Graphics.FillRectangle(dividerBrush,
                layout.Divider.Left + (layout.Divider.Width - Scale(1)) / 2,
                layout.Divider.Top, Scale(1), layout.Divider.Height);
        }

        var frameInset = Scale(1);
        var frameBounds = Rectangle.Inflate(layout.Body, -frameInset, -frameInset);
        if (frameBounds.Width > 1 && frameBounds.Height > 1)
        {
            using var framePen = new Pen(Color.FromArgb(32, border), frameInset);
            e.Graphics.DrawRoundedRectangle(
                framePen,
                frameBounds,
                Math.Max(0, PanelCornerRadius - frameInset));
            if (!layout.Attachment.IsEmpty &&
                layout.Attachment.Height > frameInset * 2)
            {
                e.Graphics.DrawLine(
                    framePen,
                    layout.Attachment.Left,
                    layout.Attachment.Top + frameInset,
                    layout.Attachment.Left,
                    layout.Attachment.Bottom - frameInset);
            }
        }

        DrawTerminal(e.Graphics, layout.Terminal, text, Pick(_theme.Brand, text));
        DrawStatus(e.Graphics, layout.Terminal, muted, text);
        DrawDockBackdrop(e.Graphics, layout.Body, background);
        DrawControlStrip(e.Graphics, surface, input, text, border);
        DrawCubes(e.Graphics);
        if (_pickerOpen) DrawPicker(e.Graphics, surface, input, text, muted, border);
    }

    private void DrawDockBackdrop(Graphics graphics, Rectangle terminal, Color background)
    {
        var state = graphics.Save();
        graphics.SetClip(Rectangle.Inflate(terminal, -Scale(1), -Scale(1)), CombineMode.Intersect);
        foreach (var controls in new[] { CubeBounds(), CalculateControls().Bar })
        {
            var fade = Rectangle.Inflate(controls, PanelPadding, PanelPadding);
            fade.Height += _bodyFont.Height * 2;
            using var brush = new LinearGradientBrush(
                fade, Color.FromArgb(200, background), Color.FromArgb(0, background),
                LinearGradientMode.Vertical);
            graphics.FillRoundedRectangle(brush, fade, PanelCornerRadius);
        }
        graphics.Restore(state);
    }

    private void DrawControlStrip(
        Graphics graphics,
        Color surface,
        Color input,
        Color text,
        Color border)
    {
        var controls = CalculateControls();
        using var stripBrush = new SolidBrush(Color.FromArgb(225, surface));
        using var stripPen = new Pen(Color.FromArgb(32, border), Scale(1));
        graphics.FillRoundedRectangle(stripBrush, controls.Bar, PanelCornerRadius);
        var frameInset = Scale(1);
        var frameBounds = Rectangle.Inflate(controls.Bar, -frameInset, -frameInset);
        if (frameBounds.Width > 1 && frameBounds.Height > 1)
        {
            graphics.DrawRoundedRectangle(
                stripPen,
                frameBounds,
                Math.Max(0, PanelCornerRadius - frameInset));
        }
        foreach (var separator in controls.Separators)
        {
            graphics.DrawLine(
                stripPen,
                separator,
                controls.Bar.Top + ButtonPadding,
                separator,
                controls.Bar.Bottom - ButtonPadding);
        }
        foreach (var button in controls.Buttons)
        {
            DrawControlButton(graphics, button, surface, text);
            if (Focused && _keyboardAction == button.Action)
                ControlPaint.DrawFocusRectangle(graphics, Rectangle.Inflate(button.Bounds, -Scale(3), -Scale(3)), text, input);
        }
        TextRenderer.DrawText(
            graphics,
            $"{Math.Round(_terminalScale * 100):0}%",
            _bodyFont,
            controls.ZoomLabel,
            text,
            TextFormatFlags.HorizontalCenter |
            TextFormatFlags.VerticalCenter |
            TextFormatFlags.NoPadding);
    }

    private void DrawControlButton(
        Graphics graphics,
        ControlButton button,
        Color surface,
        Color text)
    {
        var enabled = ControlEnabled(button.Action);
        var level = button.Action == ControlAction.Mode || _keyboardAction == button.Action
            ? 1f : _controlHover.GetValueOrDefault(button.Action);
        var activeInk = button.Action == ControlAction.Close ? _theme.Error : text;
        var ink = enabled ? Blend(activeInk, _theme.Muted, level) : _theme.Muted;
        var amount = (button.Action == ControlAction.Close ? _theme.ChromeCloseMix : _theme.ChromeHoverMix) / 100f;
        using var fill = new SolidBrush(enabled ? Blend(activeInk, surface, amount * level) : surface);
        graphics.FillRoundedRectangle(fill, button.Bounds, ButtonCornerRadius);
        var iconBounds = CenteredSquare(button.Bounds, IconSize);
        if (button.Action == ControlAction.Attach && _attachmentIcon is not null)
        {
            graphics.DrawIcon(_attachmentIcon, iconBounds);
            return;
        }
        DrawGlyph(graphics, button.Action, iconBounds, ink);
    }

    private void DrawGlyph(Graphics graphics, ControlAction action, Rectangle bounds, Color color)
    {
        var inset = Math.Max(Scale(1), bounds.Width / 7);
        var left = bounds.Left + inset;
        var top = bounds.Top + inset;
        var right = bounds.Right - inset - 1;
        var bottom = bounds.Bottom - inset - 1;
        var middleX = bounds.Left + bounds.Width / 2;
        var middleY = bounds.Top + bounds.Height / 2;
        using var pen = new Pen(color, Math.Max(Scale(1), bounds.Width / 9f))
        {
            StartCap = LineCap.Round,
            EndCap = LineCap.Round,
            LineJoin = LineJoin.Round,
        };
        switch (action)
        {
            case ControlAction.Attach:
                graphics.DrawRoundedRectangle(
                    pen,
                    new Rectangle(left, top + inset, right - left - inset, bottom - top - inset),
                    ButtonCornerRadius);
                graphics.DrawLine(pen, right - inset, top, right - inset, middleY);
                graphics.DrawLine(pen, right - inset * 2, top + inset, right, top + inset);
                break;
            case ControlAction.Detach:
                graphics.DrawRoundedRectangle(
                    pen,
                    new Rectangle(left, top, right - left - inset, bottom - top),
                    ButtonCornerRadius);
                graphics.DrawLine(pen, middleX, middleY, right, bottom);
                graphics.DrawLine(pen, right, middleY, middleX, bottom);
                break;
            case ControlAction.Mode:
                if (_selectedMode == AttachmentMode.Embedded)
                {
                    graphics.DrawRectangle(pen, left, top, right - left, bottom - top);
                    graphics.DrawRectangle(
                        pen,
                        left + inset,
                        top + inset,
                        Math.Max(1, right - left - inset * 2),
                        Math.Max(1, bottom - top - inset * 2));
                }
                else
                {
                    var width = Math.Max(2, (right - left - inset) / 2);
                    graphics.DrawRectangle(pen, left, top + inset, width, bottom - top - inset);
                    graphics.DrawRectangle(pen, right - width, top, width, bottom - top - inset);
                }
                break;
            case ControlAction.ZoomOut:
                graphics.DrawLine(pen, left, middleY, right, middleY);
                break;
            case ControlAction.ZoomIn:
                graphics.DrawLine(pen, left, middleY, right, middleY);
                graphics.DrawLine(pen, middleX, top, middleX, bottom);
                break;
            case ControlAction.Maximize:
                if (_maximized)
                {
                    graphics.DrawRectangle(
                        pen,
                        left + inset,
                        top,
                        Math.Max(1, right - left - inset),
                        Math.Max(1, bottom - top - inset));
                    graphics.DrawRectangle(
                        pen,
                        left,
                        top + inset,
                        Math.Max(1, right - left - inset),
                        Math.Max(1, bottom - top - inset));
                }
                else
                {
                    graphics.DrawRectangle(pen, left, top, right - left, bottom - top);
                }
                break;
            case ControlAction.Collapse:
                graphics.DrawLines(pen, new[]
                {
                    new Point(left, middleY + inset / 2),
                    new Point(middleX, middleY - inset),
                    new Point(right, middleY + inset / 2),
                });
                break;
            case ControlAction.Close:
                graphics.DrawLine(pen, left, top, right, bottom);
                graphics.DrawLine(pen, right, top, left, bottom);
                break;
        }
    }

    private void DrawTerminal(Graphics graphics, Rectangle terminal, Color defaultText, Color cursorColor)
    {
        var state = graphics.Save();
        var inset = Math.Max(Scale(2), PanelPadding / 2);
        graphics.SetClip(new Rectangle(
            terminal.X + 1,
            terminal.Y + 1,
            Math.Max(1, terminal.Width - 2),
            Math.Max(1, terminal.Height - 2)));
        var cell = TerminalCellSize();
        var cellWidth = cell.Width;
        var availableHeight = Math.Max(1, terminal.Height - TerminalVerticalPadding * 2);
        var lineHeight = cell.Height;
        var visibleRows = Math.Max(1, availableHeight / lineHeight);
        var firstRow = Math.Max(0, _screenRows - visibleRows);
        var origin = new Point(terminal.X + inset, terminal.Y + TerminalVerticalPadding);
        DrawTerminalSelection(graphics, terminal, firstRow, visibleRows, origin, cell);
        var y = origin.Y;
        foreach (var row in _screen.Skip(firstRow).Take(visibleRows))
        {
            var x = terminal.X + inset;
            foreach (var fragment in row)
            {
                var columns = Math.Max(0, fragment.Columns);
                var width = columns * cellWidth;
                var foreground = fragment.Foreground ?? defaultText;
                if (fragment.Dim)
                {
                    foreground = Blend(foreground, Pick(_theme.Background, BackColor), 0.55f);
                }
                var style = FontStyle.Regular;
                if (fragment.Bold) style |= FontStyle.Bold;
                if (fragment.Italic) style |= FontStyle.Italic;
                if (fragment.Underline) style |= FontStyle.Underline;
                if (fragment.Strike) style |= FontStyle.Strikeout;
                TextRenderer.DrawText(
                    graphics,
                    fragment.Text,
                    fragment.Emoji ? EmojiFont(style) : TerminalFont(style),
                    new Point(x, y),
                    foreground,
                    TerminalTextFlags);
                x += width;
            }
            y += lineHeight;
        }
        var cursorRow = _cursorY - firstRow;
        if (_cursorVisible && cursorRow >= 0 && cursorRow < visibleRows)
        {
            using var cursorBrush = new SolidBrush(cursorColor);
            graphics.FillRectangle(
                cursorBrush,
                terminal.X + inset + _cursorX * cellWidth,
                terminal.Y + TerminalVerticalPadding + cursorRow * lineHeight + lineHeight - Scale(2),
                cellWidth,
                Scale(2));
        }
        graphics.Restore(state);
    }

    private void DrawTerminalSelection(
        Graphics graphics,
        Rectangle terminal,
        int firstRow,
        int visibleRows,
        Point origin,
        Size cell)
    {
        if (!TryOrderedSelection(out var start, out var end)) return;
        var background = Pick(_theme.Background, BackColor);
        var accent = Pick(_theme.Brand, SystemColors.Highlight);
        using var brush = new SolidBrush(Blend(accent, background, 0.42f));
        var maxColumns = Math.Max(0, (terminal.Right - origin.X) / Math.Max(1, cell.Width));
        for (var offset = 0; offset < visibleRows; offset++)
        {
            var row = firstRow + offset;
            if (row < start.Row || row > end.Row) continue;
            var rowColumns = Math.Min(maxColumns, TerminalRowColumns(row));
            var left = row == start.Row ? start.Column : 0;
            var right = row == end.Row ? end.Column : rowColumns;
            left = Math.Clamp(left, 0, rowColumns);
            right = Math.Clamp(right, 0, rowColumns);
            if (right <= left) continue;
            graphics.FillRectangle(
                brush,
                origin.X + left * cell.Width,
                origin.Y + offset * cell.Height,
                (right - left) * cell.Width,
                cell.Height);
        }
    }

    private void DrawStatus(Graphics graphics, Rectangle terminal, Color muted, Color text)
    {
        if (_status.Length == 0) return;
        TextRenderer.DrawText(
            graphics,
            _status,
            _bodyFont,
            new Rectangle(
                terminal.X + PanelPadding,
                terminal.Bottom - _bodyFont.Height - ButtonPadding,
                Math.Max(0, terminal.Width - PanelPadding * 2),
                _bodyFont.Height + ButtonPadding),
            _statusError ? Pick(_theme.Error, text) : muted,
            TextFormatFlags.EndEllipsis |
            TextFormatFlags.VerticalCenter |
            TextFormatFlags.NoPadding);
    }

    private void DrawPicker(
        Graphics graphics,
        Color surface,
        Color input,
        Color text,
        Color muted,
        Color border)
    {
        var bounds = PickerBounds();
        using var background = new SolidBrush(surface);
        using var outline = new Pen(border, Scale(1));
        graphics.FillRoundedRectangle(background, bounds, PanelCornerRadius);
        graphics.DrawRoundedRectangle(outline, bounds, PanelCornerRadius);
        TextRenderer.DrawText(
            graphics,
            "Attach a window",
            _titleFont,
            new Rectangle(
                bounds.X + PanelPadding,
                bounds.Y + ButtonPadding,
                bounds.Width - PanelPadding * 2 - ControlHeight,
                HeaderHeight - ButtonPadding),
            text,
            TextFormatFlags.EndEllipsis |
            TextFormatFlags.VerticalCenter |
            TextFormatFlags.NoPadding);
        DrawGlyph(graphics, ControlAction.Close, CenteredSquare(PickerCloseBounds(), IconSize), text);

        if (_candidates.Count == 0)
        {
            TextRenderer.DrawText(
                graphics,
                "No visible windows are available.",
                _bodyFont,
                new Rectangle(
                    bounds.X + PanelPadding,
                    bounds.Y + HeaderHeight + PanelPadding,
                    bounds.Width - PanelPadding * 2,
                    _bodyFont.Height + ButtonPadding),
                muted,
                TextFormatFlags.EndEllipsis | TextFormatFlags.NoPadding);
            return;
        }

        var visibleRows = VisiblePickerRows();
        for (var row = 0; row < visibleRows; row++)
        {
            var entry = _pickerOffset + row;
            if (entry >= PickerEntryCount()) break;
            var rowBounds = CandidateBounds(row);
            if (IsUtilityHeader(entry))
            {
                DrawUtilityHeader(graphics, rowBounds, muted, border);
                continue;
            }
            var candidate = CandidateAt(entry);
            if (candidate is null) continue;
            if (_hoverEntry == entry)
            {
                using var hover = new SolidBrush(input);
                graphics.FillRoundedRectangle(hover, rowBounds, ButtonCornerRadius);
            }
            var iconBounds = CenteredSquare(
                new Rectangle(rowBounds.X + ButtonPadding, rowBounds.Y, CandidateRowHeight, rowBounds.Height),
                PickerIconSize);
            if (candidate.Icon is not null)
            {
                graphics.DrawIcon(candidate.Icon, iconBounds);
            }
            else
            {
                DrawWindowGlyph(graphics, iconBounds, muted);
            }
            var processWidth = Math.Min(
                rowBounds.Width / 3,
                TextRenderer.MeasureText(candidate.ProcessName, _bodyFont).Width + ButtonPadding);
            TextRenderer.DrawText(
                graphics,
                candidate.Title,
                _bodyFont,
                new Rectangle(
                    iconBounds.Right + ButtonPadding,
                    rowBounds.Y,
                    Math.Max(0, rowBounds.Right - iconBounds.Right - processWidth - ButtonPadding * 3),
                    rowBounds.Height),
                text,
                TextFormatFlags.EndEllipsis |
                TextFormatFlags.VerticalCenter |
                TextFormatFlags.NoPadding);
            TextRenderer.DrawText(
                graphics,
                candidate.ProcessName,
                _bodyFont,
                new Rectangle(
                    rowBounds.Right - processWidth - ButtonPadding,
                    rowBounds.Y,
                    processWidth,
                    rowBounds.Height),
                muted,
                TextFormatFlags.EndEllipsis |
                TextFormatFlags.Right |
                TextFormatFlags.VerticalCenter |
                TextFormatFlags.NoPadding);
        }
    }

    private void DrawUtilityHeader(
        Graphics graphics,
        Rectangle bounds,
        Color muted,
        Color border)
    {
        const string label = "Tray / utility windows";
        var labelWidth = TextRenderer.MeasureText(
            label,
            _bodyFont,
            Size.Empty,
            TextFormatFlags.NoPadding).Width;
        var labelBounds = new Rectangle(
            bounds.X + ButtonPadding,
            bounds.Y,
            Math.Min(labelWidth, Math.Max(0, bounds.Width - ButtonPadding * 2)),
            bounds.Height);
        TextRenderer.DrawText(
            graphics,
            label,
            _bodyFont,
            labelBounds,
            muted,
            TextFormatFlags.EndEllipsis |
            TextFormatFlags.VerticalCenter |
            TextFormatFlags.NoPadding);
        var lineX = labelBounds.Right + ButtonPadding;
        if (lineX >= bounds.Right - ButtonPadding) return;
        using var pen = new Pen(border, Scale(1));
        graphics.DrawLine(
            pen,
            lineX,
            bounds.Y + bounds.Height / 2,
            bounds.Right - ButtonPadding,
            bounds.Y + bounds.Height / 2);
    }

    private void DrawWindowGlyph(Graphics graphics, Rectangle bounds, Color color)
    {
        using var pen = new Pen(color, Scale(1));
        graphics.DrawRoundedRectangle(pen, bounds, ButtonCornerRadius);
        graphics.DrawLine(
            pen,
            bounds.Left,
            bounds.Top + Math.Max(Scale(2), ButtonPadding),
            bounds.Right,
            bounds.Top + Math.Max(Scale(2), ButtonPadding));
    }

    private void DrawCubes(Graphics graphics)
    {
        DrawCubePair(graphics, CubeRectangles(), InstanceColor, _working,
            _attachmentActive, _identityHovered, WorkingElapsed());
    }

    private void DrawCubePair(Graphics graphics, IReadOnlyList<Rectangle> cubes,
        Color brand, bool working, bool attached, bool hovered, double elapsed, float energy = 0)
    {
        var glow = Pick(_theme.Glow, brand);
        var edge = cubes[0].Width;
        var radius = Math.Max(0, (int)Math.Round(edge * _theme.CubeCornerRadius));
        var glowLevel = Math.Clamp(_theme.CubeGlow + energy * .8f, 0, 1);
        for (var index = 0; index < cubes.Count; index++)
        {
            var rectangle = cubes[index];
            var frame = FrameBrightness(index, working, elapsed);
            var brightness = index == 1 && !attached ? 0.55f : 1f;
            brightness = Math.Min(1, brightness + CubeDropBrightness());
            if (hovered) brightness = Math.Min(1, brightness + 0.2f);
            brightness += (1 - brightness) * energy;
            if (working) brightness *= 0.72f + 0.28f * frame;
            var cubeColor = Blend(Blend(_theme.Text, brand, energy * .4f), Color.Black, brightness);
            using var cube = new SolidBrush(cubeColor);
            graphics.FillRoundedRectangle(cube, rectangle, radius);
            if (glowLevel > 0 || working || hovered)
            {
                var inset = Rectangle.Inflate(rectangle, -Scale(1), -Scale(1));
                var alpha = Math.Max(
                    (int)(120 * glowLevel),
                    hovered ? 210 : (int)(210 * frame));
                using var glowPen = new Pen(
                    Color.FromArgb(alpha, glow),
                    Scale((hovered || frame >= 0.65f) ? 2 : 1));
                graphics.DrawRoundedRectangle(
                    glowPen,
                    inset,
                    Math.Max(0, radius - Scale(1)));
            }
            using var shade = new Pen(Color.FromArgb(90, Pick(_theme.Input, brand)), Scale(1));
            var shadeX = rectangle.Right - Math.Max(Scale(1), edge / 4);
            graphics.DrawLine(shade, shadeX, rectangle.Top + edge / 4, shadeX, rectangle.Bottom - Scale(1));
        }
    }

    internal object LaunchTarget(Point origin)
    {
        using var bitmap = new Bitmap(Width, Height);
        using (var graphics = Graphics.FromImage(bitmap))
        {
            graphics.Clear(Color.Transparent);
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
            DrawCubes(graphics);
        }
        using var stream = new MemoryStream();
        bitmap.Save(stream, System.Drawing.Imaging.ImageFormat.Png);
        return new { png = Convert.ToBase64String(stream.ToArray()),
            position = new[] { origin.X, origin.Y },
            cubes = CubeRectangles().Select(rectangle => new[] {
                rectangle.X, rectangle.Y, rectangle.Width, rectangle.Height }).ToArray() };
    }

    private static float FrameBrightness(int index, bool working, double elapsed)
    {
        if (!working) return 0;
        const int count = 2;
        var position = (elapsed * WorkingStepsPerSecond) % count;
        var distance = Math.Min(
            (index - position + count) % count,
            (position - index + count) % count);
        return (float)Math.Max(0, 1 - distance * 1.15);
    }

    public Bitmap RenderCompactFrame()
    {
        var bitmap = new Bitmap(Math.Max(1, Width), Math.Max(1, Height),
            System.Drawing.Imaging.PixelFormat.Format32bppPArgb);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
        graphics.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
        graphics.Clear(Color.Transparent);
        DrawCompact(graphics);
        return bitmap;
    }

    public Bitmap RenderPresentationFrame()
    {
        if (_collapsed) return RenderCompactFrame();
        var bitmap = new Bitmap(Math.Max(1, Width), Math.Max(1, Height),
            System.Drawing.Imaging.PixelFormat.Format32bppPArgb);
        using var graphics = Graphics.FromImage(bitmap);
        OnPaint(new PaintEventArgs(graphics, ClientRectangle));
        using var region = CreateWindowRegion();
        graphics.ResetClip();
        graphics.ExcludeClip(region);
        graphics.CompositingMode = CompositingMode.SourceCopy;
        using var clear = new SolidBrush(Color.Transparent);
        graphics.FillRectangle(clear, ClientRectangle);
        return bitmap;
    }

    private void DrawCompact(Graphics graphics)
    {
        if (HasCompactGroup) { DrawCompactGroup(graphics); return; }
        DrawCubes(graphics);
        DrawIdentityLabel(graphics);
        if (Focused && ShowFocusCues)
            ControlPaint.DrawFocusRectangle(graphics, CubeBounds(), _theme.Text, Color.Transparent);
    }

    private void DrawIdentityLabel(Graphics graphics)
    {
        if (_identityLabelOpacity <= 0) return;
        var bounds = IdentityLabelBounds();
        var alpha = Math.Clamp((int)Math.Round(255 * _identityLabelOpacity), 0, 255);
        using var brush = new SolidBrush(Color.FromArgb(alpha, Pick(_theme.Text, ForeColor)));
        using var format = new StringFormat
        {
            Alignment = StringAlignment.Center,
            LineAlignment = StringAlignment.Center,
            Trimming = StringTrimming.EllipsisCharacter,
            FormatFlags = StringFormatFlags.NoWrap,
        };
        using var shadow = new SolidBrush(Color.FromArgb(alpha * 3 / 4, Pick(_theme.Background, BackColor)));
        var shadowBounds = bounds;
        shadowBounds.Offset(0, Scale(1));
        graphics.DrawString(IdentityTitle(), _identityFont, shadow, shadowBounds, format);
        graphics.DrawString(IdentityTitle(), _identityFont, brush, bounds, format);
    }

    private void OnMouseDown(object? sender, MouseEventArgs e)
    {
        if (e.Button != MouseButtons.Left) return;
        if (GroupMouseDown(e)) return;
        Focus();
        _keyboardAction = null;
        var resizeHit = ResizeHitTest(e.Location);
        if (!_collapsed && resizeHit != 1)
        {
            Capture = false;
            _ = ReleaseCapture();
            var form = FindForm();
            if (form is not null)
            {
                _ = SendMessage(form.Handle, 0x00A1, (nint)resizeHit, 0);
            }
            return;
        }
        _cursorDown = PointToScreen(e.Location);
        _formDown = FindForm()?.Location ?? Point.Empty;
        _windowDragStarted = false;
        if (CubeRectangles().Any(cube => cube.Contains(e.Location)))
        {
            _draggingCube = true;
            Capture = true;
            return;
        }
        if (_collapsed) return;
        if (!_pickerOpen && CalculateControls().Bar.Contains(e.Location))
        {
            ClearTerminalSelection();
            return;
        }
        if (!_pickerOpen && TitleBarBounds().Contains(e.Location))
        {
            if (!_maximized)
            {
                _draggingTitleBar = true;
                Capture = true;
            }
            return;
        }
        if (!_pickerOpen && DividerHitBounds().Contains(e.Location))
        {
            ClearTerminalSelection();
            _resizing = true;
            Capture = true;
            return;
        }
        var terminal = CalculateLayout().Terminal;
        if (!_pickerOpen && terminal.Contains(e.Location))
        {
            _selectionAnchor = TerminalPointAt(e.Location);
            _selectionEnd = _selectionAnchor;
            _selecting = true;
            Capture = true;
            Invalidate(terminal);
            return;
        }
        ClearTerminalSelection();
    }

    private void OnMouseMove(object? sender, MouseEventArgs e)
    {
        if (GroupMouseMove(e)) return;
        UpdateHover(e.Location);
        var terminal = CalculateLayout().Terminal;
        var resizeHit = ResizeHitTest(e.Location);
        Cursor = resizeHit != 1
            ? ResizeCursor(resizeHit)
            : !_collapsed && TitleBarBounds().Contains(e.Location)
                ? Cursors.Default
                : _resizing || DividerHitBounds().Contains(e.Location)
                    ? Cursors.VSplit
                    : !_collapsed && !_pickerOpen && terminal.Contains(e.Location)
                        ? Cursors.IBeam
                        : Cursors.Default;
        if ((e.Button & MouseButtons.Left) == 0) return;
        if (_draggingCube || _draggingTitleBar)
        {
            var form = FindForm();
            var cursor = PointToScreen(e.Location);
            if (form is not null && !_maximized &&
                (_windowDragStarted || Distance(cursor, _cursorDown) >= Scale(6)))
            {
                _windowDragStarted = true;
                form.Location = new Point(
                    _formDown.X + cursor.X - _cursorDown.X,
                    _formDown.Y + cursor.Y - _cursorDown.Y);
            }
            return;
        }
        if (_selecting)
        {
            _selectionEnd = TerminalPointAt(e.Location);
            Invalidate(terminal);
            return;
        }
        if (!_resizing) return;
        var layout = CalculateLayout();
        var available = Math.Max(1, layout.Body.Width - DividerWidth);
        var x = e.X - layout.Body.Left;
        var minimum = (float)MinimumPaneWidth / available;
        var maximum = 1f - (float)AttachmentMinimumPaneWidth / available;
        _split = minimum <= maximum
            ? Math.Clamp((float)x / available, minimum, maximum)
            : Math.Clamp((float)x / available, 0f, 1f);
        Invalidate(layout.Body);
        NotifyLayoutChanged();
    }

    private void OnMouseUp(object? sender, MouseEventArgs e)
    {
        if (e.Button != MouseButtons.Left) return;
        if (GroupMouseUp(e)) return;
        var moved = _windowDragStarted || Distance(PointToScreen(e.Location), _cursorDown) >= Scale(6);
        if (_draggingCube || _draggingTitleBar)
        {
            if (_draggingCube && !moved) ToggleCollapsedRequested?.Invoke();
            if (_collapsed && moved) CompactDragCompleted?.Invoke();
            ResetPointerState();
            return;
        }
        if (_resizing)
        {
            ResetPointerState();
            return;
        }
        if (_selecting)
        {
            _selectionEnd = TerminalPointAt(e.Location);
            _selecting = false;
            Capture = false;
            Invalidate(CalculateLayout().Terminal);
            return;
        }
        if (_collapsed)
        {
            ResetPointerState();
            return;
        }
        if (_pickerOpen && TrySelectCandidate(e.Location))
        {
            ResetPointerState();
            return;
        }
        var action = CalculateControls().Buttons
            .FirstOrDefault(button => button.Bounds.Contains(e.Location))?.Action;
        if (action is ControlAction selected) InvokeControl(selected);
        else if (_pickerOpen && !PickerBounds().Contains(e.Location)) ClosePicker();
        ResetPointerState();
    }

    private void InvokeControl(ControlAction action)
    {
        if (!ControlEnabled(action)) return;
        switch (action)
        {
            case ControlAction.Attach:
                OpenPicker();
                break;
            case ControlAction.Detach:
                DetachRequested?.Invoke();
                break;
            case ControlAction.Mode:
                _selectedMode = _selectedMode == AttachmentMode.Managed
                    ? AttachmentMode.Embedded
                    : AttachmentMode.Managed;
                _toolTip.SetToolTip(this, ToolTipText(ControlAction.Mode));
                if (_pickerOpen) OpenPicker();
                Invalidate(CalculateControls().Bar);
                break;
            case ControlAction.ZoomOut:
                ChangeTerminalScale(-0.1f);
                break;
            case ControlAction.ZoomIn:
                ChangeTerminalScale(0.1f);
                break;
            case ControlAction.Maximize:
                ToggleMaximizedRequested?.Invoke();
                break;
            case ControlAction.Collapse:
                ToggleCollapsedRequested?.Invoke();
                break;
            case ControlAction.Close:
                CloseRequested?.Invoke();
                break;
        }
    }

    private void OnMouseWheel(object? sender, MouseEventArgs e)
    {
        if (!_pickerOpen)
        {
            if ((ModifierKeys & Keys.Control) != 0)
            {
                ChangeTerminalScale(e.Delta > 0 ? 0.1f : -0.1f);
            }
            else if (!_collapsed && e.Delta != 0 &&
                CalculateLayout().Terminal.Contains(e.Location))
            {
                ClearTerminalSelection();
                InputRequested?.Invoke(e.Delta > 0 ? "\x1b[5~" : "\x1b[6~");
            }
            return;
        }
        if (PickerEntryCount() <= VisiblePickerRows()) return;
        var direction = e.Delta > 0 ? -1 : 1;
        _pickerOffset = Math.Clamp(
            _pickerOffset + direction,
            0,
            Math.Max(0, PickerEntryCount() - VisiblePickerRows()));
        _hoverEntry = -1;
        Invalidate(PickerBounds());
    }

    private void ChangeTerminalScale(float delta)
    {
        var next = Math.Clamp((float)Math.Round((_terminalScale + delta) * 10) / 10f, 0.7f, 1.6f);
        if (Math.Abs(next - _terminalScale) < 0.001f) return;
        _terminalScale = next;
        RebuildTerminalFonts(_theme.MonoFont);
        Invalidate();
        NotifyLayoutChanged();
    }

    private void OpenPicker()
    {
        ClosePicker();
        ClearTerminalSelection();
        try
        {
            _candidates = WindowCandidatesRequested?.Invoke() ?? Array.Empty<WindowCandidate>();
            _pickerOffset = 0;
            _hoverEntry = Enumerable.Range(0, PickerEntryCount())
                .FirstOrDefault(entry => CandidateAt(entry) is not null, -1);
            _pickerOpen = true;
            _status = "";
            PickerVisibilityChanged?.Invoke(true);
            AccessibilityNotifyClients(AccessibleEvents.Reorder, -1);
            NotifyKeyboardFocus();
        }
        catch (Exception error)
        {
            ClosePicker();
            SetStatus($"Could not list windows: {error.Message}", isError: true);
        }
        Invalidate();
    }

    private void ClosePicker()
    {
        var wasOpen = _pickerOpen;
        foreach (var candidate in _candidates) candidate.Dispose();
        _candidates = Array.Empty<WindowCandidate>();
        _pickerOffset = 0;
        _hoverEntry = -1;
        _pickerOpen = false;
        if (wasOpen) PickerVisibilityChanged?.Invoke(false);
        if (wasOpen) AccessibilityNotifyClients(AccessibleEvents.Reorder, -1);
        Invalidate();
    }

    private void SelectCandidate(int entry)
    {
        if (!_pickerOpen || CandidateAt(entry) is not WindowCandidate candidate) return;
        AttachRequested?.Invoke(candidate, _selectedMode);
        if (_pickerOpen) ClosePicker();
    }

    private bool TrySelectCandidate(Point location)
    {
        var picker = PickerBounds();
        if (!picker.Contains(location)) return false;
        if (PickerCloseBounds().Contains(location))
        {
            ClosePicker();
            return true;
        }
        if (location.Y < picker.Y + HeaderHeight) return true;
        var row = (location.Y - picker.Y - HeaderHeight) / CandidateRowHeight;
        if (row < 0 || row >= VisiblePickerRows()) return true;
        var entry = _pickerOffset + row;
        SelectCandidate(entry);
        return true;
    }

    private bool ControlEnabled(ControlAction action) => action switch
    {
        ControlAction.Detach => _attachmentActive,
        ControlAction.Mode => !_attachmentActive,
        ControlAction.ZoomOut => _terminalScale > 0.7f,
        ControlAction.ZoomIn => _terminalScale < 1.6f,
        _ => true,
    };

    protected override bool ProcessCmdKey(ref Message message, Keys keyData)
    {
        var key = keyData & Keys.KeyCode;
        if (GroupKey(keyData)) return true;
        if ((keyData & (Keys.Control | Keys.Alt)) != 0)
            return base.ProcessCmdKey(ref message, keyData);
        if (_collapsed && key is Keys.Enter or Keys.Space)
        {
            ToggleCollapsedRequested?.Invoke();
            return true;
        }
        if (!_collapsed && !_pickerOpen && key == Keys.F6)
        {
            _keyboardAction = _keyboardAction is null ? ControlAction.Attach : null;
            Invalidate();
            NotifyKeyboardFocus();
            return true;
        }
        if (_pickerOpen)
        {
            if (key == Keys.Escape) { ClosePicker(); return true; }
            if (key is Keys.Enter or Keys.Space)
            {
                if (_hoverEntry < 0) ClosePicker();
                else SelectCandidate(_hoverEntry);
                return true;
            }
            if (key is Keys.Up or Keys.Down or Keys.Home or Keys.End or Keys.Tab)
            {
                var entries = Enumerable.Range(0, PickerEntryCount())
                    .Where(entry => CandidateAt(entry) is not null).ToArray();
                if (entries.Length == 0) return true;
                var index = Array.IndexOf(entries, _hoverEntry);
                var backwards = key == Keys.Up || (key == Keys.Tab && keyData.HasFlag(Keys.Shift));
                if (index < 0) index = backwards ? 0 : -1;
                index = key == Keys.Home ? 0 : key == Keys.End ? entries.Length - 1 :
                    (index + (backwards ? -1 : 1) + entries.Length) % entries.Length;
                _hoverEntry = entries[index];
                _pickerOffset = Math.Clamp(_pickerOffset,
                    Math.Max(0, _hoverEntry - VisiblePickerRows() + 1), _hoverEntry);
                Invalidate(PickerBounds());
                NotifyKeyboardFocus();
                return true;
            }
        }
        else if (_keyboardAction is ControlAction action)
        {
            if (key == Keys.Escape) { _keyboardAction = null; Invalidate(); return true; }
            if (key is Keys.Enter or Keys.Space) { InvokeControl(action); return true; }
            if (key is Keys.Left or Keys.Right or Keys.Up or Keys.Down or Keys.Tab or Keys.Home or Keys.End)
            {
                var actions = CalculateControls().Buttons.Select(button => button.Action)
                    .Where(ControlEnabled).ToArray();
                var index = Array.IndexOf(actions, action);
                var backwards = key is Keys.Left or Keys.Up || (key == Keys.Tab && keyData.HasFlag(Keys.Shift));
                if (index < 0) index = backwards ? 0 : -1;
                index = key == Keys.Home ? 0 : key == Keys.End ? actions.Length - 1 :
                    (index + (backwards ? -1 : 1) + actions.Length) % actions.Length;
                _keyboardAction = actions[index];
                Invalidate(CalculateControls().Bar);
                NotifyKeyboardFocus();
                return true;
            }
        }
        return base.ProcessCmdKey(ref message, keyData);
    }

    private void NotifyKeyboardFocus() => AccessibilityNotifyClients(
        AccessibleEvents.Focus, Array.FindIndex(AccessibleParts().ToArray(), part => part.Focused));

    private sealed record AccessiblePart(string Key, string Name, Rectangle Bounds,
        AccessibleRole Role, bool Enabled, bool Focused, Action Activate, Action Focus);

    private IEnumerable<AccessiblePart> AccessibleParts()
    {
        if (IsDisposed) yield break;
        if (HasCompactGroup)
        {
            for (var index = 0; index < _group.Length; index++)
            {
                var member = _group[index];
                var selected = index;
                yield return new($"shell:{member.Handle}", $"Expand MO Shell {member.Ordinal}",
                    GroupMemberBounds(member.Handle), AccessibleRole.PushButton, true,
                    Focused && _groupSelection == index, () => GroupOpenRequested?.Invoke(member.Handle),
                    () => { Focus(); _groupSelection = selected; Invalidate(); });
            }
            yield break;
        }
        if (_collapsed)
        {
            yield return new("identity", "Expand MO Shell", CubeBounds(), AccessibleRole.PushButton,
                true, Focused, () => ToggleCollapsedRequested?.Invoke(), () => Focus());
            yield break;
        }
        foreach (var button in CalculateControls().Buttons)
        {
            var action = button.Action;
            yield return new(action.ToString(), ToolTipText(action), button.Bounds, AccessibleRole.PushButton,
                ControlEnabled(action), Focused && !_pickerOpen && _keyboardAction == action,
                () => InvokeControl(action), () => { Focus(); _keyboardAction = action; Invalidate(); });
        }
        if (!_pickerOpen) yield break;
        yield return new("picker-close", "Close window picker", PickerCloseBounds(), AccessibleRole.PushButton,
            true, Focused && _hoverEntry < 0, ClosePicker,
            () => { Focus(); _hoverEntry = -1; Invalidate(PickerBounds()); });
        for (var row = 0; row < VisiblePickerRows(); row++)
        {
            var entry = _pickerOffset + row;
            if (CandidateAt(entry) is not WindowCandidate candidate) continue;
            yield return new($"window-{candidate.Handle}", candidate.Title, CandidateBounds(row), AccessibleRole.ListItem,
                true, Focused && _hoverEntry == entry, () => SelectCandidate(entry),
                () => { Focus(); _hoverEntry = entry; Invalidate(PickerBounds()); });
        }
    }

    protected override AccessibleObject CreateAccessibilityInstance() => new SurfaceAccessibility(this);

    private sealed class SurfaceAccessibility(ShellSurface owner) : ControlAccessibleObject(owner)
    {
        public override string? Name { get => "MO Shell"; set => base.Name = value; }
        public override AccessibleRole Role => AccessibleRole.Pane;
        public override int GetChildCount() => owner.AccessibleParts().Count();
        public override AccessibleObject? GetChild(int index)
        {
            var part = owner.AccessibleParts().ElementAtOrDefault(index);
            return part is null ? null : new PartAccessibility(owner, part.Key);
        }
        public override AccessibleObject? GetFocused()
        {
            var part = owner.AccessibleParts().FirstOrDefault(part => part.Focused);
            return part is null ? base.GetFocused() : new PartAccessibility(owner, part.Key);
        }
        public override AccessibleObject? HitTest(int x, int y)
        {
            var point = owner.PointToClient(new Point(x, y));
            var part = owner.AccessibleParts().FirstOrDefault(part => part.Bounds.Contains(point));
            return part is null ? base.HitTest(x, y) : new PartAccessibility(owner, part.Key);
        }
    }

    private sealed class PartAccessibility(ShellSurface owner, string key) : AccessibleObject
    {
        private AccessiblePart? Part => owner.AccessibleParts().FirstOrDefault(part => part.Key == key);
        public override AccessibleObject? Parent => owner.AccessibilityObject;
        public override string? Name { get => Part?.Name ?? ""; set { } }
        public override Rectangle Bounds => Part is { } part ? owner.RectangleToScreen(part.Bounds) : Rectangle.Empty;
        public override AccessibleRole Role => Part?.Role ?? AccessibleRole.None;
        public override string DefaultAction => Role == AccessibleRole.ListItem ? "Select" : "Press";
        public override AccessibleStates State => Part is { } part
            ? AccessibleStates.Focusable | (part.Enabled ? 0 : AccessibleStates.Unavailable)
                | (part.Focused ? AccessibleStates.Focused : 0)
            : AccessibleStates.Invisible | AccessibleStates.Unavailable;
        public override void DoDefaultAction() => OnUi(() =>
        {
            if (Part is { Enabled: true } part) part.Activate();
        });
        public override void Select(AccessibleSelection flags) => OnUi(() =>
        {
            if (flags.HasFlag(AccessibleSelection.TakeFocus) && Part is { Enabled: true } part)
            {
                part.Focus();
                owner.NotifyKeyboardFocus();
            }
        });
        private void OnUi(Action action)
        {
            if (owner.IsDisposed) return;
            if (owner.InvokeRequired) owner.BeginInvoke(action);
            else action();
        }
    }

    private void UpdateHover(Point location, bool outside = false)
    {
        var previousAction = _hoverAction;
        var previousEntry = _hoverEntry;
        var previousIdentity = _identityHovered;
        _identityHovered = !outside && CubeRectangles().Any(cube => cube.Contains(location));
        _hoverAction = outside || _collapsed
            ? null
            : CalculateControls().Buttons
                .FirstOrDefault(button => button.Bounds.Contains(location))?.Action;
        _hoverEntry = -1;
        if (!outside && _pickerOpen && PickerBounds().Contains(location) &&
            location.Y >= PickerBounds().Y + HeaderHeight)
        {
            var row = (location.Y - PickerBounds().Y - HeaderHeight) / CandidateRowHeight;
            var entry = _pickerOffset + row;
            if (row >= 0 && row < VisiblePickerRows() && CandidateAt(entry) is not null)
            {
                _hoverEntry = entry;
            }
        }
        if (previousAction != _hoverAction)
        {
            if (_hoverAction is ControlAction hovered) _controlHover.TryAdd(hovered, 0);
            _controlHoverTick = Stopwatch.GetTimestamp();
            UpdateIdentityAnimation();
            _toolTip.SetToolTip(
                this,
                _hoverAction is ControlAction action
                    ? ToolTipText(action)
                    : "");
            Invalidate(CalculateControls().Bar);
        }
        if (previousIdentity != _identityHovered)
        {
            UpdateIdentityAnimation();
            CompactPresentationChanged?.Invoke();
            Invalidate(CubeBounds());
        }
        if (previousEntry != _hoverEntry && _pickerOpen) Invalidate(PickerBounds());
    }

    private string IdentityTitle()
    {
        var title = HasCompactGroup ? _group.FirstOrDefault(member => member.Handle == _groupHover)?.Title : _attachmentTitle;
        return string.IsNullOrWhiteSpace(title) ? "MO Agent" : $"MO Agent + {title}";
    }

    private string ToolTipText(ControlAction action) => action switch
    {
        ControlAction.Attach => "Attach a window",
        ControlAction.Detach => "Detach window",
        ControlAction.Mode => _selectedMode == AttachmentMode.Managed
            ? "Managed attachment mode"
            : "Embedded attachment mode",
        ControlAction.ZoomOut => "Zoom terminal out",
        ControlAction.ZoomIn => "Zoom terminal in",
        ControlAction.Maximize => _maximized ? "Restore MO Shell" : "Maximize MO Shell",
        ControlAction.Collapse => "Collapse to cubes",
        ControlAction.Close => "Close MO Shell",
        _ => "",
    };

    private void OnKeyPress(object? sender, KeyPressEventArgs e)
    {
        if (_collapsed || _pickerOpen || _keyboardAction is not null || char.IsControl(e.KeyChar)) return;
        ClearTerminalSelection();
        InputRequested?.Invoke(e.KeyChar.ToString());
    }

    private void OnKeyDown(object? sender, KeyEventArgs e)
    {
        if (_collapsed) return;
        if (_keyboardAction is not null && !_pickerOpen)
        {
            e.SuppressKeyPress = true;
            return;
        }
        if (_pickerOpen)
        {
            if (e.KeyCode == Keys.Escape)
            {
                ClosePicker();
                e.SuppressKeyPress = true;
            }
            return;
        }
        if (e.Control && !e.Alt && e.KeyCode == Keys.V)
        {
            PasteTerminalClipboard();
            e.SuppressKeyPress = true;
            return;
        }
        if (e.Control && e.KeyCode == Keys.C && CopyTerminalSelection())
        {
            e.SuppressKeyPress = true;
            return;
        }
        if (e.KeyCode == Keys.Escape && HasTerminalSelection)
        {
            ClearTerminalSelection();
            e.SuppressKeyPress = true;
            return;
        }
        var value = e.KeyCode switch
        {
            Keys.Enter => "\r",
            Keys.Back => "\b",
            Keys.Tab => "\t",
            Keys.Escape => "\x1b",
            Keys.Up when e.Alt => "\x1b[1;3A",
            Keys.Down when e.Alt => "\x1b[1;3B",
            Keys.Right when e.Alt => "\x1b[1;3C",
            Keys.Left when e.Alt => "\x1b[1;3D",
            Keys.Home when e.Alt => "\x1b[1;3H",
            Keys.End when e.Alt => "\x1b[1;3F",
            Keys.Up => "\x1b[A",
            Keys.Down => "\x1b[B",
            Keys.Right => "\x1b[C",
            Keys.Left => "\x1b[D",
            Keys.Home => "\x1b[H",
            Keys.End => "\x1b[F",
            Keys.Delete => "\x1b[3~",
            _ when e.Alt && e.KeyCode >= Keys.A && e.KeyCode <= Keys.Z =>
                $"\x1b{char.ToLowerInvariant((char)e.KeyCode)}",
            _ => e.Control && e.KeyCode >= Keys.A && e.KeyCode <= Keys.Z
                ? ((char)((int)e.KeyCode - (int)Keys.A + 1)).ToString()
                : "",
        };
        if (value.Length == 0) return;
        ClearTerminalSelection();
        InputRequested?.Invoke(value);
        e.SuppressKeyPress = true;
    }

    private void PasteTerminalClipboard()
    {
        try
        {
            var text = Clipboard.GetText(TextDataFormat.UnicodeText);
            if (text.Length == 0) return;
            // Keep pasted newlines and control characters out of the key stream.
            // The canonical TUI owns normalization, limits and the unsent holder.
            text = text.Replace("\x1b", "");
            if (text.Length == 0) return;
            ClearTerminalSelection();
            InputRequested?.Invoke($"\x1b[200~{text}\x1b[201~");
        }
        catch (ExternalException)
        {
            SetStatus("Windows clipboard is busy; try pasting again", isError: true);
        }
    }

    private bool HasTerminalSelection =>
        _selectionAnchor is { } anchor &&
        _selectionEnd is { } end &&
        anchor != end;

    private TerminalPoint TerminalPointAt(Point location)
    {
        var terminal = CalculateLayout().Terminal;
        var inset = Math.Max(Scale(2), PanelPadding / 2);
        var cell = TerminalCellSize();
        var visibleRows = Math.Max(1, Math.Max(1, terminal.Height - TerminalVerticalPadding * 2) / cell.Height);
        var firstRow = Math.Max(0, _screenRows - visibleRows);
        var row = (int)Math.Floor((location.Y - terminal.Y - TerminalVerticalPadding) / (double)cell.Height);
        var column = (int)Math.Floor(
            (location.X - terminal.X - inset) / (double)cell.Width + 0.5);
        var maxColumns = Math.Max(0, (terminal.Width - inset * 2) / cell.Width);
        return new TerminalPoint(
            firstRow + Math.Clamp(row, 0, visibleRows - 1),
            Math.Clamp(column, 0, maxColumns));
    }

    private bool TryOrderedSelection(out TerminalPoint start, out TerminalPoint end)
    {
        start = default;
        end = default;
        if (!HasTerminalSelection) return false;
        var anchor = _selectionAnchor!.Value;
        var current = _selectionEnd!.Value;
        if (anchor.Row < current.Row ||
            (anchor.Row == current.Row && anchor.Column <= current.Column))
        {
            start = anchor;
            end = current;
        }
        else
        {
            start = current;
            end = anchor;
        }
        return true;
    }

    private int TerminalRowColumns(int row)
    {
        return row >= 0 && row < _screen.Length
            ? _screen[row].Sum(fragment => Math.Max(0, fragment.Columns))
            : 0;
    }

    private string TerminalRowSlice(int row, int left, int right)
    {
        if (row < 0 || row >= _screen.Length || right <= left) return "";
        var selected = new StringBuilder();
        var column = 0;
        foreach (var fragment in _screen[row])
        {
            var width = Math.Max(0, fragment.Columns);
            var fragmentEnd = column + width;
            if (right <= column) break;
            if (width == 0) continue;
            if (left < fragmentEnd && right > column && fragment.Text.Length > 0)
            {
                var elements = StringInfo.ParseCombiningCharacters(fragment.Text);
                var startElement = Math.Clamp(
                    (int)Math.Floor(Math.Max(0, left - column) * elements.Length / (double)width),
                    0,
                    elements.Length);
                var endElement = Math.Clamp(
                    (int)Math.Ceiling(Math.Min(width, right - column) * elements.Length / (double)width),
                    startElement,
                    elements.Length);
                var startIndex = startElement < elements.Length
                    ? elements[startElement]
                    : fragment.Text.Length;
                var endIndex = endElement < elements.Length
                    ? elements[endElement]
                    : fragment.Text.Length;
                selected.Append(fragment.Text, startIndex, endIndex - startIndex);
            }
            column = fragmentEnd;
        }
        return selected.ToString();
    }

    private bool CopyTerminalSelection()
    {
        if (!TryOrderedSelection(out var start, out var end)) return false;
        var lines = new List<string>();
        for (var row = start.Row; row <= end.Row; row++)
        {
            var columns = TerminalRowColumns(row);
            var left = Math.Clamp(row == start.Row ? start.Column : 0, 0, columns);
            var right = Math.Clamp(row == end.Row ? end.Column : columns, 0, columns);
            lines.Add(TerminalRowSlice(row, left, right).TrimEnd());
        }
        var text = string.Join(Environment.NewLine, lines);
        if (text.Length == 0) return false;
        try
        {
            Clipboard.SetText(text);
            return true;
        }
        catch (ExternalException error)
        {
            SetStatus($"Selected text could not be copied: {error.Message}", isError: true);
            return true;
        }
    }

    private void ClearTerminalSelection()
    {
        if (_selectionAnchor is null && _selectionEnd is null) return;
        _selectionAnchor = null;
        _selectionEnd = null;
        _selecting = false;
        if (!_collapsed) Invalidate(CalculateLayout().Terminal);
    }

    private void ResetPointerState()
    {
        _draggingCube = false;
        _draggingTitleBar = false;
        _windowDragStarted = false;
        _resizing = false;
        _selecting = false;
        Capture = false;
    }

    private ShellLayout CalculateLayout()
    {
        var inset = Math.Max(Scale(1), PanelPadding / 2);
        var body = new Rectangle(
            inset,
            inset,
            Math.Max(1, Width - inset * 2),
            Math.Max(1, Height - inset * 2));
        var headerInset = ResizeGrip + TitleBarHeight;
        var content = new Rectangle(body.X, body.Y + headerInset, body.Width,
            Math.Max(1, body.Height - headerInset));
        if (!_attachmentActive)
        {
            return new ShellLayout(body, content, Rectangle.Empty, Rectangle.Empty);
        }
        var available = Math.Max(1, content.Width - DividerWidth);
        var terminalWidth = (int)Math.Round(available * _split);
        var minimum = MinimumPaneWidth;
        var attachmentMinimum = AttachmentMinimumPaneWidth;
        if (available >= minimum + attachmentMinimum)
        {
            terminalWidth = Math.Clamp(terminalWidth, minimum, available - attachmentMinimum);
        }
        else
        {
            terminalWidth = Math.Clamp(terminalWidth, 1, Math.Max(1, available - 1));
        }
        var terminal = new Rectangle(content.X, content.Y, terminalWidth, content.Height);
        var divider = new Rectangle(terminal.Right, content.Y, DividerWidth, content.Height);
        var attachment = new Rectangle(
            divider.Right,
            content.Y,
            Math.Max(1, content.Right - divider.Right),
            content.Height);
        return new ShellLayout(body, terminal, divider, attachment);
    }

    private Rectangle DividerHitBounds()
    {
        var divider = CalculateLayout().Divider;
        return divider.IsEmpty ? divider : Rectangle.Inflate(divider, Scale(4), 0);
    }

    private Rectangle TitleBarBounds()
    {
        var body = CalculateLayout().Body;
        return new Rectangle(body.Left + ResizeGrip, body.Top + ResizeGrip,
            Math.Max(1, body.Width - ResizeGrip * 2), TitleBarHeight);
    }

    private ControlLayout CalculateControls()
    {
        var buttons = new List<ControlButton>();
        var separators = new List<int>();
        var button = ControlHeight;
        var itemGap = ControlItemGap;
        var groupGap = ControlGroupGap;
        var labelWidth = ControlZoomLabelWidth;
        var barPadding = ControlBarPadding;
        var titleBar = TitleBarBounds();
        var bar = new Rectangle(titleBar.Right - ControlStripWidth, titleBar.Top,
            ControlStripWidth, titleBar.Height);
        var buttonTop = bar.Top + (bar.Height - button) / 2;
        var x = bar.X + barPadding;
        buttons.Add(new ControlButton(ControlAction.Attach, new Rectangle(x, buttonTop, button, button)));
        x += button + itemGap;
        if (_attachmentActive)
        {
            buttons.Add(new ControlButton(ControlAction.Detach, new Rectangle(x, buttonTop, button, button)));
            x += button + itemGap;
        }
        buttons.Add(new ControlButton(ControlAction.Mode, new Rectangle(x, buttonTop, button, button)));
        x += button + groupGap;
        separators.Add(x - groupGap / 2);
        buttons.Add(new ControlButton(ControlAction.ZoomOut, new Rectangle(x, buttonTop, button, button)));
        x += button + itemGap;
        var zoomLabel = new Rectangle(x, buttonTop, labelWidth, button);
        x += labelWidth + itemGap;
        buttons.Add(new ControlButton(ControlAction.ZoomIn, new Rectangle(x, buttonTop, button, button)));
        x += button + groupGap;
        separators.Add(x - groupGap / 2);
        buttons.Add(new ControlButton(ControlAction.Maximize, new Rectangle(x, buttonTop, button, button)));
        x += button + itemGap;
        buttons.Add(new ControlButton(ControlAction.Collapse, new Rectangle(x, buttonTop, button, button)));
        x += button + itemGap;
        buttons.Add(new ControlButton(ControlAction.Close, new Rectangle(x, buttonTop, button, button)));
        return new ControlLayout(bar, buttons, zoomLabel, separators);
    }

    private Rectangle CubeBounds()
    {
        var edge = CubeEdge;
        if (!_collapsed)
        {
            var titleBar = TitleBarBounds();
            return new Rectangle(titleBar.Left + ControlBarPadding,
                titleBar.Top + (titleBar.Height - edge) / 2, CubePairWidth, edge);
        }
        return CompactPairBounds();
    }

    private int CubeEdge => Math.Max(
        Scale(9),
        (int)Math.Round(_theme.CubeSize * _theme.CubeEdgeRatio));
    private int CubePairWidth => CubeEdge * 2 + Math.Max(Scale(3), CubeEdge / 3);

    private Rectangle IdentityLabelBounds()
    {
        var cubes = HasCompactGroup ? GroupMemberBounds(_groupHover) : CubeBounds();
        var textWidth = TextRenderer.MeasureText(
            IdentityTitle(),
            _identityFont,
            Size.Empty,
            TextFormatFlags.NoPadding | TextFormatFlags.NoPrefix | TextFormatFlags.SingleLine).Width;
        var width = Math.Clamp(
            textWidth + ButtonPadding * 2,
            cubes.Height,
            Scale(320));
        var height = Math.Max(cubes.Height, (int)Math.Ceiling(_identityFont.GetHeight()) + Scale(2));
        return new Rectangle(
            (HasCompactGroup ? GroupBounds.Right : cubes.Right) + IdentityMargin,
            cubes.Y + (cubes.Height - height) / 2,
            width,
            height);
    }

    private Rectangle InsetAttachment(Rectangle attachment)
    {
        if (attachment.IsEmpty) return Rectangle.Empty;
        var dividerFrame = Math.Max(1, Scale(1));
        return Rectangle.FromLTRB(
            attachment.Left + dividerFrame,
            attachment.Top,
            attachment.Right - ResizeGrip,
            attachment.Bottom - ResizeGrip);
    }

    private IReadOnlyList<Rectangle> CubeRectangles()
    {
        var area = CubeBounds();
        var edge = area.Height;
        var gap = Math.Max(0, area.Width - edge * 2);
        var cubes = new Rectangle[2];
        for (var index = 0; index < cubes.Length; index++)
        {
            var offset = CubeDropOffset(index, edge);
            cubes[index] = new Rectangle(
                area.X + index * (edge + gap) + offset,
                area.Y,
                edge,
                edge);
        }
        return cubes;
    }

    private Rectangle DragCatchBounds()
    {
        var area = CubeBounds();
        var inset = Math.Max(1, area.Height / 3);
        return Rectangle.FromLTRB(
            area.Left,
            area.Top + inset,
            area.Right,
            Math.Max(area.Top + inset + 1, area.Bottom - inset));
    }

    private int CubeDropOffset(int index, int edge)
    {
        var side = index == 0 ? -1f : 1f;
        var offset = side * _dragMouth * edge * 0.22f;
        if (_applicationDrag)
        {
            var phase = index * 0.7;
            offset += 0.10f * edge * (float)Math.Sin(WorkingClock() * 20 + phase);
        }
        if (_dropReactionStarted != 0)
        {
            offset += -side * 0.55f * edge * DropPull();
        }
        return (int)Math.Round(offset);
    }

    private float CubeDropBrightness()
    {
        var brightness = 0.12f * _dragMouth;
        return _dropReactionStarted == 0
            ? brightness
            : brightness + 0.5f * Math.Max(0, DropPull());
    }

    private float DropPull()
    {
        var progress = Math.Clamp(DropElapsed() / DropReactionSeconds, 0, 1);
        var pull = progress < 0.35
            ? Math.Pow(progress / 0.35, 2)
            : (1 - (progress - 0.35) / 0.65) *
                Math.Cos((progress - 0.35) / 0.65 * 9) *
                Math.Exp(-(progress - 0.35) / 0.65 * 3.5);
        return (float)pull;
    }

    private static double WorkingClock() =>
        Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency;

    private Rectangle PickerBounds()
    {
        var body = CalculateLayout().Body;
        var width = Math.Min(Scale(520), Math.Max(Scale(280), body.Width - PanelPadding * 2));
        var height = Math.Min(
            Scale(420),
            Math.Max(HeaderHeight + CandidateRowHeight * 2, body.Height - PanelPadding * 2));
        return new Rectangle(
            body.X + Math.Max(0, (body.Width - width) / 2),
            body.Y + PanelPadding,
            width,
            Math.Min(height, Math.Max(1, body.Bottom - body.Y - PanelPadding * 2)));
    }

    private Rectangle PickerCloseBounds()
    {
        var picker = PickerBounds();
        return new Rectangle(
            picker.Right - ControlHeight - ButtonPadding,
            picker.Y + ButtonPadding,
            ControlHeight,
            ControlHeight);
    }

    private Rectangle CandidateBounds(int visibleRow)
    {
        var picker = PickerBounds();
        return new Rectangle(
            picker.X + ButtonPadding,
            picker.Y + HeaderHeight + visibleRow * CandidateRowHeight,
            picker.Width - ButtonPadding * 2,
            CandidateRowHeight);
    }

    private int UtilityStart() =>
        _candidates.TakeWhile(candidate => !candidate.IsUtilityWindow).Count();

    private int PickerEntryCount() =>
        _candidates.Count + (_candidates.Any(candidate => candidate.IsUtilityWindow) ? 1 : 0);

    private bool IsUtilityHeader(int entry) =>
        _candidates.Any(candidate => candidate.IsUtilityWindow) && entry == UtilityStart();

    private WindowCandidate? CandidateAt(int entry)
    {
        if (entry < 0 || entry >= PickerEntryCount() || IsUtilityHeader(entry)) return null;
        var candidateIndex = entry > UtilityStart() &&
            _candidates.Any(candidate => candidate.IsUtilityWindow)
            ? entry - 1
            : entry;
        return candidateIndex >= 0 && candidateIndex < _candidates.Count
            ? _candidates[candidateIndex]
            : null;
    }

    private int VisiblePickerRows() =>
        Math.Max(1, (PickerBounds().Height - HeaderHeight - ButtonPadding) / CandidateRowHeight);

    private int PanelPadding => Math.Max(Scale(1), _theme.PanelPadding);
    private int PanelCornerRadius => Math.Max(0, _theme.PanelCornerRadius);
    private int ButtonPadding => Math.Max(Scale(1), _theme.ButtonPadding);
    private int ButtonCornerRadius => Math.Max(0, _theme.ButtonCornerRadius);
    private int IdentityMargin => Math.Max(Scale(4), ButtonPadding / 2);
    private int IconSize => Scale(_theme.ChromeIcon);
    private int PickerIconSize => Math.Max(Scale(16), _bodyFont.Height + ButtonPadding / 2);
    private int ControlHeight => Scale(_theme.ChromeButton);
    private int TopInset => Math.Max(Scale(4), PanelPadding / 2);
    private int ControlItemGap => Scale(_theme.ChromeGap);
    private int ControlGroupGap => Math.Max(Scale(3), PanelPadding / 2);
    private int ControlBarPadding => Math.Max(Scale(2), ButtonPadding / 2);
    private int ControlZoomLabelWidth => TextRenderer.MeasureText(
        "100%", _bodyFont, Size.Empty, TextFormatFlags.NoPadding).Width + ButtonPadding * 2;
    private int ControlStripWidth
    {
        get
        {
            var count = _attachmentActive ? 3 : 2;
            return (count + 5) * ControlHeight + (count + 3) * ControlItemGap +
                ControlZoomLabelWidth + ControlGroupGap * 2 + ControlBarPadding * 2;
        }
    }
    private int DividerWidth => Scale(6);
    private int ResizeGrip => Scale(8);
    private int MinimumPaneWidth => Math.Max(Scale(220),
        CubePairWidth + ControlBarPadding * 2 + ResizeGrip * 2);
    private int TitleBarHeight => Math.Max(ControlHeight, CubeEdge) + ControlBarPadding * 2;
    private int TerminalVerticalPadding => Math.Min(Math.Max(Scale(1), PanelPadding / 2), Scale(2));
    private int AttachmentMinimumPaneWidth => Math.Max(
        Scale(220),
        _attachmentMinimumContentSize.Width + Math.Max(1, Scale(1)) + ResizeGrip);
    private int CandidateRowHeight => Math.Max(Scale(30), _bodyFont.Height + ButtonPadding * 2);
    private int HeaderHeight => Math.Max(ControlHeight + ButtonPadding * 2, _titleFont.Height + PanelPadding);

    private int Scale(int logical) => Math.Max(1, (int)Math.Round(logical * DeviceDpi / 96f));

    private static Rectangle CenteredSquare(Rectangle bounds, int size)
    {
        var edge = Math.Min(size, Math.Min(bounds.Width, bounds.Height));
        return new Rectangle(
            bounds.X + (bounds.Width - edge) / 2,
            bounds.Y + (bounds.Height - edge) / 2,
            edge,
            edge);
    }

    private Font CreateFont(ThemeFont spec, FontStyle style)
    {
        try
        {
            return new Font(spec.Family, spec.Size, style, GraphicsUnit.Point);
        }
        catch (ArgumentException)
        {
            return new Font(DefaultFont.FontFamily, spec.Size, style, GraphicsUnit.Point);
        }
    }

    private void RebuildTerminalFonts(ThemeFont spec)
    {
        foreach (var font in _terminalFonts.Values) font.Dispose();
        _terminalFonts.Clear();
        foreach (var font in _emojiFonts.Values) font.Dispose();
        _emojiFonts.Clear();
        var scaled = new ThemeFont(spec.Family, Math.Max(1, spec.Size * _terminalScale));
        _terminalFonts[FontStyle.Regular] = CreateFont(scaled, FontStyle.Regular);
        _emojiFonts[FontStyle.Regular] = CreateFont(
            new ThemeFont("Segoe UI Emoji", scaled.Size),
            FontStyle.Regular);
    }

    private Font TerminalFont(FontStyle style)
    {
        if (_terminalFonts.TryGetValue(style, out var font)) return font;
        var regular = _terminalFonts[FontStyle.Regular];
        try
        {
            font = new Font(regular.FontFamily, regular.Size, style, GraphicsUnit.Point);
        }
        catch (ArgumentException)
        {
            font = new Font(regular.FontFamily, regular.Size, FontStyle.Regular, GraphicsUnit.Point);
        }
        _terminalFonts[style] = font;
        return font;
    }

    private Font EmojiFont(FontStyle style)
    {
        if (_emojiFonts.TryGetValue(style, out var font)) return font;
        var regular = _emojiFonts[FontStyle.Regular];
        try
        {
            font = new Font(regular.FontFamily, regular.Size, style, GraphicsUnit.Point);
        }
        catch (ArgumentException)
        {
            font = new Font(regular.FontFamily, regular.Size, FontStyle.Regular, GraphicsUnit.Point);
        }
        _emojiFonts[style] = font;
        return font;
    }

    private Size TerminalCellSize()
    {
        var measured = TextRenderer.MeasureText(
            "M",
            TerminalFont(FontStyle.Regular),
            Size.Empty,
            TerminalTextFlags);
        return new Size(Math.Max(1, measured.Width), Math.Max(1, measured.Height));
    }

    private static void ReplaceFont(ref Font target, Font replacement)
    {
        var previous = target;
        target = replacement;
        previous.Dispose();
    }

    private static int Distance(Point first, Point second) =>
        Math.Max(Math.Abs(first.X - second.X), Math.Abs(first.Y - second.Y));

    private static Cursor ResizeCursor(int hit) => hit switch
    {
        10 or 11 => Cursors.SizeWE,
        12 or 15 => Cursors.SizeNS,
        13 or 17 => Cursors.SizeNWSE,
        14 or 16 => Cursors.SizeNESW,
        _ => Cursors.Default,
    };

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    private static extern nint SendMessage(nint handle, uint message, nint wParam, nint lParam);

    private static Color Pick(Color value, Color fallback) => value.IsEmpty ? fallback : value;

    internal static Color Blend(Color foreground, Color background, float amount)
    {
        amount = Math.Clamp(amount, 0, 1);
        return Color.FromArgb(
            (int)Math.Round(background.R + (foreground.R - background.R) * amount),
            (int)Math.Round(background.G + (foreground.G - background.G) * amount),
            (int)Math.Round(background.B + (foreground.B - background.B) * amount));
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _bodyFont.Dispose();
            _titleFont.Dispose();
            _identityFont.Dispose();
            _toolTip.Dispose();
            _identityAnimation.Dispose();
            foreach (var font in _terminalFonts.Values) font.Dispose();
            _terminalFonts.Clear();
            foreach (var font in _emojiFonts.Values) font.Dispose();
            _emojiFonts.Clear();
            _attachmentIcon?.Dispose();
            foreach (var candidate in _candidates) candidate.Dispose();
        }
        base.Dispose(disposing);
    }

    private enum ControlAction
    {
        Attach,
        Detach,
        Mode,
        ZoomOut,
        ZoomIn,
        Maximize,
        Collapse,
        Close,
    }

    private sealed record ControlButton(ControlAction Action, Rectangle Bounds);
    private sealed record ControlLayout(
        Rectangle Bar,
        IReadOnlyList<ControlButton> Buttons,
        Rectangle ZoomLabel,
        IReadOnlyList<int> Separators);
    private readonly record struct ShellLayout(
        Rectangle Body,
        Rectangle Terminal,
        Rectangle Divider,
        Rectangle Attachment);
}

internal static class GraphicsExtensions
{
    public static void FillRoundedRectangle(
        this Graphics graphics,
        Brush brush,
        Rectangle rectangle,
        int radius)
    {
        using var path = RoundedPath(rectangle, radius);
        graphics.FillPath(brush, path);
    }

    public static void DrawRoundedRectangle(
        this Graphics graphics,
        Pen pen,
        Rectangle rectangle,
        int radius)
    {
        using var path = RoundedPath(rectangle, radius);
        graphics.DrawPath(pen, path);
    }

    public static GraphicsPath RoundedPath(Rectangle rectangle, int radius)
    {
        var path = new GraphicsPath();
        if (rectangle.Width <= 1 || rectangle.Height <= 1 || radius <= 0)
        {
            path.AddRectangle(rectangle);
            return path;
        }
        var diameter = Math.Max(1, Math.Min(radius * 2, Math.Min(rectangle.Width, rectangle.Height)));
        path.AddArc(rectangle.Left, rectangle.Top, diameter, diameter, 180, 90);
        path.AddArc(rectangle.Right - diameter, rectangle.Top, diameter, diameter, 270, 90);
        path.AddArc(rectangle.Right - diameter, rectangle.Bottom - diameter, diameter, diameter, 0, 90);
        path.AddArc(rectangle.Left, rectangle.Bottom - diameter, diameter, diameter, 90, 90);
        path.CloseFigure();
        return path;
    }
}
