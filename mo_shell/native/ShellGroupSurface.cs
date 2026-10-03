using System.Diagnostics;
using System.Drawing.Drawing2D;

namespace MoShell.Native;

internal sealed record ShellGroupMember(nint Handle, int Ordinal, Color Color, bool Working, bool Attached)
{
    public string Title { get; init; } = "";
}

// The existing surface paints grouped identities using the same pair painter,
// skin, input/accessibility bounds and finite animation clock as a lone Shell.
internal sealed partial class ShellSurface
{
    private ShellGroupMember[] _group = Array.Empty<ShellGroupMember>();
    private readonly Dictionary<nint, Rectangle> _groupStarts = new();
    private int _instanceOrdinal = 1, _groupSelection;
    private long _groupMotionStarted, _groupIdleStarted, _groupReturnStarted, _groupPressStarted;
    private double _groupIdleStoppedAt;
    private nint _groupPressed, _groupExtracted;
    private nint _groupHover;
    private bool _groupPointerDown, _groupMoved;
    private Point _groupCursorDown, _groupFormDown;

    public event Action<nint>? GroupOpenRequested;
    public event Action<nint, Point, bool>? GroupExtractRequested;
    public event Action? CompactDragCompleted;
    public bool Working => _working;
    public bool GroupWorking => _group.Any(member => member.Working);
    public bool GroupTouched => _groupPointerDown || HasCompactGroup && ClientRectangle.Contains(PointToClient(Cursor.Position));
    public bool HasCompactGroup => _collapsed && _group.Length > 1;
    private int GroupGap => Math.Max(Scale(8), ButtonPadding);
    private int GroupRowHeight => CubeEdge + GroupGap;
    private Size GroupCompactSize => new(
        CubePairWidth + GroupGap * 2, _group.Length * GroupRowHeight + GroupGap);
    private Rectangle GroupBounds => new(Point.Empty, GroupCompactSize);
    public Size PairSize => new(CubePairWidth, CubeEdge);
    private static double Since(long timestamp) => timestamp == 0 ? double.PositiveInfinity :
        (Stopwatch.GetTimestamp() - timestamp) / (double)Stopwatch.Frequency;
    internal static float Ease(float progress) => 1 - MathF.Pow(1 - Math.Clamp(progress, 0, 1), 3);
    private bool GroupNeedsFrames => HasCompactGroup && (GroupWorking || Since(_groupMotionStarted) < .24 ||
        _groupIdleStarted != 0 || Since(_groupReturnStarted) < .14 || _groupPointerDown);

    public Color InstanceColor
    {
        get
        {
            var basis = Pick(_theme.CubeColor, Pick(_theme.Brand, SystemColors.Highlight));
            if (_instanceOrdinal <= 1) return basis;
            // Tint/shade the skin's color, preserving its hue and the first Shell.
            var variant = _instanceOrdinal - 2;
            var lighten = (variant % 2 == 0) == (basis.GetBrightness() < .7f);
            var amount = .30f + .13f * (variant / 2 % 3);
            return Blend(basis, lighten ? Color.White : Color.Black, 1 - amount);
        }
    }

    public void SetInstanceOrdinal(int ordinal) { _instanceOrdinal = Math.Max(1, ordinal); Invalidate(); }

    public bool SetGroupMembers(ShellGroupMember[] members, IReadOnlyDictionary<nint, Rectangle>? entering = null)
    {
        if (_group.SequenceEqual(members)) return false;
        var geometryChanged = !_group.Select(m => m.Handle).SequenceEqual(members.Select(m => m.Handle));
        if (geometryChanged)
        {
            var starts = _group.ToDictionary(member => member.Handle, AnimatedMemberBounds);
            _groupStarts.Clear();
            foreach (var (handle, bounds) in starts) _groupStarts[handle] = bounds;
            if (entering is not null)
                foreach (var (handle, bounds) in entering)
                    _groupStarts.TryAdd(handle, bounds);
            _groupMotionStarted = Stopwatch.GetTimestamp();
            StopGroupIdleMotion();
        }
        _group = members;
        _groupSelection = Math.Clamp(_groupSelection, 0, Math.Max(0, members.Length - 1));
        if (geometryChanged)
        {
            _identityHovered = false;
            _identityLabelOpacity = 0;
        }
        UpdateIdentityAnimation();
        Invalidate();
        if (geometryChanged) AccessibilityNotifyClients(AccessibleEvents.Reorder, -1);
        return geometryChanged;
    }

