using System.Diagnostics;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace MoShell.Native;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        if (OperatingSystem.IsWindows())
        {
            _ = SetCurrentProcessExplicitAppUserModelID("MOAgent.Shell");
        }
        ApplicationConfiguration.Initialize();
        using var form = new ShellForm();
        Application.Run(form);
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);
}

internal sealed partial class ShellForm : Form
{
    private const string AttachmentProperty = "MO_SHELL_ATTACHED_WINDOW_V1";
    private const int WsThickFrame = 0x00040000;
    private const int WmNcCalcSize = 0x0083;
    private const int WmDisplayChange = 0x007E;
    private const int WmSettingChange = 0x001A;
    private const double ApplicationWindowTimeoutSeconds = 12;
    private static readonly Size ExpandedSize = new(1080, 680);
    private static readonly Size ExpandedMinimumSize = new(680, 440);
    private Size CompactSize => _surface.CompactSize;
    private readonly ShellSurface _surface = new();
    private readonly BridgeClient _bridge = new();
    private readonly WindowAttachment _attachment = new();
    private readonly System.Windows.Forms.Timer _attachmentWatch = new() { Interval = 100 };
    private readonly System.Windows.Forms.Timer _effectSettle = new() { Interval = 120 };
    private Icon? _shellIcon;
    private Rectangle _expandedBounds;
    private Rectangle _restoreBounds;
    private Point _lastCompactLocation;
    private bool _collapsed = true;
    private bool _maximized;
    private bool _changingBounds;
    private bool _themeReady;
    private bool _entrancePending;
    private bool _launchSourcePending = ReadLaunchSourceRequest();
    private PendingApplicationDrop? _pendingApplicationDrop;
    protected override bool ShowWithoutActivation => _collapsed;

    protected override CreateParams CreateParams
    {
        get
        {
            var parameters = base.CreateParams;
            parameters.Style |= WsThickFrame;
            return parameters;
        }
    }

    protected override void WndProc(ref Message message)
    {
        if (HandleGroupMessage(ref message)) return;
        if (message.Msg == 0x0084 && _collapsed)
        {
            message.Result = (nint)1; // Compact surfaces are draggable, never resizable.
            return;
        }
        if (message.Msg == WmNcCalcSize)
        {
            message.Result = IntPtr.Zero;
            return;
        }
        base.WndProc(ref message);
        if (message.Msg is WmDisplayChange or WmSettingChange)
            OnShellBoundsChanged(resized: true);
    }

    public ShellForm()
    {
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.Manual;
        Size = CompactSize;
        MinimumSize = new Size(1, 1);
        KeyPreview = true;
        ShowInTaskbar = true;
        ShowIcon = true;
        TopMost = true;
        Text = "MO Shell";
        AutoScaleMode = AutoScaleMode.Dpi;
        Opacity = 0;

        _surface.SetCollapsed(true);
        _surface.WindowCandidatesRequested = () => WindowAttachment.Enumerate(Handle);
        _surface.InputRequested += value => _bridge.Send(new { type = "input", text = value });
        _surface.ResizeRequested += (columns, rows) =>
            _bridge.Send(new { type = "resize", columns, rows });
        _surface.ToggleCollapsedRequested += ToggleCollapsed;
        _surface.ToggleMaximizedRequested += ToggleMaximized;
        _surface.CloseRequested += Close;
        _surface.AttachRequested += AttachWindow;
        _surface.DetachRequested += DetachWindow;
        _surface.AttachmentGeometryChanged += () => _ = SyncAttachment();
        _surface.PickerVisibilityChanged += OnPickerVisibilityChanged;
        _surface.CompactPresentationChanged += RefreshCompactPresentation;
        _surface.Invalidated += (_, _) => QueueCompactFrame();
        _surface.SizeChanged += (_, _) => QueueCompactFrame();
        _surface.GroupOpenRequested += OpenGroupMember;
        _surface.GroupExtractRequested += ExtractGroupMember;
        _surface.CompactDragCompleted += TryMergeNearbyGroup;
        _surface.VisualFrame += TickVisualMotion;
        foreach (var dropTarget in new Control[] { this, _surface })
        {
            dropTarget.AllowDrop = true;
            dropTarget.DragEnter += OnApplicationDragOver;
            dropTarget.DragOver += OnApplicationDragOver;
            dropTarget.DragLeave += (_, _) => _surface.BeginApplicationDragLeave();
            dropTarget.DragDrop += OnApplicationDrop;
        }
        _attachment.PositionChanged += () => _ = SyncAttachment();
        Controls.Add(_surface);

        _bridge.EventReceived += OnBridgeEvent;
        _bridge.ErrorReceived += message => Post(() =>
        {
            _surface.SetStatus(message, isError: true);
            RevealShell(false);
        });
        _attachmentWatch.Tick += (_, _) => { CheckAttachment(); CheckGroupState(); };
        _attachmentWatch.Start();
        _effectSettle.Tick += (_, _) =>
        {
            _effectSettle.Stop();
            SendWindowEffect(show: true);
        };

        Shown += (_, _) => StartShell();
        Activated += (_, _) =>
        {
            _ = SyncAttachment();
            SendWindowEffect(show: true);
        };
        Move += (_, _) => OnShellBoundsChanged(resized: false);
        Resize += (_, _) => OnShellBoundsChanged(resized: true);
        FormClosed += (_, _) => DisposeShell();
    }

