from pathlib import Path


def test_devmode05_root_is_loader_and_modules_exist():
    root = Path("devmode/DEVMODE05.md")
    module_dir = Path("devmode/DEVMODE05")

    root_text = root.read_text(encoding="utf-8")
    modules = sorted(module_dir.glob("*.md"))

    assert "## Load Order" in root_text
    assert "stable entry point" in root_text
    assert len(root_text.splitlines()) < 120
    assert len(modules) == 10
    assert modules[0].name == "00-activation-and-behavior.md"
    assert modules[-1].name == "09-boundaries-purpose-reporting.md"


def test_devmode05_protocol_body_starts_with_activation_contract():
    text = Path("devmode/DEVMODE05/00-activation-and-behavior.md").read_text(encoding="utf-8")

    assert "explicit approval" in text
    assert "## Activation" in text
    assert "Startup evidence order" in text


def test_devmode05_modules_stay_provider_sized():
    modules = sorted(Path("devmode/DEVMODE05").glob("*.md"))

    assert modules
    assert max(len(path.read_text(encoding="utf-8").splitlines()) for path in modules) < 300


def test_vs05_root_is_loader_and_modules_exist():
    root = Path("devmode/VS05.md")
    module_dir = Path("devmode/VS05")

    root_text = root.read_text(encoding="utf-8")
    modules = sorted(module_dir.glob("*.md"))

    assert "## Load Order" in root_text
    assert "comparison mode, not owner self-edit mode" in root_text
    assert "Default target is always the current MO workspace" in root_text
    assert len(root_text.splitlines()) < 120
    assert len(modules) == 5
    assert modules[0].name == "00-activation-and-boundaries.md"
    assert modules[-1].name == "04-tracking-closeout.md"


def test_vs05_modules_stay_provider_sized_and_read_only_first():
    modules = sorted(Path("devmode/VS05").glob("*.md"))
    joined = "\n".join(path.read_text(encoding="utf-8") for path in modules)

    assert modules
    assert max(len(path.read_text(encoding="utf-8").splitlines()) for path in modules) < 180
    assert "Read-only until approval" in joined
    assert "Current MO workspace is the default adoption target" in joined
    assert "not as external-path-vs-external-path product planning" in joined
    assert "Never write that the running MO workspace is \"not a comparison target\"" in joined
    assert "append-only taskboard ledger/resume exists" in joined
    assert "persistent memory/profile/workflow learning exists" in joined
    assert "structural/code graph caches exist" in joined
    assert "classify only the exact delta" in joined
    assert "Required Behavior-Economy Dimensions" in joined
    assert "provider-first runtime behavior" in joined
    assert "Ghost/planning overlap" in joined
    assert "Replacement Guard" in joined
    assert "replacing or removing Ghost" in joined
    assert "No source edit may happen until the operator approves" in joined
