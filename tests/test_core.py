import tempfile
import unittest
from pathlib import Path
from unittest import mock


class CoreSettingsTests(unittest.TestCase):
    """捕获配置损坏时崩溃、更新时覆盖其他字段这两类回归。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_invalid_json_falls_back_to_complete_defaults(self):
        from mimonitor_toolbox import core

        self.config_path.write_text("not json", encoding="utf-8")
        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            settings = core.load_settings()

        self.assertEqual(settings["close_behavior"], "tray")
        self.assertEqual(settings["local_dimming_memory"], {"sdr": None, "hdr": None})

    def test_update_settings_preserves_existing_keys(self):
        from mimonitor_toolbox import core

        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            self.assertTrue(core.update_settings({"saved_ip": "192.168.1.8"}))
            self.assertTrue(core.update_settings({"close_behavior": "exit"}))
            settings = core.load_settings()

        self.assertEqual(settings["saved_ip"], "192.168.1.8")
        self.assertEqual(settings["close_behavior"], "exit")

    def test_missing_active_preset_falls_back_to_baseline(self):
        from mimonitor_toolbox import core

        self.config_path.write_text(
            '{"active_preset_id": "deleted", "presets": []}', encoding="utf-8"
        )
        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            self.assertIsNone(core.load_settings()["active_preset_id"])

    def test_existing_active_preset_is_preserved(self):
        from mimonitor_toolbox import core

        self.config_path.write_text(
            '{"active_preset_id": "p1", "presets": [{"id": "p1"}]}',
            encoding="utf-8",
        )
        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            self.assertEqual(core.load_settings()["active_preset_id"], "p1")

    def test_source_mode_base_dir_is_project_root(self):
        from mimonitor_toolbox import core

        expected = Path(__file__).resolve().parents[1]
        with mock.patch.object(core.sys, "frozen", False, create=True):
            actual = Path(core.get_app_base_dir()).resolve()

        self.assertEqual(actual, expected)


class CleanupStaleExtractDirsTests(unittest.TestCase):
    """验证 onefile/_MEI 残留清理只删除死实例的解压目录。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_dir(self, name, dll_name="python313.dll"):
        d = self.base / name
        d.mkdir()
        if dll_name:
            (d / dll_name).write_bytes(b"fake dll")
        return d

    def test_removes_dead_extract_dirs(self):
        import os
        import time
        from mimonitor_toolbox import core

        dead_onefile = self._make_dir("onefile_123_456")
        dead_mei = self._make_dir("_MEI12345")
        unrelated = self._make_dir("unrelated_dir", dll_name=None)

        old_ts = time.time() - 600
        for p in (dead_onefile, dead_onefile / "python313.dll", dead_mei, dead_mei / "python313.dll", unrelated):
            os.utime(p, (old_ts, old_ts))

        core.cleanup_stale_extract_dirs(str(self.base))

        self.assertFalse(dead_onefile.exists())
        self.assertFalse(dead_mei.exists())
        self.assertTrue(unrelated.exists())

    def test_skips_recent_extract_dir(self):
        from mimonitor_toolbox import core

        recent_mei = self._make_dir("_MEI99999")
        core.cleanup_stale_extract_dirs(str(self.base), min_age_seconds=300.0)
        self.assertTrue(recent_mei.exists())

    def test_skips_dirs_without_python_dll(self):
        from mimonitor_toolbox import core

        suspicious = self._make_dir("onefile_789", dll_name=None)
        (suspicious / "some.txt").write_text("data", encoding="utf-8")

        core.cleanup_stale_extract_dirs(str(self.base))

        self.assertTrue(suspicious.exists())

    def test_skips_locked_dll_dir(self):
        from mimonitor_toolbox import core

        locked = self._make_dir("onefile_999_1")
        dll_path = locked / "python313.dll"
        original_open = open

        def refusing_open(path, mode="r", *args, **kwargs):
            if str(path) == str(dll_path) and "+" in mode:
                raise PermissionError(13, "denied")
            return original_open(path, mode, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=refusing_open):
            core.cleanup_stale_extract_dirs(str(self.base))

        self.assertTrue(locked.exists())
        self.assertTrue(dll_path.exists())


