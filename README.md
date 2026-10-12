<p align="center">
  <img src="assets/mo-banner-v4.png" alt="MO Agent — one local agent runtime, reachable everywhere through Terminal, Desktop, and Android" width="100%">
</p>

<h1 align="center">MO Agent</h1>

<p align="center">
  <strong><a href="https://github.com/IQMO/MO/releases/download/v0.1.0-preview.2/MO-0.1.0-preview.2-windows-x64.exe">Download MO for Windows</a></strong> ·
  <a href="#installation-availability">Install with a command</a> ·
  <a href="https://github.com/IQMO/MO/issues">Support</a><br>
  Free · Windows 11 x64 · One installer; no source checkout or developer tools needed
</p>

<p align="center">
  <strong>One local agent runtime. Honest progress. Reachable everywhere.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="PolyForm Shield License 1.0.0" src="https://img.shields.io/badge/License-PolyForm%20Shield%201.0.0-2ea44f"></a>
</p>

<p align="center">
  <img src="assets/mo-teaser.gif" alt="Scattered terminals, tabs, chats and jobs gather into MO's four cubes: Meet MO Agent" width="100%">
</p>

MO is a local agent runtime around the AI model you choose. The model proposes;
MO's runtime owns the tools, safety, sessions, memory, proof and continuity, so
your work survives a switch of model, a closed terminal, or a move to another
device. Work in the terminal, MO's main workbench; add MO Desktop as a resident
assistant on Windows; reach your machines from the Android app through your own
Hub; or use Telegram and a headless service. Everything stays private by default.

