"""应用启动、单实例通信和 Qt 事件循环。"""

import os
import sys
import threading
import traceback
from datetime import datetime

from PyQt6.QtCore import QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from .core import cleanup_stale_extract_dirs, get_app_data_dir, load_settings
from .main_window import App
from .widgets import apply_theme_mode


SERVER_NAME = "mitv_gpro27u_controller_single_instance"
_EXCEPTION_LOG_LOCK = threading.Lock()
_SINGLE_INSTANCE_MAX_BYTES = 256


def _install_excepthook():
    """记录未捕获异常到日志，替代 PyQt6 默认的 qFatal 终止。

    PyQt6 对 Qt 回调内的未捕获异常默认调用 qFatal 终止进程；
    安装自定义 excepthook 后改为记录日志继续运行。
    """
    def _hook(exc_type, exc_value, exc_tb):
        try:
            log_path = os.path.join(get_app_data_dir(), "logs", "exceptions.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with _EXCEPTION_LOG_LOCK:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 未捕获异常:\n")
                    traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
                    f.write("\n")
        except Exception:
            pass  # 日志写入失败时静默，不影响程序运行

    sys.excepthook = _hook

    def _thread_hook(args):
        _hook(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = _thread_hook


def _handle_single_instance_socket(client_socket, window):
    """安全处理单实例客户端连接：限制读取上限并容忍非 UTF-8 字节，不阻塞事件循环。"""
    chunks = bytearray()
    finished = False

    def _finish():
        nonlocal finished
        if finished:
            return
        finished = True
        try:
            client_socket.close()
        except Exception:
            pass
        client_socket.deleteLater()

    def _consume():
        if finished:
            return
        available = client_socket.bytesAvailable()
        if available > 0:
            remaining = _SINGLE_INSTANCE_MAX_BYTES - len(chunks)
            if remaining > 0:
                chunks.extend(bytes(client_socket.read(remaining)))
            else:
                _finish()
                return
        message = bytes(chunks).decode("utf-8", errors="replace").strip()
        if message == "show":
            window.show_and_raise()
            _finish()
        elif len(chunks) >= _SINGLE_INSTANCE_MAX_BYTES:
            _finish()

    def _on_disconnected():
        if not finished and client_socket.bytesAvailable() > 0:
            _consume()
        _finish()

    timeout_timer = QTimer(client_socket)
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(_finish)
    timeout_timer.start(500)

    client_socket.readyRead.connect(_consume)
    client_socket.disconnected.connect(_on_disconnected)
    if client_socket.bytesAvailable() > 0:
        _consume()


def _accept_single_instance_connections(local_server, window):
    """清空所有排队的单实例连接，防止并发唤醒丢失。"""
    while local_server.hasPendingConnections():
        client_socket = local_server.nextPendingConnection()
        if client_socket is not None:
            _handle_single_instance_socket(client_socket, window)


def main() -> int:
    _install_excepthook()
    # 后台清理 onefile/_MEI 解压残留，不阻塞启动
    threading.Thread(target=cleanup_stale_extract_dirs, daemon=True).start()
    application = QApplication(sys.argv)

    check_socket = QLocalSocket()
    check_socket.connectToServer(SERVER_NAME)
    if check_socket.waitForConnected(500):
        check_socket.write(b"show")
        check_socket.waitForBytesWritten(500)
        check_socket.close()
        return 0

    local_server = QLocalServer()
    local_server.removeServer(SERVER_NAME)
    if not local_server.listen(SERVER_NAME):
        return 1

    settings = load_settings()
    apply_theme_mode(settings.get("theme", "dark"))

    window = App()
    local_server.newConnection.connect(
        lambda: _accept_single_instance_connections(local_server, window)
    )

    if "--minimized" not in sys.argv:
        window.show()
    return application.exec()


__all__ = ["main"]
