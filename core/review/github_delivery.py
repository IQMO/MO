"""PRT → GitHub delivery (Phase 1). Post PRT's verified review back to a GitHub PR.

Default OFF and DRY-RUN by default. Reuses the review engine (``review_diff`` over
the PR's ``base...head`` range), the secret redactor (nothing reaches a public PR
unredacted), and ``gh`` for every GitHub op — BYOK, self-hosted, no SaaS. Posts a
PR review (summary + score + findings) and a commit status; the score maps to a
pass/fail check against the target.

Guardrails: never raises (a delivery failure can't break anything); posts only when
PRT and ``maintainer_github`` are enabled AND ``post=True`` is passed (the default
is a dry-run that returns the formatted review without touching GitHub).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .maintainer import maintainer_settings
from ..runtime.subprocess_flags import apply_windows_hidden_process_flags

_STATUS_CONTEXT = "MO PRT"


def github_enabled(agent: Any) -> bool:
    """True when both PRT and its GitHub delivery arm are enabled."""
    try:
        s = maintainer_settings(agent)
        return bool(s.enabled and s.github)
    except Exception:
        return False


def _cwd(agent: Any) -> str:
    return str(getattr(agent, "project_cwd", "") or getattr(agent, "workspace", "") or os.getcwd())


def _bind_review_workspace(agent: Any) -> str:
    """Bind the Actions-created PR worktree without importing code from it."""
    configured = str(os.environ.get("MO_PROJECT_CWD") or "").strip()
    if not configured:
        return _cwd(agent)
    root = Path(configured).expanduser().resolve(strict=False)
    if not root.is_dir() or not (root / ".git").exists():
        raise RuntimeError("MO_PROJECT_CWD is not an existing Git worktree")
    resolved = str(root)
    agent.project_cwd = resolved
    agent.workspace = resolved
    agent.allowed_roots = [resolved]
    return resolved


def _gh(agent: Any, args: list[str], *, input_text: str | None = None) -> tuple[bool, str]:
    """Run a ``gh`` command in the repo. Returns (ok, stdout|error). Never raises."""
    try:
        run_kwargs = {
            "text": True, "encoding": "utf-8", "errors": "replace",
            "capture_output": True, "input": input_text,
            "cwd": _cwd(agent), "timeout": 60,
        }
        apply_windows_hidden_process_flags(run_kwargs)
        proc = subprocess.run(["gh", *args], **run_kwargs)
        if proc.returncode == 0:
            return True, (proc.stdout or "").strip()
        return False, (proc.stderr or proc.stdout or f"gh exit {proc.returncode}").strip()
    except FileNotFoundError:
        return False, "gh CLI not installed"
    except Exception as exc:  # timeout / unexpected
        return False, f"{type(exc).__name__}: {exc}"


def pr_context(agent: Any, pr: str) -> dict | None:
    """Resolve a PR's immutable base/head revisions via ``gh``."""
    ok, out = _gh(agent, [
        "pr", "view", str(pr), "--json",
        "number,baseRefName,baseRefOid,headRefName,headRefOid,url",
    ])
    if not ok:
        return None
    try:
        data = json.loads(out)
    except Exception:
        return None
    ok2, owner_repo = _gh(agent, ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    return {
        "number": data.get("number"),
        "base": str(data.get("baseRefName") or ""),
        "base_sha": str(data.get("baseRefOid") or ""),
        "head": str(data.get("headRefName") or ""),
        "head_sha": str(data.get("headRefOid") or ""),
        "url": str(data.get("url") or ""),
        "owner_repo": owner_repo if ok2 else "",
    }


def _git_output(agent: Any, args: list[str]) -> str:
    """Run a read-only Git command in the bound review worktree."""
    try:
        run_kwargs = {
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "capture_output": True,
            "cwd": _cwd(agent),
            "timeout": 60,
        }
        apply_windows_hidden_process_flags(run_kwargs)
        proc = subprocess.run(["git", *args], **run_kwargs)
        return proc.stdout if proc.returncode == 0 else ""
    except Exception:
        return ""


def _pr_target_error(
    agent: Any,
    pr: str,
    target: dict[str, Any],
    *,
    context: dict | None = None,
) -> str:
    """Return why *target* no longer matches GitHub or its prepared worktree."""
    live = context if context is not None else pr_context(agent, pr)
    if not live:
        return "could not revalidate the prepared PR target"
    if str(live.get("base_sha") or "") != target["base"]:
        return "PR base changed after the review worktree was prepared"
    if str(live.get("head_sha") or "") != target["head"]:
        return "PR head changed after the review worktree was prepared"
    if target["prepared"] and _git_output(agent, ["rev-parse", "HEAD"]).strip() != target["head"]:
        return "review worktree does not match the pinned PR head"
    return ""


def _resolve_pr_target(
    agent: Any,
    pr: str,
    *,
    require_prepared: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    """Resolve one immutable base/head pair and validate the Actions worktree."""
    expected_base = str(os.environ.get("MO_PRT_EXPECTED_BASE") or "").strip()
    expected_head = str(os.environ.get("MO_PRT_EXPECTED_HEAD") or "").strip()
    prepared = bool(expected_base or expected_head)
    if prepared and not (expected_base and expected_head):
        return None, "prepared PR target is missing its base or head commit"
    if require_prepared and not prepared:
        return None, "prepared PR target is unavailable"

    context = pr_context(agent, pr)
    if not context:
        return None, "could not resolve PR via gh (not installed / not authed / no such PR)"
    target = {
        "base": expected_base or str(context.get("base_sha") or ""),
        "head": expected_head or str(context.get("head_sha") or ""),
        "context": context,
        "prepared": prepared,
    }
    if not target["base"] or not target["head"]:
        return None, "PR context does not include immutable base and head commits"
    error = _pr_target_error(agent, pr, target, context=context)
    return (None, error) if error else (target, "")


_LABEL = {"critical": "BLOCKER", "major": "SUGGESTION", "minor": "MINOR", "info": "NOTE"}


def _is_anchorable(f: Any) -> bool:
    """True when a finding maps to a concrete new-side file:line (postable inline).
    Synthetic findings (``<callgraph>``, ``<diff>``, ``<review>``) are not."""
    file = str(getattr(f, "file", "") or "")
    lr = getattr(f, "line_range", None) or [0, 0]
    return bool(file) and not file.startswith("<") and bool(lr and lr[0])


def _suggestion_block(text: str) -> str:
    """If a finding's suggestion carries fenced code, surface it as a GitHub one-click
    ``suggestion`` block (applies to the commented line). '' for prose suggestions —
    reuses the model's own replacement only when it actually gave one."""
    m = re.search(r"```[a-zA-Z0-9]*\n(.*?)```", str(text or ""), re.DOTALL)
    if not m:
        return ""
    code = m.group(1).rstrip("\n")
    return f"\n\n```suggestion\n{code}\n```" if code.strip() else ""


def _inline_comments(report: Any) -> list[dict]:
    """Anchorable findings → GitHub review inline comments (new side), redacted.
    Adds a one-click ``suggestion`` block when the model supplied replacement code."""
    from .critic import redact_secret_material
    out: list[dict] = []
    for f in list(getattr(report, "findings", []) or []):
        if not _is_anchorable(f):
            continue
        sev = str(getattr(f, "severity", "info") or "info").lower()
        conf = float(getattr(f, "confidence", 0.0) or 0.0)
        parts = [f"**[{_LABEL.get(sev, sev.upper())}]** {str(getattr(f, 'message', '') or '')} _(confidence {conf:.0%})_"]
        why = str(getattr(f, "rationale", "") or "").strip()
        sug = str(getattr(f, "suggestion", "") or "").strip()
        if why:
            parts.append(f"\n\n_why:_ {why}")
        if sug:
            parts.append(f"\n\n_suggestion:_ {sug}")
        block = _suggestion_block(sug)
        if block:
            parts.append(block)
        cbody, _changed = redact_secret_material("".join(parts))
        out.append({
            "path": str(getattr(f, "file", "") or ""),
            "line": int((getattr(f, "line_range", None) or [0])[0]),
            "side": "RIGHT",
            "body": cbody,
        })
    return out


def _oversize_note(additions: Any, deletions: Any, files: Any) -> str:
    """Advisory when a PR's surface is large enough to degrade review precision:
    steer toward smaller, sequential 'stacked' PRs (backend → UI → parser). '' below
    the threshold. The GitHub-side twin of MO's local large-change split nudge."""
    try:
        changed = int(additions or 0) + int(deletions or 0)
        nfiles = int(files or 0)
    except (TypeError, ValueError):
        return ""
    if changed >= 1000 or nfiles >= 12:
        return (f"> ⚠️ **Large change** — {changed} changed line(s) across {nfiles} file(s). "
                "Smaller, sequential PRs (e.g. backend → UI → parser) review with higher precision.")
    return ""


def format_review(report: Any, *, inline: bool = False, redact: bool = True) -> str:
    """Redacted PR-review body: score + verdict, positives, and findings with
    severity/confidence — the same evidence-weighted content PRT shows locally.

    ``inline=True`` drops the line-anchored findings from the body (they are posted as
    inline PR comments) and keeps the summary, positives, and non-anchorable
    (call-graph / whole-diff) findings."""
    from .critic import redact_secret_material
    from .prt_report import history_coverage_line, review_is_incomplete, verification_coverage_lines

    score = float(getattr(report, "score", 0.0) or 0.0)
    target = float(getattr(report, "score_target", 4.5) or 4.5)
    incomplete = review_is_incomplete(report)
    met = not incomplete and bool(getattr(report, "is_target_met", False))
    findings = list(getattr(report, "findings", []) or [])
    positives = list(getattr(report, "positives", []) or [])
    score_line = (
        f"**Score: unavailable** (review incomplete; target {target})"
        if incomplete else
        f"**Score: {score}/5.0** ({'aligned' if met else 'needs work'}; target {target})"
    )

    lines = [
        "## ◈ MO PRT review",
        "",
        f"{score_line} · {len(findings)} finding(s) · "
        f"+{int(getattr(report, 'additions', 0) or 0)}/-{int(getattr(report, 'deletions', 0) or 0)}",
    ]
    note = _oversize_note(getattr(report, "additions", 0), getattr(report, "deletions", 0), getattr(report, "files_changed", 0))
    if note:
        lines += ["", note]
    history_line = history_coverage_line(report)
    if history_line:
        lines += ["", history_line]
    lines += ["", *verification_coverage_lines(report)]
    if positives:
        lines += ["", "**What's good**"] + [f"- {p}" for p in positives[:6]]

    body_findings = [f for f in findings if not (inline and _is_anchorable(f))]
    inline_count = len(findings) - len(body_findings)
    if body_findings:
        lines += ["", "**Findings**"]
        for f in body_findings:
            sev = str(getattr(f, "severity", "info") or "info").lower()
            loc = str(getattr(f, "file", "") or "")
            lr = getattr(f, "line_range", None) or [0, 0]
            if lr and lr[0]:
                loc += f":{lr[0]}"
            conf = float(getattr(f, "confidence", 0.0) or 0.0)
            msg = str(getattr(f, "message", "") or "")
            why = str(getattr(f, "rationale", "") or "").strip()
            if loc == "<review>":
                why = str(getattr(f, "explanation", "") or why).strip()
            label = "REVIEW ERROR" if loc == "<review>" else _LABEL.get(sev, sev.upper())
            lines.append(f"- **[{label}]** `{loc}` — {msg}  _(confidence {conf:.0%})_")
            if why:
                lines.append(f"  - why: {why}")
    if inline_count:
        lines += ["", f"_{inline_count} line-anchored finding(s) posted as inline comments._"]
    if not findings:
        lines += ["", "No issues found — clean."]

    if incomplete:
        lines += ["", "_PRT could not complete this review, so no code score was assigned. Resolve the review error and rerun._"]
    else:
        lines += [
            "",
            "_◈ MO PRT — evidence-gated: file and call-graph evidence is used where "
            "available; graph and affected-test candidate selection are bounded samples, "
            "not exhaustive coverage. Unproved provider concerns are excluded. Score and "
            "confidence are evidence-weighted heuristics, not correctness probabilities. MO's local review "
            "agent, not a human._",
        ]
    body = "\n".join(lines)
    if redact:
        body, _changed = redact_secret_material(body)
    return body


def review_pr(agent: Any, pr: str, *, post: bool | None = None) -> dict:
    """Review a GitHub PR with PRT and (optionally) post the result back.

    ``post=None`` (default) → dry-run: format the review, do NOT touch GitHub, and
    return it for preview. ``post=True`` posts only when the GitHub arm is enabled.
    Never raises."""
    result: dict[str, Any] = {"pr": str(pr), "posted": False, "ok": False}
    try:
        target, error = _resolve_pr_target(agent, pr)
        if error or target is None:
            result["error"] = error
            return result
        ctx = target["context"]
        diff_range = f"{target['base']}...{target['head']}"

        # A GitHub runner/worktree starts without graph state. Build explicitly
        # so structural impact and affected-test selection are current.
        try:
            from core.graph.structural_graph import build_structural_graph_isolated
            graph_result = build_structural_graph_isolated(_cwd(agent))
            result["graph_ready"] = bool(graph_result.get("built") or graph_result.get("skipped"))
        except Exception:
            result["graph_ready"] = False

        from .diff_review import review_diff
        report = review_diff(
            agent, diff_range,
            structural_evidence_root=_cwd(agent),
            execute_affected_tests=False,
            include_private_history=False,
        )

        def _body(inline: bool) -> str:
            return (
                format_review(report, inline=inline)
                + "\n\n**Affected tests:** unavailable in this GitHub review — PR-controlled "
                "test code is not executed while review credentials are present."
            )

        body = _body(False)
        score = float(getattr(report, "score", 0.0) or 0.0)
        from .prt_report import review_is_incomplete
        incomplete = review_is_incomplete(report)
        met = not incomplete and bool(getattr(report, "is_target_met", False))
        result.update({
            "ok": True,
            "range": diff_range,
            "score": None if incomplete else score,
            "verdict": "incomplete" if incomplete else ("aligned" if met else "needs_work"),
            "body": body,
            "url": ctx.get("url", ""),
        })

        comments = _inline_comments(report)
        result["inline_comments"] = len(comments)
        should_post = bool(post) and github_enabled(agent)
        result["dry_run"] = not should_post
        if not should_post:
            return result

        owner_repo = ctx.get("owner_repo", "")
        num = ctx.get("number")
        sha = target["head"]

        def _posting_drift() -> bool:
            drift = _pr_target_error(agent, pr, target)
            if not drift:
                return False
            result.update({"ok": False, "error": drift})
            return True

        # 1) Post a review with line-anchored inline comments; fall back to a body-only
        #    review if GitHub rejects a comment position (line outside the PR diff).
        inline_ok, out = False, ""
        if owner_repo and num and comments:
            if _posting_drift():
                return result
            payload = json.dumps({
                "commit_id": sha,
                "body": _body(True),
                "event": "COMMENT",
                "comments": comments,
            })
            inline_ok, out = _gh(agent, ["api", "--method", "POST", f"repos/{owner_repo}/pulls/{num}/reviews", "--input", "-"], input_text=payload)
        if inline_ok:
            ok_review = True
        else:
            if _posting_drift():
                return result
            payload = json.dumps({
                "commit_id": sha,
                "body": _body(False),
                "event": "COMMENT",
            })
            ok_review, out = _gh(
                agent,
                [
                    "api",
                    "--method",
                    "POST",
                    f"repos/{owner_repo}/pulls/{num}/reviews",
                    "--input",
                    "-",
                ],
                input_text=payload,
            ) if owner_repo and num else (False, "missing PR repository context")
        result["inline"] = bool(inline_ok)

        # 2) Set a commit status: review-engine failures are infrastructure errors,
        #    not 0/5 code-quality verdicts. Completed reviews still pass/fail vs target.
        ok_status = False
        if incomplete:
            state = "error"
            desc = "PRT review incomplete — no code score assigned"
        else:
            state = "success" if met else "failure"
            desc = f"PRT {score}/5.0 ({'aligned' if met else 'needs work'})"
        if owner_repo and sha:
            if _posting_drift():
                result.update({"posted": bool(ok_review), "status_set": False})
                return result
            ok_status, _s = _gh(agent, [
                "api", "--method", "POST", f"repos/{owner_repo}/statuses/{sha}",
                "-f", f"state={state}", "-f", f"context={_STATUS_CONTEXT}", "-f", f"description={desc}",
            ])
        result["posted"] = bool(ok_review)
        result["status_set"] = bool(ok_status)
        result["status_state"] = state
        if not ok_review:
            result["error"] = f"review post failed: {out}"
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


def _diff_for_target(agent: Any, target: dict[str, Any]) -> str:
    out = _git_output(
        agent,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            f"{target['base']}...{target['head']}",
            "--",
        ],
    )
    if not out:
        return ""
    from .critic import redact_secret_material
    txt, _c = redact_secret_material(out)
    return txt


def _pr_diff(agent: Any, pr: str) -> str:
    """Read the exact pinned PR diff locally; never follow a moving ``gh pr diff``."""
    target, error = _resolve_pr_target(agent, pr)
    return "" if error or target is None else _diff_for_target(agent, target)


def describe_pr(
    agent: Any,
    pr: str,
    *,
    max_chars: int = 8000,
    _target: dict[str, Any] | None = None,
) -> str:
    """A short PR summary (what/why + one-line risk) — the `/describe` analog.

    Reuses the no-tools model over the (redacted) PR diff; returns '' if unavailable."""
    diff = _diff_for_target(agent, _target) if _target is not None else _pr_diff(agent, pr)
    if not diff:
        return ""
    messages = [
        {"role": "system", "content": (
            "You summarize a pull request for reviewers in 2-4 sentences: what it changes "
            "and why, then a one-line risk note. No preamble, no markdown headers.")},
        {"role": "user", "content": f"Summarize this PR diff:\n\n{diff[:max_chars]}"},
    ]
    try:
        resp, _p = agent.complete_no_tools(surface="review-standard", request="pr-describe", messages=messages, max_tokens=400)
        return str(getattr(resp, "content", "") or "").strip()
    except Exception:
        return ""


def _post_pr_comment(
    agent: Any,
    pr: str,
    body: str,
    target: dict[str, Any],
) -> tuple[bool, str]:
    error = _pr_target_error(agent, pr, target)
    if error:
        return False, error
    ok, out = _gh(agent, ["pr", "comment", str(pr), "--body-file", "-"], input_text=body)
    return ok, "" if ok else out


def answer_pr_question(
    agent: Any,
    pr: str,
    question: str,
    *,
    post: bool = False,
    _target: dict[str, Any] | None = None,
) -> str:
    """Answer a reviewer's question about a PR from its diff (no tools), redacted.
    Posts as a PR comment when *post* and the GitHub arm is enabled."""
    should_post = bool(post) and github_enabled(agent)
    target = _target
    if target is None:
        target, error = _resolve_pr_target(agent, pr, require_prepared=should_post)
        if error or target is None:
            return ""
    diff = _diff_for_target(agent, target)
    if not diff:
        return ""
    messages = [
        {"role": "system", "content": "You answer a reviewer's question about a pull request, from the diff. Be concise and specific; say so if the diff doesn't show the answer."},
        {"role": "user", "content": f"PR diff:\n\n{diff[:8000]}\n\nQuestion: {str(question or '').strip()}"},
    ]
    try:
        resp, _p = agent.complete_no_tools(surface="review-standard", request="pr-ask", messages=messages, max_tokens=600)
        answer = str(getattr(resp, "content", "") or "").strip()
    except Exception:
        return ""
    from .critic import redact_secret_material
    answer, _c = redact_secret_material(answer)
    if should_post and answer:
        posted, _error = _post_pr_comment(agent, pr, f"**@mo:** {answer}", target)
        if not posted:
            return ""
    return answer


def plan_issue(agent: Any, issue: str, *, post: bool = False) -> dict:
    """Issue → coding plan: reuse the goal decomposer on the issue text, post the plan.
    The CodeRabbit/Pullfrog 'Issue Planner' analog, on MO's evidence-gated planner."""
    result: dict[str, Any] = {"issue": str(issue), "ok": False}
    ok, out = _gh(agent, ["issue", "view", str(issue), "--json", "title,body"])
    if not ok:
        result["error"] = "gh issue view failed"
        return result
    try:
        data = json.loads(out)
    except Exception:
        result["error"] = "bad issue json"
        return result
    objective = f"{data.get('title', '')}\n\n{data.get('body', '')}".strip()
    try:
        from core.goal import decompose_goal
        steps = decompose_goal(objective)
    except Exception as exc:
        result["error"] = f"decompose failed: {exc}"
        return result
    plan_md = "**@mo — coding plan for this issue**\n\n" + "\n".join(
        f"{i + 1}. {str(getattr(s, 'title', s))}" for i, s in enumerate(steps))
    from .critic import redact_secret_material
    plan_md, _c = redact_secret_material(plan_md)
    result.update({"ok": True, "steps": len(steps), "plan": plan_md})
    if post and github_enabled(agent):
        posted, _o = _gh(agent, ["issue", "comment", str(issue), "--body-file", "-"], input_text=plan_md)
        result["posted"] = bool(posted)
    return result


def handle_pr_comment(agent: Any, pr: str, comment_body: str, *, post: bool = True) -> dict | None:
    """Dispatch an ``@mo ...`` PR comment: ``/prt`` (review), ``/describe``, or a
    question (``/ask <q>`` or a bare question). None when @mo is not addressed."""
    text = str(comment_body or "").strip()
    if "@mo" not in text.lower():
        return None
    after = text[text.lower().index("@mo") + 3:].strip()
    low = after.lower()
    if low.startswith(("/prt", "review")):
        return {"action": "review", "result": review_pr(agent, pr, post=post)}
    if low.startswith("/describe"):
        should_post = bool(post) and github_enabled(agent)
        target = None
        if should_post:
            target, error = _resolve_pr_target(agent, pr, require_prepared=True)
            if error or target is None:
                return {"action": "describe", "summary": "", "error": error}
        summary = describe_pr(agent, pr, _target=target)
        result = {"action": "describe", "summary": summary}
        if should_post and summary and target is not None:
            posted, error = _post_pr_comment(
                agent,
                pr,
                f"**@mo — summary:** {summary}",
                target,
            )
            result["posted"] = posted
            if error:
                result["error"] = error
        return result
    question = after
    for prefix in ("/ask", "ask"):
        if question.lower().startswith(prefix):
            question = question[len(prefix):]
            break
    question = question.strip(" :")
    if question:
        should_post = bool(post) and github_enabled(agent)
        target = None
        if should_post:
            target, error = _resolve_pr_target(agent, pr, require_prepared=True)
            if error or target is None:
                return {"action": "ask", "answer": "", "error": error}
        answer = answer_pr_question(agent, pr, question, _target=target)
        result = {"action": "ask", "answer": answer}
        if should_post and answer and target is not None:
            posted, error = _post_pr_comment(agent, pr, f"**@mo:** {answer}", target)
            result["posted"] = posted
            if error:
                result["error"] = error
        return result
    return {"action": "none"}


def _review_cli_succeeded(result: dict, *, post: bool) -> bool:
    """True for previews, or for an aligned review delivered with its status."""
    if not result.get("ok") or result.get("verdict") == "incomplete":
        return False
    if not post or result.get("dry_run"):
        return True
    return bool(
        result.get("verdict") == "aligned"
        and result.get("posted")
        and result.get("status_set")
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the GitHub arm (used by the example Action).

    Usage: ``python -m core.review.github_delivery <pr> [--post] [--config PATH]``

    Without ``--post`` it is a DRY RUN (prints the review, posts nothing). With
    ``--post`` it posts only when ``prt.enabled`` + ``prt.maintainer_github`` are
    enabled in the config the runner uses."""
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in {"-h", "--help"}:
        print("usage: python -m core.review.github_delivery <pr> [--post] [--config PATH]")
        return 2
    pr = args[0]
    post = "--post" in args
    config_path = None
    if "--config" in args:
        i = args.index("--config")
        if i + 1 < len(args):
            config_path = args[i + 1]
    try:
        from core.agent.agent import create_agent
        agent = create_agent(config_path)
        _bind_review_workspace(agent)
    except Exception as exc:
        print(f"MO PRT: could not start agent: {exc}")
        return 1

    # @mo comment dispatch (issue_comment Action). Read the (untrusted) body from the
    # MO_COMMENT env var when no inline value is given — avoids shell-arg injection.
    if "--comment" in args:
        import os
        i = args.index("--comment")
        body = args[i + 1] if (i + 1 < len(args) and not args[i + 1].startswith("--")) else os.environ.get("MO_COMMENT", "")
        res = handle_pr_comment(agent, pr, body, post=post) or {"action": "none"}
        print(json.dumps(res, default=str)[:2000])
        if res.get("error"):
            return 1
        if res.get("action") == "review":
            return 0 if _review_cli_succeeded(res.get("result") or {}, post=post) else 1
        return 0
    # Issue → coding plan (the pr arg is the issue number).
    if "--plan-issue" in args:
        res = plan_issue(agent, pr, post=post)
        print(res.get("plan") or res.get("error", ""))
        return 0 if res.get("ok") else 1
    result = review_pr(agent, pr, post=post)
    if result.get("posted"):
        print(f"MO PRT: posted review to PR {pr} — {result.get('verdict')} ({result.get('score')}/5.0); status_set={result.get('status_set')}")
        return 0 if _review_cli_succeeded(result, post=post) else 1
    if result.get("ok"):
        print(result.get("body", ""))
        if post and result.get("dry_run"):
            print("\n[dry-run: enable prt.enabled and prt.maintainer_github to post]")
        return 0 if _review_cli_succeeded(result, post=post) else 1
    print(f"MO PRT: {result.get('error', 'review failed')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
