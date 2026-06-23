"""Python-AST skeleton compression: contract + the measured code win."""
from pathlib import Path

from core.code_skeleton import code_skeleton, strip_read_file_numbering
from core.tool_compress import compress


_SAMPLE = '''"""Module docstring line one.

More detail.
"""
import os
from pathlib import Path

X = 1


def foo(a, b=2):
    """Foo does a thing."""
    total = 0
    for i in range(a):
        total += i * b
    return total


class Bar(Base):
    """Bar holds state."""

    def method(self, x):
        y = x + 1
        return y
'''


def test_skeleton_keeps_structure_drops_bodies_and_shrinks():
    sk = code_skeleton(_SAMPLE)
    # signatures + imports + docstring kept
    assert "def foo(a, b=2): ..." in sk
    assert "class Bar(Base):" in sk
    assert "def method(self, x): ..." in sk
    assert "import os" in sk and "X = 1" in sk
    assert "Module docstring line one." in sk
    # bodies dropped
    assert "total += i * b" not in sk
    assert "y = x + 1" not in sk
    # real savings
    assert len(sk) < len(_SAMPLE)


def test_skeleton_handles_read_file_numbered_format():
    numbered = "[Lines 1-3 of 3]\n  1: def f(x):\n  2:     return x + 1\n  3: "
    assert strip_read_file_numbering(numbered) == "def f(x):\n    return x + 1\n"
    sk = code_skeleton(numbered)
    assert "def f(x): ..." in sk
    assert "return x + 1" not in sk


def test_skeleton_passthrough_on_non_python_or_no_gain():
    assert code_skeleton("ERROR: something failed\nstack trace line\n" * 5) == ""  # not python
    assert code_skeleton("") == ""
    assert code_skeleton("def f(): ...") == ""  # already minimal -> no gain


def test_skeleton_respects_max_chars():
    # Real bodies so the skeleton is smaller than the input but still exceeds max_chars.
    big = "\n".join(f"def f{i}(a, b, c):\n    x = a + b + c\n    return x * {i}" for i in range(2000))
    sk = code_skeleton(big, max_chars=300)
    assert len(sk) <= 360 and "skeleton truncated" in sk


def test_skeleton_beats_current_numbered_read_compression_on_real_mo_code():
    """Head-to-head guard: source reads used to pass through MO's numbered-read
    compressor; the AST skeleton must keep structure while materially shrinking.
    """
    files = [
        Path("core/agent/agent.py"),
        Path("core/gateway.py"),
        Path("core/session/session_momentum.py"),
        Path("core/tool_compress.py"),
    ]
    raw_total = 0
    current_total = 0
    skeleton_total = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        numbered = "[Lines 1-{0} of {0}]\n".format(len(text.splitlines())) + "\n".join(
            f"{idx}: {line}" for idx, line in enumerate(text.splitlines(), 1)
        )
        current, stats = compress(numbered, min_bytes=0, pressure=0.0)
        skeleton = code_skeleton(numbered, max_chars=20_000)

        raw_total += len(numbered)
        current_total += len(current)
        skeleton_total += len(skeleton)

        assert stats is None or stats["saved_pct"] < 5
        assert "def " in skeleton or "class " in skeleton
        assert len(skeleton) < len(current)

    assert current_total == raw_total
    assert skeleton_total < raw_total * 0.25
