"""ADB 子进程、设备协议和扫描识别。"""

import contextlib
import logging
import os
import shlex
import socket
import subprocess
import sys
import threading
import time

from .core import (
    CONNECTION_MODE_ADB,
    CONNECTION_MODE_DAEMON,
    MTK_BATCH_RESULT_FILE,
    MTK_DAEMON_PORT,
    bundled_resource_path,
    get_app_data_dir,
    get_colorful_led_tool_path,
    get_mtk_direct_tool_path,
    is_frozen_build,
    load_settings,
    normalize_connection_mode,
)
from .network_scan import (
    WindowsAdapterError,
    build_probe_targets,
    get_windows_scan_networks,
    probe_tcp_targets,
)

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_startup_warnings = []


def drain_startup_warnings():
    warnings = list(_startup_warnings)
    _startup_warnings.clear()
    return warnings

def ensure_persistent_adb_runtime(adb_path):
    if sys.platform != "win32" or not is_frozen_build():
        return adb_path
    try:
        import shutil
        runtime_dir = os.path.join(get_app_data_dir(), "runtime")
        os.makedirs(runtime_dir, exist_ok=True)
        for filename in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
            src = bundled_resource_path("assets", "runtime", filename)
            if not src or not os.path.exists(src):
                continue
            dst = os.path.join(runtime_dir, filename)
            try:
                same_file = os.path.abspath(src).lower() == os.path.abspath(dst).lower()
            except Exception:
                same_file = False
            if same_file:
                continue
            should_copy = not os.path.exists(dst)
            if not should_copy:
                try:
                    should_copy = os.path.getsize(src) != os.path.getsize(dst) or int(os.path.getmtime(src)) > int(os.path.getmtime(dst))
                except Exception:
                    should_copy = True
            if should_copy:
                shutil.copy2(src, dst)
        persistent_adb = os.path.join(runtime_dir, "adb.exe")
        if os.path.exists(persistent_adb):
            return persistent_adb
    except Exception as exc:
        _startup_warnings.append(
            f"ADB 运行时准备失败，已回退到内置路径: "
            f"{type(exc).__name__}: {exc}"
        )
    return adb_path

def get_adb_path():
    adb_names = ["adb.exe"] if sys.platform == "win32" else ["adb"]
    for name in adb_names:
        path = bundled_resource_path("assets", "runtime", name)
        if path:
            return ensure_persistent_adb_runtime(path)
    return "adb"


ADB = get_adb_path()
ADB_SERVER_PORT = os.environ.get("MIMONITOR_ADB_SERVER_PORT", "5038")
# 内嵌 platform-tools 37.x 的 mDNS zero-config 在 macOS 26 上会偶发 panic，
# adb server 被 SIGABRT 带走后 install 只返回空错误（保活守护部署失败）。
# 本工具全程按 IP:5555 直连，不需要 mDNS 发现；外部显式设置优先。
os.environ.setdefault("ADB_MDNS", "0")
ADB_DEVICE_PORT = 5555
ADB_DISCONNECTED_MARKERS = (
    "offline", "unauthorized", "no devices", "not found",
    "cannot", "failed", "error:",
)


def format_adb_serial(ip, port=ADB_DEVICE_PORT):
    ip = str(ip or "").strip()
    return f"{ip}:{port}" if ip else ""


import re

_TVSERVICE_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:@+=-]+$")


def _quote_tvservice_token(value):
    text = str(value)
    if any(ch in ("\r", "\n", "\x00") or ch.isspace() for ch in text):
        raise ValueError(f"Unsafe whitespace or control char in TvService argument: {text!r}")
    if _TVSERVICE_SAFE_TOKEN_RE.match(text):
        return text
    # Inside outer `s16 "sh -c eval${IFS}..."`, outer sh expands \, ", $, `,
    # then inner `eval` consumes POSIX single-quotes.
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
        .replace("'", "'\\\\''")
    )
    return f"'{escaped}'"


def build_tvservice_app_process_command(jar, args):
    quoted_jar = _quote_tvservice_token(jar)
    encoded_args = "".join(f"\\${{IFS}}{_quote_tvservice_token(part)}" for part in args)
    return (
        'service call TvService 3 s16 "sh -c eval\\${IFS}'
        f'CLASSPATH={quoted_jar}'
        '\\${IFS}/system/bin/app_process'
        '\\${IFS}/data/data/mitv.service/cache'
        f'{encoded_args}"'
    )


def build_tvservice_shell_command(parts):
    """构造一条经 TvService 执行的外部命令（`sh -c eval` + `${IFS}` 分隔）。

    TvService 的 runSystemCommand 直接调 `Runtime.exec(String)`，而它按空白
    切分、**不起 shell**。所以命令体里的空格必须写成字面量 `${IFS}`：`sh` 会
    把它展开回空白，再交给 `eval` 重新解析。

    写成"真实空格 + 转义双引号"的老形式会静默失败 —— `sh -c` 只拿到被截断的
    第一个词（如 `"cp`），而 `service call` 依旧返回 Parcel，看着像成功。
    同理，如果没有 `sh -c` 这层而直接写 `cp A B`，虽然 argv 恰好正确，但没有
    实测背书，不要回退到那种写法。
    """
    encoded = "".join(f"\\${{IFS}}{_quote_tvservice_token(part)}" for part in parts)
    return f'service call TvService 3 s16 "sh -c eval{encoded}"'


def parse_jni_batch_output(output):
    values = {}
    for line in str(output or "").splitlines():
        line = line.strip()
        if not line or line.startswith("__") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if raw.startswith("ERROR"):
            continue
        try:
            values[key] = int(raw)
        except (TypeError, ValueError):
            values[key] = raw
    return values


def is_adb_server_alive(timeout=1.0):
    """检查本软件独立 ADB Server 的监听端口，不触发 ADB 自动拉起。"""
    # 默认 timeout 不能太小：慢 loopback 上握手可能 >0.2s，会假阴性。
    sock = None
    try:
        port = int(ADB_SERVER_PORT)
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

