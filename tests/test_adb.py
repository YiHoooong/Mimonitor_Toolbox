import threading
import unittest
from unittest import mock


class AdbModuleTests(unittest.TestCase):
    """捕获私有 server 端口丢失和 MiTV 型号识别失效。"""

    def setUp(self):
        from mimonitor_toolbox import adb as adb_runtime

        adb_runtime.clear_ignored_scan_ips()

    def tearDown(self):
        from mimonitor_toolbox import adb as adb_runtime

        adb_runtime.clear_ignored_scan_ips()

    def test_adb_command_uses_private_server_port(self):
        from mimonitor_toolbox import adb as adb_runtime

        with mock.patch.object(adb_runtime, "ADB", "adb-test"), \
                mock.patch.object(adb_runtime, "ADB_SERVER_PORT", "5038"):
            command = adb_runtime.adb_command(["version"])

        self.assertEqual(command, ["adb-test", "-P", "5038", "version"])

    def test_mitv_matching_is_case_insensitive(self):
        from mimonitor_toolbox import adb as adb_runtime

        self.assertTrue(adb_runtime.is_mitv_model("xiaomi mitv pro"))
        self.assertFalse(adb_runtime.is_mitv_model("Android TV"))

    def test_scan_without_physical_network_raises_domain_error(self):
        from mimonitor_toolbox import adb as adb_runtime

        with mock.patch.object(adb_runtime, "get_windows_scan_networks", return_value=[]):
            with self.assertRaises(adb_runtime.WindowsAdapterError):
                adb_runtime.scan_adb()

    def test_adb_target_has_no_personal_default_and_formats_one_serial(self):
        from mimonitor_toolbox import adb as adb_runtime

        self.assertEqual(adb_runtime.Adb().serial, "")
        self.assertEqual(
            adb_runtime.Adb("192.168.1.9").serial,
            "192.168.1.9:5555",
        )

    def test_mtk_command_builder_preserves_tvservice_escaping(self):
        from mimonitor_toolbox import adb as adb_runtime

        command = adb_runtime.build_tvservice_app_process_command(
            "/data/data/mitv.service/cache/MtkDirectTool.jar",
            ["MtkDirectTool", "set", "g_picture_mode", 7, 3],
        )

        self.assertEqual(
            command,
            'service call TvService 3 s16 "sh -c eval\\${IFS}'
            'CLASSPATH=/data/data/mitv.service/cache/MtkDirectTool.jar'
            '\\${IFS}/system/bin/app_process'
            '\\${IFS}/data/data/mitv.service/cache'
            '\\${IFS}MtkDirectTool\\${IFS}set'
            '\\${IFS}g_picture_mode\\${IFS}7\\${IFS}3"',
        )

    def test_jni_batch_parser_ignores_markers_and_error_values(self):
        from mimonitor_toolbox import adb as adb_runtime

        output = """\
__MIMONITOR_BATCH_BEGIN__
picture_mode=7
bad=ERROR: unavailable
label=cinema
malformed
__MIMONITOR_BATCH_END__
"""

        self.assertEqual(
            adb_runtime.parse_jni_batch_output(output),
            {"picture_mode": 7, "label": "cinema"},
        )

    def test_jar_healing_pushes_mismatched_file_and_copies_it(self):
        from mimonitor_toolbox import adb as adb_runtime

        target = adb_runtime.Adb("192.168.1.9")
        shell_calls = []

        def shell(command, check=False):
            shell_calls.append((command, check))
            return "1200" if command.startswith("stat -c") else ""

        with mock.patch.object(target, "shell", side_effect=shell), \
                mock.patch.object(adb_runtime, "get_colorful_led_tool_path", return_value="/tmp/ColorfulLedTool.jar"), \
                mock.patch.object(adb_runtime.os.path, "getsize", return_value=1300), \
                mock.patch.object(adb_runtime, "adb_run") as run:
            healed = target.check_and_heal_colorful_led_tool(check=True)

        self.assertTrue(healed)
        run.assert_called_once_with(
            [
                "-s", "192.168.1.9:5555", "push",
                "/tmp/ColorfulLedTool.jar", "/sdcard/ColorfulLedTool.jar",
            ],
            check=True,
        )
        self.assertEqual(
            shell_calls,
            [
                (
                    "stat -c %s /sdcard/ColorfulLedTool.jar "
                    "2>/dev/null || echo 0",
                    True,
                ),
                (
                    # 必须走 sh -c eval + ${IFS}：runSystemCommand 是
                    # Runtime.exec(String)，按空白切分且不起 shell，
                    # 用真实空格会让 sh -c 只拿到被截断的第一个词而静默失败。
                    "service call TvService 3 s16 \"sh -c eval\\${IFS}cp"
                    "\\${IFS}/sdcard/ColorfulLedTool.jar"
                    "\\${IFS}/data/data/mitv.service/cache/ColorfulLedTool.jar\"",
                    True,
                ),
            ],
        )

    def test_async_run_reports_uncaught_worker_exception(self):
        from mimonitor_toolbox import adb as adb_runtime

        reported = []
        report_ready = threading.Event()

        def report(error):
            reported.append(error)
            report_ready.set()

        def fail_in_worker():
            raise RuntimeError("worker exploded")

        adb_runtime.set_async_error_handler(report)
        try:
            worker = adb_runtime.async_run(fail_in_worker)
            worker.join(timeout=1)
        finally:
            adb_runtime.set_async_error_handler(None)

        self.assertTrue(report_ready.is_set())
        self.assertEqual(len(reported), 1)
        self.assertIsInstance(reported[0], RuntimeError)
        self.assertEqual(str(reported[0]), "worker exploded")

    def test_persistent_runtime_failure_falls_back_and_records_warning(self):
        from mimonitor_toolbox import adb as adb_runtime

        adb_runtime.drain_startup_warnings()
        with mock.patch.object(adb_runtime.sys, "platform", "win32"), \
                mock.patch.object(adb_runtime.sys, "frozen", True, create=True), \
                mock.patch.object(adb_runtime, "get_app_data_dir", return_value="C:/app-data"), \
                mock.patch.object(adb_runtime.os, "makedirs", side_effect=PermissionError("denied")):
            selected = adb_runtime.ensure_persistent_adb_runtime("C:/bundle/adb.exe")

        self.assertEqual(selected, "C:/bundle/adb.exe")
        warnings = adb_runtime.drain_startup_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertIn("ADB 运行时准备失败", warnings[0])
        self.assertIn("denied", warnings[0])


