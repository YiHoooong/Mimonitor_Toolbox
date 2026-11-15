import time
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock


_qt_application = None


def setUpModule():
    global _qt_application
    from PyQt6.QtWidgets import QApplication

    _qt_application = QApplication.instance() or QApplication([])


class DisplayFeatureTests(unittest.TestCase):
    """捕获画面模式分组在迁移后丢失或映射错误。"""

    def test_picture_mode_group_maps_presets_to_primary_mode(self):
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        host = object()
        self.assertEqual(DisplayFeaturesMixin._picture_mode_group_name(host, 64), "标准")
        self.assertEqual(DisplayFeaturesMixin._picture_mode_group_name(host, 25), "游戏")
        self.assertEqual(DisplayFeaturesMixin._picture_mode_group_name(host, 9), "电影")

    def test_zero_scenario_uses_picture_mode_without_overriding_valid_scenarios(self):
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        host = DisplayFeaturesMixin()
        host.current_vals = {"picture_preset_scenario": 0, "picture_mode": 13}
        self.assertEqual(host._active_picture_scene_mode(), 13)
        host.current_vals["picture_mode"] = 14
        self.assertEqual(host._active_picture_scene_mode(), 14)
        host.current_vals["picture_preset_scenario"] = 25
        self.assertEqual(host._active_picture_scene_mode(), 25)
        host.current_vals = {"picture_preset_scenario": 0, "picture_mode": 0}
        self.assertEqual(host._active_picture_scene_mode(), 0)

    def test_hdr_state_query_uses_selected_display_only(self):
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        host = DisplayFeaturesMixin()
        host._hdr_target_displays = [
            {"device_name": "DISPLAY1", "device_id": "XMI27B3-1", "label": "红米"},
            {"device_name": "DISPLAY2", "device_id": "VIRTUAL-2", "label": "虚拟屏"},
        ]
        with mock.patch.object(display_features, "load_settings",
                               return_value={"hdr_target_display_id": "XMI27B3-1"}), \
                mock.patch.object(display_features, "query_windows_hdr_enabled",
                                  return_value=False) as query:
            host._apply_hdr_target_resolution()
            self.assertFalse(host._query_windows_hdr_state())
        # 查询只带 GDI 名字，不再自己去枚举/解析显示器
        query.assert_called_once_with(target_device_name="DISPLAY1")
        self.assertEqual(host._hdr_target_display_label, "红米")

    def test_missing_hdr_target_does_not_use_other_display(self):
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        host = DisplayFeaturesMixin()
        host._hdr_target_displays = [
            {"device_name": "DISPLAY2", "device_id": "VIRTUAL-2", "label": "虚拟屏"},
        ]
        with mock.patch.object(display_features, "load_settings",
                               return_value={"hdr_target_display_id": "missing"}), \
                mock.patch.object(display_features, "query_windows_hdr_enabled") as query:
            host._apply_hdr_target_resolution()
            self.assertIsNone(host._query_windows_hdr_state())
        query.assert_not_called()
        self.assertIsNone(host._hdr_target_device_name)

    def test_hdr_state_query_never_enumerates_displays(self):
        """轮询每 3 秒一次，绝不能顺手去枚举显示器。"""
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        host = DisplayFeaturesMixin()
        host._hdr_target_device_name = "DISPLAY1"
        with mock.patch.object(display_features, "query_windows_hdr_enabled",
                               return_value=True) as query:
            self.assertTrue(host._query_windows_hdr_state())
        query.assert_called_once_with(target_device_name="DISPLAY1")
        self.assertFalse(hasattr(display_features, "list_windows_displays"))


class CrosshairModeReconcileTests(unittest.TestCase):
    """准星模式联动：离开游戏模式记住并隐藏，回到游戏模式还原。"""

    def _reconcile(self, vals, settings=None, connected=True, busy=False, last_mode=None):
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        settings = settings if settings is not None else {
            "crosshair_game_mode_only": True,
            "crosshair_memory": None,
        }
        applied = []

        class Host(DisplayFeaturesMixin):
            def __init__(self):
                self.current_vals = dict(vals)
                self.adb_connected = connected
                self._crosshair_reconcile_busy = busy
                if last_mode is not None:
                    self._crosshair_last_mode = last_mode

            def _apply_crosshair_mode_value(self, value, message):
                applied.append(value)
                self.current_vals["front_sight_index"] = value

            def _update_crosshair_mode_status_label(self):
                pass

        host = Host()
        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(display_features, "update_settings", side_effect=settings.update):
            host._reconcile_crosshair_mode_state()
        return applied, settings

    def test_leaving_game_mode_hides_crosshair_and_remembers_it(self):
        applied, settings = self._reconcile({
            "picture_mode": 14,
            "front_sight_index": 3,
        }, last_mode=10)

        self.assertEqual(applied, [0])
        self.assertEqual(settings["crosshair_memory"], 3)

    def test_entering_game_mode_restores_remembered_crosshair(self):
        applied, _ = self._reconcile(
            {"picture_mode": 10, "front_sight_index": 0},
            settings={"crosshair_game_mode_only": True, "crosshair_memory": 3},
            last_mode=14,
        )

        self.assertEqual(applied, [3])

    def test_game_mode_without_transition_does_not_restore(self):
        """用户用显示器 OSD 主动关掉准星后，不应被应用自动打开。"""
        applied, _ = self._reconcile(
            {"picture_mode": 10, "front_sight_index": 0},
            settings={"crosshair_game_mode_only": True, "crosshair_memory": 3},
            last_mode=10,
        )

        self.assertEqual(applied, [])

    def test_first_reconcile_after_connect_is_baseline_only(self):
        """首次对账只确立基线，不做还原（此前模式未知）。"""
        applied, _ = self._reconcile(
            {"picture_mode": 10, "front_sight_index": 0},
            settings={"crosshair_game_mode_only": True, "crosshair_memory": 3},
        )

        self.assertEqual(applied, [])

    def test_game_mode_keeps_user_choice_without_memory(self):
        applied, _ = self._reconcile({
            "picture_mode": 10,
            "front_sight_index": 5,
        }, last_mode=14)

        self.assertEqual(applied, [])

    def test_disabled_toggle_leaves_crosshair_alone(self):
        applied, _ = self._reconcile(
            {"picture_mode": 14, "front_sight_index": 3},
            settings={"crosshair_game_mode_only": False, "crosshair_memory": None},
            last_mode=10,
        )

        self.assertEqual(applied, [])

    def test_unknown_mode_or_value_is_skipped(self):
        for vals in (
            {"front_sight_index": 3},
            {"picture_mode": 14, "front_sight_index": None},
            {"picture_mode": 14, "front_sight_index": "N/A"},
            {"picture_mode": "null", "front_sight_index": 3},
        ):
            with self.subTest(vals=vals):
                applied, _ = self._reconcile(vals, last_mode=10)
                self.assertEqual(applied, [])

    def test_already_hidden_crosshair_is_not_rewritten(self):
        applied, settings = self._reconcile({
            "picture_mode": 9,
            "front_sight_index": 0,
        }, last_mode=10)

        self.assertEqual(applied, [])
        self.assertIsNone(settings["crosshair_memory"])

    def test_disconnected_device_is_skipped(self):
        applied, _ = self._reconcile(
            {"picture_mode": 14, "front_sight_index": 3},
            connected=False,
            last_mode=10,
        )

        self.assertEqual(applied, [])

    def test_busy_flag_blocks_reentrant_reconcile(self):
        applied, _ = self._reconcile(
            {"picture_mode": 14, "front_sight_index": 3},
            busy=True,
            last_mode=10,
        )

        self.assertEqual(applied, [])

    def test_manual_choice_updates_memory(self):
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        settings = {"crosshair_game_mode_only": True, "crosshair_memory": None}
        calls = []

        class Host(DisplayFeaturesMixin):
            def _set_game_feature(
                self, key, value, message, retrigger_game_mode=False, on_success=None
            ):
                calls.append((key, value, retrigger_game_mode))
                if callable(on_success):
                    on_success()

            def _update_crosshair_mode_status_label(self):
                pass

            def log(self, message):
                pass

        host = Host()
        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(display_features, "update_settings", side_effect=settings.update):
            host._fs(4)
            self.assertEqual(settings["crosshair_memory"], 4)
            host._fs(0)
            self.assertIsNone(settings["crosshair_memory"])

        self.assertEqual(
            calls,
            [
                ("front_sight_index", 4, True),
                ("front_sight_index", 0, True),
            ],
        )


class HotkeyCountdownTests(unittest.TestCase):
    """快捷键「松手后生效」的等待时长与悬浮提示倒计时条。"""

    def _host(self, settings, osd=None):
        from PyQt6.QtCore import QObject

        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        class Host(DisplayFeaturesMixin, QObject):
            def __init__(self):
                super().__init__()
                self.current_vals = {}
                self._cycle_hotkey_pending = {}
                self._cycle_hotkey_timers = {}
                self._adjust_hotkey_pending = {}
                self._adjust_hotkey_timers = {}
                self.osd = osd
                self.logs = []
                self.values_signal = mock.Mock()

            def log(self, message):
                self.logs.append(message)

            def _highlight_mode(self, _value):
                pass

        return Host()

    def _delay(self, settings):
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        class Host(DisplayFeaturesMixin):
            pass

        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)):
            return Host().effective_hotkey_delay()

    def test_delay_follows_settings(self):
        self.assertAlmostEqual(
            self._delay({"hotkey_countdown_enabled": True, "hotkey_countdown_seconds": 0.8}), 0.8
        )
        self.assertAlmostEqual(
            self._delay({"hotkey_countdown_enabled": True, "hotkey_countdown_seconds": 0.0}), 0.1
        )
        self.assertEqual(
            self._delay({"hotkey_countdown_enabled": False, "hotkey_countdown_seconds": 0.8}), 0.0
        )
        self.assertAlmostEqual(self._delay({}), 0.8)

    def test_staging_shows_countdown_and_waits(self):
        from mimonitor_toolbox import display_features

        osd = mock.Mock()
        host = self._host({}, osd=osd)
        settings = {"hotkey_countdown_enabled": True, "hotkey_countdown_seconds": 0.8}

        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(host, "_commit_cycle_hotkey_action") as commit:
            host._stage_cycle_hotkey_action(
                "picture_mode_cycle", "picture_mode",
                [(14, "标准"), (10, "游戏")], "画面模式", lambda v, n: None,
            )
            timer = host._cycle_hotkey_timers["picture_mode_cycle"]

        osd.show_hud.assert_called_once_with("画面模式", "游戏", countdown=0.8)
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), 800)
        commit.assert_not_called()

    def test_disabled_countdown_applies_immediately(self):
        from mimonitor_toolbox import display_features

        osd = mock.Mock()
        host = self._host({}, osd=osd)
        settings = {"hotkey_countdown_enabled": False}

        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(host, "_commit_cycle_hotkey_action") as commit:
            host._stage_cycle_hotkey_action(
                "picture_mode_cycle", "picture_mode",
                [(14, "标准"), (10, "游戏")], "画面模式", lambda v, n: None,
            )
            timer = host._cycle_hotkey_timers["picture_mode_cycle"]

        # 不带进度条，且立即提交
        osd.show_hud.assert_called_once_with("画面模式", "游戏", countdown=None)
        commit.assert_called_once_with("picture_mode_cycle")
        self.assertFalse(timer.isActive())

    def test_tray_slider_does_not_pop_hud(self):
        """托盘菜单里已内联显示数值，滑块调整不应再弹悬浮窗。"""
        from mimonitor_toolbox import display_features

        osd = mock.Mock()
        host = self._host({}, osd=osd)
        settings = {"hotkey_countdown_enabled": True, "hotkey_countdown_seconds": 0.8}
        cfg = {"label": "背光", "setting": "picture_backlight", "min": 1, "max": 100}

        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(host, "_set_adjustable_display_value"):
            host._stage_adjustable_display_value("backlight", cfg, 45, show_hud=False)

        osd.show_hud.assert_not_called()
        self.assertIn("backlight", host._adjust_hotkey_pending)

    def test_commit_ends_countdown_bar(self):
        osd = mock.Mock()
        host = self._host({}, osd=osd)
        host._cycle_hotkey_pending["x"] = None

        host._commit_cycle_hotkey_action("x")

        osd.end_countdown.assert_called_once()

    def test_adjust_hotkey_pops_hud_once(self):
        """回归：调节类快捷键以前会弹两次悬浮提示 —— 一次无倒计时、紧接着
        又一次带倒计时。前者完全是多余的，每按一次都多做一轮窗口操作。"""
        from mimonitor_toolbox import display_features

        osd = mock.Mock()
        host = self._host({}, osd=osd)
        host.adb_connected = True
        host._adjust_hotkey_resolving = set()
        host.current_vals["picture_backlight"] = 50
        settings = {"hotkey_countdown_enabled": True, "hotkey_countdown_seconds": 0.8}

        with mock.patch.object(display_features, "load_settings",
                               side_effect=lambda: dict(settings)), \
                mock.patch.object(host, "_stage_adjustable_display_value") as stage:
            host.trigger_adjust_hotkey(
                {"param": "backlight", "direction": "increase", "step": 5})

        # trigger_adjust_hotkey 自己不再弹；提示由 _stage_adjustable_display_value
        # 内部那一次（带倒计时）负责
        osd.show_hud.assert_not_called()
        stage.assert_called_once()
        self.assertEqual(stage.call_args.args[0], "backlight")
        self.assertEqual(stage.call_args.args[2], 55, "50 + 5")


