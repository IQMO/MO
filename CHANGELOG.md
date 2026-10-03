# Changelog

MO Agent runs from its Git checkout rather than a versioned Python package. This
changelog begins with the fresh public repository; pre-public implementation
history is intentionally not reproduced here. Current behavior and limitations
are owned by the linked product documentation and source.

## Unreleased — initial public release

### Product

- MO Terminal provides the primary agentic workbench for project inspection,
  editing, command execution, evidence-backed tasks, goals, schedules, reviews,
  knowledge, and private continuity.
- The optional Windows-focused MO Desktop companion provides conversation,
  screen help, Dashboard, Files, Design/Board, Phone, SystemCare, voice, Shell,
  and the Mologrthim role workroom through the same runtime owners and guards.
- MO Everywhere supplies the authenticated Hub, explicit portable
  conversations, bounded work controls, file transfer, schedules, and native
  Live Control. The Android companion is distributed only through Google Play;
  its source, packages, signing inputs, tests, and release automation are not in
  the public repository.
- Telegram and the headless service provide optional remote and resident
  surfaces over the same Agent, Gateway, task, state, and authorization owners.
- Provider routes include the documented hosted providers, OpenAI/Codex OAuth,
  local Ollama, and custom OpenAI-compatible endpoints. Provider access and
  charges remain the user's responsibility.

### Current maintenance baseline

- Installation, provider setup, optional dependency groups, update, recovery,
  removal, privacy, and surface boundaries are documented from a new user's
  perspective in the [README](README.md), [FAQ](FAQ.md), and
  [capability contract](CAPABILITIES.md).
- Desktop app title bars and controls use shared compact geometry and skin
  settings. MO Files uses its guarded WebView board, while Project Architect
  uses the Mologrthim workroom.
- Retired compatibility callables are removed; public diagnostics enforce
  registered compatibility debt, source duplication, prompt ownership,
  documentation links, packaging boundaries, and privacy gates.
- Windows child processes preserve the non-secret machine name required by
  ordinary system utilities. Requested native key actions use the normal
  computer-action authority and verification boundaries.

### Publication boundary

- The tracked tree contains product source only. Credentials, operator profile
  data, personal material, maintainer test overlays, deployment configuration,
  and Android client implementation remain outside the repository.
- The project is owner-maintained for users. Reproducible sanitized defects are
  accepted through the issue form; unsolicited implementation work is not an
  upstream participation path.
- Security reports use GitHub's private vulnerability-reporting channel. The
  public repository does not publish a personal maintainer contact.

The initial repository release remains **unreleased** until the stable candidate
completes the repository gate, clean-install and container acceptance, required
native UI checks, public-site privacy correction, and the fresh-repository launch
checklist. Android's separate closed-test and Production availability remain
truthfully documented in [ANDROID.md](ANDROID.md); they do not gate publication
of the source repository.