    private void StartShell()
    {
        InitializeGroupIdentity();
        SetInitialBounds(Screen.FromControl(this).WorkingArea);
        try
        {
            _bridge.Start(Handle);
        }
        catch (Exception error) when (error is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            _surface.SetStatus($"MO terminal unavailable: {error.Message}", isError: true);
            RevealShell(false);
        }
    }

    private void SetInitialBounds(Rectangle working)
    {
        _expandedBounds = new Rectangle(
            working.Left + Math.Max(0, (working.Width - ExpandedSize.Width) / 2),
            working.Top + Math.Max(0, (working.Height - ExpandedSize.Height) / 2),
            Math.Min(ExpandedSize.Width, working.Width),
            Math.Min(ExpandedSize.Height, working.Height));
        _restoreBounds = _expandedBounds;
        var compactBounds = CompactBoundsFor(_expandedBounds);
        compactBounds = InitialPeerBounds(compactBounds, working);
        _lastCompactLocation = compactBounds.Location;
        _changingBounds = true;
        try
        {
            Bounds = compactBounds;
        }
        finally
        {
            _changingBounds = false;
        }
    }

    private void OnBridgeEvent(JsonElement message)
    {
        Post(() =>
        {
            var type = message.TryGetProperty("type", out var typeValue) ? typeValue.GetString() : "";
            switch (type)
            {
                case "ready":
                    _surface.NotifyLayoutChanged();
                    if (_launchSourcePending)
                    {
                        Console.WriteLine("{\"kind\":\"launch_source\"}");
                        Console.Out.Flush();
                        _ = Task.Run(() =>
                        {
                            try
                            {
                                var line = Console.ReadLine();
                                using var source = JsonDocument.Parse(line ?? "null");
                                var current = source.RootElement.Clone();
                                Post(() =>
                                {
                                    if (!_launchSourcePending) return;
                                    if (current.ValueKind == JsonValueKind.Null) RevealShell(false);
                                    else BeginShellEntrance(current);
                                });
                            }
                            catch (Exception error) when (error is JsonException or IOException)
                            {
                                Post(() => RevealShell(false));
                            }
                        });
                    }
                    break;
                case "screen":
                    var rows = message.TryGetProperty("fragments", out var fragmentValue)
                        ? fragmentValue.EnumerateArray().Select(ParseTerminalRow).ToArray()
                        : Array.Empty<TerminalFragment[]>();
                    var screenRows = message.TryGetProperty("rows", out var rowCount) &&
                        rowCount.TryGetInt32(out var parsedRows)
                        ? Math.Max(1, parsedRows)
                        : Math.Max(1, rows.Length);
                    var cursorX = 0;
                    var cursorY = 0;
                    var cursorVisible = false;
                    var busy = ReadBoolean(message, "busy");
                    if (message.TryGetProperty("cursor", out var cursor))
                    {
                        if (cursor.TryGetProperty("x", out var x)) cursorX = x.GetInt32();
                        if (cursor.TryGetProperty("y", out var y)) cursorY = y.GetInt32();
                        if (cursor.TryGetProperty("visible", out var visible)) cursorVisible = visible.GetBoolean();
                    }
                    _surface.SetScreen(
                        rows,
                        screenRows,
                        cursorX,
                        cursorY,
                        cursorVisible,
                        busy);
                    PublishGroupState();
                    break;
                case "theme" when message.TryGetProperty("payload", out var payload):
                    if (ThemeState.TryFromPayload(payload, out var theme))
                    {
                        _surface.SetTheme(theme);
                        PublishGroupState();
                        BackColor = theme.Background;
                        ApplyWindowRegion();
                        ApplyBrandIcon(payload);
                        if (!_themeReady)
                        {
                            _themeReady = true;
                            if (!_launchSourcePending) RevealShell();
                        }
                        SendWindowEffect(show: true);
                    }
                    break;
                case "error":
                    _surface.SetStatus(
                        message.TryGetProperty("message", out var value)
                            ? value.GetString() ?? "MO Shell bridge error"
                            : "MO Shell bridge error",
                        isError: true);
                    if (!_themeReady || _entrancePending || _launchSourcePending) RevealShell(false);
                    break;
                case "entrance_finished":
                    RevealShell(ReadBoolean(message, "confirmed"));
                    break;
                case "entrance_started":
                    Console.WriteLine("{\"kind\":\"launch_started\"}");
                    Console.Out.Flush();
                    break;
                case "recovered" when message.TryGetProperty("message", out var recovered):
                    _surface.ClearStatus(recovered.GetString() ?? "");
                    break;
            }
        });
    }

