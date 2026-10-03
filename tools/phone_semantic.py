"""Semantic Android accessibility tools for the authenticated origin phone."""
from __future__ import annotations

import json
import hashlib
from typing import Any

from core.desktop.runtime import (
    attach_latest_observation_facts,
    bind_target,
    record_action,
    record_observation,
    validate_action,
)
from mo_everywhere.phone_actuation import (
    PhoneActuationError,
    current_phone_principal,
    request_current_phone,
)


MAX_PHONE_NODES = 40
MAX_PHONE_TEXT_CHARS = 240
MAX_PHONE_FILE_ENTRIES = 12
MAX_PHONE_FILE_READ_BYTES = 6 * 1024
MIN_PHONE_CACHE_TRIM_BYTES = 16 * 1024 * 1024
MAX_PHONE_CACHE_TRIM_BYTES = 10 * 1024 * 1024 * 1024
MAX_PHONE_CACHE_ENTRIES = 20
MAX_PHONE_PACKAGE_ENTRIES = 200
MAX_PHONE_ANALYSIS_DOCUMENTS = 400
MAX_PHONE_ANALYSIS_DEPTH = 12
_NODE_ACTIONS = frozenset({"click", "set_text", "scroll_forward", "scroll_backward"})
_PACKAGE_ACTIONS = frozenset({
    "force_stop", "enable", "disable", "clear_data", "uninstall",
    "grant_permission", "revoke_permission",
})


def execute_phone_context(arguments: dict[str, Any]) -> str:
    args = dict(arguments or {})
    query = _bounded_text(args.get("query"), 160)
    max_nodes = _bounded_int(args.get("max_nodes"), default=40, minimum=1, maximum=MAX_PHONE_NODES)
    try:
        raw = request_current_phone("observe", {"query": query, "max_nodes": max_nodes})
        snapshot = _normalize_snapshot(raw, max_nodes=max_nodes)
        return _record_snapshot("phone_context", snapshot)
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_context failed: {exc}"


def execute_phone_click(arguments: dict[str, Any]) -> str:
    try:
        target_ref = _required_ref((arguments or {}).get("target"))
    except ValueError as exc:
        return f"Error: phone_click failed: {exc}"
    return _execute_action("phone_click", "click", {"target": target_ref})


def execute_phone_set_text(arguments: dict[str, Any]) -> str:
    args = dict(arguments or {})
    try:
        target_ref = _required_ref(args.get("target"))
    except ValueError as exc:
        return f"Error: phone_set_text failed: {exc}"
    value = str(args.get("text") or "")
    if not 1 <= len(value) <= 2_000 or any(ord(char) < 0x20 and char not in "\t\r\n" for char in value):
        return "Error: phone_set_text text must contain 1-2000 safe characters."
    return _execute_action("phone_set_text", "set_text", {"target": target_ref, "text": value})


def execute_phone_scroll(arguments: dict[str, Any]) -> str:
    args = dict(arguments or {})
    direction = str(args.get("direction") or "down").strip().lower()
    if direction not in {"up", "down", "left", "right"}:
        return "Error: phone_scroll direction must be up, down, left, or right."
    try:
        target_ref = _optional_ref(args.get("target"))
    except ValueError as exc:
        return f"Error: phone_scroll failed: {exc}"
    payload: dict[str, Any] = {"direction": direction}
    if target_ref:
        payload["target"] = target_ref
    return _execute_action("phone_scroll", "scroll", payload)


def execute_phone_key(arguments: dict[str, Any]) -> str:
    action = str((arguments or {}).get("action") or "").strip().lower()
    if action not in {"back", "home"}:
        return "Error: phone_key action must be back or home."
    return _execute_global_key(action)


def execute_phone_files(arguments: dict[str, Any]) -> str:
    args = dict(arguments or {})
    try:
        path = _phone_file_path(args.get("path"), allow_root=True)
        max_entries = _bounded_int(
            args.get("max_entries"),
            default=MAX_PHONE_FILE_ENTRIES,
            minimum=1,
            maximum=MAX_PHONE_FILE_ENTRIES,
        )
        raw = request_current_phone("files_list", {"path": path, "max_entries": max_entries})
        return _record_file_listing(_normalize_file_listing(raw))
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_files failed: {exc}"


def execute_phone_storage_report(arguments: dict[str, Any]) -> str:
    args = dict(arguments or {})
    try:
        path = _phone_file_path(args.get("path"), allow_root=True)
        max_documents = _strict_bounded_int(
            args.get("max_documents"),
            default=200,
            minimum=1,
            maximum=MAX_PHONE_ANALYSIS_DOCUMENTS,
        )
        max_depth = _strict_bounded_int(
            args.get("max_depth"),
            default=8,
            minimum=1,
            maximum=MAX_PHONE_ANALYSIS_DEPTH,
        )
        request = {
            "path": path,
            "max_documents": max_documents,
            "max_depth": max_depth,
        }
        value = _normalize_storage_report(
            request_current_phone("files_analyze", request, timeout_seconds=20),
            expected_path=path,
            max_documents=max_documents,
        )
        return _record_system_observation(
            "phone_storage_report",
            value,
            scope="origin_phone_storage",
            metadata=request,
            facts={"largest_file_paths": [item["path"] for item in value["largest_files"]]},
        )
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_storage_report failed: {exc}"


