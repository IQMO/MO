"""User-facing Terminal operations for Game Collaboration."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .model import append_history, clean_text, next_item_id, normalize_project_root
from .store import GameCollaborationError, GameCollaborationStore


class GameCollaborationService:
    """Keep game-project lifecycle and decisions separate from taskboards."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.store = GameCollaborationStore(config)

    @staticmethod
    def _cwd(value: str | Path | None) -> Path:
        return normalize_project_root(value or Path.cwd())

    def _resolve(self, selector: str, cwd: str | Path, *, create: bool = False) -> dict[str, Any] | None:
        value = str(selector or "").strip()
        current_root = self._cwd(cwd)
        if not value:
            record = self.store.find(project_root=current_root)
            if record is None and create:
                record = self.store.create(current_root)
            return record

        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = current_root / candidate
        if candidate.exists() and candidate.is_dir():
            root = candidate.resolve(strict=False)
            record = self.store.find(project_root=root)
            if record is None and create:
                record = self.store.create(root)
            return record

        record = self.store.find(value)
        if record is not None:
            return record
        if create:
            # A non-path selector is a user-facing display name for the current project.
            return self.store.create(current_root, value)
        return None

    @staticmethod
    def binding(record: dict[str, Any], mode: str = "active") -> dict[str, Any]:
        return {
            "mode": mode,
            "surface": "terminal",
            "project_key": str(record.get("project_key") or ""),
            "project_name": str(record.get("display_name") or "Game project"),
            "record_revision": int(record.get("record_revision", 0) or 0),
        }

    def start(self, selector: str, cwd: str | Path) -> tuple[dict[str, Any], bool]:
        before = self._resolve(selector, cwd, create=False)
        record = before or self._resolve(selector, cwd, create=True)
        if record is None:
            raise GameCollaborationError("Game project could not be resolved")
        if record.get("project_status") == "archived":
            raise GameCollaborationError("Game project is archived; choose another project")
        changed = str(record.get("project_status")) != "active"
        if changed:
            record = self._set_status(record, "active", "started")
        return record, before is None or changed

    def resume(self, selector: str, cwd: str | Path) -> dict[str, Any]:
        record = self._resolve(selector, cwd, create=False)
        if record is None:
            raise GameCollaborationError("Game project not found; use `/game start` in the project or provide its exact name")
        if record.get("project_status") == "archived":
            raise GameCollaborationError("Game project is archived and cannot be resumed")
        if record.get("project_status") != "active":
            record = self._set_status(record, "active", "resumed")
        return record

    def pause(self, binding: dict[str, Any]) -> dict[str, Any]:
        key = str(binding.get("project_key") or "")
        if not key:
            raise GameCollaborationError("No active Game Collaboration project")
        record = self.store.load(key)
        if record is None:
            raise GameCollaborationError("Game Collaboration project not found")
        return self._set_status(
            record,
            "paused",
            "paused",
            expected_revision=int(binding.get("record_revision", 0) or 0) or None,
        )

    def status(self, selector: str, cwd: str | Path) -> dict[str, Any] | None:
        return self._resolve(selector, cwd, create=False)

    def review(self, selector: str, cwd: str | Path) -> dict[str, Any] | None:
        return self.status(selector, cwd)

    def ask(self, binding: dict[str, Any], question_id: str, question: str) -> dict[str, Any]:
        key = self._active_key(binding)
        question_id = clean_text(question_id, 32).upper()
        question = clean_text(question, 1000)
        if not question_id or not question:
            raise GameCollaborationError("Use `/game ask <question-id> <question>`")

        def mutate(record: dict[str, Any]) -> dict[str, Any]:
            if any(str(item.get("id")) == question_id for item in record["open_questions"] if isinstance(item, dict)):
                raise GameCollaborationError(f"Question already exists: {question_id}")
            record["open_questions"].append({"id": question_id, "question": question, "status": "open"})
            append_history(record, "question_added", question_id)
            return record

        return self.store.update(key, mutate, expected_revision=self._revision(binding))

    def decide(self, binding: dict[str, Any], question_id: str, answer: str) -> dict[str, Any]:
        key = self._active_key(binding)
        question_id = clean_text(question_id, 32).upper()
        answer = clean_text(answer, 1800)
        if not question_id or not answer:
            raise GameCollaborationError("Use `/game decide <question-id> <answer>`")

        def mutate(record: dict[str, Any]) -> dict[str, Any]:
            question = next((item for item in record["open_questions"] if str(item.get("id")) == question_id), None)
            if question is not None:
                question["status"] = "resolved"
                question["answer"] = answer
            decision_id = next_item_id(record, "decisions", "D")
            record["decisions"].append({"id": decision_id, "question_id": question_id, "answer": answer, "status": "accepted"})
            append_history(record, "decision_recorded", f"{question_id}: {answer}")
            return record

        return self.store.update(key, mutate, expected_revision=self._revision(binding))

    def propose(self, binding: dict[str, Any], proposal_id: str, summary: str) -> dict[str, Any]:
        key = self._active_key(binding)
        proposal_id = clean_text(proposal_id, 32).upper()
        summary = clean_text(summary, 1800)
        if not proposal_id or not summary:
            raise GameCollaborationError("Use `/game propose <proposal-id> <summary>`")

        def mutate(record: dict[str, Any]) -> dict[str, Any]:
            if any(str(item.get("id")) == proposal_id for item in record["proposals"] if isinstance(item, dict)):
                raise GameCollaborationError(f"Proposal already exists: {proposal_id}")
            record["proposals"].append({"id": proposal_id, "summary": summary, "status": "proposed"})
            append_history(record, "proposal_added", proposal_id)
            return record

        return self.store.update(key, mutate, expected_revision=self._revision(binding))

    def approve(self, binding: dict[str, Any], proposal_id: str) -> dict[str, Any]:
        return self._set_proposal(binding, proposal_id, "approved")

    def reject(self, binding: dict[str, Any], proposal_id: str) -> dict[str, Any]:
        return self._set_proposal(binding, proposal_id, "rejected")

    def _set_proposal(self, binding: dict[str, Any], proposal_id: str, status: str) -> dict[str, Any]:
        key = self._active_key(binding)
        proposal_id = clean_text(proposal_id, 32).upper()

        def mutate(record: dict[str, Any]) -> dict[str, Any]:
            proposal = next((item for item in record["proposals"] if str(item.get("id")) == proposal_id), None)
            if proposal is None:
                raise GameCollaborationError(f"Proposal not found: {proposal_id}")
            if proposal.get("status") not in {"proposed", "approved", "rejected"}:
                raise GameCollaborationError(f"Proposal cannot be changed: {proposal_id}")
            proposal["status"] = status
            if status == "approved":
                approval_id = next_item_id(record, "approvals", "A")
                record["approvals"].append({
                    "id": approval_id,
                    "proposal_id": proposal_id,
                    "scope": clean_text(proposal.get("summary"), 1800),
                    "status": "approved",
                    "record_revision": int(record.get("record_revision", 0) or 0) + 1,
                })
            append_history(record, f"proposal_{status}", proposal_id)
            return record

        return self.store.update(key, mutate, expected_revision=self._revision(binding))

    def _active_key(self, binding: dict[str, Any]) -> str:
        if str(binding.get("mode") or "") != "active":
            raise GameCollaborationError("Game Collaboration is not active; use `/game resume`")
        key = str(binding.get("project_key") or "")
        if not key:
            raise GameCollaborationError("No active Game Collaboration project")
        return key

    @staticmethod
    def _revision(binding: dict[str, Any]) -> int | None:
        revision = int(binding.get("record_revision", 0) or 0)
        return revision or None

    def _set_status(
        self,
        record: dict[str, Any],
        status: str,
        event: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        key = str(record.get("project_key") or "")

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            current["project_status"] = status
            current["last_terminal_binding"] = {
                "mode": "active" if status == "active" else status,
                "surface": "terminal",
                "record_revision": int(current.get("record_revision", 0) or 0) + 1,
            }
            append_history(current, event, f"project status: {status}")
            return current

        return self.store.update(key, mutate, expected_revision=expected_revision)
