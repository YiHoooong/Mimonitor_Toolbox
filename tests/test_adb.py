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

        try:
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
        finally:
            adb_runtime.clear_ignored_scan_ips()

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


class AdbDaemonModeTests(unittest.TestCase):
    """覆盖混合加速 (Daemon) 协议解析、批处理合批、PUT_DENIED 混合回退及降级逻辑。"""

    def test_daemon_mode_defaults_and_switching_clears_batch_state(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        self.assertEqual(client.connection_mode, "adb")
        self.assertFalse(client.is_daemon_mode())

        self.assertEqual(client.set_connection_mode("daemon"), "daemon")
        self.assertTrue(client.is_daemon_mode())
        client._batch_ops.append(("PUT", "brightness", "50"))
        client._batch_read_cache["brightness"] = "50"
        client._batch_jni_cache["g_video__freesync_switch"] = 3

        self.assertEqual(client.set_connection_mode("unknown"), "adb")
        self.assertFalse(client.is_daemon_mode())
        self.assertEqual(client._batch_ops, [])
        self.assertEqual(client._batch_read_cache, {})
        self.assertEqual(client._batch_jni_cache, {})

    def test_daemon_query_parses_settings_jni_flags_and_skips_errors(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        self.assertIsNone(client.daemon_query(settings_keys=("brightness",)))

        client.set_connection_mode("daemon")
        payloads = []

        def fake_req(payload, timeout=5.0):
            payloads.append(payload)
            return [
                "OK",
                "S brightness=65",
                "S missing=__ERR__:null",
                "J g_video__freesync_switch=3",
                "J bad_int=notanumber",
                "F volume=42",
                "F muted=1",
                "ELAPSED 14",
            ]

        with mock.patch.object(client, "ensure_daemon", return_value=True), \
                mock.patch.object(client, "_daemon_socket_request", side_effect=fake_req):
            snap = client.daemon_query(
                settings_keys=("brightness", "missing"),
                jni_keys=("g_video__freesync_switch", "bad_int", "bad;key"),
                flags=("volume", "muted"),
            )

        self.assertEqual(len(payloads), 1)
        self.assertNotIn("bad;key", payloads[0])
        self.assertEqual(snap["settings"], {"brightness": "65"})
        self.assertEqual(snap["jni"], {"g_video__freesync_switch": 3})
        self.assertEqual(snap["flags"], {"volume": "42", "muted": "1"})
        self.assertEqual(snap["elapsed_ms"], 14)

    def test_batch_writes_coalesces_refresh_pq_and_hits_prefetch_cache(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        sent_batches = []
        shell_calls = []

        def fake_query(settings_keys=(), jni_keys=(), flags=(), check=False):
            return {
                "settings": {"brightness": "70"},
                "jni": {"g_video__freesync_switch": 3},
                "flags": {},
                "elapsed_ms": 5,
            }

        def fake_batch(ops, check=False):
            sent_batches.append(list(ops))
            return True, None, [(idx, "OK", "") for idx in range(len(ops))]

        with mock.patch.object(client, "daemon_query", side_effect=fake_query), \
                mock.patch.object(client, "ensure_daemon", return_value=True), \
                mock.patch.object(client, "daemon_batch", side_effect=fake_batch), \
                mock.patch.object(client, "shell", side_effect=shell_calls.append):
            with client.batch_writes(
                prefetch_settings=("brightness",),
                prefetch_jni=("g_video__freesync_switch",),
            ):
                self.assertEqual(client.get("brightness"), "70")
                self.assertEqual(client.jni_get("g_video__freesync_switch"), "3")
                client.put("brightness", 80)
                client.refresh_pq()
                client.jni_set("g_video__freesync_switch", 3)
                client.refresh_pq()

        self.assertEqual(shell_calls, [])
        self.assertEqual(len(sent_batches), 1)
        self.assertEqual(
            sent_batches[0],
            [
                ("PUT", "brightness", "80"),
                ("JNI_SET", "g_video__freesync_switch", 3, 3),
                ("REFRESH_PQ",),
            ],
        )

    def test_put_denied_triggers_ordered_hybrid_fallback(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        client._batch_ops = [
            ("JNI_SET", "g_video__freesync_switch", "3", "3"),
            ("PUT", "brightness", "75"),
            ("PUT", "contrast", "55"),
            ("REFRESH_PQ",),
        ]
        trace = []

        def fake_batch(ops, check=False):
            trace.append(("batch", list(ops)))
            if any(op[0] == "PUT" for op in ops):
                return False, 1, [(0, "OK", "")]
            return True, None, [(idx, "OK", "") for idx in range(len(ops))]

        def fake_shell(cmd, check=False):
            trace.append(("shell", cmd))
            return ""

        with mock.patch.object(client, "ensure_daemon", return_value=True), \
                mock.patch.object(client, "daemon_batch", side_effect=fake_batch), \
                mock.patch.object(client, "shell", side_effect=fake_shell):
            self.assertTrue(client.flush_batch())

        self.assertFalse(client._daemon_put_supported)
        self.assertEqual(trace[0][0], "batch")
        self.assertEqual(trace[1][0], "shell")
        self.assertIn("settings put global brightness 75", trace[1][1])
        self.assertIn("settings put global contrast 55", trace[1][1])
        self.assertEqual(trace[2], ("batch", [("REFRESH_PQ",)]))

        # 后续批次直接跳过 Daemon PUT，按顺序切块混合下发
        trace.clear()
        client._batch_ops = [
            ("PUT", "gamma", "2"),
            ("JNI_SET", "g_video__dp_adaptive_sync", "1", "3"),
        ]
        with mock.patch.object(client, "ensure_daemon", return_value=True), \
                mock.patch.object(client, "daemon_batch", side_effect=fake_batch), \
                mock.patch.object(client, "shell", side_effect=fake_shell):
            self.assertTrue(client.flush_batch())
        self.assertEqual(trace[0][0], "shell")
        self.assertIn("settings put global gamma 2", trace[0][1])
        self.assertEqual(trace[1], ("batch", [("JNI_SET", "g_video__dp_adaptive_sync", "1", "3")]))

    def test_ensure_daemon_failure_falls_back_to_adb_shell(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        shell_cmds = []

        def fake_shell(cmd, check=False):
            shell_cmds.append(cmd)
            if cmd == "settings get global brightness":
                return "50"
            return ""

        with mock.patch.object(client, "ensure_daemon", return_value=False), \
                mock.patch.object(client, "shell", side_effect=fake_shell):
            self.assertEqual(client.get("brightness"), "50")
            client.put("brightness", 60)

        self.assertEqual(
            shell_cmds,
            ["settings get global brightness", "settings put global brightness 60"],
        )

    def test_ensure_daemon_uses_ifs_encoded_app_process_and_parses_put_cap(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        launch_cmds = []
        socket_calls = []

        def fake_forward():
            client._daemon_forward_port = 27891
            client._daemon_ip = client.ip
            return True

        def fake_heal():
            client._daemon_jar_healed_ip = client.ip

        def fake_shell(cmd, check=False):
            launch_cmds.append(cmd)
            return ""

        def fake_socket(payload, timeout=2.0):
            socket_calls.append(payload)
            if len(socket_calls) == 1:
                return None
            return ["OK MtkDirectTool v2.1 pid=123 uid=1000 put=0"]

        with mock.patch.object(adb_runtime.time, "sleep"), \
                mock.patch.object(client, "_setup_adb_forward", side_effect=fake_forward), \
                mock.patch.object(client, "check_and_heal_jar", side_effect=fake_heal), \
                mock.patch.object(client, "shell", side_effect=fake_shell), \
                mock.patch.object(client, "_daemon_socket_request", side_effect=fake_socket):
            ready = client.ensure_daemon()

        self.assertTrue(ready)
        self.assertTrue(client._daemon_ready)
        self.assertIs(client._daemon_put_supported, False)
        self.assertEqual(len(launch_cmds), 1)
        launch_cmd = launch_cmds[0]
        self.assertIn('s16 "sh -c eval\\${IFS}', launch_cmd)
        self.assertIn(
            "CLASSPATH=/data/data/mitv.service/cache/MtkDirectTool.jar"
            "\\${IFS}/system/bin/app_process"
            "\\${IFS}/data/data/mitv.service/cache"
            "\\${IFS}MtkDirectTool\\${IFS}daemon\\${IFS}27891",
            launch_cmd,
        )
        # eval 后的命令行体内不能包含未转义空格，否则 TvService Runtime.exec 会截断
        eval_body = launch_cmd.split("eval", 1)[1]
        self.assertNotIn(" ", eval_body)

    def test_coalesced_mixed_chunks_preserves_order_and_batches_contiguous_puts(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        client._daemon_put_supported = False
        client._batch_ops = [
            ("JNI_SET", "k1", "1", "3"),
            ("PUT", "brightness", "70"),
            ("PUT", "contrast", "50"),
            ("JNI_SET", "k2", "2", "3"),
            ("REFRESH_PQ",),
            ("SLEEP", "200"),
            ("PUT", "gamma", "1"),
            ("VOL_SET", "20"),
        ]
        trace = []

        def fake_shell(cmd, check=False):
            trace.append(("shell", cmd))
            return ""

        def fake_batch(ops, check=False):
            trace.append(("batch", list(ops)))
            return True, None, [(idx, "OK", "") for idx in range(len(ops))]

        with mock.patch.object(adb_runtime.time, "sleep") as sleep_mock, \
                mock.patch.object(client, "ensure_daemon", return_value=True), \
                mock.patch.object(client, "shell", side_effect=fake_shell), \
                mock.patch.object(client, "daemon_batch", side_effect=fake_batch):
            self.assertTrue(client.flush_batch())

        self.assertEqual(trace[0], ("batch", [("JNI_SET", "k1", "1", "3")]))
        self.assertEqual(trace[1][0], "shell")
        self.assertIn("settings put global brightness 70 && settings put global contrast 50", trace[1][1])
        self.assertEqual(
            trace[2],
            ("batch", [("JNI_SET", "k2", "2", "3"), ("REFRESH_PQ",)]),
        )
        sleep_mock.assert_called_once_with(0.2)
        self.assertEqual(trace[3][0], "shell")
        self.assertIn("settings put global gamma 1", trace[3][1])
        self.assertEqual(trace[4], ("batch", [("VOL_SET", "20")]))

    def test_ensure_daemon_rebuilds_dropped_forward_after_reconnect_same_ip(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        # 模拟断开重连同 IP 后：旧 forward 端口与 _daemon_jar_healed_ip 仍缓存，但远端隧道已断
        client._daemon_ip = "192.168.1.9"
        client._daemon_forward_port = 50001
        client._daemon_jar_healed_ip = "192.168.1.9"
        client._daemon_ready = True

        forward_calls = []
        launch_calls = []

        def fake_forward():
            forward_calls.append(True)
            client._daemon_forward_port = 50002
            client._daemon_ip = client.ip
            return 50002

        def fake_socket(payload, timeout=2.0):
            # 旧端口 50001 不可达，重建到 50002 后远端常驻 Daemon 立即可响应
            if client._daemon_forward_port == 50001:
                return None
            return ["OK MtkDirectTool v2.1 pid=777 uid=1000 put=0"]

        with mock.patch.object(client, "_setup_adb_forward", side_effect=fake_forward), \
                mock.patch.object(client, "_daemon_socket_request", side_effect=fake_socket), \
                mock.patch.object(client, "shell", side_effect=lambda cmd, **_: launch_calls.append(cmd)):
            ready = client.ensure_daemon()

        self.assertTrue(ready)
        self.assertEqual(len(forward_calls), 1)
        self.assertEqual(client._daemon_forward_port, 50002)
        self.assertEqual(launch_calls, [])

    def test_ensure_daemon_cooldown_avoids_repeated_timeout_waits_until_reset(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        launch_calls = []

        def fake_forward():
            client._daemon_forward_port = 27891
            client._daemon_ip = client.ip
            return 27891

        with mock.patch.object(adb_runtime.time, "sleep"), \
                mock.patch.object(client, "_setup_adb_forward", side_effect=fake_forward), \
                mock.patch.object(client, "check_and_heal_jar", side_effect=lambda: setattr(client, "_daemon_jar_healed_ip", client.ip) or True), \
                mock.patch.object(client, "_daemon_socket_request", return_value=None), \
                mock.patch.object(client, "shell", side_effect=lambda cmd, **_: launch_calls.append(cmd) or ""):
            self.assertFalse(client.ensure_daemon())
            self.assertEqual(len(launch_calls), 1)
            # 冷却期内直接短路，不再重复执行 2.35s 握手
            self.assertFalse(client.ensure_daemon())
            self.assertEqual(len(launch_calls), 1)
            # 重置模式或重连后清除冷却，允许再次尝试
            client.set_connection_mode("daemon")
            self.assertFalse(client.ensure_daemon())
            self.assertEqual(len(launch_calls), 2)

    def test_ensure_daemon_aborts_immediately_on_device_not_found(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")

        def fake_forward():
            client._daemon_forward_port = 27892
            client._daemon_ip = client.ip
            return 27892

        with mock.patch.object(adb_runtime.time, "sleep") as sleep_mock, \
                mock.patch.object(client, "_setup_adb_forward", side_effect=fake_forward), \
                mock.patch.object(client, "check_and_heal_jar", side_effect=lambda: setattr(client, "_daemon_jar_healed_ip", client.ip) or True), \
                mock.patch.object(client, "_daemon_socket_request", return_value=None) as sock_mock, \
                mock.patch.object(client, "shell", return_value="error: device '192.168.1.9:5555' not found"):
            self.assertFalse(client.ensure_daemon())

        sleep_mock.assert_not_called()
        # 仅包含启动前的一次复用探测，启动返回断连后不得继续轮询 PING
        self.assertEqual(sock_mock.call_count, 1)

    def test_adb_run_disconnect_preempts_command_lock(self):
        import threading
        from mimonitor_toolbox import adb as adb_runtime

        entered = threading.Event()
        release = threading.Event()
        done = threading.Event()
        proc_mock = mock.Mock()
        proc_mock.communicate.return_value = ("disconnected 192.168.1.9:5555", "")
        proc_mock.returncode = 0

        def hold_lock():
            with adb_runtime._adb_command_lock:
                entered.set()
                release.wait(timeout=2.0)

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        self.assertTrue(entered.wait(timeout=1.0))
        try:
            with mock.patch.object(adb_runtime.subprocess, "Popen", return_value=proc_mock):
                worker = threading.Thread(
                    target=lambda: (adb_runtime.adb_run(["disconnect", "192.168.1.9:5555"]), done.set()),
                    daemon=True,
                )
                worker.start()
                self.assertTrue(done.wait(timeout=1.0))
        finally:
            release.set()
            holder.join(timeout=1.0)


class AdbSafetyAndProtocolFixesTests(unittest.TestCase):
    """Covers TCP END framing, token validation, daemon ERR fallback, locking, and scan_adb safety."""

    def _run_loopback_server(self, response_bytes):
        import socket
        import threading

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        received = []

        def serve():
            try:
                conn, _ = srv.accept()
                with conn:
                    conn.settimeout(2.0)
                    chunks = []
                    while True:
                        data = conn.recv(4096)
                        if not data:
                            break
                        chunks.append(data)
                        if b"\nEND\n" in b"".join(chunks) or b"PING\n" in b"".join(chunks):
                            break
                    received.append(b"".join(chunks).decode("utf-8", errors="replace"))
                    conn.sendall(response_bytes)
            finally:
                srv.close()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        return port, t, received

    def test_raw_socket_exchange_requires_end_sentinel_on_real_tcp_loopback(self):
        from mimonitor_toolbox import adb as adb_runtime

        port_eof, thread_eof, _ = self._run_loopback_server(b"OK\nSTEP 0 OK -\n")
        res_eof = adb_runtime.Adb._raw_socket_exchange(port_eof, "PING\n", timeout=2.0)
        thread_eof.join(timeout=2.0)
        self.assertIsNone(res_eof)

        port_ok, thread_ok, rec_ok = self._run_loopback_server(b"OK MtkDirectTool v2.1 put=1\nEND\n")
        res_ok = adb_runtime.Adb._raw_socket_exchange(port_ok, "PING\n", timeout=2.0)
        thread_ok.join(timeout=2.0)
        self.assertEqual(res_ok, ["OK MtkDirectTool v2.1 put=1"])
        self.assertEqual(rec_ok, ["PING\n"])

    def test_protocol_validation_and_put_space_preservation(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")

        with self.assertRaises(ValueError):
            client.daemon_query(settings_keys=("bright\nQUIT",))
        with self.assertRaises(ValueError):
            client.daemon_query(jni_keys=("bad\nkey",))
        with self.assertRaises(ValueError):
            client._format_batch_line(("PUT", "key with space", "1"))
        with self.assertRaises(ValueError):
            client._format_batch_line(("PUT", "valid_key", "line1\nQUIT"))

        formatted = client._format_batch_line(("PUT", "custom_name", "  spaced val  "))
        self.assertEqual(formatted, "PUT custom_name   spaced val  ")

    def test_daemon_batch_non_put_step_err_falls_back_and_check_propagates(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        client._daemon_put_supported = True

        with mock.patch.object(client, "ensure_daemon", return_value=True), \
                mock.patch.object(
                    client,
                    "_daemon_socket_request",
                    return_value=["OK", "STEP 0 ERR JNI_FAIL"],
                ):
            ok, err_idx, steps = client.daemon_batch([("JNI_SET", "k1", 5, 3)])
        self.assertFalse(ok)
        self.assertIsNone(err_idx)
        self.assertEqual(steps, [(0, "ERR", "JNI_FAIL")])

        adb_calls = []

        def failing_exec(op, check=False):
            adb_calls.append((op, check))
            if check:
                raise RuntimeError("adb failed")

        with mock.patch.object(client, "ensure_daemon", return_value=True), \
                mock.patch.object(
                    client,
                    "daemon_batch",
                    return_value=(False, None, [(0, "OK", "-"), (1, "ERR", "JNI_FAIL")]),
                ), \
                mock.patch.object(client, "_exec_op_via_adb", side_effect=failing_exec):
            with self.assertRaises(RuntimeError):
                with client.batch_writes():
                    client.jni_set("k_ok", 1, check=False)
                    client.jni_set("k_bad", 2, check=True)
        self.assertEqual(adb_calls, [(("JNI_SET", "k_bad", 2, 3), True)])

    def test_daemon_batch_dynamic_timeout_and_no_blind_retry(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        ops = [
            ("SLEEP", 5000),
            ("JNI_RAMP", "g_backlight", 0, 100, 10, 500, 3),
        ]
        seen_timeouts = []

        def fake_req(payload, timeout=4.0):
            seen_timeouts.append(timeout)
            return None

        with mock.patch.object(client, "ensure_daemon", return_value=True) as ensure_mock, \
                mock.patch.object(client, "_daemon_socket_request", side_effect=fake_req):
            ok, _, _ = client.daemon_batch(ops)
        self.assertFalse(ok)
        self.assertEqual(ensure_mock.call_count, 1)
        self.assertEqual(len(seen_timeouts), 1)
        self.assertGreaterEqual(seen_timeouts[0], 18.0)

    def test_flush_batch_err_at_falls_back_failed_steps_before_err_idx(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        client._batch_ops = [
            ("JNI_SET", "k_bad", 1, 3),
            ("PUT", "brightness", "50"),
        ]
        fallback_calls = []

        def fake_batch(ops, check=False):
            if ops[0][0] == "JNI_SET":
                return False, 1, [(0, "ERR", "JNI_FAIL")]
            return True, None, []

        with mock.patch.object(client, "ensure_daemon", return_value=True), \
                mock.patch.object(client, "daemon_batch", side_effect=fake_batch), \
                mock.patch.object(
                    client,
                    "_exec_op_via_adb",
                    side_effect=lambda op, check=False: fallback_calls.append(op),
                ), \
                mock.patch.object(
                    client,
                    "_adb_put",
                    side_effect=lambda k, v, check=False: fallback_calls.append(("PUT", k, v)),
                ):
            self.assertTrue(client.flush_batch(check=False))
        self.assertEqual(
            fallback_calls,
            [("JNI_SET", "k_bad", 1, 3), ("PUT", "brightness", "50")],
        )

    def test_ensure_daemon_releases_command_lock_during_poll_sleep_and_parses_put_1(self):
        import threading
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.9")
        client.set_connection_mode("daemon")
        in_sleep = threading.Event()
        release_sleep = threading.Event()
        lock_acquired_during_sleep = []

        def fake_forward():
            client._daemon_forward_port = 27895
            client._daemon_ip = client.ip
            return 27895

        sock_count = [0]

        def fake_socket(payload, timeout=1.2):
            sock_count[0] += 1
            if sock_count[0] == 1:
                return None
            return ["OK MtkDirectTool v2.1 pid=1 uid=1000 put=1"]

        def fake_sleep(_delay):
            in_sleep.set()
            release_sleep.wait(timeout=2.0)

        def check_lock():
            if in_sleep.wait(timeout=1.0):
                acquired = adb_runtime._adb_command_lock.acquire(blocking=False)
                lock_acquired_during_sleep.append(acquired)
                if acquired:
                    adb_runtime._adb_command_lock.release()
                release_sleep.set()

        probe_thread = threading.Thread(target=check_lock, daemon=True)
        probe_thread.start()
        with mock.patch.object(adb_runtime.time, "sleep", side_effect=fake_sleep), \
                mock.patch.object(client, "_setup_adb_forward", side_effect=fake_forward), \
                mock.patch.object(client, "check_and_heal_jar", side_effect=lambda: setattr(client, "_daemon_jar_healed_ip", client.ip) or True), \
                mock.patch.object(client, "_daemon_socket_request", side_effect=fake_socket), \
                mock.patch.object(client, "shell", return_value=""):
            ready = client.ensure_daemon()
        probe_thread.join(timeout=2.0)

        self.assertTrue(ready)
        self.assertEqual(lock_acquired_during_sleep, [True])
        self.assertIs(client._daemon_put_supported, True)

    def test_ip_change_and_reset_remove_old_forward_and_reset_binder_verified(self):
        from mimonitor_toolbox import adb as adb_runtime

        client = adb_runtime.Adb("192.168.1.10", connection_mode="daemon")
        client._daemon_ip = "192.168.1.10"
        client._daemon_forward_port = 45678
        client._media_volume_binder_verified = True

        calls = []
        with mock.patch.object(adb_runtime, "adb_run", side_effect=lambda args, **kw: calls.append(list(args)) or ""):
            client.ip = "192.168.1.20"

        self.assertIsNone(client._media_volume_binder_verified)
        self.assertIsNone(client._daemon_forward_port)
        self.assertEqual(calls, [["-s", "192.168.1.10:5555", "forward", "--remove", "tcp:45678"]])

    def test_ensure_adb_server_tracks_popen_in_adb_processes(self):
        from mimonitor_toolbox import adb as adb_runtime

        fake_proc = mock.Mock()
        fake_proc.poll.return_value = None
        alive_states = iter([False, False, True])

        with adb_runtime._adb_process_lock:
            adb_runtime._adb_processes.clear()
        with mock.patch.object(adb_runtime, "is_adb_server_alive", side_effect=lambda: next(alive_states, True)), \
                mock.patch.object(adb_runtime.subprocess, "Popen", return_value=fake_proc), \
                mock.patch.object(adb_runtime.time, "sleep"):
            self.assertTrue(adb_runtime.ensure_adb_server(timeout=2.0))
        with adb_runtime._adb_process_lock:
            self.assertIn(fake_proc, adb_runtime._adb_processes)
        adb_runtime.cleanup_adb_processes(kill_server=False)
        fake_proc.kill.assert_called_once()

    def test_jar_healing_skips_cp_when_unchanged_and_fails_on_offline_stat(self):
        from mimonitor_toolbox import adb as adb_runtime

        target = adb_runtime.Adb("192.168.1.9")
        shell_calls = []

        with mock.patch.object(target, "shell", side_effect=lambda cmd, check=False: shell_calls.append(cmd) or "1500"), \
                mock.patch.object(adb_runtime, "get_mtk_direct_tool_path", return_value="/tmp/MtkDirectTool.jar"), \
                mock.patch.object(adb_runtime.os.path, "getsize", return_value=1500), \
                mock.patch.object(adb_runtime, "adb_run") as run_mock:
            ok = target.check_and_heal_jar()
        self.assertTrue(ok)
        run_mock.assert_not_called()
        self.assertEqual(len(shell_calls), 1)
        self.assertEqual(target._daemon_jar_healed_ip, "192.168.1.9")

        target2 = adb_runtime.Adb("192.168.1.9")
        with mock.patch.object(target2, "shell", return_value="error: device offline"), \
                mock.patch.object(adb_runtime, "get_mtk_direct_tool_path", return_value="/tmp/MtkDirectTool.jar"):
            ok2 = target2.check_and_heal_jar()
        self.assertFalse(ok2)
        self.assertEqual(target2._daemon_jar_healed_ip, "")

    def test_adb_jni_get_uses_batch_file_fast_path_and_quoted_logcat_fallback(self):
        from mimonitor_toolbox import adb as adb_runtime

        target = adb_runtime.Adb("192.168.1.9")
        with mock.patch.object(target, "_adb_jni_batch_get", return_value={"g_video__clr_temp": 4}):
            self.assertEqual(target._adb_jni_get("g_video__clr_temp"), "4")

        shell_cmds = []

        def fake_shell(cmd, check=False):
            shell_cmds.append(cmd)
            if "logcat -d" in cmd:
                return "RESULT: GET g_video__clr_temp = 7"
            return ""

        with mock.patch.object(target, "_adb_jni_batch_get", return_value={}), \
                mock.patch.object(adb_runtime.time, "sleep"), \
                mock.patch.object(target, "shell", side_effect=fake_shell):
            self.assertEqual(target._adb_jni_get("g_video__clr_temp"), "7")
        self.assertTrue(any("grep 'GET g_video__clr_temp'" in c for c in shell_cmds))

    def test_scan_adb_preserves_preexisting_connection_and_breaks_on_cancel(self):
        import ipaddress
        import threading
        from mimonitor_toolbox import adb as adb_runtime

        networks = [mock.Mock(ip=ipaddress.ip_address("192.168.1.0"), netmask="255.255.255.0")]
        t1 = mock.Mock(ip=ipaddress.ip_address("192.168.1.10"))
        t2 = mock.Mock(ip=ipaddress.ip_address("192.168.1.20"))
        cancel = threading.Event()
        disconnected = []

        def fake_adb_run(args, timeout=10, check=False):
            if args == ["devices"]:
                return "List of devices attached\n192.168.1.10:5555\tdevice\n"
            if args[0] == "disconnect":
                disconnected.append(args[1])
                return "disconnected"
            if "get-state" in args:
                return "device"
            if "getprop ro.product.model" in args:
                cancel.set()  # cancel right after reading first target
                return "MiTV-MFFU1"
            return ""

        try:
            with mock.patch.object(adb_runtime, "get_windows_scan_networks", return_value=networks), \
                    mock.patch.object(adb_runtime, "build_probe_targets", return_value=[t1, t2]), \
                    mock.patch.object(adb_runtime, "probe_tcp_targets", return_value=[t1, t2]), \
                    mock.patch.object(adb_runtime, "ensure_adb_server", return_value=True), \
                    mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run):
                found = adb_runtime.scan_adb(cancel_event=cancel)
        finally:
            adb_runtime.clear_ignored_scan_ips()

        self.assertEqual(found, [("192.168.1.10", "MiTV-MFFU1")])
        self.assertEqual(disconnected, [])

    def test_tvservice_command_builder_quotes_unsafe_tokens_and_rejects_spaces(self):
        from mimonitor_toolbox import adb as adb_runtime

        cmd = adb_runtime.build_tvservice_shell_command(["echo", "a;b"])
        self.assertIn("'a;b'", cmd)
        with self.assertRaises(ValueError):
            adb_runtime.build_tvservice_shell_command(["echo", "a b"])

    def test_set_input_source_prefers_daemon_then_jar_then_adb(self):
        from mimonitor_toolbox import adb as adb_runtime

        target = adb_runtime.Adb()
        target.ip = "192.168.1.20"
        target._connection_mode = adb_runtime.CONNECTION_MODE_DAEMON
        self.assertEqual(target._format_batch_line(("SET_SOURCE", 24)), "SET_SOURCE 24")

        # 1) Daemon mode succeeds -> neither JAR CLI nor raw ADB shell runs
        with mock.patch.object(target, "is_daemon_mode", return_value=True), \
                mock.patch.object(target, "daemon_batch", return_value=(True, None, [])) as batch_mock, \
                mock.patch.object(target, "_jar_set_input_source") as jar_mock, \
                mock.patch.object(target, "_adb_set_input_source") as raw_mock:
            target.set_input_source(23)
        batch_mock.assert_called_once_with([("SET_SOURCE", 23)], check=False)
        jar_mock.assert_not_called()
        raw_mock.assert_not_called()

        # 2) Daemon fails -> JAR CLI runs and succeeds via .mtk_source_result.txt
        shell_cmds = []

        def fake_shell_jar_ok(cmd, check=False):
            shell_cmds.append(cmd)
            return "RESULT: SET_SOURCE sourceId=29\n"

        with mock.patch.object(target, "is_daemon_mode", return_value=True), \
                mock.patch.object(target, "daemon_batch", return_value=(False, None, [])), \
                mock.patch.object(target, "check_and_heal_jar", return_value=True), \
                mock.patch.object(target, "shell", side_effect=fake_shell_jar_ok), \
                mock.patch.object(target, "_adb_set_input_source") as raw_mock:
            target.set_input_source(29)
        self.assertEqual(len(shell_cmds), 1)
        self.assertIn("setSource", shell_cmds[0])
        self.assertIn(".mtk_source_result.txt", shell_cmds[0])
        raw_mock.assert_not_called()

        # 3) JAR CLI fails -> falls back to raw ADB am force-stop + am start
        raw_cmds = []

        def fake_shell_raw(cmd, check=False):
            raw_cmds.append((cmd, check))
            return ""

        with mock.patch.object(target, "is_daemon_mode", return_value=False), \
                mock.patch.object(target, "check_and_heal_jar", return_value=False), \
                mock.patch.object(target, "shell", side_effect=fake_shell_raw):
            target.set_input_source(30, check=True)
        self.assertEqual(
            raw_cmds,
            [
                ("am force-stop com.xiaomi.mitv.tvplayer", True),
                (
                    "am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY -n com.xiaomi.mitv.tvplayer/.ExternalSourceActivity --ei input 30 -f 0x10000000",
                    True,
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()