class ConnectByDeviceStateTests(unittest.TestCase):
    """connect 输出为空（客户端超时被杀）时以 get-state 为唯一裁决。"""

    def _patch_adb_run(self, connect_output, states):
        """states: get-state 依次返回的值列表（每次调用消耗一个）。"""
        from mimonitor_toolbox import adb as adb_runtime
        state_outputs = list(states)
        calls = []

        def fake_adb_run(args, timeout=10, check=False):
            calls.append(list(args))
            if args[0] == "connect":
                return connect_output
            if "get-state" in args:
                return state_outputs.pop(0) if state_outputs else "unknown"
            return ""

        return mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run), calls

    def test_manual_connect_succeeds_on_empty_output_with_device_state(self):
        from mimonitor_toolbox import adb as adb_runtime

        # connect 输出为空 + 首次 get-state 即 device（server 已建好 transport）
        patcher, calls = self._patch_adb_run("", ["device"])
        with patcher:
            ok = adb_runtime.Adb("192.168.1.9").connect()

        self.assertTrue(ok)
        get_state_called = any("get-state" in c for c in calls)
        self.assertTrue(get_state_called)

    def test_manual_connect_succeeds_after_empty_connect_output_then_device(self):
        from mimonitor_toolbox import adb as adb_runtime

        # connect 空输出，首次 get-state 失败、connect 后再查为 device
        patcher, _ = self._patch_adb_run("", ["offline", "device"])
        with patcher:
            ok = adb_runtime.Adb("192.168.1.9").connect()

        self.assertTrue(ok)

    def test_manual_connect_fails_on_empty_output_and_bad_state(self):
        from mimonitor_toolbox import adb as adb_runtime

        patcher, _ = self._patch_adb_run("", ["offline", "offline"])
        with patcher:
            ok = adb_runtime.Adb("192.168.1.9").connect()

        self.assertFalse(ok)

    def _scan_with(self, state_output, model_output):
        import ipaddress

        from mimonitor_toolbox import adb as adb_runtime
        networks = [mock.Mock(ip=ipaddress.ip_address("192.168.1.0"), netmask="255.255.255.0")]
        probe_results = [mock.Mock(ip=ipaddress.ip_address("192.168.1.9"))]

        def fake_adb_run(args, timeout=10, check=False):
            if args[0] == "connect":
                return ""  # 客户端超时被杀，空输出
            if "get-state" in args:
                return state_output
            if "getprop ro.product.model" in args:
                return model_output
            return ""

        with mock.patch.object(adb_runtime, "get_windows_scan_networks", return_value=networks), \
                mock.patch.object(adb_runtime, "build_probe_targets", return_value=probe_results), \
                mock.patch.object(adb_runtime, "probe_tcp_targets", return_value=probe_results), \
                mock.patch.object(adb_runtime, "ensure_adb_server", return_value=True), \
                mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run):
            return adb_runtime.scan_adb()

    def test_scan_discovers_device_with_empty_connect_output(self):
        self.assertEqual(
            self._scan_with("device", "xiaomi mitv pro"),
            [("192.168.1.9", "xiaomi mitv pro")],
        )

    def test_scan_skips_device_with_empty_output_and_offline_state(self):
        self.assertEqual(self._scan_with("offline", ""), [])

    def test_scan_adb_caches_unauthorized_and_non_mitv_ips_until_cleared(self):
        import ipaddress
        from mimonitor_toolbox import adb as adb_runtime

        networks = [mock.Mock(ip=ipaddress.ip_address("192.168.1.0"), netmask="255.255.255.0")]
        ip_unauth = ipaddress.ip_address("192.168.1.50")
        ip_phone = ipaddress.ip_address("192.168.1.60")
        ip_mitv = ipaddress.ip_address("192.168.1.70")
        all_targets = [
            mock.Mock(ip=ip_unauth),
            mock.Mock(ip=ip_phone),
            mock.Mock(ip=ip_mitv),
        ]
        probe_target_batches = []
        connect_calls = []
        connected_serials = set()

        def fake_probe_tcp_targets(targets, _cancel_event, log=None):
            batch = list(targets)
            probe_target_batches.append([str(t.ip) for t in batch])
            return batch

        def fake_adb_run(args, timeout=10, check=False):
            if args[0] == "connect":
                connect_calls.append(args[1])
                connected_serials.add(args[1])
                return f"connected to {args[1]}"
            if "get-state" in args:
                serial = args[1]
                if serial not in connected_serials:
                    return f"error: device '{serial}' not found"
                if serial.startswith("192.168.1.50"):
                    return "unauthorized"
                return "device"
            if "getprop ro.product.model" in args:
                serial = args[1]
                if serial.startswith("192.168.1.60"):
                    return "Pixel 8"
                if serial.startswith("192.168.1.70"):
                    return "MiTV-MFFU1"
            return ""

        with mock.patch.object(adb_runtime, "get_windows_scan_networks", return_value=networks), \
                mock.patch.object(adb_runtime, "build_probe_targets", return_value=list(all_targets)), \
                mock.patch.object(adb_runtime, "probe_tcp_targets", side_effect=fake_probe_tcp_targets), \
                mock.patch.object(adb_runtime, "ensure_adb_server", return_value=True), \
                mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run):
            first = adb_runtime.scan_adb()
            self.assertEqual(first, [("192.168.1.60", "Pixel 8"), ("192.168.1.70", "MiTV-MFFU1")])
            self.assertEqual(
                adb_runtime.get_ignored_scan_ips(),
                {"192.168.1.50", "192.168.1.60"},
            )
            self.assertEqual(
                connect_calls,
                ["192.168.1.50:5555", "192.168.1.60:5555", "192.168.1.70:5555"],
            )

            connect_calls.clear()
            connected_serials.clear()
            second = adb_runtime.scan_adb()
            self.assertEqual(second, [("192.168.1.70", "MiTV-MFFU1")])
            self.assertEqual(probe_target_batches[-1], ["192.168.1.70"])
            self.assertEqual(connect_calls, ["192.168.1.70:5555"])

            adb_runtime.clear_ignored_scan_ips()
            connect_calls.clear()
            connected_serials.clear()
            third = adb_runtime.scan_adb()
            self.assertEqual(len(third), 2)
            self.assertEqual(
                probe_target_batches[-1],
                ["192.168.1.50", "192.168.1.60", "192.168.1.70"],
            )
            self.assertEqual(
                connect_calls,
                ["192.168.1.50:5555", "192.168.1.60:5555", "192.168.1.70:5555"],
            )

    def test_reconnect_disconnects_then_ensures_device_state(self):
        from mimonitor_toolbox import adb as adb_runtime

        # reconnect = disconnect 清 stale transport + ensure_connected
        state_outputs = ["offline", "device"]
        calls = []

        def fake_adb_run(args, timeout=10, check=False):
            calls.append(list(args))
            if args[0] == "disconnect":
                return "disconnected"
            if args[0] == "connect":
                return "connected to 192.168.1.9:5555"
            if "get-state" in args:
                return state_outputs.pop(0) if state_outputs else "unknown"
            return ""

        with mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run):
            ok, state = adb_runtime.Adb("192.168.1.9").reconnect()

        self.assertTrue(ok)
        self.assertEqual(state, "device")
        self.assertEqual(calls, [
            ["disconnect", "192.168.1.9:5555"],
            ["-s", "192.168.1.9:5555", "get-state"],
            ["connect", "192.168.1.9:5555"],
            ["-s", "192.168.1.9:5555", "get-state"],
        ])

    def test_reconnect_fails_when_state_stays_offline(self):
        from mimonitor_toolbox import adb as adb_runtime

        def fake_adb_run(args, timeout=10, check=False):
            if args[0] == "disconnect":
                return "disconnected"
            if args[0] == "connect":
                return "connected to 192.168.1.9:5555"
            if "get-state" in args:
                return "offline"
            return ""

        with mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run):
            ok, state = adb_runtime.Adb("192.168.1.9").reconnect()

        self.assertFalse(ok)
        self.assertEqual(state, "offline")

    def test_set_sound_output_uses_tvservice_without_settings_put(self):
        from mimonitor_toolbox import adb as adb_runtime

        calls = []

        def fake_shell(command, check=False):
            calls.append((command, check))
            return "Result: Parcel(00000000    '....')"

        client = adb_runtime.Adb("192.168.1.9")
        with mock.patch.object(client, "shell", side_effect=fake_shell):
            client.set_sound_output(8, check=True)

        self.assertEqual(len(calls), 1)
        command, check = calls[0]
        self.assertTrue(check)
        self.assertIn("service call TvService 3 s16", command)
        self.assertIn("MtkDirectTool", command)
        self.assertIn("setSoundOutput", command)
        self.assertIn("8", command)
        self.assertNotIn("settings put", command)

    def test_service_call_helpers_parse_int_and_detect_errors(self):
        from mimonitor_toolbox import adb as adb_runtime

        self.assertTrue(adb_runtime.service_call_succeeded("Result: Parcel(00000000 00000018   '........')"))
        self.assertFalse(adb_runtime.service_call_succeeded("Parcel: Exception occurred"))
        self.assertEqual(
            adb_runtime.parse_service_call_int("Result: Parcel(\t00000000 00000019   '........')"),
            25,
        )
        # Void binder response (only status word) must not be treated as return value 0.
        self.assertIsNone(adb_runtime.parse_service_call_int("Result: Parcel(00000000    '....')"))

    def test_media_volume_reads_binder_then_falls_back_to_settings(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        with mock.patch.object(
            client,
            "shell",
            return_value="Result: Parcel(\t00000000 0000002a   '....*...')",
        ):
            self.assertEqual(client.media_volume(), 42)

        calls = []

        def fake_fallback(cmd, check=False):
            calls.append(cmd)
            if cmd.startswith("service call audio 22"):
                return "Result: Parcel(00000000    '....')"
            return "19"

        with mock.patch.object(client, "shell", side_effect=fake_fallback):
            self.assertEqual(client.media_volume(), 19)
        self.assertEqual(
            calls,
            ["service call audio 22 i32 3", "settings get system volume_music_speaker"],
        )

    def test_set_media_volume_verifies_binder_and_falls_back_to_keyevent(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        commands = []

        def fake_binder_ok(cmd, check=False):
            commands.append(cmd)
            if cmd.startswith("service call audio 17"):
                return "Result: Parcel(00000000 00000000   '........')"
            if cmd.startswith("service call audio 22"):
                return "Result: Parcel(00000000 0000001e   '........')"
            return "Result: Parcel(00000000    '....')"

        with mock.patch.object(client, "shell", side_effect=fake_binder_ok):
            used_binder = client.set_media_volume(30, unmute_first=False, check=True)
            used_binder_unmute = client.set_media_volume(35, unmute_first=True, show_ui=True, check=True)

        self.assertTrue(used_binder)
        self.assertTrue(used_binder_unmute)
        self.assertTrue(client._media_volume_binder_verified)
        self.assertEqual(
            commands,
            [
                "service call audio 12 i32 3 i32 30 i32 0 s16 com.android.shell",
                "service call audio 22 i32 3",
                "service call audio 10 i32 3 i32 100 i32 0 s16 com.android.shell",
                "service call audio 12 i32 3 i32 35 i32 1 s16 com.android.shell",
            ],
        )

        client_fb = adb_runtime.Adb("192.168.1.9")
        fb_commands = []

        def fake_binder_fail(cmd, check=False):
            fb_commands.append(cmd)
            if cmd.startswith("service call audio 12"):
                return "Result: Parcel(Exception occurred)"
            if cmd.startswith("service call audio 22"):
                return "Result: Parcel(00000000    '....')"
            if cmd == "settings get system volume_music_speaker":
                return "10"
            return ""

        with mock.patch.object(client_fb, "shell", side_effect=fake_binder_fail):
            used_binder_fb = client_fb.set_media_volume(13)

        self.assertFalse(used_binder_fb)
        self.assertFalse(client_fb._media_volume_binder_verified)
        self.assertEqual(
            fb_commands[-1],
            "input keyevent KEYCODE_VOLUME_UP KEYCODE_VOLUME_UP KEYCODE_VOLUME_UP",
        )

    def test_set_media_muted_verifies_binder_and_falls_back_to_mute_key(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")

        def fake_ok(cmd, check=False):
            if cmd.startswith("service call audio 17"):
                return "Result: Parcel(00000000 00000001   '........')"
            return "Result: Parcel(00000000    '....')"

        with mock.patch.object(client, "shell", side_effect=fake_ok):
            self.assertTrue(client.set_media_muted(True))
        self.assertTrue(client._media_volume_binder_verified)

        client_fb = adb_runtime.Adb("192.168.1.9")
        cmds = []

        def fake_fail(cmd, check=False):
            cmds.append(cmd)
            return "Error: failed" if cmd.startswith("service call") else ""

        with mock.patch.object(client_fb, "shell", side_effect=fake_fail):
            self.assertFalse(client_fb.set_media_muted(True))
        self.assertEqual(cmds[-1], "input keyevent KEYCODE_VOLUME_MUTE")


if __name__ == "__main__":
    unittest.main()