class PresetOrchestrationTests(unittest.TestCase):
    """预设的应用 / 恢复 / 自动任务调度。

    只测编排：真正逐项怎么写的顺序在 test_presets.py 里测。这里关心的是
    「快照什么时候记、记到哪、还原用谁的快照、未连接时怎么办」。
    """

    def _host(self, settings, connected=True):
        from contextlib import nullcontext

        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        calls = []
        logs = []

        class FakeSignal:
            def __init__(self):
                self.emitted = []

            def emit(self, *args):
                self.emitted.append(args)

        class FakeAdb:
            def __init__(self):
                self.calls = calls      # 同一个 list，测试里可以直接清空

            def transaction(self):
                return nullcontext()

            def put(self, key, value, check=False):
                calls.append(("put", key, value))

            def jni_set(self, key, value, upd=3, check=False):
                calls.append(("jni_set", key, value, upd))

            def refresh_pq(self, check=False):
                calls.append(("refresh_pq",))

            def jni_set_color_gains(self, red, green, blue, check=False):
                calls.append(("gains", red, green, blue))

            def hdr_tone_mapping(self, value, upd=3, check=False):
                calls.append(("hdr", value))

            def check_and_heal_jar(self):
                calls.append(("heal",))

        class FakeTimer:
            def __init__(self):
                self.start_count = 0

            def start(self):
                self.start_count += 1

        class Host(DisplayFeaturesMixin):
            adb = FakeAdb()
            current_vals = {}
            adb_connected = connected
            _preset_sync_timer = FakeTimer()
            _page_loaded = set()
            _page_loading = set()
            _cleanup_done = False
            _adb_busy_until = 0.0
            _local_dimming_memory_suppress_until = 0.0
            _auto_task_checking = False
            _auto_task_active_id = None
            _auto_task_waiting_id = None
            message_signal = FakeSignal()
            preset_page = object()
            picture_page = object()

            def __init__(self):
                self.stack = []

                class FakeStack:
                    def setCurrentWidget(inner, page):
                        self.stack.append(page)

                self.stackedWidget = FakeStack()
                self._preset_banner = mock.Mock()

            def check_connection(self):
                return connected

            def _mark_adb_busy(self, seconds=2.0):
                pass

            def log(self, message):
                logs.append(message)

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                operation()
                if on_success:
                    on_success()

            def _read_picture_values(self):
                return {"picture_mode": 14, "picture_contrast": 50}

            def _refresh_page_data(self, page):
                calls.append(("refresh", page))

        return Host(), calls, logs

    @staticmethod
    def _frozen_time(hour, minute):
        """把 display_features 里的 time 换成假时钟（只看得到 localtime/monotonic/strftime）。"""
        import time as real_time

        from mimonitor_toolbox import display_features

        fake = mock.MagicMock()
        fake.localtime.return_value = real_time.struct_time(
            (2026, 9, 22, hour, minute, 0, 0, 0, 0)
        )
        fake.monotonic.return_value = 1000.0
        fake.strftime.return_value = "2026-09-22 %02d:%02d:00" % (hour, minute)
        return mock.patch.object(display_features, "time", fake)

    def _patched(self, settings):
        from mimonitor_toolbox import display_features

        return (
            mock.patch.object(display_features, "load_settings",
                              side_effect=lambda: dict(settings)),
            mock.patch.object(display_features, "update_settings",
                              side_effect=settings.update),
            # 应用成功后会排一个延时回读，测试里不需要真的排
            mock.patch.object(display_features, "QTimer"),
        )

    def _run(self, settings, connected, fn):
        load_patch, save_patch, timer_patch = self._patched(settings)
        with load_patch, save_patch, timer_patch:
            host, calls, logs = self._host(settings, connected=connected)
            fn(host)
            return host, calls, logs

    # ── 手动应用 ──

    def test_applying_a_preset_snapshots_before_writing(self):
        settings = {"presets": [{"id": "p1", "name": "看电影",
                                 "values": {"picture_mode": 9, "picture_contrast": 70}}]}

        def run(host):
            host.apply_preset_by_id("p1")

        _, calls, _ = self._run(settings, True, run)

        # 快照记的是**应用前**读回来的值，不是预设的值
        self.assertEqual(settings["preset_snapshot"]["values"],
                         {"picture_mode": 14, "picture_contrast": 50})
        self.assertEqual(settings["preset_snapshot"]["source"], "看电影")
        # 写进去的是预设的值
        self.assertIn(("put", "picture_mode", "9"), calls)
        self.assertIn(("put", "picture_contrast", "70"), calls)

    def test_applying_an_unknown_preset_does_nothing(self):
        settings = {"presets": []}

        def run(host):
            host.apply_preset_by_id("missing")

        _, calls, logs = self._run(settings, True, run)
        self.assertEqual(calls, [])
        self.assertNotIn("preset_snapshot", settings)
        self.assertTrue(any("不存在" in m for m in logs))

    def test_apply_is_refused_when_disconnected(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {"picture_mode": 9}}]}

        def run(host):
            host.apply_preset_by_id("p1")

        _, calls, _ = self._run(settings, False, run)
        self.assertEqual(calls, [])
        self.assertNotIn("preset_snapshot", settings)

    # ── 恢复 ──

    def test_restore_writes_the_snapshot_back(self):
        settings = {"preset_snapshot": {"source": "A",
                                        "values": {"picture_mode": 9, "picture_contrast": 77}}}

        def run(host):
            host.restore_preset_snapshot()

        _, calls, _ = self._run(settings, True, run)
        self.assertIn(("put", "picture_mode", "9"), calls)
        self.assertIn(("put", "picture_contrast", "77"), calls)

    def test_restore_does_not_overwrite_the_snapshot(self):
        """否则第二次点恢复就还原到「恢复后的状态」，撤销链断掉。"""
        settings = {"preset_snapshot": {"source": "A", "values": {"picture_mode": 9}}}

        def run(host):
            host.restore_preset_snapshot()

        self._run(settings, True, run)
        self.assertEqual(settings["preset_snapshot"]["values"], {"picture_mode": 9})
        self.assertEqual(settings["preset_snapshot"]["source"], "A")

    def test_restore_without_snapshot_is_a_noop(self):
        settings = {}

        def run(host):
            host.restore_preset_snapshot()

        _, calls, logs = self._run(settings, True, run)
        self.assertEqual(calls, [])
        self.assertTrue(any("还没有可退回" in m for m in logs))

    def test_has_preset_snapshot_reflects_storage(self):
        from mimonitor_toolbox import display_features

        empty = {"preset_snapshot": None}
        with mock.patch.object(display_features, "load_settings",
                               side_effect=lambda: dict(empty)):
            host, _, _ = self._host(empty)
            self.assertFalse(host.has_preset_snapshot())

        filled = {"preset_snapshot": {"values": {"picture_mode": 9}}}
        with mock.patch.object(display_features, "load_settings",
                               side_effect=lambda: dict(filled)):
            host, _, _ = self._host(filled)
            self.assertTrue(host.has_preset_snapshot())

    # ── 自动任务调度 ──

    def _task_settings(self, snapshot=None):
        return {
            "presets": [{"id": "p1", "name": "A", "values": {"picture_mode": 9}}],
            "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                            "preset_id": "p1", "snapshot": snapshot}],
        }

    def test_task_starts_inside_its_period(self):
        settings = self._task_settings()

        def run(host):
            with self._frozen_time(9, 0):
                host._auto_task_tick()

        host, calls, _ = self._run(settings, True, run)
        self.assertEqual(host._auto_task_active_id, "t1")
        self.assertIn(("put", "picture_mode", "9"), calls)
        # 快照存进任务自身，供时段结束时还原
        self.assertEqual(settings["auto_tasks"][0]["snapshot"]["values"],
                         {"picture_mode": 14, "picture_contrast": 50})

    def test_task_does_not_start_outside_its_period(self):
        settings = self._task_settings()

        def run(host):
            with self._frozen_time(14, 0):
                host._auto_task_tick()

        host, calls, _ = self._run(settings, True, run)
        self.assertIsNone(host._auto_task_active_id)
        self.assertEqual(calls, [])

    def test_task_waits_when_disconnected_and_writes_nothing(self):
        settings = self._task_settings()

        def run(host):
            with self._frozen_time(9, 0):
                host._auto_task_tick()

        host, calls, logs = self._run(settings, False, run)
        self.assertIsNone(host._auto_task_active_id)
        self.assertEqual(host._auto_task_waiting_id, "t1")
        self.assertEqual(calls, [])
        self.assertTrue(any("未连接" in m for m in logs))

    def test_period_end_restores_the_tasks_own_snapshot(self):
        """还原用任务自己的快照，且不碰「手动应用预设」的撤销槽。"""
        settings = self._task_settings(snapshot={"values": {"picture_mode": 10,
                                                            "picture_contrast": 44}})
        settings["preset_snapshot"] = {"source": "手动", "values": {"picture_mode": 14}}

        def run(host):
            host._auto_task_active_id = "t1"      # 假装时段内已经生效过
            with self._frozen_time(14, 0):        # 现在已出时段
                host._auto_task_tick()

        host, calls, logs = self._run(settings, True, run)
        self.assertIn(("put", "picture_mode", "10"), calls)
        self.assertIn(("put", "picture_contrast", "44"), calls)
        self.assertIsNone(host._auto_task_active_id)
        # 任务快照用掉即清空；手动撤销槽纹丝不动
        self.assertIsNone(settings["auto_tasks"][0]["snapshot"])
        self.assertEqual(settings["preset_snapshot"]["values"], {"picture_mode": 14})

    def test_period_end_without_snapshot_only_logs(self):
        settings = self._task_settings(snapshot=None)

        def run(host):
            host._auto_task_active_id = "t1"
            with self._frozen_time(14, 0):
                host._auto_task_tick()

        _, calls, logs = self._run(settings, True, run)
        self.assertEqual(calls, [])
        self.assertTrue(any("没有记录到可还原的快照" in m for m in logs))

    def test_repeated_ticks_do_not_rewrite_an_active_task(self):
        """同一时段内反复 tick 不该重复下发。"""
        settings = self._task_settings()

        def run(host):
            with self._frozen_time(9, 0):
                for _ in range(3):
                    host._auto_task_tick()

        _, calls, _ = self._run(settings, True, run)
        self.assertEqual(len([c for c in calls if c == ("put", "picture_mode", "9")]), 1)

    def test_missing_preset_is_logged_not_crashed(self):
        settings = {"presets": [],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": "gone", "snapshot": None}]}

        def run(host):
            with self._frozen_time(9, 0):
                host._auto_task_tick()

        host, calls, logs = self._run(settings, True, run)
        self.assertEqual(calls, [])
        self.assertTrue(any("已不存在" in m for m in logs))

    # ── 选项 / 新建（无编辑会话，改用「选项」管理大项）──

    def test_update_preset_options_filters_values_and_triggers_sync_when_active(self):
        settings = {
            "active_preset_id": "p1",
            "presets": [{
                "id": "p1",
                "name": "游戏",
                "values": {
                    "picture_mode": 10,
                    "picture_contrast": 60,
                    "freesync": 1,
                    "volume_music_speaker": 50,
                    "atmosphere_light_switcher_pm2": 2,
                },
            }],
        }
        noted = []

        def run(host):
            host._note_picture_change = lambda: noted.append(True)
            host.update_preset_options("p1", {"audio": False, "screen_light": False})

        self._run(settings, True, run)
        updated = settings["presets"][0]
        self.assertFalse(updated["options"]["audio"])
        self.assertFalse(updated["options"]["screen_light"])
        self.assertIn("picture_mode", updated["values"])
        self.assertIn("freesync", updated["values"])
        self.assertNotIn("volume_music_speaker", updated["values"])
        self.assertNotIn("atmosphere_light_switcher_pm2", updated["values"])
        self.assertEqual(noted, [True])

    def test_apply_preset_by_id_and_sync_respect_preset_options(self):
        settings = {
            "presets": [{
                "id": "p1",
                "name": "仅同步音频",
                "options": {"picture": False, "display_sync": False, "game_tools": False, "screen_light": False, "audio": True},
                "values": {"mitv.settings.db.sound.device.output_device": 8},
            }],
        }

        def run(host):
            host.apply_preset_by_id("p1")
            host._read_picture_values = lambda: {"picture_mode": 14, "mitv.settings.db.sound.device.output_device": 5}
            host._sync_active_preset()

        host, calls, logs = self._run(settings, True, run)
        self.assertFalse(any("缺" in m for m in logs))
        self.assertEqual(settings["active_preset_id"], "p1")
        self.assertFalse(any(c[0] == "put" and c[1] == "picture_mode" for c in calls))
        self.assertEqual(settings["presets"][0]["values"], {"mitv.settings.db.sound.device.output_device": 5})

    def test_create_preset_saves_current_values_and_activates_it(self):
        settings = {"presets": []}

        def run(host):
            host.create_preset_from_current("我的预设")

        self._run(settings, True, run)
        presets = settings["presets"]
        self.assertEqual(len(presets), 1)
        self.assertEqual(presets[0]["name"], "我的预设")
        self.assertEqual(presets[0]["values"], {"picture_mode": 14, "picture_contrast": 50})
        self.assertEqual(settings["active_preset_id"], presets[0]["id"])

    def test_create_preset_jumps_to_the_picture_page(self):
        settings = {"presets": []}

        def run(host):
            host.create_preset_from_current("新的")

        host, _, _ = self._run(settings, True, run)
        self.assertEqual(host.stack, [host.picture_page])

    def test_create_preset_keeps_duplicate_names_apart(self):
        settings = {"presets": [{"id": "p1", "name": "重名", "values": {}}]}

        def run(host):
            host.create_preset_from_current("重名")

        self._run(settings, True, run)
        self.assertEqual(settings["presets"][1]["name"], "重名 (2)")

    def test_create_preset_is_refused_when_disconnected(self):
        settings = {"presets": []}

        def run(host):
            host.create_preset_from_current("新的")

        self._run(settings, False, run)
        self.assertEqual(settings.get("presets", []), [])
        self.assertNotIn("active_preset_id", settings)




class ActivePresetAutoSaveTests(unittest.TestCase):
    """当前预设：改动自动写回、无预设时不动任何预设。"""

    def _host(self, settings, connected=True):
        from contextlib import nullcontext

        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        writes = []
        logs = []

        class FakeTimer:
            def __init__(self):
                self.start_count = 0

            def start(self):
                self.start_count += 1

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def put(self, key, value, check=False):
                writes.append(("put", key, value))

            def jni_set(self, key, value, upd=3, check=False):
                writes.append(("jni_set", key, value))

            def refresh_pq(self, check=False):
                pass

            def jni_set_color_gains(self, red, green, blue, check=False):
                pass

            def hdr_tone_mapping(self, value, upd=3, check=False):
                pass

            def check_and_heal_jar(self):
                pass

        class Host(DisplayFeaturesMixin):
            adb = FakeAdb()
            current_vals = {}
            adb_connected = connected
            _page_loaded = set()
            _page_loading = set()
            _cleanup_done = False
            _adb_busy_until = 0.0
            _preset_sync_timer = FakeTimer()

            def check_connection(self):
                return connected

            def _mark_adb_busy(self, seconds=2.0):
                pass

            def log(self, message):
                logs.append(message)

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                operation()
                if on_success:
                    on_success()

            def _read_picture_values(self):
                return {"picture_mode": 14, "picture_contrast": 88}

        return Host(), writes, logs

    def _patched(self, settings):
        from mimonitor_toolbox import display_features

        return (
            mock.patch.object(display_features, "load_settings",
                              side_effect=lambda: dict(settings)),
            mock.patch.object(display_features, "update_settings",
                              side_effect=settings.update),
            mock.patch.object(display_features, "QTimer"),
        )

    def test_change_schedules_a_sync_when_a_preset_is_active(self):
        settings = {"active_preset_id": "p1",
                    "presets": [{"id": "p1", "name": "A", "values": {}}]}
        load, save, timer = self._patched(settings)
        with load, save, timer:
            host, _, _ = self._host(settings)
            host._note_picture_change()
            self.assertEqual(host._preset_sync_timer.start_count, 1)

    def test_change_does_nothing_under_baseline(self):
        """无预设时改动就是改动本身，没有地方要存。"""
        settings = {"active_preset_id": None,
                    "presets": [{"id": "p1", "name": "A", "values": {}}]}
        load, save, timer = self._patched(settings)
        with load, save, timer:
            host, _, _ = self._host(settings)
            host._note_picture_change()
            self.assertEqual(host._preset_sync_timer.start_count, 0)

    def test_sync_writes_device_values_into_the_active_preset(self):
        settings = {"active_preset_id": "p1",
                    "presets": [
                        {"id": "p1", "name": "测试预设", "values": {"picture_mode": 9}},
                        {"id": "p2", "name": "别的", "values": {"picture_mode": 10}},
                    ]}
        load, save, timer = self._patched(settings)
        with load, save, timer:
            host, _, logs = self._host(settings)
            host._sync_active_preset()

        self.assertEqual(settings["presets"][0]["values"],
                         {"picture_mode": 14, "picture_contrast": 88})
        # 其他预设不受影响
        self.assertEqual(settings["presets"][1]["values"], {"picture_mode": 10})
        self.assertTrue(any("测试预设" in m for m in logs))

    def test_sync_is_skipped_when_disconnected(self):
        settings = {"active_preset_id": "p1",
                    "presets": [{"id": "p1", "name": "A", "values": {"picture_mode": 9}}]}
        load, save, timer = self._patched(settings)
        with load, save, timer:
            host, _, _ = self._host(settings, connected=False)
            host._sync_active_preset()
        self.assertEqual(settings["presets"][0]["values"], {"picture_mode": 9})

    def test_sync_without_change_does_not_write(self):
        settings = {"active_preset_id": "p1",
                    "presets": [{"id": "p1", "name": "A",
                                 "values": {"picture_mode": 14, "picture_contrast": 88}}]}
        load, save, timer = self._patched(settings)
        before = dict(settings)
        with load, save, timer:
            host, _, _ = self._host(settings)
            host._sync_active_preset()
        self.assertEqual(settings, before)


class ActivePresetTransitionTests(unittest.TestCase):
    """切换当前预设时的状态与快照语义。"""

    def _run(self, settings, fn, connected=True):
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        logs = []

        class Host(DisplayFeaturesMixin):
            adb = mock.Mock()
            current_vals = {}
            adb_connected = connected
            _page_loaded = set()

            def check_connection(self):
                return connected

            def _mark_adb_busy(self, seconds=2.0):
                pass

            def log(self, message):
                logs.append(message)

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                operation()
                if on_success:
                    on_success()

            def _apply_picture_values(self, values, label, before_apply=None,
                                      on_finished=None, quiet=False):
                self.applied = getattr(self, "applied", [])
                self.applied.append((label, dict(values)))
                if before_apply:
                    before_apply({"picture_mode": 14, "picture_contrast": 50})
                if on_finished:
                    on_finished(object())      # 非 None = 成功

            def _read_picture_values(self):
                return {"picture_mode": 14, "picture_contrast": 50}

            def _refresh_preset_views(self):
                pass

            preset_page = object()
            picture_page = object()

            def __init__(self):
                self.stack = []

                class FakeStack:
                    def setCurrentWidget(inner, page):
                        self.stack.append(page)

                class FakeBanner:
                    def __init__(inner):
                        inner.visible = False
                        inner.name = None

                    def set_preset_name(inner, name):
                        inner.name = name

                    def show_banner(inner):
                        inner.visible = True

                    def hide(inner):
                        inner.visible = False

                self.stackedWidget = FakeStack()
                self._preset_banner = FakeBanner()

        host = Host()
        with mock.patch.object(display_features, "load_settings",
                               side_effect=lambda: dict(settings)), \
                mock.patch.object(display_features, "update_settings",
                                  side_effect=settings.update):
            fn(host)
        return host, logs

    def test_applying_a_preset_marks_it_active(self):
        settings = {"active_preset_id": None,
                    "presets": [{"id": "p1", "name": "看电影", "values": {"picture_mode": 9}}]}

        def run(host):
            host.apply_preset_by_id("p1")

        self._run(settings, run)
        self.assertEqual(settings["active_preset_id"], "p1")

    def test_switching_from_baseline_records_the_baseline_values(self):
        settings = {"active_preset_id": None,
                    "presets": [{"id": "p1", "name": "看电影", "values": {"picture_mode": 9}}]}

        def run(host):
            host.apply_preset_by_id("p1")

        self._run(settings, run)
        self.assertEqual(settings["preset_snapshot"]["values"],
                         {"picture_mode": 14, "picture_contrast": 50})
        self.assertEqual(settings["preset_snapshot"]["source"], "看电影")

    def test_switching_between_presets_keeps_the_baseline_values(self):
        """预设之间互切不该覆盖「无预设」那份值。"""
        settings = {"active_preset_id": "p1",
                    "preset_snapshot": {"source": "更早的", "values": {"picture_mode": 9}},
                    "presets": [{"id": "p1", "name": "A", "values": {"picture_mode": 9}},
                                {"id": "p2", "name": "B", "values": {"picture_mode": 10}}]}

        def run(host):
            host.apply_preset_by_id("p2")

        self._run(settings, run)
        self.assertEqual(settings["preset_snapshot"]["source"], "更早的")
        self.assertEqual(settings["active_preset_id"], "p2")

    def test_going_back_to_baseline_clears_the_active_preset(self):
        settings = {"active_preset_id": "p1",
                    "preset_snapshot": {"source": "A", "values": {"picture_mode": 9}}}

        def run(host):
            host.restore_preset_snapshot()

        self._run(settings, run)
        self.assertIsNone(settings["active_preset_id"])

    def test_baseline_card_is_active_by_default(self):
        settings = {"active_preset_id": None}
        host, _ = self._run(settings, lambda host: None)
        self.assertIsNone(host.active_preset_id())

    def test_finishing_an_edit_returns_to_the_preset_page(self):
        settings = {"presets": []}

        def run(host):
            host._goto_preset_page()

        host, _ = self._run(settings, run)
        self.assertEqual(host.stack, [host.preset_page])


class CurrentPresetBannerTests(unittest.TestCase):
    """顶部指示条只表达状态：有预设在用就显示，无预设就收起。"""

    def _run(self, settings):
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        class FakeBanner:
            def __init__(self):
                self.visible = False
                self.name = None

            def set_preset_name(self, name):
                self.name = name

            def show_banner(self):
                self.visible = True

            def hide(self):
                self.visible = False

        class Host(DisplayFeaturesMixin):
            _preset_banner = FakeBanner()

        host = Host()
        with mock.patch.object(display_features, "load_settings",
                               side_effect=lambda: dict(settings)):
            host._update_preset_banner()
        return host._preset_banner

    def test_banner_hidden_under_baseline(self):
        banner = self._run({"active_preset_id": None,
                            "presets": [{"id": "p1", "name": "A", "values": {}}]})
        self.assertFalse(banner.visible)

    def test_banner_shows_the_active_preset_name(self):
        settings = {"active_preset_id": "p1",
                    "presets": [{"id": "p1", "name": "看电影", "values": {}}]}
        banner = self._run(settings)
        self.assertTrue(banner.visible)
        self.assertEqual(banner.name, "看电影")

    def test_banner_hidden_when_active_preset_is_gone(self):
        """预设被删掉后不该还挂着名字。"""
        settings = {"active_preset_id": "gone", "presets": []}
        banner = self._run(settings)
        self.assertFalse(banner.visible)

    def test_banner_has_no_buttons(self):
        """指示条不是编辑会话控件 —— 没有保存/取消。"""
        from mimonitor_toolbox.widgets import CurrentPresetBanner

        self.assertEqual(CurrentPresetBanner.__init__.__code__.co_argcount, 2)  # self, parent
        self.assertFalse(hasattr(CurrentPresetBanner, "save_requested"))
        self.assertFalse(hasattr(CurrentPresetBanner, "cancel_requested"))


class PresetSwitchingOverlayTests(unittest.TestCase):
    """切换预设时的全窗口遮罩；窗口在后台时改用悬浮窗提示。"""

    def _host(self, visible=True, minimized=False, connected=True, succeed=True):
        from contextlib import nullcontext

        from PyQt6.QtWidgets import QWidget

        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        class FakeAdb:
            """应用流程会 `with self.adb.transaction()`，所以 transaction 得是个
            上下文管理器 —— 光用 mock.Mock() 会在 with 那一行炸掉。"""

            def __init__(self):
                self.calls = []

            def transaction(self):
                return nullcontext()

            def __getattr__(self, name):
                def record(*args, **kwargs):
                    self.calls.append((name,) + args)
                return record

        class Host(DisplayFeaturesMixin, QWidget):
            def __init__(self):
                super().__init__()
                self.current_vals = {}
                self.adb_connected = connected
                self.adb = FakeAdb()
                self._page_loaded = set()
                self._adb_busy_until = 0.0
                self._local_dimming_memory_suppress_until = 0.0
                self.osd = mock.Mock()
                self._visible = visible
                self._minimized = minimized
                self._succeed = succeed

            def check_connection(self):
                return connected

            def _mark_adb_busy(self, seconds=2.0):
                pass

            def log(self, message):
                pass

            def isVisible(self):
                return self._visible

            def isMinimized(self):
                return self._minimized

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                operation()
                if self._succeed:
                    if on_success:
                        on_success()
                elif on_failure:
                    on_failure("故意失败")

            def _read_picture_values(self):
                return {"picture_mode": 14}

        return Host()

    @staticmethod
    def _visible_overlays(host):
        from PyQt6.QtWidgets import QWidget

        return [c for c in host.findChildren(QWidget, "_preset_overlay")
                if not c.isHidden()]

    def test_foreground_window_gets_the_overlay_not_the_hud(self):
        host = self._host(visible=True)
        host._show_preset_switching_overlay()
        self.assertEqual(len(self._visible_overlays(host)), 1)
        host.osd.show_hud.assert_not_called()

    def test_hidden_window_uses_the_hud_instead(self):
        """收进托盘时遮罩看不见，也没有挡住操作的意义。"""
        host = self._host(visible=False)
        host._show_preset_switching_overlay()
        self.assertEqual(self._visible_overlays(host), [])
        host.osd.show_hud.assert_called_once()

    def test_minimized_window_uses_the_hud(self):
        host = self._host(visible=True, minimized=True)
        host._show_preset_switching_overlay()
        self.assertEqual(self._visible_overlays(host), [])
        host.osd.show_hud.assert_called_once()

    def test_repeated_show_leaves_only_one_visible_overlay(self):
        host = self._host()
        host._show_preset_switching_overlay()
        host._show_preset_switching_overlay()
        self.assertEqual(len(self._visible_overlays(host)), 1)

    def test_hide_clears_the_overlay(self):
        host = self._host()
        host._show_preset_switching_overlay()
        host._hide_preset_switching_overlay()
        self.assertEqual(self._visible_overlays(host), [])

    def test_applying_a_preset_shows_then_clears_the_overlay(self):
        from mimonitor_toolbox import display_features

        host = self._host()
        with mock.patch.object(display_features, "QTimer"):
            host._apply_picture_values({"picture_mode": 9}, "应用预设")
        self.assertEqual(self._visible_overlays(host), [],
                         "应用结束后遮罩必须收掉，否则会把整个界面挡住")

    def test_overlay_is_cleared_even_when_the_apply_fails(self):
        from mimonitor_toolbox import display_features

        host = self._host(succeed=False)
        with mock.patch.object(display_features, "QTimer"):
            host._apply_picture_values({"picture_mode": 9}, "应用预设")
        self.assertEqual(self._visible_overlays(host), [],
                         "失败路径同样要收遮罩")

    def test_overlay_lives_on_the_window_not_a_page(self):
        """挂在主窗口上，切预设时不管停在哪一页都挡得住。"""
        host = self._host()
        host._show_preset_switching_overlay()
        overlay = self._visible_overlays(host)[0]
        self.assertIs(overlay.parentWidget(), host)


class MemorySuspensionUnderPresetTests(unittest.TestCase):
    """有预设生效时两个「记忆」暂停。

    预设和记忆管的是同一件事（为另一种状态备好一套值）。同时生效会打架：
    HDR 记忆会去写精密控光盖掉预设，而那个写入不会被预设吸收，结果是
    设备值、画面页值、预设值三方不一致。
    """

    def _host(self, active_id):
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        class Host(DisplayFeaturesMixin):
            def __init__(self):
                self.current_vals = {}
                self.saved = []
                self.applied = []
                # 固定 HDR 状态来源：本类只测"记忆要不要落桶"的逻辑，不该去碰真实
                # DXGI/显示器枚举 —— 那样会在没接显示器的机器上（含 Linux）失败，
                # 而 _query_windows_hdr_state 现在依赖已解析好的目标屏，裸 mixin 没有。
                self._hdr_last_state = False

            def _hdr_memory_enabled(self):
                return True

            def log(self, message):
                pass

            def _save_local_dimming_memory(self, memory):
                self.saved.append(dict(memory))

            def _get_local_dimming_memory(self):
                return {"sdr": 3, "hdr": 3}

            def _set_local_dimming_for_memory(self, value, message):
                self.applied.append(value)

            def _hdr_memory_enabled_flag(self):
                return True

            def _local_dimming_memory_bucket(self):
                return "sdr"

        return Host()

    def _run(self, active_id, fn):
        from mimonitor_toolbox import display_features

        settings = {"active_preset_id": active_id}
        host = self._host(active_id)
        with mock.patch.object(display_features, "load_settings",
                               side_effect=lambda: dict(settings)):
            fn(host)
        return host

    def test_flag_follows_the_active_preset(self):
        # 断言要在 patch 作用域内求值，否则读的是真实配置
        flags = []
        self._run("p1", lambda h: flags.append(h.memories_suspended_by_preset()))
        self._run(None, lambda h: flags.append(h.memories_suspended_by_preset()))
        self.assertEqual(flags, [True, False])

    def test_local_dimming_is_not_remembered_under_a_preset(self):
        """否则预设的值会被记忆当成"用户偏好"收走，停用预设后又被还原回来。"""
        def record(host):
            host._remember_local_dimming_value(2)

        host = self._run("p1", record)
        self.assertEqual(host.saved, [], "预设生效时不该写记忆桶")

        host = self._run(None, record)
        self.assertTrue(host.saved, "无预设时照旧记录")

    def test_hdr_memory_does_not_write_under_a_preset(self):
        def apply(host):
            host._apply_hdr_memory_for_current_state()

        host = self._run("p1", apply)
        self.assertEqual(host.applied, [], "预设生效时不该去写精密控光")

    def test_freesync_memory_is_bypassed_under_a_preset(self):
        """FreeSync 记忆和预设都管 FreeSync + 画面模式，规则还不一样。"""
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        settings = {"active_preset_id": "p1", "freesync_mode_memory_enabled": True}
        remembered = []

        class FakeAdb:
            def transaction(self):
                from contextlib import nullcontext
                return nullcontext()

            def jni_set(self, *args, **kwargs):
                pass

            def put(self, *args, **kwargs):
                pass

            def refresh_pq(self, *args, **kwargs):
                pass

        class Host(DisplayFeaturesMixin):
            current_vals = {"freesync": 0, "picture_mode": 9}
            adb_connected = True
            adb = FakeAdb()
            state_buttons = {}

            def _refresh_pages(self, pages, delay_ms=0, force=False):
                pass

            def _freesync_mode_memory_enabled(self):
                return True

            def memories_suspended_by_preset(self):
                return True

            def _remember_freesync_previous_mode(self):
                remembered.append(True)

            def _get_freesync_memory_mode(self):
                return 9

            def check_connection(self):
                return True

            def _mark_adb_busy(self, seconds=2.0):
                pass

            def _take_control_previous(self, key):
                return None

            def log(self, message):
                pass

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                operation()
                if on_success:
                    on_success()

            def _note_picture_change(self):
                pass

            def _get_input_source(self, check=False):
                return "23"

        host = Host()
        with mock.patch.object(display_features, "load_settings",
                               side_effect=lambda: dict(settings)):
            host._fsync(True)
        self.assertEqual(remembered, [], "预设生效时不该记忆 FreeSync 前的模式")


class FreeSyncAutoSaveTests(unittest.TestCase):
    """FreeSync 已在预设范围内，手动开关要写回当前预设。"""

    def test_freesync_toggle_notes_a_picture_change(self):
        import inspect

        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        source = inspect.getsource(DisplayFeaturesMixin._fsync)
        self.assertIn("_note_picture_change()", source,
                      "FreeSync 进了预设范围，手动开关必须触发自动保存")


class SoundOutputTests(unittest.TestCase):
    """声音输出切换、回滚、托盘目录、热键循环与预设回读同步。"""

    def _make_host(self, current=0, should_fail=False):
        from contextlib import nullcontext

        from mimonitor_toolbox import core, display_features

        calls = []
        logs = []
        highlights = []
        noted = []
        polls = []
        refreshed = []

        class FakeLabel:
            def __init__(self):
                self.text = None

            def setText(self, text):
                self.text = text

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def check_and_heal_jar(self):
                calls.append(("heal",))

            def set_sound_output(self, device_id, check=False):
                calls.append(("set_sound_output", device_id, check))
                if should_fail:
                    raise RuntimeError("adb failed")

            def put(self, key, value, check=False):
                calls.append(("put", key, value, check))

            def get(self, key, check=False):
                calls.append(("get", key, check))
                return "3"

        class Host(display_features.DisplayFeaturesMixin):
            adb_connected = True
            adb = FakeAdb()
            current_vals = (
                {core.SOUND_OUTPUT_SETTINGS_KEY: current}
                if current is not None
                else {}
            )
            state_buttons = {
                core.SOUND_OUTPUT_SETTINGS_KEY: {0: "btn0", 8: "btn8", 5: "btn5"}
            }
            sound_output_label = FakeLabel()
            _page_loaded = {"picturePage", "gamePage", "sourcePage"}
            _adb_busy_until = 0.0
            _cleanup_done = False
            _control_rollback_values = {}

            def check_connection(self):
                return self.adb_connected

            def _mark_adb_busy(self, seconds=2.0):
                pass

            def _adb_channel_busy(self):
                return False

            def log(self, message):
                logs.append(message)

            def _optimistic_highlight(self, key, val):
                highlights.append((key, val))

            def _note_picture_change(self):
                noted.append(True)

            def _start_source_polling(self):
                polls.append(True)

            def _refresh_pages(self, pages, delay_ms=0, force=False):
                refreshed.append((tuple(pages), force))

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                try:
                    operation()
                except Exception:
                    if on_failure:
                        on_failure()
                    return
                if on_success:
                    on_success()

        return Host(), calls, logs, highlights, noted, polls, refreshed

    def test_set_sound_output_writes_mtk_and_setting_and_syncs_state(self):
        from mimonitor_toolbox import core

        host, calls, logs, highlights, noted, polls, _ = self._make_host(current=0)
        host.set_sound_output(8, "HDMI(eARC)")

        self.assertEqual(
            calls,
            [
                ("heal",),
                ("set_sound_output", 8, True),
                ("put", core.SOUND_OUTPUT_SETTINGS_KEY, "8", True),
            ],
        )
        self.assertEqual(host.current_vals[core.SOUND_OUTPUT_SETTINGS_KEY], 8)
        self.assertEqual(host.sound_output_label.text, "HDMI(eARC)")
        self.assertIn((core.SOUND_OUTPUT_SETTINGS_KEY, 8), highlights)
        self.assertEqual(noted, [True])
        self.assertEqual(polls, [True])
        self.assertIn("声音输出: HDMI(eARC)", logs)

    def test_set_sound_output_failure_rolls_back_previous_value(self):
        from mimonitor_toolbox import core

        host, _, _, highlights, noted, _, _ = self._make_host(
            current=0, should_fail=True
        )
        host.set_sound_output(5, "耳机孔")

        self.assertEqual(host.current_vals[core.SOUND_OUTPUT_SETTINGS_KEY], 0)
        self.assertEqual(host.sound_output_label.text, "本机扬声器")
        self.assertEqual(highlights[-1], (core.SOUND_OUTPUT_SETTINGS_KEY, 0))
        self.assertEqual(noted, [])

    def test_get_sound_output_normalizes_arc_3_to_8(self):
        host, calls, _, _, _, _, _ = self._make_host()
        self.assertEqual(host._get_sound_output(check=True), 8)
        self.assertEqual(
            calls[-1],
            ("get", "mitv.settings.db.sound.device.output_device", True),
        )

    def test_tray_catalog_value_and_apply_option(self):
        from mimonitor_toolbox import core

        host, calls, _, _, _, _, _ = self._make_host(current="3")
        entry = host.tray_entry("sound_output")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["kind"], "options")
        self.assertEqual(entry["options"], core.SOUND_OUTPUT_OPTIONS)
        self.assertEqual(host.tray_entry_value("sound_output"), 8)

        host.tray_apply_option("sound_output", 5)
        self.assertEqual(host.current_vals[core.SOUND_OUTPUT_SETTINGS_KEY], 5)
        self.assertIn(("set_sound_output", 5, True), calls)

    def test_hotkey_cycle_stages_next_sound_output(self):
        from mimonitor_toolbox import core

        host, _, _, _, _, _, _ = self._make_host(current=0)
        host._cycle_hotkey_pending = {}
        staged = []
        host._stage_cycle_hotkey_action = (
            lambda action, sk, options, label, exec_fn: staged.append(
                (action, sk, list(options), label)
            )
        )
        host.trigger_hotkey_action("sound_output_cycle")
        self.assertEqual(
            staged,
            [(
                "sound_output_cycle",
                core.SOUND_OUTPUT_SETTINGS_KEY,
                core.SOUND_OUTPUT_OPTIONS,
                "声音输出",
            )],
        )

    def test_preset_verify_refreshes_source_page(self):
        host, _, _, _, _, _, refreshed = self._make_host()
        host._verify_after_preset_apply()
        self.assertEqual(
            refreshed,
            [(("picturePage", "gamePage", "sourcePage", "lightPage"), True)],
        )

    def test_preset_snapshot_and_restore_round_trips_sound_output(self):
        from contextlib import nullcontext

        from mimonitor_toolbox import core, display_features

        calls = []
        settings = {
            "active_preset_id": None,
            "preset_snapshot": None,
            "presets": [{
                "id": "p_earc",
                "name": "观影",
                "values": {
                    "picture_mode": 9,
                    core.SOUND_OUTPUT_SETTINGS_KEY: 8,
                },
            }],
        }

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def check_and_heal_jar(self):
                calls.append(("heal",))

            def set_sound_output(self, device_id, check=False):
                calls.append(("set_sound_output", device_id))

            def put(self, key, value, check=False):
                calls.append(("put", key, value))

            def jni_set(self, key, val, upd=3, check=False):
                calls.append(("jni_set", key, val))

            def refresh_pq(self, check=False):
                calls.append(("refresh_pq",))

            def get(self, key, check=False):
                return "23"

        class Host(display_features.DisplayFeaturesMixin):
            adb_connected = True
            adb = FakeAdb()
            current_vals = {}
            state_buttons = {}
            _page_loaded = {"picturePage", "gamePage", "sourcePage"}
            _adb_busy_until = 0.0
            _cleanup_done = False

            def check_connection(self):
                return True

            def _mark_adb_busy(self, seconds=2.0):
                pass

            def log(self, message):
                pass

            def _read_picture_values(self):
                return {
                    "picture_mode": 14,
                    core.SOUND_OUTPUT_SETTINGS_KEY: 0,
                }

            def _show_preset_switching_overlay(self):
                pass

            def _hide_preset_switching_overlay(self):
                pass

            def _refresh_preset_views(self):
                pass

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                operation()
                if on_success:
                    on_success()

        host = Host()
        with mock.patch.object(
            display_features, "load_settings", side_effect=lambda: dict(settings)
        ), mock.patch.object(
            display_features, "update_settings", side_effect=settings.update
        ), mock.patch.object(display_features, "QTimer"):
            host.apply_preset_by_id("p_earc")
            self.assertEqual(
                settings["preset_snapshot"]["values"][core.SOUND_OUTPUT_SETTINGS_KEY],
                0,
            )
            self.assertIn(("set_sound_output", 8), calls)
            self.assertIn(("put", core.SOUND_OUTPUT_SETTINGS_KEY, "8"), calls)
            self.assertNotIn("sourcePage", host._page_loaded)

            calls.clear()
            host.restore_preset_snapshot()
            self.assertIn(("set_sound_output", 0), calls)
            self.assertIn(("put", core.SOUND_OUTPUT_SETTINGS_KEY, "0"), calls)


