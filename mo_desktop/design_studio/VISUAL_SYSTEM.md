# MO Design visual-system direction

Status: active implementation authority for MO Design Studio chrome
Model-route guidance: [shared Terminal selection](README.md#runtime-flow), not a Studio chooser
Scope: trusted Studio and standalone Board chrome; generated Preview content remains isolated

## Why this exists

The end-to-end review found a sound workflow and state architecture, but a visually
uniform shell. Too many similarly rounded, translucent surfaces weakened hierarchy,
and the single large HTML shell made visual iteration riskier than necessary. This
document records the intended correction so future work does not mistake the old
appearance for the target or rewrite the proven workflow.

## Preserve these invariants

- Preview is the primary judgment surface; standalone conversation is a quieter supporting rail.
- Tray opens Welcome independently of working windows. Its three-step introduction
  uses shared tokens, a short one-pass animation, and reduced-motion support.
- Terminal-linked Design hides the entire conversation/Handoff rail and its
  history shortcuts; manual editing and Download keep the available width.
- Board and Preview are two views of one `.modesign` artifact.
- MO Board proposals remain visually distinct and require explicit Accept or Reject.
- Handoff remains a separate final boundary and does not become a recurring prompt.
- Generated Preview HTML, CSS, and script remain sandboxed from trusted Studio chrome.
- Studio consumes the current MO skin and panel geometry; it does not create a second theme.
- Project files remain read-only throughout Design work.
- Streaming keeps the last accepted visual visible and uses one truthful activity line.

## Visual hierarchy

Use four explicit layers instead of treating every region as a floating card:

1. **Window frame** — quiet MO background and one structural border.
2. **Workspace chrome** — title bar, preview toolbar, and activity line; compact and stable.
3. **Judgment surface** — Preview or Board; the largest, highest-contrast working area.
4. **Supporting rail** — conversation, project context, brief, and composer; visibly secondary.

Brand color communicates selection, focus, or a primary decision. It is not ambient
decoration. Glass blur is not part of the Studio language. Large pill geometry is
reserved for compact status metadata, not containers, menus, or message cards.

## Shape and spacing

- Structural regions use square joins or the shared button radius, not the panel radius.
- The Preview viewport may use the shared panel radius because it represents the artifact.
- Status badges may use the pill radius when their compact metadata role is unambiguous.
- Controls provide a minimum 32 px pointer target; primary actions provide at least 36 px.
- App title bars share `interface.desktop_brand`'s 44 px height, 32 px window
  controls, glyphs and finite hover treatment. Studio owns only the layout of
  its workspace tools and metadata within that header.
- Dividers and spacing establish grouping before additional containers are introduced.

## Typography and interaction

- The concept title leads the Preview toolbar; revision and connection are metadata.
- The rail heading leads project context, then brief, conversation, composer, and Handoff.
- Every icon-only control has an accessible name and visible keyboard focus.
- Board toolbar tools use aligned inline SVG icons rather than Unicode glyphs; the 36 px controls contain 20 px icons and preserve the existing tool labels and shortcuts.
- Preview provides an explicit Edit design / Done mode, element selection, drag and resize, a compact inspector, save status, and undo/redo. Inspector colors and geometry consume shared MO tokens.
- Board controls include image insertion, shared element properties, resize and arrow endpoint/bend handles, and whiteboard paper/ink derived from the active skin.
- Double-clicking an element opens its properties, including editing existing text. Styling and handle edits preserve element identity, revisions, and undo across autosave; properties reuse the existing MO form styling.
- Segmented controls expose selected state through `aria-pressed` or `aria-selected`.
- Dialogs have programmatic names and preserve native modal focus behavior.
- Board is keyboard focusable and its shortcut reference remains available with `?`.
- Status and error changes remain announced through bounded live regions.

## Responsive contract

- **Wide (over 930 px):** Preview and conversation appear side by side.
- **Compact (761–930 px):** the rail narrows but the Preview remains primary.
- **Narrow (760 px and below):** workspace stacks; title actions remain reachable and
  Preview/Board receives the larger share of height.
- **Very narrow (560 px and below):** nonessential title metadata is hidden, window controls remain reachable,
  dialog width follows the viewport, and no primary action is clipped.
- Standalone Board never gains conversation, library, Handoff, or hidden Preview controls.
- Terminal-linked Preview never gains the supporting rail at narrow sizes.

## Required state matrix

Visual changes are not complete until these representative states remain legible:

- boot and reconnecting;
- Welcome, its saved-design chooser, and terminal-linked Preview;
- empty conversation and saved static concept;
- interactive prototype;
- active/streaming work with the last accepted Preview retained;
- first-turn attention with no completed Preview, later attention with an
  unaccepted partial revision, and Preview-script failure recovery;
- Preview desktop, tablet, and mobile sizing;
- editable Board, selected element, and read-only historical Board;
- pending MO Board proposal with Accept and Reject;
- revision history and delete confirmation;
- reuse of Terminal's saved model and an explicitly labeled failure-triggered model fallback;
- composer attachment picker, removable chips, attachment-only send, and
  attachment metadata in conversation history;
- Handoff with available and unavailable routes;
- wide, compact, narrow, reduced-motion, and keyboard-focus presentations.

## Frontend ownership

- `studio.html` owns semantic trusted-chrome markup and the CSP boundary.
- `studio_visual.css` owns workspace and responsive/accessibility styling;
  `interface.desktop_brand` owns the shared app title bar and window buttons.
- `studio.html` owns shell orchestration and Board behavior. `preview_editor.js`
  owns the sandbox Preview runtime and its trusted manual-edit controller.
  Visual changes must not add behavior to the stylesheet.
- `mo_renderer.py` composes the trusted local stylesheet and Preview controller into the shell before pywebview
  receives it. The result remains inline and network-free.
- `theme.py` remains the authority for skin-derived colors and geometry.
- `bridge.py` remains the narrow trusted API and state coordinator.

This separation is deliberate: it removes day-to-day visual work from the behavioral
shell without introducing Node, a build step, a second runtime format, or external assets.

## Acceptance

A visual-system change is complete when:

- the Studio visual checker reports no glass or oversized-container-radius warnings;
- focused renderer, Board, Desktop, and tool tests pass;
- semantic assertions cover selection state, dialog naming, Board focusability, and
  responsive/reduced-motion rules;
- a real pywebview Studio and standalone Board launch successfully;
- representative Preview, Board, draft-review, dialog, and narrow-window states are
  exercised with fresh post-action observations;
- docs match the final ownership and interaction behavior.
