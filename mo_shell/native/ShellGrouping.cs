using System.Diagnostics;
using System.Runtime.InteropServices;

namespace MoShell.Native;

// Transient presentation coordination only. Every HWND still owns its original
// bridge, terminal and attachment. Nothing here is persisted or routed to Agent.
internal sealed partial class ShellForm
{
    private const string PeerProperty = "MO_SHELL_COMPACT_PEER_V1";
    private const string GroupProperty = "MO_SHELL_COMPACT_GROUP_V1";
    private const string CollapsedProperty = "MO_SHELL_COMPACT_VISIBLE_V1";
    private const string BusyProperty = "MO_SHELL_COMPACT_BUSY_V1";
    private const string ColorProperty = "MO_SHELL_COMPACT_COLOR_V1";
    private const long GroupMessageTag = 0x4d4f4701;
    private readonly List<nint> _groupMembers = new();
    private nint _groupLeader, _returnGroup, _initialGroupTarget;
    private int _instanceOrdinal;
    private long _groupIdleSince = Environment.TickCount64;
    private uint _groupLastInput;
    private bool _groupIdleShown, _leavingGroup;
    private bool _groupDiscoveryPending;
    private static readonly Dictionary<nint, uint> VerifiedPeers = new();

    private enum GroupCommand { Join = 1, Adopt, Park, Release, Open, Remove, Drop }
    [StructLayout(LayoutKind.Sequential)]
    private struct GroupPacket { public int Command, X, Y; public nint Other; }
    [StructLayout(LayoutKind.Sequential)]
    private struct CopyPacket { public nint Tag; public int Size; public nint Data; }
    [StructLayout(LayoutKind.Sequential)]
    private struct LastInput { public uint Size, Time; }

    private void InitializeGroupIdentity()
    {
        using var gate = new Mutex(false, @"Local\MO.Shell.Compact.Identity.V1");
        var acquired = false;
        try
        {
            try { acquired = gate.WaitOne(TimeSpan.FromSeconds(1)); }
            catch (AbandonedMutexException) { acquired = true; }
            if (!acquired) return;
            _instanceOrdinal = Peers().Select(h => (int)GetProp(h, PeerProperty)).DefaultIfEmpty(0).Max() + 1;
            SetProp(Handle, PeerProperty, _instanceOrdinal);
            _groupLeader = Handle;
            _surface.SetInstanceOrdinal(_instanceOrdinal);
            PublishGroupState();
        }
        finally { if (acquired) gate.ReleaseMutex(); }
    }

    private Rectangle InitialPeerBounds(Rectangle ordinary, Rectangle workArea)
    {
        var peer = Peers().Where(h => h != Handle && GetProp(h, CollapsedProperty) != 0)
            .Select(h => GetProp(h, GroupProperty)).Where(h => h != 0 && h != Handle)
            .Distinct().OrderBy(h => (int)GetProp(h, PeerProperty)).FirstOrDefault();
        if (peer == 0 || !GetWindowRect(peer, out var rect)) return ordinary;
        _initialGroupTarget = peer;
        return FitToWorkArea(new Rectangle(rect.Left, rect.Top - ordinary.Height - 6,
            ordinary.Width, ordinary.Height), workArea);
    }

    private void PublishGroupState()
    {
        if (!IsHandleCreated || _instanceOrdinal == 0) return;
        SetProp(Handle, GroupProperty, _groupLeader == 0 ? Handle : _groupLeader);
        SetProp(Handle, CollapsedProperty, _collapsed && _themeReady ? 1 : 0);
        SetProp(Handle, BusyProperty, _surface.Working ? 1 : 0);
        SetProp(Handle, ColorProperty, unchecked((nint)(uint)_surface.InstanceColor.ToArgb()));
    }

    private static bool IsPeer(nint handle)
    {
        if (handle == 0 || GetProp(handle, PeerProperty) == 0) return false;
        GetWindowThreadProcessId(handle, out var pid);
        if (VerifiedPeers.TryGetValue(handle, out var known) && known == pid) return true;
        try
        {
            using var process = Process.GetProcessById((int)pid);
            var valid = string.Equals(process.MainModule?.FileName, Environment.ProcessPath,
                StringComparison.OrdinalIgnoreCase);
            if (valid)
            {
                if (VerifiedPeers.Count > 256) VerifiedPeers.Clear();
                VerifiedPeers[handle] = pid;
            }
            return valid;
        }
        catch (Exception error) when (error is ArgumentException or InvalidOperationException or System.ComponentModel.Win32Exception)
        { return false; }
    }