    public Rectangle GroupMemberBounds(nint handle)
    {
        var index = Array.FindIndex(_group, member => member.Handle == handle);
        return CompactPairBounds(Math.Max(0, index));
    }

    public Rectangle CompactPairBounds(int groupIndex = -1) => groupIndex >= 0
        ? new Rectangle(GroupGap, GroupGap + groupIndex * GroupRowHeight, CubePairWidth, CubeEdge)
        : new Rectangle((SingleCompactSize.Width - CubePairWidth) / 2,
            TopInset + Math.Max(0, (ControlHeight - CubeEdge) / 2), CubePairWidth, CubeEdge);

    public PointF MemberCenter(nint handle)
    {
        var member = _group.FirstOrDefault(item => item.Handle == handle);
        var bounds = member is null ? CompactPairBounds() : AnimatedMemberBounds(member);
        return new PointF(bounds.Left + bounds.Width / 2f, bounds.Top + bounds.Height / 2f);
    }

    private Rectangle AnimatedMemberBounds(ShellGroupMember member)
    {
        var target = GroupMemberBounds(member.Handle);
        var start = _groupStarts.GetValueOrDefault(member.Handle,
            new Rectangle(target.X, target.Y - GroupRowHeight / 2, target.Width, target.Height));
        var amount = Ease((float)(Since(_groupMotionStarted) / .24));
        target.X = (int)Math.Round(start.X + (target.X - start.X) * amount);
        target.Y = (int)Math.Round(start.Y + (target.Y - start.Y) * amount);
        if (_groupPointerDown && _groupPressed == member.Handle && !_groupMoved && Since(_groupPressStarted) > .35)
            target.X += (int)Math.Round(Math.Sin(Since(_groupPressStarted) * 30) * Scale(2));
        return target;
    }

    private IReadOnlyList<Rectangle> PairAt(Rectangle area) => new[] {
        new Rectangle(area.Left, area.Top, CubeEdge, CubeEdge),
        new Rectangle(area.Right - CubeEdge, area.Top, CubeEdge, CubeEdge),
    };

    private void DrawCompactGroup(Graphics graphics)
    {
        var poses = _group.Select(AnimatedMemberBounds).ToArray();
        for (var index = 1; index < _group.Length; index++)
        {
            var upper = poses[index - 1];
            var lower = poses[index];
            var energy = Math.Max(GroupIdleEnergy(_group[index - 1].Handle), GroupIdleEnergy(_group[index].Handle));
            var color = Blend(_group[index].Color, _group[index - 1].Color, .5f);
            DrawElectricConnection(graphics,
                new PointF(upper.Left + upper.Width / 2f, upper.Top + upper.Height / 2f),
                new PointF(lower.Left + lower.Width / 2f, lower.Top + lower.Height / 2f),
                color, energy, Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency);
        }
        for (var index = 0; index < _group.Length; index++)
        {
            var member = _group[index];
            var rectangle = poses[index];
            var energy = GroupIdleEnergy(member.Handle);
            var cubes = PairAt(rectangle);
            DrawElectricConnection(graphics,
                new PointF(cubes[0].Right - 1, rectangle.Top + rectangle.Height / 2f),
                new PointF(cubes[1].Left + 1, rectangle.Top + rectangle.Height / 2f),
                member.Color, energy, Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency);
            foreach (var cube in cubes)
            {
                var aura = Rectangle.Inflate(cube, GroupGap, GroupGap);
                using var path = GraphicsExtensions.RoundedPath(aura, aura.Height / 2);
                using var glow = new PathGradientBrush(path)
                {
                    CenterColor = Color.FromArgb((int)(48 + 155 * energy), member.Color),
                    SurroundColors = new[] { Color.FromArgb(0, member.Color) },
                };
                graphics.FillPath(glow, path);
            }
            DrawCubePair(graphics, cubes, member.Color, member.Working,
                member.Attached, rectangle.Contains(PointToClient(Cursor.Position)),
                Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency, energy);
        }
        DrawIdentityLabel(graphics);
    }