    private void BeginShellEntrance(JsonElement source)
    {
        if (source.TryGetProperty("work_area", out var area) && area.ValueKind == JsonValueKind.Array && area.GetArrayLength() == 4)
        {
            var values = area.EnumerateArray().ToArray();
            if (values[0].TryGetInt32(out var left) && values[1].TryGetInt32(out var top) &&
                values[2].TryGetInt32(out var right) && values[3].TryGetInt32(out var bottom) &&
                right > left && bottom > top)
                SetInitialBounds(Rectangle.FromLTRB(left, top, right, bottom));
        }
        _launchSourcePending = false;
        _entrancePending = true;
        _bridge.Send(new { type = "window_entrance", hwnd = Handle.ToInt64(),
            source, target = _surface.LaunchTarget(Location) });
    }

    private void RevealShell(bool confirmed = true)
    {
        var completingEntrance = _entrancePending || _launchSourcePending;
        _launchSourcePending = false;
        _entrancePending = false;
        Opacity = 1;
        QueueCompactFrame();
        SendWindowEffect(show: true);
        if (completingEntrance)
        {
            Console.WriteLine(JsonSerializer.Serialize(new { kind = "launch_ready", confirmed }));
            Console.Out.Flush();
        }
        PublishGroupState();
        if (_initialGroupTarget != 0)
        {
            var target = _initialGroupTarget;
            _initialGroupTarget = 0;
            BeginInvoke(() => RequestGroupJoin(target));
        }
    }

    private static bool ReadLaunchSourceRequest()
    {
        var source = Environment.GetEnvironmentVariable("MO_SHELL_LAUNCH_ORIGIN");
        Environment.SetEnvironmentVariable("MO_SHELL_LAUNCH_ORIGIN", null);
        if (string.IsNullOrEmpty(source)) return false;
        try
        {
            using var document = JsonDocument.Parse(source);
            return ReadBoolean(document.RootElement, "deferred");
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException)
        {
            return false;
        }
    }

    private static TerminalFragment[] ParseTerminalRow(JsonElement row) =>
        row.ValueKind == JsonValueKind.Array
            ? row.EnumerateArray().Select(ParseTerminalFragment).ToArray()
            : Array.Empty<TerminalFragment>();

    private static TerminalFragment ParseTerminalFragment(JsonElement value)
    {
        var text = value.TryGetProperty("text", out var textValue)
            ? textValue.GetString() ?? ""
            : "";
        var columns = value.TryGetProperty("columns", out var columnValue) &&
            columnValue.TryGetInt32(out var parsedColumns)
            ? Math.Max(0, parsedColumns)
            : text.Length;
        return new TerminalFragment(
            text,
            columns,
            ParseOptionalColor(value, "foreground"),
            ReadBoolean(value, "bold"),
            ReadBoolean(value, "dim"),
            ReadBoolean(value, "italic"),
            ReadBoolean(value, "underline"),
            ReadBoolean(value, "strike"),
            ReadBoolean(value, "emoji"));
    }

    private static bool ReadBoolean(JsonElement value, string name) =>
        value.TryGetProperty(name, out var property) &&
        property.ValueKind == JsonValueKind.True;

    private static Color? ParseOptionalColor(JsonElement value, string name)
    {
        if (!value.TryGetProperty(name, out var property) ||
            property.ValueKind != JsonValueKind.String)
        {
            return null;
        }
        try { return ColorTranslator.FromHtml(property.GetString() ?? ""); }
        catch (ArgumentException) { return null; }
    }

    private void ApplyBrandIcon(JsonElement payload)
    {
        if (!payload.TryGetProperty("brand_icon", out var value) ||
            value.ValueKind != JsonValueKind.String)
        {
            return;
        }
        try
        {
            var data = Convert.FromBase64String(value.GetString() ?? "");
            using var stream = new MemoryStream(data, writable: false);
            using var loaded = new Icon(stream);
            var replacement = (Icon)loaded.Clone();
            var previous = _shellIcon;
            _shellIcon = replacement;
            Icon = replacement;
            previous?.Dispose();
        }
        catch (Exception error) when (error is ArgumentException or FormatException)
        {
            _surface.SetStatus("MO Shell taskbar icon could not be loaded", isError: true);
        }
    }

    private void Post(Action action)
    {
        if (IsDisposed || !IsHandleCreated) return;
        try { BeginInvoke(action); }
        catch (InvalidOperationException) { }
    }

