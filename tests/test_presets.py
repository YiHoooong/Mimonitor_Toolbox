import unittest


class FakeAdb:
    """记录调用序列的假 adb，只实现 presets.py 用到的那几个方法。"""

    def __init__(self, fail_on=None, source="23"):
        self.calls = []
        self.fail_on = fail_on or set()
        self.source = source

    def _record(self, name, *args, **kwargs):
        self.calls.append((name,) + args)
        if name in self.fail_on:
            raise RuntimeError(f"{name} 故意失败")

    def put(self, key, value, check=False):
        self._record("put", key, value, check)

    def jni_set(self, key, val, upd=3, check=False):
        self._record("jni_set", key, val, upd, check)

    def get(self, key, check=False):
        self._record("get", key, check)
        return self.source

    def refresh_pq(self, check=False):
        self._record("refresh_pq", check)

    def jni_set_color_gains(self, red, green, blue, check=False):
        self._record("jni_set_color_gains", red, green, blue, check)

    def hdr_tone_mapping(self, val, upd=3, check=False):
        self._record("hdr_tone_mapping", val, upd, check)

    def check_and_heal_jar(self):
        self._record("check_and_heal_jar")


def full_values(mode=14, color_temp=1, light_sensor=0, freesync=0):
    """一套完整可用的值（含 FreeSync —— 它也在预设范围内）。"""
    return {
        "freesync": freesync,
        "picture_mode": mode,
        "tv_picture_light_sensor": light_sensor,
        "picture_backlight": 52,
        "xiaomi_picture_backlight": 52,
        "picture_brightness": 50,
        "picture_contrast": 60,
        "picture_saturation": 55,
        "picture_hue": 50,
        "picture_sharpness": 5,
        "picture_color_temperature": color_temp,
        "picture_red_gain": 1024,
        "picture_green_gain": 1034,
        "picture_blue_gain": 1014,
        "picture_local_dimming": 3,
        "tv_picture_video_local_dimming": 3,
        "picture_hdr_tone_mapping": 5,
        "settings_display_hdr_color_tone": 0,
        "picture_dynamic_definition": 2,
        "picture_response_time": 3,
        "tv_picture_advanced_video_color_space": 6,
        "tv_picture_video_color_space": 6,
    }


class WriteOrderTests(unittest.TestCase):
    """写入顺序是功能正确性的核心。

    FreeSync 排第一（开着会把设备锁在游戏模式），画面模式排第二，
    其余各项排在其后。
    """

    def test_freesync_is_written_before_picture_mode(self):
        """FreeSync 开着会把设备锁在游戏模式，必须先把它设成目标值，
        否则后面写的画面模式会被顶掉。"""
        from mimonitor_toolbox import presets

        ids = [item.id for item in presets.PICTURE_ITEMS]
        self.assertEqual(ids[0], "freesync")
        self.assertEqual(ids[1], "picture_mode")

    def test_freesync_written_before_picture_mode_on_the_wire(self):
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        presets.apply_preset(adb, full_values())
        fs_index = adb.calls.index(("jni_set", "g_video__freesync_switch", 0, 3, True))
        mode_index = adb.calls.index(("put", "picture_mode", "14", True))
        self.assertLess(fs_index, mode_index)

    def test_picture_mode_written_before_the_other_settings(self):
        """真机实测：写 picture_mode 会让设备重新应用该模式整套参数，
        所以参数必须写在模式之后，否则会被模式的默认值冲掉。

        FreeSync 是唯一例外 —— 它必须排在模式**之前**（见上一条测试）。
        """
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        presets.apply_preset(adb, full_values())
        mode_index = adb.calls.index(("put", "picture_mode", "14", True))
        other_writes = [
            i for i, call in enumerate(adb.calls)
            if call[0] in ("put", "jni_set")
            and call[1] != "picture_mode"
            and "freesync" not in call[1] and "adaptive_sync" not in call[1]
        ]
        self.assertTrue(other_writes)
        self.assertLess(mode_index, min(other_writes))

    def test_every_item_runs_when_both_conditions_are_met(self):
        """模式选 HDR、色温选自定义，两个条件项才都会执行。"""
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        result = presets.apply_preset(adb, full_values(mode=15, color_temp=3))
        self.assertEqual(len(result.applied), len(presets.PICTURE_ITEMS))
        self.assertEqual(result.failed, [])
        self.assertEqual(result.skipped, [])

    def test_conditional_items_are_skipped_not_failed(self):
        """默认（标准模式 + 非自定义色温）下应恰好跳过两个条件项，且不算失败。"""
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        result = presets.apply_preset(adb, full_values())
        self.assertEqual(sorted(name for name, _ in result.skipped),
                         ["HDR 色调映射", "色增益"])
        self.assertEqual(result.failed, [])
        self.assertEqual(len(result.applied), len(presets.PICTURE_ITEMS) - 2)