class MonitorVolumeFeatureTests(unittest.TestCase):
    def _make_host(self, volume=15, muted=0, should_fail=False):
        from mimonitor_toolbox import display_features

        calls = []
        logs = []
        emitted = []
        hud = []

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def media_volume(self, check=False):
                calls.append(("media_volume", check))
                return volume

            def media_muted(self, check=False):
                calls.append(("media_muted", check))
                return bool(muted)

            def set_media_volume(self, value, unmute_first=False, show_ui=False, check=False):
                calls.append(("set_media_volume", value, unmute_first, show_ui, check))
                if should_fail:
                    raise RuntimeError("adb failed")

            def set_media_muted(self, target_muted, show_ui=False, check=False):
                calls.append(("set_media_muted", target_muted, show_ui, check))
                if should_fail:
                    raise RuntimeError("adb failed")

        timer_events = []

        class Host(display_features.DisplayFeaturesMixin):
            adb_connected = True
            adb = FakeAdb()
            current_vals = {
                "volume_music_speaker": volume,
                "media_muted": muted,
            }
            sliders = {}
            osd = SimpleNamespace(
                show_hud=lambda title, value, countdown=None: hud.append((title, value, countdown)),
                end_countdown=lambda: None,
            )
            _adjust_hotkey_pending = {}
            _adjust_hotkey_timers = {
                "volume": SimpleNamespace(
                    stop=lambda: timer_events.append("stop"),
                    setInterval=lambda ms: timer_events.append(("interval", ms)),
                    start=lambda: timer_events.append("start"),
                )
            }
            Host_timer_events = timer_events
            _adjust_hotkey_resolving = set()
            _control_rollback_values = {}
            values_signal = SimpleNamespace(emit=lambda d: emitted.append(dict(d)))

            def check_connection(self):
                return self.adb_connected

            def _mark_adb_busy(self, seconds=2.0):
                pass

            def log(self, msg):
                logs.append(msg)

            def effective_hotkey_delay(self):
                return 0.0

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                try:
                    operation()
                except Exception:
                    if on_failure:
                        on_failure()
                    return
                if on_success:
                    on_success()

        return Host(), calls, logs, emitted, hud

    def test_tray_volume_entry_and_set_value(self):
        host, calls, _, emitted, _ = self._make_host(volume=15, muted=1)
        entry = host.tray_entry("volume")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["kind"], "stepper")
        self.assertEqual((entry["min"], entry["max"], entry["step"]), (0, 100, 5))
        self.assertEqual(host.tray_entry_value("volume"), 15)

        host.tray_set_value("volume", 42)
        # 即使关闭快捷键倒计时，托盘滑块仍按 300ms 去抖等待提交
        self.assertIn(("interval", 300), host.Host_timer_events)
        self.assertIn("start", host.Host_timer_events)
        self.assertEqual(calls, [])

        host._commit_pending_adjustment("volume")
        self.assertEqual(host.current_vals["volume_music_speaker"], 42)
        self.assertEqual(host.current_vals["media_muted"], 0)
        self.assertIn(("set_media_volume", 42, True, False, True), calls)
        self.assertEqual(emitted[-1], {"volume_music_speaker": 42, "media_muted": 0})

    def test_set_media_volume_failure_rolls_back(self):
        host, _, _, emitted, _ = self._make_host(volume=20, muted=1, should_fail=True)
        host.set_media_volume(55)
        self.assertEqual(host.current_vals["volume_music_speaker"], 20)
        self.assertEqual(host.current_vals["media_muted"], 1)
        self.assertEqual(emitted[-1], {"volume_music_speaker": 20, "media_muted": 1})

    def test_volume_mute_hotkey_toggles_and_queries_volume(self):
        host, calls, _, emitted, hud = self._make_host(volume=24, muted=0)
        host.trigger_hotkey_action("volume_mute_toggle")
        self.assertIn(("set_media_muted", True, False, True), calls)
        self.assertEqual(host.current_vals["media_muted"], 1)
        self.assertEqual(emitted[-1], {"media_muted": 1})
        self.assertEqual(hud[-1][:2], ("静音", "开"))
        self.assertEqual(host.query_setting_or_jni("volume_music_speaker", check=True), 24)

    def test_show_volume_osd_setting_passes_show_ui_flag(self):
        from mimonitor_toolbox import display_features

        host, calls, _, _, _ = self._make_host(volume=15, muted=0)
        with mock.patch.object(
            display_features,
            "load_settings",
            return_value={"show_volume_osd": True},
        ):
            host.set_media_volume(28)
            host.toggle_media_mute(muted=True)
        self.assertIn(("set_media_volume", 28, False, True, True), calls)
        self.assertIn(("set_media_muted", True, True, True), calls)


