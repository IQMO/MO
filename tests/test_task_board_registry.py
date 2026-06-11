from core.tasking.task_board import TaskBoard, TaskItem
from core.tasking.task_board_registry import TaskBoardRegistry


def test_task_board_registry_tracks_surfaces_and_events_without_replacing_truth():
    registry = TaskBoardRegistry()
    main = TaskBoard(turn_id="turn-main", tasks=[TaskItem("1", "Inspect", "active")])
    goal = TaskBoard(turn_id="turn-goal", source="goal", tasks=[TaskItem("g1", "Goal", "active")])

    registry.set_board("main", main)
    registry.set_board("goal:1", goal)
    event = registry.record_event("main", main, update="created")

    assert registry.get_board("main") is main
    assert registry.get_board("goal:1") is goal
    assert event["type"] == "taskboard_update"
    assert event["surface"] == "main"
    assert registry.recent_events(surface="main", limit=1)[0]["board_id"] == main.board_id
    assert registry.recent_events(surface="goal:1", limit=1) == []


def test_task_board_registry_clear_is_surface_local():
    registry = TaskBoardRegistry()
    main = TaskBoard(tasks=[TaskItem("1", "Inspect", "active")])
    goal = TaskBoard(source="goal", tasks=[TaskItem("g1", "Goal", "active")])
    registry.set_board("main", main)
    registry.set_board("goal", goal)

    registry.clear_board("main")

    assert registry.get_board("main") is None
    assert registry.get_board("goal") is goal