def execute_phone_file_read(arguments: dict[str, Any]) -> str:
    args = dict(arguments or {})
    try:
        path = _phone_file_path(args.get("path"), allow_root=False)
        max_bytes = _bounded_int(
            args.get("max_bytes"),
            default=MAX_PHONE_FILE_READ_BYTES,
            minimum=1,
            maximum=MAX_PHONE_FILE_READ_BYTES,
        )
        raw = request_current_phone("files_read", {"path": path, "max_bytes": max_bytes})
        if not isinstance(raw, dict) or str(raw.get("path") or "") != path:
            raise ValueError("phone file response is invalid")
        text = raw.get("text")
        size = raw.get("bytes")
        mime = _bounded_text(raw.get("mime_type"), 120)
        if (
            not isinstance(text, str)
            or len(text.encode("utf-8")) > max_bytes
            or any(ord(char) < 0x20 and char not in "\t\r\n" for char in text)
        ):
            raise ValueError("phone file text is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= max_bytes:
            raise ValueError("phone file size is invalid")
        return json.dumps(
            {"path": path, "mime_type": mime, "bytes": size, "text": text},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_file_read failed: {exc}"


def _phone_file_delete_preconditions(
    arguments: dict[str, Any],
) -> tuple[str, Any, Any, str | None]:
    try:
        path = _phone_file_path((arguments or {}).get("path"), allow_root=False)
    except ValueError as exc:
        return "", None, None, f"Error: phone_file_delete failed: {exc}"
    target, observation, error = validate_action("phone")
    if error:
        return path, target, observation, error
    assert target is not None and observation is not None
    parent = path.rpartition("/")[0]
    if str(target.metadata.get("tree_path") or "") != parent:
        return (
            path,
            target,
            observation,
            "Error: list the file's parent folder with phone_files immediately before deleting it.",
        )
    listed = observation.facts.get("file_paths", [])
    if path not in listed:
        return (
            path,
            target,
            observation,
            "Error: the requested phone file is not present in the latest folder observation.",
        )
    return path, target, observation, None


def execute_phone_file_delete(arguments: dict[str, Any]) -> str:
    path, target, observation, error = _phone_file_delete_preconditions(arguments)
    if error:
        return error
    assert target is not None and observation is not None
    parent = path.rpartition("/")[0]
    try:
        raw = request_current_phone("files_delete", {"path": path})
        if (
            not isinstance(raw, dict)
            or raw.get("deleted") is not True
            or str(raw.get("path") or "") != path
        ):
            return "Error: the phone did not confirm file deletion."
        record_action(
            "phone_file_delete",
            target=target,
            observation=observation,
            status="executed",
            state_changed=True,
        )
        fresh = request_current_phone(
            "files_list",
            {"path": parent, "max_entries": MAX_PHONE_FILE_ENTRIES},
        )
        return "Phone file deleted after confirmation.\n" + _record_file_listing(_normalize_file_listing(fresh))
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_file_delete failed: {exc}"


def execute_phone_capabilities(arguments: dict[str, Any]) -> str:
    if arguments:
        return "Error: phone_capabilities does not accept arguments."
    try:
        value = _normalize_capabilities(request_current_phone("capabilities", {}))
        return _record_system_observation(
            "phone_capabilities",
            value,
            scope="origin_phone_capabilities",
            facts={
                "privileged_ready": value["privileged"]["ready"],
                "all_files_ready": value["all_files"]["ready"],
                "arbitrary_shell": value["arbitrary_shell"],
                "background_updates": value["background_updates"],
                "root_backend": value["root_backend"],
                "device_owner": value["device_owner"],
            },
        )
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_capabilities failed: {exc}"


def execute_phone_system_status(arguments: dict[str, Any]) -> str:
    if arguments:
        return "Error: phone_system_status does not accept arguments."
    try:
        value = _normalize_system_status(request_current_phone("system_status", {}))
        return _record_system_observation(
            "phone_system_status",
            value,
            scope="origin_phone_system",
        )
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_system_status failed: {exc}"


def execute_phone_cache_report(arguments: dict[str, Any]) -> str:
    args = dict(arguments or {})
    include_system = args.get("include_system", False)
    if type(include_system) is not bool:
        return "Error: phone_cache_report include_system must be true or false."
    try:
        max_entries = _strict_bounded_int(
            args.get("max_entries"),
            default=MAX_PHONE_CACHE_ENTRIES,
            minimum=1,
            maximum=MAX_PHONE_CACHE_ENTRIES,
        )
        request = {"include_system": include_system, "max_entries": max_entries}
        value = _normalize_cache_report(request_current_phone("cache_report", request))
        return _record_system_observation(
            "phone_cache_report",
            value,
            scope="origin_phone_cache",
            metadata=request,
            facts={
                "cache_packages": [item["package"] for item in value["candidates"]],
                "cache_total_bytes": value["cache_total_bytes"],
            },
        )
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_cache_report failed: {exc}"


def _phone_cache_trim_preconditions(
    arguments: dict[str, Any],
) -> tuple[int, Any, Any, str | None]:
    try:
        requested = _exact_int(
            (arguments or {}).get("bytes_to_free"),
            minimum=MIN_PHONE_CACHE_TRIM_BYTES,
            maximum=MAX_PHONE_CACHE_TRIM_BYTES,
        )
    except ValueError:
        return 0, None, None, (
            "Error: phone_cache_trim bytes_to_free must be an exact integer from "
            f"{MIN_PHONE_CACHE_TRIM_BYTES} to {MAX_PHONE_CACHE_TRIM_BYTES}."
        )
    target, observation, error = validate_action("phone")
    if error:
        return requested, target, observation, error
    assert target is not None and observation is not None
    if str(target.metadata.get("scope") or "") != "origin_phone_cache":
        return (
            requested,
            target,
            observation,
            "Error: run phone_cache_report immediately before trimming Android caches.",
        )
    return requested, target, observation, None


def execute_phone_cache_trim(arguments: dict[str, Any]) -> str:
    requested, target, observation, error = _phone_cache_trim_preconditions(arguments)
    if error:
        return error
    assert target is not None and observation is not None
    try:
        value = _normalize_cache_trim(
            request_current_phone("cache_trim", {"bytes_to_free": requested}),
            requested=requested,
        )
        record_action(
            "phone_cache_trim",
            target=target,
            observation=observation,
            status="executed",
            state_changed=value["cache_freed_bytes"] > 0,
        )
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_cache_trim failed: {exc}"
    include_system = bool(target.metadata.get("include_system", False))
    max_entries = int(target.metadata.get("max_entries", MAX_PHONE_CACHE_ENTRIES))
    request = {"include_system": include_system, "max_entries": max_entries}
    try:
        fresh = _normalize_cache_report(request_current_phone("cache_report", request))
        rendered = _record_system_observation(
            "phone_cache_trim",
            fresh,
            scope="origin_phone_cache",
            metadata=request,
            facts={
                "cache_packages": [item["package"] for item in fresh["candidates"]],
                "cache_total_bytes": fresh["cache_total_bytes"],
            },
        )
        return (
            "Android cache trim executed after confirmation. "
            f"Reported cache freed: {value['cache_freed_bytes']} bytes.\n{rendered}"
        )
    except (PhoneActuationError, ValueError) as exc:
        return (
            "Android cache trim executed after confirmation, but the fresh post-action "
            f"cache report was unavailable: {exc}"
        )


def execute_phone_packages(arguments: dict[str, Any]) -> str:
    args = dict(arguments or {})
    scope = str(args.get("scope", "user") or "").strip().lower()
    if scope not in {"user", "all"}:
        return "Error: phone_packages scope must be user or all."
    try:
        max_entries = _strict_bounded_int(
            args.get("max_entries"),
            default=MAX_PHONE_PACKAGE_ENTRIES,
            minimum=1,
            maximum=MAX_PHONE_PACKAGE_ENTRIES,
        )
        request = {"scope": scope, "max_entries": max_entries}
        value = _normalize_packages(request_current_phone("packages_list", request))
        return _record_system_observation(
            "phone_packages",
            value,
            scope="origin_phone_packages",
            metadata=request,
            facts={"package_names": value["packages"]},
        )
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_packages failed: {exc}"


def _phone_package_action_preconditions(
    arguments: dict[str, Any],
) -> tuple[str, str, str, Any, Any, str | None]:
    args = dict(arguments or {})
    action = str(args.get("action") or "").strip().lower()
    if action not in _PACKAGE_ACTIONS:
        return action, "", "", None, None, "Error: phone_package_action action is invalid."
    try:
        package = _phone_package_name(args.get("package"), "package")
        permission = ""
        if action in {"grant_permission", "revoke_permission"}:
            permission = _phone_package_name(args.get("permission"), "permission")
        elif "permission" in args:
            raise ValueError("permission is only valid for grant_permission or revoke_permission")
    except ValueError as exc:
        return action, "", "", None, None, f"Error: phone_package_action failed: {exc}"
    target, observation, error = validate_action("phone")
    if error:
        return action, package, permission, target, observation, error
    assert target is not None and observation is not None
    if str(target.metadata.get("scope") or "") != "origin_phone_packages":
        return (
            action,
            package,
            permission,
            target,
            observation,
            "Error: run phone_packages immediately before a package action.",
        )
    if package not in observation.facts.get("package_names", []):
        return (
            action,
            package,
            permission,
            target,
            observation,
            "Error: the exact package is absent from the latest phone_packages observation.",
        )
    return action, package, permission, target, observation, None


def execute_phone_package_action(arguments: dict[str, Any]) -> str:
    action, package, permission, target, observation, error = (
        _phone_package_action_preconditions(arguments)
    )
    if error:
        return error
    assert target is not None and observation is not None
    request: dict[str, Any] = {"action": action, "package": package}
    if permission:
        request["permission"] = permission
    try:
        value = _normalize_package_action(
            request_current_phone("package_action", request),
            action=action,
            package=package,
            permission=permission,
        )
        record_action(
            "phone_package_action",
            target=target,
            observation=observation,
            status="executed",
            state_changed=True,
        )
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_package_action failed: {exc}"
    scope = str(target.metadata.get("scope_value") or target.metadata.get("scope") or "user")
    if scope not in {"user", "all"}:
        scope = "user"
    max_entries = int(target.metadata.get("max_entries", MAX_PHONE_PACKAGE_ENTRIES))
    listing_request = {"scope": scope, "max_entries": max_entries}
    try:
        fresh = _normalize_packages(request_current_phone("packages_list", listing_request))
        rendered = _record_system_observation(
            "phone_package_action",
            fresh,
            scope="origin_phone_packages",
            metadata=listing_request,
            facts={"package_names": fresh["packages"]},
        )
        return (
            f"Android package action executed after confirmation: {value['action']} "
            f"on {value['package']}.\n{rendered}"
        )
    except (PhoneActuationError, ValueError) as exc:
        return (
            f"Android package action executed after confirmation: {value['action']} "
            f"on {value['package']}, but the fresh post-action package listing was "
            f"unavailable: {exc}"
        )


def phone_action_precondition_block_reason(
    tool: str,
    arguments: dict[str, Any],
) -> str | None:
    """Reject non-executable high-impact phone actions before asking approval."""
    name = str(tool or "")
    if name == "phone_file_delete":
        _path, _target, _observation, error = _phone_file_delete_preconditions(arguments)
        return error
    if name == "phone_cache_trim":
        _requested, _target, _observation, error = _phone_cache_trim_preconditions(arguments)
        return error
    if name == "phone_package_action":
        _action, _package, _permission, _target, _observation, error = (
            _phone_package_action_preconditions(arguments)
        )
        return error
    if name == "phone_shell":
        _command, _target, _observation, error = _phone_shell_preconditions(arguments)
        return error
    return None


def _phone_shell_preconditions(
    arguments: dict[str, Any],
) -> tuple[str, Any, Any, str | None]:
    command = str((arguments or {}).get("command") or "")
    if (
        not 1 <= len(command) <= 4 * 1024
        or any(ord(char) == 0 or (ord(char) < 0x20 and char not in "\t\r\n") for char in command)
    ):
        return (
            command,
            None,
            None,
            "Error: phone_shell command must contain 1-4096 safe characters.",
        )
    target, observation, error = validate_action("phone")
    if error:
        return command, target, observation, error
    assert target is not None and observation is not None
    if (
        str(target.metadata.get("scope") or "") != "origin_phone_capabilities"
        or observation.facts.get("arbitrary_shell") is not True
    ):
        return (
            command,
            target,
            observation,
            "Error: run phone_capabilities immediately before requesting an enabled "
            "arbitrary shell command.",
        )
    return command, target, observation, None


def execute_phone_shell(arguments: dict[str, Any]) -> str:
    command, target, observation, error = _phone_shell_preconditions(arguments)
    if error:
        return error
    assert target is not None and observation is not None
    try:
        raw = request_current_phone("shell_execute", {"command": command}, timeout_seconds=20)
        if not isinstance(raw, dict) or raw.get("executed") is not True:
            raise ValueError("phone shell response is invalid")
        exit_code = _exact_int(raw.get("exit_code"), minimum=0, maximum=255)
        output = raw.get("output")
        truncated = raw.get("truncated")
        backend = _bounded_text(raw.get("backend"), 40)
        if (
            not isinstance(output, str)
            or len(output) > 8 * 1024
            or any(ord(char) < 0x20 and char not in "\t\r\n" for char in output)
            or type(truncated) is not bool
            or backend not in {"shizuku_shell", "shizuku_root"}
        ):
            raise ValueError("phone shell response is invalid")
        record_action(
            "phone_shell",
            target=target,
            observation=observation,
            status="executed",
            state_changed=True,
        )
        return json.dumps(
            {
                "executed": True,
                "exit_code": exit_code,
                "output": output,
                "truncated": truncated,
                "backend": backend,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_shell failed: {exc}"


def _execute_global_key(operation: str) -> str:
    """Run explicit Android navigation even when MO's own UI is foreground."""
    principal = current_phone_principal()
    if principal is None:
        return "Error: phone_key failed: authenticated origin phone context is unavailable"
    target, observation, _error = validate_action("phone", require_observation=False)
    if target is None:
        target = bind_target(
            kind="phone",
            identity=principal.device_id,
            label="Authenticated origin phone",
            metadata={"scope": "origin_phone"},
        )
    try:
        raw = request_current_phone(operation, {})
        if raw.get("executed") is not True:
            return "Error: the phone did not confirm Android navigation."
        record_action(
            "phone_key",
            target=target,
            observation=observation,
            status="executed",
            state_changed=bool(raw.get("state_changed", True)),
        )
        fresh_raw = raw.get("observation")
        if isinstance(fresh_raw, dict):
            fresh = _normalize_snapshot(fresh_raw, max_nodes=MAX_PHONE_NODES)
            rendered = _record_snapshot("phone_key", fresh)
            return (
                f"Phone navigation executed: {operation}. "
                f"Fresh post-action state observed.\n{rendered}"
            )
        return f"Phone navigation executed: {operation}. Run phone_context to verify the UI state."
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: phone_key failed: {exc}"


def _execute_action(tool: str, operation: str, payload: dict[str, Any]) -> str:
    target, observation, error = validate_action("phone")
    if error:
        return error
    assert target is not None and observation is not None
    snapshot_id = str(observation.signature or "")
    if not snapshot_id:
        return "Error: the phone observation has no snapshot identity; run phone_context again."
    request = dict(payload)
    request["snapshot_id"] = snapshot_id
    try:
        raw = request_current_phone(operation, request)
        if raw.get("executed") is not True:
            return "Error: the phone did not confirm semantic action execution."
        record_action(
            tool,
            target=target,
            observation=observation,
            status="executed",
            state_changed=bool(raw.get("state_changed", True)),
        )
        fresh_raw = raw.get("observation")
        if isinstance(fresh_raw, dict):
            fresh = _normalize_snapshot(fresh_raw, max_nodes=MAX_PHONE_NODES)
            rendered = _record_snapshot(tool, fresh)
            return (
                f"Phone semantic action executed: {operation}. "
                f"Fresh post-action state observed.\n{rendered}"
            )
        return (
            f"Phone semantic action executed: {operation}. "
            "Run phone_context to verify the resulting UI state."
        )
    except (PhoneActuationError, ValueError) as exc:
        return f"Error: {tool} failed: {exc}"


def _record_snapshot(tool: str, snapshot: dict[str, Any]) -> str:
    principal = current_phone_principal()
    if principal is None:
        raise ValueError("authenticated origin phone context is unavailable")
    package_name = str(snapshot["package_name"])
    title = str(snapshot.get("title") or package_name or "Android screen")
    target = bind_target(
        kind="phone",
        identity=principal.device_id,
        label=title,
        metadata={
            "package_name": package_name,
            "window_id": snapshot["window_id"],
            "host_revision": snapshot["revision"],
            "snapshot_id": snapshot["snapshot_id"],
        },
    )
    observation = record_observation(
        tool,
        target=target,
        origin="android_accessibility",
        trust="external_untrusted",
        foreground_identity=package_name,
        signature=str(snapshot["snapshot_id"]),
    )
    attach_latest_observation_facts("phone", {
        "elements_by_ref": {
            str(node["ref"]): {
                "role": str(node.get("role") or ""),
                "label": " ".join(
                    item for item in (
                        str(node.get("text") or ""),
                        str(node.get("description") or ""),
                    ) if item
                )[:200],
                "actions": list(node.get("actions") or []),
            }
            for node in snapshot["nodes"]
        }
    })
    public = {
        "snapshot_id": snapshot["snapshot_id"],
        "screen": {
            "package": package_name,
            "title": title,
            "window_id": snapshot["window_id"],
            "revision": snapshot["revision"],
            "target_id": target.target_id,
            "target_revision": observation.target_revision,
        },
        "nodes": snapshot["nodes"],
        "sensitive_omitted": snapshot["sensitive_omitted"],
    }
    return json.dumps(public, ensure_ascii=False, separators=(",", ":"))


def _record_file_listing(listing: dict[str, Any]) -> str:
    principal = current_phone_principal()
    if principal is None:
        raise ValueError("authenticated origin phone context is unavailable")
    path = str(listing["path"])
    target = bind_target(
        kind="phone",
        identity=principal.device_id,
        label=f"Phone files · {path or listing['label'] or 'selected folder'}",
        metadata={"scope": "origin_phone_files", "tree_path": path},
    )
    signature = hashlib.sha256(
        json.dumps(listing, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    observation = record_observation(
        "phone_files",
        target=target,
        origin="android_storage_access_framework",
        trust="external_untrusted",
        foreground_identity="origin_phone_files",
        signature=signature,
    )
    attach_latest_observation_facts(
        "phone",
        {"file_paths": [entry["path"] for entry in listing["entries"] if not entry["directory"]]},
    )
    public = dict(listing)
    public["target_id"] = target.target_id
    public["target_revision"] = observation.target_revision
    public["observation_id"] = observation.observation_id
    return json.dumps(public, ensure_ascii=False, separators=(",", ":"))


def _record_system_observation(
    tool: str,
    value: dict[str, Any],
    *,
    scope: str,
    metadata: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
) -> str:
    principal = current_phone_principal()
    if principal is None:
        raise ValueError("authenticated origin phone context is unavailable")
    target_metadata: dict[str, Any] = {"scope": scope}
    for key, item in (metadata or {}).items():
        target_metadata["scope_value" if key == "scope" else key] = item
    target = bind_target(
        kind="phone",
        identity=principal.device_id,
        label=f"Origin phone · {scope.removeprefix('origin_phone_').replace('_', ' ')}",
        metadata=target_metadata,
    )
    signature = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    observation = record_observation(
        tool,
        target=target,
        origin="android_privileged_tools",
        trust="external_untrusted",
        foreground_identity=scope,
        signature=signature,
    )
    attach_latest_observation_facts("phone", dict(facts or {}))
    public = dict(value)
    public["target_id"] = target.target_id
    public["target_revision"] = observation.target_revision
    public["observation_id"] = observation.observation_id
    return json.dumps(public, ensure_ascii=False, separators=(",", ":"))


def _normalize_capabilities(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("api_version") != 1:
        raise ValueError("phone capability response is invalid")
    selected = value.get("selected_folder")
    all_files = value.get("all_files")
    privileged = value.get("privileged")
    if not isinstance(selected, dict) or not isinstance(all_files, dict) or not isinstance(privileged, dict):
        raise ValueError("phone capability response is invalid")
    selected_granted = selected.get("granted")
    consent = privileged.get("consent")
    ready = privileged.get("ready")
    if any(type(item) is not bool for item in (selected_granted, consent, ready)):
        raise ValueError("phone capability state is invalid")
    state = _bounded_text(privileged.get("state"), 40)
    if state not in {"stopped", "permission_required", "denied", "ready", "unsupported"}:
        raise ValueError("phone privileged state is invalid")
    backend = _bounded_text(privileged.get("backend"), 40)
    privilege_uid = _exact_int(privileged.get("privilege_uid"), minimum=-1, maximum=2**31 - 1)
    operations = privileged.get("operations")
    allowed_operations = {
        "system_status", "cache_report", "cache_trim", "packages_list", "package_action",
        "shell_execute",
    }
    if (
        not isinstance(operations, list)
        or not 3 <= len(operations) <= len(allowed_operations)
        or any(not isinstance(item, str) or item not in allowed_operations for item in operations)
        or len(set(operations)) != len(operations)
        or not {"system_status", "cache_report", "packages_list"}.issubset(operations)
    ):
        raise ValueError("phone privileged operations are invalid")
    if value.get("semantic_ui") is not True:
        raise ValueError("phone semantic capability is invalid")
    arbitrary_shell = value.get("arbitrary_shell")
    background_updates = value.get("background_updates")
    root_backend = value.get("root_backend")
    device_owner = value.get("device_owner")
    if (
        type(arbitrary_shell) is not bool
        or type(background_updates) is not bool
        or type(root_backend) is not bool
        or type(device_owner) is not bool
        or value.get("root_bypass") is not False
        or value.get("lockscreen_bypass") is not False
    ):
        raise ValueError("phone safety capability response is invalid")
    expected_file_lane = "phone_files_v1" if selected_granted else ""
    file_lane = _bounded_text(selected.get("lane"), 40)
    if file_lane != expected_file_lane:
        raise ValueError("phone file capability state is inconsistent")
    all_files_consent = all_files.get("consent")
    all_files_granted = all_files.get("granted")
    all_files_ready = all_files.get("ready")
    if any(type(item) is not bool for item in (all_files_consent, all_files_granted, all_files_ready)):
        raise ValueError("phone all-files capability is invalid")
    if all_files_ready != (all_files_consent and all_files_granted):
        raise ValueError("phone all-files capability is inconsistent")
    all_files_lane = _bounded_text(all_files.get("lane"), 40)
    if all_files_lane != ("phone_files_v1" if all_files_ready else ""):
        raise ValueError("phone all-files lane is inconsistent")
    expected_ready = consent and state == "ready"
    system_lane = _bounded_text(privileged.get("lane"), 40)
    if ready != expected_ready or system_lane != ("phone_system_v1" if ready else ""):
        raise ValueError("phone privileged capability state is inconsistent")
    if state == "ready":
        expected_backend = "shizuku_root" if privilege_uid == 0 else "shizuku_shell"
        if privilege_uid < 0 or backend != expected_backend:
            raise ValueError("phone privileged backend is inconsistent")
    elif backend or privilege_uid != -1:
        raise ValueError("phone unavailable privileged backend is inconsistent")
    if arbitrary_shell != (ready and "shell_execute" in operations):
        raise ValueError("phone arbitrary-shell capability is inconsistent")
    raw_capabilities = value.get("capabilities")
    if not isinstance(raw_capabilities, list) or not 1 <= len(raw_capabilities) <= 16:
        raise ValueError("phone capability ledger is invalid")
    allowed_tools = {
        "phone_context", "phone_click", "phone_set_text", "phone_scroll", "phone_key",
        "phone_files", "phone_storage_report", "phone_file_read", "phone_file_delete",
        "phone_system_status", "phone_cache_report", "phone_cache_trim",
        "phone_packages", "phone_package_action", "phone_shell",
    }
    clean_capabilities: list[dict[str, Any]] = []
    for item in raw_capabilities:
        if not isinstance(item, dict):
            raise ValueError("phone capability ledger row is invalid")
        expected_keys = {
            "id", "title", "authority_tier", "backend", "supported", "available",
            "enabled", "authorized", "risk_class", "prerequisite", "lock_behavior",
            "expires_at", "scope", "self_test", "self_test_at", "lane",
            "tool_families",
        }
        if set(item) != expected_keys:
            raise ValueError("phone capability ledger row is invalid")
        supported = item.get("supported")
        available = item.get("available")
        enabled = item.get("enabled")
        authorized = item.get("authorized")
        if any(type(flag) is not bool for flag in (supported, available, enabled, authorized)):
            raise ValueError("phone capability ledger state is invalid")
        if authorized and (not supported or not available or not enabled):
            raise ValueError("phone capability ledger authorization is inconsistent")
        risk = _bounded_text(item.get("risk_class"), 16)
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("phone capability ledger risk is invalid")
        lane = _bounded_text(item.get("lane"), 40)
        if lane not in {"", "phone_semantic_v1", "phone_files_v1", "phone_system_v1"}:
            raise ValueError("phone capability ledger lane is invalid")
        self_test = _bounded_text(item.get("self_test"), 24)
        if self_test not in {"", "pass", "off", "unavailable", "unsupported"}:
            raise ValueError("phone capability self-test is invalid")
        tools = item.get("tool_families")
        if (
            not isinstance(tools, list)
            or len(tools) > 12
            or any(not isinstance(tool, str) or tool not in allowed_tools for tool in tools)
            or len(set(tools)) != len(tools)
        ):
            raise ValueError("phone capability tool families are invalid")
        clean_capabilities.append({
            "id": _ascii_id(item.get("id"), "capability"),
            "title": _bounded_text(item.get("title"), 80),
            "authority_tier": _bounded_text(item.get("authority_tier"), 40),
            "backend": _bounded_text(item.get("backend"), 40),
            "supported": supported,
            "available": available,
            "enabled": enabled,
            "authorized": authorized,
            "risk_class": risk,
            "prerequisite": _bounded_text(item.get("prerequisite"), 200),
            "lock_behavior": _bounded_text(item.get("lock_behavior"), 80),
            "expires_at": _exact_int(item.get("expires_at"), minimum=0, maximum=2**63 - 1),
            "scope": _bounded_text(item.get("scope"), 240),
            "self_test": self_test,
            "self_test_at": _exact_int(item.get("self_test_at"), minimum=0, maximum=2**63 - 1),
            "lane": lane,
            "tool_families": list(tools),
        })
    if len({item["id"] for item in clean_capabilities}) != len(clean_capabilities):
        raise ValueError("phone capability ledger contains duplicate rows")
    shell_row = next((item for item in clean_capabilities if item["id"] == "arbitrary_shell"), None)
    update_row = next((item for item in clean_capabilities if item["id"] == "background_updates"), None)
    root_row = next((item for item in clean_capabilities if item["id"] == "root_backend"), None)
    owner_row = next((item for item in clean_capabilities if item["id"] == "device_owner"), None)
    if (
        shell_row is None or update_row is None or root_row is None or owner_row is None
        or arbitrary_shell != shell_row["authorized"]
        or background_updates != update_row["authorized"]
        or root_backend != root_row["authorized"]
        or device_owner != owner_row["authorized"]
    ):
        raise ValueError("phone safety capability ledger is inconsistent")
    return {
        "api_version": 1,
        "semantic_ui": True,
        "selected_folder": {
            "granted": selected_granted,
            "lane": file_lane,
        },
        "all_files": {
            "consent": all_files_consent,
            "granted": all_files_granted,
            "ready": all_files_ready,
            "lane": all_files_lane,
        },
        "privileged": {
            "consent": consent,
            "state": state,
            "ready": ready,
            "backend": backend,
            "privilege_uid": privilege_uid,
            "lane": system_lane,
            "operations": list(operations),
        },
        "capabilities": clean_capabilities,
        "arbitrary_shell": arbitrary_shell,
        "background_updates": background_updates,
        "root_backend": root_backend,
        "root_bypass": False,
        "lockscreen_bypass": False,
        "device_owner": device_owner,
    }


def _normalize_system_status(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("phone system status is invalid")
    battery = value.get("battery")
    storage = value.get("storage")
    if not isinstance(battery, dict) or not isinstance(storage, dict):
        raise ValueError("phone system status is invalid")
    return {
        "backend": _bounded_text(value.get("backend"), 40),
        "privilege_uid": _exact_int(value.get("privilege_uid"), minimum=0, maximum=2**31 - 1),
        "battery": {
            "level": _exact_int(battery.get("level"), minimum=0, maximum=100),
            "status": _exact_int(battery.get("status"), minimum=0, maximum=100),
            "health": _exact_int(battery.get("health"), minimum=0, maximum=100),
            "temperature_tenths_c": _exact_int(
                battery.get("temperature_tenths_c"), minimum=0, maximum=2_000,
            ),
            "usb_powered": _exact_bool(battery.get("usb_powered")),
            "ac_powered": _exact_bool(battery.get("ac_powered")),
        },
        "storage": {
            "data_free_bytes": _exact_int(
                storage.get("data_free_bytes"), minimum=0, maximum=2**63 - 1,
            ),
            "data_total_bytes": _exact_int(
                storage.get("data_total_bytes"), minimum=0, maximum=2**63 - 1,
            ),
            "app_cache_bytes": _exact_int(
                storage.get("app_cache_bytes"), minimum=0, maximum=2**63 - 1,
            ),
        },
    }


def _normalize_cache_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("phone cache report is invalid")
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > MAX_PHONE_CACHE_ENTRIES:
        raise ValueError("phone cache candidates are invalid")
    clean_candidates = []
    for item in candidates:
        if not isinstance(item, dict):
            raise ValueError("phone cache candidate is invalid")
        clean_candidates.append({
            "package": _phone_package_name(item.get("package"), "package"),
            "cache_bytes": _exact_int(item.get("cache_bytes"), minimum=0, maximum=2**63 - 1),
        })
    if type(value.get("include_system")) is not bool or type(value.get("truncated")) is not bool:
        raise ValueError("phone cache report flags are invalid")
    return {
        "backend": _bounded_text(value.get("backend"), 40),
        "include_system": value["include_system"],
        "cache_total_bytes": _exact_int(value.get("cache_total_bytes"), minimum=0, maximum=2**63 - 1),
        "data_free_bytes": _exact_int(value.get("data_free_bytes"), minimum=0, maximum=2**63 - 1),
        "data_total_bytes": _exact_int(value.get("data_total_bytes"), minimum=0, maximum=2**63 - 1),
        "candidates": clean_candidates,
        "truncated": value["truncated"],
        "source": _bounded_text(value.get("source"), 40),
    }


def _normalize_cache_trim(value: Any, *, requested: int) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("executed") is not True:
        raise ValueError("phone cache trim result is invalid")
    clean = {
        key: _exact_int(value.get(key), minimum=0, maximum=2**63 - 1)
        for key in (
            "requested_bytes", "cache_before_bytes", "cache_after_bytes",
            "cache_freed_bytes", "data_free_before_bytes", "data_free_after_bytes",
        )
    }
    if clean["requested_bytes"] != requested:
        raise ValueError("phone cache trim result does not match the confirmed request")
    clean["executed"] = True
    return clean


def _normalize_packages(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("phone package listing is invalid")
    scope = str(value.get("scope") or "")
    packages = value.get("packages")
    if scope not in {"user", "all"} or not isinstance(packages, list) or len(packages) > MAX_PHONE_PACKAGE_ENTRIES:
        raise ValueError("phone package listing is invalid")
    clean_packages = [_phone_package_name(item, "package") for item in packages]
    if clean_packages != sorted(set(clean_packages)) or type(value.get("truncated")) is not bool:
        raise ValueError("phone package listing is invalid")
    return {"scope": scope, "packages": clean_packages, "truncated": value["truncated"]}


def _normalize_package_action(
    value: Any,
    *,
    action: str,
    package: str,
    permission: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("executed") is not True:
        raise ValueError("phone package action result is invalid")
    if value.get("action") != action or value.get("package") != package:
        raise ValueError("phone package action result does not match the confirmed request")
    returned_permission = str(value.get("permission") or "")
    if returned_permission != permission:
        raise ValueError("phone package permission result does not match the confirmed request")
    return {
        "executed": True,
        "action": action,
        "package": package,
        "permission": permission,
        "result": _bounded_text(value.get("result"), 200),
    }


def _normalize_snapshot(value: Any, *, max_nodes: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("phone observation is invalid")
    snapshot_id = _ascii_id(value.get("snapshot_id"), "snapshot")
    package_name = _bounded_text(value.get("package_name"), 200)
    if not package_name:
        raise ValueError("phone observation package is missing")
    window_id = _exact_int(value.get("window_id"), minimum=-1, maximum=2**31 - 1)
    revision = _exact_int(value.get("revision"), minimum=1, maximum=2**63 - 1)
    title = _bounded_text(value.get("title"), MAX_PHONE_TEXT_CHARS)
    raw_nodes = value.get("nodes")
    if not isinstance(raw_nodes, list) or len(raw_nodes) > MAX_PHONE_NODES:
        raise ValueError("phone observation nodes are invalid")
    nodes = []
    omitted = _exact_int(value.get("sensitive_omitted", 0), minimum=0, maximum=100_000)
    for raw in raw_nodes[:max_nodes]:
        if not isinstance(raw, dict):
            raise ValueError("phone observation node is invalid")
        if raw.get("password") is True or raw.get("sensitive") is True:
            omitted += 1
            continue
        nodes.append(_normalize_node(raw))
    return {
        "snapshot_id": snapshot_id,
        "package_name": package_name,
        "window_id": window_id,
        "revision": revision,
        "title": title,
        "nodes": nodes,
        "sensitive_omitted": omitted,
    }


def _normalize_node(value: dict[str, Any]) -> dict[str, Any]:
    ref = _ascii_id(value.get("ref"), "node reference")
    role = _bounded_text(value.get("role"), 80) or "node"
    bounds = value.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError("phone node bounds are invalid")
    clean_bounds = [_exact_int(item, minimum=-100_000, maximum=100_000) for item in bounds]
    actions = value.get("actions", [])
    if not isinstance(actions, list) or len(actions) > len(_NODE_ACTIONS):
        raise ValueError("phone node actions are invalid")
    clean_actions = sorted({str(item) for item in actions if str(item) in _NODE_ACTIONS})
    return {
        "ref": ref,
        "role": role,
        "text": _bounded_text(value.get("text"), MAX_PHONE_TEXT_CHARS),
        "description": _bounded_text(value.get("description"), MAX_PHONE_TEXT_CHARS),
        "view_id": _bounded_text(value.get("view_id"), MAX_PHONE_TEXT_CHARS),
        "enabled": value.get("enabled") is True,
        "clickable": value.get("clickable") is True,
        "editable": value.get("editable") is True,
        "scrollable": value.get("scrollable") is True,
        "checked": value.get("checked") is True,
        "selected": value.get("selected") is True,
        "bounds": clean_bounds,
        "actions": clean_actions,
    }


def _normalize_file_listing(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("phone files listing is invalid")
    path = _phone_file_path(value.get("path"), allow_root=True)
    label = _bounded_text(value.get("label"), 80)
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_PHONE_FILE_ENTRIES:
        raise ValueError("phone files entries are invalid")
    clean_entries = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("phone files entry is invalid")
        name = _phone_file_name(item.get("name"))
        child = _phone_file_path(item.get("path"), allow_root=False)
        if not name or child != (f"{path}/{name}" if path else name):
            raise ValueError("phone files entry path is invalid")
        directory = item.get("directory")
        size = item.get("size")
        modified = item.get("modified_at")
        if not isinstance(directory, bool):
            raise ValueError("phone files entry type is invalid")
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (size, modified)):
            raise ValueError("phone files entry metadata is invalid")
        clean_entries.append({
            "name": name,
            "path": child,
            "directory": directory,
            "mime_type": _bounded_text(item.get("mime_type"), 120),
            "size": size,
            "modified_at": modified,
        })
    if not isinstance(value.get("truncated"), bool):
        raise ValueError("phone files listing bound is invalid")
    return {"path": path, "label": label, "entries": clean_entries, "truncated": value["truncated"]}


def _normalize_storage_report(
    value: Any,
    *,
    expected_path: str,
    max_documents: int,
) -> dict[str, Any]:
    if not isinstance(value, dict) or _phone_file_path(value.get("path"), allow_root=True) != expected_path:
        raise ValueError("phone storage report is invalid")
    documents = _exact_int(value.get("documents_scanned"), minimum=0, maximum=max_documents)
    directories = _exact_int(value.get("directories_scanned"), minimum=1, maximum=max_documents + 1)
    files = _exact_int(value.get("file_count"), minimum=0, maximum=documents)
    total = _exact_int(value.get("total_file_bytes"), minimum=0, maximum=2**63 - 1)
    largest = value.get("largest_files")
    file_types = value.get("file_types")
    if not isinstance(largest, list) or len(largest) > 20:
        raise ValueError("phone storage largest-file report is invalid")
    if not isinstance(file_types, list) or len(file_types) > 20:
        raise ValueError("phone storage type report is invalid")
    clean_largest = []
    for item in largest:
        if not isinstance(item, dict):
            raise ValueError("phone storage file entry is invalid")
        path = _phone_file_path(item.get("path"), allow_root=False)
        if expected_path and not path.startswith(expected_path + "/"):
            raise ValueError("phone storage file escaped the selected analysis path")
        clean_largest.append({
            "path": path,
            "mime_type": _bounded_text(item.get("mime_type"), 120),
            "size": _exact_int(item.get("size"), minimum=0, maximum=2**63 - 1),
            "modified_at": _exact_int(item.get("modified_at"), minimum=0, maximum=2**63 - 1),
        })
    if [item["size"] for item in clean_largest] != sorted(
        (item["size"] for item in clean_largest), reverse=True,
    ):
        raise ValueError("phone storage largest-file ordering is invalid")
    clean_types = []
    for item in file_types:
        if not isinstance(item, dict):
            raise ValueError("phone storage type entry is invalid")
        extension = _bounded_text(item.get("extension"), 20)
        if not extension:
            raise ValueError("phone storage type entry is invalid")
        clean_types.append({
            "extension": extension,
            "count": _exact_int(item.get("count"), minimum=1, maximum=max_documents),
            "bytes": _exact_int(item.get("bytes"), minimum=0, maximum=2**63 - 1),
        })
    if type(value.get("truncated")) is not bool:
        raise ValueError("phone storage truncation state is invalid")
    return {
        "path": expected_path,
        "label": _bounded_text(value.get("label"), 80),
        "documents_scanned": documents,
        "directories_scanned": directories,
        "file_count": files,
        "total_file_bytes": total,
        "largest_files": clean_largest,
        "file_types": clean_types,
        "truncated": value["truncated"],
    }


def _phone_file_path(value: Any, *, allow_root: bool) -> str:
    if value is None and allow_root:
        return ""
    if not isinstance(value, str):
        raise ValueError("phone files path is invalid")
    clean = value.strip().replace("\\", "/").strip("/")
    if len(clean) > 512 or any(ord(char) < 0x20 for char in clean):
        raise ValueError("phone files path is invalid")
    segments = [segment for segment in clean.split("/") if segment]
    if not allow_root and not segments:
        raise ValueError("phone files path is required")
    if any(segment in {".", ".."} or len(segment) > 120 for segment in segments):
        raise ValueError("phone files path is invalid")
    return "/".join(segments)


def _phone_file_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("phone files entry name is invalid")
    name = value.strip()
    if (
        not 1 <= len(name) <= 120
        or name in {".", ".."}
        or any(ord(char) < 0x20 or char in "/\\" for char in name)
    ):
        raise ValueError("phone files entry name is invalid")
    return name


def _phone_package_name(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"phone {label} is invalid")
    clean = value.strip()
    parts = clean.split(".")
    framework_package = label == "package" and clean == "android"
    if (
        not 3 <= len(clean) <= 220
        or (
            not framework_package
            and (
                len(parts) < 2
                or any(
                    not part or any(not (char.isalnum() or char == "_") for char in part)
                    for part in parts
                )
            )
        )
    ):
        raise ValueError(f"phone {label} is invalid")
    return clean


def _required_ref(value: Any) -> str:
    try:
        return _ascii_id(value, "target")
    except ValueError as exc:
        raise ValueError(str(exc)) from None


def _optional_ref(value: Any) -> str:
    return "" if value is None or str(value).strip() == "" else _required_ref(value)


def _ascii_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not 1 <= len(text) <= 96 or not text.isascii() or any(
        not (char.isalnum() or char in "-_.:") for char in text
    ):
        raise ValueError(f"phone {label} is invalid")
    return text


def _bounded_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit or any(ord(char) < 0x20 for char in text):
        raise ValueError("phone text is outside bounds")
    return text


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _strict_bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    return _exact_int(value, minimum=minimum, maximum=maximum)


def _exact_bool(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("phone boolean field is invalid")
    return value


def _exact_int(value: Any, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError("phone numeric field is invalid")
    return value