    private void AttachWindow(WindowCandidate candidate, AttachmentMode mode)
    {
        _pendingApplicationDrop = null;
        var previousTarget = _attachment.Handle;
        try
        {
            _attachment.Attach(candidate, mode, Handle);
            _surface.SetAttachment(true, mode, candidate.CloneIcon(), candidate.Title);
            ApplyExpandedMinimumSize();
            ApplyWindowRegion();
            if (!SyncAttachment()) return;
            PublishAttachmentContext();
        }
        catch (Exception error) when (error is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            // An admission refusal must not detach the previously owned target.
            if (previousTarget == 0 || _attachment.Handle != previousTarget)
            {
                _attachment.Detach();
                ClearAttachmentContext();
                _surface.SetAttachment(false, mode);
                ApplyExpandedMinimumSize();
                ApplyWindowRegion();
            }
            _surface.SetStatus($"Could not attach {candidate.Title}: {error.Message}", isError: true);
        }
    }

    private void DetachWindow()
    {
        ClearAttachmentContext();
        _attachment.Detach();
        _surface.SetAttachment(false, _surface.SelectedMode);
        ApplyExpandedMinimumSize();
        ApplyWindowRegion();
    }

    private void CheckAttachment()
    {
        _surface.PollApplicationDragArm();
        if (_attachment.IsAttached && !_attachment.IsAlive)
        {
            ClearAttachmentContext();
            _attachment.ForgetClosed();
            _surface.SetAttachment(false, _surface.SelectedMode);
            ApplyExpandedMinimumSize();
            ApplyWindowRegion();
        }
        CheckPendingApplicationDrop();
    }

    private void OnApplicationDragOver(object? sender, DragEventArgs e)
    {
        if (!_collapsed || _changingBounds ||
            !TryGetDroppedApplicationPath(e.Data, out _))
        {
            e.Effect = DragDropEffects.None;
            _surface.SetApplicationDrag(false);
            return;
        }
        e.Effect = DragDropEffects.Copy;
        _surface.SetApplicationDrag(
            true,
            _surface.ApplicationDragNearness(new Point(e.X, e.Y)));
    }

    private void OnApplicationDrop(object? sender, DragEventArgs e)
    {
        _surface.SetApplicationDrag(false);
        if (!_collapsed || _changingBounds ||
            !TryGetDroppedApplicationPath(e.Data, out var launchPath))
        {
            e.Effect = DragDropEffects.None;
            return;
        }
        if (!TryResolveApplicationLaunch(launchPath, out var launch))
        {
            e.Effect = DragDropEffects.None;
            _surface.SetStatus(
                $"{Path.GetFileName(launchPath)} does not resolve to an existing Windows executable",
                isError: true);
            return;
        }
        e.Effect = DragDropEffects.Copy;
        if (StartApplicationDrop(launch)) _surface.PlayApplicationDrop();
    }

    private bool StartApplicationDrop(ApplicationLaunch launch)
    {
        var baseline = new HashSet<nint>();
        var candidates = WindowAttachment.Enumerate(Handle);
        try
        {
            foreach (var candidate in candidates)
            {
                baseline.Add(candidate.Handle);
            }
        }
        finally
        {
            foreach (var candidate in candidates) candidate.Dispose();
        }

        try
        {
            using var process = Process.Start(new ProcessStartInfo
            {
                FileName = launch.LaunchPath,
                WorkingDirectory = Path.GetDirectoryName(launch.LaunchPath) ?? Environment.CurrentDirectory,
                UseShellExecute = true,
            });
            var processId = process is null ? 0 : (uint)process.Id;
            var executablePath = launch.ExecutablePath;
            try
            {
                var startedPath = process?.MainModule?.FileName;
                if (!string.IsNullOrWhiteSpace(startedPath))
                {
                    executablePath = Path.GetFullPath(startedPath);
                }
            }
            catch (Exception error) when (
                error is InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                // ShellExecute may return a launcher that exits immediately. The
                // resolved shortcut target remains the exact fallback in that case.
            }
            _pendingApplicationDrop = new PendingApplicationDrop(
                launch.LaunchPath,
                executablePath,
                processId,
                baseline,
                Stopwatch.GetTimestamp());
            return true;
        }
        catch (Exception error) when (
            error is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            _pendingApplicationDrop = null;
            _surface.SetStatus(
                $"Could not open {Path.GetFileName(launch.LaunchPath)}: {error.Message}",
                isError: true);
            return false;
        }
    }