    internal static PointF[] ElectricFilament(PointF start, PointF end, float energy, double seconds, float scale)
    {
        var dx = end.X - start.X;
        var dy = end.Y - start.Y;
        var length = Math.Max(.01f, MathF.Sqrt(dx * dx + dy * dy));
        var amplitude = Math.Min(length * .08f, 2 * scale);
        var points = new PointF[25];
        for (var index = 0; index < points.Length; index++)
        {
            var t = index / (float)(points.Length - 1);
            var wave = MathF.Sin(t * MathF.PI * 2) * .5f + energy * MathF.Sin(t * MathF.PI) *
                (MathF.Sin(t * MathF.PI * 6 - (float)(seconds * 18 % (Math.PI * 2))) +
                 .3f * MathF.Sin(t * MathF.PI * 14 + (float)(seconds * 29 % (Math.PI * 2))));
            var bend = wave * amplitude;
            points[index] = new PointF(start.X + dx * t - dy / length * bend,
                start.Y + dy * t + dx / length * bend);
        }
        points[0] = start;
        points[^1] = end;
        return points;
    }

    public void DrawElectricConnection(Graphics graphics, PointF start, PointF end,
        Color color, float energy, double seconds, float opacity = 1)
    {
        if (start == end || opacity <= 0) return;
        using var path = new GraphicsPath();
        path.AddCurve(ElectricFilament(start, end, energy, seconds, Scale(1)), .35f);
        // The same filament, light and phase continue from group to release.
        // Endpoints stay attached; only a finite charge moves through the wire.
        foreach (var (width, alpha) in new[] { (Scale(7), 30 + 50 * energy),
                    (Scale(4), 55 + 90 * energy), (Scale(2), 190 + 65 * energy) })
        {
            using var pen = new Pen(Color.FromArgb((int)(alpha * opacity), color), width)
                { StartCap = LineCap.Round, EndCap = LineCap.Round };
            graphics.DrawPath(pen, path);
        }
        if (energy > .05f)
        {
            var light = Blend(Pick(_theme.Text, Color.White), color, .7f);
            var progress = .15f + .70f * (float)(seconds * 2 % 1);
            using var charge = new LinearGradientBrush(start, end, color, color)
            {
                InterpolationColors = new ColorBlend
                {
                    Positions = new[] { 0f, progress - .14f, progress, progress + .14f, 1f },
                    Colors = new[] { Color.FromArgb(0, light), Color.FromArgb(0, light),
                        Color.FromArgb((int)(230 * energy * opacity), light),
                        Color.FromArgb(0, light), Color.FromArgb(0, light) },
                },
            };
            using var core = new Pen(charge, Scale(1))
                { StartCap = LineCap.Round, EndCap = LineCap.Round };
            graphics.DrawPath(core, path);
        }
    }

    private double GroupIdleDuration => 1.05 + Math.Max(0, _group.Length - 1) * .10;

    private float GroupIdleEnergy(nint handle)
    {
        var returning = _groupIdleStarted == 0;
        if (returning && Since(_groupReturnStarted) >= .14) return 0;
        var seconds = returning ? _groupIdleStoppedAt : Since(_groupIdleStarted);
        var index = Array.FindIndex(_group, member => member.Handle == handle);
        var local = seconds - .10 - Math.Max(0, index) * .10;
        static float Charge(double time) => time < 0 || time >= .6 ? 0 :
            time < .10 ? Ease((float)(time / .10)) : 1 - Ease((float)((time - .10) / .50));
        var energy = Math.Min(1, Charge(local) + .45f * Charge(local - .32));
        return energy * (returning ? 1 - Ease((float)(Since(_groupReturnStarted) / .14)) : 1);
    }

    private void TickGroupAnimation()
    {
        if (!HasCompactGroup) return;
        if (GroupTouched || GroupWorking || Since(_groupIdleStarted) >= GroupIdleDuration)
            StopGroupIdleMotion();
        // Paint the final resting pose before the shared clock stops.
        Invalidate();
    }

    public void StartGroupIdleMotion()
    {
        if (!HasCompactGroup || GroupWorking || GroupTouched) return;
        _groupIdleStarted = Stopwatch.GetTimestamp();
        _groupReturnStarted = 0;
        UpdateIdentityAnimation();
    }

