"""Tests for companion system tray (Phase 4)."""


class TestCompanionTrayImport:
    """Smoke tests for tray module imports and basic structure."""

    def test_import_tray_module(self):
        from interface.companion.tray import CompanionTray, start_tray_if_enabled
        assert CompanionTray is not None
        assert start_tray_if_enabled is not None

    def test_tray_instantiation(self):
        from interface.companion.tray import CompanionTray
        tray = CompanionTray(companion=None)
        assert tray is not None
        assert tray.mode == "guide"

    def test_tray_available_is_bool(self):
        from interface.companion.tray import CompanionTray
        tray = CompanionTray(companion=None)
        result = tray.available
        # pystray may or may not be installed; result is always bool
        assert isinstance(result, bool)


class TestCompanionTrayMode:
    """Guide/Do mode switching."""

    def test_default_mode_is_guide(self):
        from interface.companion.tray import CompanionTray
        tray = CompanionTray(companion=None)
        assert tray.mode == "guide"

    def test_set_mode_to_do(self):
        from interface.companion.tray import CompanionTray
        tray = CompanionTray(companion=None)
        tray.set_mode("do")
        assert tray.mode == "do"

    def test_set_mode_to_guide(self):
        from interface.companion.tray import CompanionTray
        tray = CompanionTray(companion=None)
        tray.set_mode("do")
        tray.set_mode("guide")
        assert tray.mode == "guide"


class TestCompanionTrayStartup:
    """Startup shortcut management (Windows-only, degrades gracefully)."""

    def test_startup_enabled_is_bool(self):
        from interface.companion.tray import CompanionTray
        result = CompanionTray._startup_enabled()
        assert isinstance(result, bool)

    def test_set_startup_noop_import_error(self, monkeypatch):
        """_set_startup degrades gracefully when win32com unavailable."""
        from interface.companion.tray import CompanionTray
        # Simulate win32com not importable
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "win32com.client" or name == "pythoncom":
                raise ImportError("Mock missing win32com")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        # Should not raise
        CompanionTray._set_startup(True)
        CompanionTray._set_startup(False)

    def test_set_startup_enable_mocked(self, monkeypatch, tmp_path):
        """_set_startup creates shortcut when win32com is available."""
        from interface.companion.tray import CompanionTray
        import sys

        # Mock APPDATA to tmp_path
        monkeypatch.setenv("APPDATA", str(tmp_path))
        startup_dir = tmp_path / "Microsoft/Windows/Start Menu/Programs/Startup"
        startup_dir.mkdir(parents=True, exist_ok=True)

        # Mock win32com + pythoncom
        mock_dispatch_calls = []

        class MockShortcut:
            def __init__(self):
                self.TargetPath = None
                self.Arguments = None
                self.WorkingDirectory = None
                self.Description = None
                self.IconLocation = None

            def Save(self):
                mock_dispatch_calls.append("save")

        class MockShell:
            def CreateShortcut(self, path):
                mock_dispatch_calls.append(("create_shortcut", path))
                return MockShortcut()

        # Patch sys.modules for import
        import types
        mock_pythoncom = types.ModuleType("pythoncom")
        mock_pythoncom.CoInitialize = lambda: None
        mock_pythoncom.CoUninitialize = lambda: None
        monkeypatch.setitem(sys.modules, "pythoncom", mock_pythoncom)

        mock_win32com = types.ModuleType("win32com")
        mock_win32com_client = types.ModuleType("win32com.client")
        mock_win32com_client.Dispatch = lambda progid: MockShell()
        mock_win32com.client = mock_win32com_client
        monkeypatch.setitem(sys.modules, "win32com", mock_win32com)
        monkeypatch.setitem(sys.modules, "win32com.client", mock_win32com_client)

        CompanionTray._set_startup(True)

        shortcut_path = startup_dir / "MO Companion.lnk"
        assert shortcut_path.exists() or any("create_shortcut" in str(c) for c in mock_dispatch_calls)

        # Cleanup
        CompanionTray._set_startup(False)
        assert not shortcut_path.exists()


class TestStartTrayIfEnabled:
    """start_tray_if_enabled factory function."""

    def test_returns_none_when_disabled(self):
        from interface.companion.tray import start_tray_if_enabled
        result = start_tray_if_enabled(companion=None, voice_config={})
        assert result is None

    def test_returns_none_when_tray_disabled_explicit(self):
        from interface.companion.tray import start_tray_if_enabled
        result = start_tray_if_enabled(
            companion=None,
            voice_config={"tray_enabled": False},
        )
        assert result is None
