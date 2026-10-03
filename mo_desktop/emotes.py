"""Emote / formation library for the cube companion.

Each emote modulates the cubes on top of the idle breathe: a per-cube position jolt
(in cube-edge units, kept small so nothing clips) plus a brightness delta. Worm is one
of many. Emotes are transient — played once over a duration; the cube clears the active
emote when it elapses, so this stays compatible with the sprite-cache renderer (no new
sprites, just where each cube is pasted and which brightness bucket it uses).

Callable signature: ``fn(i, t, dur) -> (dx, dy, dbright)`` where ``i`` is the
cube index, ``t`` is elapsed seconds, ``dur`` the total duration; ``dx``/``dy``
are offsets in cube-edge units and ``dbright`` is added to the cube's 0..1
brightness. Studio-authored emotes can also use ``EmoteSpec`` keyframes.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable

_CORNERS = [(-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)]


@dataclass(frozen=True)
class CubeKeyframe:
    """One cube's offset/brightness/alpha delta at normalized ``time`` 0..1."""

    time: float
    dx: float = 0.0
    dy: float = 0.0
    brightness: float = 0.0
    alpha: float = 0.0


@dataclass(frozen=True)
class EmoteSpec:
    """Per-cube authored motion for the studio and vetted product emotes."""

    name: str
    duration: float
    keyframes_by_cube: dict[int, tuple[CubeKeyframe, ...]]
    easing: str = "linear"
    loopable: bool = False


def _corner(i: int) -> tuple[float, float]:
    if 0 <= i < len(_CORNERS):
        return _CORNERS[i]
    if i == 4:
        return (0.0, 0.0)
    a = (i - 4) * 2.399963229728653  # golden-angle spread for extra cubes
    return (math.cos(a), math.sin(a))


def _env(t: float, dur: float) -> float:
    """Velocity-smooth rise-and-fall envelope, 0 -> 1 -> 0."""
    if dur <= 0:
        return 0.0
    wave = math.sin(math.pi * max(0.0, min(1.0, t / dur)))
    return wave * wave


