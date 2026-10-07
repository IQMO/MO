"""Thin provider-tool adapter for native media creation; no UI or provider logic."""
from __future__ import annotations

import json
from pathlib import Path


def execute_media(arguments: dict) -> str:
    from core.media import jobs, kie
    from core.media.catalog import OPERATIONS, PRIVACY_NOTICE, apply_selection, settings
    from core.media.references import helper_path
    from core.state.secrets import secret_status
    from core.tooling.sandbox import path_allowed, secret_read_path_kind

    config = arguments.get("_mo_config") or {}
    session_id = str(arguments.get("_mo_session_id") or "")
    action = str(arguments.get("action") or "catalog")
    try:
        if action == "catalog":
            import sys
            cfg = settings(config)
            value = {"provider": "Kie", "enabled": cfg.get("enabled") is True,
                     "reference_sharing": cfg.get("reference_sharing") is True,
                     "reference_helper_ready": bool(helper_path(config)),
                     "reference_platform_ready": sys.platform == "win32",
                     "credential_present": secret_status(str(cfg.get("api_key_env") or "KIE_API_KEY"),
                                                          config=config, service="providers").present,
                     "operations": {key: {"label": row[0], "models": row[1]} for key, row in OPERATIONS.items()},
                     "privacy": PRIVACY_NOTICE, "voice_enrollment": "later phase; not a local speech clone"}
        elif action == "credits":
            value = kie.credits(config)
        else:
            if not session_id:
                raise ValueError("Media jobs require a live conversation.")
            if action == "create":
                arguments = apply_selection(arguments, arguments.get("_mo_media_selection"))
                references = arguments.get("references") or []
                if not isinstance(references, list) or any(not isinstance(r, dict) for r in references):
                    raise ValueError("References must be a list of selected local files.")
                for ref in references:
                    path = Path(str(ref.get("path") or "")).expanduser().resolve(strict=True)
                    if secret_read_path_kind(path) or not path_allowed(str(path), arguments.get("_mo_allowed_roots")):
                        raise ValueError("A reference is outside the authorized media paths or is a protected credential file.")
                value = jobs.create(config, session_id, str(arguments.get("_mo_turn_id") or ""),
                                    operation=str(arguments.get("operation") or ""),
                                    model=str(arguments.get("model") or ""), options=arguments.get("options"),
                                    references=references, parent_id=str(arguments.get("parent_id") or ""),
                                    output_index=arguments.get("output_index", 0),
                                    on_activity=arguments.get("_on_activity"), cancel=arguments.get("_cancel_event"))
            elif action == "wait":
                value = jobs.wait(config, str(arguments.get("job_id") or ""), session_id,
                                  seconds=arguments.get("seconds", 60), on_activity=arguments.get("_on_activity"),
                                  cancel=arguments.get("_cancel_event"))
            elif action == "status":
                value = jobs.status(config, str(arguments.get("job_id") or ""), session_id)
            elif action == "list":
                value = {"jobs": jobs.recent(config, session_id)}
            elif action == "cleanup_review":
                value = jobs.review_cleanup(config, str(arguments.get("job_id") or ""), session_id)
            else:
                raise ValueError("Unknown media action.")
        callback = arguments.get("_on_operator_media")
        if callable(callback) and action in {"create", "wait", "cleanup_review"}:
            try:
                callback({**value, "session_id": session_id})
            except Exception:
                value = {**value, "presentation_pending": True}  # Delivery truth is independent of UI availability.
        return json.dumps(value, ensure_ascii=False)
    except (ValueError, OSError, kie.ProviderError) as exc:
        # Only the domain owner's sanitized job errors may include explanation;
        # arbitrary filesystem/network errors must not expose source data.
        message = str(exc) if isinstance(exc, (ValueError, kie.ProviderError)) else "Local media files could not be accessed."
        return "Error: " + message