    public void StopGroupIdleMotion()
    {
        if (_groupIdleStarted == 0) return;
        _groupIdleStoppedAt = Since(_groupIdleStarted);
        _groupIdleStarted = 0;
        _groupReturnStarted = Stopwatch.GetTimestamp();
        UpdateIdentityAnimation();
        Invalidate();
    }

    private bool GroupMouseDown(MouseEventArgs e)
    {
        if (!HasCompactGroup) return false;
        StopGroupIdleMotion();
        Focus();
        _groupPointerDown = true;
        _groupMoved = false;
        _groupExtracted = 0;
        _groupPressed = _group.FirstOrDefault(m => GroupMemberBounds(m.Handle).Contains(e.Location))?.Handle ?? 0;
        _groupPressStarted = Stopwatch.GetTimestamp();
        _groupCursorDown = PointToScreen(e.Location);
        _groupFormDown = FindForm()?.Location ?? Point.Empty;
        Capture = true;
        UpdateIdentityAnimation();
        return true;
    }

    private bool GroupMouseMove(MouseEventArgs e)
    {
        if (!HasCompactGroup && !_groupPointerDown) return false;
        StopGroupIdleMotion();
        if (HasCompactGroup)
        {
            var member = _group.FirstOrDefault(m => GroupMemberBounds(m.Handle).Contains(e.Location));
            var hovered = member is not null;
            var changedMember = member is not null && _groupHover != member.Handle;
            if (member is not null) _groupHover = member.Handle;
            if (_identityHovered != hovered || changedMember)
            {
                _identityHovered = hovered;
                UpdateIdentityAnimation();
                CompactPresentationChanged?.Invoke();
            }
        }
        Cursor = Cursors.SizeAll;
        if (!_groupPointerDown || (e.Button & MouseButtons.Left) == 0) { Invalidate(); return true; }
        var cursor = PointToScreen(e.Location);
        if (_groupExtracted != 0)
        {
            GroupExtractRequested?.Invoke(_groupExtracted, cursor, false);
            return true;
        }
        if (Distance(cursor, _groupCursorDown) < Scale(6) && !_groupMoved) return true;
        if (!_groupMoved && _groupPressed != 0 && Since(_groupPressStarted) >= .45)
        {
            // The thread that received button-down owns capture through release.
            // Windows cannot transfer an in-progress gesture to another process.
            _groupExtracted = _groupPressed;
            GroupExtractRequested?.Invoke(_groupExtracted, cursor, false);
        }
        else if (FindForm() is Form form)
        {
            _groupMoved = true;
            form.Location = new Point(_groupFormDown.X + cursor.X - _groupCursorDown.X,
                _groupFormDown.Y + cursor.Y - _groupCursorDown.Y);
        }
        return true;
    }

    private bool GroupMouseUp(MouseEventArgs e)
    {
        if (!_groupPointerDown) return false;
        _groupPointerDown = false;
        var extracted = _groupExtracted;
        _groupExtracted = 0;
        Capture = false;
        if (extracted != 0) GroupExtractRequested?.Invoke(extracted, PointToScreen(e.Location), true);
        else if (_groupMoved) CompactDragCompleted?.Invoke();
        else if (_groupPressed != 0) GroupOpenRequested?.Invoke(_groupPressed);
        _groupPressed = 0;
        UpdateIdentityAnimation();
        return true;
    }

    private void GroupCaptureChanged()
    {
        if (Capture || !_groupPointerDown) return;
        _groupPointerDown = false;
        _groupExtracted = 0;
        _groupPressed = 0;
        UpdateIdentityAnimation();
    }

    private bool GroupKey(Keys keyData)
    {
        if (!HasCompactGroup) return false;
        StopGroupIdleMotion();
        var key = keyData & Keys.KeyCode;
        if (key is Keys.Enter or Keys.Space) { GroupOpenRequested?.Invoke(_group[_groupSelection].Handle); return true; }
        if (key is not (Keys.Up or Keys.Down or Keys.Tab)) return false;
        var backwards = key == Keys.Up || keyData.HasFlag(Keys.Shift);
        _groupSelection = (_groupSelection + (backwards ? -1 : 1) + _group.Length) % _group.Length;
        Invalidate();
        NotifyKeyboardFocus();
        return true;
    }
}