    private void CheckPendingApplicationDrop()
    {
        var pending = _pendingApplicationDrop;
        if (pending is null) return;
        var elapsed = (Stopwatch.GetTimestamp() - pending.Started) / (double)Stopwatch.Frequency;
        if (elapsed < _surface.ApplicationDropReactionSeconds) return;
        if (elapsed >= ApplicationWindowTimeoutSeconds)
        {
            _pendingApplicationDrop = null;
            _surface.SetStatus(
                $"{Path.GetFileName(pending.LaunchPath)} did not open an attachable window",
                isError: true);
            return;
        }

        var candidates = WindowAttachment.Enumerate(
            Handle,
            pending.ExecutablePath,
            pending.ProcessId);
        try
        {
            var exactProcess = pending.ProcessId == 0
                ? Array.Empty<WindowCandidate>()
                : candidates
                    .Where(candidate => candidate.ProcessId == pending.ProcessId)
                    .ToArray();
            var exactPath = candidates
                .Where(candidate => WindowAttachment.SameExecutablePath(
                    candidate.ExecutablePath,
                    pending.ExecutablePath))
                .ToArray();
            var newExactPath = exactPath
                .Where(candidate => !pending.ExistingHandles.Contains(candidate.Handle))
                .ToArray();
            var candidate = exactProcess.Length == 1
                ? exactProcess[0]
                : newExactPath.Length == 1
                    ? newExactPath[0]
                    : (pending.ProcessId == 0 || ProcessExited(pending.ProcessId)) &&
                        exactPath.Length == 1
                        ? exactPath[0]
                        : null;
            if (candidate is null) return;

            AttachWindow(candidate, AttachmentMode.Managed);
            if (!_attachment.IsAttached) return;
            _pendingApplicationDrop = null;
            if (_collapsed && !_changingBounds) SwitchCollapsed(collapsing: false);
        }
        finally
        {
            foreach (var candidate in candidates) candidate.Dispose();
        }
    }

    private static bool TryGetDroppedApplicationPath(IDataObject? data, out string launchPath)
    {
        launchPath = "";
        string[] paths;
        try
        {
            if (data?.GetDataPresent(DataFormats.FileDrop) != true ||
                data.GetData(DataFormats.FileDrop) is not string[] { Length: 1 } droppedPaths)
            {
                return false;
            }
            paths = droppedPaths;
        }
        catch (ExternalException)
        {
            return false;
        }
        var path = paths[0];
        var extension = Path.GetExtension(path);
        if (!string.Equals(extension, ".exe", StringComparison.OrdinalIgnoreCase) &&
            !string.Equals(extension, ".lnk", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }
        launchPath = Path.GetFullPath(path);
        return true;
    }

    private static bool TryResolveApplicationLaunch(
        string launchPath,
        out ApplicationLaunch launch)
    {
        launch = new ApplicationLaunch("", "");
        if (!File.Exists(launchPath)) return false;
        var extension = Path.GetExtension(launchPath);
        var executablePath = string.Equals(extension, ".exe", StringComparison.OrdinalIgnoreCase)
            ? launchPath
            : ResolveShortcutTarget(launchPath);
        if (string.IsNullOrWhiteSpace(executablePath) ||
            !string.Equals(
                Path.GetExtension(executablePath),
                ".exe",
                StringComparison.OrdinalIgnoreCase) ||
            !File.Exists(executablePath))
        {
            return false;
        }
        launch = new ApplicationLaunch(launchPath, Path.GetFullPath(executablePath));
        return true;
    }

    private static string? ResolveShortcutTarget(string shortcutPath)
    {
        object? shell = null;
        object? shortcut = null;
        try
        {
            var shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType is null) return null;
            shell = Activator.CreateInstance(shellType);
            if (shell is null) return null;
            shortcut = shellType.InvokeMember(
                "CreateShortcut",
                BindingFlags.InvokeMethod,
                binder: null,
                shell,
                new object[] { shortcutPath });
            var target = shortcut?.GetType().InvokeMember(
                "TargetPath",
                BindingFlags.GetProperty,
                binder: null,
                shortcut,
                args: null) as string;
            if (string.IsNullOrWhiteSpace(target)) return null;
            target = Environment.ExpandEnvironmentVariables(target.Trim().Trim('"'));
            return Path.IsPathRooted(target)
                ? Path.GetFullPath(target)
                : Path.GetFullPath(target, Path.GetDirectoryName(shortcutPath)!);
        }
        catch (Exception error) when (
            error is ArgumentException or COMException or InvalidOperationException or
            NotSupportedException or PathTooLongException or TargetInvocationException)
        {
            return null;
        }
        finally
        {
            ReleaseComObject(shortcut);
            ReleaseComObject(shell);
        }
    }

    private static void ReleaseComObject(object? value)
    {
        if (value is not null && Marshal.IsComObject(value))
        {
            _ = Marshal.FinalReleaseComObject(value);
        }
    }

    private static bool ProcessExited(uint processId)
    {
        try
        {
            using var process = Process.GetProcessById((int)processId);
            return process.HasExited;
        }
        catch (Exception error) when (
            error is ArgumentException or InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            return true;
        }
    }

