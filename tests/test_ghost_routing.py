from core.ghost.ghost_routing import enhance_route_objective, is_route_confirmation, is_route_rejection, recommend_ghost_route


def test_recommend_route_uses_main_when_idle():
    suggestion = recommend_ghost_route("please check the current task", main_busy=False)

    assert suggestion is not None
    assert suggestion.route == "main"
    assert suggestion.risky is False


def test_recommend_route_steers_current_adjustment_when_main_busy():
    suggestion = recommend_ghost_route("please fix this current issue", main_busy=True)

    assert suggestion is not None
    assert suggestion.route == "steer"
    assert "next safe checkpoint" in suggestion.offer_text()


def test_recommend_route_steers_keyboard_game_adjustment_when_main_busy():
    suggestion = recommend_ghost_route("the game is unplayable without mouse, enhance it for keyboard", main_busy=True)

    assert suggestion is not None
    assert suggestion.route == "steer"


def test_recommend_route_can_use_background_for_independent_safe_work():
    suggestion = recommend_ghost_route("scan docs for broken links", main_busy=True, goal_active=False)

    assert suggestion is not None
    assert suggestion.route == "background"


def test_recommend_route_does_not_start_second_background_goal():
    suggestion = recommend_ghost_route("scan docs for broken links", main_busy=True, goal_active=True)

    assert suggestion is not None
    assert suggestion.route == "queue"


def test_recommend_route_treats_ask_as_work():
    suggestion = recommend_ghost_route("ask main MO to verify the Wraith page", main_busy=False)

    assert suggestion is not None
    assert suggestion.route == "main"
    assert "verify the Wraith page" in suggestion.objective


def test_recommend_route_explicit_worker_can_run_background_when_main_idle():
    suggestion = recommend_ghost_route("run worker random new game task", main_busy=False)

    assert suggestion is not None
    assert suggestion.route == "background"
    assert "random new game task" in suggestion.objective


def test_recommend_route_treats_give_him_as_work_for_mo():
    suggestion = recommend_ghost_route("give him random task new game", main_busy=False)

    assert suggestion is not None
    assert suggestion.route == "main"
    assert suggestion.objective == "random task new game"


def test_recommend_route_treats_i_want_game_as_routeable_work():
    suggestion = recommend_ghost_route("I want angry cow 3d running game.", main_busy=False)

    assert suggestion is not None
    assert suggestion.route == "main"
    assert "angry cow" in suggestion.objective.lower()


def test_recommend_route_skips_conversational_strategy_questions():
    suggestion = recommend_ghost_route("what should we ask MO to do next?", main_busy=False)

    assert suggestion is None


def test_recommend_route_keeps_risky_work_with_main_gateway():
    suggestion = recommend_ghost_route("deploy and git push to production", main_busy=False)

    assert suggestion is not None
    assert suggestion.risky is True
    assert suggestion.route == "main"
    assert "high-risk" in suggestion.offer_text()


def test_route_confirmation_and_rejection_phrases():
    assert is_route_confirmation("yes")
    assert is_route_confirmation("go")
    assert is_route_confirmation("go ahead")
    assert is_route_rejection("no")
    assert is_route_rejection("cancel")


def test_route_objective_extracts_markdown_suggested_ask():
    response = "Roger.\n\n**Suggested ask:** Build an angry cow 3D runner web game in a standalone file."

    assert enhance_route_objective("yes route it", response) == "Build an angry cow 3D runner web game in a standalone file."


def test_route_objective_rejects_stale_suggested_ask_when_current_request_has_concrete_terms():
    response = "**Suggested ask:** Investigate and fix Phase 3A scroll/viewport lock."

    result = enhance_route_objective("I want angry cow 3d running game.", response)

    # Ghost routing no longer stamps "Build X" — passes through original text
    assert "angry cow" in result.lower()
    assert "phase 3a" not in result.lower()


def test_route_objective_turns_deliverable_request_into_build_prompt_without_ghost_text():
    result = enhance_route_objective("I want angry cow 3d running game.")

    # Ghost routing no longer stamps "Build X" — passes through the original
    assert "angry cow" in result.lower()