class MemcFeatureTests(unittest.TestCase):
    def _make_host(
        self,
        mode=9,
        source="23",
        jni=None,
        settings=None,
        current_memc=0,
        host_refresh_hz=None,
    ):
        from mimonitor_toolbox import core, display_features

        calls = []
        logs = []
        messages = []
        highlights = []
        noted = []
        refreshed = []

        live_settings = {
            "picture_mode": str(mode),
            "mitv.tvplayer.hdmi.last.source": str(source),
        }
        if settings:
            live_settings.update({k: str(v) for k, v in settings.items()})
        live_jni = dict(jni or {})
        live_jni.setdefault("g_video__picture_mode", mode)

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def shell(self, cmd, check=False):
                calls.append(("shell", cmd))
                return "\n".join(f"{k}={v}" for k, v in live_settings.items())

            def get(self, key, check=False):
                calls.append(("get", key, check))
                return live_settings.get(key, "null")

            def jni_batch_get(self, keys, check=False):
                calls.append(("jni_batch_get", tuple(keys)))
                return {k: live_jni[k] for k in keys if k in live_jni}

            def jni_get(self, key, check=False):
                calls.append(("jni_get", key, check))
                return live_jni.get(key, 0)

            def jni_set(self, key, val, upd=3, check=False):
                calls.append(("jni_set", key, val, upd, check))

            def put(self, key, val, check=False):
                calls.append(("put", key, val, check))

            def refresh_pq(self, check=False):
                calls.append(("refresh_pq", check))

        class Host(display_features.DisplayFeaturesMixin):
            adb_connected = True
            adb = FakeAdb()
            current_vals = {
                "picture_mode": mode,
                core.MEMC_SETTINGS_KEY: current_memc,
            }
            state_buttons = {}
            _control_rollback_values = {}
            _picture_mode_switch_seq = 0
            message_signal = SimpleNamespace(
                emit=lambda mtype, title, body: messages.append((mtype, title, body))
            )

            def check_connection(self):
                return self.adb_connected

            def _mark_adb_busy(self, seconds=2.0):
                pass

            def log(self, msg):
                logs.append(msg)

            def _note_picture_change(self):
                noted.append(True)

            def _highlight_mode(self, value):
                highlights.append(("picture_mode", value))

            def _optimistic_highlight(self, key, value):
                highlights.append((key, value))

            def _refresh_pages(self, pages, delay_ms=0, force=False):
                refreshed.append((tuple(pages), force))

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                try:
                    operation()
                except Exception:
                    if on_failure:
                        on_failure()
                    return
                if on_success:
                    on_success()

            def _read_memc_host_refresh_rate(self):
                return host_refresh_hz

        return Host(), calls, logs, messages, highlights, noted, refreshed

    def test_case_a_disallowed_modes_intercept_write_and_report_conditions(self):
        from mimonitor_toolbox import core

        for bad_mode in (14, 18, 69):
            host, calls, _, messages, highlights, noted, _ = self._make_host(
                mode=bad_mode, current_memc=0
            )
            host._set_memc(2, "中")
            self.assertFalse(any(c[0] in ("jni_set", "put") for c in calls), f"mode={bad_mode} 不应写入")
            self.assertEqual(host.current_vals[core.MEMC_SETTINGS_KEY], 0)
            self.assertEqual(highlights[-1], (core.MEMC_SETTINGS_KEY, 0))
            self.assertEqual(noted, [])
            self.assertEqual(len(messages), 1)
            mtype, title, body = messages[0]
            self.assertEqual(mtype, "warn")
            self.assertEqual(title, "当前画面模式不支持开启运动补偿")
            self.assertIn("✗ 画面模式支持", body)
            self.assertIn("ℹ 输入信号刷新率 ≤ 120Hz", body)

    def test_case_b_supported_mode_with_freesync_or_320_or_dlg_still_writes_and_warns(self):
        from mimonitor_toolbox import core

        # 电影模式 + HDMI FreeSync 开
        host, calls, _, messages, highlights, noted, _ = self._make_host(
            mode=9, source="23", jni={"g_video__freesync_switch": 3}
        )
        host._set_memc(2, "中")
        self.assertIn(("jni_set", core.MEMC_JNI_KEY, 2, 3, True), calls)
        self.assertIn(("put", core.MEMC_SETTINGS_KEY, "2", True), calls)
        self.assertIn(("put", core.MEMC_SAVE_KEY, "2", True), calls)
        self.assertEqual(host.current_vals[core.MEMC_SETTINGS_KEY], 2)
        self.assertEqual(noted, [True])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0][0], "info")
        self.assertIn("已保存「中」档位", messages[0][1])
        self.assertIn("✗ FreeSync 已关闭", messages[0][2])

        # 游戏模式 + DLG 开
        host2, calls2, _, messages2, _, _, _ = self._make_host(
            mode=10, source="23", jni={"g_video__dlg_vrr_switch": 1}
        )
        host2._set_memc(3, "高")
        self.assertIn(("jni_set", core.MEMC_GAME_JNI_KEY, 3, 3, True), calls2)
        self.assertIn(("put", core.MEMC_GAME_SETTINGS_KEY, "3", True), calls2)
        self.assertIn(("put", core.MEMC_GAME_PREF_KEY, "3", True), calls2)
        self.assertIn("DLG 高帧率已开启", messages2[0][2])

        # 模式 5 (DV IQ) + 内容自适应开
        host3, calls3, _, messages3, _, _, _ = self._make_host(
            mode=5, source="23", jni={"g_video__vid_content_adaptive": 1}
        )
        host3._set_memc(1, "低")
        self.assertIn(("jni_set", core.MEMC_JNI_KEY, 1, 3, True), calls3)
        self.assertIn("✗ 内容自适应已关闭", messages3[0][2])

    def test_hdmi_does_not_read_dp_adaptive_sync_and_case_c_has_no_popup(self):
        from mimonitor_toolbox import core

        # HDMI1 下即使 dp_adaptive_sync=1，也应判定为 Case C 且不弹窗
        host, calls, _, messages, _, noted, _ = self._make_host(
            mode=9,
            source="23",
            jni={
                "g_video__freesync_switch": 0,
                "g_video__dp_adaptive_sync": 1,
                "g_fusion_picture__hdmi_edid_version": 1,
                "g_video__dlg_vrr_switch": 0,
            },
        )
        host._set_memc(2, "中")
        self.assertIn(("jni_set", core.MEMC_JNI_KEY, 2, 3, True), calls)
        self.assertEqual(messages, [])
        self.assertEqual(noted, [True])

        # DP 源下 dp_adaptive_sync=1 必须判定为未满足
        host_dp, _, _, messages_dp, _, _, _ = self._make_host(
            mode=9,
            source="29",
            jni={"g_video__dp_adaptive_sync": 1, "g_fusion_picture__dp_edid_version": 2},
        )
        host_dp._set_memc(2, "中")
        self.assertEqual(len(messages_dp), 1)
        self.assertIn("✗ FreeSync 已关闭", messages_dp[0][2])

        # 刷新率检查在清单中始终为 info 提示态，不伪造 ✓
        report = core.evaluate_memc_conditions({"picture_mode": 9, "mitv.tvplayer.hdmi.last.source": 23}, {})
        self.assertEqual(report.case, "C")
        rr = next(item for item in report.items if item.key == "refresh_rate")
        self.assertEqual(rr.status, "info")

    def test_setting_memc_off_skips_condition_check_even_in_standard_mode(self):
        from mimonitor_toolbox import core

        host, calls, _, messages, _, _, _ = self._make_host(mode=14, current_memc=2)
        host._set_memc(0, "关")
        self.assertIn(("jni_set", core.MEMC_JNI_KEY, 0, 3, True), calls)
        self.assertIn(("put", core.MEMC_SETTINGS_KEY, "0", True), calls)
        self.assertEqual(messages, [])

    def test_switch_to_cinema_and_enable_memc_writes_mode_9_then_memc(self):
        from mimonitor_toolbox import core, display_features

        host, calls, _, _, highlights, noted, refreshed = self._make_host(mode=14, current_memc=0)
        with mock.patch.object(display_features.time, "sleep"):
            host._switch_to_cinema_and_enable_memc(2)
        mode_idx = calls.index(("put", "picture_mode", "9", True))
        memc_idx = calls.index(("jni_set", core.MEMC_JNI_KEY, 2, 3, True))
        self.assertLess(mode_idx, memc_idx)
        self.assertEqual(host.current_vals["picture_mode"], 9)
        self.assertEqual(host.current_vals[core.MEMC_SETTINGS_KEY], 2)
        self.assertEqual(noted, [True])
        self.assertIn(("picture_mode", 9), highlights)
        self.assertIn((core.MEMC_SETTINGS_KEY, 2), highlights)
        self.assertEqual(refreshed, [(("picturePage",), True)])

    def test_tray_hotkey_and_query_support_memc(self):
        from mimonitor_toolbox import core

        host, calls, _, _, _, _, _ = self._make_host(
            mode=64, jni={core.MEMC_GAME_JNI_KEY: 2}, current_memc=1
        )
        entry = host.tray_entry("memc")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["label"], "运动补偿")
        self.assertEqual(entry["options"], core.MEMC_OPTIONS)
        self.assertEqual(host.tray_entry_value("memc"), 1)

        self.assertEqual(host.query_setting_or_jni(core.MEMC_SETTINGS_KEY, check=True), 2)
        self.assertIn(("jni_get", core.MEMC_GAME_JNI_KEY, True), calls)

        host.tray_apply_option("memc", 3)
        self.assertIn(("jni_set", core.MEMC_GAME_JNI_KEY, 3, 3, True), calls)

        staged = []
        host._stage_cycle_hotkey_action = (
            lambda action, sk, options, label, exec_fn: staged.append(
                (action, sk, list(options), label)
            )
        )
        host.trigger_hotkey_action("memc_cycle")
        self.assertEqual(
            staged,
            [("memc_cycle", core.MEMC_SETTINGS_KEY, core.MEMC_OPTIONS, "运动补偿")],
        )

    def test_windows_refresh_rate_check_saves_and_warns_above_120hz_with_disclaimer(self):
        from mimonitor_toolbox import core, display_features

        # 160Hz (>120Hz) 触发 Case B：已保存档位，并提示仅供参考/可能不准
        host_high, calls_high, _, messages_high, _, noted_high, _ = self._make_host(
            mode=9,
            source="23",
            jni={"g_video__freesync_switch": 0, "g_fusion_picture__hdmi_edid_version": 1, "g_video__dlg_vrr_switch": 0},
            host_refresh_hz=160,
        )
        host_high._set_memc(2, "中")
        self.assertIn(("jni_set", core.MEMC_JNI_KEY, 2, 3, True), calls_high)
        self.assertEqual(noted_high, [True])
        self.assertEqual(len(messages_high), 1)
        self.assertEqual(messages_high[0][0], "info")
        self.assertIn("已保存「中」档位", messages_high[0][1])
        self.assertIn("✗ 输入信号刷新率 ≤ 120Hz", messages_high[0][2])
        self.assertIn("160Hz", messages_high[0][2])
        self.assertIn("可能不准", messages_high[0][2])

        # 120Hz (<=120Hz) 且其它条件满足时直接 Case C 无弹窗，Case A 清单内含 ✓ 与仅供参考说明
        host_ok, calls_ok, _, messages_ok, _, _, _ = self._make_host(
            mode=9,
            source="23",
            jni={"g_video__freesync_switch": 0, "g_fusion_picture__hdmi_edid_version": 1, "g_video__dlg_vrr_switch": 0},
            host_refresh_hz=120,
        )
        host_ok._set_memc(3, "高")
        self.assertIn(("jni_set", core.MEMC_JNI_KEY, 3, 3, True), calls_ok)
        self.assertEqual(messages_ok, [])

        report_ok = core.evaluate_memc_conditions(
            {"picture_mode": 9, "mitv.tvplayer.hdmi.last.source": 23},
            {},
            host_refresh_hz=120,
        )
        rr = next(item for item in report_ok.items if item.key == "refresh_rate")
        self.assertEqual(rr.status, "ok")
        self.assertIn("120Hz", rr.detail)
        self.assertIn("仅供参考", rr.detail)

        # 验证 Mixin 真实方法会委托 query_target_display_refresh_rate 并保留目标屏参数
        plain_host = display_features.DisplayFeaturesMixin()
        plain_host._hdr_target_device_name = r"\\.\DISPLAY1"
        with mock.patch.object(display_features, "load_settings", return_value={"hdr_target_display_id": "ID1"}), \
                mock.patch.object(display_features, "query_target_display_refresh_rate", return_value=144) as query_rr:
            self.assertEqual(plain_host._read_memc_host_refresh_rate(), 144)
        query_rr.assert_called_once_with(configured_id="ID1", target_device_name=r"\\.\DISPLAY1")