class SoundOutputHelpersTests(unittest.TestCase):
    """声音输出常量、兼容归一化（3 -> 8）与展示文案映射。"""

    def test_constants_and_options(self):
        from mimonitor_toolbox import core

        self.assertEqual(
            core.SOUND_OUTPUT_SETTINGS_KEY,
            "mitv.settings.db.sound.device.output_device",
        )
        self.assertEqual(
            core.SOUND_OUTPUT_OPTIONS,
            [(0, "本机扬声器"), (8, "HDMI(eARC)"), (5, "耳机孔")],
        )
        self.assertEqual(core.SOUND_OUTPUT_NAMES[3], "HDMI(eARC)")

    def test_parse_and_normalize_sound_output_value(self):
        from mimonitor_toolbox import core

        self.assertEqual(core.normalize_sound_output_value("0"), 0)
        self.assertEqual(core.normalize_sound_output_value("8"), 8)
        self.assertEqual(core.normalize_sound_output_value(5), 5)
        self.assertEqual(core.normalize_sound_output_value("3"), 8)
        self.assertEqual(core.parse_sound_output_value("3"), 3)
        self.assertIsNone(core.normalize_sound_output_value(None))
        self.assertIsNone(core.normalize_sound_output_value("null"))
        self.assertIsNone(core.normalize_sound_output_value(""))
        self.assertIsNone(core.normalize_sound_output_value("bad"))
        self.assertIsNone(core.normalize_sound_output_value(True))

    def test_format_sound_output_label(self):
        from mimonitor_toolbox import core

        self.assertEqual(core.format_sound_output_label(0), "本机扬声器")
        self.assertEqual(core.format_sound_output_label(8), "HDMI(eARC)")
        self.assertEqual(core.format_sound_output_label("3"), "HDMI(eARC)")
        self.assertEqual(core.format_sound_output_label(5), "耳机孔")
        self.assertEqual(core.format_sound_output_label(99), "未知 (99)")
        self.assertEqual(core.format_sound_output_label(None), "未知")


