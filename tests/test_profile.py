from core.profile import Profile


def test_profile_context_includes_identity_file(tmp_path):
    profile = Profile(_path=str(tmp_path / "mo.db"), user_name="Ada")
    profile.ensure_operator_profile()
    (tmp_path / "profile" / "identity.md").write_text("# MO Identity\n\n- Runtime provider is not identity.\n", encoding="utf-8")

    context = profile.build_profile_context(max_chars=5000)

    assert "### identity.md" in context
    assert "Runtime provider is not identity" in context


def test_profile_summary_is_light_and_points_to_full_file(tmp_path):
    # Design: inject a LIGHT summary + a pointer to read the full operator.md on
    # demand (the profile dir is read-allowed in the sandbox). Do NOT dump the
    # whole profile every turn.
    profile = Profile(_path=str(tmp_path / "mo.db"), user_name="Ada")
    profile.ensure_operator_profile()
    big_operator_md = "- operator/project detail line\n" * 400  # ~12k chars
    (tmp_path / "profile" / "operator.md").write_text(f"# Operator\n{big_operator_md}", encoding="utf-8")

    context = profile.build_profile_context()

    assert "read the full file with read_file" in context  # pointer to on-demand read
    assert "operator.md" in context
    assert len(context) < 6000, "profile context should be a light summary, not the whole file"


def test_profile_structured_project_paths_injected(tmp_path):
    # F3 regression: structured project paths reach the model, not just /profile.
    profile = Profile(_path=str(tmp_path / "mo.db"), user_name="Ada")
    profile.touch_project("/srv/widgets/checkout", name="checkout")
    context = profile.build_profile_context()
    assert "Known operator project paths" in context
    assert "/srv/widgets/checkout" in context


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


def test_profile_important_paths_round_trip(tmp_path):
    # Regression: important_paths is consumed by graph scoring but was never
    # serialized, so it always loaded back as [] and any set value was lost.
    path = str(tmp_path / "mo.db")
    profile = Profile(_path=path, user_name="Ada")
    profile.important_paths = ["core/agent", "interface/tui_app.py"]
    profile.save()

    reloaded = Profile.load(path)
    assert reloaded.important_paths == ["core/agent", "interface/tui_app.py"]
