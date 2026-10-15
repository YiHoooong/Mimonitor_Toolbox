"""主窗口状态、托盘、原生消息和应用生命周期。"""

import ctypes
import os
import subprocess
import sys
import threading
import time

from PyQt6.QtCore import QRect, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMenu,
    QSystemTrayIcon,
)
from qfluentwidgets import (
    Action,
    FluentIcon as FIF,
    FluentWindow,
    MenuAnimationType,
    MenuIndicatorType,
    MessageBox,
)

from . import adb as adb_runtime
from .adb import (
    _adb_command_lock,
    adb_run,
    adb_text_has_disconnected_marker,
    block_adb_spawns,
    cleanup_adb_processes,
    drain_startup_warnings,
    is_connected_status_text,
    normalize_status_text,
    set_async_error_handler,
    unblock_adb_spawns,
)
from .core import (
    ADJUSTABLE_HOTKEY_PARAMS,
    HOTKEY_EXTRA_VK,
    get_log_dir,
    load_settings,
    update_settings,
)
from .device_features import DeviceFeaturesMixin
from .display_features import DisplayFeaturesMixin
from .pages import PagesMixin
from .widgets import (
    CloseConfirmDialog,
    OsdHud,
    CurrentPresetBanner,
    TrayMenu,
    TrayOptionMenu,
    TraySliderCard,
)
from .windows import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    WM_DISPLAYCHANGE,
    WM_ENDSESSION,
    WM_HOTKEY,
    WM_QUERYENDSESSION,
    WM_SETTINGCHANGE,
    dispatch_power_broadcast,
    get_autostart_path,
    get_executable_path,
    install_autostart,
    remove_autostart,
    user32,
)


