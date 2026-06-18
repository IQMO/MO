"""MO agent PRT integration mixin — extracted from core/agent.py (DEVMODE05 Phase 3)."""

import os
import subprocess
import threading
from pathlib import Path
import traceback


class AgentPRT:
    """PRT (Project Review Team) integration methods for the MO Agent."""

    @staticmethod
    def _prt_adaptive_action(complexity: str, risk: str, prt_config: dict) -> str:
        """Return suggest/empty for post-commit PRT gating."""
        complexity_value = str(complexity or "").strip().lower()
        risk_value = str(risk or "").strip().lower()
        cfg = prt_config if isinstance(prt_config, dict) else {}
        large = complexity_value in {"complex", "high"} or risk_value == "high"
        medium = complexity_value in {"moderate", "medium"} or risk_value == "medium"
        # PRT should never auto-run after commits; large/high-risk work is suggested instead.
        if large:
            return "suggest" if cfg.get("ghost_suggest_medium", True) else ""
        if medium and cfg.get("ghost_suggest_medium", True):
            return "suggest"
        return ""

    def _trigger_prt_adaptive_gate(self, tool_name: str, arguments: dict) -> None:
        """Evaluate if PRT should run after a commit."""
        try:
            prt_config = self.config.get("prt", {}) if isinstance(getattr(self, "config", {}), dict) else {}
            if not prt_config.get("enabled", True):
                return

            is_commit = False
            if tool_name == "shell":
                cmd = str((arguments or {}).get("command", "")).lower()
                if "git " in cmd and "commit " in cmd:
                    is_commit = True
            elif tool_name == "git_status": # Just in case it's added later
                pass

            if not is_commit:
                return

            # Pin the just-created commit SHA now: by the time the deferred gate
            # runs, another commit may have moved HEAD, so HEAD~1..HEAD would
            # analyze the wrong change set.
            cwd = str(getattr(self, "project_cwd", "") or getattr(self, "workspace", "") or os.getcwd())
            try:
                commit_sha = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"],
                    text=True, encoding="utf-8", errors="replace",
                    stderr=subprocess.DEVNULL, cwd=cwd,
                ).strip() or "HEAD"
            except Exception:
                commit_sha = "HEAD"

            def gate_check():
                import time
                time.sleep(1.0)
                try:
                    diff_text = subprocess.check_output(
                        ["git", "diff", f"{commit_sha}~1", commit_sha],
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        stderr=subprocess.DEVNULL,
                        cwd=cwd,
                    )

                    # Phase C: Adaptive Gate
                    from core.work_patterns import estimate_work_complexity
                    complexity = estimate_work_complexity(diff_text)

                    from core.graph.code_graph import risk_score, analyze_diff_impact
                    impacted = analyze_diff_impact(diff_text, root=cwd)

                    import re
                    changed_files = []
                    for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", diff_text, flags=re.MULTILINE):
                        changed_files.append(match.group(2))

                    r_score = risk_score(changed_files, impacted)
                    try:
                        from core.graph.structural_graph import maybe_update_graph_async
                        maybe_update_graph_async(profile=getattr(self, "profile", None), reason="post-commit")
                    except Exception:
                        traceback.print_exc()

                    action = self._prt_adaptive_action(complexity, r_score, prt_config)
                    if action == "suggest":
                        setattr(self, "_prt_ghost_suggestion", "HEAD")
                except Exception:
                    traceback.print_exc()

            threading.Thread(target=gate_check, daemon=True, name="mo-prt-gate").start()
        except Exception:
            traceback.print_exc()

    def _prt_claimed_paths(self, diff_ref: str) -> list[str]:
        """Return changed paths for a PRT diff ref, best-effort for worker coordination."""
        try:
            ref_text = str(diff_ref or "").strip()
            cwd_path = Path(str(getattr(self, "project_cwd", "") or getattr(self, "workspace", "") or os.getcwd())).resolve(strict=False)
            if ref_text and (cwd_path / ref_text).resolve(strict=False).exists():
                return [ref_text.replace("\\", "/")]
            cwd = str(cwd_path)
            proc = subprocess.run(
                ["git", "diff", "--name-only", f"{diff_ref}~1", diff_ref],
                text=True,
                capture_output=True,
                timeout=3,
                cwd=cwd,
            )
        except Exception:
            return []
        if proc.returncode != 0:
            return []
        from ..workers import normalize_worker_paths
        return normalize_worker_paths([line.strip() for line in proc.stdout.splitlines() if line.strip()])
