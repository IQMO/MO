using System.Diagnostics;
using System.Runtime.InteropServices;

namespace MoShell.Native;

// Display-synchronous replacement for the Windows Forms timer behind the Shell's
// surface animation. WM_TIMER rounds to the 15.6 ms Windows quantum, so a 15 ms
// budget on a 144 Hz display beats against the refresh grid. This pulse is released
// by the DirectComposition compositor clock (one tick per refresh); Interval stays
// a millisecond budget rounded to whole refreshes, and Tick runs on the UI thread.
// Without that clock it is an ordinary Windows Forms timer.
internal sealed class DisplayTimer : IDisposable
{
    [DllImport("dcomp.dll")]
    private static extern uint DCompositionWaitForCompositorClock(uint count, IntPtr[]? handles, uint timeoutMs);

    private const uint CompositorTick = 0; // WAIT_OBJECT_0 + the handle count (none); a timeout is 258
    private const uint TickTimeoutMs = 100; // a sleeping display still releases the final frame
    private readonly System.Windows.Forms.Timer _fallback = new();
    private readonly ManualResetEventSlim _running = new(false);
    private SynchronizationContext? _context;
    private Thread? _thread;
    private volatile bool _enabled, _disposed, _pending, _clockMissing, _fallbackActive;
    private bool _calibrated;
    private volatile int _interval = 31;
    private double _periodMs = 1000d / 60;

    public event EventHandler? Tick
    {
        add { _tick += value; _fallback.Tick += value; }
        remove { _tick -= value; _fallback.Tick -= value; }
    }
    private EventHandler? _tick;

    public bool Enabled => _fallbackActive ? _fallback.Enabled : _enabled;

    public int Interval
    {
        get => _interval;
        set { _interval = value; _fallback.Interval = value; }
    }

    public void Start()
    {
        if (_disposed) return;
        _context ??= SynchronizationContext.Current;
        if (_clockMissing || _context == null)
        {
            _fallbackActive = true;
            _fallback.Start();
            return;
        }
        _fallbackActive = false;
        _enabled = true;
        if (_thread == null)
        {
            _thread = new Thread(Pulse) { IsBackground = true, Name = "mo-shell-display" };
            _thread.Start();
        }
        _running.Set();
    }

    public void Stop()
    {
        _enabled = false;
        _running.Reset();
        _fallback.Stop();
    }

    public void Dispose()
    {
        _disposed = true;
        _enabled = false;
        _running.Set();
        _fallback.Dispose();
    }

    private void Pulse()
    {
        long last = 0;
        var count = 0;
        while (!_disposed)
        {
            _running.Wait();
            if (_disposed) break;
            uint signal;
            try
            {
                signal = DCompositionWaitForCompositorClock(0, null, TickTimeoutMs);
            }
            catch (Exception error) when (error is DllNotFoundException or EntryPointNotFoundException)
            {
                _clockMissing = true;
                _context?.Post(_ =>
                {
                    if (!_enabled || _disposed) return;
                    _enabled = false;
                    _fallbackActive = true;
                    _fallback.Start();
                }, null);
                return;
            }
            var now = Stopwatch.GetTimestamp();
            if (signal == CompositorTick && last != 0)
            {
                var gap = (now - last) * 1000d / Stopwatch.Frequency;
                // The first gap is the refresh period; later ones refine it, and a
                // missed tick (about two periods) is not evidence of a new period.
                if (!_calibrated)
                {
                    _periodMs = gap;
                    _calibrated = true;
                }
                else if (gap > _periodMs * .7 && gap < _periodMs * 1.3) _periodMs += (gap - _periodMs) * .2;
            }
            last = signal == CompositorTick ? now : 0;
            if (!_enabled)
            {
                count = 0;
                continue;
            }
            if (signal == CompositorTick && ++count < Math.Max(1, (int)Math.Round(_interval / _periodMs))) continue;
            count = 0;
            if (_pending) continue; // the UI thread is behind: drop the frame, never queue it
            _pending = true;
            _context!.Post(_ =>
            {
                _pending = false;
                if (_enabled && !_disposed) _tick?.Invoke(this, EventArgs.Empty);
            }, null);
        }
    }
}