    private bool SyncAttachment()
    {
        if (_collapsed || _changingBounds || !_attachment.IsAttached) return true;
        var pane = _surface.AttachmentContentBounds;
        if (pane.Width <= 0 || pane.Height <= 0) return true;
        try
        {
            var actual = _attachment.Sync(pane, PointToScreen(Point.Empty));
            var changed = _surface.EnsureAttachmentContentSize(pane.Size, actual);
            var workArea = Screen.FromRectangle(Bounds).WorkingArea;
            if (_surface.MinimumExpandedWidth > workArea.Width ||
                _surface.MinimumExpandedHeight > workArea.Height)
            {
                DetachWindow();
                _surface.SetStatus("This window's native minimum cannot fit in the screen work area", isError: true);
                return false;
            }
            if (changed)
            {
                ApplyExpandedMinimumSize();
                ApplyWindowRegion();
                _surface.NotifyLayoutChanged();
            }
            return true;
        }
        catch (System.ComponentModel.Win32Exception error)
        {
            DetachWindow();
            _surface.SetStatus($"Attached window could not be positioned: {error.Message}", isError: true);
            return false;
        }
    }

    private void OnPickerVisibilityChanged(bool visible)
    {
        if (visible)
        {
            _attachment.Hide();
            return;
        }
        if (_collapsed) return;
        _attachment.Show();
        _ = SyncAttachment();
    }

    private void ToggleCollapsed()
    {
        if (_changingBounds) return;
        if (_collapsed)
        {
            SwitchCollapsed(collapsing: false);
            return;
        }

        SwitchCollapsed(collapsing: true);
    }

    private void SwitchCollapsed(bool collapsing)
    {
        FinishFold();
        var before = Visible && Opacity > 0 ? _surface.RenderPresentationFrame() : null;
        var previous = Bounds;
        var sourceIdentity = _surface.IdentityBounds;
        var previousBody = _surface.BodyBounds;
        if (before is not null)
        {
            EndCompactComposition();
            Opacity = 0;
        }
        if (!collapsing) LeaveCompactGroup();
        if (collapsing)
        {
            _expandedBounds = Bounds;
        }
        else if (_maximized)
        {
            var compactCenter = new Point(Left + Width / 2, Top + Height / 2);
            _expandedBounds = Screen.FromPoint(compactCenter).WorkingArea;
        }
        _effectSettle.Stop();
        SendWindowEffect(show: false);
        _attachment.Hide();
        MinimumSize = new Size(1, 1);
        var target = collapsing ? ReturnCompactBounds() : _expandedBounds;
        _changingBounds = true;
        try
        {
            if (collapsing)
            {
                _collapsed = true;
                TopMost = true;
                _surface.SetCollapsed(true);
                Bounds = target;
                ConstrainCompactBounds();
                _lastCompactLocation = Location;
            }
            else
            {
                _collapsed = false;
                EndCompactComposition();
                TopMost = false;
                Bounds = target;
                _surface.SetCollapsed(false);
                ApplyExpandedMinimumSize();
                _expandedBounds = Bounds;
            }
            ApplyWindowRegion();
            _surface.Refresh();
        }
        finally
        {
            _changingBounds = false;
        }
        if (before is not null) StartFold(before, previous, sourceIdentity, previousBody);
        else FinishCollapsedPresentation();
    }

    private void ToggleMaximized()
    {
        if (_collapsed || _changingBounds) return;
        _effectSettle.Stop();
        SendWindowEffect(show: false);
        _changingBounds = true;
        try
        {
            if (_maximized)
            {
                _maximized = false;
                _expandedBounds = _restoreBounds;
            }
            else
            {
                _restoreBounds = Bounds;
                _maximized = true;
                _expandedBounds = Screen.FromControl(this).WorkingArea;
            }
            _surface.SetMaximized(_maximized);
            Bounds = FitToWorkArea(_expandedBounds, Screen.FromRectangle(_expandedBounds).WorkingArea);
            ApplyExpandedMinimumSize();
            _expandedBounds = Bounds;
            ApplyWindowRegion();
        }
        finally
        {
            _changingBounds = false;
        }
        _surface.NotifyLayoutChanged();
        SendWindowEffect(show: true);
    }

    private void ApplyExpandedMinimumSize()
    {
        if (_collapsed && !_changingBounds) return;
        var workArea = Screen.FromRectangle(Bounds).WorkingArea;
        var changing = _changingBounds;
        _changingBounds = true;
        try
        {
            // Native application minima cannot force Shell beyond its monitor.
            MinimumSize = new Size(
                Math.Min(workArea.Width, Math.Max(ExpandedMinimumSize.Width, _surface.MinimumExpandedWidth)),
                Math.Min(workArea.Height, Math.Max(ExpandedMinimumSize.Height, _surface.MinimumExpandedHeight)));
            MaximumSize = workArea.Size;
            Bounds = _maximized ? workArea : FitToWorkArea(Bounds, workArea);
        }
        finally { _changingBounds = changing; }
    }

