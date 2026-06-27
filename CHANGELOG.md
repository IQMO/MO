# Changelog

Notable changes to MO, newest first. MO is installed by `git clone`, so updating
is `git pull` in your checkout — `/update` (or `mo --update`) does that for you,
and the TUI footer shows when new updates are available.

## Unreleased

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