def ensure_adb_server(timeout=12.0):
    """Cold-start the private ADB server and wait until it listens; never kill the bootstrap client."""
    if is_adb_server_alive():
        return True
    proc = None
    with _adb_process_lock:
        _prune_finished_adb_processes_locked()
        if _adb_spawn_blocked:
            return False
        try:
            proc = subprocess.Popen(
                adb_command(["start-server"]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
            )
            _adb_processes.add(proc)
        except Exception as exc:
            _adb_log(f"start-server spawn failed: {exc}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and isinstance(proc.poll(), int):
            with _adb_process_lock:
                _adb_processes.discard(proc)
            proc = None
        if is_adb_server_alive():
            return True
        time.sleep(0.2)
    if proc is not None and isinstance(proc.poll(), int):
        with _adb_process_lock:
            _adb_processes.discard(proc)
    return is_adb_server_alive()


def adb_text_has_disconnected_marker(text):
    lower = str(text or "").lower()
    return any(marker in lower for marker in ADB_DISCONNECTED_MARKERS)

def adb_device_state(serial, timeout=3):
    out = adb_run(["-s", serial, "get-state"], timeout=timeout).strip()
    lower = out.lower()
    if lower in ("device", "offline", "unauthorized"):
        return lower
    if "unauthorized" in lower:
        return "unauthorized"
    if "offline" in lower:
        return "offline"
    if "not found" in lower:
        return "not found"
    if "no devices" in lower:
        return "no devices"
    if "cannot" in lower or "failed" in lower or "error:" in lower:
        return lower or "error"
    return lower or "unknown"


def adb_device_state_label(state):
    lower = str(state or "").lower()
    if "offline" in lower:
        return "设备离线"
    if "unauthorized" in lower:
        return "设备未授权"
    if "not found" in lower or "no devices" in lower:
        return "未找到设备"
    if "cannot" in lower or "failed" in lower or "error" in lower:
        return "连接异常"
    if lower == "unknown" or not lower:
        return "状态未知"
    return str(state)


def disconnected_status_text(detail):
    return f"未连接（{adb_device_state_label(detail)}）"

def is_connected_status_text(text):
    text = str(text or "")
    return (text == "已连接" or text.startswith("已连接:")) and not adb_text_has_disconnected_marker(text)

def normalize_status_text(text):
    text = str(text or "")
    if "已连接" in text and adb_text_has_disconnected_marker(text):
        return disconnected_status_text(text)
    return text

# ===== 日志文件 =====
_log_file = None
_log_path = None
_log_to_file_enabled = False
_adb_processes = set()
_adb_spawn_blocked = False
_adb_command_lock = threading.RLock()
_adb_process_lock = threading.RLock()
_log_lock = threading.RLock()
_async_error_handler = None
_logger = logging.getLogger(__name__)


def _prune_finished_adb_processes_locked():
    done = []
    for proc in _adb_processes:
        try:
            if isinstance(proc.poll(), int):
                done.append(proc)
        except Exception:
            done.append(proc)
    for proc in done:
        _adb_processes.discard(proc)


def set_async_error_handler(handler):
    global _async_error_handler
    _async_error_handler = handler


def adb_command(args):
    cmd = [ADB]
    if ADB_SERVER_PORT:
        cmd += ["-P", str(ADB_SERVER_PORT)]
    return cmd + args


def adb_command_text(args):
    parts = [f'"{ADB}"']
    if ADB_SERVER_PORT:
        parts += ["-P", str(ADB_SERVER_PORT)]
    return " ".join(parts + args)


def _adb_log(msg):
    """写入 ADB 操作日志到文件。"""
    with _log_lock:
        if _log_file and _log_to_file_enabled:
            try:
                _log_file.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
                _log_file.flush()
            except Exception:
                pass


def adb_run(args, timeout=10, check=False):
    proc = None
    # disconnect 需要高优先级抢占，不能卡在后台挂起的 shell/query 命令锁后面
    lock_ctx = (
        contextlib.nullcontext()
        if args and args[0] == "disconnect"
        else _adb_command_lock
    )
    with lock_ctx:
        try:
            cmd = adb_command(args)
            with _adb_process_lock:
                if _adb_spawn_blocked:
                    _adb_log(f"{adb_command_text(args)} => SKIPPED: shutting down")
                    if check:
                        raise RuntimeError("应用正在退出，ADB 命令已取消")
                    return ""
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=NO_WINDOW,
                    stdin=subprocess.DEVNULL,
                )
                _adb_processes.add(proc)
            stdout, stderr = proc.communicate(timeout=timeout)
            out = stdout.strip()
            if not out and stderr:
                out = stderr.strip()
            _adb_log(f"{adb_command_text(args)} => {out[:200]}")
            if check and proc.returncode not in (0, None):
                raise RuntimeError(out or f"ADB 进程退出码 {proc.returncode}")
            return out
        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
                try:
                    proc.communicate(timeout=1)
                except Exception:
                    pass
            _adb_log(f"{adb_command_text(args)} => TIMEOUT")
            if check:
                raise RuntimeError(f"ADB 命令超时（{timeout} 秒）") from None
            return ""
        except Exception as exc:
            _adb_log(f"{adb_command_text(args)} => ERROR: {exc}")
            if check:
                raise
            return ""
        finally:
            if proc:
                with _adb_process_lock:
                    _adb_processes.discard(proc)


def cleanup_adb_processes(kill_server=False):
    with _adb_process_lock:
        processes = list(_adb_processes)
        _adb_processes.clear()
    for proc in processes:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
    if kill_server:
        try:
            subprocess.run(adb_command(["kill-server"]), capture_output=True, text=True, timeout=3,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        except Exception:
            pass

def block_adb_spawns():
    global _adb_spawn_blocked
    with _adb_process_lock:
        _adb_spawn_blocked = True

def unblock_adb_spawns():
    global _adb_spawn_blocked
    with _adb_process_lock:
        _adb_spawn_blocked = False


_AUDIO_SERVICE_TXN_ADJUST_STREAM_VOLUME = 10
_AUDIO_SERVICE_TXN_SET_STREAM_VOLUME = 12
_AUDIO_SERVICE_TXN_IS_STREAM_MUTE = 17
_AUDIO_SERVICE_TXN_GET_STREAM_VOLUME_FOR_MUTE = 22
_AUDIO_STREAM_MUSIC = 3
_AUDIO_ADJUST_MUTE = -100
_AUDIO_ADJUST_UNMUTE = 100
_AUDIO_BINDER_SHELL_PACKAGE = "com.android.shell"


def service_call_succeeded(output):
    """判断 `service call` 输出是否成功且无 binder 异常。"""
    text = str(output or "")
    if "Result: Parcel(" not in text:
        return False
    lower = text.lower()
    return "error" not in lower and "exception" not in lower


def parse_service_call_int(output):
    """从 `service call` 返回的 Parcel dump 中提取末尾 int32 返回值。"""
    hex_digits = set("0123456789abcdefABCDEF")
    tokens = []
    current = []
    for ch in str(output or ""):
        if ch in hex_digits:
            current.append(ch)
        else:
            if len(current) == 8:
                tokens.append("".join(current))
            current = []
    if len(current) == 8:
        tokens.append("".join(current))
    if len(tokens) < 2:
        return None
    val = int(tokens[-1], 16)
    if val >= 0x80000000:
        val -= 0x100000000
    return val


class Adb:
    def __init__(self, ip="", connection_mode=CONNECTION_MODE_ADB):
        self._state_lock = threading.RLock()
        self._ip = str(ip or "")
        self.connection_mode = normalize_connection_mode(connection_mode)
        self._media_volume_binder_verified = None
        self._daemon_ready = False
        self._daemon_ip = ""
        self._daemon_forward_port = None
        self._daemon_put_supported = None
        self._daemon_vol_supported = None
        self._daemon_jar_healed_ip = ""
        self._daemon_retry_after = 0.0
        self._daemon_needs_restart = False
        self._batch_depth = 0
        self._batch_ops = []
        self._batch_check_required = False
        self._batch_read_cache = {}
        self._batch_jni_cache = {}

    @property
    def ip(self):
        return self._ip

    @ip.setter
    def ip(self, value):
        new_ip = str(value or "")
        changed = False
        with self._state_lock:
            if new_ip != self._ip:
                self._ip = new_ip
                self._media_volume_binder_verified = None
                changed = True
        if changed:
            self._reset_daemon_state()

    @property
    def serial(self):
        return format_adb_serial(self.ip)

    def transaction(self):
        return _adb_command_lock

    def is_daemon_mode(self):
        with self._state_lock:
            return self.connection_mode == CONNECTION_MODE_DAEMON

    def _reset_daemon_state(self):
        with self._state_lock:
            old_ip = self._daemon_ip
            old_port = self._daemon_forward_port
            self._media_volume_binder_verified = None
            self._daemon_ready = False
            self._daemon_ip = ""
            self._daemon_forward_port = None
            self._daemon_put_supported = None
            self._daemon_vol_supported = None
            self._daemon_jar_healed_ip = ""
            self._daemon_retry_after = 0.0
            self._daemon_needs_restart = False
            if self._batch_depth == 0:
                self._batch_check_required = False
        if old_port and old_ip:
            adb_run(
                ["-s", format_adb_serial(old_ip), "forward", "--remove", f"tcp:{old_port}"],
                timeout=2,
                check=False,
            )

    def set_connection_mode(self, mode):
        normalized = normalize_connection_mode(mode)
        with self._state_lock:
            self._daemon_retry_after = 0.0
            if normalized != self.connection_mode:
                self.connection_mode = normalized
                if normalized != CONNECTION_MODE_DAEMON:
                    self._batch_depth = 0
                    self._batch_check_required = False
                    self._batch_ops.clear()
                    self._batch_read_cache.clear()
                    self._batch_jni_cache.clear()
        return normalized

    def stop_daemon(self):
        with self._state_lock:
            port = self._daemon_forward_port
            target_ip = self._daemon_ip or self._ip
        if port and target_ip:
            with _adb_process_lock:
                blocked = _adb_spawn_blocked
            if not blocked:
                self._raw_socket_exchange(port, "QUIT\n", timeout=0.8)
        self._reset_daemon_state()

    def shell(self, cmd, check=False):
        out = adb_run(["-s", self.serial, "shell", cmd], check=check)
        return out

    @staticmethod
    def _pick_free_local_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _raw_socket_exchange(port, payload, timeout=4.0):
        if not port:
            return None
        sock = None
        try:
            sock = socket.create_connection(("127.0.0.1", int(port)), timeout=min(timeout, 0.4))
            sock.settimeout(timeout)
            text_payload = payload if payload.endswith("\n") else payload + "\n"
            sock.sendall(text_payload.encode("utf-8"))
            lines = []
            saw_end = False
            with sock.makefile("r", encoding="utf-8", errors="replace") as reader:
                for raw_line in reader:
                    line = raw_line.rstrip("\r\n")
                    if line == "END":
                        saw_end = True
                        break
                    lines.append(line)
            if not saw_end:
                return None
            return lines if lines else None
        except OSError:
            return None
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def _setup_adb_forward(self):
        with self._state_lock:
            old_port = self._daemon_forward_port
            old_ip = self._daemon_ip
            self._daemon_forward_port = None
            self._daemon_ip = ""
        if old_port and old_ip:
            adb_run(
                ["-s", format_adb_serial(old_ip), "forward", "--remove", f"tcp:{old_port}"],
                timeout=2,
                check=False,
            )
        for _ in range(2):
            local_port = self._pick_free_local_port()
            out = adb_run(
                ["-s", self.serial, "forward", f"tcp:{local_port}", f"tcp:{MTK_DAEMON_PORT}"],
                timeout=4,
                check=False,
            )
            lower = str(out or "").lower()
            if (
                adb_text_has_disconnected_marker(out)
                or "error:" in lower
                or "cannot bind" in lower
                or "failed" in lower
            ):
                continue
            with self._state_lock:
                self._daemon_forward_port = local_port
                self._daemon_ip = self.ip
            return local_port
        return None

    def _daemon_socket_request(self, payload, timeout=4.0):
        with self._state_lock:
            ip = self._ip
            daemon_ip = self._daemon_ip
            port = self._daemon_forward_port
        if not ip or daemon_ip != ip or not port:
            return None
        with _adb_process_lock:
            if _adb_spawn_blocked:
                return None
        lines = self._raw_socket_exchange(port, payload, timeout=timeout)
        if lines is None:
            with self._state_lock:
                self._daemon_ready = False
        return lines

    def _parse_daemon_pong(self, pong_line):
        for token in str(pong_line or "").split():
            if token == "put=0":
                self._daemon_put_supported = False
            elif token == "put=1":
                self._daemon_put_supported = True

    def ensure_daemon(self, check=False):
        if not self.is_daemon_mode() or not self.ip:
            return False
        with _adb_process_lock:
            if _adb_spawn_blocked:
                if check:
                    raise RuntimeError("应用正在退出，Daemon 启动已取消")
                return False

        with self.transaction():
            if self._daemon_ip and self._daemon_ip != self.ip:
                self._reset_daemon_state()

            if (
                self._daemon_ready
                and self._daemon_forward_port
                and self._daemon_jar_healed_ip == self.ip
                and not self._daemon_needs_restart
            ):
                pong = self._daemon_socket_request("PING\n", timeout=1.2)
                if pong and pong[0].startswith("OK MtkDirectTool v2"):
                    with self._state_lock:
                        self._parse_daemon_pong(pong[0])
                        self._daemon_retry_after = 0.0
                    return True
                with self._state_lock:
                    self._daemon_ready = False

            if time.monotonic() < getattr(self, "_daemon_retry_after", 0.0):
                return False

            if self._daemon_jar_healed_ip != self.ip:
                if self.check_and_heal_jar() is False:
                    with self._state_lock:
                        self._daemon_retry_after = time.monotonic() + 5.0
                    return False

            fresh_forward = False
            if not self._daemon_forward_port or self._daemon_ip != self.ip:
                if not self._setup_adb_forward():
                    with self._state_lock:
                        self._daemon_retry_after = time.monotonic() + 5.0
                    _adb_log("daemon: adb forward 失败，回退 ADB 模式")
                    return False
                fresh_forward = True

            pong = self._daemon_socket_request("PING\n", timeout=1.2)
            # 若旧的 forward 映射已被 disconnect / 重连 / ADB 重启销毁，重建 forward 后再 PING
            if not pong and not fresh_forward:
                if not self._setup_adb_forward():
                    with self._state_lock:
                        self._daemon_retry_after = time.monotonic() + 5.0
                    _adb_log("daemon: 重建 adb forward 失败，回退 ADB 模式")
                    return False
                pong = self._daemon_socket_request("PING\n", timeout=1.2)

            if pong:
                if not self._daemon_needs_restart and pong[0].startswith("OK MtkDirectTool v2"):
                    with self._state_lock:
                        self._parse_daemon_pong(pong[0])
                        self._daemon_ready = True
                        self._daemon_retry_after = 0.0
                    _adb_log(f"daemon: 已复用常驻服务 ({pong[0]})")
                    return True
                self._daemon_socket_request("QUIT\n", timeout=0.8)
                with self._state_lock:
                    self._daemon_needs_restart = False
            else:
                with self._state_lock:
                    self._daemon_needs_restart = False

            jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            launch_cmd = build_tvservice_app_process_command(
                jar, ["MtkDirectTool", "daemon", MTK_DAEMON_PORT]
            )
            launch_out = self.shell(launch_cmd, check=check)
            if not self.ip or adb_text_has_disconnected_marker(launch_out):
                with self._state_lock:
                    self._daemon_retry_after = time.monotonic() + 5.0
                _adb_log("daemon: 设备已断连，取消启动")
                return False

        # Release _adb_command_lock between startup poll sleeps so ADB/disconnect calls aren't blocked.
        for delay in (0.15, 0.2, 0.25, 0.35, 0.5, 0.5, 0.5):
            if not self.ip or not self.is_daemon_mode():
                return False
            time.sleep(delay)
            if not self.ip or not self.is_daemon_mode():
                return False
            with _adb_process_lock:
                if _adb_spawn_blocked:
                    return False
            with self.transaction():
                if not self.ip or not self.is_daemon_mode():
                    return False
                pong = self._daemon_socket_request("PING\n", timeout=1.2)
                if pong and pong[0].startswith("OK MtkDirectTool v2"):
                    with self._state_lock:
                        self._parse_daemon_pong(pong[0])
                        self._daemon_ready = True
                        self._daemon_retry_after = 0.0
                    _adb_log(f"daemon: 启动就绪 ({pong[0]})")
                    return True

        with self._state_lock:
            self._daemon_retry_after = time.monotonic() + 5.0
        _adb_log("daemon: 启动握手超时，回退标准 ADB")
        return False

    @staticmethod
    def _validate_daemon_token(value, field_name="token"):
        text = str(value).strip()
        if not text or "\r" in text or "\n" in text or "\x00" in text or any(ch.isspace() for ch in text):
            raise ValueError(f"Invalid {field_name}: {value!r}")
        return text

    def daemon_query(self, settings_keys=(), jni_keys=(), flags=(), check=False):
        if not self.is_daemon_mode() or not self.ip:
            return None
        safe_settings = [
            self._validate_daemon_token(k, "settings key")
            for k in settings_keys
            if str(k).strip()
        ]
        safe_jni = []
        for k in jni_keys:
            text = str(k).strip()
            if not text:
                continue
            if "\r" in text or "\n" in text or "\x00" in text or any(ch.isspace() for ch in text):
                raise ValueError(f"Invalid JNI key: {k!r}")
            if all(ch.isalnum() or ch == "_" for ch in text):
                safe_jni.append(text)
        safe_flags = [
            self._validate_daemon_token(f, "flag")
            for f in flags
            if str(f).strip()
        ]
        if not safe_settings and not safe_jni and not safe_flags:
            return {"settings": {}, "jni": {}, "flags": {}, "elapsed_ms": 0}

        lines_req = ["QUERY"]
        if safe_settings:
            lines_req.append("SETTINGS " + " ".join(safe_settings))
        if safe_jni:
            lines_req.append("JNI " + " ".join(safe_jni))
        if safe_flags:
            lines_req.append("FLAGS " + " ".join(safe_flags))
        lines_req.append("END")
        payload = "\n".join(lines_req) + "\n"

        if not self.ensure_daemon(check=check):
            return None
        with self.transaction():
            resp = self._daemon_socket_request(payload, timeout=5.0)
        if not resp:
            if not self.ensure_daemon(check=check):
                return None
            with self.transaction():
                resp = self._daemon_socket_request(payload, timeout=5.0)
        if not resp or resp[0] != "OK":
            return None

        result = {"settings": {}, "jni": {}, "flags": {}, "elapsed_ms": None}
        for line in resp[1:]:
            if line.startswith("S "):
                body = line[2:]
                if "=" in body:
                    k, v = body.split("=", 1)
                    if not v.startswith("__ERR__:"):
                        result["settings"][k] = v
            elif line.startswith("J "):
                body = line[2:]
                if "=" in body:
                    k, v = body.split("=", 1)
                    if not v.startswith("__ERR__:"):
                        try:
                            result["jni"][k] = int(v)
                        except ValueError:
                            pass
            elif line.startswith("F "):
                body = line[2:]
                if "=" in body:
                    k, v = body.split("=", 1)
                    if not v.startswith("__ERR__:"):
                        result["flags"][k] = v
            elif line.startswith("ELAPSED "):
                try:
                    result["elapsed_ms"] = int(line.split(" ", 1)[1].strip())
                except ValueError:
                    pass
        _adb_log(
            f"daemon_query s={len(result['settings'])}/{len(safe_settings)} "
            f"j={len(result['jni'])}/{len(safe_jni)} elapsed={result['elapsed_ms']}ms"
        )
        return result

    @classmethod
    def _format_batch_line(cls, op):
        kind = op[0]
        if kind == "PUT":
            key = cls._validate_daemon_token(op[1], "PUT key")
            val = str(op[2])
            if "\r" in val or "\n" in val or "\x00" in val:
                raise ValueError(f"Invalid PUT value containing control char: {val!r}")
            return f"PUT {key} {val}"
        if kind == "JNI_SET":
            key = cls._validate_daemon_token(op[1], "JNI_SET key")
            return f"JNI_SET {key} {int(op[2])} {int(op[3])}"
        if kind == "JNI_RAMP":
            key = cls._validate_daemon_token(op[1], "JNI_RAMP key")
            start = int(op[2])
            end = int(op[3])
            step_size = int(op[4])
            step_ms = int(op[5])
            upd = int(op[6])
            if step_size <= 0:
                raise ValueError(f"JNI_RAMP step_size must be positive: {step_size}")
            if step_ms < 0:
                raise ValueError(f"JNI_RAMP step_ms must be non-negative: {step_ms}")
            step_ms = min(step_ms, 1000)
            return f"JNI_RAMP {key} {start} {end} {step_size} {step_ms} {upd}"
        if kind == "COLOR_GAINS":
            return f"COLOR_GAINS {int(op[1])} {int(op[2])} {int(op[3])}"
        if kind == "HDR_TM":
            return f"HDR_TM {int(op[1])} {int(op[2])}"
        if kind == "SOUND_OUT":
            return f"SOUND_OUT {int(op[1])}"
        if kind == "LED":
            action = cls._validate_daemon_token(op[1], "LED action")
            args = [cls._validate_daemon_token(x, "LED arg") for x in op[2:]]
            return f"LED {action} {' '.join(args)}".strip()
        if kind == "VOL_SET":
            return f"VOL_SET {int(op[1])} {int(op[2])} {int(op[3])}"
        if kind == "VOL_MUTE":
            return f"VOL_MUTE {int(op[1])} {int(op[2])}"
        if kind == "KEY":
            key_code = cls._validate_daemon_token(op[1], "KEY code")
            return f"KEY {key_code}"
        if kind == "SLEEP":
            return f"SLEEP {max(0, int(op[1]))}"
        if kind == "REFRESH_PQ":
            return "REFRESH_PQ"
        if kind == "SET_SOURCE":
            return f"SET_SOURCE {int(op[1])}"
        raise ValueError(f"Unsupported batch op: {op!r}")

    @staticmethod
    def _estimate_daemon_batch_timeout(ops):
        extra_ms = 0
        for op in ops:
            kind = op[0]
            if kind == "SLEEP":
                extra_ms += max(0, int(op[1]))
            elif kind == "JNI_RAMP":
                start = int(op[2])
                end = int(op[3])
                step_size = max(1, int(op[4]))
                step_ms = min(1000, max(0, int(op[5])))
                steps = (abs(end - start) + step_size - 1) // step_size
                extra_ms += min(15000, steps * step_ms)
        return max(8.0, 8.0 + (extra_ms / 1000.0) + (0.25 * len(ops)))

    def daemon_batch(self, ops, check=False):
        if not ops:
            return True, None, []
        if not self.is_daemon_mode() or not self.ip:
            return False, None, []
        lines = ["BATCH"] + [self._format_batch_line(op) for op in ops] + ["END"]
        payload = "\n".join(lines) + "\n"
        timeout = self._estimate_daemon_batch_timeout(ops)

        if not self.ensure_daemon(check=check):
            return False, None, []
        with self.transaction():
            # Write batches are not idempotent on socket timeout; never blindly replay them.
            resp = self._daemon_socket_request(payload, timeout=timeout)
        if not resp:
            return False, None, []

        head = resp[0]
        steps = []
        for line in resp[1:]:
            if line.startswith("STEP "):
                parts = line.split(" ", 3)
                if len(parts) >= 4:
                    try:
                        steps.append((int(parts[1]), parts[2], parts[3]))
                    except ValueError:
                        pass
        if head == "OK":
            if any(status == "ERR" for _, status, _ in steps):
                _adb_log(f"daemon_batch ops={len(ops)} => OK_WITH_STEP_ERR")
                return False, None, steps
            _adb_log(f"daemon_batch ops={len(ops)} => OK")
            return True, None, steps
        if head.startswith("ERR_AT "):
            rest = head[7:].split(" ", 1)
            try:
                err_idx = int(rest[0])
            except (TypeError, ValueError):
                err_idx = 0
            err_msg = rest[1] if len(rest) > 1 else ""
            _adb_log(f"daemon_batch ops={len(ops)} => ERR_AT {err_idx}: {err_msg}")
            return False, err_idx, steps
        return False, None, steps

    def _queue_batch_op(self, op, check=False):
        self._batch_ops.append(op)
        if check:
            self._batch_check_required = True

    @contextlib.contextmanager
    def batch_writes(self, prefetch_settings=(), prefetch_jni=(), check=None):
        if not self.is_daemon_mode():
            yield
            return
        outermost = self._batch_depth == 0
        if outermost:
            self._batch_check_required = bool(check)
        elif check:
            self._batch_check_required = True
        self._batch_depth += 1
        try:
            if outermost and (prefetch_settings or prefetch_jni):
                snapshot = self.daemon_query(
                    settings_keys=prefetch_settings,
                    jni_keys=prefetch_jni,
                    check=False,
                )
                if snapshot:
                    self._batch_read_cache.update(snapshot.get("settings") or {})
                    self._batch_jni_cache.update(snapshot.get("jni") or {})
            yield
        except Exception:
            if outermost:
                self._batch_ops.clear()
                self._batch_check_required = False
                self._batch_read_cache.clear()
                self._batch_jni_cache.clear()
            raise
        else:
            if outermost:
                effective_check = self._batch_check_required if check is None else bool(check)
                self._batch_check_required = False
                self.flush_batch(check=effective_check)
                self._batch_read_cache.clear()
                self._batch_jni_cache.clear()
        finally:
            self._batch_depth = max(0, self._batch_depth - 1)

    def batch_sleep(self, ms):
        delay_ms = max(0, int(ms))
        if delay_ms <= 0:
            return
        if self.is_daemon_mode() and self._batch_depth > 0:
            self._batch_ops.append(("SLEEP", delay_ms))
        else:
            time.sleep(delay_ms / 1000.0)

    @staticmethod
    def _coalesce_batch_ops(ops):
        last_refresh_idx = -1
        for i, op in enumerate(ops):
            if op[0] == "REFRESH_PQ":
                last_refresh_idx = i
        if last_refresh_idx < 0:
            return list(ops)
        return [
            op for i, op in enumerate(ops)
            if op[0] != "REFRESH_PQ" or i == last_refresh_idx
        ]

    def _exec_op_via_adb(self, op, check=False):
        kind = op[0]
        if kind == "PUT":
            return self._adb_put(op[1], op[2], check=check)
        if kind == "JNI_SET":
            return self._adb_jni_set(op[1], op[2], upd=op[3], check=check)
        if kind == "JNI_RAMP":
            return self._adb_jni_set(op[1], op[3], upd=op[6], check=check)
        if kind == "COLOR_GAINS":
            return self._adb_jni_set_color_gains(op[1], op[2], op[3], check=check)
        if kind == "HDR_TM":
            return self._adb_hdr_tone_mapping(op[1], upd=op[2], check=check)
        if kind == "SOUND_OUT":
            return self._adb_set_sound_output(op[1], check=check)
        if kind == "LED":
            return self._adb_colorful_led(op[1], *op[2:], check=check)
        if kind == "VOL_SET":
            return self._adb_set_media_volume(
                op[1], unmute_first=bool(op[2]), show_ui=bool(op[3]), check=check
            )
        if kind == "VOL_MUTE":
            return self._adb_set_media_muted(bool(op[1]), show_ui=bool(op[2]), check=check)
        if kind == "KEY":
            return self._adb_key(op[1], check=check)
        if kind == "SLEEP":
            time.sleep(max(0, int(op[1])) / 1000.0)
            return ""
        if kind == "REFRESH_PQ":
            return self._adb_refresh_pq(check=check)
        if kind == "SET_SOURCE":
            return self._adb_set_input_source(op[1], check=check)
        return ""

    def _exec_put_run_via_adb(self, put_ops, check=False):
        if len(put_ops) == 1:
            _, k, v = put_ops[0]
            self._adb_put(k, v, check=check)
            return
        _adb_log(f"settings put batch ({len(put_ops)} keys)")
        cmd = " && ".join(
            f"settings put global {shlex.quote(str(op[1]))} {shlex.quote(str(op[2]))}"
            for op in put_ops
        )
        self.shell(cmd, check=check)

    def _apply_daemon_step_fallbacks(self, chunk_ops, steps, check=False, include_missing=False):
        if not chunk_ops:
            return
        step_by_idx = {}
        for idx, status, msg in steps:
            if 0 <= idx < len(chunk_ops):
                step_by_idx[idx] = (status, msg)
        for idx, op in enumerate(chunk_ops):
            entry = step_by_idx.get(idx)
            if entry is None:
                if include_missing:
                    self._exec_op_via_adb(op, check=check)
                continue
            status, msg = entry
            if status == "ERR":
                if op[0] in ("VOL_SET", "VOL_MUTE") and "VOL_VERIFY_FAILED" in msg:
                    self._daemon_vol_supported = False
                self._exec_op_via_adb(op, check=check)

    def _execute_mixed_chunks(self, ops, check=False):
        i = 0
        n = len(ops)
        while i < n:
            op = ops[i]
            if op[0] == "SLEEP":
                self._exec_op_via_adb(op, check=check)
                i += 1
                continue
            if self._daemon_vol_supported is False and op[0] in ("VOL_SET", "VOL_MUTE"):
                self._exec_op_via_adb(op, check=check)
                i += 1
                continue
            if self._daemon_put_supported is False and op[0] == "PUT":
                j = i + 1
                while j < n and ops[j][0] == "PUT":
                    j += 1
                self._exec_put_run_via_adb(ops[i:j], check=check)
                i = j
                continue

            j = i
            while j < n:
                nxt = ops[j]
                if nxt[0] == "SLEEP":
                    break
                if self._daemon_vol_supported is False and nxt[0] in ("VOL_SET", "VOL_MUTE"):
                    break
                if self._daemon_put_supported is False and nxt[0] == "PUT":
                    break
                j += 1

            daemon_ops = ops[i:j]
            ok, err_idx, steps = self.daemon_batch(daemon_ops, check=check)
            if ok:
                self._apply_daemon_step_fallbacks(daemon_ops, steps, check=check)
            elif err_idx is not None:
                self._daemon_put_supported = False
                safe_idx = max(0, min(err_idx, len(daemon_ops)))
                self._apply_daemon_step_fallbacks(
                    daemon_ops[:safe_idx], steps, check=check, include_missing=True
                )
                self._execute_mixed_chunks(daemon_ops[safe_idx:], check=check)
            elif steps:
                self._apply_daemon_step_fallbacks(
                    daemon_ops, steps, check=check, include_missing=True
                )
            else:
                for sub_op in daemon_ops:
                    self._exec_op_via_adb(sub_op, check=check)
            i = j

    def flush_batch(self, check=None):
        if check is None:
            effective_check = self._batch_check_required
        else:
            effective_check = bool(check) or self._batch_check_required
        self._batch_check_required = False
        if not self._batch_ops:
            return True
        ops = self._coalesce_batch_ops(self._batch_ops)
        self._batch_ops = []

        if not self.is_daemon_mode() or not self.ip:
            for op in ops:
                self._exec_op_via_adb(op, check=effective_check)
            return True

        daemon_ready = self.ensure_daemon(check=effective_check)
        with self.transaction():
            has_puts = any(op[0] == "PUT" for op in ops)
            if (not has_puts or self._daemon_put_supported is not False) and (
                daemon_ready or self.ensure_daemon(check=effective_check)
            ):
                if self._daemon_vol_supported is False and any(
                    op[0] in ("VOL_SET", "VOL_MUTE") for op in ops
                ):
                    self._execute_mixed_chunks(ops, check=effective_check)
                    return True
                ok, err_idx, steps = self.daemon_batch(ops, check=effective_check)
                if ok:
                    if has_puts and self._daemon_put_supported is None:
                        self._daemon_put_supported = True
                    self._apply_daemon_step_fallbacks(ops, steps, check=effective_check)
                    return True
                if err_idx is not None:
                    self._daemon_put_supported = False
                    safe_idx = max(0, min(err_idx, len(ops)))
                    self._apply_daemon_step_fallbacks(
                        ops[:safe_idx], steps, check=effective_check, include_missing=True
                    )
                    self._execute_mixed_chunks(ops[safe_idx:], check=effective_check)
                    return True
                if steps:
                    self._apply_daemon_step_fallbacks(
                        ops, steps, check=effective_check, include_missing=True
                    )
                    return True

            if self._daemon_put_supported is False and (
                daemon_ready or self.ensure_daemon(check=effective_check)
            ):
                self._execute_mixed_chunks(ops, check=effective_check)
                return True

            for op in ops:
                self._exec_op_via_adb(op, check=effective_check)
            return False

    def _check_and_heal_remote_jar(self, filename, local_jar, check=False):
        with self.transaction():
            sdcard_jar = f"/sdcard/{filename}"
            cache_jar = f"/data/data/mitv.service/cache/{filename}"
            stat_out = self.shell(
                f"stat -c %s {shlex.quote(sdcard_jar)} 2>/dev/null || echo 0",
                check=check,
            )
            if adb_text_has_disconnected_marker(stat_out):
                return False
            sd_size_lines = str(stat_out or "").strip().splitlines()
            if not sd_size_lines:
                return False
            sd_size_text = sd_size_lines[-1]
            try:
                sd_size = int(sd_size_text)
            except (TypeError, ValueError):
                sd_size = 0
            local_size = 0
            if local_jar:
                try:
                    local_size = os.path.getsize(local_jar)
                except OSError:
                    local_size = 0
            jar_updated = False
            if sd_size < 1000 or (local_size > 0 and sd_size != local_size):
                if local_jar:
                    push_out = adb_run(
                        ["-s", self.serial, "push", local_jar, sdcard_jar],
                        check=check,
                    )
                    if isinstance(push_out, str) and (
                        not push_out.strip() or adb_text_has_disconnected_marker(push_out)
                    ):
                        _adb_log(f"WARNING: {filename} push 失败: {push_out}")
                        return False
                    jar_updated = True
                else:
                    _adb_log(f"WARNING: {filename} 本地未找到，无法推送到设备")
                    return False
            if jar_updated:
                if filename == "MtkDirectTool.jar":
                    self._daemon_needs_restart = True
                    self.stop_daemon()
                cp_out = self.shell(
                    build_tvservice_shell_command(["cp", sdcard_jar, cache_jar]),
                    check=check,
                )
                cp_lower = str(cp_out or "").lower()
                if adb_text_has_disconnected_marker(cp_out) or "exception" in cp_lower:
                    _adb_log(f"WARNING: {filename} TvService cp 失败: {cp_out}")
                    return False
                if filename == "MtkDirectTool.jar":
                    time.sleep(0.2)
            return True

    def check_and_heal_jar(self):
        if (
            self.is_daemon_mode()
            and self._batch_depth > 0
            and self._daemon_ready
            and self._daemon_jar_healed_ip == self.ip
        ):
            return True
        ok = self._check_and_heal_remote_jar(
            "MtkDirectTool.jar",
            get_mtk_direct_tool_path(),
        )
        if ok:
            self._daemon_jar_healed_ip = self.ip
        return ok

    def check_and_heal_colorful_led_tool(self, check=False):
        return self._check_and_heal_remote_jar(
            "ColorfulLedTool.jar",
            get_colorful_led_tool_path(),
            check=check,
        )

    def connect(self):
        # adb connect 客户端超时被杀时输出为空，但独立 ADB server 可能
        # 已在后台建立 transport；连接成败只以 get-state 为准。
        ok, _state = self.ensure_connected()
        return ok
    def ensure_connected(self):
        if not self.ip:
            return False, "unknown"
        state = adb_device_state(self.serial, timeout=2)
        if state == "device":
            return True, state
        # 传输已断开时原有 adb forward 映射随之失效，清除旧状态以便重建
        self._reset_daemon_state()
        adb_run(["connect", self.serial], timeout=5)
        state = adb_device_state(self.serial, timeout=3)
        return state == "device", state

    def reconnect(self):
        """强制重连：先 disconnect 清除 stale transport 再 ensure_connected。

        TCP 已断但 server 仍持有旧 transport 时，adb connect 会返回
        "already connected" 而实际不可用，必须先 disconnect。
        """
        if not self.ip:
            return False, "unknown"
        self._reset_daemon_state()
        adb_run(["disconnect", self.serial], timeout=3)
        return self.ensure_connected()

    def device_state(self, timeout=3):
        if not self.ip:
            return "unknown"
        return adb_device_state(self.serial, timeout=timeout)

    def _adb_get(self, k, check=False):
        v = self.shell(f"settings get global {shlex.quote(str(k))}", check=check)
        _adb_log(f"settings get {k} => {v}")
        return v

    def get(self, k, check=False):
        key = str(k)
        if self.is_daemon_mode():
            if self._batch_depth > 0 and key in self._batch_read_cache:
                v = self._batch_read_cache[key]
                _adb_log(f"settings get (batch cache) {key} => {v}")
                return v
            res = self.daemon_query(settings_keys=(key,), check=False)
            if res and key in res["settings"]:
                v = res["settings"][key]
                if self._batch_depth > 0:
                    self._batch_read_cache[key] = v
                _adb_log(f"settings get (daemon) {key} => {v}")
                return v
        v = self._adb_get(key, check=check)
        if self._batch_depth > 0:
            self._batch_read_cache[key] = v
        return v

    def _adb_put(self, k, v, check=False):
        _adb_log(f"settings put {k} = {v}")
        return self.shell(
            f"settings put global {shlex.quote(str(k))} {shlex.quote(str(v))}",
            check=check,
        )

    def put(self, k, v, check=False):
        key = str(k)
        val = str(v)
        if self.is_daemon_mode():
            if self._batch_depth > 0:
                _adb_log(f"settings put (batch) {key} = {val}")
                self._batch_read_cache[key] = val
                self._queue_batch_op(("PUT", key, val), check=check)
                return ""
            if self._daemon_put_supported is not False:
                ok, err_idx, _ = self.daemon_batch([("PUT", key, val)], check=False)
                if ok:
                    self._daemon_put_supported = True
                    _adb_log(f"settings put (daemon) {key} = {val}")
                    return ""
                if err_idx is not None:
                    self._daemon_put_supported = False
        return self._adb_put(key, val, check=check)

    def _adb_key(self, k, check=False):
        _adb_log(f"keyevent {k}")
        return self.shell(f"input keyevent {shlex.quote(str(k))}", check=check)

    def key(self, k, check=False):
        if self.is_daemon_mode():
            if self._batch_depth > 0:
                _adb_log(f"keyevent (batch) {k}")
                self._queue_batch_op(("KEY", str(k)), check=check)
                return ""
            ok, _, _ = self.daemon_batch([("KEY", str(k))], check=False)
            if ok:
                _adb_log(f"keyevent (daemon) {k}")
                return ""
        return self._adb_key(k, check=check)

    def _adb_colorful_led(self, action, *args, check=False):
        with self.transaction():
            _adb_log(f"colorful_led {action} {' '.join(map(str, args))}")
            if not self.check_and_heal_colorful_led_tool(check=check):
                return ""
            jar = "/data/data/mitv.service/cache/ColorfulLedTool.jar"
            parts = ["ColorfulLedTool", str(action)] + [str(a) for a in args]
            return self.shell(
                build_tvservice_app_process_command(jar, parts),
                check=check,
            )

    def colorful_led(self, action, *args, check=False):
        op = ("LED", str(action), *[str(a) for a in args])
        if self.is_daemon_mode():
            if self._batch_depth > 0:
                _adb_log(f"colorful_led (batch) {action} {' '.join(map(str, args))}")
                self._queue_batch_op(op, check=check)
                return ""
            ok, _, _ = self.daemon_batch([op], check=False)
            if ok:
                _adb_log(f"colorful_led (daemon) {action} {' '.join(map(str, args))}")
                return ""
        return self._adb_colorful_led(action, *args, check=check)

    def _adb_jni_set(self, key, val, upd=3, check=False):
        _adb_log(f"jni_set {key} = {val}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        command = build_tvservice_app_process_command(
            jar, ["MtkDirectTool", "set", key, val, upd]
        )
        return self.shell(command, check=check)

    def jni_set(self, key, val, upd=3, check=False):
        op = ("JNI_SET", str(key), int(val), int(upd))
        if self.is_daemon_mode():
            if self._batch_depth > 0:
                _adb_log(f"jni_set (batch) {key} = {val}")
                self._batch_jni_cache[str(key)] = int(val)
                self._queue_batch_op(op, check=check)
                return ""
            ok, _, _ = self.daemon_batch([op], check=False)
            if ok:
                _adb_log(f"jni_set (daemon) {key} = {val}")
                return ""
        return self._adb_jni_set(key, val, upd=upd, check=check)

    def _adb_hdr_tone_mapping(self, val, upd=3, check=False):
        _adb_log(f"hdr_tone_mapping = {val}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        command = build_tvservice_app_process_command(
            jar, ["MtkDirectTool", "setHdrToneMapping", val, upd]
        )
        return self.shell(command, check=check)

    def hdr_tone_mapping(self, val, upd=3, check=False):
        op = ("HDR_TM", int(val), int(upd))
        if self.is_daemon_mode():
            if self._batch_depth > 0:
                _adb_log(f"hdr_tone_mapping (batch) = {val}")
                self._batch_jni_cache["g_video__vid_hdr_tone_mapping_mode"] = int(val)
                self._queue_batch_op(op, check=check)
                return ""
            ok, _, _ = self.daemon_batch([op], check=False)
            if ok:
                _adb_log(f"hdr_tone_mapping (daemon) = {val}")
                return ""
        return self._adb_hdr_tone_mapping(val, upd=upd, check=check)

    def _adb_set_sound_output(self, device_id, check=False):
        """通过 TvService 3 调用 MtkDirectTool 切换音频输出设备（不在此处写 Settings.Global）。"""
        _adb_log(f"set_sound_output = {device_id}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        command = build_tvservice_app_process_command(
            jar, ["MtkDirectTool", "setSoundOutput", int(device_id)]
        )
        return self.shell(command, check=check)

    def set_sound_output(self, device_id, check=False):
        """通过 TvService 3 调用 MtkDirectTool 切换音频输出设备（不在此处写 Settings.Global）。"""
        op = ("SOUND_OUT", int(device_id))
        if self.is_daemon_mode():
            if self._batch_depth > 0:
                _adb_log(f"set_sound_output (batch) = {device_id}")
                self._queue_batch_op(op, check=check)
                return ""
            ok, _, _ = self.daemon_batch([op], check=False)
            if ok:
                _adb_log(f"set_sound_output (daemon) = {device_id}")
                return ""
        return self._adb_set_sound_output(device_id, check=check)

    def _adb_set_input_source(self, source_id, check=False):
        """原始 ADB 回退方式切换外部输入信号源。"""
        target = int(source_id)
        _adb_log(f"set_input_source (adb) = {target}")
        self.shell("am force-stop com.xiaomi.mitv.tvplayer", check=check)
        return self.shell(
            f"am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY -n com.xiaomi.mitv.tvplayer/.ExternalSourceActivity --ei input {target} -f 0x10000000",
            check=check,
        )

    def _jar_set_input_source(self, source_id):
        """通过设备端 MtkDirectTool.jar CLI 切换信号源，失败返回 False 以便回退。"""
        if not self.ip or not self.check_and_heal_jar():
            return False
        target = int(source_id)
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        result_file = "/sdcard/Download/Mimonitor_Toolbox/.mtk_source_result.txt"
        set_cmd = build_tvservice_app_process_command(
            jar, ["MtkDirectTool", "setSource", target]
        )
        cmd = (
            f"mkdir -p /sdcard/Download/Mimonitor_Toolbox; "
            f"rm -f {result_file}; "
            f"{set_cmd} >/dev/null; "
            f"i=0; while [ $i -lt 30 ] && [ ! -f {result_file} ]; do sleep 0.1; i=$((i+1)); done; "
            f"cat {result_file} 2>/dev/null"
        )
        out = self.shell(cmd, check=False)
        text = str(out or "")
        return (
            "RESULT: SET_SOURCE" in text
            and not adb_text_has_disconnected_marker(text)
            and "ERROR" not in text
            and "Exception" not in text
        )

    def set_input_source(self, source_id, check=False):
        """优先通过 MtkDirectTool.jar（Daemon / CLI）切换信号源，不可用时回退原始 ADB。"""
        target = int(source_id)
        op = ("SET_SOURCE", target)
        if self.is_daemon_mode():
            if self._batch_depth > 0:
                _adb_log(f"set_input_source (batch) = {target}")
                self._batch_read_cache["mitv.tvplayer.hdmi.last.source"] = str(target)
                self._queue_batch_op(op, check=check)
                return ""
            ok, _, _ = self.daemon_batch([op], check=False)
            if ok:
                _adb_log(f"set_input_source (daemon) = {target}")
                return ""
        if self._jar_set_input_source(target):
            _adb_log(f"set_input_source (jar) = {target}")
            return ""
        return self._adb_set_input_source(target, check=check)

    def _adb_media_volume(self, check=False):
        """读取显示器媒体音量（0~100）。优先走 AudioService getStreamVolumeForMute。"""
        out = self.shell(
            f"service call audio {_AUDIO_SERVICE_TXN_GET_STREAM_VOLUME_FOR_MUTE} i32 {_AUDIO_STREAM_MUSIC}",
            check=check,
        )
        val = parse_service_call_int(out)
        if val is not None and 0 <= val <= 100:
            _adb_log(f"media_volume => {val}")
            return val
        raw = str(self.shell("settings get system volume_music_speaker", check=check) or "").strip()
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None and 0 <= parsed <= 100:
            _adb_log(f"media_volume (fallback) => {parsed}")
            return parsed
        return None

    def media_volume(self, check=False):
        """读取显示器媒体音量（0~100）。优先走 AudioService getStreamVolumeForMute。"""
        if self.is_daemon_mode():
            res = self.daemon_query(flags=("volume",), check=False)
            if res and "volume" in res["flags"]:
                try:
                    val = int(res["flags"]["volume"])
                    if 0 <= val <= 100:
                        _adb_log(f"media_volume (daemon) => {val}")
                        return val
                except (TypeError, ValueError):
                    pass
        return self._adb_media_volume(check=check)

    def _adb_media_muted(self, check=False):
        """读取显示器媒体流静音状态。"""
        out = self.shell(
            f"service call audio {_AUDIO_SERVICE_TXN_IS_STREAM_MUTE} i32 {_AUDIO_STREAM_MUSIC}",
            check=check,
        )
        val = parse_service_call_int(out)
        if val is None:
            return None
        muted = val != 0
        _adb_log(f"media_muted => {muted}")
        return muted

    def media_muted(self, check=False):
        """读取显示器媒体流静音状态。"""
        if self.is_daemon_mode():
            res = self.daemon_query(flags=("muted",), check=False)
            if res and "muted" in res["flags"]:
                raw = res["flags"]["muted"]
                if raw in ("0", "1"):
                    muted = raw == "1"
                    _adb_log(f"media_muted (daemon) => {muted}")
                    return muted
        return self._adb_media_muted(check=check)

    def _apply_media_volume_by_keyevent(self, target, check=False):
        current = self.media_volume(check=check)
        if current is None:
            return
        delta = target - current
        if delta == 0:
            return
        key = "KEYCODE_VOLUME_UP" if delta > 0 else "KEYCODE_VOLUME_DOWN"
        batch = " ".join([key] * min(abs(delta), 100))
        self.shell(f"input keyevent {batch}", check=check)

    def _adb_set_media_volume(self, value, unmute_first=False, show_ui=False, check=False):
        target = max(0, min(100, int(value)))
        flags = 1 if show_ui else 0
        _adb_log(f"set_media_volume = {target} (unmute_first={bool(unmute_first)}, show_ui={bool(show_ui)})")
        if unmute_first:
            self._adb_set_media_muted(False, show_ui=False, check=check)
        if self._media_volume_binder_verified is not False:
            out = self.shell(
                f"service call audio {_AUDIO_SERVICE_TXN_SET_STREAM_VOLUME} "
                f"i32 {_AUDIO_STREAM_MUSIC} i32 {target} i32 {flags} s16 {_AUDIO_BINDER_SHELL_PACKAGE}",
                check=check,
            )
            if service_call_succeeded(out):
                if self._media_volume_binder_verified is None:
                    applied = self.media_volume(check=check) == target
                    self._media_volume_binder_verified = applied
                    if applied:
                        return True
                else:
                    return True
            else:
                self._media_volume_binder_verified = False
        self._apply_media_volume_by_keyevent(target, check=check)
        return False

    def set_media_volume(self, value, unmute_first=False, show_ui=False, check=False):
        """绝对设置显示器媒体音量（0~100）。返回是否使用了 binder 快速通道。"""
        target = max(0, min(100, int(value)))
        if self.is_daemon_mode() and self._daemon_vol_supported is not False:
            op = ("VOL_SET", target, int(bool(unmute_first)), int(bool(show_ui)))
            if self._batch_depth > 0:
                _adb_log(f"set_media_volume (batch) = {target}")
                self._queue_batch_op(op, check=check)
                return True
            ok, _, steps = self.daemon_batch([op], check=False)
            if ok and not any(st == "ERR" and "VOL_VERIFY_FAILED" in msg for _, st, msg in steps):
                self._daemon_vol_supported = True
                _adb_log(f"set_media_volume (daemon) = {target}")
                return True
            if steps and any("VOL_VERIFY_FAILED" in msg for _, _, msg in steps):
                self._daemon_vol_supported = False
        return self._adb_set_media_volume(value, unmute_first=unmute_first, show_ui=show_ui, check=check)

    def _adb_set_media_muted(self, muted, show_ui=False, check=False):
        muted_bool = bool(muted)
        flags = 1 if show_ui else 0
        _adb_log(f"set_media_muted = {muted_bool} (show_ui={bool(show_ui)})")
        if self._media_volume_binder_verified is not False:
            direction = _AUDIO_ADJUST_MUTE if muted_bool else _AUDIO_ADJUST_UNMUTE
            out = self.shell(
                f"service call audio {_AUDIO_SERVICE_TXN_ADJUST_STREAM_VOLUME} "
                f"i32 {_AUDIO_STREAM_MUSIC} i32 {direction} i32 {flags} s16 {_AUDIO_BINDER_SHELL_PACKAGE}",
                check=check,
            )
            if service_call_succeeded(out):
                if self._media_volume_binder_verified is None:
                    applied = self.media_muted(check=check) == muted_bool
                    self._media_volume_binder_verified = applied
                    if applied:
                        return True
                else:
                    return True
            else:
                self._media_volume_binder_verified = False
        self.shell("input keyevent KEYCODE_VOLUME_MUTE", check=check)
        return False

    def set_media_muted(self, muted, show_ui=False, check=False):
        """设置/解除显示器媒体流静音。返回是否使用了 binder 快速通道。"""
        muted_bool = bool(muted)
        if self.is_daemon_mode() and self._daemon_vol_supported is not False:
            op = ("VOL_MUTE", int(muted_bool), int(bool(show_ui)))
            if self._batch_depth > 0:
                _adb_log(f"set_media_muted (batch) = {muted_bool}")
                self._queue_batch_op(op, check=check)
                return True
            ok, _, steps = self.daemon_batch([op], check=False)
            if ok and not any(st == "ERR" and "VOL_VERIFY_FAILED" in msg for _, st, msg in steps):
                self._daemon_vol_supported = True
                _adb_log(f"set_media_muted (daemon) = {muted_bool}")
                return True
            if steps and any("VOL_VERIFY_FAILED" in msg for _, _, msg in steps):
                self._daemon_vol_supported = False
        return self._adb_set_media_muted(muted, show_ui=show_ui, check=check)

    def _adb_jni_set_color_gains(self, red, green, blue, check=False):
        _adb_log(f"jni_set_color_gains r={red} g={green} b={blue}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        command = build_tvservice_app_process_command(
            jar, ["MtkDirectTool", "setColorGains", red, green, blue]
        )
        return self.shell(command, check=check)

    def jni_set_color_gains(self, red, green, blue, check=False):
        op = ("COLOR_GAINS", int(red), int(green), int(blue))
        if self.is_daemon_mode():
            if self._batch_depth > 0:
                _adb_log(f"jni_set_color_gains (batch) r={red} g={green} b={blue}")
                self._queue_batch_op(op, check=check)
                return ""
            ok, _, _ = self.daemon_batch([op], check=False)
            if ok:
                _adb_log(f"jni_set_color_gains (daemon) r={red} g={green} b={blue}")
                return ""
        return self._adb_jni_set_color_gains(red, green, blue, check=check)

    def _adb_jni_get(self, key, check=False):
        k = str(key)
        if k and all(ch.isalnum() or ch == "_" for ch in k):
            batch_vals = self._adb_jni_batch_get([k], check=False)
            if k in batch_vals:
                v = str(batch_vals[k])
                _adb_log(f"jni_get (file) {k} => {v}")
                return v
        with self.transaction():
            jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            self.shell("logcat -c", check=check)
            command = build_tvservice_app_process_command(
                jar, ["MtkDirectTool", "get", k]
            )
            self.shell(command, check=check)
            time.sleep(0.8)
            log = self.shell(
                f"logcat -d | grep {shlex.quote('GET ' + k)} | tail -1",
                check=check,
            )
        i = log.find("= ")
        if check and i < 0:
            raise RuntimeError(f"未读取到 JNI 值：{k}")
        v = log[i+2:].strip() if i >= 0 else "N/A"
        _adb_log(f"jni_get {k} => {v}")
        return v

    def jni_get(self, key, check=False):
        k = str(key)
        if self.is_daemon_mode():
            if self._batch_depth > 0 and k in self._batch_jni_cache:
                v = str(self._batch_jni_cache[k])
                _adb_log(f"jni_get (batch cache) {k} => {v}")
                return v
            res = self.daemon_query(jni_keys=(k,), check=False)
            if res and k in res["jni"]:
                v = str(res["jni"][k])
                if self._batch_depth > 0:
                    self._batch_jni_cache[k] = res["jni"][k]
                _adb_log(f"jni_get (daemon) {k} => {v}")
                return v
        return self._adb_jni_get(k, check=check)

    def _adb_jni_batch_get(self, keys, check=False):
        safe_keys = []
        for key in keys:
            key = str(key)
            if key and all(ch.isalnum() or ch == "_" for ch in key):
                safe_keys.append(key)
        if not safe_keys:
            return {}

        with self.transaction():
            jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            batch_command = build_tvservice_app_process_command(
                jar, ["MtkDirectTool", "batchGet"] + safe_keys
            )
            cmd = (
                f"mkdir -p /sdcard/Download/Mimonitor_Toolbox; "
                f"rm -f {MTK_BATCH_RESULT_FILE}; "
                f"{batch_command} >/dev/null; "
                f"i=0; while [ $i -lt 30 ] && [ ! -f {MTK_BATCH_RESULT_FILE} ]; do sleep 0.1; i=$((i+1)); done; "
                f"cat {MTK_BATCH_RESULT_FILE} 2>/dev/null"
            )
            out = self.shell(cmd, check=check)

        vals = parse_jni_batch_output(out)
        if check and not vals:
            raise RuntimeError("未读取到批量 JNI 值")
        _adb_log(f"jni_batch_get {','.join(safe_keys)} => {vals}")
        return vals

    def jni_batch_get(self, keys, check=False):
        if self.is_daemon_mode():
            safe_keys = [
                str(k) for k in keys
                if str(k) and all(ch.isalnum() or ch == "_" for ch in str(k))
            ]
            if not safe_keys:
                return {}
            if self._batch_depth > 0 and all(k in self._batch_jni_cache for k in safe_keys):
                cached = {k: self._batch_jni_cache[k] for k in safe_keys}
                _adb_log(f"jni_batch_get (batch cache) => {cached}")
                return cached
            res = self.daemon_query(jni_keys=safe_keys, check=False)
            if res and res["jni"]:
                if self._batch_depth > 0:
                    self._batch_jni_cache.update(res["jni"])
                _adb_log(f"jni_batch_get (daemon) => {res['jni']}")
                return dict(res["jni"])
        return self._adb_jni_batch_get(keys, check=check)

    def jni_batch_get_or_single(self, keys, check=False):
        vals = self.jni_batch_get(keys)
        for key in keys:
            if key in vals:
                continue
            try:
                vals[key] = int(self.jni_get(key, check=check))
            except Exception:
                if check:
                    raise
                pass
        return vals

    def _adb_refresh_pq(self, check=False):
        _adb_log("refresh_pq")
        return self.shell("am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7", check=check)

    def refresh_pq(self, check=False):
        if self.is_daemon_mode():
            if self._batch_depth > 0:
                _adb_log("refresh_pq (batch)")
                self._queue_batch_op(("REFRESH_PQ",), check=check)
                return ""
            ok, _, _ = self.daemon_batch([("REFRESH_PQ",)], check=False)
            if ok:
                _adb_log("refresh_pq (daemon)")
                return ""
        return self._adb_refresh_pq(check=check)
    def get_model(self):
        m = self.shell("getprop ro.product.model")
        _adb_log(f"get_model => {m}")
        return m


def is_mitv_model(model):
    return "mitv" in str(model or "").lower()


_ignored_scan_ips_lock = threading.Lock()
_ignored_scan_ips: set[str] = set()


def ignore_scan_ip(ip):
    text = str(ip or "").strip()
    if not text:
        return
    with _ignored_scan_ips_lock:
        _ignored_scan_ips.add(text)


def unignore_scan_ip(ip):
    text = str(ip or "").strip()
    if not text:
        return
    with _ignored_scan_ips_lock:
        _ignored_scan_ips.discard(text)


def clear_ignored_scan_ips():
    with _ignored_scan_ips_lock:
        _ignored_scan_ips.clear()


def get_ignored_scan_ips():
    with _ignored_scan_ips_lock:
        return set(_ignored_scan_ips)


def _list_adb_device_serials(timeout=2):
    out = adb_run(["devices"], timeout=timeout)
    serials = set()
    for line in str(out or "").splitlines():
        text = line.strip()
        if not text or text.startswith("List of devices"):
            continue
        parts = text.split()
        if parts:
            serials.add(parts[0])
    return serials


def scan_adb(cb=None, log=None, cancel_event=None, *, skip_ignored=True):
    """扫描全部有效物理网卡，并通过临时 ADB 连接读取设备型号。"""

    cancel_event = cancel_event or threading.Event()
    settings = load_settings()
    networks = get_windows_scan_networks(
        log=log,
        force_devices=settings.get("scan_force_devices") or (),
        force_subnets=settings.get("scan_force_subnets") or (),
        block_devices=settings.get("scan_block_devices") or (),
    )
    if not networks:
        raise WindowsAdapterError("未找到可扫描的物理网卡")
    ensure_adb_server()
    preexisting_serials = _list_adb_device_serials()
    targets = build_probe_targets(networks, log=log)
    if skip_ignored:
        ignored_ips = get_ignored_scan_ips()
        if ignored_ips:
            targets = [item for item in targets if str(item.ip) not in ignored_ips]
    probe_results = probe_tcp_targets(targets, cancel_event, log=log)
    probe_results.sort(key=lambda item: int(item.ip))

    found = []
    for result in probe_results:
        if cancel_event.is_set():
            break
        ip = str(result.ip)
        if skip_ignored and ip in get_ignored_scan_ips():
            continue
        target = Adb(ip)
        should_disconnect = target.serial not in preexisting_serials
        attempted = False
        try:
            if log:
                log(f"[扫描] {target.serial} 开放，正在验证...")
            # 连接成败只以 get-state 为准：connect 输出为空（客户端超时被杀）
            # 不代表失败，独立 ADB server 可能已建立 transport。
            if cancel_event.is_set():
                break
            attempted = True
            state = target.ensure_connected()[1]
            if state != "device":
                if "unauthorized" in str(state or "").lower():
                    ignore_scan_ip(ip)
                if log:
                    log(f"[扫描] {ip} 状态为 {state}，跳过")
                continue
            if cancel_event.is_set():
                break
            model = target.shell("getprop ro.product.model", check=False).strip()
            if not model or adb_text_has_disconnected_marker(model):
                continue
            if is_mitv_model(model):
                unignore_scan_ip(ip)
            else:
                ignore_scan_ip(ip)
            found.append((ip, model))
            if cb:
                cb(ip, model)
        except Exception as exc:
            if log:
                log(f"[扫描] 验证 {ip} 失败: {exc}")
        finally:
            if attempted and should_disconnect:
                adb_run(["disconnect", target.serial], 3)

    if log:
        mitv_count = sum(1 for _ip, model in found if is_mitv_model(model))
        log(f"[扫描] 完成，发现 {len(found)} 台 ADB 设备，其中 {mitv_count} 台 MiTV")
    return found


def async_run(fn):
    def guarded():
        try:
            fn()
        except Exception as exc:
            task_name = getattr(fn, "__name__", fn.__class__.__name__)
            _adb_log(f"BACKGROUND ERROR [{task_name}]: {exc}")
            handler = _async_error_handler
            if handler is None:
                _logger.exception("后台任务 %s 执行失败", task_name)
                return
            try:
                handler(exc)
            except Exception:
                _logger.exception("后台任务错误处理器执行失败")

    worker = threading.Thread(target=guarded, daemon=True)
    worker.start()
    return worker