    private static Rectangle FitToWorkArea(Rectangle bounds, Rectangle workArea)
    {
        var width = Math.Clamp(bounds.Width, 1, workArea.Width);
        var height = Math.Clamp(bounds.Height, 1, workArea.Height);
        return new Rectangle(
            Math.Clamp(bounds.X, workArea.Left, workArea.Right - width),
            Math.Clamp(bounds.Y, workArea.Top, workArea.Bottom - height),
            width, height);
    }

    private void ConstrainCompactBounds()
    {
        var workArea = Screen.FromRectangle(Bounds).WorkingArea;
        var changing = _changingBounds;
        _changingBounds = true;
        try
        {
            // Compact input cannot resize. Its surface owns its exact size;
            // changing WinForms.MinimumSize here also activates this window,
            // stealing another member's drag or focus during idle animation.
            MaximumSize = workArea.Size;
            Bounds = FitToWorkArea(Bounds, workArea);
        }
        finally { _changingBounds = changing; }
    }

    private void OnShellBoundsChanged(bool resized)
    {
        if (_changingBounds) return;
        if (_groupLeader != 0 && _groupLeader != Handle) return;
        if (_collapsed)
        {
            ConstrainCompactBounds();
            var workArea = Screen.FromRectangle(Bounds).WorkingArea;
            if (_maximized)
            {
                _expandedBounds = workArea;
            }
            else
            {
                var delta = new Size(Location.X - _lastCompactLocation.X, Location.Y - _lastCompactLocation.Y);
                _expandedBounds.Offset(delta.Width, delta.Height);
                _expandedBounds = FitToWorkArea(_expandedBounds, workArea);
            }
            _lastCompactLocation = Location;
            SendWindowEffect(show: true);
            ParkGroupMembers();
            return;
        }
        ApplyExpandedMinimumSize();
        _expandedBounds = Bounds;
        _ = SyncAttachment();
        if (resized)
        {
            ApplyWindowRegion();
            if (!_effectSettle.Enabled) SendWindowEffect(show: false);
            _effectSettle.Stop();
            _effectSettle.Start();
            return;
        }
        if (!_effectSettle.Enabled) SendWindowEffect(show: true);
    }

    private void ApplyWindowRegion()
    {
        var previous = Region;
        Region = _surface.CreateWindowRegion();
        previous?.Dispose();
    }

    private void RefreshCompactPresentation()
    {
        if (!_collapsed || _changingBounds) return;
        if (_groupLeader != 0 && _groupLeader != Handle) return;
        var width = _surface.CompactWidth(CompactSize.Width);
        var size = new Size(width, CompactSize.Height);
        if (ClientSize != size)
        {
            _changingBounds = true;
            try
            {
                // WM_NCCALCSIZE gives the surface the entire outer rectangle.
                // ClientSize would add the hidden WS_THICKFRAME margins again.
                Size = size;
            }
            finally
            {
                _changingBounds = false;
            }
        }
        ConstrainCompactBounds();
        ApplyWindowRegion();
        OnShellBoundsChanged(resized: false);
    }

    private void PublishAttachmentContext()
    {
        var target = _attachment.Handle;
        if (target != 0) _ = SetProp(Handle, AttachmentProperty, target);
    }

    private void ClearAttachmentContext()
    {
        if (IsHandleCreated) _ = RemoveProp(Handle, AttachmentProperty);
    }

    private void SendWindowEffect(bool show)
    {
        if (!IsHandleCreated) return;
        var content = Rectangle.Intersect(
            _collapsed ? _surface.IdentityBounds : _surface.BodyBounds,
            new Rectangle(Point.Empty, ClientSize));
        if (content.Width <= 0 || content.Height <= 0) return;
        _bridge.Send(new
        {
            type = "window_visual",
            hwnd = Handle.ToInt64(),
            owner_width = ClientSize.Width,
            owner_height = ClientSize.Height,
            x = content.X,
            y = content.Y,
            width = content.Width,
            height = content.Height,
            radius = _collapsed ? _surface.IdentityCornerRadius : _surface.BodyCornerRadius,
            visible = show && Visible && Opacity > 0 && !_surface.HasCompactGroup,
        });
    }

    private Rectangle CompactBoundsFor(Rectangle expanded) => new(
        expanded.Left + (expanded.Width - CompactSize.Width) / 2,
        expanded.Top,
        CompactSize.Width,
        CompactSize.Height);

