import queue
import threading
from types import SimpleNamespace

from core.workers import WorkerRegistry
from interface.queueing import QueueingMixin


class QueueHarness(QueueingMixin):
    def __init__(self):
        self.agent = SimpleNamespace(workers=WorkerRegistry(), process_slash_command=lambda _text: None)
        self.busy = False
        self._pending_inputs = queue.Queue()
        self._last_queued_input = None
        self._goal_queued = False
        self._current_turn_cancel_event = None
        self._busy_escape_count = 0
        self.lines = []
        self.handled = []
        self.goal_started = False

    def _add(self, style, text):
        self.lines.append((style, text))

    def _start_goal_thread(self):
        self.goal_started = True

    def _handle_input(self, text):
        self.handled.append(text)


def test_command_allowed_while_working_preserves_control_exceptions():
    assert QueueHarness._command_allowed_while_working("/exit") is True
    assert QueueHarness._command_allowed_while_working("/goal status") is True
    # /gp and /pg were removed (prompt enhancement is the Ctrl+E keybinding now).
    assert QueueHarness._command_allowed_while_working("/gp rough prompt") is False
    assert QueueHarness._command_allowed_while_working("/ghost ask") is False
    assert QueueHarness._command_allowed_while_working("/gh ask") is False
    assert QueueHarness._command_allowed_while_working("/ghot ask") is False
    assert QueueHarness._command_allowed_while_working("/ghostly wrong") is False
    assert QueueHarness._command_allowed_while_working("normal chat") is False


def test_queue_input_creates_worker_record_and_cancel_updates_it():
    harness = QueueHarness()

    harness._queue_input("fix queued bug")
    item = harness._last_queued_input
    worker_id = item["worker_id"]

    assert harness._pending_inputs.qsize() == 1
    assert harness.agent.workers.get(worker_id).state == "accepted"
    assert harness._cancel_last_queued_input() is True
    assert harness._pending_inputs.qsize() == 0
    assert harness.agent.workers.get(worker_id).state == "cancelled"


def test_promote_last_queued_input_to_steer_moves_it_to_front():
    harness = QueueHarness()
    harness.busy = True
    harness._queue_input("first")
    first = harness._last_queued_input
    harness._queue_input("second")

    harness._last_queued_input = first

    assert harness._promote_last_queued_input_to_steer() is True
    promoted = harness._pending_inputs.get_nowait()
    assert promoted is first
    assert promoted["steer"] is True


def test_process_next_queued_input_promotes_goal_and_main_records():
    harness = QueueHarness()
    goal = harness.agent.workers.create(kind="goal", source="user", route="background", objective="review", state="accepted")
    harness._pending_inputs.put({"text": "[GOAL_START]", "worker_id": goal.id})

    harness._process_next_queued_input()

    assert harness.goal_started is True
    assert harness.agent._goal_worker_id == goal.id
    assert harness.agent.workers.get(goal.id).state == "running"

    main = harness.agent.workers.create(kind="queue", source="ghost", route="queue", objective="fix", state="accepted")
    harness._pending_inputs.put({"text": "fix", "worker_id": main.id, "steer": True})
    harness._process_next_queued_input()

    assert harness.handled == ["fix"]
    assert harness._active_main_worker_id == main.id
    assert harness.agent.workers.get(main.id).note == "queued item promoted to MO"


def test_queue_control_messages_are_short_and_do_not_echo_long_request_text():
    harness = QueueHarness()
    harness.busy = True
    long_text = "MO, test yourself live from the current runtime. First verify Ghost can route a concrete build request, then report every detail."

    harness._queue_input(long_text)
    harness._promote_last_queued_input_to_steer()
    harness._request_current_turn_stop_for_steer()
    rendered = "\n".join(text for _style, text in harness.lines)

    assert "Queued next" in rendered
    assert "Queued request selected" in rendered
    assert "Stopping MO" not in rendered  # no cancel event in this unit path
    assert long_text[:40] not in rendered
    assert "then report every detail" not in rendered


def test_three_busy_esc_stops_current_turn_after_canceling_queue():
    harness = QueueHarness()
    harness.busy = True
    harness._current_turn_cancel_event = threading.Event()
    harness._queue_input("cancel then stop")

    assert harness._handle_busy_escape() is True
    assert harness._pending_inputs.qsize() == 0
    assert harness._current_turn_cancel_event.is_set() is False

    assert harness._handle_busy_escape() is True
    assert harness._current_turn_cancel_event.is_set() is False

    assert harness._handle_busy_escape() is True
    assert harness._current_turn_cancel_event.is_set() is True

    rendered = "\n".join(text for _style, text in harness.lines)
    assert "Queue canceled" in rendered
    assert "Esc 2/3" in rendered
    assert "Stopping MO" in rendered
