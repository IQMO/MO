from types import SimpleNamespace

from core.tasking.task_board import TaskBoard, TaskItem
from core.workers import WorkerRegistry
from interface.turn_runner import TurnRunnerMixin


class FakeApp:
    def __init__(self):
        self.invalidated = 0

    def invalidate(self):
        self.invalidated += 1


class TurnHarness(TurnRunnerMixin):
    def __init__(self, gateway):
        self.agent = SimpleNamespace(workers=WorkerRegistry(), autosave_session=lambda: setattr(self, "autosaved", True))
        self.gateway = gateway
        self._app = FakeApp()
        self._current_turn_cancel_event = None
        self._active_main_worker_id = ""
        self.busy = False
        self.activity_text = ""
        self.activity_started_at = 0.0
        self.board_text = "stale"
        self._ghost_enabled = True
        self._show_tool_activity = False
        self.lines = []
        self.processed_queue = False

    def _proposal_chat_text(self, proposal):
        return proposal

    def _add_response_block(self, text):
        self.lines.append(("response", text))

    def _add(self, style, text):
        self.lines.append((style, text))

    def _add_fragments_line(self, fragments):
        self.lines.append(("fragments", fragments))

    def _process_next_queued_input(self):
        self.processed_queue = True


class BoardGateway:
    def __init__(self):
        self.last_task_board = None
        self.callbacks = None

    def should_show_task_board(self, _user_input):
        return True

    def run_turn(self, user_input, **callbacks):
        self.callbacks = callbacks
        callbacks["on_activity"]("thinking")
        if callbacks.get("on_proposal"):
            callbacks["on_proposal"]("Proposal visible")
        self.last_task_board = TaskBoard(
            "turn-1",
            "build_create",
            [
                TaskItem("1", "Inspect real files", "completed", ["read_file:real.py"]),
                TaskItem("2", "Verify real behavior", "active"),
            ],
        )
        callbacks["on_board_update"]("999 tasks forged")
        callbacks["on_board_event"]({"type": "taskboard_update", "rendered": "999 tasks forged from event"})
        callbacks["on_token"]("x")
        return f"done {user_input}"


def test_turn_runner_uses_gateway_board_truth_not_callback_markup():
    harness = TurnHarness(BoardGateway())

    harness._run_turn_thread("build real thing")

    assert "Inspect real files" in harness.board_text
    assert "Verify real behavior" in harness.board_text
    assert "forged" not in harness.board_text
    assert ("response", "Proposal visible") not in harness.lines
    assert harness.lines[-1] == ("response", "done build real thing")
    assert harness.busy is False
    assert harness.activity_text == ""
    assert harness.processed_queue is True
    assert harness.autosaved is True


def test_turn_runner_does_not_request_ghost_proposal_for_direct_taskboard_turn():
    class ProposalProbeGateway:
        last_task_board = None

        def __init__(self):
            self.on_proposal = "unset"

        def should_show_task_board(self, _user_input):
            return True

        def run_turn(self, _user_input, **callbacks):
            self.on_proposal = callbacks.get("on_proposal")
            return "direct"

    gateway = ProposalProbeGateway()
    harness = TurnHarness(gateway)

    harness._run_turn_thread("build real thing")

    assert gateway.on_proposal is None
    assert ("response", "direct") in harness.lines


def test_turn_runner_keeps_ghost_proposal_for_explicit_ghost_route():
    class ProposalProbeGateway:
        last_task_board = None

        def __init__(self):
            self.on_proposal = None
            self.route_source = ""

        def should_show_task_board(self, _user_input):
            return True

        def run_turn(self, _user_input, **callbacks):
            self.on_proposal = callbacks.get("on_proposal")
            self.route_source = callbacks.get("route_source", "")
            if self.on_proposal:
                self.on_proposal("Ghost-scoped handoff")
            return "routed"

    gateway = ProposalProbeGateway()
    harness = TurnHarness(gateway)
    record = harness.agent.workers.create(kind="main", source="ghost", route="main", objective="build", state="accepted")
    harness._active_main_worker_id = record.id

    harness._run_turn_thread("build real thing")

    assert callable(gateway.on_proposal)
    assert gateway.route_source == "ghost"
    assert ("response", "Ghost-scoped handoff") not in harness.lines
    assert ("response", "routed") in harness.lines


