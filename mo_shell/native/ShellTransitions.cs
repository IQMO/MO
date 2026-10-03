using System.Diagnostics;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace MoShell.Native;

// Pixels only. Terminal layout, attachment ownership and extraction commit once
// through their existing owners. This passive layer cannot receive mouse input.
internal sealed partial class ShellForm
{
    private sealed class VisualLayer : Form
    {
        protected override bool ShowWithoutActivation => true;
        protected override CreateParams CreateParams
        {
            get
            {
                var value = base.CreateParams;
                value.ExStyle |= LayeredStyle | 0x20 | 0x80 | 0x08000000;
                return value;
            }
        }
        public VisualLayer()
        {
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            TopMost = true;
        }
    }

    private VisualLayer? _visualLayer;
    private Bitmap? _foldBefore, _foldAfter;
    private Rectangle _foldStart, _foldEnd;
    private Rectangle _foldSourceIdentity, _foldTargetIdentity, _foldBody;
    private long _foldStarted, _connectionStarted;
    private nint _connectionMember, _connectionPeer;
    private nint[] _connectionMembers = Array.Empty<nint>();
    private float _connectionLength;
    private Color _connectionColor;
    private const double FoldSeconds = .18, ConnectionSeconds = .55;
    private static double MotionSeconds(long start) =>
        (Stopwatch.GetTimestamp() - start) / (double)Stopwatch.Frequency;

    private void PresentVisual(Bitmap frame, Point location)
    {
        _visualLayer ??= new VisualLayer();
        _visualLayer.Location = location;
        PresentAlphaFrame(_visualLayer, frame);
        ShowWindow(_visualLayer.Handle, 4);
    }

    private void StartFold(Bitmap before, Rectangle previous, Rectangle sourceIdentity, Rectangle previousBody)
    {
        _foldBefore = before;
        _foldAfter = _surface.RenderPresentationFrame();
        _foldStart = previous;
        _foldEnd = Bounds;
        _foldSourceIdentity = sourceIdentity;
        _foldTargetIdentity = _surface.IdentityBounds;
        _foldBody = _collapsed ? previousBody : _surface.BodyBounds;
        _foldStarted = Stopwatch.GetTimestamp();
        _surface.SetVisualMotion(true);
        TickVisualMotion();
    }

    private void FinishFold()
    {
        if (_foldStarted == 0) return;
        _foldStarted = 0;
        _foldBefore?.Dispose(); _foldBefore = null;
        _foldAfter?.Dispose(); _foldAfter = null;
        _visualLayer?.Hide();
        Opacity = 1;
        QueueCompactFrame();
        FinishCollapsedPresentation();
        _surface.SetVisualMotion(_connectionStarted != 0);
    }

    private void FinishCollapsedPresentation()
    {
        if (!_collapsed)
        {
            _attachment.Show();
            _ = SyncAttachment();
        }
        SendWindowEffect(show: true);
        PublishGroupState();
        if (_collapsed && _returnGroup != 0) RequestGroupJoin(_returnGroup);
    }

    internal static Rectangle FoldBounds(Rectangle start, Rectangle end, float progress)
    {
        var amount = ShellSurface.Ease(progress);
        return new Rectangle(
            (int)Math.Round(start.X + (end.X-start.X)*amount),
            (int)Math.Round(start.Y + (end.Y-start.Y)*amount),
            Math.Max(1, (int)Math.Round(start.Width + (end.Width-start.Width)*amount)),
            Math.Max(1, (int)Math.Round(start.Height + (end.Height-start.Height)*amount)));
    }

    private static float SmoothStep(float value)
    {
        var t = Math.Clamp(value, 0, 1);
        return t * t * (3 - 2 * t);
    }

    internal static float ConnectionOpacity(double distance, double elapsed) =>
        (1 - SmoothStep((float)(distance / 480))) *
        (1 - SmoothStep((float)((elapsed - .08) / (ConnectionSeconds - .08))));

    private void StartConnectionRelease(nint member)
    {
        var origin = _surface.MemberCenter(member);
        _connectionPeer = _groupMembers.Where(handle => handle != member)
            .MinBy(handle => Math.Abs(_surface.MemberCenter(handle).Y - origin.Y));
        if (_connectionPeer == 0) return;
        _connectionMembers = _groupMembers.ToArray();
        _connectionLength = Math.Abs(_surface.MemberCenter(_connectionPeer).Y - origin.Y);
        _connectionMember = member;
        _connectionColor = ShellSurface.Blend(
            Color.FromArgb(unchecked((int)GetProp(member, ColorProperty))),
            Color.FromArgb(unchecked((int)GetProp(_connectionPeer, ColorProperty))), .5f);
        _connectionStarted = Stopwatch.GetTimestamp();
        _surface.SetVisualMotion(true);
    }

    private PointF? ConnectionMemberCenter(nint member)
    {
        var leader = GetProp(member, GroupProperty);
        if (leader == 0 || GetProp(member, CollapsedProperty) == 0 || !GetWindowRect(leader, out var rect))
            return null;
        if (leader == Handle)
        {
            var point = _surface.MemberCenter(member);
            return new PointF(Left + point.X, Top + point.Y);
        }
        var peers = _connectionMembers.Where(h => GetProp(h, GroupProperty) == leader)
            .OrderByDescending(h => (int)GetProp(h, PeerProperty)).ToArray();
        var bounds = _surface.CompactPairBounds(peers.Length > 1 ? Array.IndexOf(peers, member) : -1);
        return new PointF(rect.Left + bounds.Left + bounds.Width / 2f,
            rect.Top + bounds.Top + bounds.Height / 2f);
    }