**[Installation availability](#installation-availability)** · **[What makes MO different](#what-makes-mo-different)** ·
**[Everything MO does](#everything-mo-does)** · **[Choose an interface](#reachable-everywhere)** ·
**[Read the FAQ](FAQ.md)**

> Start with the [critical FAQ and surface comparison](FAQ.md) if you want to
> understand what runs where, what stays private, or which interface to use.

MO is a closed-source product. The [public repository](https://github.com/IQMO/MO)
provides downloads, updates and user guides. MO's implementation and history remain private.

MO for Windows is free. The [Android companion](ANDROID.md#availability) is
the paid app; its purchase is separate from provider access and Windows setup.

## What makes MO different

- **A runtime, not a model.** Use any provider, or a local model fully offline; switching providers keeps the same tools, safety, memory and proof.
- **Done means proven.** The runtime, not the model, decides what counts as done: tasks close only on tool evidence, and final-answer gates flag unverified claims.
- **Your work carries on.** Conversations are saved and resumable, interrupted work is kept honestly, several terminals coexist, and each surface knows where the work stands.
- **One runtime, every surface.** Terminal, Desktop, Android, Telegram and a headless service reach the same private runtime; your Hub's provider keys never reach the phone, and the phone runs no second agent.
- **Your phone reaches your machines.** From the phone: chat with your MO, start and follow work, open a terminal on your Hub, drive a running terminal or Desktop, move files. The computers you reach connect out to your Hub, so they open no inbound port.
- **Private, and it learns you openly.** Everything lives in your private MO home; credentials never enter the model's context; learning is reviewable and memory is kept in separate, honest kinds.
- **Deep project work.** A structural code graph, project mapping, project rules, goals with an auditor, background workers, roles and a review team that hands confirmed findings back for fixing.
- **It uses your computer with you.** Target-owned actions on real windows, tabs and controls, each returning its own proof; actions requiring approval name their target; a resident companion you can talk to.

## Everything MO does

Every public feature, grouped by what it gives you: **what it is** — *how you use it.* What you get.
*(setup)* needs an account, an extra install or configuration first; *(closed test)* is in the Android
app's closed testing on Google Play.

### A runtime, not a model

Any provider or a local model; the runtime owns tools, context, discovery and attachments. Provider access and costs are yours; MO does not include an AI subscription.

- **Same MO with any model** — *Automatic.* Your provider proposes; MO's runtime owns the tools, safety, sessions, memory and proof, so switching providers keeps all of it.
- **The right context, automatically** — *Automatic.* Each turn gets the relevant profile facts, earlier conversation, recall, continuity, skills, conventions and code context within a budget; MO records what was actually delivered.
- **Follows your project's rules** — *Automatic in project work.* Reads the project's AGENTS.md rule chain before work; offers a minimal starter when there is none; checks changed rules before reporting.
- **Tools on demand** — *Automatic; ask what MO can do.* MO finds and switches on the right tool from its whole catalog instead of loading everything into every turn.
- **Works offline** — *Configure a local model such as Ollama.* Every capability that runs locally keeps working without the internet; MO never disguises a cloud route as local.
- **Any provider** — */model.* DeepSeek, Z.ai, OpenCode, OpenAI/Codex, Ollama or any OpenAI-compatible endpoint, with fallback.
- **A model per surface** — *Config.* Different models for main, Desktop, voice, prompt enhancement, mapping and review.
- **Spending caps** — *Config.* Limit provider requests per turn.
- **Works in your projects** — *Ask in the Terminal.* Reads, edits, runs commands and tests in your real repo, following its AGENTS.md rules.
- **Web research** — *Ask it to look something up.* Searches the web (ranked results with your search key) and reads pages as untrusted evidence.
- **Read any GitHub repo** — *Name a repo or paste its URL.* Answers questions from a repo's code and docs without installing anything, or turns it into a reviewed skill.
- **Attachments anywhere** — *Attach files to a turn.* Up to eight files per turn from any surface, kept private and treated as untrusted.
- **extrathink** — *Add the word extrathink.* MO re-audits its work before finishing.
- **Reads PDFs** *(setup)* — *Give it a PDF.* Text from PDFs into the conversation.
- **MCP tools** *(setup)* — *Add servers to config.* Bring external tool servers in, behind the same sandbox.

### Done means proven

Task truth, verification, final gates, goals and reviews run on evidence.

- **Done means proven** — *Automatic.* A task only closes with tool evidence; unverified results are said plainly.
- **Live taskboard** — *Automatic for real work.* Rows with dependencies and approval gates; resume where you left off.
- **Goals** — */goal <task>.* Autonomous multi-step work audited against your own rules; Ctrl+G sends it to the background; /goal pause lets the current step finish, then pauses.
- **Project Review Team** — */prt.* A review of your changes with evidence and a score; confirmed issues handed back for fixing.
- **PRT on GitHub** *(setup)* — *Enable GitHub delivery.* Posts PR reviews and a status check, answers PR questions, plans issues.
- **Docs stay in step** — *Automatic.* Flags code changes whose documentation wasn't updated.
- **LSP diagnostics** *(setup)* — *Configure language servers.* Real compiler/linter diagnostics after every edit.

### Continuity: your work carries on

Saved, resumable, honest sessions; portable conversations; continuity across surfaces and terminals.

- **Every conversation saved** — */session, /new, /projects.* Conversations are saved privately and grouped by project; reopen, rename or switch any of them.
- **Resume, retry, undo** — */resume, /retry, /undo.* Pick up an earlier conversation, retry the last request, or undo the last turn.
- **Interrupted work is kept** — *Automatic.* If a turn is cut off (stop, crash or power loss), your request and the tool work already done stay in history, marked interrupted; nothing resumes on its own.
- **Continuity** — *"What were we busy with?".* Answers from real runtime state, then memory; resumable banners.
- **Handoff instead of forgetting** — *Automatic on long sessions.* Long contexts continue without destructive compaction.
- **Several terminals at once** — *Open another terminal; /heartbeat instances.* Each terminal keeps its own conversation; MO sees what its sibling terminals and MO Desktop are doing, checks before touching shared work, and running MOs can message each other (delivered once, never as an instruction).
- **Portable sessions** — */session share.* Named conversations that move between surfaces.
- **Continuity across surfaces** *(setup)* — *Automatic once your Hub is set up.* Each surface shares a short note of intent, outcome and next step through your Hub, so the phone, Desktop or another terminal knows where things stand; never raw transcripts, tool output or secrets.
- **Continue on…** *(closed test)* — *History → Continue on.* Move a conversation to the terminal, Desktop, another phone or Telegram.
- **Desktop follows your terminal** — *Ask the companion what the terminal is doing.* The companion reads what the bound MO terminal is working on and takes it as context, then says so on the cubes; it changes nothing.
- **Honest session closeout** — *Automatic at the end of a session.* MO records what is clean, what is unresolved, open goals and workers, and uncommitted work, so the next start begins from the truth.

### Your Hub, your phone, your machines

Your own Hub, the Android app, remote terminals, Live Control, files and transfers, Telegram and a headless service. Android is in closed testing on Google Play; see the [Android guide](ANDROID.md#availability).

- **Your own Hub** *(setup)* — */everywhere setup on your computer or server.* An authenticated hub you run yourself; devices pair to it with exact, revocable scopes; the computers you reach from the phone connect out to it, so they open no inbound port.
- **MO from ChatGPT Desktop** *(optional setup)* — A [paired local MCP connection](mo_everywhere/README.md#use-mo-from-a-local-chatgpt-desktop-mcp-connection) can submit work to your running MO Hub, retrieve the reply or cancel the request. Separate from MO's Codex provider sign-in; Desktop only, with no conversation-history sync.
- **Chat with your MO** *(closed test)* — *Hub chat.* Your full MO from the phone, with history and named conversations.
- **Control → Work** *(closed test)* — *Phone Control tab.* Start and follow background work and schedules; notifications when done.
- **Start a terminal from the phone** *(closed test)* — *Control → Start MO terminal.* A fresh MO terminal on your Desktop or Hub, ready to watch.
- **Live Control** *(closed test)* — *Phone → Remote.* Drive your Desktop and MO terminals by touch, virtual cursor and keyboard.
- **Terminal workspaces on the phone** *(closed test)* — *Phone → Remote.* Your running MO terminals grouped into one workspace you can drive from the phone; closing a view never kills a terminal.
- **Live Control host** *(closed test)* — *Pair a phone with Live Control.* Share this screen with your paired phone, which can drive it.
- **The phone cube** *(closed test)* — *Install MO Everywhere.* A floating cube on your phone; tap cycles Dashboard, Chat, Control, Files.
- **Pair a phone by QR** *(closed test)* — *MO Phone → pair.* A one-use QR code to pair your Android with your Hub.
- **Setup in plain words** — *Ask MO about Everywhere or the Android app.* Where the Hub runs, where to get the app, which credentials are present, what blocks setup, and the one next step.
- **Files and transfers on the phone** *(closed test)* — *Files tab.* Panes across Hub, Desktop, terminals and phone; verified transfers.
- **Send files between your machines** *(setup)* — */send, /transfers, or ask.* Resumable, verified transfers between your paired computers and phone, with receipts on both sides.
- **MO Files** — *Launcher → Files.* One file manager across this PC, your other MO machines and your phone; previews and verified transfers.
- **Quick Share** — *MO Files → share.* A short-lived link on your local network so any device with a browser can grab a file.
- **Kill switch and consent** *(setup)* — *mo_everywhere CLI.* Pairing, capabilities, consent and a kill switch from the terminal.
- **Share a phone folder** *(closed test)* — *Pick a folder.* Let your other MO devices read one folder you choose.
- **This-phone chat** *(closed test)* — *Chat → This phone.* Direct chat with your own provider key when no Hub is around.
- **Phone voice and charging cue** *(closed test)* — *Mic button; plug in.* Dictation into the draft; the cube plays its charging emote.
- **Phone trackpad and pen** — *Launcher → hover MO Phone → Trackpad.* Your phone becomes the PC's mouse and keyboard; Board mode draws as a real Windows pen. Requires a compatible Android client; see [Android availability](ANDROID.md#availability).
- **Telegram remote** *(setup)* — *Pair your Telegram.* Talk to MO from Telegram; approve actions; /status /stop /continue /cancel.
- **Headless service** *(setup)* — Run MO on your computer or server for the Hub, schedules and integrations. Public server packages are not available yet.

### Private, and it learns you

Profile, terms, memory, reviewable learning, skills and moving in from other agents.

- **It remembers you** — *Automatic.* Profile facts, preferences and your project list, private on your machine.
- **Your words** — *Define a shorthand.* MO learns your terms and abbreviations.
- **It learns from corrections** — *Correct it.* Rules learned from your feedback, shown as ◈ learned notices.
- **Learning you can review** — */learning.* See what it learned; confirm or dismiss suggestions.
- **Recall past conversations** — *Ask about earlier work.* Keyword recall, optional meaning-based recall.
- **Skills** — */skills.* Local skill packs chosen by task and by the code you're touching.
- **Learn from a repo or docs** — *"Learn from <GitHub repo / docs site / llms.txt>".* Risk-scanned, use once or promote to a skill.
- **Move in from other agents** — *"Move me from OpenClaw/Hermes".* Inspect, plan and import identity, memory and rules; secrets skipped.
- **Onboarding** — *First run.* Notices it doesn't know you yet and learns your name and setup.
- **Profile sync between your computers** *(setup)* — *Configure a private Git remote.* Your curated profile on every machine, over your own SSH.
- **Move learning between machines** — *Learning bundle export/import.* Your learned state travels to your other MO.

### Safe by design

One sandbox for every tool call, exact confirmations, secrets kept out, untrusted content fenced.

- **One safety gate** — *Automatic.* Every tool call passes one sandbox: paths, destructive commands, network, secrets.
- **Secrets stay secret** — *Automatic.* Credentials never enter the model's context; answers are scanned and redacted.
- **Your forbidden phrases** — *Edit answer_rules.md.* Phrases MO must never say, or must warn about.
- **Injection and malware guard** — *Automatic.* Blocks instruction smuggling in memory and refuses to build malware (authorized security work allowed).
- **Changed-file security check** — *Automatic.* Scans changed files for hardcoded secrets and unsafe shell.
- **Untrusted content fenced** — *Automatic.* Web pages and imports are marked untrusted before MO reads them.
- **Exact action approval** — *When required.* Review the target and parameters before a consequential action; permitted actions proceed through the normal gates. A corner flick stops the mouse.
- **Panic Stop and Action Log** — *Tray.* Stop everything MO is doing at once; see every action it took.

### The engineering workbench

The Terminal: workspace and panes, code graph and maps, roles and workers, presentation and orientation.

- **Split-terminal workspace** — */workspace.* Split MO into panes with ordinary terminals.
- **Remote panes** *(setup)* — */workspace new on another MO machine.* Panes of MO terminals running on your other machines, in one grid.
- **Run any agent inside MO** — */terminal codex, /terminal claude.* Codex, Claude or any shell on MO's own console, then back to MO.
- **Workspace rail** — *Ctrl+B.* Your projects and their local and host terminals in one rail; switch live groups without stopping them; a quiet Health view of machine resources.
- **Code graph and search** — *Ask "where is…", or /structural-graph.* Ranks source owners by identifiers, words and bounded concept matches; follows recorded static relationships with their uncertainty and coverage.
- **3D code map** — */dashboard map.* An interactive 3D map of your codebase, coloured by your skin, overlaid with current work.
- **mapthis** — *Add the word mapthis.* Maps a whole project into a docs page with verified citations, using parallel workers.
- **Project history and knowledge** — */knowledge, or ask how something changed.* Git history joined to code; source-linked project knowledge.
- **Redundancy scan** — *Ask for duplicates.* Finds duplicate and near-duplicate code.
- **Graph for other agents** *(setup)* — *Add the mo-graph MCP server to Claude Code or others.* Other coding agents use MO's code graph.
- **Mologrthim** — *Launcher → Mologrthim.* MO's operations floor: every running MO, their specialists' checked work and rank, candidates waiting for your hire, MO Care; assign work, hire or pause a goal through that MO.
- **Inventory** — *Launcher → Inventory.* A basket of everything MO did with you, today first; search, pick several and drop them on a running MO terminal, where they wait in its composer.
- **Background workers** — *Ask for parallel work; /activity.* Workers in their own sessions, with completion notices and conflict detection.
- **Roles** — */role <name>.* Reusable perspectives that govern a conversation, workers or schedules.
- **Project Architect** — */role project-architect.* Calibrates a project and dispatches specialists it registers from evidence.
- **Book Writer** — */role activate book-writer.* MO writes a book on any subject you pick (PDF, EPUB or print), keeps your library of books, and reads them aloud on request; it never invents facts about real people or events.
- **Game Collaboration** — */game start.* A game project's questions, decisions, proposals and approvals, kept in one place.
- **Skins and window effects** — *Settings.* Pick or make a skin, and a window effect for every MO window.
- **Prompt enhancer** — *Ctrl+E while typing.* Rewrites your own request more clearly before you send it.
- **Steer while it works** — *Type during a turn.* Your message queues or steers the running work.
- **Command palette** — *Press /.* Every command, grouped, with submenus.
- **Live footer** — *Automatic.* Context use, provider quota or balance, and updates waiting.
- **Custom skins** — */skin.* Built-in skins or your own; one skin themes Terminal, Desktop, phone and code map.
- **Inline images and charts** — *Ask for a chart or image.* Truecolor images, bar charts, sparklines and tables right in the terminal.
- **Arabic input** — *Type Arabic.* Right-to-left text shown correctly in the TUI.
- **Rotating hints** — */hints.* Tips on the idle line; your own hints.txt.
- **Full Dashboard** — *Launcher → Dashboard, or /dashboard show.* Your own view: who you are, projects, what MO learns, work pulse; a brain map of memory and learning.

### It uses your computer with you

Computer use, the Desktop companion and its apps, voice, MO Shell and PC care.

- **Uses your PC** — *Ask in plain words.* Opens apps and sites, clicks, types, closes windows; each action returns its own proof.
- **Points the way** — *"Where is…?" / "show me".* The cubes glide to the spot and label it.
- **Walkthroughs** — *"Walk me through…".* Numbered on-screen steps over the real controls.
- **Sees the screen** — *Ask about what's on screen.* Captures the right window by handle, even behind others, and explains it.
- **Works in your Chrome** *(setup)* — *Install MO Connected Tab.* Acts inside your real Chrome tab; no separate browser.
- **Four-cube companion** — *Runs on the desktop; Win+Alt+M summons it.* A small companion that lives on your screen and comes when called.
- **Desktop recipes** — *Recorded step sequences.* Transparent replay of known desktop steps.
- **Living panel** — *Left-click the cubes* (again to close). One panel grows from the cubes: type, attach, choose; replies appear in place.
- **Native inline cards** — *Desktop replies, when useful.* Compact visual explanations appear inside the panel; choose **More**, **Normal** or **Never** in Settings. Larger visual work keeps its existing MO Design route.
- **The swallow** — *Drag a file near the cubes and drop it.* Their mouth opens by distance and they swallow the file; it's attached to your message.
- **Files in your sentence** — *Drop or attach a file while you write.* It sits where you are typing as a named chip ([Image1], [Video1], [File1]); point at it for a small preview, Backspace removes it, and MO is told which file each name means.
- **App launcher** — *Double-click the cubes.* Four grouped app tiles (Work, Devices, Care, Your apps); add your own files and folders; drag to arrange.
- **Compact Dashboard** — *Right-click the cubes.* Now, You and System at a glance, with a link to the full Dashboard.
- **Screen capture by hold** — *Hold a cube for two seconds, drag a rectangle.* A full-resolution snip you can preview and send to MO.
- **Volume and brightness on the cubes** — *Scroll over the cubes* for Windows volume; *Shift + scroll* for the brightness of the screen they sit on (a laptop panel's own brightness, or MO's dim layer on an external screen).
- **Clipboard history** — *Win+Shift+Z, or hold the top-left cube.* What you copied, in the one panel: copy it again, ask MO about it, or remove it. Memory only; secrets are masked and never sent.
- **Chase or rest** — *Ctrl-Ctrl.* The cubes follow your cursor, or stay where you left them.
- **Body language** — *Automatic.* The cubes react to real events with their own emotes.
- **Personality** — *Settings: moodiness, warmth, playfulness.* Tune how lively the companion is.
- **Characters for roles and tools** — *Activate a role or connect a tool server.* The cubes change colour and formation to show who is working.
- **Glance notices** *(setup)* — *Automatic.* Mail, finished work and reminders arrive as one short line; details when you look.
- **Docks to its terminal** — *Automatic.* The cubes know their own MO terminal window and sit by it.
- **Stays above dimmers** — *Automatic.* Keeps the cubes visible over approved always-on-top overlays such as a screen dimmer.
- **Pick-and-submit choices** — *When MO offers options.* Choices appear as buttons instead of text you retype.
- **Report an issue** — *Ask, or the report action.* Opens a separate report session so the companion stays clean.
- **Hands real code to the Terminal** — *Ask the companion for a code change.* It hands the request to the same project's live MO Terminal, or opens one in MO Shell with your request as its first turn; say "on the server" and it runs on your paired MO host.
- **Focus mode** — *Hold the lower-right cube, or the tray.* A window switcher with previews, app and file search, taskbar pins, tray icons, calendar, dimming and power controls.
- **MO Phone (mirror)** *(setup)* — *Launcher → Phone.* Your Android screen live on the PC (scrcpy), USB or wireless; MO can keep a frame to see your phone.
- **Settings app** — *Launcher → Settings.* Cube size, glow, corners, formation, colour; window effects; movement; voice; role.
- **Hold to talk** — *Double-Alt, hold, speak, release.* Speak anywhere; local recognition (Whisper or Windows) in English and Arabic.
- **Spoken replies** — *Talk to it.* The voice conversation layer answers sentence by sentence and can respond while a delegated task continues.
- **It says what it's doing** — *During a task.* Short spoken progress ("Opening it now.") instead of silence.
- **Talk while it works** — *Speak during a task.* Continue the voice conversation while the task runs; newly delegated work queues behind it.
- **Voice commands** — *Say stop, repeat, or what are you doing (English or Arabic).* Instant control without a model call.
- **Continuous Voice Chat** — *Tray toggle.* Hands-free listen, answer, listen.
- **Hands to a task** — *"Open my report and…".* Anything needing tools is handed to MO in your own words and spoken back when done.
- **Your own voice** *(setup)* — *Settings → Voice → Your voice.* Record yourself reading short lines, and MO makes your voice with the trainer installed on your computer (Applio), then speaks in it; or choose a voice clone you trained. It says whether your voice loaded, and never ships anyone's cloned voice.
- **Arabic speech** *(setup)* — *Settings → Voice → Arabic voice.* Arabic replies spoken through an Arabic voice you placed; never misread by the English voice.
- **MO Shell** — *Launcher → Shell.* A floating native window for your MO terminal, attached to one window you choose; in a fresh conversation MO names the attached app and asks how it can help.
- **SystemCare app** — *Launcher → SystemCare.* The PC-care workspace.
- **Scan, plan, apply, undo** — *SystemCare scan.* A read-only scan, an exact plan you approve, and receipts you can roll back.
- **Game Mode** — *Game Session in the tray.* The PC set up for gaming, then restored.
- **Care actions** — *From a plan.* Startup items and services, Windows repair, disk optimisation, DNS, updates, app leftovers, browser history, Recycle Bin, drivers and PATH duplicates.
- **Scheduled care** — *Schedule a scan.* Care jobs that run on schedule without a model turn.
- **MO Care** — *Automatic, every 15 minutes.* MO watches its own background without a model (provider and turn errors, workers that ended blocked, apps that failed to open, failed scheduled jobs, failing offline checks) and reports each problem once in MO Desktop or the terminal; Investigate opens a terminal.
- **Care for your projects and servers** — *Choose a project or host.* Inspection of selected projects and configured servers.

### Life and making things

Mail, Life records and money, schedules, MO Design, visuals, images and explainer videos. See [mail setup](core/mail/README.md) and [Life records](core/life/README.md) for the privacy and account limits.

- **Mail** *(setup)* — *"Check my mail" / Gmail or Outlook.* Search, read, draft; send and delete only after you confirm.
- **Life records** — *"Track my car insurance".* Commitments and cases with dated updates and payment plans.
- **Money** — *"I paid 40 for…".* Income and outgoings with a month view and summary.
- **Schedules and reminders** — */schedule add <when> :: <task>.* Plain reminders, timed MO turns and scripts, delivered to Desktop or Telegram.
- **MO Design Studio and Board** — *Launcher → Design.* Sketch on a shared Board with MO; MO proposes, you accept; live previews; send the result to a terminal or a background Goal.
- **Images** *(setup)* — *"Make an image of…" / "crop this".* Generate, edit and show images.
- **Generate** *(setup)* — *Composer → Generate; pick image, video or song.* Seedream images, Seedance video and Suno music through your Kie.ai key: references named in your sentence ("the motion of [Video1] on [Image1]"), checked against what the model takes as you drop them, Refine to sharpen the prompt, your credits shown, and saved results that play from the composer.
- **Diagrams** — */visualize.* JSON, YAML, Markdown or folders as Mermaid or ASCII diagrams.
- **Explainer videos** — *"Make an explainer about…".* Narrated videos with captions, rendered locally.

### Setup and operations

Install, health checks, credentials, updates and settings.

- **Health checks** — */doctor, /status, /usage, /now.* Offline health, status, token use and the current snapshot.
- **Setup checks** — */credentials, /settings, /reload.* Check credentials without displaying them and review your configuration.
- **Installation and updates** — See [current availability](#installation-availability). Public Windows installation and update instructions will accompany an accepted release.

## Reachable everywhere

| Surface | Best for | What you need |
| --- | --- | --- |
| **MO Terminal** | Coding, research, file work, goals, reviews and automation | The base install and your provider; add screen tools only when needed |
| **MO Desktop** | Dashboard, visual design, screen help, voice and files | Optional companion and computer-use packages; native Desktop is Windows-focused |
| **MO Shell** | Keep the terminal beside a selected app in a floating workspace | Optional native Windows shell around the existing terminal |
| **MO Everywhere for Android** | Conversations, tasks, schedules, Remote and Files while away | Google Play access and your running Hub for connected features; separate **This phone** chat needs only your provider |
| **Telegram** | Remote conversation and approvals | Your configured bot and running MO service |
| **Headless service** | Keep the Hub, scheduler and integrations running | Your computer/server, selected extras and private configuration |

Detailed ownership, capability, and limitation rows are in
[FAQ — Which MO surface should I use?](FAQ.md#which-mo-surface-should-i-use).
The [capability reference](CAPABILITIES.md) covers built-in commands, supported
surfaces, privacy boundaries and observable behavior.

## MO Everywhere in one minute

**[Watch the 60-second walkthrough](https://youtu.be/OgEWYONoGlo)** ·
**[Closed-test access on Google Play](https://play.google.com/apps/testing/app.moagent.mobile)** ·
**[Features and setup](ANDROID.md)**

The film uses native Android UI with fictional sample content and supporting
illustration. Your own running computer/server supplies connected agent work;
the phone does not run the full MO tool runtime. Android is currently in
**closed testing for eligible users**, with Google Play as its only public
installation and update source. The [Android guide](ANDROID.md#availability)
owns current availability. The repository contains no Android package downloads.

## Installation availability

**MO 0.1.0-preview.2** is a free preview for **Windows 11 x64**.
[Download the Windows installer](https://github.com/IQMO/MO/releases/download/v0.1.0-preview.2/MO-0.1.0-preview.2-windows-x64.exe) and open it, or use these two commands
in PowerShell to download and run the same per-user installer:

```powershell
Invoke-WebRequest -UseBasicParsing 'https://github.com/IQMO/MO/releases/download/v0.1.0-preview.2/MO-install-windows-x64.ps1' -OutFile "$env:TEMP\MO-install-windows-x64.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\MO-install-windows-x64.ps1"
```

Both routes install the same application. No MO source checkout, Python, Git
or .NET SDK is required. The command verifies the installer's size and SHA256
before opening it; it does not build from source. Optional integrations can
have separate requirements. See the [preview release notes](https://github.com/IQMO/MO/releases/tag/v0.1.0-preview.2).
Public server packages and container images are not available yet.

If you installed **preview.1**, run the preview.2 installer once to switch to
the final download address. It preserves your profile. After that, use `/update`
inside MO or `mo update` from a terminal for future releases.

After installation, close and reopen the terminal application before running
`mo`. Opening another tab in an already running Windows Terminal can retain
its old PATH. Your project folder does not need to be inside MO's install folder.

Existing source installations are not automatically converted. Their Git-based
updates require authorized access to the private source repository and working
Git credentials. An access failure does not mean your MO profile is damaged;
do not delete or reinitialize it to resolve a repository access problem.
Moving from a source installation to this installed preview is an explicit choice.

This preview installer is unsigned. The installation command and updater use
HTTPS and validate the exact selected GitHub asset, size and SHA256; this trusts
the official distribution repository and GitHub, without an independent
publisher signature.

The unfinished **Open with MO** integration in the Windows 11 main folder menu
is excluded from this preview. It remains deferred; no legacy menu entry is
substituted for it.

Android is separately available through Google Play's eligible testing track;
see [Android availability](ANDROID.md#availability). GitHub does not supply
Android APKs or replace Google Play updates.

## Getting started with an available MO installation

The following describes use of an existing MO installation. It is not a source
build or a substitute for the release's installation instructions.

### Configure a provider

Run `mo setup` to choose an existing provider, model and reasoning level in MO's
native setup window. Setup also offers MO Desktop and optional startup at
Windows sign-in. Save, then open MO and send a first request to check the connection.
These controls are also available from Desktop Settings.

Choose a provider already configured for MO. Provider availability, model access
and billing belong to that provider account; you do not need credentials for
every provider in the example configuration. MO's existing Codex connection
uses a ChatGPT subscription session, separately from API-key providers.

Use **OpenAI OAuth → Connect Codex (ChatGPT subscription)** for MO's existing
Codex-compatible device sign-in. Complete the browser sign-in, then send a short
request to verify the connection; a saved session alone does not verify a reply.
This compatibility connection does not imply OpenAI endorsement of MO.
The optional [ChatGPT Desktop MCP connection](mo_everywhere/README.md#use-mo-from-a-local-chatgpt-desktop-mcp-connection)
is a separate way to send work to MO; it does not grant provider subscription access.

Android pairing is optional and can wait until later. Once your Hub is ready,
open **MO Phone → Add phone** in Desktop and follow the existing QR flow. See
the [Android guide](ANDROID.md#install-and-pair) for app availability and pairing.

For advanced configuration, [`config.example.yaml`](config.example.yaml)
describes the supported fields. Provider keys belong only in the private
`~/.mo/credentials/providers.env` file.

In `~/.mo/config.yaml`, keep the selected provider's configuration and set
`model.default` to its name. For example, these are the relevant DeepSeek sections:

```yaml
providers:
  - name: deepseek
    type: chat_completions
    base_url: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    model: deepseek-v4-pro

model:
  default: deepseek
```

MO reads provider keys from that canonical credential file. `api_key_env` is a
logical key name, not a request to read arbitrary process environment variables.
Keep keys and personal configuration in your private MO home, separate from
the installed application and your project files.

Local Ollama models use the same `chat_completions` interface at
`http://localhost:11434/v1`; no MO-specific local-model dependency is required.
For the other supported routes, see [Which providers work?](FAQ.md#which-providers-work).

### Work in your project

Open MO in the project you want it to work on. The Terminal keeps that project
separate from MO's private profile. Use the `mo` command where it has been
configured by your installation; command availability is not proof that a
public Windows package has shipped.

Try a small first task: **“Read this project's README and explain how to run it.
Do not change files.”** Use `/credentials` to check provider readiness without
displaying keys, `/model` to select a provider/model, and `/doctor` for runtime
health. For scripts, the same launcher supports:

```text
mo -p "review the current diff and report verified findings"
mo --prompt-file task.txt
```

`/model`, `/show`, `/hints`, and `/activity` save Terminal preferences privately;
authored config remains the baseline. `/doctor personalization` provides a
read-only profile, learning, memory, and retention audit. `/profile provider`
records profile metadata only.

### Optional features

Desktop, Design, Connected Tab, voice, PDF reading, local embeddings and
Everywhere have their own platform, permission or account requirements.
The guides below describe those boundaries. Optional models, accounts and
services need their own setup; a feature listed here does not mean they are
already installed or connected.

### Limit provider requests

To enforce a request limit, set it explicitly in your private config:

```yaml
agent:
  max_provider_requests_per_turn: 8
```

Run `/reload`, then `/settings` to check the configured value. The default is
`0` (unlimited). For a one-shot run, use
`mo --config path/to/config.yaml --prompt-file task.txt` with your configured
provider and this limit. A limit written only in the prompt is not enforced.

The cap counts model completion attempts, including retries and auxiliary or
worker calls started from that turn. Internal continuation shares the same
allowance. Exhaustion stops further provider requests and leaves unfinished
work open. HTTP redirect hops, credential refresh and separate embedding work
are outside this count. It is not a token, cost, or elapsed-time limit.

### Optional surfaces

- **MO Desktop:** the Windows companion can be opened with `/desktop` when
  available and enabled. See the [Desktop guide](mo_desktop/README.md).
- **MO Design:** open Design from the Desktop launcher, or ask MO to create a
  visual. Sketch and diagram requests can use a compact Board connected to the
  originating Terminal. Accepting a draft does not authorize implementation.
  See the [artifact guide](core/design/README.md) and
  [Design Studio guide](mo_desktop/design_studio/README.md).
- **Everywhere:** your running Hub supplies connected work to paired devices.
  See [Hub setup and permissions](mo_everywhere/README.md).
- **Android:** install through Google Play only when eligible. See the
  [Android guide](ANDROID.md) for testing and Production availability.
- **Telegram and headless use:** these require a configured running MO host.
  A public server distribution is not available yet.

## Documentation

| Guide | What it covers |
| --- | --- |
| [Capabilities](CAPABILITIES.md) | Features, command coverage and behavior limits |
| [FAQ](FAQ.md) | Common questions and choosing a surface |
| [Terminal](interface/README.md) | Commands, input, queueing and steering |
| [Desktop](mo_desktop/README.md) | Companion behavior, apps and settings |
| [MO Shell](mo_shell/README.md) | Windows terminal and attached-app workspace |
| [Everywhere](mo_everywhere/README.md) | Hub, continuity, pairing, API and Live Control |
| [Connected Tab](clients/chrome/README.md) | Chrome consent, status and removal |
| [Android](ANDROID.md) | Availability, requirements, setup and privacy |
| [Publisher](mo_publisher/README.md) | Optional public pages and AI-report privacy |
| [Changelog](CHANGELOG.md) | Product change history |

## Privacy and security

- Your profile data stays separate from the application.
- Credentials stay in scoped files under `~/.mo/credentials/` and never enter
  model-visible config.
- Tool calls pass through path, network, command, actuation, and secret guards.
- High-impact actions use exact target-and-parameter confirmation.
- Normal terminal use requires no inbound network listener.
- Everywhere and Telegram are opt-in; device grants are revocable and scoped.
- Suspected vulnerabilities use the private
  [security reporting policy](.github/SECURITY.md), never a public issue.

## Distribution

MO is an owner-maintained product with private implementation sources. Public
downloads, updates and user support will use a separate distribution channel.
See [installation availability](#installation-availability) before looking for
an installer. Repository privacy does not change the licenses of copies
already distributed.

For product reports and participation, see [Project participation](CONTRIBUTING.md).
Do not include credentials, personal conversations or unsanitized logs in a report.

### Update and recover

Use `/update` inside MO, or run `mo update` / `mo --update` in a terminal.
All check the same official preview channel, verify the selected download and
open the same installer to apply a newer version when available. Reinstalling
manually is not the normal update workflow. A build without a configured
release channel reports that automatic updates are unavailable and changes
nothing.

Existing source checkouts retain their separate Git-based update route.
`/doctor` and `/credentials` diagnose runtime and provider readiness without
showing credential values. An update failure does not call for deleting your
private MO home.

Android updates come from Google Play for eligible users; see
[Android availability](ANDROID.md#availability).

### Remove MO

Close MO, then use **Windows Settings > Apps > Installed apps > MO > Uninstall**.
Your private MO home and project folders are separate from the application.
Keep them if you want to preserve configuration, memory and conversations.
Removing the application must not be mistaken for erasing your profile,
revoking paired devices or closing a provider account. Do not delete a profile
directory or parent folder merely to remove application files.

## License

MO's existing source license is the PolyForm Shield License 1.0.0: you may
use, change and share it for any purpose except providing a product that
competes with MO Agent. See [LICENSE](LICENSE). Copies published up to commit
`b7e94b3a` (9 October 2026) were released under the MIT License and keep it.

Third-party components retain their original licenses and notices; MO's Shield
restrictions do not override their grants. For covered combined works, MO
permits modification for your own use and reverse engineering to debug those
modifications, with library replacement or relinking rights as required by the
applicable [LGPL 2.1 section 6](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n9.0.2/COPYING.LGPLv2.1)
or [LGPL 3 section 4](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n9.0.2/COPYING.LGPLv3).
These preserved rights do not establish that every proposed component or binary
is cleared for public distribution; each release must satisfy its own terms.
