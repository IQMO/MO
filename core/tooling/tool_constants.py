"""Shared tool/lane constants — single source of truth for sandbox enforcement."""

# Canonical durable-file mutation tools. Graph overlays, task evidence, and
# continuity accounting consume this same set instead of restating tool names.
FILE_MUTATION_TOOLS = frozenset({"write_file", "edit_file"})

# Tools that mutate durable state — barred from read-only lanes. record_profile_fact
# writes the operator profile (facts.md, auto-injected into context), so a review/
# investigate lane must not be able to persist profile memory.
MUTATING_TOOLS = frozenset({
    "mo_design",
    "write_file",
    "edit_file",
    "generate_image",
    "media",
    "edit_image",
    "record_profile_fact",
    "project_history",
    "schedule_job",
    "role_work",
    "everywhere_pair_android",
    "file_transfer",
    "migrate",
    "mail",
    "systemcare_apply",
    "systemcare_rollback",
    "systemcare_cancel",
    "mo_message",
})

# MO Design work is read-only with respect to the selected project.  Its
# one durable write surface is the private ``.modesign`` artifact owner below; the
# sandbox handles that exception explicitly instead of widening the lane.
DESIGN_ONLY_LANE = "mo-design"
DESIGN_ARTIFACT_TOOL = "mo_design"
DESIGN_ARTIFACT_ACTIONS = frozenset({"update", "read", "board_read", "board_propose"})
# A clarification Board writes only its private Design artifact.  These actions
# remain safe in other read-only lanes because they cannot mutate the selected
# project; full Preview creation/update stays behind its existing lane boundary.
CLARIFICATION_BOARD_ACTIONS = frozenset({"board_read", "board_propose"})
# Provider-facing Design workers need a focused evidence and preview catalog,
# not every product capability that the dispatch sandbox would later reject.
# The hard lane guard below remains authoritative; this set only removes noise
# and provider round-trips from the already isolated visual conversation.
DESIGN_PROVIDER_TOOLS = frozenset({
    "mo_design",
    "read_file",
    "find_files",
    "grep",
    "git_status",
    "inspect_repo",
    "code_search",
    "find_callers",
    "find_callees",
    # Graph readers explicitly direct MO here when the artifact is missing or
    # stale.  Keep the remedy in the same focused catalog; otherwise Design can
    # diagnose staleness but cannot queue the existing bounded refresh.
    "build_graph",
    "graph_explain",
    "graph_neighbors",
    "graph_path",
    "graph_stats",
    "computer_targets",
    "computer_observe",
    "perceive",
    "show_image",
    "web_fetch",
    "web_search",
})
READ_ONLY_LANES = frozenset({
    "report",
    "review-only",
    "investigate",
    "prt-review-only",
    DESIGN_ONLY_LANE,
})

# Raw reads of these path shapes can disclose credential values before output
# redaction can help. Keep this path-based and narrow; content redaction remains
# deliberately conservative so normal source-code reads are not corrupted.
SECRET_READ_BASENAMES = frozenset({".env", ".netrc", "credentials", "mo_cred", "wallet.dat"})
SECRET_READ_PREFIXES = frozenset({".env.", "id_rsa"})
SECRET_READ_SUFFIXES = frozenset({".env", ".pem", ".key"})
SECRET_READ_DIR_NAMES = frozenset({".ssh", "credentials"})
SECRET_READ_PATH_SUFFIXES = ((".aws", "credentials"),)

# Computer-use actuation — these drive the operator's real mouse/keyboard or a
# real browser. They mutate machine state outside the workspace, so they are
# barred from read-only lanes alongside file-mutating tools.
# Desktop uses pyautogui with FAILSAFE=True; browser actions target only a tab
# selected through the MO Chrome extension and local native bridge.
ACTUATION_TOOLS = frozenset({
    "computer_act",
    "phone_click", "phone_set_text", "phone_scroll", "phone_key",
    "phone_file_delete", "phone_cache_trim", "phone_package_action", "phone_shell",
    "systemcare_apply", "systemcare_rollback", "systemcare_cancel",
})

SYSTEMCARE_ACTUATION_TOOLS = frozenset({
    "systemcare_apply", "systemcare_rollback", "systemcare_cancel",
})
SYSTEMCARE_OBSERVATION_TOOLS = frozenset({
    "systemcare_inspect",
    "systemcare_status", "systemcare_calibrate", "systemcare_scan", "systemcare_plan",
})

PHONE_ACTUATION_TOOLS = frozenset({
    "phone_click", "phone_set_text", "phone_scroll", "phone_key",
    "phone_file_delete", "phone_cache_trim", "phone_package_action", "phone_shell",
})
PHONE_OBSERVATION_TOOLS = frozenset({
    "phone_context", "phone_files", "phone_storage_report", "phone_file_read", "phone_capabilities",
    "phone_system_status", "phone_cache_report", "phone_packages",
})
