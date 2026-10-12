# Changelog

MO Agent's implementation and history are private. This changelog records product
behavior and installed releases. Current behavior and limitations are owned by
the linked product documentation. See the
[installation guide](README.md#installation-availability).

## 0.1.0-preview.2 — Windows fixes

- Correct escaped quotes and literal operators in Windows Python commands.
- Permit embedded audio and video in Design previews under the existing offline
  media policy.
- Download one Windows installer or use the command in the installation guide.
  Preview.1 users run this installer once to adopt the final download address;
  subsequent releases use `/update` or `mo update`.

## 0.1.0-preview.1 — Windows preview

- Free Windows 11 x64 application with the same per-user installer through
  [download](https://github.com/IQMO/MO/releases/download/v0.1.0-preview.1/MO-0.1.0-preview.1-windows-x64.exe) or the
  [PowerShell installation command](README.md#installation-availability).
- Optional Desktop and existing provider/model setup; Android is a separate
  paid companion. No MO source checkout, Python, Git or .NET SDK is required.
- `/update`, `mo update` and `mo --update` use the installed preview channel.
- The installer is unsigned. The unfinished modern Windows 11 **Open with MO**
  menu is excluded from this preview.

See the [release notes](https://github.com/IQMO/MO/releases/tag/v0.1.0-preview.1) for the exact release assets.

## Preparation history

### Product

- MO Terminal provides the primary agentic workbench for project inspection,
  editing, command execution, evidence-backed tasks, goals, schedules, reviews,
  knowledge, and private continuity.
- The optional Windows-focused MO Desktop companion provides conversation,
  screen help, Dashboard, Files, Design/Board, Phone, SystemCare, voice, Shell,
  the Mologrthim operations floor and Inventory through the same runtime owners and guards.
- MO Everywhere supplies the authenticated Hub, explicit portable
  conversations, bounded work controls, file transfer, schedules, and native
  Live Control. The Android companion is distributed only through Google Play;
  its source, packages, signing inputs, tests, and release automation are not in
  the implementation repository or public download channel.
- Telegram and the headless service provide optional remote and resident
  surfaces over the same Agent, Gateway, task, state, and authorization owners.
- Provider routes include the documented hosted providers, OpenAI/Codex OAuth,
  local Ollama, and custom OpenAI-compatible endpoints. Provider access and
  charges remain the user's responsibility.

### Current preparation

- User guidance is being separated from private development instructions. The
  [README](README.md), [FAQ](FAQ.md) and [capability reference](CAPABILITIES.md)
  describe product behavior and availability.
- Windows packaging is under development. Component checks do not establish
  that an installer or installed runtime is ready.
- Native `mo setup` reuses existing provider/model choices and Codex subscription
  sign-in, with optional Desktop enablement and Windows sign-in startup. Android
  pairing stays optional in MO Phone after Hub setup. The branded installer and
  first-conversation journey still require installed acceptance.
- Setup keeps the saved provider/model and Codex session status visible when
  reopened. A saved session is distinguished from a verified provider response.
- An optional paired local MCP connection lets ChatGPT Desktop submit work to
  a running MO Hub, retrieve replies and request cancellation through existing
  permissions and approvals. It is separate from Codex provider sign-in;
  actual ChatGPT UI acceptance remains pending.
- Background checks in source installations no longer request interactive Git
  credentials or open a sign-in browser. Explicit update authentication remains
  separate.
- Verified file transfers use short temporary names so long destination names
  do not overflow the staging filename limit.
- Installed MO distinguishes supported product customization from development
  in a separately supplied source checkout, while retaining normal coding help
  for users' projects.
- Dashboard project labels and related runtime metadata follow the selected
  workspace, including temporary project switches. Terminal activity distinguishes
  model waiting, received reasoning, response output and retries while preserving
  the elapsed turn clock.
- Window-effect changes use the shared visual-state update so existing floating
  panels receive them. Native blur windows remain hidden if composition, clipping
  or placement fails.
- Starting without optional Git no longer prints a missing-executable traceback
  during project-root and revision detection; source indexing remains available.
- Specific personal mailbox requests follow the private mail path even when
  the action verb is misspelled. Accurate reports of mail guard failures remain
  valid. Repeated failed calls preserve their actual error and let the agent
  continue other work; exhausted recovery reports a plain error without an
  internal tool-control banner.
- Optional voice setup in an installed build uses the bundled speech engine
  and existing model installation, integrity and removal owner; it requires no
  separate Python environment. Voice recordings remain private and are kept by
  default when removing the voice component.
- Desktop app title bars and controls use shared compact geometry and skin
  settings. MO Files uses its guarded WebView board, and Project Architect's
  specialists appear on the Mologrthim operations floor (a WebView app).
- Windows child processes preserve the non-secret machine name required by
  ordinary system utilities. Requested native key actions use the normal
  computer-action authority and verification boundaries.

### Distribution and support

- Source and its history stay private. Public downloads will contain the
  installed product and user guidance, separately from profile data and credentials.
- A public defect-reporting route will accompany distribution. The
  [security policy](.github/SECURITY.md) records the private reporting channel
  still required before Windows release.

The Windows installed product remains **unreleased**. Android's separate
closed-test and Production availability are documented in [ANDROID.md](ANDROID.md);
Windows packaging checks do not change that status.