    private static List<nint> Peers()
    {
        var peers = new List<nint>();
        EnumWindows((handle, _) => { if (IsPeer(handle)) peers.Add(handle); return true; }, 0);
        return peers;
    }

    private bool SendGroup(nint target, GroupCommand command, Point point = default, nint other = default)
    {
        if (!IsPeer(target)) return false;
        var packet = new GroupPacket { Command = (int)command, X = point.X, Y = point.Y, Other = other };
        if (target == Handle) { ReceiveGroup(Handle, packet); return true; }
        var data = Marshal.AllocHGlobal(Marshal.SizeOf<GroupPacket>());
        var copy = Marshal.AllocHGlobal(Marshal.SizeOf<CopyPacket>());
        try
        {
            Marshal.StructureToPtr(packet, data, false);
            Marshal.StructureToPtr(new CopyPacket { Tag = (nint)GroupMessageTag,
                Size = Marshal.SizeOf<GroupPacket>(), Data = data }, copy, false);
            return SendMessageTimeout(target, 0x004A, Handle, copy, 0x0002, 150, out var result) != 0 && result != 0;
        }
        finally { Marshal.FreeHGlobal(copy); Marshal.FreeHGlobal(data); }
    }

    private bool HandleGroupMessage(ref Message message)
    {
        if (message.Msg != 0x004A || message.LParam == 0) return false;
        var copy = Marshal.PtrToStructure<CopyPacket>(message.LParam);
        if (copy.Tag != (nint)GroupMessageTag) return false;
        message.Result = 0;
        if (copy.Size != Marshal.SizeOf<GroupPacket>() || copy.Data == 0 || !IsPeer(message.WParam)) return true;
        var packet = Marshal.PtrToStructure<GroupPacket>(copy.Data);
        // Defer mutations until the sender has released SendMessage. Nested peer
        // adoption cannot deadlock another GUI thread or re-enter a layout switch.
        var sender = message.WParam;
        BeginInvoke(() => ReceiveGroup(sender, packet));
        message.Result = 1;
        return true;
    }

    private void ReceiveGroup(nint sender, GroupPacket packet)
    {
        if (IsDisposed) return;
        var point = new Point(packet.X, packet.Y);
        switch ((GroupCommand)packet.Command)
        {
            case GroupCommand.Join:
                if (!_collapsed || _groupLeader != Handle) return;
                var incomingLeader = GetProp(sender, GroupProperty);
                var members = Peers().Where(h => h == Handle || h == sender ||
                    GetProp(h, GroupProperty) == Handle || GetProp(h, GroupProperty) == incomingLeader)
                    .Where(h => GetProp(h, CollapsedProperty) != 0).Distinct().ToArray();
                _groupMembers.Clear();
                _groupMembers.AddRange(members);
                foreach (var member in members.Where(h => h != Handle))
                    SendGroup(member, GroupCommand.Adopt, other: Handle);
                RefreshGroupMembers();
                break;
            case GroupCommand.Adopt:
                if (!_collapsed || !IsPeer(packet.Other)) return;
                _groupLeader = packet.Other;
                _returnGroup = packet.Other;
                _groupMembers.Clear();
                _surface.SetGroupMembers(Array.Empty<ShellGroupMember>());
                PublishGroupState();
                if (_groupLeader != Handle) { SendWindowEffect(false); Hide(); }
                else { ShowWindow(Handle, 4); _groupDiscoveryPending = true; }
                break;
            case GroupCommand.Park:
                if (sender != _groupLeader) return;
                Location = point;
                break;
            case GroupCommand.Release:
            case GroupCommand.Open:
                if (sender != _groupLeader && sender != Handle) return;
                _returnGroup = _groupLeader == Handle ? packet.Other : _groupLeader;
                _groupLeader = Handle;
                _groupMembers.Clear();
                _surface.SetGroupMembers(Array.Empty<ShellGroupMember>());
                Size = CompactSize;
                Location = point;
                ApplyWindowRegion();
                PublishGroupState();
                // Form.Show focuses topmost forms even with ShowWithoutActivation.
                // Native reveal keeps the originating thread's drag capture intact.
                ShowWindow(Handle, 4);
                SendWindowEffect(true);
                if ((GroupCommand)packet.Command == GroupCommand.Open) SwitchCollapsed(false);
                break;
            case GroupCommand.Drop:
                if (sender == _returnGroup || sender == Handle) TryMergeNearbyGroup();
                break;
            case GroupCommand.Remove:
                _groupMembers.Remove(sender);
                RefreshGroupMembers();
                break;
        }
        _groupIdleSince = Environment.TickCount64;
        _groupIdleShown = false;
    }