def test_turn_runner_formats_gateway_exception_as_mo_interface_error():
    class FailingGateway:
        last_task_board = None

        def should_show_task_board(self, _user_input):
            return False

        def run_turn(self, _user_input, **_callbacks):
            raise RuntimeError("boom")

    harness = TurnHarness(FailingGateway())
    harness._run_turn_thread("hello")

    response = [text for kind, text in harness.lines if kind == "response"][-1]
    assert response.startswith("MO interface error: turn failed")
    assert "where: TUI turn runner" in response
    assert "RuntimeError" not in response


def test_turn_runner_keeps_lazy_no_board_when_gateway_says_no_board():
    class NoBoardGateway:
        last_task_board = None

        def should_show_task_board(self, _user_input):
            return False

        def run_turn(self, _user_input, **callbacks):
            assert callable(callbacks["on_board_update"])
            assert callbacks["on_proposal"] is None
            callbacks["on_activity"]("thinking")
            return "simple answer"

    harness = TurnHarness(NoBoardGateway())
    harness._run_turn_thread("hello")

    assert harness.board_text == ""
    assert ("response", "simple answer") in harness.lines


def test_turn_runner_reflects_runtime_created_board_even_when_visibility_probe_says_no_board():
    class RuntimeBoardGateway:
        def __init__(self):
            self.last_task_board = None

        def should_show_task_board(self, _user_input):
            return False

        def run_turn(self, _user_input, **callbacks):
            self.last_task_board = TaskBoard(
                "turn-runtime",
                "runtime board",
                [TaskItem("1", "Inspect real runtime work", "active")],
            )
            callbacks["on_board_update"]("ignored callback markup")
            return "runtime answer"

    harness = TurnHarness(RuntimeBoardGateway())
    harness._run_turn_thread("small request that used tools")

    assert "Inspect real runtime work" in harness.board_text
    assert "ignored callback markup" not in harness.board_text
    assert ("response", "runtime answer") in harness.lines


def test_turn_runner_keeps_completed_board_visible():
    """Board stays visible after final report so user sees honest completed state."""
    class CompleteBoardGateway:
        def __init__(self):
            self.last_task_board = None

        def should_show_task_board(self, _user_input):
            return True

        def run_turn(self, _user_input, **callbacks):
            self.last_task_board = TaskBoard(
                "turn-2",
                "build_create",
                [TaskItem("1", "Inspect", "completed", ["read_file:a.py"]), TaskItem("2", "Report", "completed", ["final_answer_delivered"])],
            )
            callbacks["on_board_update"]("ignored")
            return "Detailed report stays visible."

    harness = TurnHarness(CompleteBoardGateway())
    harness._run_turn_thread("build")
    footer = ["".join(text for _style, text in fragments) for kind, fragments in harness.lines if kind == "fragments"]

    # Board now stays visible — no visual gap.
    assert harness.board_text != ""
    assert ("response", "Detailed report stays visible.") in harness.lines
    assert footer == []
    assert not any("Recap:" in str(line) for line in harness.lines)


def test_turn_runner_does_not_print_recap_for_final_answer_evidence():
    class FinalAnswerOnlyGateway:
        def __init__(self):
            self.last_task_board = None

        def should_show_task_board(self, _user_input):
            return True

        def run_turn(self, _user_input, **callbacks):
            self.last_task_board = TaskBoard(
                "turn-3",
                "deep_review",
                [TaskItem("1", "Inspect", "completed", ["final_answer:concrete_evidence"]), TaskItem("2", "Report", "completed", ["final_answer:findings_evidence"])],
            )
            callbacks["on_board_update"]("ignored")
            return "Findings\n\n6 files · 723 tests passing · uncommitted"

    harness = TurnHarness(FinalAnswerOnlyGateway())
    harness._run_turn_thread("review")
    footer = ["".join(text for _style, text in fragments) for kind, fragments in harness.lines if kind == "fragments"]

    assert footer == []
    assert ("response", "Findings\n\n6 files · 723 tests passing · uncommitted") in harness.lines
    assert not any("final_answer" in str(line) for line in footer)
    assert not any("Recap:" in str(line) for line in harness.lines)


def test_turn_runner_marks_active_main_worker_complete_with_result_card():
    gateway = BoardGateway()
    harness = TurnHarness(gateway)
    record = harness.agent.workers.create(kind="main", source="ghost", route="main", objective="review", state="accepted")
    harness._active_main_worker_id = record.id

    harness._run_turn_thread("review")

    stored = harness.agent.workers.get(record.id)
    assert stored.state == "completed"
    assert stored.result_summary == "done review"
    assert harness._active_main_worker_id == ""