    private void DisposeShell()
    {
        DisposeVisualMotion();
        LeaveCompactGroup();
        if (IsHandleCreated) RemoveGroupProperties();
        _attachmentWatch.Stop();
        _effectSettle.Stop();
        _pendingApplicationDrop = null;
        SendWindowEffect(show: false);
        ClearAttachmentContext();
        _attachment.Dispose();
        _bridge.Dispose();
        _shellIcon?.Dispose();
        _shellIcon = null;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "SetPropW")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetProp(nint handle, string name, nint value);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "RemovePropW")]
    private static extern nint RemoveProp(nint handle, string name);

    private sealed record ApplicationLaunch(string LaunchPath, string ExecutablePath);

    private sealed record PendingApplicationDrop(
        string LaunchPath,
        string ExecutablePath,
        uint ProcessId,
        HashSet<nint> ExistingHandles,
        long Started);
}

internal sealed class BridgeClient : IDisposable
{
    private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);
    private Process? _process;
    private StreamWriter? _input;
    private bool _disposed;
    public event Action<JsonElement>? EventReceived;
    public event Action<string>? ErrorReceived;

    public void Start(nint shellHandle)
    {
        if (_process is not null) return;
        var python = Environment.GetEnvironmentVariable("MO_PYTHON") ?? "python";
        var project = ResolveProjectDirectory();
        // The bridge runs from MO's checkout so `-m mo_shell.bridge` imports from any project; --cwd is the project.
        var root = Environment.GetEnvironmentVariable("MO_AGENT_ROOT");
        var info = new ProcessStartInfo
        {
            FileName = python,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardInputEncoding = Utf8NoBom,
            StandardOutputEncoding = Utf8NoBom,
            StandardErrorEncoding = Utf8NoBom,
            CreateNoWindow = true,
            WorkingDirectory = !string.IsNullOrWhiteSpace(root) && Directory.Exists(root) ? root : project,
        };
        info.ArgumentList.Add("-m");
        info.ArgumentList.Add("mo_shell.bridge");
        info.ArgumentList.Add("--cwd");
        info.ArgumentList.Add(project);
        info.Environment["MO_SHELL_HOST_HWND"] = shellHandle.ToInt64().ToString(CultureInfo.InvariantCulture);
        var process = Process.Start(info) ?? throw new InvalidOperationException("MO Shell could not start its bridge");
        _process = process;
        _input = process.StandardInput;
        _ = ReadEvents(process);
        _ = ReadErrors(process);
    }

    private async Task ReadEvents(Process process)
    {
        try
        {
            while (true)
            {
                var line = await process.StandardOutput.ReadLineAsync().ConfigureAwait(false);
                if (line is null) break;
                if (string.IsNullOrWhiteSpace(line)) continue;
                try
                {
                    using var document = JsonDocument.Parse(line);
                    EventReceived?.Invoke(document.RootElement.Clone());
                }
                catch (JsonException)
                {
                    ErrorReceived?.Invoke("MO Shell received an invalid bridge event");
                }
            }
            if (!process.HasExited || _disposed) return;
            ErrorReceived?.Invoke($"MO terminal bridge exited with code {process.ExitCode}");
        }
        catch (Exception error) when (error is IOException or ObjectDisposedException or InvalidOperationException)
        {
            if (!_disposed) ErrorReceived?.Invoke($"MO terminal bridge stopped: {error.Message}");
        }
    }

    private async Task ReadErrors(Process process)
    {
        var reported = false;
        try
        {
            while (true)
            {
                var line = await process.StandardError.ReadLineAsync().ConfigureAwait(false);
                if (line is null) break;
                if (!reported && !string.IsNullOrWhiteSpace(line) && !_disposed)
                {
                    ErrorReceived?.Invoke("MO terminal bridge reported an error");
                    reported = true;
                }
            }
        }
        catch (Exception error) when (error is IOException or ObjectDisposedException or InvalidOperationException) { }
    }

    private static string ResolveProjectDirectory()
    {
        var configured = Environment.GetEnvironmentVariable("MO_PROJECT_CWD");
        if (!string.IsNullOrWhiteSpace(configured) && Directory.Exists(configured))
        {
            return Path.GetFullPath(configured);
        }

        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null)
        {
            if (File.Exists(Path.Combine(directory.FullName, "mo.py")) &&
                File.Exists(Path.Combine(directory.FullName, "mo_shell", "bridge.py")))
            {
                return directory.FullName;
            }
            directory = directory.Parent;
        }
        return Environment.CurrentDirectory;
    }

    public void Send(object message)
    {
        try
        {
            _input?.WriteLine(JsonSerializer.Serialize(message));
            _input?.Flush();
        }
        catch (Exception error) when (error is IOException or InvalidOperationException or ObjectDisposedException)
        {
            ErrorReceived?.Invoke($"MO terminal input failed: {error.Message}");
        }
    }

    public void Dispose()
    {
        Send(new { type = "close" });
        _disposed = true;
        var process = _process;
        try { process?.WaitForExit(500); } catch (InvalidOperationException) { }
        try { if (process is { HasExited: false }) process.Kill(true); } catch (InvalidOperationException) { }
        _input?.Dispose();
        process?.Dispose();
        _input = null;
        _process = null;
    }
}