class ItemWriteSequenceTests(unittest.TestCase):
    """逐项复刻控件自身的写序列 —— 只写 settings 是不够的。"""

    def _calls_for(self, values):
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        presets.apply_preset(adb, values)
        return adb.calls

    def test_backlight_writes_jni_then_refresh_then_both_settings(self):
        calls = self._calls_for(full_values())
        start = calls.index(("jni_set", "g_disp__disp_back_light", 52, 3, True))
        self.assertEqual(calls[start:start + 4], [
            ("jni_set", "g_disp__disp_back_light", 52, 3, True),
            ("refresh_pq", True),
            ("put", "picture_backlight", "52", True),
            ("put", "xiaomi_picture_backlight", "52", True),
        ])

    def test_light_sensor_uses_upd_1_and_no_refresh(self):
        """光感用 isUpdate=1（其余 JNI 写入用 3），且不跟 refresh_pq。"""
        calls = self._calls_for(full_values(light_sensor=1))
        start = calls.index(("jni_set", "g_video__light_sensor_switch", 1, 1, True))
        self.assertEqual(calls[start:start + 2], [
            ("jni_set", "g_video__light_sensor_switch", 1, 1, True),
            ("put", "tv_picture_light_sensor", "1", True),
        ])

    def test_plain_sliders_do_not_trigger_refresh(self):
        """黑色级别等纯 settings 项不该发 refresh_pq（与界面滑条一致）。"""
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        # 只保留纯 settings 项所需的值，其余项故意缺值以便区分
        minimal = {
            "picture_contrast": 60,
        }
        for item in presets.PICTURE_ITEMS:
            if item.id != "contrast":
                continue
            item.apply(adb, minimal)
        self.assertEqual(adb.calls, [("put", "picture_contrast", "60", True)])

    def test_color_temp_maps_xiaomi_enum_to_mtk(self):
        """色温 settings 存小米枚举、JNI 存 MTK 枚举，必须映射。"""
        calls = self._calls_for(full_values(color_temp=2))
        self.assertIn(("jni_set", "g_video__clr_temp", 3, 3, True), calls)
        self.assertIn(("put", "picture_color_temperature", "2", True), calls)

    def test_hdr_tone_mapping_writes_both_enums_separately(self):
        """两个键存不同枚举：MTK 5 / UI 0，不能写同一份值。"""
        calls = self._calls_for(full_values(mode=15))
        self.assertIn(("hdr_tone_mapping", 5, 3, True), calls)
        self.assertIn(("put", "picture_hdr_tone_mapping", "5", True), calls)
        self.assertIn(("put", "settings_display_hdr_color_tone", "0", True), calls)

    def test_gamut_writes_both_settings_keys(self):
        calls = self._calls_for(full_values())
        self.assertIn(("jni_set", "g_video__vid_gamut_mapping_mode", 6, 3, True), calls)
        self.assertIn(("put", "tv_picture_advanced_video_color_space", "6", True), calls)
        self.assertIn(("put", "tv_picture_video_color_space", "6", True), calls)


