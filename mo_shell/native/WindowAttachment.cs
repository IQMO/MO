using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace MoShell.Native;

internal enum AttachmentMode
{
    Managed,
    Embedded,
}

internal sealed class WindowCandidate : IDisposable
{
    public WindowCandidate(
        nint handle,
        string title,
        string processName,
        uint processId,
        string executablePath,
        Icon? icon,
        bool isUtilityWindow)
    {
        Handle = handle;
        Title = title;
        ProcessName = processName;
        ProcessId = processId;
        ExecutablePath = executablePath;
        Icon = icon;
        IsUtilityWindow = isUtilityWindow;
    }

    public nint Handle { get; }
    public string Title { get; }
    public string ProcessName { get; }
    public uint ProcessId { get; }
    public string ExecutablePath { get; }
    public Icon? Icon { get; }
    public bool IsUtilityWindow { get; }

    public Icon? CloneIcon() => Icon is null ? null : (Icon)Icon.Clone();

    public void Dispose() => Icon?.Dispose();
}

internal sealed class WindowAttachment : IDisposable
{
    private const int GwlStyle = -16;
    private const int GwlExStyle = -20;
    private const int GwlHwndParent = -8;
    private const long WsChild = 0x40000000L;
    private const long WsPopup = unchecked((long)0x80000000L);
    private const long WsCaption = 0x00C00000L;
    private const long WsThickFrame = 0x00040000L;
    private const long WsMinimizeBox = 0x00020000L;
    private const long WsMaximizeBox = 0x00010000L;
    private const long WsSysMenu = 0x00080000L;
    private const long WsExToolWindow = 0x00000080L;
    private const long WsExAppWindow = 0x00040000L;
    private const long WsExTopmost = 0x00000008L;
    private static readonly nint HwndTopmost = new(-1);
    private static readonly nint HwndNotTopmost = new(-2);
    private const uint GwOwner = 4;
    private const uint DwmwaCloaked = 14;
    private const uint SwpNoZOrder = 0x0004;
    private const uint SwpNoActivate = 0x0010;
    private const uint SwpFrameChanged = 0x0020;
    private const uint SwpShowWindow = 0x0040;
    private const int SwHide = 0;
    private const int SwShowNoActivate = 4;
    private const uint WmGetIcon = 0x007F;
    private const uint EventObjectLocationChange = 0x800B;
    private const int ObjidWindow = 0;
    private const uint WineventOutofcontext = 0x0000;
    private const nuint IconSmall = 0;
    private const nuint IconBig = 1;
    private const nuint IconSmall2 = 2;
    private const uint SmtoAbortIfHung = 0x0002;
    private const int GclpHIcon = -14;
    private const int GclpHIconSm = -34;

    private nint _handle;
    private Mutex? _claim;
    private nint _host;
    private nint _originalParent;
    private nint _originalStyle;
    private nint _originalExStyle;
    private NativeRect _originalRect;
    private NativeWindowPlacement _originalPlacement;
    private bool _originallyVisible;
    private bool _originallyMinimized;
    private bool _originallyMaximized;
    private AttachmentMode _mode;
    private readonly WinEventCallback _positionCallback;
    private nint _positionHook;

    public WindowAttachment()
    {
        _positionCallback = OnPositionChanged;
    }

    public bool IsAttached => _handle != 0;
    public bool IsAlive => _handle != 0 && IsWindow(_handle);
    public nint Handle => IsAlive ? _handle : 0;
    public event Action? PositionChanged;