    private void TickVisualMotion()
    {
        if (_foldStarted != 0)
        {
            var progress = (float)(MotionSeconds(_foldStarted)/FoldSeconds);
            if (progress >= 1) { FinishFold(); return; }
            var bounds = FoldBounds(_foldStart, _foldEnd, progress);
            using var frame = _surface.RenderFoldFrame(_foldBefore!, _foldAfter!, bounds.Size, progress,
                _foldSourceIdentity, _foldTargetIdentity, _foldBody, _collapsed);
            PresentVisual(frame, bounds.Location);
            return;
        }
        if (_connectionStarted == 0) return;
        var startPoint = ConnectionMemberCenter(_connectionPeer);
        var endPoint = ConnectionMemberCenter(_connectionMember);
        var start = startPoint.GetValueOrDefault();
        var end = endPoint.GetValueOrDefault();
        var dx = end.X - start.X;
        var dy = end.Y - start.Y;
        var distance = Math.Max(0, Math.Sqrt(dx * dx + dy * dy) - _connectionLength);
        var opacity = startPoint is null || endPoint is null ? 0 :
            ConnectionOpacity(distance, MotionSeconds(_connectionStarted));
        if (opacity < .01f)
        {
            _connectionStarted = 0;
            _connectionMembers = Array.Empty<nint>();
            _visualLayer?.Hide();
            _surface.SetVisualMotion(false);
            return;
        }
        var bounds2 = Rectangle.Inflate(Rectangle.Ceiling(RectangleF.FromLTRB(Math.Min(end.X, start.X),
            Math.Min(end.Y, start.Y), Math.Max(end.X, start.X) + 1,
            Math.Max(end.Y, start.Y) + 1)), 12, 12);
        using var tether = new Bitmap(bounds2.Width, bounds2.Height, PixelFormat.Format32bppPArgb);
        using var g = Graphics.FromImage(tether);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TranslateTransform(-bounds2.Left, -bounds2.Top);
        _surface.DrawElectricConnection(g, start, end, _connectionColor, .75f * opacity,
            Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency, opacity);
        PresentVisual(tether, bounds2.Location);
    }

    private void DisposeVisualMotion()
    {
        _surface.SetVisualMotion(false);
        _foldBefore?.Dispose(); _foldAfter?.Dispose();
        _visualLayer?.Dispose();
    }

}

internal sealed partial class ShellSurface
{
    // Native counterpart of card.fold_frame: morph the rounded shell and reveal
    // natural-size content. The canonical pair travels once without stretching.
    public Bitmap RenderFoldFrame(Bitmap before, Bitmap after, Size size, float progress,
        Rectangle sourceIdentity, Rectangle targetIdentity, Rectangle body, bool collapsing)
    {
        var frame = new Bitmap(size.Width, size.Height, PixelFormat.Format32bppPArgb);
        using var graphics = Graphics.FromImage(frame);
        if (progress <= 0 || progress >= 1)
        {
            graphics.DrawImageUnscaled(progress <= 0 ? before : after, Point.Empty);
            return frame;
        }
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
        var amount = Ease(progress);
        var expandedAmount = collapsing ? 1 - amount : amount;
        var expanded = collapsing ? before : after;
        var compact = collapsing ? after : before;
        var compactIdentity = collapsing ? targetIdentity : sourceIdentity;
        var expandedIdentity = collapsing ? sourceIdentity : targetIdentity;
        var movingBody = ShellForm.FoldBounds(collapsing ? body : sourceIdentity,
            collapsing ? targetIdentity : body, progress);
        using var path = GraphicsExtensions.RoundedPath(movingBody, Math.Min(PanelCornerRadius, movingBody.Height / 2));
        using var fill = new SolidBrush(Color.FromArgb((int)(255 * Math.Min(1, expandedAmount * 5)),
            Pick(_theme.Background, SystemColors.Window)));
        graphics.FillPath(fill, path);
        var state = graphics.Save();
        graphics.SetClip(path);
        graphics.ExcludeClip(expandedIdentity);
        using var attributes = new ImageAttributes();
        attributes.SetColorMatrix(new ColorMatrix { Matrix33 = Math.Max(0, (expandedAmount - .2f) / .8f) });
        graphics.DrawImage(expanded, new Rectangle(Point.Empty, expanded.Size), 0, 0,
            expanded.Width, expanded.Height, GraphicsUnit.Pixel, attributes);
        graphics.Restore(state);
        // Header controls remain at their natural scale and are revealed at the
        // same content cadence; they never get stretched across the growing body.
        graphics.SetClip(new Rectangle(0, 0, size.Width, Math.Max(0, body.Top)));
        graphics.ExcludeClip(expandedIdentity);
        graphics.DrawImage(expanded, new Rectangle(Point.Empty, expanded.Size), 0, 0,
            expanded.Width, expanded.Height, GraphicsUnit.Pixel, attributes);
        graphics.ResetClip();
        var identity = ShellForm.FoldBounds(sourceIdentity, targetIdentity, progress);
        graphics.DrawImage(compact, identity, compactIdentity, GraphicsUnit.Pixel);
        return frame;
    }
}