class ConditionalItemTests(unittest.TestCase):
    """条件项不该盲目下发 —— 色增益依赖自定义色温，HDR 色调映射依赖 HDR 模式。"""

    def test_color_gains_skipped_when_color_temp_not_custom(self):
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        result = presets.apply_preset(adb, full_values(color_temp=1))
        self.assertNotIn("色增益", result.applied)
        labels = [name for name, _ in result.skipped]
        self.assertIn("色增益", labels)
        self.assertFalse([c for c in adb.calls if c[0] == "jni_set_color_gains"])

    def test_color_gains_applied_when_color_temp_is_custom(self):
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        result = presets.apply_preset(adb, full_values(color_temp=3))
        self.assertIn("色增益", result.applied)
        self.assertIn(("jni_set_color_gains", 1024, 1034, 1014, True), adb.calls)

    def test_color_gains_submitted_as_one_group(self):
        """三色必须成组提交 —— 分开写会让固件把没写的通道恢复成 1024。"""
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        presets.apply_preset(adb, full_values(color_temp=3))
        self.assertEqual(len([c for c in adb.calls if c[0] == "jni_set_color_gains"]), 1)

    def test_hdr_tone_mapping_skipped_in_sdr_mode(self):
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        result = presets.apply_preset(adb, full_values(mode=14))
        self.assertNotIn("HDR 色调映射", result.applied)
        self.assertFalse([c for c in adb.calls if c[0] == "hdr_tone_mapping"])

    def test_hdr_tone_mapping_applied_in_hdr_mode(self):
        from mimonitor_toolbox import presets

        adb = FakeAdb()
        result = presets.apply_preset(adb, full_values(mode=15))
        self.assertIn("HDR 色调映射", result.applied)

    def test_hdr_condition_uses_preset_mode_not_device_mode(self):
        """模式最先写，所以条件判断读的是预设自己的模式。"""
        from mimonitor_toolbox import presets

        values = full_values(mode=15)
        self.assertEqual(presets._needs_hdr_mode(values), (True, ""))
        values["picture_mode"] = 14
        self.assertFalse(presets._needs_hdr_mode(values)[0])


class FailureHandlingTests(unittest.TestCase):
    """产品决定：单项失败不中断，写完最后汇报哪几项失败。"""

    def test_failure_does_not_stop_later_items(self):
        from mimonitor_toolbox import presets

        adb = FakeAdb(fail_on={"put"})
        result = presets.apply_preset(adb, full_values())
        # put 全挂，但 jni_set 那些项仍应尝试执行
        self.assertTrue(result.failed)
        self.assertTrue([c for c in adb.calls if c[0] == "jni_set"])

    def test_failed_items_are_reported_by_label(self):
        from mimonitor_toolbox import presets

        adb = FakeAdb(fail_on={"refresh_pq"})
        result = presets.apply_preset(adb, full_values())
        labels = [name for name, _ in result.failed]
        self.assertIn("背光", labels)
        self.assertFalse(result.ok)
        self.assertIn("失败", result.summary())

    def test_missing_value_marks_item_failed_not_crash(self):
        from mimonitor_toolbox import presets

        values = full_values()
        del values["picture_contrast"]
        adb = FakeAdb()
        result = presets.apply_preset(adb, values)
        labels = [name for name, _ in result.failed]
        self.assertEqual(labels, ["对比度"])

    def test_on_item_callback_reports_each_outcome(self):
        from mimonitor_toolbox import presets

        seen = []
        adb = FakeAdb(fail_on={"refresh_pq"})
        presets.apply_preset(adb, full_values(), on_item=lambda item, state, info: seen.append((item.id, state)))
        states = dict(seen)
        self.assertEqual(states["picture_mode"], "applied")
        self.assertEqual(states["backlight"], "failed")
        self.assertEqual(states["color_gains"], "skipped")


