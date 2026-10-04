"""Native Settings adapter over existing resident configuration owners."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mo_desktop.app_window import NativeAppWindow
from mo_desktop.settings_app.catalog import BY_ID, controls, validate_value


class SettingsPanel(NativeAppWindow):
    app_module = "mo_desktop.settings_app.app"
    app_title = "MO Settings"

    def __init__(self, companion: Any) -> None:
        self._c = companion
        super().__init__(getattr(getattr(companion, "_agent", None), "config", {}))
        self._hwnd = 0

    def open(self, *, on_source: Any = None, on_started: Any = None, on_ready: Any = None) -> None:
        super().show(on_source=on_source, on_started=on_started, on_ready=on_ready)

    def snapshot(self) -> dict[str, Any]:
        from importlib.metadata import PackageNotFoundError, version
        from interface import theming
        from mo_desktop.settings import load_settings
        from mo_desktop.tray import CompanionTray
        from core.provider.model_catalog import model_catalog_projection
        from core.state.preferences import RuntimePreferenceError, load_runtime_preferences
        from mo_desktop.voice.output import normalize_speech_rate
        from interface.workspace import activity_enabled

        settings = asdict(load_settings(self.config))
        values = {f"{section}.{key}": value for section, fields in settings.items()
                  if isinstance(fields, dict) for key, value in fields.items()}
        voice = dict(getattr(self._c, "_voice_cfg", {}) or {})
        active_role = self._c._active_skill_role_label()
        values.update({"voice." + key: voice.get(key, False) for key in ("stt_enabled", "tts_enabled", "chat_enabled", "role_active")})
        values.update({"voice.role": active_role or str(voice.get("role") or ""),
                       "voice.role_active": bool(active_role) or bool(voice.get("role_active", voice.get("role"))),
                       "voice.speech_rate": normalize_speech_rate(voice.get("speech_rate", 1)),
                       "voice.output_device": str(voice.get("output_device") or "default"),
                       "voice.conversation_provider": str(voice.get("conversation_provider") or "")})
        agent = self._c._agent
        catalog = model_catalog_projection(agent, allow_live=False)
        try:
            preferences = load_runtime_preferences(self.config).get("terminal", {})
        except (OSError, ValueError, RuntimePreferenceError):
            preferences = {}
        ui = self.config.get("interface", {})
        terminal = {"show_reasoning": preferences.get("show", {}).get("reasoning", ui.get("show", {}).get("reasoning", False)),
                    "show_tools": preferences.get("show", {}).get("tools", ui.get("show", {}).get("tools", True)),
                    "hints": preferences.get("hints", ui.get("hints", {}).get("enabled", True)),
                    "activity": preferences.get("activity", activity_enabled(self.config))}
        try:
            build = version("mo-agent")
        except PackageNotFoundError:
            build = "development checkout"
        return {
            "version": build,
            "controls": controls(values), "skin": theming.get_skin_name(),
            "skins": [{"id": key, "label": theming.skin_display_name(key),
                       "custom": theming.is_custom_skin(key), "colors": theming.skin_palette(skin)}
                      for key, skin in theming.available_skin_items()],
            "roles": list(self._c.conversation_role_options()),
            "voice_providers": list(self._c.voice_conversation_provider_names()),
            "startup": CompanionTray._startup_enabled(), "terminal": terminal,
            "models": catalog, "model_state": self._model_state(),
            "projects": self._projects(), "graph": self._graph(),
            "language_servers": sorted((self._authored().get("lsp") or {}).get("servers") or {}),
            "overview": self._overview(),
        }

    def _overview(self) -> list[dict[str, Any]]:
        from mo_desktop.settings_app.catalog import configuration_overview
        return configuration_overview(self._authored(), current=self.config)

    def _authored(self) -> dict:
        import yaml
        from pathlib import Path
        from core.state.paths import runtime_config_path
        path = runtime_config_path(self.config, fallback_to_default=True)
        if not path:
            raise ValueError("The active configuration source is unavailable.")
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(value, dict):
            raise ValueError("The configuration must contain a mapping.")
        return value

    def _graph(self) -> dict:
        from core.graph.structural_graph import graph_settings
        return graph_settings(self.config)

    def _projects(self) -> list[dict]:
        from pathlib import Path
        from core.state.paths import mo_home
        from core.state.preferences import project_preference_key
        agent = self._c._agent
        current = getattr(agent, "_effective_project_cwd", lambda: str(Path.cwd()))()
        entries = [(str(current), Path(current).name)]
        profile = getattr(agent, "profile", None)
        locations = getattr(profile, "project_locations", lambda: ())()
        entries.extend((item.path, item.name) for item in locations if item.path)
        private = mo_home(self.config) / "personal"
        result = {}
        manager = getattr(agent, "lsp_manager", None)
        for path, label in entries[:64]:
            root = Path(path).expanduser().absolute()
            if root == private or private in root.parents:
                continue
            root = root.resolve()
            if root == private or private in root.parents:
                continue
            key = project_preference_key(root)
            result.setdefault(key, {"id": key, "label": label, "path": str(root),
                                   "lsp": manager.status(str(root)) if manager else None})
        return list(result.values())

    def _model_state(self) -> dict:
        import os
        from core.runtime.surface_identity import DESKTOP_SURFACES
        from core.runtime.instance import recent_instance_snapshots
        from core.provider.model_catalog import runtime_model_selection
        from core.state.preferences import load_runtime_preferences, RuntimePreferenceError
        agent = self._c._agent
        try:
            preferences = load_runtime_preferences(self.config)
        except RuntimePreferenceError:
            preferences = {}
        instances = []
        desktop_observed = None
        for item in recent_instance_snapshots(self.config, current_pid=-1, max_age_seconds=180, limit=32):
            if not item.get("pid_alive"):
                continue
            if item["pid"] == os.getpid() and item.get("surface") in DESKTOP_SURFACES:
                desktop_observed = {"selection": item.get("model_selection"), "age": round(item["age_seconds"])}
            if item.get("surface") != "terminal":
                continue
            selection = item.get("model_selection") or {"model": item.get("model", ""), "source": "", "thinking": ""}
            instances.append({"instance": item.get("instance_id", ""), "pid": item["pid"],
                              "project": item.get("cwd", ""), "slot": item.get("slot", ""),
                              "selection": selection, "age": round(item["age_seconds"]),
                              "receipt": item.get("model_control", {}),
                              "controllable": bool(item.get("model_selection"))})
        thread = getattr(self._c, "_turn_thread", None)
        try:
            desktop = runtime_model_selection(agent, surface="mo_desktop")
        except ValueError:
            desktop = None
        return {"desktop": desktop,
                "desktop_follows": not bool(preferences.get("desktop", {}).get("model")),
                "desktop_busy": bool(thread and thread.is_alive()), "desktop_pid": os.getpid(),
                "desktop_observed": desktop_observed,
                "terminal_default": runtime_model_selection(agent, surface="terminal"),
                "instances": instances}

    def _handle_status(self, status: dict[str, Any]) -> None:
        if status.get("kind") == "settings_handle":
            self._hwnd = int(status.get("hwnd") or 0)
            return
        if status.get("kind") != "settings_request":
            return
        request_id = str(status.get("request_id") or "")[:80]
        action, payload = str(status.get("action") or ""), status.get("payload")
        if not request_id or not isinstance(payload, dict):
            return

        def run() -> None:
            try:
                response = {"ok": True, **self.dispatch(action, payload)}
            except (ValueError, KeyError) as exc:
                response = {"ok": False, "message": str(exc)[:180]}
            except Exception:
                from mo_desktop.desktop_log import log_exception
                log_exception("mo-settings-request-failed", config=self.config)
                response = {"ok": False, "message": "This change could not be completed."}
            if self.is_running():
                self._send({"cmd": "result", "request_id": request_id, "result": response})

        # Slow enumeration uses the existing pipe reader; live Desktop changes
        # are posted to their existing GUI owner without another worker pool.
        if action in {"devices", "chrome_status", "chrome_repair", "chrome_remove", "model", "models_status", "configuration", "lsp"}:
            run()
        elif self._c._post_gui_call(run) is False and self.is_running():
            self._send({"cmd": "result", "request_id": request_id,
                        "result": {"ok": False, "message": "MO Desktop is closing."}})

    def dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "snapshot":
            return {"state": self.snapshot()}
        if action in {"preview", "save"}:
            return self._setting(str(payload.get("id") or ""), payload.get("value"), save=action == "save")
        if action == "terminal":
            from core.state.preferences import persist_terminal_preferences
            key, value = payload.get("id"), payload.get("value")
            if key not in {"show_reasoning", "show_tools", "hints", "activity"} or not isinstance(value, bool):
                raise ValueError("Unknown terminal preference")
            persist_terminal_preferences(self.config, **{key: value})
            return {"message": "Saved · open terminals adopt this on reload."}
        if action == "model":
            return self._model(payload)
        if action == "models_status":
            return {"model_state": self._model_state()}
        if action == "configuration":
            from mo_desktop.settings_app.catalog import validate_configuration
            from core.state.configuration import persist_configuration
            key = str(payload.get("id") or "")
            value = validate_configuration(key, payload.get("value"))
            changes = node = {}
            if value is not None:
                keys = key.split(".")
                for name in keys[:-1]:
                    node = node.setdefault(name, {})
                node[keys[-1]] = value
            if not persist_configuration(self.config, changes, remove=(key,) if value is None else (), update_runtime=False):
                raise ValueError("Configuration was not saved; the previous value was kept.")
            return {"message": "Saved · reload the affected Terminal or restart Desktop to apply.", "overview": self._overview()}
        if action == "graph":
            from core.state.preferences import persist_graph_preferences
            key = str(payload.get("id") or "")
            option = self._graph().get(key)
            if not option:
                raise ValueError("Unknown graph preference")
            if option["managed_by"]:
                raise ValueError(f"Controlled by {option['managed_by']} in this process.")
            persist_graph_preferences(self.config, key, payload.get("value"))
            return {"graph": self._graph(), "message": "Saved · used by the next graph operation. Existing work can finish."}
        if action == "lsp":
            project = next((p for p in self._projects() if p["id"] == payload.get("project")), None)
            if not project or not project["lsp"]:
                raise ValueError("The project or its language-server manager is unavailable.")
            result = self._c._agent.lsp_manager.set_project_selection(str(payload.get("selection") or ""), project["path"])
            return {"lsp": result, "message": "Project preference saved · diagnostic requests use it immediately."}
        if action == "lsp_server":
            import re
            from core.state.configuration import persist_configuration
            language = str(payload.get("language") or "").strip().lower()
            command, args = payload.get("command"), payload.get("args")
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", language):
                raise ValueError("Use a language key such as python, typescript or go.")
            if (not isinstance(command, str) or not command.strip() or len(command) > 1000
                    or not isinstance(args, list) or len(args) > 40
                    or any(not isinstance(arg, str) or len(arg) > 1000 or "\x00" in arg for arg in args)
                    or "\x00" in command):
                raise ValueError("Enter an executable and up to 40 separate arguments.")
            if not persist_configuration(self.config, {"lsp": {"servers": {language: {"command": command.strip(), "args": args}}}}, update_runtime=False):
                raise ValueError("The language-server configuration was not saved.")
            return {"language_servers": sorted((self._authored().get("lsp") or {}).get("servers") or {}),
                    "message": "Server saved · reload Terminal or restart Desktop to use it. Nothing was installed or started."}
        if action == "devices":
            import sounddevice as sd
            names = ["default", *[str(row["name"]) for row in sd.query_devices() if row.get("max_output_channels", 0) > 0]]
            return {"devices": list(dict.fromkeys(names))}
        if action in {"chrome_status", "chrome_repair", "chrome_remove"}:
            from core import browser_bridge
            if action == "chrome_repair":
                browser_bridge.install()
            elif action == "chrome_remove":
                browser_bridge.uninstall()
            state = browser_bridge.status()
            return {"chrome": {key: bool(state.get(key)) for key in ("installed", "bridge_live", "extension_connected")}}
        if action == "chrome_copy":
            from core.browser_bridge import status
            directory = str(status().get("extension_dir") or "")
            from mo_desktop.gui_loop import set_clipboard_text

            set_clipboard_text(directory)
            return {"message": "Extension folder copied."}
        if action.startswith("skin_"):
            return self._skin(action, payload)
        if action == "startup":
            from mo_desktop.tray import CompanionTray
            enabled = payload.get("value")
            if not isinstance(enabled, bool):
                raise ValueError("Choose on or off")
            if not CompanionTray._set_startup(enabled):
                raise ValueError("Run at Startup could not be saved.")
            return {"message": "Startup preference saved."}
        if action == "show_cube":
            cube = getattr(self._c, "_cube", None)
            if cube is not None:
                cube.wake(seconds=5)
                cube.react("notice")
        elif action == "voice_preview":
            if not self._c.preview_voice():
                raise ValueError("Voice preview is unavailable. Check your speech configuration.")
        elif action == "reset":
            result = self._c.reset_desktop_settings()
            if not result.get("saved"):
                raise ValueError("Desktop preferences could not be reset.")
            return {"state": self.snapshot(), "message": "Desktop preferences reset." if result.get("live_applied") else "Reset saved · restart Desktop to finish applying."}
        elif action == "edit_config":
            from mo_desktop.tray import CompanionTray
            CompanionTray(self._c)._on_edit_config(None, None)
        elif action == "restart":
            self._c.request_restart()
        elif action == "dashboard":
            self._c.open_dashboard()
        elif action == "phone":
            self._c._display_phone_panel()
        elif action == "systemcare":
            self._c.open_systemcare_panel()
        else:
            raise ValueError("Unknown Settings action")
        return {}

    def _model(self, payload: dict) -> dict:
        import json
        import secrets
        from core.provider.model_catalog import model_catalog_projection, validate_model_selection
        from core.state.preferences import persist_desktop_model, persist_terminal_preferences
        target = payload.get("target")
        if target not in {"desktop", "terminal_default", "instance"}:
            raise ValueError("Choose Desktop, the Terminal default, or a running Terminal.")
        selection = None if target == "desktop" and payload.get("follow") is True else validate_model_selection(
            model_catalog_projection(self._c._agent, allow_live=False), payload.get("selection"))
        if target == "desktop":
            persist_desktop_model(self.config, selection)
            return {"model_state": self._model_state(), "message": "Desktop choice saved · applies to its next request."}
        if target == "terminal_default":
            persist_terminal_preferences(self.config, model=selection)
            return {"model_state": self._model_state(), "message": "Terminal default saved · existing Terminals keep their active model until changed or reloaded."}
        instance = next((row for row in self._model_state()["instances"]
                         if row["instance"] == payload.get("instance") and row["pid"] == payload.get("pid")), None)
        if not instance or not instance["controllable"]:
            raise ValueError("That Terminal is unavailable or needs a reload for Settings control.")
        from core.design.terminal_handoff import queue_terminal_control
        request_id = secrets.token_hex(12)
        queue_terminal_control("model", json.dumps({"request_id": request_id, "selection": selection}),
                               {"instance_id": instance["instance"], "pid": instance["pid"]},
                               project_root=instance["project"], expected_slot=instance["slot"], config=self.config)
        return {"request_id": request_id, "message": "Requested · waiting for that Terminal to confirm."}

    def _setting(self, key: str, value: Any, *, save: bool) -> dict[str, Any]:
        value = validate_value(key, value)
        section, field = key.split(".", 1)
        if not save and BY_ID[key][5] != "range":
            raise ValueError("This setting applies when selected.")
        if key == "voice.role":
            accepted = self._c.set_voice_role(value)
        elif key == "voice.role_active":
            accepted = self._c.set_voice_role_active(value)
        elif key == "voice.output_device":
            accepted = self._c.set_voice_output_device(value)
        elif key == "voice.conversation_provider":
            accepted = self._c.set_voice_conversation_provider(value)
        else:
            accepted = self._c.apply_desktop_setting(section, field, value)
        if accepted is False:
            raise ValueError("This setting could not be applied. The saved value was kept.")
        if section == "panel":
            self.apply_visual_state(self._c._visuals)
        if not save:
            return {"value": value}
        changes = {section: {field: value}}
        if key == "voice.role":
            changes[section]["role_active"] = bool(value)
        if self._c.persist_desktop_settings(changes) is False:
            return {"ok": False, "value": value, "message": "Preview only · this change could not be saved."}
        return {"value": value, "message": "Saved", "role_active": bool(value) if key == "voice.role" else None}

    def _skin(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        from interface import theming
        key = str(payload.get("id") or "")
        if action == "skin_apply":
            theming.set_skin(key)
        elif action == "skin_save":
            base = theming.registered_skins().get(key)
            if base is None:
                raise ValueError("Choose an existing skin first")
            label, colors = payload.get("label"), payload.get("colors")
            if not isinstance(label, str) or not isinstance(colors, dict):
                raise ValueError("Name your skin and choose its colors")
            key = theming.save_custom_skin(label, colors, base=base)
            theming.set_skin(key)
        elif action == "skin_delete":
            if not theming.is_custom_skin(key):
                raise ValueError("Only custom skins can be deleted")
            theming.delete_custom_skin(key)
        else:
            raise ValueError("Unknown skin action")
        self._c._refresh_theme_from_disk()
        return {"state": self.snapshot(), "message": "Skin saved"}