class ConnectionModeTests(unittest.TestCase):
    """覆盖连接模式默认配置、规范化、展示文案和持久化。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_connection_mode_defaults_and_normalization(self):
        from mimonitor_toolbox import core

        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            settings = core.load_settings()

        self.assertEqual(settings["connection_mode"], core.CONNECTION_MODE_ADB)
        self.assertEqual(core.normalize_connection_mode("daemon"), core.CONNECTION_MODE_DAEMON)
        self.assertEqual(core.normalize_connection_mode(" DAEMON "), core.CONNECTION_MODE_DAEMON)
        self.assertEqual(core.normalize_connection_mode("adb"), core.CONNECTION_MODE_ADB)
        self.assertEqual(core.normalize_connection_mode(None), core.CONNECTION_MODE_ADB)
        self.assertEqual(core.normalize_connection_mode("invalid"), core.CONNECTION_MODE_ADB)
        self.assertEqual(core.format_connection_mode_label("adb"), "兼容模式 (ADB)")
        self.assertEqual(core.format_connection_mode_label("daemon"), "混合加速 (Daemon)")

    def test_update_settings_persists_connection_mode(self):
        from mimonitor_toolbox import core

        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            self.assertTrue(core.update_settings({"connection_mode": "daemon"}))
            settings = core.load_settings()
            self.assertEqual(settings["connection_mode"], core.CONNECTION_MODE_DAEMON)
            self.assertTrue(core.update_settings({"connection_mode": "adb"}))
            self.assertEqual(core.load_settings()["connection_mode"], core.CONNECTION_MODE_ADB)

    def test_verbose_log_defaults_false_and_page_display_names(self):
        from mimonitor_toolbox import core

        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            self.assertFalse(core.load_settings()["verbose_log"])
            core.update_settings({"verbose_log": True})
            self.assertTrue(core.load_settings()["verbose_log"])

        self.assertEqual(core.format_page_display_name("picturePage"), "画面设置")
        self.assertEqual(core.format_page_display_name("gamePage"), "游戏模式")
        self.assertEqual(core.format_page_display_name("sourcePage"), "信号源与音频")
        self.assertEqual(core.format_page_display_name("lightPage"), "屏幕灯")
        self.assertEqual(core.format_page_display_name("unknownPage"), "unknownPage")

        detail_calls = []
        host_with_detail = type("Host", (), {"log_detail": detail_calls.append})()
        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            core.emit_detail_log(host_with_detail, "明细条目")
        self.assertEqual(detail_calls, ["明细条目"])


class SettingsSafetyTests(unittest.TestCase):
    """Settings backup, validation, relative path, retry, and verbose_log cache behavior."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_corrupt_config_backed_up_and_unreadable_refuses_overwrite(self):
        from mimonitor_toolbox import core

        self.config_path.write_text("{corrupt json", encoding="utf-8")
        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            loaded = core.load_settings()
            self.assertEqual(loaded["connection_mode"], core.CONNECTION_MODE_ADB)
            backup_file = Path(f"{self.config_path}.corrupt.bak")
            self.assertTrue(backup_file.exists())
            self.assertEqual(backup_file.read_text(encoding="utf-8"), "{corrupt json")

        self.config_path.write_text('{"last_ip": "192.168.1.9"}', encoding="utf-8")
        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)), \
                mock.patch.object(core, "_read_settings_json", side_effect=PermissionError(13, "denied")):
            self.assertFalse(core.update_settings({"last_ip": "192.168.1.88"}))
            self.assertEqual(self.config_path.read_text(encoding="utf-8"), '{"last_ip": "192.168.1.9"}')

    def test_list_settings_validation_coerces_bad_types(self):
        import json
        from mimonitor_toolbox import core

        self.config_path.write_text(
            json.dumps({
                "scan_force_devices": "not-a-list",
                "scan_block_devices": ["eth0", {"bad": 1}, 123, True],
                "scan_force_subnets": 42,
                "tray_items": None,
            }),
            encoding="utf-8",
        )
        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            loaded = core.load_settings()
        self.assertEqual(loaded["scan_force_devices"], [])
        self.assertEqual(loaded["scan_block_devices"], ["eth0", "123"])
        self.assertEqual(loaded["scan_force_subnets"], [])
        self.assertEqual(loaded["tray_items"], ["picture_mode", "local_dimming", "backlight"])

    def test_relative_settings_path_and_replace_retry(self):
        import os
        from mimonitor_toolbox import core

        old_cwd = os.getcwd()
        os.chdir(self.temp_dir.name)
        try:
            real_replace = os.replace
            attempts = [0]

            def flaky_replace(src, dst):
                attempts[0] += 1
                if attempts[0] == 1:
                    raise PermissionError(5, "Access denied")
                return real_replace(src, dst)

            with mock.patch.dict(os.environ, {"MIMONITOR_SETTINGS_PATH": "config.json"}), \
                    mock.patch.object(core.os, "replace", side_effect=flaky_replace), \
                    mock.patch.object(core.time, "sleep"):
                self.assertTrue(core.update_settings({"last_ip": "192.168.1.55"}))
            self.assertEqual(attempts[0], 2)
            self.assertTrue((Path(self.temp_dir.name) / "config.json").exists())
        finally:
            os.chdir(old_cwd)

    def test_emit_detail_log_uses_cache_and_invalidation(self):
        from mimonitor_toolbox import core

        log_calls = []
        host = type("Host", (), {"log": log_calls.append})()
        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            core.invalidate_verbose_log_cache()
            core.update_settings({"verbose_log": True})
            with mock.patch.object(core.json, "load", wraps=core.json.load) as load_spy:
                core.emit_detail_log(host, "first")
                core.emit_detail_log(host, "second")
                self.assertEqual(load_spy.call_count, 1)
            core.update_settings({"verbose_log": False})
            core.emit_detail_log(host, "third")
        self.assertEqual(log_calls, ["first", "second"])


if __name__ == "__main__":
    unittest.main()