class Hdmi320AndFreeSyncTests(unittest.TestCase):
    """HDMI 下 320Hz 与 FreeSync 共享 g_fusion_picture__hdmi_edid_version 四态枚举（1/3/5/6）。"""

    def _make_host(self, edid=1, fs_switch=0, source="23"):
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        state = {
            "g_fusion_picture__hdmi_edid_version": edid,
            "g_video__freesync_switch": fs_switch,
        }
        writes = []

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def jni_batch_get_or_single(self, keys, check=False):
                return {k: state[k] for k in keys if k in state}

            def jni_get(self, key, check=False):
                return state.get(key)

            def get(self, key, check=False):
                if key == "mitv.tvplayer.hdmi.last.source":
                    return source
                return ""

            def jni_set(self, key, val, upd=3, check=False):
                writes.append((key, val))
                state[key] = val

            def put(self, key, val, check=False):
                writes.append(("put", key, val))

            def refresh_pq(self, check=False):
                writes.append(("refresh_pq",))

        class Host(DisplayFeaturesMixin):
            adb = FakeAdb()
            adb_connected = True
            current_vals = {"picture_mode": 10}
            state_buttons = {}

            def check_connection(self):
                return True

            def _mark_adb_busy(self, _s=2.0):
                pass

            def _take_control_previous(self, _k):
                return None

            def _freesync_mode_memory_enabled(self):
                return False

            def memories_suspended_by_preset(self):
                return False

            def _get_input_source(self, check=False):
                return source

            def _optimistic_highlight(self, _k, _v):
                pass

            def _note_picture_change(self):
                pass

            def _refresh_pages(self, *_args, **_kwargs):
                pass

            def log(self, _msg):
                pass

            def _run_adb_action(self, _label, op, success, _failure):
                op()
                success()

        return Host(), writes, state

    def test_320_preserves_freesync_and_fixes_stale_switch(self):
        # FreeSync 开 + 320Hz 关 (edid=3, fs=3) -> 开 320Hz 应写 5，再关应回 3
        host, writes, state = self._make_host(edid=3, fs_switch=3)
        host._320(True)
        self.assertEqual(state["g_fusion_picture__hdmi_edid_version"], 5)
        self.assertEqual(state["g_video__freesync_switch"], 3)
        host._320(False)
        self.assertEqual(state["g_fusion_picture__hdmi_edid_version"], 3)

        # OSD 留下 edid=6 + freesync_switch=2/3（FS 实际关）-> 关 320Hz 应清理 freesync_switch=0 且回 1
        host2, writes2, state2 = self._make_host(edid=6, fs_switch=2)
        host2._320(False)
        self.assertEqual(state2["g_video__freesync_switch"], 0)
        self.assertEqual(state2["g_fusion_picture__hdmi_edid_version"], 1)
        self.assertLess(
            writes2.index(("g_video__freesync_switch", 0)),
            writes2.index(("g_fusion_picture__hdmi_edid_version", 1)),
        )

    def test_fsync_writes_switch_then_coupled_hdmi_edid(self):
        # 320Hz 关时开 FS -> 写 fs=3 再写 edid=3（不能跳成 5）
        host, writes, state = self._make_host(edid=1, fs_switch=0)
        host._fsync(True)
        self.assertEqual(state["g_video__freesync_switch"], 3)
        self.assertEqual(state["g_fusion_picture__hdmi_edid_version"], 3)
        self.assertLess(
            writes.index(("g_video__freesync_switch", 3)),
            writes.index(("g_fusion_picture__hdmi_edid_version", 3)),
        )

        # 320Hz 开时开关 FS -> 在 6 与 5 之间切换并保持 320Hz
        host2, writes2, state2 = self._make_host(edid=6, fs_switch=0)
        host2._fsync(True)
        self.assertEqual(state2["g_fusion_picture__hdmi_edid_version"], 5)
        writes2.clear()
        host2._fsync(False)
        self.assertEqual(state2["g_video__freesync_switch"], 0)
        self.assertEqual(state2["g_fusion_picture__hdmi_edid_version"], 6)
        self.assertLess(
            writes2.index(("g_video__freesync_switch", 0)),
            writes2.index(("g_fusion_picture__hdmi_edid_version", 6)),
        )

    def test_query_setting_or_jni_uses_hdmi_edid_over_stale_switch(self):
        host_off, _, _ = self._make_host(edid=6, fs_switch=3)
        self.assertEqual(host_off.query_setting_or_jni("freesync"), 0)

        host_on, _, _ = self._make_host(edid=5, fs_switch=3)
        self.assertEqual(host_on.query_setting_or_jni("freesync"), 1)


