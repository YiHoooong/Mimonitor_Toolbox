import os
import unittest
from types import SimpleNamespace
from unittest import mock


class WindowsRuntimeTests(unittest.TestCase):
    """捕获 HDR 查询在非 Windows 崩溃和自启动路径漂移。"""

    def test_hdr_query_is_unavailable_off_windows(self):
        from mimonitor_toolbox import windows

        with mock.patch.object(windows.sys, "platform", "linux"):
            self.assertIsNone(windows.query_windows_hdr_enabled())

    def test_repeated_hdr_query_reuses_ctypes_pointer_types(self):
        from mimonitor_toolbox import windows

        class FailingCreateFactory:
            argtypes = None
            restype = None

            def __call__(self, *_args):
                return 1

        fake_dxgi = SimpleNamespace(CreateDXGIFactory1=FailingCreateFactory())
        pointer_cache = windows.ctypes._pointer_type_cache
        initial_size = len(pointer_cache) if hasattr(pointer_cache, "__len__") else None

        with mock.patch.object(windows.sys, "platform", "win32"), mock.patch.object(
            windows.ctypes,
            "WinDLL",
            return_value=fake_dxgi,
            create=True,
        ):
            for _ in range(4):
                self.assertIsNone(windows.query_windows_hdr_enabled())

        if initial_size is not None:
            self.assertEqual(len(pointer_cache), initial_size)
        self.assertIs(windows.ctypes.POINTER(windows._GUID), windows._GUID_POINTER)

    def test_hdr_selection_only_uses_the_target_display(self):
        from mimonitor_toolbox import windows

        outputs = [
            (r"\\.\DISPLAY1", False),
            (r"\\.\DISPLAY2", True),
        ]
        self.assertFalse(windows._select_hdr_output_state(outputs, r"\\.\DISPLAY1"))
        self.assertTrue(windows._select_hdr_output_state(outputs, r"\\.\DISPLAY2"))
        # 指定了却找不到就返回 None，不退回"任意一块开着 HDR 的屏"
        self.assertIsNone(windows._select_hdr_output_state(outputs, r"\\.\DISPLAY3"))

    def test_target_resolution_does_not_substitute_the_second_screen(self):
        from mimonitor_toolbox import windows

        displays = [
            {"device_name": "DISPLAY1", "device_id": "XMI27B3-1", "label": "Mi Monitor"},
            {"device_name": "DISPLAY2", "device_id": "OTHER-2", "label": "Other"},
        ]
        self.assertEqual(windows.resolve_hdr_target_display(displays), displays[0])
        self.assertIsNone(windows.resolve_hdr_target_display(displays, "missing"))
        self.assertIsNone(windows.resolve_hdr_target_display(displays[1:] + [
            {"device_name": "DISPLAY3", "device_id": "OTHER-3", "label": "Other"},
        ]))

    def test_hdr_query_walks_dxgi_vtables_and_releases_interfaces(self):
        from mimonitor_toolbox import windows

        method_calls = []
        release_calls = []

        class CreateFactory:
            argtypes = None
            restype = None

            def __call__(self, _iid, factory_out):
                windows.ctypes.cast(
                    factory_out,
                    windows._VOID_POINTER_POINTER,
                ).contents.value = 1001
                return 0

        def set_void_pointer(pointer_out, value):
            windows.ctypes.cast(
                pointer_out,
                windows._VOID_POINTER_POINTER,
            ).contents.value = value

        def com_method(pointer, index, _prototype):
            method_calls.append((pointer, index))
            if (pointer, index) == (1001, 12):
                def enum_adapters(_this, item_index, adapter_out):
                    if item_index:
                        return windows.DXGI_ERROR_NOT_FOUND
                    set_void_pointer(adapter_out, 2001)
                    return 0

                return enum_adapters
            if (pointer, index) == (2001, 7):
                def enum_outputs(_this, item_index, output_out):
                    if item_index:
                        return windows.DXGI_ERROR_NOT_FOUND
                    set_void_pointer(output_out, 3001)
                    return 0

                return enum_outputs
            if (pointer, index) == (3001, 0):
                def query_interface(_this, _iid, output6_out):
                    set_void_pointer(output6_out, 4001)
                    return 0

                return query_interface
            if (pointer, index) == (4001, 27):
                def get_desc1(_this, desc_out):
                    desc = windows.ctypes.cast(
                        desc_out,
                        windows._DXGI_OUTPUT_DESC1_POINTER,
                    ).contents
                    desc.AttachedToDesktop = 1
                    desc.ColorSpace = (
                        windows.DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020
                    )
                    return 0

                return get_desc1
            self.fail(f"unexpected COM method: {(pointer, index)}")

        fake_dxgi = SimpleNamespace(CreateDXGIFactory1=CreateFactory())
        with mock.patch.object(windows.sys, "platform", "win32"), mock.patch.object(
            windows.ctypes,
            "WinDLL",
            return_value=fake_dxgi,
            create=True,
        ), mock.patch.object(
            windows,
            "_make_guid",
            side_effect=lambda _value: windows._GUID(),
        ), mock.patch.object(windows, "_com_method", side_effect=com_method), mock.patch.object(
            windows,
            "_release_com",
            side_effect=release_calls.append,
        ):
            self.assertTrue(windows.query_windows_hdr_enabled())

        self.assertEqual(
            method_calls,
            [(1001, 12), (2001, 7), (3001, 0), (4001, 27)],
        )
        self.assertEqual(release_calls, [4001, 3001, 2001, 1001])

    def test_autostart_path_uses_windows_startup_folder(self):
        from mimonitor_toolbox import windows

        roaming = r"C:\Users\tester\AppData\Roaming"
        with mock.patch.dict(os.environ, {"APPDATA": roaming}, clear=False):
            path = windows.get_autostart_path()

        self.assertEqual(
            path,
            os.path.join(
                roaming,
                r"Microsoft\Windows\Start Menu\Programs\Startup",
                "RedmiToolbox.bat",
            ),
        )

    def test_power_broadcast_uses_automatic_resume_as_the_single_trigger(self):
        from mimonitor_toolbox import windows

        dispatch = getattr(windows, "dispatch_power_broadcast", None)
        self.assertIsNotNone(dispatch)

        resume_events = []
        self.assertTrue(dispatch(0x0218, 0x0012, lambda: resume_events.append("automatic")))
        self.assertFalse(dispatch(0x0218, 0x0007, lambda: resume_events.append("user-present")))
        self.assertFalse(dispatch(0x0218, 0x0006, lambda: resume_events.append("critical")))
        self.assertFalse(dispatch(0x0218, 0x0004, lambda: resume_events.append("suspend")))
        self.assertFalse(dispatch(0x001A, 0x0012, lambda: resume_events.append("other")))
        self.assertEqual(resume_events, ["automatic"])