class TimePeriodTests(unittest.TestCase):
    def test_parse_hhmm(self):
        from mimonitor_toolbox import presets

        self.assertEqual(presets.parse_hhmm("00:00"), 0)
        self.assertEqual(presets.parse_hhmm("09:30"), 570)
        self.assertEqual(presets.parse_hhmm("23:59"), 1439)
        for bad in ("9:30", "24:00", "09:60", "abc", "", None, "09-30"):
            self.assertIsNone(presets.parse_hhmm(bad), bad)

    def test_format_hhmm_roundtrip(self):
        from mimonitor_toolbox import presets

        for text in ("00:00", "07:05", "23:59"):
            self.assertEqual(presets.format_hhmm(presets.parse_hhmm(text)), text)

    def test_normal_period_boundaries_are_half_open(self):
        from mimonitor_toolbox import presets

        start, end = presets.parse_hhmm("08:00"), presets.parse_hhmm("10:00")
        self.assertFalse(presets.is_in_period(presets.parse_hhmm("07:59"), start, end))
        self.assertTrue(presets.is_in_period(presets.parse_hhmm("08:00"), start, end))
        self.assertTrue(presets.is_in_period(presets.parse_hhmm("09:59"), start, end))
        self.assertFalse(presets.is_in_period(presets.parse_hhmm("10:00"), start, end))

    def test_cross_midnight_period(self):
        from mimonitor_toolbox import presets

        start, end = presets.parse_hhmm("22:00"), presets.parse_hhmm("06:00")
        self.assertTrue(presets.is_in_period(presets.parse_hhmm("22:00"), start, end))
        self.assertTrue(presets.is_in_period(presets.parse_hhmm("23:59"), start, end))
        self.assertTrue(presets.is_in_period(presets.parse_hhmm("00:00"), start, end))
        self.assertTrue(presets.is_in_period(presets.parse_hhmm("05:59"), start, end))
        self.assertFalse(presets.is_in_period(presets.parse_hhmm("06:00"), start, end))
        self.assertFalse(presets.is_in_period(presets.parse_hhmm("12:00"), start, end))

    def test_equal_start_and_end_is_defensive_full_day(self):
        from mimonitor_toolbox import presets

        noon = presets.parse_hhmm("12:00")
        self.assertTrue(presets.is_in_period(noon, noon, noon))


class TaskSelectionTests(unittest.TestCase):
    def _task(self, tid, start, end, preset_id="p1"):
        return {"id": tid, "start": start, "end": end, "preset_id": preset_id}

    def test_valid_task_rejects_bad_fields(self):
        from mimonitor_toolbox import presets

        self.assertTrue(presets.valid_task(self._task("a", "08:00", "10:00")))
        self.assertFalse(presets.valid_task(self._task("a", "8:00", "10:00")))
        self.assertFalse(presets.valid_task(self._task("a", "08:00", "08:00")))
        self.assertFalse(presets.valid_task({"id": "a", "start": "08:00", "end": "10:00"}))
        self.assertFalse(presets.valid_task(None))

    def test_no_task_active_returns_none(self):
        from mimonitor_toolbox import presets

        tasks = [self._task("a", "08:00", "10:00")]
        self.assertIsNone(presets.find_active_task(tasks, presets.parse_hhmm("12:00")))

    def test_overlap_later_task_wins(self):
        """多个任务重叠时后者覆盖前者（用户已定）。"""
        from mimonitor_toolbox import presets

        tasks = [
            self._task("early", "08:00", "12:00", preset_id="A"),
            self._task("late", "10:00", "14:00", preset_id="B"),
        ]
        active = presets.find_active_task(tasks, presets.parse_hhmm("11:00"))
        self.assertEqual(active["preset_id"], "B")

    def test_invalid_tasks_are_ignored_not_fatal(self):
        from mimonitor_toolbox import presets

        tasks = [
            {"id": "broken", "start": "nope", "end": "10:00", "preset_id": "A"},
            self._task("good", "08:00", "12:00", preset_id="B"),
        ]
        active = presets.find_active_task(tasks, presets.parse_hhmm("09:00"))
        self.assertEqual(active["preset_id"], "B")

    def test_disabled_task_is_skipped(self):
        from mimonitor_toolbox import presets

        tasks = [
            self._task("off", "08:00", "12:00", preset_id="A"),
            self._task("on", "08:00", "12:00", preset_id="B"),
        ]
        tasks[0]["enabled"] = False
        active = presets.find_active_task(tasks, presets.parse_hhmm("09:00"))
        self.assertEqual(active["preset_id"], "B")
        tasks[1]["enabled"] = False
        self.assertIsNone(presets.find_active_task(tasks, presets.parse_hhmm("09:00")))