class DisplayChangeRefreshTests(unittest.TestCase):
    """Windows 显示变化 / HDR 变化时同时刷新画面和游戏页面。"""

    def _make_host(self):
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        refreshed = []
        logs = []

        class Host(DisplayFeaturesMixin):
            adb_connected = True
            _adb_busy_until = 0.0
            _display_change_refresh_suppress_until = 0.0
            _display_change_refresh_pending_reason = "WM_DISPLAYCHANGE"
            _display_last_mode = None
            _page_loading = set()
            _page_loaded = {"picturePage", "gamePage"}
            _hdr_memory_apply_timer = None
            _display_change_refresh_timer = None
            _adjust_hotkey_timers = {}
            _cycle_hotkey_timers = {}

            def _refresh_pages(self, pages, delay_ms=0, force=False):
                refreshed.append((tuple(pages), delay_ms, force))

            def log(self, msg):
                logs.append(msg)

        return Host(), refreshed, logs

    def test_display_change_refresh_updates_picture_and_game_pages(self):
        host, refreshed, _ = self._make_host()
        host._refresh_pages_after_display_change()
        self.assertEqual(refreshed, [(("picturePage", "gamePage"), 0, True)])

        # 断开或被自身模式切换抑制时不触发
        refreshed.clear()
        host.adb_connected = False
        host._refresh_pages_after_display_change()
        host.adb_connected = True
        host._suppress_display_change_refresh(5.0)
        host._refresh_pages_after_display_change()
        self.assertEqual(refreshed, [])

    def test_hdr_change_refresh_updates_both_picture_and_game_pages(self):
        host, refreshed, _ = self._make_host()
        host._refresh_picture_data_after_hdr_change()
        self.assertEqual(refreshed, [(("picturePage", "gamePage"), 0, True)])

    def test_display_mode_poll_skips_baseline_and_refreshes_on_change(self):
        host, _, _ = self._make_host()
        scheduled = []
        host._schedule_display_change_refresh = (
            lambda reason="windows_event", delay_ms=900: scheduled.append((reason, delay_ms))
        )

        # 首次检测到分辨率/刷新率只是建立基线，不触发刷新
        host._reconcile_display_mode_state((3840, 2160, 160))
        self.assertEqual(scheduled, [])

        # 同模式下不重复触发
        host._reconcile_display_mode_state((3840, 2160, 160))
        self.assertEqual(scheduled, [])

        # OSD 或系统切换导致刷新率/分辨率变化时触发刷新
        host._reconcile_display_mode_state((1920, 1080, 320))
        self.assertEqual(scheduled, [("显示模式变化", 200)])


class FullPresetIntegrationTests(unittest.TestCase):
    """全功能预设：手动修改各非画面页设置触发预设自动同步，以及预设期间暂停准星记忆。"""

    def _make_sync_host(self):
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        noted = []
        refreshed = []

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def get(self, _key, check=False):
                return "29"

            def put(self, _k, _v, check=False):
                pass

            def shell(self, _cmd, check=False):
                return ""

            def jni_set(self, _k, _v, upd=3, check=False):
                pass

            def refresh_pq(self, check=False):
                pass

            def set_media_volume(self, *_args, **_kwargs):
                pass

            def set_media_muted(self, *_args, **_kwargs):
                pass

            def colorful_led(self, *_args, **_kwargs):
                pass

        class Host(DisplayFeaturesMixin):
            adb_connected = True
            adb = FakeAdb()
            current_vals = {
                "picture_mode": 10,
                "mode_320": 0,
                "front_sight_index": 0,
                "volume_music_speaker": 40,
                "media_muted": 0,
                "atmosphere_light_switcher_pm2": 4,
            }
            _control_rollback_values = {}
            _adjust_hotkey_pending = {}
            _adjust_hotkey_timers = {}
            _adjust_hotkey_resolving = set()
            _page_loaded = {"picturePage", "gamePage", "sourcePage", "lightPage"}
            _display_change_refresh_suppress_until = 0.0
            values_signal = SimpleNamespace(emit=lambda _d: None)
            logs = []

            def check_connection(self):
                return True

            def _mark_adb_busy(self, _seconds=2.0):
                pass

            def _run_adb_action(self, _label, operation, success, failure=None):
                operation()
                success()

            def _optimistic_highlight(self, _k, _v):
                pass

            def _refresh_pages(self, pages, delay_ms=0, force=False):
                refreshed.append((tuple(pages), delay_ms, force))

            def _note_picture_change(self):
                noted.append(True)

            def log(self, msg):
                self.logs.append(msg)

        return Host(), noted

    def test_manual_changes_across_game_menu_audio_light_note_picture_change(self):
        host, noted = self._make_sync_host()

        host._320(True)
        host._set_game_feature("front_sight_index", 2, "准星: 2")
        host._set("monitor_menu_fps_counter", 1, "FPS: 刷新率")
        host.set_media_volume(55)
        host.toggle_media_mute(muted=True)
        host._set_screen_light_mode(2, "纯色")
        self.assertEqual(len(noted), 6)

        # 输入源切换不应触发预设保存
        host.source_label = SimpleNamespace(setText=lambda _t: None)
        host._source_names = {29: "DP"}
        host._start_source_polling = lambda: None
        host._set("mitv.tvplayer.hdmi.last.source", 29, "切源")
        self.assertEqual(len(noted), 6)

    def test_crosshair_memory_suspended_while_preset_active(self):
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        writes = []

        class Host(DisplayFeaturesMixin):
            adb_connected = True
            adb = SimpleNamespace(put=lambda k, v, check=False: writes.append((k, v)))
            current_vals = {"picture_mode": 14, "front_sight_index": 2}

            def memories_suspended_by_preset(self):
                return True

            def _crosshair_game_mode_only_enabled(self):
                return True

        Host()._reconcile_crosshair_mode_state()
        self.assertEqual(writes, [])

    def test_preset_apply_releases_busy_lock_coalesces_verify_timer_and_routes_skipped_to_detail_log(self):
        from contextlib import nullcontext
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.presets import ApplyResult

        logs = []
        detail_logs = []

        class FakeTimer:
            def __init__(self):
                self.starts = []

            def start(self, ms):
                self.starts.append(ms)

        class FakeAdb:
            def transaction(self):
                return nullcontext()

        fake_result = ApplyResult(
            applied=[("亮度", "picture_brightness", "70")],
            skipped=[("色增益", "色温不是「自定义」，色增益不生效")],
            failed=[("对比度", "写入超时")],
        )

        class Host(display_features.DisplayFeaturesMixin):
            adb_connected = True
            adb = FakeAdb()
            current_vals = {}
            _page_loaded = {"picturePage", "gamePage", "sourcePage", "lightPage"}
            _adb_busy_until = 0.0
            _display_change_refresh_suppress_until = 0.0
            _local_dimming_memory_suppress_until = 0.0
            _cleanup_done = False

            def __init__(self):
                self._preset_verify_timer = FakeTimer()

            def check_connection(self):
                return True

            def _read_picture_values(self):
                return {"picture_mode": 14}

            def _show_preset_switching_overlay(self):
                pass

            def _hide_preset_switching_overlay(self):
                pass

            def _refresh_preset_views(self):
                pass

            def log(self, message):
                logs.append(message)

            def log_detail(self, message):
                detail_logs.append(message)

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                operation()
                if on_success:
                    on_success()

        host = Host()
        with mock.patch.object(display_features, "apply_preset", return_value=fake_result):
            host._apply_picture_values({"picture_brightness": 70}, "应用预设「A」")
            host._apply_picture_values({"picture_brightness": 75}, "应用预设「B」")

        self.assertEqual(host._adb_busy_until, 0.0)
        self.assertFalse(host._adb_channel_busy())
        self.assertEqual(host._preset_verify_timer.starts, [1800, 1800])
        self.assertTrue(any("应用预设「A」：成功 1 项，跳过 1 项，失败 1 项" in m for m in logs))
        self.assertTrue(any("失败 对比度：写入超时" in m for m in logs))
        self.assertFalse(any("跳过 色增益" in m for m in logs))
        self.assertEqual(
            detail_logs,
            [
                "  · 跳过 色增益（色温不是「自定义」，色增益不生效）",
                "  · 跳过 色增益（色温不是「自定义」，色增益不生效）",
            ],
        )