class _FakeEnumDisplayDevices:
    """够用的 EnumDisplayDevicesW 替身：适配器枚举 + 按适配器枚举显示器。"""

    def __init__(self, adapters, monitors):
        self.argtypes = None
        self.restype = None
        self._adapters = adapters
        self._monitors = monitors

    def __call__(self, lpDevice, iDevNum, lpDisplayDevice, dwFlags):
        device = lpDisplayDevice._obj
        if lpDevice is None:
            if iDevNum >= len(self._adapters):
                return 0
            name, string, flags = self._adapters[iDevNum]
            device.DeviceName = name
            device.DeviceString = string
            device.StateFlags = flags
            return 1
        entries = self._monitors.get(str(lpDevice), [])
        if iDevNum >= len(entries):
            return 0
        device_id, device_string = entries[iDevNum]
        device.DeviceName = f"{lpDevice}\\Monitor{iDevNum}"
        device.DeviceID = device_id
        device.DeviceString = device_string
        device.StateFlags = 0x1
        return 1


def _monitor_entry(code, uid, name="Generic PnP Monitor"):
    return (rf"\\?\DISPLAY#{code}#5&mock&0&UID{uid}#{{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}}", name)


class HdrTargetDetectionTests(unittest.TestCase):
    """自动识别按 EDID 厂商码匹配：不能写死型号（2025 款 XMI27B3 / 2026 款 XMI3009）。"""

    def _display(self, code, uid, name="Monitor"):
        return {"device_name": rf"\\.\DISPLAY{uid}", "device_id": _monitor_entry(code, uid)[0],
                "label": name}

    def test_matches_both_2025_and_2026_product_codes(self):
        from mimonitor_toolbox import windows

        for code in ("XMI3009", "XMI27B3"):
            with self.subTest(code=code):
                displays = [self._display(code, 1), self._display("ZAKO99", 2)]
                self.assertIs(windows.resolve_hdr_target_display(displays), displays[0])

    def test_non_xiaomi_screens_are_not_matched(self):
        from mimonitor_toolbox import windows

        displays = [self._display("ZAKO99", 1), self._display("ACME01", 2)]
        self.assertIsNone(windows.resolve_hdr_target_display(displays))

    def test_two_xiaomi_screens_are_not_guessed(self):
        from mimonitor_toolbox import windows

        displays = [self._display("XMI3009", 1), self._display("XMI27B3", 2)]
        self.assertIsNone(windows.resolve_hdr_target_display(displays))

    def test_single_non_matching_screen_still_falls_back(self):
        from mimonitor_toolbox import windows

        displays = [self._display("ZAKO99", 1)]
        self.assertIs(windows.resolve_hdr_target_display(displays), displays[0])

    def test_manual_selection_wins(self):
        from mimonitor_toolbox import windows

        displays = [self._display("XMI3009", 1), self._display("ZAKO99", 2)]
        self.assertIs(windows.resolve_hdr_target_display(displays, displays[1]["device_id"]),
                      displays[1])
        # 选定的屏不在了也不改用别的屏
        self.assertIsNone(windows.resolve_hdr_target_display(displays, "missing"))

    def test_product_code_parsing(self):
        from mimonitor_toolbox import windows

        self.assertEqual(windows.display_product_code(_monitor_entry("XMI3009", 1)[0]), "XMI3009")
        self.assertEqual(windows.display_product_code(""), "")
        self.assertEqual(windows.display_product_code(None), "")
        self.assertEqual(windows.display_product_code(r"\\.\DISPLAY1"), "")

    def test_list_windows_displays_labels_carry_the_product_code(self):
        from mimonitor_toolbox import windows

        fake = _FakeEnumDisplayDevices(
            adapters=[(r"\\.\DISPLAY1", "Intel(R) UHD Graphics", 0x1),
                      (r"\\.\DISPLAY2", "Zako Virtual Display", 0x0)],  # 未接入桌面，应跳过
            monitors={
                r"\\.\DISPLAY1": [_monitor_entry("XMI3009", 4352)],
                r"\\.\DISPLAY2": [_monitor_entry("ZAKO99", 9999, "Zako Virtual Display")],
            },
        )
        with mock.patch.object(windows.sys, "platform", "win32"), \
                mock.patch.object(windows, "user32", mock.Mock(EnumDisplayDevicesW=fake)):
            displays = windows.list_windows_displays()

        self.assertEqual(len(displays), 1)
        self.assertEqual(displays[0]["device_name"], r"\\.\DISPLAY1")
        self.assertIn("XMI3009", displays[0]["device_id"])
        # 占位名 "Generic PnP Monitor" 被丢掉，产品码才是能区分屏幕的那一段
        self.assertEqual(displays[0]["label"], "XMI3009 · 屏幕 1")

    def test_generic_monitor_names_are_dropped_from_labels(self):
        from mimonitor_toolbox import windows

        device_id = _monitor_entry("XMI3009", 4352)[0]
        for generic in ("Generic PnP Monitor", "generic pnp monitor", "Default Monitor",
                        "通用即插即用监视器", "显示器", "   ", None):
            with self.subTest(name=generic):
                self.assertEqual(windows._display_label(generic, device_id, "屏幕 1"),
                                 "XMI3009 · 屏幕 1")
        # 真名要留着
        self.assertEqual(windows._display_label("G Pro 27U", device_id, "屏幕 1"),
                         "G Pro 27U · XMI3009 · 屏幕 1")
        # 产品码解析不到时也不能剩下空段
        self.assertEqual(windows._display_label("Generic PnP Monitor", "", "屏幕 2"), "屏幕 2")

    def test_two_monitors_on_one_adapter_get_distinct_slot_labels(self):
        """一个适配器下挂多台 monitor 时槽位名不能重号（真机上出现过两条「屏幕 1」）。"""
        from mimonitor_toolbox import windows

        fake = _FakeEnumDisplayDevices(
            adapters=[(r"\\.\DISPLAY1", "Intel(R) UHD Graphics", 0x1)],
            monitors={r"\\.\DISPLAY1": [_monitor_entry("XMI3009", 4352),
                                        _monitor_entry("ZAKO99", 9999, "Zako Virtual Display")]},
        )
        with mock.patch.object(windows.sys, "platform", "win32"), \
                mock.patch.object(windows, "user32", mock.Mock(EnumDisplayDevicesW=fake)):
            displays = windows.list_windows_displays()

        self.assertEqual(len(displays), 2)
        labels = [item["label"] for item in displays]
        self.assertEqual(len(set(labels)), 2, labels)
        self.assertEqual(labels[0], "XMI3009 · 屏幕 1")
        self.assertEqual(labels[1], "Zako Virtual Display · ZAKO99 · 屏幕 1-2")

    def test_query_target_display_refresh_rate_reads_resolved_xiaomi_screen(self):
        from mimonitor_toolbox import windows

        fake_devmode_calls = []

        def fake_enum_settings(device_name, mode_num, lp_devmode):
            fake_devmode_calls.append((device_name, int(mode_num) & 0xFFFFFFFF))
            dm = lp_devmode._obj
            if device_name == r"\\.\DISPLAY2":
                dm.dmDisplayFrequency = 160
                return 1
            if device_name == r"\\.\DISPLAY3":
                dm.dmDisplayFrequency = 1
                return 1
            return 0

        fake_devices = _FakeEnumDisplayDevices(
            adapters=[
                (r"\\.\DISPLAY1", "Notebook Display", 0x1),
                (r"\\.\DISPLAY2", "External Mi Monitor", 0x1),
            ],
            monitors={
                r"\\.\DISPLAY1": [_monitor_entry("BOE0D16", 1001)],
                r"\\.\DISPLAY2": [_monitor_entry("XMI3009", 4352)],
            },
        )
        fake_user32 = mock.Mock(
            EnumDisplayDevicesW=fake_devices,
            EnumDisplaySettingsW=fake_enum_settings,
        )
        with mock.patch.object(windows.sys, "platform", "win32"), \
                mock.patch.object(windows, "user32", fake_user32):
            self.assertEqual(windows.query_target_display_refresh_rate(), 160)
            self.assertIsNone(windows.query_windows_display_refresh_rate(r"\\.\DISPLAY3"))
            self.assertIsNone(windows.query_windows_display_refresh_rate(""))

        self.assertEqual(fake_devmode_calls[0], (r"\\.\DISPLAY2", 0xFFFFFFFF))

    def test_query_target_display_refresh_rate_returns_none_when_ambiguous(self):
        from mimonitor_toolbox import windows

        fake_devices = _FakeEnumDisplayDevices(
            adapters=[
                (r"\\.\DISPLAY1", "Monitor A", 0x1),
                (r"\\.\DISPLAY2", "Monitor B", 0x1),
            ],
            monitors={
                r"\\.\DISPLAY1": [_monitor_entry("XMI3009", 1)],
                r"\\.\DISPLAY2": [_monitor_entry("XMI27B3", 2)],
            },
        )
        fake_enum_settings = mock.Mock(return_value=1)
        with mock.patch.object(windows.sys, "platform", "win32"), \
                mock.patch.object(windows, "user32", mock.Mock(
                    EnumDisplayDevicesW=fake_devices,
                    EnumDisplaySettingsW=fake_enum_settings,
                )):
            self.assertIsNone(windows.query_target_display_refresh_rate())
        fake_enum_settings.assert_not_called()


if __name__ == "__main__":
    unittest.main()
