import unittest
from unittest import mock


class DisplayFeatureTests(unittest.TestCase):
    """捕获画面模式分组在迁移后丢失或映射错误。"""

    def test_picture_mode_group_maps_presets_to_primary_mode(self):
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        host = object()
        self.assertEqual(DisplayFeaturesMixin._picture_mode_group_name(host, 64), "标准")
        self.assertEqual(DisplayFeaturesMixin._picture_mode_group_name(host, 25), "游戏")
        self.assertEqual(DisplayFeaturesMixin._picture_mode_group_name(host, 9), "电影")

    def test_hdr_state_query_uses_selected_display_only(self):
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        host = DisplayFeaturesMixin()
        displays = [
            {"device_name": "DISPLAY1", "device_id": "XMI27B3-1", "label": "红米"},
            {"device_name": "DISPLAY2", "device_id": "VIRTUAL-2", "label": "虚拟屏"},
        ]
        with mock.patch.object(display_features, "load_settings",
                               return_value={"hdr_target_display_id": "XMI27B3-1"}), \
                mock.patch.object(display_features, "list_windows_displays", return_value=displays), \
                mock.patch.object(display_features, "query_windows_hdr_enabled",
                                  return_value=False) as query:
            self.assertFalse(host._query_windows_hdr_state())
        query.assert_called_once_with(target_device_id="XMI27B3-1")
        self.assertEqual(host._hdr_target_display_label, "红米")

    def test_missing_hdr_target_does_not_use_other_display(self):
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        host = DisplayFeaturesMixin()
        displays = [{"device_name": "DISPLAY2", "device_id": "VIRTUAL-2",
                     "label": "虚拟屏"}]
        with mock.patch.object(display_features, "load_settings",
                               return_value={"hdr_target_display_id": "missing"}), \
                mock.patch.object(display_features, "list_windows_displays", return_value=displays), \
                mock.patch.object(display_features, "query_windows_hdr_enabled") as query:
            self.assertIsNone(host._query_windows_hdr_state())
        query.assert_not_called()


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
            def _set_game_feature(self, key, value, message, retrigger_game_mode=False):
                calls.append((key, value, retrigger_game_mode))

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

    # ── 编辑 / 新建（无会话，切过去就是当前预设）──

    def test_edit_applies_the_preset_then_jumps_to_the_picture_page(self):
        settings = {"presets": [{"id": "p1", "name": "看电影",
                                 "values": {"picture_mode": 9, "picture_contrast": 70}}]}

        def run(host):
            host.begin_preset_edit(preset_id="p1")

        host, calls, _ = self._run(settings, True, run)
        self.assertIn(("put", "picture_mode", "9"), calls)
        self.assertIn(("put", "picture_contrast", "70"), calls)
        self.assertEqual(settings["active_preset_id"], "p1")
        self.assertEqual(host.stack, [host.picture_page])

    def test_edit_on_already_active_preset_only_jumps(self):
        """已经是当前预设时不该再写一遍设备。"""
        settings = {"active_preset_id": "p1",
                    "presets": [{"id": "p1", "name": "A", "values": {"picture_mode": 9}}]}

        def run(host):
            host.begin_preset_edit(preset_id="p1")

        host, calls, _ = self._run(settings, True, run)
        self.assertEqual(calls, [])
        self.assertEqual(host.stack, [host.picture_page])

    def test_edit_is_refused_when_disconnected(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {"picture_mode": 9}}]}

        def run(host):
            host.begin_preset_edit(preset_id="p1")

        host, calls, _ = self._run(settings, False, run)
        self.assertEqual(calls, [])
        self.assertEqual(host.stack, [])

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


if __name__ == "__main__":
    unittest.main()
