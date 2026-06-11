from core.profile import Profile


def test_profile_context_includes_identity_file(tmp_path):
    profile = Profile(_path=str(tmp_path / "mo.db"), user_name="Ada")
    profile.ensure_operator_profile()
    (tmp_path / "profile" / "identity.md").write_text("# MO Identity\n\n- Runtime provider is not identity.\n", encoding="utf-8")

    context = profile.build_profile_context(max_chars=5000)

    assert "### identity.md" in context
    assert "Runtime provider is not identity" in context


def test_sync_operator_profile_files_updates_generated_identity_lines(tmp_path):
    profile = Profile(_path=str(tmp_path / "mo.db"), user_name="Ada")
    profile.ensure_operator_profile()
    operator_path = tmp_path / "profile" / "operator.md"
    thinking_path = tmp_path / "profile" / "thinking_model.md"

    profile.user_name = "Grace"
    profile.sync_operator_profile_files()

    assert "# Operator Profile — Grace" in operator_path.read_text(encoding="utf-8")
    assert "- **Name:** Grace" in operator_path.read_text(encoding="utf-8")
    assert thinking_path.read_text(encoding="utf-8").splitlines()[0] == "# Grace Thinking Model"


def test_profile_context_keeps_recent_learning_tail(tmp_path):
    profile = Profile(_path=str(tmp_path / "mo.db"), user_name="Ada")
    profile.ensure_operator_profile()
    learning_path = tmp_path / "profile" / "learning.md"
    learning_path.write_text(
        "# Operator Learning\n\n"
        + "old preference line\n" * 120
        + "## recent\n- evolution: Latest dynamic correction must be visible to MO\n",
        encoding="utf-8",
    )

    context = profile.build_profile_context(max_chars=2600)

    assert "profile middle truncated" in context
    assert "Latest dynamic correction must be visible to MO" in context


def test_profile_learning_deduplicates_repeated_insights(tmp_path):
    profile = Profile(_path=str(tmp_path / "mo.db"), user_name="Ada")
    profile.ensure_operator_profile()
    insight = {"core_traits": ["Verify current reality before claims"]}

    profile.append_profile_learning("feedback:a", insight)
    profile.append_profile_learning("feedback:b", insight)

    text = (tmp_path / "profile" / "learning.md").read_text(encoding="utf-8")
    assert text.count("Verify current reality before claims") == 1
    assert "category:evidence" in text


def test_profile_learning_deduplicates_existing_compact_group(tmp_path):
    profile = Profile(_path=str(tmp_path / "mo.db"), user_name="Ada")
    profile.ensure_operator_profile()
    learning_path = tmp_path / "profile" / "learning.md"
    learning_path.write_text(
        "# Operator Learning\n\n## old\n- core_traits: Verify current reality before claims; Keep scope tight\n",
        encoding="utf-8",
    )

    profile.append_profile_learning(
        "feedback:new",
        {"core_traits": ["Verify current reality before claims", "Run focused tests before reporting done"]},
    )

    text = learning_path.read_text(encoding="utf-8")
    assert text.count("Verify current reality before claims") == 1
    assert "Run focused tests before reporting done" in text


def test_profile_learning_updates_behavior_rules_without_system_mutation(tmp_path):
    profile = Profile(_path=str(tmp_path / "mo.db"), user_name="Ada")
    profile.ensure_operator_profile()

    profile.append_profile_learning("feedback:behavior", {"core_traits": ["Verify logs before claiming runtime success"]})

    behavior = (tmp_path / "profile" / "behavior.md").read_text(encoding="utf-8")
    context = profile.build_profile_context(max_chars=5000)
    assert "# MO Behavioral Learning" in behavior
    assert "[evidence] Verify logs before claiming runtime success" in behavior
    assert "### behavior.md" in context
    assert "Verify logs before claiming runtime success" in context