class ReadPipelineCoverageTests(unittest.TestCase):
    """预设采集的键必须都在页面读取管线里，否则快照会缺项。

    这条断言要 import device_features（依赖 PyQt6），所以放在方法内延迟 import，
    让本文件其余纯逻辑测试在没装 PyQt6 的环境也能跑。
    """

    def test_all_preset_keys_are_covered_by_picture_page_pipeline(self):
        from mimonitor_toolbox import presets
        from mimonitor_toolbox.device_features import DeviceFeaturesMixin

        class Probe(DeviceFeaturesMixin):
            pass

        probe = Probe()
        probe.initialize_device_features()
        page = probe._page_data_keys["picturePage"]
        covered = set(page["settings"]) | set(page["jni"])

        missing = [k for k in presets.PICTURE_PRESET_SETTINGS_KEYS if k not in covered]
        missing += [k for k in presets.PICTURE_PRESET_JNI_KEYS if k not in covered]
        self.assertEqual(missing, [])

    def test_preset_scenario_is_deliberately_excluded(self):
        """picture_preset_scenario 是设备推导的场景，不是用户可设项。"""
        from mimonitor_toolbox import presets

        self.assertNotIn("picture_preset_scenario", presets.PICTURE_PRESET_SETTINGS_KEYS)


class FreeSyncPresetTests(unittest.TestCase):
    """FreeSync 走 DP/USB-C 和 HDMI 时是不同的 MTK 键、不同的"开"值。"""

    def _apply(self, source, freesync):
        from mimonitor_toolbox import presets

        adb = FakeAdb(source=source)
        values = full_values(freesync=freesync)
        for item in presets.PICTURE_ITEMS:
            if item.id == "freesync":
                item.apply(adb, values)
        return adb.calls

    def test_hdmi_uses_freesync_switch_with_three_for_on(self):
        calls = self._apply("23", 1)
        self.assertIn(("jni_set", "g_video__freesync_switch", 3, 3, True), calls)
        self.assertIn(("refresh_pq", True), calls)

    def test_hdmi_off_writes_zero(self):
        calls = self._apply("23", 0)
        self.assertIn(("jni_set", "g_video__freesync_switch", 0, 3, True), calls)

    def test_dp_uses_adaptive_sync_with_one_for_on(self):
        calls = self._apply("29", 1)
        self.assertIn(("jni_set", "g_video__dp_adaptive_sync", 1, 3, True), calls)
        self.assertNotIn(("jni_set", "g_video__freesync_switch", 3, 3, True), calls)

    def test_usbc_behaves_like_dp(self):
        calls = self._apply("30", 0)
        self.assertIn(("jni_set", "g_video__dp_adaptive_sync", 0, 3, True), calls)

    def test_helpers_map_sources_and_on_values(self):
        from mimonitor_toolbox import presets

        self.assertEqual(presets.freesync_jni_key("29"), "g_video__dp_adaptive_sync")
        self.assertEqual(presets.freesync_jni_key("30"), "g_video__dp_adaptive_sync")
        self.assertEqual(presets.freesync_jni_key("23"), "g_video__freesync_switch")
        self.assertEqual(presets.freesync_jni_key(" 29 "), "g_video__dp_adaptive_sync")
        self.assertEqual(presets.freesync_on_value("g_video__freesync_switch"), 3)
        self.assertEqual(presets.freesync_on_value("g_video__dp_adaptive_sync"), 1)
        self.assertTrue(presets.freesync_is_on("g_video__freesync_switch", "3"))
        self.assertFalse(presets.freesync_is_on("g_video__freesync_switch", "0"))
        self.assertTrue(presets.freesync_is_on("g_video__dp_adaptive_sync", 1))
        self.assertFalse(presets.freesync_is_on("g_video__freesync_switch", None))

    def test_legacy_preset_without_freesync_is_skipped_not_failed(self):
        """早于本功能的预设没记 FreeSync —— 不能猜，保持现状并说明。"""
        from mimonitor_toolbox import presets

        values = full_values()
        del values["freesync"]
        adb = FakeAdb()
        result = presets.apply_preset(adb, values)

        labels = [name for name, _ in result.skipped]
        self.assertIn("FreeSync", labels)
        self.assertNotIn("FreeSync", result.applied)
        touched = [c for c in adb.calls if c[0] == "jni_set"
                   and ("freesync" in str(c[1]) or "adaptive_sync" in str(c[1]))]
        self.assertEqual(touched, [], "没记录 FreeSync 时不该去动它的键")


if __name__ == "__main__":
    unittest.main()
