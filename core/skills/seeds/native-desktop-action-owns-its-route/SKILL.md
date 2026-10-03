---
name: "Native Desktop action owns its route"
description: "Apply request-local native MO Desktop action routing and verification conventions"
triggers:
  - "native Desktop action"
  - "request-locally admitted"
  - "Desktop action route"
  - "action owns its route"
provenance: "seed"
approval: "shipped"
scope: "mo_desktop/** interface/** core/** tests/** docs/**"
mastery_uses: 0
mastery_successes: 0
mastery_corrections: 0
---
Use these conventions when an explicitly admitted request reaches MO Desktop or its supporting runtime paths:

- Explanation or pointing alone does not authorize another state-changing action. Keep work within the current request and its conversation-aware continuation.
- Admission primes tool discovery, not execution permission or a veto over the model's reading. Gateway, role/sandbox boundaries, exact targets, fresh observations, and high-impact confirmation remain authoritative; see `mo_desktop/MAINTAINING.md`.
- Reuse the existing Desktop action and control routes before introducing a helper, transport, or state owner.
- For changed native behavior, verify the exact loaded candidate and affected interaction. Reuse completed evidence for unchanged behavior; documentation edits alone do not require a resident restart.