class DisplayFeatureRegressionFixTests(unittest.TestCase):
    """回归覆盖 FreeSync/MEMC/色增益/失败回滚/HDR连接恢复/自动任务交接等修复。"""

    def _make_control_host(self, *, fail_adb=False, source="23", edid="2", fs="0", extra_vals=None):
        import inspect
        from PyQt6.QtCore import QObject
        from mimonitor_toolbox import display_features

        calls = []
        logs = []
        emitted = []
        jni_emitted = []
        highlights = []
        mode_highlights = []
        refreshes = []
        switch_states = []

        class FakeTimer:
            def __init__(self):
                self.stops = 0
                self.starts = []
                self.intervals = []

            def stop(self):
                self.stops += 1

            def setInterval(self, ms):
                self.intervals.append(ms)

            def start(self, *args):
                self.starts.append(args)

        class FakeSwitch:
            def __init__(self):
                self.checked = False
                self.blocked = []

            def blockSignals(self, flag):
                self.blocked.append(flag)
                return False

            def setChecked(self, val):
                self.checked = bool(val)
                switch_states.append(bool(val))

        class FakeAdb:
            def __init__(self):
                self.queries = {}

            def transaction(self):
                return nullcontext()

            def shell(self, cmd, check=False):
                calls.append(("shell", cmd, check))

            def get(self, key, check=False):
                calls.append(("get", key, check))
                if key == "mitv.tvplayer.hdmi.last.source":
                    return source
                return self.queries.get(key, "")

            def jni_get(self, key, check=False):
                calls.append(("jni_get", key, check))
                return ""

            def jni_batch_get_or_single(self, keys, check=False):
                calls.append(("jni_batch", tuple(keys)))
                return {
                    "g_fusion_picture__hdmi_edid_version": edid,
                    "g_video__freesync_switch": fs,
                }

            def put(self, key, val, check=False):
                calls.append(("put", key, val, check))
                if fail_adb:
                    raise RuntimeError("adb put failed")

            def jni_set(self, key, val, upd=3, check=False):
                calls.append(("jni_set", key, val, upd, check))
                if fail_adb:
                    raise RuntimeError("adb jni_set failed")

            def jni_set_color_gains(self, r, g, b, check=False):
                calls.append(("jni_set_color_gains", r, g, b, check))
                if fail_adb:
                    raise RuntimeError("adb gains failed")

            def hdr_tone_mapping(self, value, upd=3, check=False):
                calls.append(("hdr_tone_mapping", value, upd, check))
                if fail_adb:
                    raise RuntimeError("adb hdr failed")

            def check_and_heal_jar(self):
                pass

            def refresh_pq(self, check=False):
                calls.append(("refresh_pq", check))
                if fail_adb:
                    raise RuntimeError("adb refresh failed")

            def media_muted(self, check=False):
                calls.append(("media_muted", check))
                return False

            def set_media_muted(self, muted, show_ui=False, check=False):
                calls.append(("set_media_muted", muted, show_ui, check))
                if fail_adb:
                    raise RuntimeError("adb mute failed")

        class Host(display_features.DisplayFeaturesMixin, QObject):
            def __init__(self):
                super().__init__()
                self.adb_connected = True
                self.adb = FakeAdb()
                self.current_vals = dict(extra_vals or {})
                self.state_buttons = {}
                self.mode_btns = {}
                self.sliders = {}
                self.osd = None
                self.light_sensor_switch = FakeSwitch()
                self.source_label = SimpleNamespace(setText=lambda _t: None)
                self._source_names = {"23": "HDMI 1", "29": "DP"}
                self._cycle_hotkey_pending = {}
                self._cycle_hotkey_timers = {}
                self._control_rollback_values = {}
                self._adjust_hotkey_pending = {}
                self._adjust_hotkey_timers = {}
                self._adjust_hotkey_resolving = set()
                self._picture_mode_switch_seq = 0
                self._local_dimming_toggle_suspended = False
                self._local_dimming_memory_suppress_until = 0.0
                self._local_dimming_toggle_resolving = False
                self._media_mute_resolving = False
                self._adb_busy_until = 0.0
                self._display_change_refresh_suppress_until = 0.0
                self._page_loaded = {"picturePage", "gamePage", "sourcePage", "lightPage"}
                self._preset_sync_timer = FakeTimer()
                self._preset_verify_timer = FakeTimer()
                self.values_signal = SimpleNamespace(emit=lambda d: emitted.append(dict(d)))
                self.jni_values_signal = SimpleNamespace(emit=lambda d: jni_emitted.append(dict(d)))

            def isVisible(self):
                return True

            def isMinimized(self):
                return False

            def check_connection(self):
                return self.adb_connected

            def _mark_adb_busy(self, seconds=2.0):
                pass

            def _suppress_display_change_refresh(self, seconds=2.0):
                pass

            def _note_picture_change(self):
                pass

            def _start_source_polling(self):
                pass

            def _optimistic_highlight(self, key, val):
                highlights.append((key, val))

            def _highlight_mode(self, mode):
                mode_highlights.append(mode)

            def _refresh_pages(self, pages, delay_ms=0, force=False):
                refreshes.append((tuple(pages), delay_ms, force))

            def _force_refresh_page(self, page):
                refreshes.append((("force", page), 0, True))

            def log(self, msg):
                logs.append(msg)

            def log_detail(self, msg):
                logs.append(msg)

            def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
                try:
                    operation()
                except Exception as exc:
                    if on_failure:
                        if len(inspect.signature(on_failure).parameters) > 0:
                            on_failure(exc)
                        else:
                            on_failure()
                    return
                if on_success:
                    on_success()

        host = Host()
        return host, calls, logs, emitted, jni_emitted, highlights, mode_highlights, refreshes, switch_states

    def test_freesync_hotkey_uses_prior_state_and_refreshes_before_restoring_mode(self):
        from mimonitor_toolbox import display_features

        settings = {
            "freesync_mode_memory_enabled": True,
            "freesync_previous_mode": None,
            "active_preset_id": None,
            "hotkey_countdown_enabled": False,
        }
        host, calls, *_ = self._make_control_host(extra_vals={"freesync": 0, "picture_mode": 14})
        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(display_features, "update_settings", side_effect=settings.update):
            host.trigger_hotkey_action("freesync_toggle")
            self.assertEqual(settings["freesync_previous_mode"], 14)

            calls.clear()
            host.adb = host.adb.__class__()
            host.current_vals["picture_mode"] = 10
            host._stage_cycle_hotkey_action(
                "freesync_toggle", "freesync", [(0, "关"), (1, "开")], "FreeSync",
                lambda val, _name: host._fsync(val == 1),
            )

        refresh_idx = calls.index(("refresh_pq", True))
        mode_idx = calls.index(("put", "picture_mode", "14", True))
        self.assertLess(refresh_idx, mode_idx)
        self.assertEqual(calls[-1], ("refresh_pq", True))
        self.assertEqual(host.current_vals["picture_mode"], 14)

    def test_set_memc_does_not_overwrite_xiaomi_picture_mode_with_mtk_enum(self):
        from mimonitor_toolbox import core, display_features

        host, calls, *_ = self._make_control_host(extra_vals={"picture_mode": 10, core.MEMC_SETTINGS_KEY: 0})
        host.adb.queries["picture_mode"] = "10"

        def fake_jni_batch(keys, check=False):
            return {"g_video__picture_mode": "69"}

        host.adb.jni_batch_get_or_single = fake_jni_batch
        with mock.patch.object(display_features, "evaluate_memc_conditions") as eval_mock, \
                mock.patch.object(display_features, "write_memc_value") as write_mock:
            eval_mock.return_value = SimpleNamespace(case="C", mode=69)
            host._set_memc(2, "中")
        write_mock.assert_called_once()
        self.assertEqual(host.current_vals["picture_mode"], 10)
        self.assertEqual(host.current_vals[core.MEMC_SETTINGS_KEY], 2)

    def test_color_gain_staging_no_ui_adb_and_async_resolve_plus_rollback(self):
        from mimonitor_toolbox import core, display_features

        # 1) 暂存阶段（未 commit）不得发任何 ADB 读/写；且保留原始色温以便回滚
        host, calls, _, emitted, _, highlights, *_ = self._make_control_host(
            extra_vals={"picture_color_temperature": 1, "picture_red_gain": 1000}
        )
        host._adjust_hotkey_timers["red_gain"] = SimpleNamespace(
            stop=lambda: None, setInterval=lambda _ms: None, start=lambda: None
        )
        cfg = core.ADJUSTABLE_HOTKEY_PARAMS["red_gain"]
        with mock.patch.object(display_features, "load_settings", return_value={"hotkey_countdown_enabled": True, "hotkey_countdown_seconds": 0.5}):
            host._stage_adjustable_display_value("red_gain", cfg, 1100, show_hud=False)
        self.assertEqual(calls, [])
        self.assertEqual(host.current_vals["picture_color_temperature"], 1)

        # 未连接时中止 commit，必须回滚暂存值
        host.adb_connected = False
        host._commit_pending_adjustment("red_gain")
        self.assertEqual(host.current_vals["picture_red_gain"], 1000)

        # 2) 已连接但缺失 green/blue 增益时，在 worker 内异步补读；且仅 put 单轴
        host2, calls2, *_ = self._make_control_host(
            extra_vals={"picture_color_temperature": 3, "picture_red_gain": 1024}
        )
        host2.adb.queries["picture_green_gain"] = "1080"
        host2.adb.queries["picture_blue_gain"] = "960"
        host2._set_color_gain("红增益", "picture_red_gain", "g_video__clr_gain_r", 1120)
        self.assertIn(("jni_set_color_gains", 1120, 1080, 960, True), calls2)
        self.assertEqual([c for c in calls2 if c[0] == "put"], [("put", "picture_red_gain", "1120", True)])
        self.assertFalse(any(c[0] == "jni_set" and c[1] == "g_video__clr_temp" for c in calls2))
        self.assertEqual(host2.current_vals["picture_green_gain"], 1080)
        self.assertEqual(host2.current_vals["picture_blue_gain"], 960)

        # 3) 写入失败时，不得残留在色温 3 或新增益值
        host3, _, _, emitted3, _, highlights3, *_ = self._make_control_host(
            fail_adb=True,
            extra_vals={"picture_color_temperature": 1, "picture_red_gain": 1000, "picture_green_gain": 1024, "picture_blue_gain": 1024},
        )
        host3._adjust_hotkey_timers["red_gain"] = SimpleNamespace(
            stop=lambda: None, setInterval=lambda _ms: None, start=lambda: None
        )
        with mock.patch.object(display_features, "load_settings", return_value={"hotkey_countdown_enabled": False}):
            host3._stage_adjustable_display_value("red_gain", cfg, 1150, show_hud=False)
        self.assertEqual(host3.current_vals["picture_color_temperature"], 1)
        self.assertEqual(host3.current_vals["picture_red_gain"], 1000)
        self.assertEqual(highlights3[-1], ("picture_color_temperature", 1))
        self.assertEqual(emitted3[-1]["picture_color_temperature"], 1)

    def test_failure_with_none_previous_clears_state_and_disconnect_reverts(self):
        from mimonitor_toolbox import core, display_features

        # previous is None 时，失败应清理 current_vals 并取消按钮高亮
        host, _calls, _logs, _emitted, _jni_emitted, highlights, mode_highlights, *_ = self._make_control_host(
            fail_adb=True
        )
        host._set_mode(10, "游戏")
        self.assertNotIn("picture_mode", host.current_vals)
        self.assertEqual(mode_highlights[-1], None)

        host._320(True)
        self.assertNotIn("mode_320", host.current_vals)
        self.assertEqual(highlights[-1], ("mode_320", None))

        host._fsync(True)
        self.assertNotIn("freesync", host.current_vals)
        self.assertEqual(highlights[-1], ("freesync", None))

        host._jni("g_video__vid_od_response_time", 2, "picture_response_time", "响应时间: 快速")
        self.assertNotIn("picture_response_time", host.current_vals)
        self.assertEqual(highlights[-1], ("picture_response_time", None))

        host._set_color_temp(2, 1, "色温: 标准")
        self.assertNotIn("picture_color_temperature", host.current_vals)
        self.assertEqual(highlights[-1], ("picture_color_temperature", None))

        host._commit_screen_light("屏幕灯模式: 常亮", {"atmosphere_light_switcher_pm2": 1})
        self.assertNotIn("atmosphere_light_switcher_pm2", host.current_vals)
        self.assertEqual(highlights[-1], ("atmosphere_light_switcher_pm2", None))

        # _set_light_sensor 未连接时应还原 switch
        host.adb_connected = False
        host.light_sensor_switch.checked = True
        host._set_light_sensor(True)
        self.assertFalse(host.light_sensor_switch.checked)

        # 循环快捷键断开后 commit 应回滚 staged 值
        host.adb_connected = True
        host.current_vals["picture_mode"] = 14
        host._cycle_hotkey_timers["picture_mode_cycle"] = SimpleNamespace(
            stop=lambda: None, setInterval=lambda _ms: None, start=lambda: None
        )
        with mock.patch.object(display_features, "load_settings", return_value={"hotkey_countdown_enabled": True, "hotkey_countdown_seconds": 0.5}):
            host._stage_cycle_hotkey_action(
                "picture_mode_cycle", "picture_mode", [(14, "标准"), (10, "游戏")], "画面模式",
                lambda val, name: host._set_mode(val, name),
            )
        self.assertEqual(host.current_vals["picture_mode"], 10)
        host.adb_connected = False
        host._commit_cycle_hotkey_action("picture_mode_cycle")
        self.assertEqual(host.current_vals["picture_mode"], 14)

        # _set_adjustable_display_value 失败回滚且不强制刷新 picturePage
        host2, _, _, emitted2, _, _, _, refreshes2, _ = self._make_control_host(
            fail_adb=True, extra_vals={"picture_contrast": 50}
        )
        host2._set_adjustable_display_value("contrast", core.ADJUSTABLE_HOTKEY_PARAMS["contrast"], 75)
        self.assertEqual(host2.current_vals["picture_contrast"], 50)
        self.assertEqual(emitted2[-1], {"picture_contrast": 50})
        self.assertEqual(refreshes2, [])

    def test_poll_hdr_memory_skips_state_while_disconnected_and_applies_on_connect(self):
        from mimonitor_toolbox import display_features

        host, *_ = self._make_control_host()
        host.adb_connected = False
        host._hdr_last_state = None
        host._display_last_mode = None
        host._hdr_state_source = None
        host._hdr_windows_state = None
        applied_checks = []
        host._query_windows_hdr_state = lambda: True
        host._query_windows_display_mode = lambda: (2560, 1440, 240)
        host._update_hdr_memory_status_label = lambda *_a: None
        host._schedule_hdr_memory_apply = lambda delay_ms=120: applied_checks.append(delay_ms)
        host._schedule_picture_refresh_after_hdr_change = lambda *a, **kw: None
        host._hdr_memory_enabled = lambda: True

        host._poll_hdr_memory_state("timer")
        self.assertIsNone(host._hdr_last_state)
        self.assertIsNone(host._display_last_mode)
        self.assertEqual(applied_checks, [])

        host.adb_connected = True
        host._poll_hdr_memory_state("connected")
        self.assertIs(host._hdr_last_state, True)
        self.assertEqual(applied_checks, [120])

    def test_auto_task_direct_a_to_b_carries_baseline_and_failure_keeps_snapshot(self):
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.presets import ApplyResult

        settings = {
            "active_preset_id": "pa",
            "presets": [
                {"id": "pa", "name": "A", "values": {"picture_mode": 9}},
                {"id": "pb", "name": "B", "values": {"picture_mode": 10}},
            ],
            "auto_tasks": [
                {
                    "id": "ta",
                    "preset_id": "pa",
                    "start": "08:00",
                    "end": "10:00",
                    "snapshot": {"values": {"picture_mode": 14, "picture_contrast": 48}, "active_preset_id": None},
                },
                {"id": "tb", "preset_id": "pb", "start": "10:00", "end": "12:00", "snapshot": None},
            ],
        }
        host, *_ = self._make_control_host()
        host._auto_task_active_id = "ta"
        host._auto_task_checking = False
        host._auto_task_waiting_id = None
        host._auto_task_deferred_id = None
        host.active_preset_id = lambda: settings.get("active_preset_id")
        host._read_picture_values = lambda: {"picture_mode": 9, "picture_contrast": 99}
        host._refresh_preset_views = lambda: None
        host._show_preset_switching_overlay = lambda: None
        host._hide_preset_switching_overlay = lambda: None
        host._schedule_preset_verify = lambda _ms: None

        # A -> B 若写入失败，保留 A 为活动任务且保留 A 的原始基线快照
        fake_now = time.struct_time((2026, 11, 15, 10, 30, 0, 6, 319, -1))
        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(display_features, "update_settings", side_effect=settings.update), \
                mock.patch.object(display_features.time, "localtime", return_value=fake_now), \
                mock.patch.object(display_features, "apply_preset", side_effect=RuntimeError("boom")):
            host._auto_task_tick()
        self.assertEqual(host._auto_task_active_id, "ta")
        self.assertEqual(settings["auto_tasks"][0]["snapshot"]["values"], {"picture_mode": 14, "picture_contrast": 48})
        self.assertIsNone(settings["auto_tasks"][1]["snapshot"])

        # A -> B 成功时，把 A 的原始基线快照转移给 B，不采样 A 的值覆盖基线
        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(display_features, "update_settings", side_effect=settings.update), \
                mock.patch.object(display_features.time, "localtime", return_value=fake_now), \
                mock.patch.object(display_features, "apply_preset", return_value=ApplyResult(applied=["画面模式"])):
            host._auto_task_tick()
        self.assertEqual(host._auto_task_active_id, "tb")
        self.assertIsNone(settings["auto_tasks"][0]["snapshot"])
        self.assertEqual(settings["auto_tasks"][1]["snapshot"]["values"], {"picture_mode": 14, "picture_contrast": 48})

    def test_p2_fixes_tray_offset_submode_crosshair_preset_fail_and_hdmi_refresh(self):
        from mimonitor_toolbox import core, display_features
        from mimonitor_toolbox.presets import ApplyResult

        host, calls, _logs, _emitted, _jni_emitted, _hl, _mhl, refreshes, _ = self._make_control_host(
            extra_vals={"atmosphere_light_illumination": 0, "picture_mode": 25}
        )
        # 1) atmosphere_illumination tray_entry_value 含 +1 ui_offset
        self.assertEqual(host.tray_entry_value("atmosphere_illumination"), 1)

        # 2) _set_game_feature(retrigger_game_mode=True) 保留 25 子模式，而非写死 10
        with mock.patch.object(display_features.time, "sleep"):
            host._set_game_feature("front_sight_index", 3, "准星: 3", retrigger_game_mode=True)
        self.assertIn(("put", "picture_mode", "25", True), calls)
        self.assertNotIn(("put", "picture_mode", "10", True), calls)

        # 3) _fs 取消确认或 ADB 失败时不落盘 crosshair_memory
        settings = {"crosshair_game_mode_only": True, "crosshair_memory": 2}
        host_fail, *_ = self._make_control_host(fail_adb=True, extra_vals={"picture_mode": 14})
        host_fail._update_crosshair_mode_status_label = lambda: None
        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(display_features, "update_settings", side_effect=settings.update), \
                mock.patch.object(display_features.DisplayFeaturesMixin, "_confirm_switch_to_game_mode", return_value=False):
            host_fail._fs(5)
            self.assertEqual(settings["crosshair_memory"], 2)
        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(display_features, "update_settings", side_effect=settings.update), \
                mock.patch.object(display_features.DisplayFeaturesMixin, "_confirm_switch_to_game_mode", return_value=True):
            host_fail._fs(5)
            self.assertEqual(settings["crosshair_memory"], 2)

        # 4) _apply_picture_values 停止 _preset_sync_timer，且 failed 项不污染 current_vals
        host._show_preset_switching_overlay = lambda: None
        host._hide_preset_switching_overlay = lambda: None
        host._schedule_preset_verify = lambda _ms: None
        fake_res = ApplyResult(applied=["背光"], failed=[("对比度", "timeout")])
        with mock.patch.object(display_features, "apply_preset", return_value=fake_res):
            host._apply_picture_values({"picture_backlight": 66, "picture_contrast": 88}, "应用测试")
        self.assertEqual(host._preset_sync_timer.stops, 1)
        self.assertEqual(host.current_vals["picture_backlight"], 66)
        self.assertNotIn("picture_contrast", host.current_vals)

        # 5) _stage_cycle_hotkey_action 支持字符串子模式 "25" 与声音输出 "3"
        host.current_vals["picture_mode"] = "25"
        host._cycle_hotkey_timers["picture_mode_cycle"] = SimpleNamespace(
            stop=lambda: None, setInterval=lambda _ms: None, start=lambda: None
        )
        committed_vals = []
        with mock.patch.object(display_features, "load_settings", return_value={"hotkey_countdown_enabled": False}):
            host._stage_cycle_hotkey_action(
                "picture_mode_cycle", "picture_mode", [(14, "标准"), (10, "游戏"), (9, "电影")], "画面模式",
                lambda v, _n: committed_vals.append(v),
            )
        self.assertEqual(committed_vals, [9])

        # 6) toggle_media_mute 在缺失 media_muted 解析期间阻止并发重复触发
        pending_ops = []
        host_mute, *_ = self._make_control_host()
        host_mute._run_adb_action = lambda label, op, ok=None, fail=None: pending_ops.append((label, op, ok, fail))
        host_mute.toggle_media_mute()
        host_mute.toggle_media_mute()
        self.assertEqual(len(pending_ops), 1)
        pending_ops[0][1]()
        pending_ops[0][2]()
        self.assertFalse(host_mute._media_mute_resolving)
        self.assertEqual(len(pending_ops), 2)

        # 7) HDMI 切换成功刷新 picturePage 和 gamePage
        host._set("mitv.tvplayer.hdmi.last.source", "23", "输入源: HDMI 1")
        self.assertIn((("picturePage", "gamePage"), 800, True), refreshes)

    def test_direct_input_source_hotkeys_and_fallback(self):
        from mimonitor_toolbox import display_features

        host, calls, _logs, emitted, _jni_emitted, _hl, _mhl, refreshes, _ = self._make_control_host()
        set_source_calls = []
        host.adb.set_input_source = lambda val, check=False: set_source_calls.append((val, check))

        # input_source_cycle 已移除，触发不产生操作
        with mock.patch.object(display_features, "load_settings", return_value={"hotkey_countdown_enabled": False}):
            host.trigger_hotkey_action("input_source_cycle")
        self.assertEqual(set_source_calls, [])

        # 连按不同直切热键共享 input_source_direct 槽位并更新 HUD/状态
        timer = SimpleNamespace(stops=0, intervals=[], starts=0)
        timer.stop = lambda: setattr(timer, "stops", timer.stops + 1)
        timer.setInterval = lambda ms: timer.intervals.append(ms)
        timer.start = lambda: setattr(timer, "starts", timer.starts + 1)
        host._cycle_hotkey_timers["input_source_direct"] = timer

        hud_calls = []
        host.osd = SimpleNamespace(
            show_hud=lambda *a, **kw: hud_calls.append((a, kw)),
            end_countdown=lambda: None,
        )
        with mock.patch.object(display_features, "load_settings", return_value={"hotkey_countdown_enabled": True, "hotkey_countdown_seconds": 0.6}):
            host.trigger_hotkey_action("input_source_hdmi1")
            host.trigger_hotkey_action("input_source_dp")
            self.assertEqual(host.current_vals["mitv.tvplayer.hdmi.last.source"], 29)
            self.assertEqual(hud_calls[-1], (("信号源", "DP"), {"countdown": 0.6}))
            self.assertEqual(set_source_calls, [])
            host._commit_cycle_hotkey_action("input_source_direct")

        self.assertEqual(set_source_calls, [(29, True)])
        self.assertIn(("refresh_pq", True), calls)
        self.assertIn({"mitv.tvplayer.hdmi.last.source": 29}, emitted)

        # 关闭倒计时后 USBC 直接下发
        with mock.patch.object(display_features, "load_settings", return_value={"hotkey_countdown_enabled": False}):
            host.trigger_hotkey_action("input_source_usbc")
        self.assertEqual(set_source_calls[-1], (30, True))


if __name__ == "__main__":
    unittest.main()
