# Changelog

Notable changes to MO, newest first. MO is installed by `git clone`, so updating
is `git pull` in your checkout — `/update` (or `mo --update`) does that for you,
and the TUI footer shows when new updates are available.

## Unreleased

- **Post-turn commit reminder:** after a turn that leaves uncommitted changes,
  MO reminds the operator to commit and push. Runs via the post-provider pipeline
  using a direct `git status --porcelain` check.
- **Error recovery message:** more reassuring "no worries" tone that explicitly
  says it's fine to ignore the error report prompt.
- **Windows subprocess encoding:** all `subprocess.run(..., text=True)` calls
  now explicitly use `encoding="utf-8", errors="replace"` — prevents
  `UnicodeDecodeError` on Windows when git output contains non-ANSI characters.
- **Self-update:** live "N updates behind" notice in the TUI footer — automatic,
  cached, non-blocking, no user action. The apply path (`/update`, `mo --update`,
  and `mo --version` reporting the running git commit) is landing incrementally;
  until then `python -m core.update.apply` fast-forwards the checkout.
- **First-run onboarding:** a starter-prompt line (`Try: find issues in this
  project · …`), a warning when the active provider has no key, and operator-name
  auto-capture plus a "MO doesn't know you yet" profile nudge.
- **Owner maintenance** activation now self-heals until the codebase is provably
  clean instead of running a single pass.
- **Init:** the generated `~/.mo/.env` hints the default DeepSeek key.
- **MIT LICENSE** added.