def test_turn_runner_marks_active_main_worker_cancelled_on_abort():
    class AbortGateway:
        last_task_board = None

        def should_show_task_board(self, _user_input):
            return False

        def run_turn(self, _user_input, **_callbacks):
            return "[ABORTED] Current turn stopped."

    harness = TurnHarness(AbortGateway())
    record = harness.agent.workers.create(kind="main", source="ghost", route="main", objective="review", state="running")
    harness._active_main_worker_id = record.id

    harness._run_turn_thread("review")

    stored = harness.agent.workers.get(record.id)
    assert stored.state == "cancelled"
    assert stored.note == "main MO turn stopped"
    assert harness._active_main_worker_id == ""


def test_turn_runner_passes_ghost_route_source_to_gateway():
    class SourceGateway:
        last_task_board = None

        def __init__(self):
            self.route_source = ""

        def should_show_task_board(self, _user_input):
            return False

        def run_turn(self, _user_input, **callbacks):
            self.route_source = callbacks.get("route_source", "")
            return "routed"

    gateway = SourceGateway()
    harness = TurnHarness(gateway)
    record = harness.agent.workers.create(kind="main", source="ghost", route="main", objective="build", state="accepted")
    harness._active_main_worker_id = record.id

    harness._run_turn_thread("build")

    assert gateway.route_source == "ghost"


def test_low_balance_warns_once_below_threshold(monkeypatch):
    import core.provider.deepseek_balance as bal

    class _H(TurnRunnerMixin):
        def __init__(self):
            self._low_balance_notified = False
            self.added = []
            self.agent = SimpleNamespace(active_provider=object())

        def _add(self, style, text):
            self.added.append((style, text))

    # Below threshold -> exactly one colored notice, then never again (one-time).
    monkeypatch.setattr(bal, "balance_amount", lambda prov: 1.73)
    h = _H()
    h._maybe_warn_low_balance()
    assert len(h.added) == 1
    assert h.added[0][0] == "class:low-balance"
    assert "1.73" in h.added[0][1] and "2.00" in h.added[0][1]
    h._maybe_warn_low_balance()
    assert len(h.added) == 1

    # At/above threshold and unknown balance -> no notice.
    monkeypatch.setattr(bal, "balance_amount", lambda prov: 5.0)
    h2 = _H(); h2._maybe_warn_low_balance(); assert h2.added == []
    monkeypatch.setattr(bal, "balance_amount", lambda prov: None)
    h3 = _H(); h3._maybe_warn_low_balance(); assert h3.added == []


def test_model_fallback_notice_only_on_change():
    class _H(TurnRunnerMixin):
        def __init__(self, prov, model, reason=""):
            self.added = []
            self.agent = SimpleNamespace(provider_name=prov, model=model, last_fallback_notice=reason)
        def _add(self, style, text):
            self.added.append((style, text))

    # No change -> no notice.
    h = _H("deepseek", "deepseek-v4-pro")
    h._maybe_notify_model_change(("deepseek", "deepseek-v4-pro"))
    assert h.added == []

    # Changed mid-turn -> one colored notice naming the new model + reason.
    h2 = _H("opencode-bigpickle", "big-pickle", "Switched to opencode-bigpickle/big-pickle: balance/route blocked")
    h2._maybe_notify_model_change(("deepseek", "deepseek-v4-pro"))
    assert len(h2.added) == 1
    assert h2.added[0][0] == "class:model-fallback"
    assert "big-pickle" in h2.added[0][1] and "balance/route" in h2.added[0][1]


def test_parked_work_cleared_by_clearly_new_request_kept_for_greeting():
    import core.agent.agent as agent_mod

    class _Stub:
        def __init__(self, resume=False):
            self._pending_interrupted_work = {"user": "remove old dead path and test keys"}
            self._resume = resume
        def _looks_like_interrupted_resume_request(self, _t):
            return self._resume

    fn = agent_mod.Agent._pending_interrupted_work_context
    # Clearly-new substantive request -> park cleared, nothing injected.
    s = _Stub()
    assert fn(s, "remove old dead paths and test the keys") == ""
    assert s._pending_interrupted_work == {}
    # Short/ambiguous return -> park kept, "resume?" hint injected.
    s2 = _Stub()
    out = fn(s2, "you tell me")
    assert "Paused Interrupted Work" in out and s2._pending_interrupted_work != {}
    # Explicit resume -> park cleared, resume instruction.
    s3 = _Stub(resume=True)
    assert "resume the parked work" in fn(s3, "proceed please") and s3._pending_interrupted_work == {}