    public static IReadOnlyList<WindowCandidate> Enumerate(
        nint shellHandle,
        string targetExecutablePath = "",
        uint targetProcessId = 0)
    {
        var currentProcess = Environment.ProcessId;
        var desktopWindow = GetShellWindow();
        var candidates = new List<WindowCandidate>();
        EnumWindows((handle, ignored) =>
        {
            if (handle == shellHandle || handle == desktopWindow ||
                !IsWindowVisible(handle) || IsCloaked(handle))
            {
                return true;
            }
            _ = GetWindowThreadProcessId(handle, out var windowProcessId);
            if (windowProcessId == currentProcess || IsClaimed(handle)) return true;
            var length = GetWindowTextLength(handle);
            if (length <= 0) return true;
            var titleBuffer = new StringBuilder(length + 1);
            _ = GetWindowText(handle, titleBuffer, titleBuffer.Capacity);
            var title = titleBuffer.ToString().Trim();
            if (title.Length == 0) return true;
            var processName = "application";
            var windowExecutablePath = "";
            try
            {
                using var process = Process.GetProcessById((int)windowProcessId);
                processName = process.ProcessName;
                windowExecutablePath = process.MainModule?.FileName ?? "";
            }
            catch (Exception error) when (error is ArgumentException or InvalidOperationException or Win32Exception) { }
            if ((targetProcessId != 0 || !string.IsNullOrWhiteSpace(targetExecutablePath)) &&
                windowProcessId != targetProcessId &&
                !SameExecutablePath(windowExecutablePath, targetExecutablePath))
            {
                return true;
            }
            candidates.Add(new WindowCandidate(
                handle,
                title,
                processName,
                windowProcessId,
                windowExecutablePath,
                WindowIcon(handle, windowExecutablePath),
                IsUtilityWindow(handle)));
            return true;
        }, 0);
        return candidates
            .OrderBy(candidate => candidate.IsUtilityWindow)
            .ThenBy(candidate => candidate.ProcessName, StringComparer.OrdinalIgnoreCase)
            .ThenBy(candidate => candidate.Title, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    public static bool SameExecutablePath(string left, string right)
    {
        if (string.IsNullOrWhiteSpace(left) || string.IsNullOrWhiteSpace(right)) return false;
        try
        {
            return string.Equals(
                Path.GetFullPath(left),
                Path.GetFullPath(right),
                StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception error) when (
            error is ArgumentException or NotSupportedException or PathTooLongException)
        {
            return false;
        }
    }

    private static bool IsUtilityWindow(nint handle)
    {
        var extendedStyle = GetWindowLongPtr(handle, GwlExStyle).ToInt64();
        if ((extendedStyle & WsExAppWindow) != 0) return false;
        return (extendedStyle & WsExToolWindow) != 0 ||
            GetWindow(handle, GwOwner) != 0;
    }

    private static bool IsCloaked(nint handle)
    {
        var result = DwmGetWindowAttribute(
            handle,
            DwmwaCloaked,
            out var cloaked,
            Marshal.SizeOf<uint>());
        return result == 0 && cloaked != 0;
    }

    private static Icon? WindowIcon(nint handle, string executablePath)
    {
        foreach (var kind in new[] { IconSmall2, IconSmall, IconBig })
        {
            if (SendMessageTimeout(
                    handle, WmGetIcon, kind, 0, SmtoAbortIfHung, 100, out var result) != 0 &&
                result != 0)
            {
                var icon = CloneSharedIcon(result);
                if (icon is not null) return icon;
            }
        }
        foreach (var index in new[] { GclpHIconSm, GclpHIcon })
        {
            var result = GetClassIcon(handle, index);
            var icon = CloneSharedIcon(result);
            if (icon is not null) return icon;
        }
        if (string.IsNullOrWhiteSpace(executablePath)) return null;
        try { return Icon.ExtractAssociatedIcon(executablePath); }
        catch (Exception error) when (error is ArgumentException or Win32Exception) { return null; }
    }

    private static Icon? CloneSharedIcon(nint handle)
    {
        if (handle == 0) return null;
        try
        {
            using var borrowed = Icon.FromHandle(handle);
            return (Icon)borrowed.Clone();
        }
        catch (ArgumentException)
        {
            return null;
        }
    }

    private static nint GetClassIcon(nint handle, int index) =>
        nint.Size == 8
            ? GetClassLongPtr(handle, index)
            : new nint(unchecked((int)GetClassLong(handle, index)));

    private static string ClaimName(nint handle) => $"Local\\MO_SHELL_WINDOW_{handle.ToInt64():X}";

    private static Mutex ClaimWindow(nint handle)
    {
        Mutex claim;
        bool created;
        try
        {
            // Existence, not thread ownership, is the reservation. This also
            // excludes two attachment owners running on the same UI thread.
            claim = new Mutex(false, ClaimName(handle), out created);
        }
        catch (Exception error) when (error is UnauthorizedAccessException or IOException)
        {
            throw new InvalidOperationException("Windows could not reserve this window for MO Shell", error);
        }
        if (created) return claim;
        claim.Dispose();
        throw new InvalidOperationException("This window is already attached to another MO Shell");
    }

    private static bool IsClaimed(nint handle)
    {
        try
        {
            if (!Mutex.TryOpenExisting(ClaimName(handle), out var claim)) return false;
            claim.Dispose();
            return true;
        }
        catch (UnauthorizedAccessException) { return true; }
    }

    public void Attach(WindowCandidate candidate, AttachmentMode mode, nint host)
    {
        if (candidate.Handle == 0 || !IsWindow(candidate.Handle))
            throw new InvalidOperationException("The selected window is no longer available");
        if (host == candidate.Handle || !IsWindow(host))
            throw new InvalidOperationException("The MO Shell host is no longer available");
        _ = GetWindowThreadProcessId(candidate.Handle, out var processId);
        if (processId != candidate.ProcessId)
            throw new InvalidOperationException("The selected window has changed; select it again");

        // Reserve before detaching or snapshotting: another Shell cannot steal
        // this HWND or capture its already-mutated placement. Kernel lifetime
        // releases the reservation even if the owning Shell process exits.
        Mutex? claim = ClaimWindow(candidate.Handle);
        try
        {
            Detach();
            var placement = new NativeWindowPlacement
            {
                Length = (uint)Marshal.SizeOf<NativeWindowPlacement>(),
            };
            if (!GetWindowPlacement(candidate.Handle, ref placement))
                ThrowLastError("read the selected window placement");
            if (!GetWindowRect(candidate.Handle, out var rectangle))
                ThrowLastError("read the selected window bounds");

            _handle = candidate.Handle;
            _claim = claim;
            claim = null;
            _host = host;
            _mode = mode;
            _originalParent = GetParent(_handle);
            _originalStyle = GetWindowLongPtr(_handle, GwlStyle);
            _originalExStyle = GetWindowLongPtr(_handle, GwlExStyle);
            _originalRect = rectangle;
            _originalPlacement = placement;
            _originallyVisible = IsWindowVisible(_handle);
            _originallyMinimized = IsIconic(_handle);
            _originallyMaximized = IsZoomed(_handle);

            if (_originallyMinimized || _originallyMaximized)
                _ = ShowWindow(_handle, SwShowNoActivate);
            if (!IsWindow(_handle))
                throw new InvalidOperationException("The selected window closed while being restored");

            if (mode == AttachmentMode.Embedded)
            {
                SetParentChecked(_handle, host);
                var chrome = WsPopup | WsCaption | WsThickFrame | WsMinimizeBox | WsMaximizeBox | WsSysMenu;
                SetWindowLongPtrChecked(_handle, GwlStyle, new nint((_originalStyle.ToInt64() & ~chrome) | WsChild));
                if (!SetWindowPos(_handle, 0, 0, 0, 1, 1, SwpNoActivate | SwpFrameChanged | SwpShowWindow))
                    ThrowLastError("prepare the embedded window");
            }
            else
            {
                SetWindowLongPtrChecked(_handle, GwlHwndParent, host);
                // Changing the owner does not itself establish its z-order.
                // Promote once on attachment; subsequent pane moves preserve it.
                if (!SetWindowPos(_handle, 0, 0, 0, 0, 0,
                        0x0001 | 0x0002 | SwpNoActivate | SwpFrameChanged))
                    ThrowLastError("place the selected window above its Shell owner");
                StartPositionTracking();
            }
        }
        catch
        {
            RestoreSnapshot();
            Clear();
            throw;
        }
        finally
        {
            claim?.Dispose();
        }
    }

    public Size Sync(Rectangle paneClient, Point shellScreen)
    {
        if (!IsAttached || !IsAlive) return Size.Empty;
        var x = _mode == AttachmentMode.Embedded ? paneClient.X : shellScreen.X + paneClient.X;
        var y = _mode == AttachmentMode.Embedded ? paneClient.Y : shellScreen.Y + paneClient.Y;
        var width = Math.Max(1, paneClient.Width);
        var height = Math.Max(1, paneClient.Height);
        if (IsIconic(_handle) || IsZoomed(_handle))
        {
            _ = ShowWindow(_handle, SwShowNoActivate);
        }
        if (_mode == AttachmentMode.Managed &&
            GetWindowRect(_handle, out var current) &&
            current.Left == x && current.Top == y &&
            current.Right - current.Left == width &&
            current.Bottom - current.Top == height)
        {
            return new Size(width, height);
        }
        var flags = SwpNoActivate | SwpShowWindow;
        if (_mode == AttachmentMode.Managed) flags |= SwpNoZOrder;
        if (!SetWindowPos(
                _handle,
                0,
                x,
                y,
                width,
                height,
                flags))
        {
            ThrowLastError("move the attached window");
        }
        if (!GetWindowRect(_handle, out var actual))
        {
            ThrowLastError("read the attached window bounds");
        }
        return new Size(
            Math.Max(1, actual.Right - actual.Left),
            Math.Max(1, actual.Bottom - actual.Top));
    }

    public void Hide()
    {
        if (IsAlive) _ = ShowWindow(_handle, SwHide);
    }

    public void Show()
    {
        if (IsAlive) _ = ShowWindow(_handle, SwShowNoActivate);
    }

    public void Detach()
    {
        if (!IsAttached) return;
        StopPositionTracking();
        if (IsAlive) RestoreSnapshot();
        Clear();
    }

    public void ForgetClosed() => Clear();

    private void RestoreSnapshot()
    {
        if (_handle == 0 || !IsWindow(_handle)) return;
        if (_mode == AttachmentMode.Embedded)
        {
            SetParentUnchecked(_handle, _originalParent);
        }
        else
        {
            _ = SetWindowLongPtr(_handle, GwlHwndParent, _originalParent);
        }
        _ = SetWindowLongPtr(_handle, GwlStyle, _originalStyle);
        _ = SetWindowLongPtr(_handle, GwlExStyle, _originalExStyle);
        _originalPlacement.Length = (uint)Marshal.SizeOf<NativeWindowPlacement>();
        var flags = SwpNoActivate | SwpFrameChanged;
        var restoreNormalBounds = !_originallyMinimized && !_originallyMaximized;
        if (restoreNormalBounds)
        {
            _ = SetWindowPlacement(_handle, ref _originalPlacement);
        }
        _ = SetWindowPos(
            _handle,
            (_originalExStyle.ToInt64() & WsExTopmost) != 0 ? HwndTopmost : HwndNotTopmost,
            _originalRect.Left,
            _originalRect.Top,
            Math.Max(1, _originalRect.Right - _originalRect.Left),
            Math.Max(1, _originalRect.Bottom - _originalRect.Top),
            flags);
        if (!restoreNormalBounds)
        {
            _ = SetWindowPlacement(_handle, ref _originalPlacement);
        }
        if (!_originallyVisible) _ = ShowWindow(_handle, SwHide);
    }

    private void Clear()
    {
        StopPositionTracking();
        _handle = 0;
        _host = 0;
        _originalParent = 0;
        _originalStyle = 0;
        _originalExStyle = 0;
        _originalRect = default;
        _originalPlacement = default;
        _originallyVisible = false;
        _originallyMinimized = false;
        _originallyMaximized = false;
        _claim?.Dispose();
        _claim = null;
    }

    private void StartPositionTracking()
    {
        _ = GetWindowThreadProcessId(_handle, out var processId);
        _positionHook = SetWinEventHook(
            EventObjectLocationChange,
            EventObjectLocationChange,
            0,
            _positionCallback,
            processId,
            0,
            WineventOutofcontext);
        if (_positionHook == 0)
        {
            throw new Win32Exception(
                Marshal.GetLastPInvokeError(),
                "Windows could not lock the selected window to the MO Shell pane");
        }
    }

    private void StopPositionTracking()
    {
        var hook = _positionHook;
        _positionHook = 0;
        if (hook != 0) _ = UnhookWinEvent(hook);
    }

    private void OnPositionChanged(
        nint hook,
        uint eventType,
        nint handle,
        int objectId,
        int childId,
        uint eventThread,
        uint eventTime)
    {
        if (hook == _positionHook && eventType == EventObjectLocationChange &&
            handle == _handle && objectId == ObjidWindow && childId == 0)
        {
            PositionChanged?.Invoke();
        }
    }

    private static void SetParentChecked(nint child, nint parent)
    {
        Marshal.SetLastPInvokeError(0);
        var previous = SetParent(child, parent);
        var error = Marshal.GetLastPInvokeError();
        if (previous == 0 && error != 0) throw new Win32Exception(error, "Windows refused embedded attachment");
    }

    private static void SetParentUnchecked(nint child, nint parent)
    {
        Marshal.SetLastPInvokeError(0);
        _ = SetParent(child, parent);
    }

    private static void SetWindowLongPtrChecked(nint handle, int index, nint value)
    {
        Marshal.SetLastPInvokeError(0);
        var previous = SetWindowLongPtr(handle, index, value);
        var error = Marshal.GetLastPInvokeError();
        if (previous == 0 && error != 0) throw new Win32Exception(error, "Windows refused the embedded window style");
    }

    private static void ThrowLastError(string action)
    {
        var error = Marshal.GetLastPInvokeError();
        throw new Win32Exception(error, $"Windows could not {action}");
    }

    public void Dispose() => Detach();

    private delegate bool EnumWindowsCallback(nint handle, nint parameter);
    private delegate void WinEventCallback(
        nint hook,
        uint eventType,
        nint handle,
        int objectId,
        int childId,
        uint eventThread,
        uint eventTime);

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativePoint
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeWindowPlacement
    {
        public uint Length;
        public uint Flags;
        public uint ShowCommand;
        public NativePoint MinimumPosition;
        public NativePoint MaximumPosition;
        public NativeRect NormalPosition;
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool EnumWindows(EnumWindowsCallback callback, nint parameter);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsWindow(nint handle);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsWindowVisible(nint handle);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsIconic(nint handle);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsZoomed(nint handle);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(nint handle, StringBuilder text, int count);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextLength(nint handle);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(nint handle, out uint processId);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern nint SetWinEventHook(
        uint eventMin,
        uint eventMax,
        nint module,
        WinEventCallback callback,
        uint processId,
        uint threadId,
        uint flags);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UnhookWinEvent(nint hook);

    [DllImport("user32.dll")]
    private static extern nint GetShellWindow();

    [DllImport("user32.dll")]
    private static extern nint GetWindow(nint handle, uint command);

    [DllImport("dwmapi.dll")]
    private static extern int DwmGetWindowAttribute(
        nint handle,
        uint attribute,
        out uint value,
        int valueSize);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern nint SendMessageTimeout(
        nint handle,
        uint message,
        nuint wParam,
        nint lParam,
        uint flags,
        uint timeout,
        out nint result);

    [DllImport("user32.dll", EntryPoint = "GetClassLongPtrW")]
    private static extern nint GetClassLongPtr(nint handle, int index);

    [DllImport("user32.dll", EntryPoint = "GetClassLongW")]
    private static extern uint GetClassLong(nint handle, int index);

    [DllImport("user32.dll")]
    private static extern nint GetParent(nint handle);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern nint SetParent(nint child, nint parent);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    private static extern nint GetWindowLongPtr(nint handle, int index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern nint SetWindowLongPtr(nint handle, int index, nint value);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetWindowRect(nint handle, out NativeRect rectangle);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetWindowPlacement(nint handle, ref NativeWindowPlacement placement);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPlacement(nint handle, ref NativeWindowPlacement placement);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(
        nint handle,
        nint insertAfter,
        int x,
        int y,
        int width,
        int height,
        uint flags);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ShowWindow(nint handle, int command);
}