class App(PagesMixin, DisplayFeaturesMixin, DeviceFeaturesMixin, FluentWindow):
    # Signals for thread-safe UI updates
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    values_signal = pyqtSignal(dict)
    jni_values_signal = pyqtSignal(dict)
    devices_signal = pyqtSignal(int, list)
    scan_finished_signal = pyqtSignal(int, str, list, str)
    message_signal = pyqtSignal(str, str, str) # type, title, text
    apk_install_finished = pyqtSignal(bool, str, str)
    guardian_status_signal = pyqtSignal(dict)
    guardian_migration_prompt_signal = pyqtSignal()
    auto_scan_signal = pyqtSignal()
    page_refresh_finished = pyqtSignal(str, bool)
    adb_action_finished = pyqtSignal(object, bool, str)
    adb_server_event = pyqtSignal(str, str)
    resume_reconnect_finished = pyqtSignal(int, int, bool, str)
    connection_recovery_requested = pyqtSignal(int, str, str)
    reconnect_target_probe_finished = pyqtSignal(int, bool)
    resume_discovery_finished = pyqtSignal(int, list, str)

    def __init__(self):
        super().__init__()
        QApplication.setQuitOnLastWindowClosed(False)
        self.initialize_device_features()
        self.mode_btns = {}
        self.sliders = {}
        self.state_buttons = {}
        self.color_gain_cards = []

        # 初始化日志文件路径（等用户开启时再创建文件）
        adb_runtime._log_path = os.path.join(get_log_dir(), f"log_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        self.is_forcing_exit = False
        self.adb_connected = False
        self._cleanup_done = False
        self._windows_session_ending = False
        self._scan_id = 0
        self._scan_running = False
        self._scan_cancel_event = None
        self._connection_in_progress = False

        # Window properties
        self.setWindowTitle("红米 G Pro 27U Toolbox")
        self.resize(1000, 750)

        # Connect signals
        self.log_signal.connect(self._on_log)
        self.status_signal.connect(self._on_status)
        self.values_signal.connect(self._apply_polled_values)
        self.jni_values_signal.connect(self._apply_polled_jni_values)
        self.devices_signal.connect(self._update_scanned_devices)
        self.scan_finished_signal.connect(self._finish_scan)
        self.message_signal.connect(self._show_message_box)
        self.apk_install_finished.connect(self._on_apk_install_finished)
        self.guardian_status_signal.connect(self._apply_guardian_status)
        self.guardian_migration_prompt_signal.connect(self._prompt_guardian_migration)
        self.auto_scan_signal.connect(lambda: self.scan_net(auto=True))
        self.page_refresh_finished.connect(self._finish_page_refresh)
        self.adb_action_finished.connect(self._finish_adb_action)
        self.adb_server_event.connect(self._on_adb_server_event)
        self.resume_reconnect_finished.connect(self._finish_resume_reconnect_attempt)
        self.connection_recovery_requested.connect(self._handle_connection_recovery_request)
        self.reconnect_target_probe_finished.connect(self._finish_reconnect_target_probe)
        self.resume_discovery_finished.connect(self._finish_resume_discovery)
        set_async_error_handler(self._report_background_error)

        # Setup layout and components
        self.osd = OsdHud(self)
        self.setup_ui()
        self.setup_tray()
        self.initialize_display_features()
        for warning in drain_startup_warnings():
            self.log_signal.emit(warning)
        self.register_global_hotkeys()

        # 页面切换时按需加载数据
        self.stackedWidget.currentChanged.connect(self._on_page_changed)
        self.adb_keepalive_timer = QTimer(self)
        self.adb_keepalive_timer.setInterval(15000)
        self.adb_keepalive_timer.timeout.connect(self._keep_adb_alive)
        self.adb_keepalive_timer.start()
        self.adb_server_monitor_timer = QTimer(self)
        self.adb_server_monitor_timer.setInterval(1000)
        self.adb_server_monitor_timer.timeout.connect(self._monitor_adb_server)
        self.adb_server_monitor_timer.start()
        self.hdr_memory_timer = QTimer(self)
        self.hdr_memory_timer.setInterval(3000)
        self.hdr_memory_timer.timeout.connect(lambda: self._poll_hdr_memory_state("timer"))
        self.hdr_memory_timer.start()
        self.initialize_preset_features()
        self._preset_sync()
        # 顶部指示条：显示当前使用的预设（无按钮）
        self._preset_banner = CurrentPresetBanner(self)
        QTimer.singleShot(0, self._update_preset_banner)
        QTimer.singleShot(0, self._update_hdr_memory_status_label)
        QTimer.singleShot(0, self._update_freesync_memory_status_label)
        QTimer.singleShot(900, self._auto_connect_on_startup)
        QApplication.instance().aboutToQuit.connect(self.cleanup_before_exit)

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)

        # Create a beautiful, crisp G Pro theme icon dynamically!
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#734EFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 28, 28)
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(18)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "G")
        painter.end()
        icon = QIcon(pixmap)

        self.tray_icon.setIcon(icon)
        self.setWindowIcon(icon)
        self.tray_icon.setToolTip("红米 G Pro 27U Toolbox")

        # 不调用 setContextMenu：那样右键会被 Qt 直接接管、activated 收不到
        # Context，也无法在每次弹出前重建菜单（打勾需要反映最新值）。
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def _tray_item_ids(self):
        ids = load_settings().get("tray_items", [])
        if not isinstance(ids, list):
            return []
        return [str(i) for i in ids]

    def _tray_submenu_icon(self, entry_id):
        candidates = {
            "picture_mode": "VIEW", "local_dimming": "BRIGHTNESS",
            "color_space": "PALETTE", "color_temp": "CONSTRACT",
            "response_time": "SPEED_HIGH", "freesync": "SYNC",
            "input_source": "LINK", "backlight": "BRIGHTNESS",
            "black_level": "PHOTO", "contrast": "CONSTRACT",
            "saturation": "PALETTE", "hue": "PALETTE",
            "sharpness": "ZOOM", "red_gain": "PALETTE",
            "green_gain": "PALETTE", "blue_gain": "PALETTE",
            "atmosphere_illumination": "BRIGHTNESS",
        }
        name = candidates.get(entry_id)
        return getattr(FIF, name) if name else None

    def build_tray_menu(self):
        """构建 Fluent 风格托盘菜单。每次弹出前重建，保证打勾是当前值。"""
        menu = TrayMenu(parent=self)

        status_action = Action(self.status_label.text() or "未连接", menu)
        status_action.setEnabled(False)
        menu.addAction(status_action)
        menu.addSeparator()

        entries = {e["id"]: e for e in self.tray_entry_catalog()}
        configured = [i for i in self._tray_item_ids() if i in entries]
        if not configured:
            empty = Action("还没添加快捷项，去「工具与设置」里挑几个", menu)
            empty.setEnabled(False)
            menu.addAction(empty)
        for entry_id in configured:
            entry = entries[entry_id]
            current = self.tray_entry_value(entry_id)
            icon = self._tray_submenu_icon(entry_id)
            if entry["kind"] == "options":
                submenu = TrayOptionMenu(
                    entry["label"], parent=menu, indicatorType=MenuIndicatorType.RADIO
                )
                for value, name in entry["options"]:
                    act = Action(icon, name, submenu) if icon is not None else Action(name, submenu)
                    act.setCheckable(True)
                    if current is not None and int(current) == int(value):
                        act.setChecked(True)
                    act.triggered.connect(
                        lambda _checked=False, eid=entry_id, v=value: self.tray_apply_option(eid, v)
                    )
                    submenu.addAction(act)
                if icon is not None:
                    submenu.setIcon(icon.icon())
                menu.addMenu(submenu)
            else:
                # 数值条目：菜单行保留「标签   数值」，悬停弹出独立圆角浮条
                # （TraySliderCard）。数值就显示在这一行，拖动时实时同步。
                card = TraySliderCard(
                    icon,
                    entry["label"],
                    entry["min"],
                    entry["max"],
                    current if current is not None else entry["min"],
                    # 走 lambda 延迟查找 self.tray_set_value：直接传方法引用会在构造时
                    # 就绑死，测试里 patch 不掉（也拿不到后续替换）
                    on_change=lambda eid, value: self.tray_set_value(eid, value),
                    step=entry.get("step"),
                    entry_id=entry_id,
                    parent=menu,
                )
                card.set_connected(self.adb_connected)
                if icon is not None:
                    # addMenu 画的是 menu.icon()，不设的话这一行就没有图标，
                    # 和取值型行不一致
                    card.setIcon(icon.icon())
                menu.addMenu(card)
                # 行宽预留到取值范围内最长那一档，拖到 99→100 不会撑开菜单
                card.sync_row_text(card.value())

        menu.addSeparator()
        show_action = Action(FIF.HOME, "显示主窗口", menu)
        show_action.triggered.connect(self.show_and_raise)
        menu.addAction(show_action)
        exit_action = Action(FIF.POWER_BUTTON, "退出程序", menu)
        exit_action.triggered.connect(self.force_exit)
        menu.addAction(exit_action)
        return menu

    def popup_tray_menu(self):
        # 每次弹出都重建（打勾要反映最新值），旧的显式释放避免累积
        previous = getattr(self, "_tray_menu", None)
        if previous is not None:
            previous.deleteLater()
        self._tray_menu = self.build_tray_menu()
        # ani=False 在库里其实没被使用，必须传 aniType=NONE 才真的不播放动画；
        # 子菜单同理，见 TrayOptionMenu / TraySliderCard
        self._tray_menu.exec(QCursor.pos(), aniType=MenuAnimationType.NONE)

    def _check_adb_when_restored(self):
        if getattr(self, "adb_connected", False) and getattr(self.adb, "ip", ""):
            self._keep_adb_alive()

    def _restore_main_window(self):
        state = self.windowState()
        self.setWindowState((state & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.showNormal()
        self.raise_()
        self.activateWindow()

        if sys.platform == "win32" and user32:
            try:
                hwnd = int(self.winId())
                SW_RESTORE = 9
                HWND_TOPMOST = -1
                HWND_NOTOPMOST = -2
                SWP_NOSIZE = 0x0001
                SWP_NOMOVE = 0x0002
                SWP_SHOWWINDOW = 0x0040
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
                user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            except Exception:
                pass

    def show_and_raise(self):
        self._restore_main_window()
        QTimer.singleShot(0, self._restore_main_window)
        QTimer.singleShot(120, self._restore_main_window)
        QTimer.singleShot(250, self._check_adb_when_restored)


    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self.popup_tray_menu()
            return
        if reason == QSystemTrayIcon.ActivationReason.Trigger or reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            is_minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)
            if self.isVisible() and not is_minimized:
                self.hide()
            else:
                self.show_and_raise()

    def register_global_hotkeys(self):
        if sys.platform != "win32" or not user32:
            return
            
        self.unregister_all_hotkeys()
        
        settings = load_settings()
        hotkeys = settings.get("hotkeys", {})
        adjust_hotkeys = settings.get("adjust_hotkeys", [])
        
        self.hotkey_registry = {}
        
        mod_map = {
            "无": 0,
            "Ctrl + Alt": MOD_CONTROL | MOD_ALT,
            "Ctrl + Shift": MOD_CONTROL | MOD_SHIFT,
            "Alt + Shift": MOD_ALT | MOD_SHIFT,
            "Win + Shift": MOD_WIN | MOD_SHIFT
        }
        
        vk_map = {}
        for i in range(1, 13):
            vk_map[f"F{i}"] = 0x6F + i
        for i in range(0, 10):
            vk_map[str(i)] = 0x30 + i
        for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            vk_map[char] = ord(char)
        vk_map.update(HOTKEY_EXTRA_VK)
            
        hwnd = int(self.winId())
        
        hotkey_id = 1
        def register_one(payload, hk_conf):
            nonlocal hotkey_id
            mod_str = hk_conf.get("modifier", "无")
            key_str = hk_conf.get("key", "无")
            if mod_str == "无" and key_str == "无":
                return
                
            mod_val = mod_map.get(mod_str, 0)
            vk_val = vk_map.get(key_str, 0)
            if vk_val == 0:
                return
                
            res = user32.RegisterHotKey(hwnd, hotkey_id, mod_val, vk_val)
            if res:
                self.hotkey_registry[hotkey_id] = payload
                hotkey_id += 1
            else:
                label = payload.get("action") if isinstance(payload, dict) else str(payload)
                if isinstance(payload, dict) and payload.get("type") == "adjust":
                    cfg = ADJUSTABLE_HOTKEY_PARAMS.get(payload.get("rule", {}).get("param"), {})
                    label = cfg.get("label", "可调参数")
                self.log(f"快捷键注册失败或冲突: {label} ({mod_str} + {key_str})")

        for action_name, hk_conf in hotkeys.items():
            register_one({"type": "cycle", "action": action_name}, hk_conf)

        for rule in adjust_hotkeys:
            if isinstance(rule, dict):
                register_one({"type": "adjust", "rule": rule}, rule)

    def unregister_all_hotkeys(self):
        if sys.platform != "win32" or not user32 or not hasattr(self, "hotkey_registry"):
            return
        hwnd = int(self.winId())
        for hid in list(getattr(self, "hotkey_registry", {}).keys()):
            user32.UnregisterHotKey(hwnd, hid)
        self.hotkey_registry = {}

    def nativeEvent(self, eventType, message):
        if sys.platform == "win32" and eventType == b"windows_generic_MSG" and user32:
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_QUERYENDSESSION:
                self._windows_session_ending = True
                block_adb_spawns()
                return True, 1
            if msg.message == WM_ENDSESSION:
                if msg.wParam:
                    self._windows_session_ending = True
                    self.cleanup_before_exit()
                else:
                    self._windows_session_ending = False
                    unblock_adb_spawns()
                return True, 0
            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                if hotkey_id in getattr(self, "hotkey_registry", {}):
                    payload = self.hotkey_registry[hotkey_id]
                    if isinstance(payload, dict) and payload.get("type") == "adjust":
                        self.trigger_adjust_hotkey(payload.get("rule", {}))
                    else:
                        action = payload.get("action") if isinstance(payload, dict) else payload
                        self.trigger_hotkey_action(action)
                return True, 0
            if dispatch_power_broadcast(
                msg.message,
                msg.wParam,
                self._handle_windows_resume,
            ):
                return True, 1
            if msg.message in (WM_DISPLAYCHANGE, WM_SETTINGCHANGE):
                self._schedule_hdr_memory_check("windows_event", delay_ms=600)
        return super().nativeEvent(eventType, message)
















































    def cleanup_before_exit(self):
        if getattr(self, "_cleanup_done", False):
            return
        self._cleanup_done = True
        set_async_error_handler(None)
        self._invalidate_connection_intent()
        self._cancel_resume_reconnect()
        self._cancel_scan("程序退出")
        session_ending = getattr(self, "_windows_session_ending", False)
        if session_ending:
            block_adb_spawns()
        try:
            self.unregister_all_hotkeys()
        except Exception:
            pass
        try:
            DeviceFeaturesMixin._stop_source_polling(self)
            if hasattr(self, "adb_keepalive_timer"):
                self.adb_keepalive_timer.stop()
            if hasattr(self, "adb_server_monitor_timer"):
                self.adb_server_monitor_timer.stop()
        except Exception:
            pass
        try:
            if hasattr(self, "_auto_task_timer"):
                self._auto_task_timer.stop()
            if hasattr(self, "_preset_sync_timer"):
                self._preset_sync_timer.stop()
            banner = getattr(self, "_preset_banner", None)
            if banner is not None:
                banner.hide()
        except Exception:
            pass
        try:
            if hasattr(self, "hdr_memory_timer"):
                self.hdr_memory_timer.stop()
            if hasattr(self, "_hdr_memory_apply_timer"):
                self._hdr_memory_apply_timer.stop()
            if hasattr(self, "_hdr_picture_refresh_timer"):
                self._hdr_picture_refresh_timer.stop()
        except Exception:
            pass
        if session_ending:
            cleanup_adb_processes(kill_server=False)
        else:
            with _adb_command_lock:
                try:
                    if self.adb.ip:
                        adb_run(["disconnect", self.adb.serial], timeout=3)
                except Exception:
                    pass
                block_adb_spawns()
                cleanup_adb_processes(kill_server=True)
        try:
            if adb_runtime._log_file:
                adb_runtime._log_file.flush()
                adb_runtime._log_file.close()
        except Exception:
            pass

    def force_exit(self):
        self.is_forcing_exit = True
        self.tray_icon.hide()
        self.cleanup_before_exit()
        QApplication.quit()

    def closeEvent(self, event):
        if getattr(self, "is_forcing_exit", False):
            self.cleanup_before_exit()
            event.accept()
            return
            
        settings = load_settings()
        if settings.get("never_ask_close", False):
            behavior = settings.get("close_behavior", "tray")
            if behavior == "tray":
                event.ignore()
                self.hide()
            else:
                self.tray_icon.hide()
                self.cleanup_before_exit()
                event.accept()
                QApplication.quit()
        else:
            dialog = CloseConfirmDialog(self)
            if dialog.exec():
                choice = dialog.choice
                remember = dialog.chk_remember.isChecked()
                dialog.deleteLater()
                
                update_settings({
                    "close_behavior": choice,
                    "never_ask_close": remember,
                })
                
                if choice == "tray":
                    event.ignore()
                    self.hide()
                    self.tray_icon.showMessage(
                        "红米 G Pro 27U Toolbox",
                        "程序已最小化到系统托盘。双击托盘图标可重新打开，您也可以在 [工具与设置] 页面更改设置。",
                        QSystemTrayIcon.MessageIcon.Information,
                        3000
                    )
                else:
                    self.tray_icon.hide()
                    self.cleanup_before_exit()
                    event.accept()
                    QApplication.quit()
            else:
                dialog.deleteLater()
                event.ignore()

    def _on_log(self, text):
        self.log_widget.append(text)
        scroll_bar = self.log_widget.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())
        if adb_runtime._log_file and adb_runtime._log_to_file_enabled:
            try:
                adb_runtime._log_file.write(text + "\n")
                adb_runtime._log_file.flush()
            except Exception: pass

    def _report_background_error(self, error):
        self.log_signal.emit(
            f"后台任务异常: {type(error).__name__}: {error}"
        )

    def _toggle_log_file(self, state):
        adb_runtime._log_to_file_enabled = (state == 2)
        if adb_runtime._log_to_file_enabled and not adb_runtime._log_file:
            try:
                os.makedirs(os.path.dirname(adb_runtime._log_path), exist_ok=True)
                adb_runtime._log_file = open(adb_runtime._log_path, "a", encoding="utf-8")
                adb_runtime._log_file.write(f"{'='*60}\n")
                adb_runtime._log_file.write(f"红米 G Pro 27U Toolbox 日志 - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                adb_runtime._log_file.write(f"{'='*60}\n")
                adb_runtime._log_file.flush()
            except OSError as e:
                # 目录不可写（如安装到 Program Files）等情况：回退开关，不让异常冒泡到 Qt 槽
                adb_runtime._log_to_file_enabled = False
                adb_runtime._log_file = None
                self.log_file_toggle.blockSignals(True)
                self.log_file_toggle.setChecked(False)
                self.log_file_toggle.blockSignals(False)
                self.log(f"无法创建日志文件（目录不可写: {os.path.dirname(adb_runtime._log_path)}）: {e}")
                return
        self.log(f"本地日志记录: {'开启' if adb_runtime._log_to_file_enabled else '关闭'}")

    def _toggle_autostart(self, state):
        enabled = (state == 2)
        update_settings({"autostart": enabled})
        if enabled:
            self._install_autostart()
            self.log("已设置开机自启动")
        else:
            self._remove_autostart()
            self.log("已取消开机自启动")

    def _get_autostart_path(self):
        return get_autostart_path()

    def _get_exe_path(self):
        return get_executable_path()

    def _install_autostart(self):
        if not install_autostart(self._get_exe_path()):
            self.log("设置自启动失败")

    def _remove_autostart(self):
        if not remove_autostart():
            self.log("取消自启动失败")

    def _toggle_4k_ui(self, state):
        enable = (state == 2)
        action = "启用" if enable else "关闭"
        target = "设置分辨率为 3840×2160、DPI 640" if enable else "恢复分辨率为 1920×1080、DPI 320"
        w = MessageBox(
            "需要重启显示器",
            f"{action} 4K UI 需要重启显示器才能生效。\n\n点击确定后将{target}，并重启显示器。\n\n是否继续？",
            self,
        )
        accepted = w.exec()
        w.deleteLater()
        if not accepted:
            self.chk_4k.blockSignals(True)
            self.chk_4k.setChecked(not enable)
            self.chk_4k.blockSignals(False)
            return

        def operation():
            with self.adb.transaction():
                if enable:
                    self.adb.shell("wm size 3840x2160", check=True)
                    self.adb.shell("wm density 640", check=True)
                else:
                    self.adb.shell("wm size 1920x1080", check=True)
                    self.adb.shell("wm density 320", check=True)
                self.adb.shell("reboot", check=True)

        def success():
            if enable:
                self.log("已设置 4K UI (3840×2160 / DPI 640)")
                self.log("正在重启显示器...")
            else:
                self.log("已恢复 1080p UI (1920×1080 / DPI 320)")
                self.log("正在重启显示器...")

        def failure():
            self.chk_4k.blockSignals(True)
            self.chk_4k.setChecked(not enable)
            self.chk_4k.blockSignals(False)

        self._run_adb_action("4K UI", operation, success, failure)

    def _check_4k_state(self):
        """检测 Override size，存在且大于1080p则为4K模式"""
        if not getattr(self, "adb_connected", False):
            return
        result = {"is_4k": False}

        def operation():
            res = self.adb.shell("wm size", check=True)
            for line in res.split("\n"):
                if "Override size" not in line:
                    continue
                parts = line.split(":")[-1].strip().split("x")
                if len(parts) == 2:
                    w, h = int(parts[0]), int(parts[1])
                    result["is_4k"] = (w > 1920 or h > 1080)
                break

        def success():
            self.chk_4k.blockSignals(True)
            self.chk_4k.setChecked(result["is_4k"])
            self.chk_4k.blockSignals(False)

        self._run_adb_action("检测 4K UI", operation, success)

    def _export_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出日志", f"mitv_log_{time.strftime('%Y%m%d_%H%M%S')}.txt", "文本文件 (*.txt)")
        if path:
            try:
                if adb_runtime._log_file: adb_runtime._log_file.flush()
                import shutil
                shutil.copy2(adb_runtime._log_path, path)
                self.log(f"日志已导出: {path}")
            except Exception as e:
                self.log(f"导出失败: {e}")

    def _open_log_dir(self):
        log_dir = os.path.dirname(adb_runtime._log_path) if adb_runtime._log_path else get_log_dir()
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError as e:
            self.log(f"无法创建日志目录: {e}")
            return
        if sys.platform == "win32":
            os.startfile(log_dir)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", log_dir])
        else:
            subprocess.Popen(["xdg-open", log_dir])

    def _on_status(self, text):
        text = normalize_status_text(text)
        self.status_label.setText(text)
        was_connected = getattr(self, "adb_connected", False)
        is_scan_status = text.startswith("扫描")
        if not is_scan_status:
            self.adb_connected = is_connected_status_text(text)
            # 连接状态变化时清除页面缓存
            if was_connected and not self.adb_connected:
                self._stop_source_polling()
                self._page_loaded.clear()
                self._hdr_last_state = None
                self._hdr_state_source = None
                self._update_hdr_memory_status_label("等待显示器连接")
            elif not was_connected and self.adb_connected:
                self._page_loaded.clear()
                # 触发当前页面加载
                page = self.stackedWidget.currentWidget()
                if page:
                    self._on_page_changed(self.stackedWidget.currentIndex())
                # 连接后预加载画面页与游戏页数据（无论当前停在哪一页），切换过去时无需再等读取
                self._refresh_pages(("picturePage", "gamePage"), delay_ms=800)
                # 检测 4K 状态
                QTimer.singleShot(1500, self._check_4k_state)
                # 首次连接后同步 ADB 保活守护状态，工具页卡片不需要再手动点检测
                QTimer.singleShot(1800, self._check_guardian_status)
                QTimer.singleShot(2200, lambda: self._poll_hdr_memory_state("connected"))
        
        if "扫描中" in text:
            status_suffix = "正在扫描内网..."
            self.status_label.setStyleSheet("color: #b85c00; font-weight: bold; font-size: 14px;")
        elif "扫描完成" in text:
            status_suffix = text
            self.status_label.setStyleSheet("color: #b85c00; font-weight: bold; font-size: 14px;")
        elif "未连接" in text or "失败" in text or adb_text_has_disconnected_marker(text):
            status_suffix = "未连接"
            self.status_label.setStyleSheet("color: #d83b01; font-weight: bold; font-size: 14px;")
        elif "连接中" in text:
            status_suffix = "连接中"
            self.status_label.setStyleSheet("color: #b85c00; font-weight: bold; font-size: 14px;")
        elif "已连接" in text:
            status_suffix = f"已连接 ({text.replace('已连接: ', '')})"
            self.status_label.setStyleSheet("color: #107c41; font-weight: bold; font-size: 14px;")
        else:
            status_suffix = text
            self.status_label.setStyleSheet("color: #107c41; font-weight: bold; font-size: 14px;")
            
        self.setWindowTitle(f"红米 G Pro 27U Toolbox - {status_suffix}")

    def _show_message_box(self, mtype, title, text):
        w = MessageBox(title, text, self)
        w.yesButton.setText("知道了")
        w.cancelButton.hide()
        w.exec()
        w.deleteLater()

    def _prompt_guardian_migration(self):
        """检测到旧包名时提示升级。

        旧包（``GUARDIAN_LEGACY_PACKAGE``）与新包是两个不同的应用，不会自动互相
        替换；两者并存会同时写 ADB 相关设置、都可能重启 adbd，所以由桌面端卸载旧
        包并重新部署。

        这里是**单按钮提示而非询问**：卸载旧包是升级的必经步骤，用户没有第二个
        选择，"取消"只会留下一个持续干扰新版本的旧守护。所以隐藏 cancelButton，
        点掉即开始升级。
        """
        if getattr(self, "_guardian_migration_dialog_open", False):
            return
        self._guardian_migration_dialog_open = True
        try:
            box = MessageBox(
                "升级 ADB 保活守护",
                "检测到旧版本的 AdbGuardian，需要卸载后重新部署。\n\n"
                "升级过程会重启显示器的 ADB 连接，约 10 秒。",
                self,
            )
            box.yesButton.setText("立即升级")
            box.cancelButton.hide()
            box.exec()
            box.deleteLater()
        finally:
            self._guardian_migration_dialog_open = False
        self._migrate_guardian()


    def log(self, m):
        self.log_signal.emit(f"[{time.strftime('%H:%M:%S')}] {m}")


    # ===== Pages Creators =====







    # ===== Helpers for Cards & Sections =====




    # ===== Control Setters =====








    _MODE_NAMES = {14: "标准", 10: "游戏", 9: "电影"}
























    # ===== ADB & Connect Slots =====


















    # ===== 按需数据加载 =====





    # ===== 页面按需加载 =====

    _PAGES_NEED_CONNECTION = {"picturePage", "gamePage", "sourcePage", "lightPage", "remotePage"}
