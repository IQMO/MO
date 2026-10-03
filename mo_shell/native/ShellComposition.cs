using System.ComponentModel;
using System.Runtime.InteropServices;

namespace MoShell.Native;

// Native counterpart of Desktop's per-pixel-alpha presentation boundary. The
// existing surface supplies every pixel and owns input, skin and frame timing.
internal sealed partial class ShellForm
{
    private const int LayeredStyle = 0x00080000;
    private bool _compactComposition, _presentingCompact, _compactFramePending;

    private void QueueCompactFrame()
    {
        if (!_collapsed || !IsHandleCreated || IsDisposed || _compactFramePending) return;
        _compactFramePending = true;
        BeginInvoke(() =>
        {
            _compactFramePending = false;
            if (!IsDisposed) PresentCompactFrame();
        });
    }

    private void PresentCompactFrame()
    {
        if (!_collapsed || !Visible || Opacity == 0 || _presentingCompact) return;
        _presentingCompact = true;
        try
        {
            if (!_compactComposition)
            {
                var style = GetWindowLong(Handle, -20);
                // Startup Opacity uses SetLayeredWindowAttributes. Windows requires
                // clearing/reapplying its style before accepting per-pixel alpha.
                SetWindowLong(Handle, -20, style & ~LayeredStyle);
                SetWindowLong(Handle, -20, style | LayeredStyle);
                _compactComposition = true;
            }
            using var bitmap = _surface.RenderCompactFrame();
            PresentAlphaFrame(this, bitmap);
        }
        finally { _presentingCompact = false; }
    }

    private static void PresentAlphaFrame(Form target, Bitmap bitmap)
    {
        var screen = GetDC(0);
        var memory = CreateCompatibleDC(screen);
        var pixels = bitmap.GetHbitmap(Color.FromArgb(0));
        var previous = SelectObject(memory, pixels);
        try
        {
            var position = target.Location;
            var size = bitmap.Size;
            var origin = Point.Empty;
            var blend = new AlphaBlend { Alpha = 255, Format = 1 };
            if (!UpdateLayeredWindow(target.Handle, screen, ref position, ref size,
                    memory, ref origin, 0, ref blend, 2))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        finally
        {
            SelectObject(memory, previous);
            DeleteObject(pixels);
            DeleteDC(memory);
            ReleaseDC(0, screen);
        }
    }

    private void EndCompactComposition()
    {
        if (!_compactComposition) return;
        SetWindowLong(Handle, -20, GetWindowLong(Handle, -20) & ~LayeredStyle);
        _compactComposition = false;
    }

    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    private struct AlphaBlend { public byte Operation, Flags, Alpha, Format; }
    [DllImport("user32.dll", EntryPoint = "GetWindowLongW")]
    private static extern int GetWindowLong(nint handle, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongW")]
    private static extern int SetWindowLong(nint handle, int index, int value);
    [DllImport("user32.dll")]
    private static extern nint GetDC(nint handle);
    [DllImport("user32.dll")]
    private static extern int ReleaseDC(nint handle, nint dc);
    [DllImport("gdi32.dll")]
    private static extern nint CreateCompatibleDC(nint dc);
    [DllImport("gdi32.dll")]
    private static extern bool DeleteDC(nint dc);
    [DllImport("gdi32.dll")]
    private static extern nint SelectObject(nint dc, nint value);
    [DllImport("gdi32.dll")]
    private static extern bool DeleteObject(nint value);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UpdateLayeredWindow(nint handle, nint screen, ref Point position,
        ref Size size, nint source, ref Point origin, uint key, ref AlphaBlend blend, uint flags);
}