    private void RequestGroupJoin(nint target)
    {
        if (!_collapsed || target == Handle || !IsPeer(target)) return;
        var leader = GetProp(target, GroupProperty);
        if (leader != 0 && leader != Handle && GetProp(leader, CollapsedProperty) != 0)
            SendGroup(leader, GroupCommand.Join);
    }

    private void TryMergeNearbyGroup()
    {
        if (!_collapsed || _groupLeader != Handle) return;
        var near = Rectangle.Inflate(Bounds, 22, 22);
        var peer = Peers().Where(h => h != Handle && GetProp(h, GroupProperty) == h &&
                GetProp(h, CollapsedProperty) != 0)
            .FirstOrDefault(h => GetWindowRect(h, out var r) && near.IntersectsWith(r.Rectangle));
        if (peer != 0) RequestGroupJoin(peer);
    }

    private void RefreshGroupMembers(bool discover = false)
    {
        if (!_collapsed || _groupLeader != Handle) return;
        if (discover)
        {
            _groupMembers.Clear();
            _groupMembers.AddRange(Peers().Where(h => h == Handle || GetProp(h, GroupProperty) == Handle));
        }
        _groupMembers.RemoveAll(h => GetProp(h, PeerProperty) == 0 || GetProp(h, CollapsedProperty) == 0);
        var members = _groupMembers.OrderByDescending(h => (int)GetProp(h, PeerProperty))
            .Select(h => new ShellGroupMember(h, (int)GetProp(h, PeerProperty),
                Color.FromArgb(unchecked((int)GetProp(h, ColorProperty))),
                GetProp(h, BusyProperty) != 0, GetProp(h, AttachmentProperty) != 0)
                { Title = PeerAttachmentTitle(h) }).ToArray();
        var wasGrouped = _surface.HasCompactGroup;
        var previousCenter = _surface.MemberCenter(Handle);
        var entering = new Dictionary<nint, Rectangle>();
        foreach (var member in members)
        {
            if (!GetWindowRect(member.Handle, out var rect)) continue;
            var area = _surface.CompactPairBounds();
            area.Offset(rect.Left - Left, rect.Top - Top);
            entering[member.Handle] = area;
        }
        var changed = _surface.SetGroupMembers(members.Length > 1 ? members : Array.Empty<ShellGroupMember>(), entering);
        if (members.Length <= 1) _groupMembers.Clear();
        if (changed)
        {
            if (wasGrouped && members.Length <= 1)
            {
                var center = _surface.MemberCenter(Handle);
                Location = new Point(Left + (int)Math.Round(previousCenter.X - center.X),
                    Top + (int)Math.Round(previousCenter.Y - center.Y));
            }
            RefreshCompactPresentation();
        }
    }

    private void ParkGroupMembers()
    {
        if (_groupLeader != Handle || _groupMembers.Count < 2) return;
        foreach (var member in _groupMembers.Where(h => h != Handle))
        {
            var row = _surface.GroupMemberBounds(member);
            var pair = _surface.CompactPairBounds();
            SendGroup(member, GroupCommand.Park,
                new Point(Left + row.Left - pair.Left, Top + row.Top - pair.Top));
        }
    }

    private void OpenGroupMember(nint member)
    {
        var row = _surface.GroupMemberBounds(member);
        var pair = _surface.CompactPairBounds();
        ReleaseGroupMember(member, new Point(Left + row.Left - pair.Left, Top + row.Top - pair.Top), open: true);
    }

    private Rectangle ReturnCompactBounds()
    {
        var ordinary = CompactBoundsFor(_expandedBounds);
        if (!IsPeer(_returnGroup)) return ordinary;
        var leader = GetProp(_returnGroup, GroupProperty);
        if (leader == Handle || !IsPeer(leader) || GetProp(leader, CollapsedProperty) == 0 ||
            !GetWindowRect(leader, out var rect)) return ordinary;
        var members = Peers().Where(h => h == Handle || GetProp(h, GroupProperty) == leader &&
            GetProp(h, CollapsedProperty) != 0).OrderByDescending(h => (int)GetProp(h, PeerProperty)).ToArray();
        var destination = _surface.CompactPairBounds(Array.IndexOf(members, Handle));
        var pair = _surface.CompactPairBounds();
        ordinary.Location = new Point(rect.Left + destination.Left - pair.Left,
            rect.Top + destination.Top - pair.Top);
        return FitToWorkArea(ordinary, Screen.FromRectangle(rect.Rectangle).WorkingArea);
    }