def poke(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    cx, cy = _corner(i)
    return (cx * 0.35 * e, cy * 0.35 * e, 0.45 * e)


def jump(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    return (0.0, -0.6 * e, 0.25 * e)


def happy(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    return (0.0, -0.25 * e * (0.5 + 0.5 * math.sin(t * 11 + i)), 0.35 * e)


def moody(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    return (0.0, 0.28 * e, -0.4 * e)


def thinking(i: int, t: float, dur: float) -> tuple[float, float, float]:
    """A coherent small twist and breath of the formation, settling at each lap."""
    e = _env(t, dur)
    cx, cy = _corner(i)
    phase = 2 * math.pi * t / max(.001, dur)
    twist = .13 * math.sin(phase) * e
    spread = .10 * e
    return (cx * spread - cy * twist, cy * spread + cx * twist, .04 * e)


def alert(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    p = 1.0 if dur <= 0 else max(0.0, min(1.0, t / dur))
    # Two smooth light pulses. The former binary frame switch was the only
    # deliberately discontinuous built-in emote and visibly read as a blink.
    pulse = math.exp(-(((p - 0.24) / 0.10) ** 2)) + 0.72 * math.exp(-(((p - 0.58) / 0.13) ** 2))
    return (0.0, -0.05 * e, 0.55 * min(1.0, pulse) * e)


def worm(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    s = max(0.0, math.sin(t * 6.0 - i * 0.9))  # staggered ripple across the cubes
    return (0.0, -0.5 * e * s, 0.2 * e * s)


def wiggle(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    return (0.32 * e * math.sin(t * 18.0), 0.03 * e * math.sin(t * 9.0 + i), 0.18 * e)


def nod(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    return (0.0, 0.34 * e * math.sin(t * 7.0), 0.12 * e)


def bounce(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    lift = 0.5 - 0.5 * math.cos(t * 12.0)
    return (0.0, -0.55 * e * lift, 0.22 * e)


def shake(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    return (0.26 * e * math.sin(t * 24.0 + i * 0.35), 0.025 * e * math.sin(t * 12.0), 0.10 * e)


def spin(i: int, t: float, dur: float) -> tuple[float, float, float]:
    e = _env(t, dur)
    a = t * 7.0 + i * 1.5708   # each cube orbits its base a quarter-phase apart
    return (0.30 * e * math.cos(a), 0.30 * e * math.sin(a), 0.12 * e)


# Around the cluster, not across it: TL -> TR -> BR -> BL. The LED rides this ring like a
# shield turning around the cube. One lap per emote duration, so it turns slowly.
_CHARGE_RING: tuple[int, ...] = (0, 1, 3, 2)
_HEARTBEAT_SECONDS = 1.2


def _bell(p: float, centre: float, width: float) -> float:
    """A Gaussian on a CIRCLE: distance wraps, so the value at p=1 is the value at p=0. A plain
    Gaussian's tail does not, and the beat then jumps at every cycle boundary — a visible flick."""
    d = abs(p - centre)
    return math.exp(-((min(d, 1.0 - d) / width) ** 2))


def _heartbeat(t: float) -> float:
    """lub-dub: a strong beat, a softer echo, then rest. 0..1, continuous across the wrap."""
    p = (t % _HEARTBEAT_SECONDS) / _HEARTBEAT_SECONDS
    return min(1.0, _bell(p, 0.06, 0.05) + 0.55 * _bell(p, 0.24, 0.07))


def app_heartbeat(i: int, t: float, dur: float) -> tuple[float, float, float]:
    """Release, small counter-twist and precise return after an app handoff."""
    dx, dy, _ = thinking(i, t, dur)
    return (dx * 1.45, dy * 1.45, .04 * _env(t, dur))


def charge_accent(i: int, t: float, dur: float) -> float:
    """How strongly cube ``i`` is the travelling LED right now, 0..1. The cube paints this
    much of the fixed charge colour over its own, so the light reads as moving around the ring."""
    if dur <= 0 or i not in _CHARGE_RING:
        return 0.0
    total = len(_CHARGE_RING)
    slot = _CHARGE_RING.index(i)
    pos = (t / dur) * total % total
    gap = min((slot - pos) % total, (pos - slot) % total)
    value = max(0.0, min(1.0, 1.0 - gap))
    return value * value * (3.0 - 2.0 * value)


def charging(i: int, t: float, dur: float) -> tuple[float, float, float]:
    """Docked at home, topping up. The cluster HOLDS STILL — a thing that is charging does not
    wander — and only its light moves: one LED travels the ring around it (a shield turning),
    pulsing on a slow heartbeat. The lit cube is painted in the skin's charge colour (see
    ``charge_accent``); the rest stay MO's own colour, dimmed. The cube replays this for as long
    as it sits at home, so the recharge is constant."""
    beat = _heartbeat(t)
    lit = charge_accent(i, t, dur)
    return (0.0, 0.0, -0.34 * (1.0 - lit) + 0.10 * beat)


# emote callable -> per-cube accent strength. Only emotes that recolour a cube appear here.
ACCENTS: dict[Any, Callable] = {}

# Emotes during which the cube must not drift: the idle breathe/bob is suppressed and only the
# emote's own output moves. Declared here so ``_cube_state`` needs no per-emote branch.
STILL: set = set()

# Emotes that must never end on their own. The cube rebases them instead of clearing them, so
# there is no bare frame between one cycle and the next — that gap is a visible flick.
LOOPS: set = set()


def holds_still(emote: Any) -> bool:
    """True when this emote suppresses the idle bob — the cube is deliberately motionless."""
    return emote in STILL


def loops(emote: Any) -> bool:
    """True when this emote repeats seamlessly rather than expiring."""
    return emote in LOOPS


def accent(emote: Any, i: int, t: float, dur: float) -> float:
    """Accent-colour strength for cube ``i``, 0 when the emote does not recolour anything."""
    fn = ACCENTS.get(emote)
    if fn is None:
        return 0.0
    try:
        return max(0.0, min(1.0, float(fn(i, t, dur))))
    except Exception:
        return 0.0


def ping(i: int, t: float, dur: float) -> tuple[float, float, float]:
    """A notification landing: two quick swells of light, the cluster breathing outward with each.

    Distinct from ``alert``, which is a hard on/off blink for something wrong. A ping is arrival.
    """
    p = 1.0 if dur <= 0 else max(0.0, min(1.0, t / dur))
    cx, cy = _corner(i)
    swell = math.exp(-(((p - 0.12) / 0.07) ** 2)) + 0.70 * math.exp(-(((p - 0.42) / 0.09) ** 2))
    swell = min(1.0, swell)
    return (cx * 0.16 * swell, cy * 0.16 * swell, 0.55 * swell)


def swallow(i: int, t: float, dur: float) -> tuple[float, float, float]:
    """The gulp that closes a file drop: the open mouth snaps shut on the file (cubes
    rush inward, brightening), then springs back out and rings down to rest."""
    p = 1.0 if dur <= 0 else max(0.0, min(1.0, t / dur))
    cx, cy = _corner(i)
    if p < 0.35:
        pull = (p / 0.35) ** 2          # accelerate inward — the bite
    else:
        r = (p - 0.35) / 0.65
        pull = (1.0 - r) * math.cos(r * 9.0) * math.exp(-r * 3.5)   # elastic settle
    return (-cx * 0.55 * pull, -cy * 0.55 * pull, 0.5 * max(0.0, pull))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _ease(kind: str, t: float) -> float:
    t = max(0.0, min(1.0, t))
    if kind == "ease_in_out":
        return t * t * (3.0 - 2.0 * t)
    if kind == "ease_out":
        return 1.0 - (1.0 - t) * (1.0 - t)
    if kind == "ease_in":
        return t * t
    return t


def _sample_spec(spec: EmoteSpec, i: int, t: float, dur: float) -> tuple[float, float, float, float]:
    duration = max(0.001, float(spec.duration or dur or 0.001))
    pos = (float(t) % duration) / duration if spec.loopable else max(0.0, min(1.0, float(t) / duration))
    frames = tuple(sorted(spec.keyframes_by_cube.get(i, ()), key=lambda k: k.time))
    if not frames:
        return (0.0, 0.0, 0.0, 0.0)
    if pos <= frames[0].time:
        k = frames[0]
        return (k.dx, k.dy, k.brightness, k.alpha)
    if pos >= frames[-1].time:
        k = frames[-1]
        return (k.dx, k.dy, k.brightness, k.alpha)
    prev = frames[0]
    for nxt in frames[1:]:
        if pos <= nxt.time:
            span = max(0.001, nxt.time - prev.time)
            u = _ease(spec.easing, (pos - prev.time) / span)
            return (
                _lerp(prev.dx, nxt.dx, u),
                _lerp(prev.dy, nxt.dy, u),
                _lerp(prev.brightness, nxt.brightness, u),
                _lerp(prev.alpha, nxt.alpha, u),
            )
        prev = nxt
    return (0.0, 0.0, 0.0, 0.0)


def sample(emote: Any, i: int, t: float, dur: float, *, cube_count: int = 4) -> tuple[float, float, float, float]:
    """Return ``dx, dy, brightness_delta, alpha_delta`` for any supported emote."""
    if isinstance(emote, EmoteSpec):
        return _sample_spec(emote, i, t, dur)
    if callable(emote):
        dx, dy, db = emote(i, t, dur)
        return (float(dx), float(dy), float(db), 0.0)
    return (0.0, 0.0, 0.0, 0.0)


# name -> (callable/spec, default duration seconds)
EMOTES: dict[str, tuple[Any, float]] = {
    "poke": (poke, 0.65),
    "jump": (jump, 0.75),
    "happy": (happy, 1.2),
    "moody": (moody, 1.4),
    "thinking": (thinking, 2.4),
    "alert": (alert, 1.0),
    "worm": (worm, 1.4),
    "wiggle": (wiggle, 0.65),
    "nod": (nod, 0.8),
    "bounce": (bounce, 0.9),
    "shake": (shake, 0.6),
    "spin": (spin, 1.0),
    "swallow": (swallow, 0.7),
    "charging": (charging, 3.6),   # one slow lap of the ring = exactly three heartbeats
    "app_heartbeat": (app_heartbeat, 0.9),
    "ping": (ping, 0.9),
}

ACCENTS[charging] = charge_accent
STILL.add(charging)
LOOPS.add(charging)

# Desktop events -> emote name(s). A tuple with several names means "pick one at random"
# so a repeated event (a finished job or an incoming notification) stays lively. This
# only categorizes the emotes already in EMOTES by WHEN they fire — no new machinery.
EMOTE_EVENTS: dict[str, tuple[str, ...]] = {
    "job_done": ("happy", "jump"),
    "notify": ("alert",),
    "notify_worker": ("alert", "jump"),
    "notify_email": ("happy",),
    "notify_telegram": ("jump",),
    "lock": ("jump",),
    "listening": ("worm",),
    "file_drop": ("swallow",),
    "notice": ("ping",),          # a glance landed: the notice spine's default announcement
    "charging": ("charging",),    # verified power/attachment presence: the recharge loop
    # Generic MCP defaults. Desktop owns their visual presentation,
    # while an unfamiliar future MCP still communicates connection/work/result/error state.
    "mcp_connecting": ("thinking",),
    "mcp_active": ("ping",),
    "mcp_acting": ("thinking",),
    "mcp_success": ("happy",),
    "mcp_warning": ("alert",),
    "mcp_error": ("moody",),
}


def for_event(event: str, rng: Any = None) -> str:
    """Emote name for a desktop event (poke, job_done, notify_worker, notify_email…).
    Categories with several emotes return a random one for variety; ``""`` if unknown."""
    choices = EMOTE_EVENTS.get(str(event or "").strip().lower())
    if not choices:
        return ""
    if len(choices) == 1:
        return choices[0]
    import random
    return (rng or random).choice(choices)


def register(name: str, emote: Callable | EmoteSpec, duration: float | None = None,
             *, replace: bool = False) -> bool:
    """Register an emote without poking the registry directly.

    Replacement is explicit so studio experiments cannot accidentally override a
    vetted product emote.
    """
    key = str(name or getattr(emote, "name", "") or "").strip()
    if not key:
        return False
    if key in EMOTES and not replace:
        return False
    dur = float(duration if duration is not None else getattr(emote, "duration", 0.0) or 0.0)
    if dur <= 0:
        return False
    EMOTES[key] = (emote, dur)
    return True


def get(name: str) -> tuple:
    """Return ``(fn, duration)`` for an emote, or ``(None, 0.0)`` if unknown."""
    return EMOTES.get(str(name), (None, 0.0))


def names() -> tuple:
    return tuple(EMOTES)