    private void ExtractGroupMember(nint member, Point cursor, bool completed)
    {
        if (!IsPeer(member)) return;
        if (_groupMembers.Contains(member)) StartConnectionRelease(member);
        var location = new Point(cursor.X - _surface.PairSize.Width / 2, cursor.Y - _surface.PairSize.Height / 2);
        if (_groupMembers.Contains(member)) ReleaseGroupMember(member, location, open: false);
        else _ = SetWindowPos(member, 0, location.X, location.Y, 0, 0, 0x0001 | 0x0004 | 0x0010 | 0x4000);
        if (completed) SendGroup(member, GroupCommand.Drop);
    }

    private void ReleaseGroupMember(nint member, Point location, bool open)
    {
        if (!_groupMembers.Contains(member)) return;
        var remaining = _groupMembers.Where(h => h != member && IsPeer(h)).ToArray();
        var nextLeader = member == Handle ? remaining.FirstOrDefault() : Handle;
        if (member == Handle)
        {
            foreach (var peer in remaining) SendGroup(peer, GroupCommand.Adopt, other: nextLeader);
            // Adoption is queued on each peer. Discovery happens on its normal
            // watcher after all peers have published the new leader.
        }
        else _groupMembers.Remove(member);
        SendGroup(member, open ? GroupCommand.Open : GroupCommand.Release, location, nextLeader);
        if (member != Handle) RefreshGroupMembers();
    }

    private void LeaveCompactGroup()
    {
        if (_leavingGroup || !IsHandleCreated || _instanceOrdinal == 0) return;
        _leavingGroup = true;
        try
        {
            if (_groupLeader == Handle && _groupMembers.Count > 1)
                ReleaseGroupMember(Handle, Location, open: false);
            else if (_groupLeader != 0 && _groupLeader != Handle)
                SendGroup(_groupLeader, GroupCommand.Remove);
            _groupLeader = Handle;
            _groupMembers.Clear();
            _surface.SetGroupMembers(Array.Empty<ShellGroupMember>());
            PublishGroupState();
        }
        finally { _leavingGroup = false; }
    }

    private void CheckGroupState()
    {
        if (_instanceOrdinal == 0 || !_collapsed) return;
        if (_groupLeader != Handle)
        {
            if (IsPeer(_groupLeader)) return;
            // A leader exit/crash cannot strand another terminal off screen.
            _groupLeader = Handle;
            _returnGroup = 0;
            PublishGroupState();
            ShowWindow(Handle, 4);
            RefreshCompactPresentation();
        }
        if (_groupDiscoveryPending)
        {
            _groupDiscoveryPending = false;
            RefreshGroupMembers(discover: true);
        }
        else if (_groupMembers.Count > 1) RefreshGroupMembers();
        var input = new LastInput { Size = (uint)Marshal.SizeOf<LastInput>() };
        GetLastInputInfo(ref input);
        var active = _surface.GroupWorking || _surface.GroupTouched || input.Time != _groupLastInput;
        _groupLastInput = input.Time;
        if (active)
        {
            _groupIdleSince = Environment.TickCount64;
            _groupIdleShown = false;
            _surface.StopGroupIdleMotion();
        }
        else if (!_groupIdleShown && _groupMembers.Count > 1 && Environment.TickCount64 - _groupIdleSince >= 30000)
        {
            _groupIdleShown = true;
            _surface.StartGroupIdleMotion();
        }
    }

    private void RemoveGroupProperties()
    {
        foreach (var key in new[] { PeerProperty, GroupProperty, CollapsedProperty, BusyProperty, ColorProperty })
            RemoveProp(Handle, key);
    }

    private static string PeerAttachmentTitle(nint peer)
    {
        var target = GetProp(peer, AttachmentProperty);
        if (target == 0) return "";
        var title = new System.Text.StringBuilder(256);
        GetWindowText(target, title, title.Capacity);
        return title.ToString();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct GroupRect
    {
        public int Left, Top, Right, Bottom;
        public readonly Rectangle Rectangle => Rectangle.FromLTRB(Left, Top, Right, Bottom);
    }
    private delegate bool EnumGroupWindow(nint handle, nint parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumGroupWindow callback, nint parameter);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(nint handle, out GroupRect rect);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(nint handle, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "GetPropW")]
    private static extern nint GetProp(nint handle, string key);
    [DllImport("user32.dll")] private static extern nint SendMessageTimeout(nint handle, uint message, nint wparam, nint lparam, uint flags, uint timeout, out nint result);
    [DllImport("user32.dll")] private static extern bool GetLastInputInfo(ref LastInput input);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(nint handle, nint after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] private static extern bool ShowWindow(nint handle, int command);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(nint handle, System.Text.StringBuilder text, int maximum);
}
