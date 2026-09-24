"""显示器控制、快捷键和 HDR/模式记忆功能。"""

import time

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, FluentStyleSheet, MessageBox, ToggleButton

from .adb import async_run
from .core import (
    ADJUSTABLE_HOTKEY_PARAMS,
    CUSTOM_COLOR_TEMP_VALUE,
    HDR_TONE_MAPPING_UI_TO_MTK,
    LOCAL_DIMMING_NAMES,
    PICTURE_MODE_GROUPS,
    PICTURE_SCENE_NAMES,
    XIAOMI_TO_MTK_COLOR_TEMP,
    is_game_picture_mode,
    is_hdr_tone_mapping_picture_mode,
    load_settings,
    update_settings,
)
from .presets import (
    BASELINE_PRESET_ID,
    PICTURE_ITEMS,
    apply_preset,
    find_active_task,
    find_preset,
    missing_keys,
    new_id,
    unique_name,
)
from .widgets import LoadingSpinner, OverlayResizeFilter
from .windows import query_windows_hdr_enabled

_preset_overlay_filter = None

_GAME_FEATURE_DEPENDENCIES = {
    "mt_game_dynamic_ft": ("front_sight_index", 1, "准星 1"),
    "mt_game_scope_night": ("mt_game_scope", 1, "狙击镜 1.1x"),
}


class DisplayFeaturesMixin:
    def initialize_display_features(self):
        """初始化显示器控制缓存以及 HDR 记忆定时器。"""
        self.current_vals = {}
        self._local_dimming_toggle_suspended = False
        self._local_dimming_memory_suppress_until = 0.0
        self._picture_mode_switch_seq = 0
        self._cycle_hotkey_pending = {}
        self._cycle_hotkey_timers = {}
        self._control_rollback_values = {}
        self._adjust_hotkey_pending = {}
        self._adjust_hotkey_timers = {}
        self._adjust_hotkey_resolving = set()
        self._local_dimming_toggle_resolving = False
        self._hdr_last_state = None
        self._hdr_state_source = None
        self._hdr_windows_state = None
        self._hdr_memory_apply_timer = QTimer(self)
        self._hdr_memory_apply_timer.setSingleShot(True)
        self._hdr_memory_apply_timer.timeout.connect(self._apply_hdr_memory_for_current_state)
        self._hdr_picture_refresh_timer = QTimer(self)
        self._hdr_picture_refresh_timer.setSingleShot(True)
        self._hdr_picture_refresh_timer.timeout.connect(
            lambda: self._refresh_picture_data_after_hdr_change()
        )

    def hotkey_countdown_enabled(self):
        return bool(load_settings().get("hotkey_countdown_enabled", True))

    def hotkey_countdown_seconds(self):
        try:
            return max(0.1, float(load_settings().get("hotkey_countdown_seconds", 0.8)))
        except (TypeError, ValueError):
            return 0.8

    def effective_hotkey_delay(self):
        """快捷键实际生效前等待多久（秒）。0 表示不等待、按下即下发。"""
        if not self.hotkey_countdown_enabled():
            return 0.0
        return self.hotkey_countdown_seconds()

    def _toggle_hotkey_countdown(self, state):
        try:
            state_val = int(state)
        except Exception:
            state_val = getattr(state, "value", 0)
        enabled = state_val == Qt.CheckState.Checked.value
        update_settings({"hotkey_countdown_enabled": enabled})
        self._sync_countdown_enabled_ui(enabled)
        self.log(f"快捷键松手后生效（倒计时）: {'开启' if enabled else '关闭（按下立即下发）'}")

    def _cycle_action_catalog(self):
        """取值型动作目录：快捷键循环与托盘菜单共用同一份定义。

        每项为 (取值键, [(值, 名称), ...], 显示名, 执行函数)。
        """
        return {
            "picture_mode": (
                "picture_mode",
                [(14, "标准"), (10, "游戏"), (9, "电影")],
                "画面模式",
                lambda val, name: self._set_mode(val, name)
            ),
            "local_dimming": (
                "picture_local_dimming",
                [(0, "关"), (1, "低"), (2, "中"), (3, "高")],
                "精密控光",
                lambda val, name: self._jni("g_video__vid_local_dimming", val, "picture_local_dimming", f"精密控光: {name}", "tv_picture_video_local_dimming")
            ),
            "color_space": (
                "tv_picture_advanced_video_color_space",
                [(0, "自动"), (3, "sRGB"), (6, "DCI-P3"), (4, "AdobeRGB"), (5, "BT2020"), (7, "BT709")],
                "色域",
                lambda val, name: self._jni("g_video__vid_gamut_mapping_mode", val, "tv_picture_advanced_video_color_space", f"色域: {name}", "tv_picture_video_color_space")
            ),
            "color_temp": (
                "picture_color_temperature",
                [(0, "冷色"), (1, "标准"), (2, "暖色"), (8, "原色"), (3, "自定义")],
                "色温",
                lambda val, name: self._set_color_temp(XIAOMI_TO_MTK_COLOR_TEMP.get(val, 2), val, f"色温: {name}")
            ),
            "response_time": (
                "picture_response_time",
                [(1, "普通"), (2, "快速"), (3, "高速")],
                "灰阶响应时间",
                lambda val, name: self._jni("g_video__vid_od_response_time", val, "picture_response_time", f"响应时间: {name}")
            ),
            "freesync": (
                "freesync",
                [(0, "关"), (1, "开")],
                "FreeSync",
                lambda val, name: self._fsync(val == 1)
            ),
            "input_source": (
                "mitv.tvplayer.hdmi.last.source",
                [(23, "HDMI 1"), (24, "HDMI 2"), (29, "DP"), (30, "USBC")],
                "信号源切换",
                lambda val, name: self._set("mitv.tvplayer.hdmi.last.source", val, f"信号源: {name}")
            )
        }

    def tray_entry_catalog(self):
        """托盘右键菜单可选的条目目录。

        两类：取值型（复用快捷键的循环动作定义）与数值型（复用可调快捷键参数，
        菜单里只给「增大 / 减小」，不把 1~100 全铺开）。
        """
        entries = []
        for entry_id, (_sk, options, label, _exec) in self._cycle_action_catalog().items():
            entries.append({
                "id": entry_id,
                "label": label,
                "kind": "options",
                "options": list(options),
            })
        for param, cfg in ADJUSTABLE_HOTKEY_PARAMS.items():
            entries.append({
                "id": param,
                "label": cfg.get("label", param),
                "kind": "stepper",
                "min": cfg.get("min", 0),
                "max": cfg.get("max", 100),
                "step": cfg.get("step", 1),
            })
        return entries

    def tray_entry(self, entry_id):
        for entry in self.tray_entry_catalog():
            if entry["id"] == entry_id:
                return entry
        return None

    def tray_entry_value(self, entry_id):
        """该条目当前值（供菜单打勾）。未知时返回 None。"""
        entry = self.tray_entry(entry_id)
        if not entry:
            return None
        if entry["kind"] == "options":
            key = self._cycle_action_catalog()[entry_id][0]
        else:
            key = ADJUSTABLE_HOTKEY_PARAMS[entry_id].get("setting")
        raw = getattr(self, "current_vals", {}).get(key)
        if raw in (None, "", "null", "N/A"):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def tray_apply_option(self, entry_id, value):
        """托盘菜单里选中某个取值。"""
        if not getattr(self, "adb_connected", False):
            return
        catalog = self._cycle_action_catalog()
        if entry_id not in catalog:
            return
        _sk, options, _label, exec_fn = catalog[entry_id]
        name = next((n for v, n in options if v == value), str(value))
        exec_fn(value, name)

    def tray_set_value(self, entry_id, value):
        """托盘菜单里拖动滑块设定数值型条目的值（去抖提交）。"""
        if not getattr(self, "adb_connected", False):
            return
        cfg = ADJUSTABLE_HOTKEY_PARAMS.get(entry_id)
        if not cfg:
            return
        target = max(cfg.get("min", 0), min(cfg.get("max", 100), int(value)))
        self._stage_adjustable_display_value(entry_id, cfg, target, show_hud=False)

    def trigger_hotkey_action(self, action):
        if not getattr(self, "adb_connected", False):
            return

        if action == "local_dimming_toggle_off":
            self._toggle_local_dimming_off_hotkey()
            return

        catalog = self._cycle_action_catalog()
        actions_map = {
            "picture_mode_cycle": catalog["picture_mode"],
            "local_dimming_cycle": catalog["local_dimming"],
            "color_space_cycle": catalog["color_space"],
            "color_temp_cycle": catalog["color_temp"],
            "response_time_cycle": catalog["response_time"],
            "freesync_toggle": catalog["freesync"],
            "input_source_cycle": catalog["input_source"],
        }

        if action not in actions_map:
            return

        self._stage_cycle_hotkey_action(action, *actions_map[action])

    def _stage_cycle_hotkey_action(self, action, sk, state_tuples, label_name, exec_fn):
        pending = self._cycle_hotkey_pending.get(action)
        curr_val = pending.get("value") if pending else getattr(self, "current_vals", {}).get(sk, state_tuples[0][0])
        original_value = pending.get("original") if pending else curr_val

        curr_idx = -1
        for idx, (val, _name) in enumerate(state_tuples):
            if val == curr_val:
                curr_idx = idx
                break

        next_idx = (curr_idx + 1) % len(state_tuples)
        next_val, next_name = state_tuples[next_idx]

        self._cycle_hotkey_pending[action] = {
            "sk": sk,
            "label": label_name,
            "name": next_name,
            "value": next_val,
            "original": original_value,
            "exec": exec_fn,
        }
        self._preview_cycle_hotkey_value(sk, next_val)

        delay = self.effective_hotkey_delay()
        if getattr(self, "osd", None):
            # 等待期间显示倒计时进度条；关闭倒计时则不带进度条
            self.osd.show_hud(label_name, next_name, countdown=delay or None)

        timer = self._cycle_hotkey_timers.get(action)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda a=action: self._commit_cycle_hotkey_action(a))
            self._cycle_hotkey_timers[action] = timer
        timer.stop()
        if delay <= 0:
            # 关掉倒计时：立即下发，不等
            self._commit_cycle_hotkey_action(action)
            return
        timer.setInterval(int(delay * 1000))
        timer.start()

    def _preview_cycle_hotkey_value(self, sk, value):
        self.current_vals[sk] = value
        if sk == "picture_mode":
            self._highlight_mode(value)
            return
        if sk == "freesync":
            self.jni_values_signal.emit({sk: value})
            return
        self.values_signal.emit({sk: value})

    def _commit_cycle_hotkey_action(self, action):
        if getattr(self, "osd", None):
            self.osd.end_countdown()
        pending = self._cycle_hotkey_pending.pop(action, None)
        if not pending or not getattr(self, "adb_connected", False):
            return
        sk = pending["sk"]
        next_val = pending["value"]
        next_name = pending["name"]
        self._control_rollback_values[sk] = pending.get("original")
        try:
            pending["exec"](next_val, next_name)
        except Exception as e:
            self._control_rollback_values.pop(sk, None)
            self.log(f"快捷键执行失败: {e}")

    def _take_control_previous(self, key):
        if key in self._control_rollback_values:
            return self._control_rollback_values.pop(key)
        return self.current_vals.get(key)

    def _get_current_local_dimming_value(self):
        val = getattr(self, "current_vals", {}).get("picture_local_dimming")
        try:
            return max(0, min(3, int(val)))
        except Exception:
            return 0

    def _get_saved_local_dimming_toggle_value(self):
        try:
            val = int(load_settings().get("local_dimming_toggle_last_value", 3))
        except Exception:
            val = 3
        return max(1, min(3, val))

    def _save_local_dimming_toggle_value(self, value):
        try:
            value = max(1, min(3, int(value)))
        except Exception:
            value = 3
        update_settings({"local_dimming_toggle_last_value": value})

    def _toggle_local_dimming_off_hotkey(self):
        if "picture_local_dimming" not in self.current_vals:
            if self._local_dimming_toggle_resolving:
                return
            self._local_dimming_toggle_resolving = True
            result = {}

            def operation():
                result["value"] = self.query_setting_or_jni("picture_local_dimming", check=True)

            def success():
                self._local_dimming_toggle_resolving = False
                self.current_vals["picture_local_dimming"] = result["value"]
                self._toggle_local_dimming_off_hotkey()

            def failure():
                self._local_dimming_toggle_resolving = False

            self._run_adb_action("读取精密控光", operation, success, failure)
            return

        current = self._get_current_local_dimming_value()
        if current > 0:
            self._save_local_dimming_toggle_value(current)
            target = 0
            self._local_dimming_toggle_suspended = True
            message = f"精密控光开关: 已记录 {LOCAL_DIMMING_NAMES.get(current, current)}，切换为关"
        else:
            target = self._get_saved_local_dimming_toggle_value()
            self._local_dimming_toggle_suspended = False
            message = f"精密控光开关: 恢复 {LOCAL_DIMMING_NAMES.get(target, target)}"

        self._local_dimming_memory_suppress_until = time.monotonic() + 3.0
        value_name = LOCAL_DIMMING_NAMES.get(target, str(target))
        if getattr(self, "osd", None):
            self.osd.show_hud("精密控光", value_name)
        self._set_local_dimming_toggle_value(target, message)

    def _set_local_dimming_toggle_value(self, value, message):
        if not self.check_connection():
            return
        value = max(0, min(3, int(value)))
        self._mark_adb_busy(2.5)
        previous = self.current_vals.get("picture_local_dimming")

        def operation():
            with self.adb.transaction():
                self.adb.jni_set("g_video__vid_local_dimming", value, check=True)
                self.adb.put("picture_local_dimming", str(value), check=True)
                self.adb.put("tv_picture_video_local_dimming", str(value), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self._note_picture_change()
            self.log(message)
            self.current_vals["picture_local_dimming"] = value
            self.current_vals["tv_picture_video_local_dimming"] = value
            self._optimistic_highlight("picture_local_dimming", value)

        def failure():
            self._local_dimming_toggle_suspended = False
            if previous is not None:
                self._optimistic_highlight("picture_local_dimming", previous)

        self._run_adb_action("精密控光", operation, success, failure)

    def _sync_light_sensor_switch(self, on):
        """同步「自动调整亮度」开关控件状态，不触发写入。"""
        switch = getattr(self, "light_sensor_switch", None)
        if switch is None:
            return
        was_blocked = switch.blockSignals(True)
        try:
            switch.setChecked(bool(on))
        finally:
            switch.blockSignals(was_blocked)

    def _set_light_sensor(self, on):
        """自动调整亮度（光感）开关。

        这个开关有**两份状态**，必须都写：
        - Settings.Global 的 `tv_picture_light_sensor` 是 MiBackLightManager 真正
          监听的（功能立即生效）；
        - MTK 侧的 `g_video__light_sensor_switch` 才是设置菜单显示的值。
        只写前者功能会生效，但菜单显示不同步。

        注意这只是**结果状态等价**，不是过程等价：照抄的是某一次拨动的写入快照，
        没有走 App 层的 setLightSensor() 实时判断，也不触发菜单的埋点上报。

        菜单手拨「开」还会顺带写三个 DBC（动态背光）的值。本机未启用这套功能
        （没有 mitv.settings.backlight.dbc 功能位），那三个写入的效果**未经验证**，
        所以这里不写。

        两处偏离本模块其他 JNI 写入的惯例，都是有意的：
        - `isUpdate` 传 1（其他处用默认的 3），这是实测手拨菜单抓到的值；
        - 不跟 `refresh_pq()`，光感由 ContentObserver 立即生效，菜单路径里没有这一步。
        """
        if not self.check_connection():
            return
        on = bool(on)
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous("tv_picture_light_sensor")
        value = 1 if on else 0
        message = f"自动调整亮度: {'开' if on else '关'}"

        def operation():
            # settings put 必须由 adb shell 执行（shell 持有 WRITE_SECURE_SETTINGS）；
            # 塞进 service call TvService 会被 tvservice 的 uid 静默拒绝 —— 所以这两条
            # 是两次独立调用，不要"优化"成一条 TvService 命令。
            with self.adb.transaction():
                self.adb.jni_set("g_video__light_sensor_switch", value, upd=1, check=True)
                self.adb.put("tv_picture_light_sensor", str(value), check=True)

        def success():
            self._note_picture_change()
            self.log(message)
            self.current_vals["tv_picture_light_sensor"] = value
            self.current_vals["g_video__light_sensor_switch"] = value
            self._sync_light_sensor_switch(on)

        def failure():
            if previous is not None:
                self.current_vals["tv_picture_light_sensor"] = previous
                self._sync_light_sensor_switch(str(previous).strip() == "1")

        self._run_adb_action("自动调整亮度", operation, success, failure)

    # ===== 预设：应用与恢复 =====

    def _preset_suppress_seconds(self):
        """应用预设期间压制自动动作的时长估计。

        真正的串行保证来自 `adb.transaction()`：整批写入在一个事务里跑完，其它
        ADB 动作（HDR 记忆 apply、快捷循环）只能等锁。这个时间窗是用来劝阻**新
        排程**的，宁可给宽一点，也别让自动纠正插进批次中间。
        """
        return min(20.0, 4.0 + 0.9 * len(PICTURE_ITEMS))

    def _preset_foreground(self):
        """窗口是否在前台可见。收进托盘或最小化时算"后台服务"。"""
        return bool(self.isVisible()) and not self.isMinimized()

    def _show_preset_switching_overlay(self):
        """全窗口遮罩 + 转圈，挡住切换期间的误操作。

        挂在主窗口上而不是某一页 —— 切预设会连带改画面模式等，用户此刻可能
        停在任意页面上。窗口在后台时不显示（看不见，也没有挡住的意义），改由
        悬浮窗提示。

        遮罩是**窗口特性**：宿主若不是 QWidget（单元测试里的轻量假宿主）
        就直接跳过，不为了测试方便去给生产代码加一堆 getattr 兜底。
        """
        if not isinstance(self, QWidget):
            return
        self._hide_preset_switching_overlay()
        if not self._preset_foreground():
            osd = getattr(self, "osd", None)
            if osd is not None:
                osd.show_hud("正在切换预设", "请稍候")
            return

        overlay = QWidget(self)
        overlay.setObjectName("_preset_overlay")
        overlay.setStyleSheet("background-color: rgba(0, 0, 0, 120);")
        overlay.setGeometry(self.rect())

        column = QVBoxLayout(overlay)
        column.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.setSpacing(14)
        column.addWidget(LoadingSpinner(overlay), 0, Qt.AlignmentFlag.AlignHCenter)
        label = BodyLabel("正在切换预设...", overlay)
        label.setStyleSheet("color: white; font-size: 16px; background: transparent;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(label, 0, Qt.AlignmentFlag.AlignCenter)

        global _preset_overlay_filter
        if _preset_overlay_filter is None:
            _preset_overlay_filter = OverlayResizeFilter("_preset_overlay")
        self.installEventFilter(_preset_overlay_filter)

        overlay.show()
        overlay.raise_()

    def _hide_preset_switching_overlay(self):
        if not isinstance(self, QWidget):
            return
        for child in self.findChildren(QWidget, "_preset_overlay"):
            child.hide()
            child.deleteLater()

    def _apply_picture_values(self, values, label, before_apply=None,
                              on_finished=None, quiet=False):
        """把一组画面值按固定顺序写进设备（预设应用与快照恢复共用）。

        `before_apply(snapshot)` 非 None 时，表示这是一次"覆盖式"应用：会先在
        同一个事务里读一份当前值快照并交给回调落盘，供之后撤销。

        `quiet=True` 供自动任务使用：未连接时静默放弃，不弹「未连接显示器」对话框
        （那是给用户手动操作准备的提示，定时器不该弹窗）。

        全程压制 HDR/SDR 记忆的自动纠正 —— 否则它会把自己的写入当成用户选择记下来，
        甚至在批次中途插进来改精密控光。
        """
        if quiet:
            if not getattr(self, "adb_connected", False):
                self.log(f"{label} 已跳过：未连接显示器")
                return
        elif not self.check_connection():
            return
        seconds = self._preset_suppress_seconds()
        self._mark_adb_busy(seconds)
        self._local_dimming_memory_suppress_until = time.monotonic() + seconds
        self._show_preset_switching_overlay()
        outcome = {}

        def operation():
            with self.adb.transaction():
                if before_apply is not None:
                    before_apply(self._read_picture_values())
                outcome["result"] = apply_preset(self.adb, values)

        def success():
            self._hide_preset_switching_overlay()
            result = outcome.get("result")
            if result is None:
                return
            self.log(f"{label}：{result.summary()}")
            for name, reason in result.skipped:
                self.log(f"  · 跳过 {name}（{reason}）")
            for name, error in result.failed:
                self.log(f"  · 失败 {name}：{error}")
            self.current_vals.update(values)
            self._page_loaded.discard("picturePage")
            # FreeSync 在游戏页，预设会动它，那边的开关也得回读
            self._page_loaded.discard("gamePage")
            # 等设备把整批写入消化完再回读 —— 兼作"写入是否真的落下去"的校验，
            # 因为模式切换会重新应用该模式的整套参数。
            QTimer.singleShot(1800, self._verify_after_preset_apply)
            if on_finished:
                on_finished(result)

        def failure(error):
            self._hide_preset_switching_overlay()
            self.log(f"{label} 失败：{error}")
            if on_finished:
                on_finished(None)

        self._run_adb_action(label, operation, success, failure)

    def _verify_after_preset_apply(self):
        """应用后回读一次并刷新界面。"""
        if getattr(self, "_cleanup_done", False) or not getattr(self, "adb_connected", False):
            return
        if self._adb_channel_busy():
            QTimer.singleShot(800, self._verify_after_preset_apply)
            return
        self._refresh_pages(("picturePage", "gamePage"), force=True)

    def _store_preset_snapshot(self, values, source):
        return update_settings({"preset_snapshot": {
            "source": source,
            "values": values,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }})

    def active_preset_id(self):
        """当前正在使用的预设 id；None 表示「无预设」。"""
        return load_settings().get("active_preset_id")

    def memories_suspended_by_preset(self):
        """有预设生效时，两个「记忆」暂停。

        它们存在的理由是「没有预设时，用户没法为另一种状态预先存一套值」——
        而预设正是那套预先存好的值。两者都在管同一件事，同时生效必然打架
        （HDR 记忆会去写精密控光覆盖预设，且那个写入不会被预设吸收，
        结果是设备值、画面页值、预设值三方不一致）。
        """
        return bool(self.active_preset_id())

    def _refresh_preset_views(self):
        """预设卡片与画面页的「当前预设」提示一起刷新。"""
        if hasattr(self, "_preset_sync"):
            self._preset_sync()
        if hasattr(self, "_update_preset_banner"):
            self._update_preset_banner()

    def _goto_preset_page(self):
        page = getattr(self, "preset_page", None)
        if page is not None and hasattr(self, "stackedWidget"):
            self.stackedWidget.setCurrentWidget(page)

    def apply_preset_by_id(self, preset_id):
        """套用一个预设，并把它记为「当前预设」。"""
        preset = find_preset(load_settings().get("presets") or [], preset_id)
        if preset is None:
            self.log("预设不存在或已被删除")
            return
        values = preset.get("values") or {}
        name = preset.get("name") or preset_id
        missing = missing_keys(values)
        if missing:
            self.log(f"预设「{name}」缺 {len(missing)} 项（{'、'.join(missing)}），只应用已有的项")
        # 只有从「无预设」切进预设时才更新那份值 —— 预设之间互切不该覆盖它，
        # 否则「无预设」就不再是"套预设之前"的那套设置了。
        leaving_baseline = not self.active_preset_id()

        def remember(snapshot):
            if leaving_baseline:
                self._store_preset_snapshot(snapshot, name)

        def finished(result):
            if result is None:
                return
            update_settings({"active_preset_id": preset_id})
            self._refresh_preset_views()

        self._apply_picture_values(values, f"应用预设「{name}」",
                                   before_apply=remember, on_finished=finished)

    def restore_preset_snapshot(self):
        """退回「无预设」：还原成套预设之前的那套值。"""
        snapshot = load_settings().get("preset_snapshot")
        if not isinstance(snapshot, dict) or not snapshot.get("values"):
            self.log("还没有可退回的状态")
            return

        def finished(result):
            if result is None:
                return
            update_settings({"active_preset_id": None})
            self._refresh_preset_views()

        self._apply_picture_values(snapshot["values"], "退回无预设", on_finished=finished)

    def has_preset_snapshot(self):
        snapshot = load_settings().get("preset_snapshot")
        return bool(isinstance(snapshot, dict) and snapshot.get("values"))

    # ===== 改动自动写回当前预设 =====

    def _note_picture_change(self):
        """画面设置被改动后，把结果同步进当前预设。

        「无预设」时什么都不做 —— 那时改动就是改动本身，没有地方要存。
        """
        if not self.active_preset_id():
            return
        timer = getattr(self, "_preset_sync_timer", None)
        if timer is not None:
            timer.start()      # 单发定时器，重复 start 会把触发时间往后推（去抖）

    def _sync_active_preset(self):
        """回读设备当前值并写进当前预设，让预设始终等于设备实际状态。"""
        preset_id = self.active_preset_id()
        if not preset_id:
            return
        if getattr(self, "_cleanup_done", False) or not getattr(self, "adb_connected", False):
            return
        if self._adb_channel_busy():
            timer = getattr(self, "_preset_sync_timer", None)
            if timer is not None:
                timer.start()
            return
        captured = {}

        def operation():
            captured["values"] = self._read_picture_values()

        def success():
            values = captured.get("values") or {}
            if not values:
                return
            presets = load_settings().get("presets") or []
            preset = find_preset(presets, preset_id)
            if preset is None or preset.get("values") == values:
                return
            preset["values"] = values
            update_settings({"presets": presets})
            self.log(f"已把当前设置保存进预设「{preset.get('name')}」")
            self._refresh_preset_views()

        self._run_adb_action("保存到当前预设", operation, success)

    # ===== 当前预设的展示与切换 =====

    def _update_preset_banner(self):
        """顶部指示条：当前有预设在用就显示，无预设则收起。

        它只表达状态，不带按钮 —— 改动是自动写回当前预设的，没有"保存"要做；
        想脱离预设就点卡片上的「无预设」。
        """
        banner = getattr(self, "_preset_banner", None)
        if banner is None:
            return
        preset = find_preset(load_settings().get("presets") or [],
                             self.active_preset_id() or "")
        if preset is None:
            banner.hide()
            return
        # 画面页那行「当前预设：X」撤掉了，那句「改动会自动保存进它」挪到这里 ——
        # 否则用户不知道改动去哪了
        banner.set_preset_name(preset.get("name") or preset.get("id"))
        banner.show_banner()

    def _goto_picture_page(self):
        page = getattr(self, "picture_page", None)
        if page is None or not hasattr(self, "stackedWidget"):
            return
        self.stackedWidget.setCurrentWidget(page)

    def begin_preset_edit(self, preset_id):
        """卡片上的「编辑」：切到该预设并跳到画面设置页去调。

        没有"编辑会话"可言 —— 切过去之后它就是当前预设，之后在画面页的改动
        会自动写回它。想反悔就点「无预设」退回套预设之前的值。
        """
        if not self.check_connection():
            return
        if self.active_preset_id() == preset_id:
            # 已经是当前预设，直接跳过去调就行
            self._goto_picture_page()
            return
        self.apply_preset_by_id(preset_id)
        self._goto_picture_page()

    def create_preset_from_current(self, name):
        """新建预设：以**当前设备设置**为内容创建，并立刻切为当前预设。

        没有"保存"按钮，所以创建必须在这一步完成；之后在画面页的调整会通过
        自动保存继续写进它。
        """
        if not self.check_connection():
            return
        captured = {}

        def operation():
            captured["values"] = self._read_picture_values()

        def success():
            values = captured.get("values") or {}
            if not values:
                self.log("没能读到当前设置，预设未创建")
                return
            presets = load_settings().get("presets") or []
            final_name = unique_name(presets, name)
            created_id = new_id(presets, "preset")
            presets.append({"id": created_id, "name": final_name, "values": values})
            update_settings({"presets": presets, "active_preset_id": created_id})
            self.log(f"已新建预设「{final_name}」，共 {len(values)} 项")
            self._refresh_preset_views()
            self._goto_picture_page()

        self._run_adb_action("新建预设", operation, success)


    # ===== 自动任务调度 =====

    def initialize_preset_features(self):
        from PyQt6.QtCore import QTimer as _QTimer

        self._auto_task_checking = False
        self._auto_task_active_id = None
        self._auto_task_waiting_id = None
        self._auto_task_timer = _QTimer(self)
        self._auto_task_timer.setInterval(15000)
        self._auto_task_timer.timeout.connect(self._auto_task_tick)
        self._auto_task_timer.start()

        # 改动写回当前预设的去抖定时器：滑条松手到真正回读之间留一段，
        # 免得连点几个控件就来回读好几次设备。
        self._preset_sync_timer = _QTimer(self)
        self._preset_sync_timer.setSingleShot(True)
        self._preset_sync_timer.setInterval(1200)
        self._preset_sync_timer.timeout.connect(self._sync_active_preset)

    def _auto_task_tick(self):
        """每 15s 看一次当前时刻该由哪个任务生效。

        未连接时**不放弃**：记下命中的任务待命，等连上后再看是否仍在该时间段内 ——
        还在就补执行，已经出了时间段就自然什么都不做（下一次 tick 命中为空）。
        """
        if getattr(self, "_cleanup_done", False) or getattr(self, "_windows_session_ending", False):
            return
        if self._auto_task_checking or self._adb_channel_busy():
            return
        self._auto_task_checking = True
        try:
            tasks = load_settings().get("auto_tasks") or []
            now = time.localtime()
            active = find_active_task(tasks, now.tm_hour * 60 + now.tm_min)
            active_id = active.get("id") if active else None
            if active_id == self._auto_task_active_id:
                return

            if active is None:
                finished = self._auto_task_active_id
                self._auto_task_active_id = None
                self._auto_task_waiting_id = None
                if finished:
                    self._end_auto_task(finished)
                return

            if not getattr(self, "adb_connected", False):
                if self._auto_task_waiting_id != active_id:
                    self._auto_task_waiting_id = active_id
                    self.log("自动任务的时段已到，但显示器未连接，等连上后再看是否仍在时段内")
                return

            self._auto_task_waiting_id = None
            self._auto_task_active_id = active_id
            self._start_auto_task(active)
        finally:
            self._auto_task_checking = False

    def _find_task(self, task_id):
        for task in load_settings().get("auto_tasks") or []:
            if isinstance(task, dict) and task.get("id") == task_id:
                return task
        return None

    def _start_auto_task(self, task):
        preset_id = task.get("preset_id")
        window = f"（{task.get('start')}-{task.get('end')}）"
        if preset_id == BASELINE_PRESET_ID:
            # 「无预设」也能排任务：这段时间内不使用任何预设，退回套预设之前的值
            values = (load_settings().get("preset_snapshot") or {}).get("values") or {}
            if not values:
                self.log(f"自动任务{window}要改用「无预设」，但还没有可退回的状态")
                return
            name = "无预设"
            label = "自动任务：改用无预设"
            target_active = None
        else:
            preset = find_preset(load_settings().get("presets") or [], preset_id)
            if preset is None:
                self.log(f"自动任务{window}引用的预设已不存在")
                return
            values = preset.get("values") or {}
            name = preset.get("name") or preset_id
            label = f"自动任务：{name}"
            target_active = preset_id
        self.log(f"{label}{window}")
        task_id = task.get("id")

        def remember(snapshot):
            # 快照存进任务自身，不共用 preset_snapshot —— 时段内用户可能手动套用了
            # 别的预设，共用槽位会让时段结束时的还原回到错误的状态。
            tasks = load_settings().get("auto_tasks") or []
            for item in tasks:
                if isinstance(item, dict) and item.get("id") == task_id:
                    item["snapshot"] = {"values": snapshot, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            update_settings({"auto_tasks": tasks})

        def finished(result):
            if result is None:
                return
            update_settings({"active_preset_id": target_active})
            if hasattr(self, "_refresh_preset_views"):
                self._refresh_preset_views()

        self._apply_picture_values(values, label, before_apply=remember,
                                   on_finished=finished, quiet=True)

    def _clear_task_snapshot(self, task_id):
        tasks = load_settings().get("auto_tasks") or []
        for item in tasks:
            if isinstance(item, dict) and item.get("id") == task_id:
                item["snapshot"] = None
        update_settings({"auto_tasks": tasks})

    def _end_auto_task(self, task_id):
        task = self._find_task(task_id)
        snapshot = task.get("snapshot") if isinstance(task, dict) else None
        values = snapshot.get("values") if isinstance(snapshot, dict) else None
        if not values:
            self.log("自动任务时段结束，但没有记录到可还原的快照")
            self._clear_task_snapshot(task_id)
            return
        self.log("自动任务时段结束，正在还原之前的设置")
        self._clear_task_snapshot(task_id)
        # 不动 preset_snapshot —— 那是"手动应用预设"的撤销槽，自动任务不该覆盖它。
        self._apply_picture_values(values, "自动任务结束：还原设置", quiet=True)

    def trigger_adjust_hotkey(self, rule):
        if not getattr(self, "adb_connected", False):
            return
        if not isinstance(rule, dict):
            return

        param = rule.get("param")
        cfg = ADJUSTABLE_HOTKEY_PARAMS.get(param)
        if not cfg:
            return

        setting = cfg["setting"]
        resolve_settings = [setting]
        if cfg.get("color_gain"):
            resolve_settings = ["picture_red_gain", "picture_green_gain", "picture_blue_gain"]
        missing_settings = [key for key in resolve_settings if key not in self.current_vals]
        if missing_settings:
            if param in self._adjust_hotkey_resolving:
                return
            self._adjust_hotkey_resolving.add(param)
            result = {}

            def operation():
                with self.adb.transaction():
                    for key in missing_settings:
                        result[key] = self.query_setting_or_jni(key, check=True)

            def success():
                self._adjust_hotkey_resolving.discard(param)
                self.current_vals.update(result)
                self.trigger_adjust_hotkey(rule)

            def failure():
                self._adjust_hotkey_resolving.discard(param)

            self._run_adb_action(f"读取{cfg['label']}", operation, success, failure)
            return

        direction = rule.get("direction", "increase")
        try:
            step = abs(int(rule.get("step", cfg.get("step", 1))))
        except Exception:
            step = cfg.get("step", 1)
        if step <= 0:
            step = cfg.get("step", 1)

        pending = self._adjust_hotkey_pending.get(param, {})
        curr_val = pending.get("value", self._get_adjustable_display_value(cfg))
        delta = step if direction == "increase" else -step
        next_val = max(cfg["min"], min(cfg["max"], curr_val + delta))

        # 这里**不**单独弹一次提示：紧接着的 _stage_adjustable_display_value
        # 会用同样的文案再弹一次（并且带上倒计时条）。以前两处都弹，等于每按
        # 一次调节快捷键都多做一轮无谓的窗口操作。
        try:
            self._stage_adjustable_display_value(param, cfg, next_val)
        except Exception as e:
            self.log(f"可调快捷键执行失败: {e}")

    def _stage_adjustable_display_value(self, param, cfg, value, show_hud=True):
        """暂存一次数值调整并去抖提交。

        show_hud=False 用于托盘滑块——菜单里已经内联显示数值，再弹悬浮窗是重复。
        """
        setting = cfg["setting"]
        raw_value = value - int(cfg.get("ui_offset", 0))

        if cfg.get("screen_light"):
            self.current_vals[setting] = raw_value
            self.values_signal.emit({setting: raw_value})
        elif cfg.get("color_gain"):
            self._ensure_color_gain_values()
            self.current_vals[setting] = value
            self.values_signal.emit({setting: value})
        else:
            settings_keys = cfg.get("settings", [setting])
            for k in settings_keys:
                self.current_vals[k] = value
            self.values_signal.emit({setting: value})

        self._adjust_hotkey_pending[param] = {"cfg": cfg, "value": value}

        delay = self.effective_hotkey_delay()
        if show_hud and getattr(self, "osd", None):
            label = cfg.get("label", param)
            self.osd.show_hud(label, str(value), countdown=delay or None)

        timer = self._adjust_hotkey_timers.get(param)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda p=param: self._commit_pending_adjustment(p))
            self._adjust_hotkey_timers[param] = timer
        timer.stop()
        if delay <= 0:
            self._commit_pending_adjustment(param)
            return
        timer.setInterval(int(delay * 1000))
        timer.start()

    def _commit_pending_adjustment(self, param):
        if getattr(self, "osd", None):
            self.osd.end_countdown()
        pending = self._adjust_hotkey_pending.pop(param, None)
        if not pending or not getattr(self, "adb_connected", False):
            return
        try:
            self._set_adjustable_display_value(param, pending["cfg"], pending["value"])
        except Exception as e:
            self.log(f"可调快捷键提交失败: {e}")

    def _get_adjustable_display_value(self, cfg):
        setting = cfg["setting"]
        if setting in getattr(self, "current_vals", {}):
            val = self.current_vals.get(setting)
        else:
            if cfg.get("slider") in self.sliders:
                val = self.sliders[cfg["slider"]][0].value()
            else:
                val = cfg.get("default", cfg["min"])

        try:
            val = int(val)
        except Exception:
            val = cfg.get("default", cfg["min"])
        if cfg.get("ui_offset"):
            val += int(cfg["ui_offset"])
        return max(cfg["min"], min(cfg["max"], val))

    def _set_adjustable_display_value(self, param, cfg, value):
        setting = cfg["setting"]
        raw_value = value - int(cfg.get("ui_offset", 0))

        if cfg.get("screen_light"):
            self._set_screen_light_illumination(value)
            self.current_vals[setting] = raw_value
            self.values_signal.emit({setting: raw_value})
            return

        if cfg.get("color_gain"):
            self._ensure_color_gain_values()
            self._set_color_gain(cfg["label"], setting, cfg.get("jni"), value)
            self.values_signal.emit({setting: value})
            return

        settings_keys = cfg.get("settings", [setting])
        for k in settings_keys:
            self.current_vals[k] = value
        self.values_signal.emit({setting: value})
        self._mark_adb_busy(2.5)

        def do():
            with self.adb.transaction():
                if cfg.get("jni"):
                    self.adb.jni_set(cfg["jni"], value, check=True)
                    self.adb.refresh_pq(check=True)
                for k in settings_keys:
                    self.adb.put(k, str(value), check=True)

        def success():
            self.log(f"{cfg['label']}: {value}")
            self._note_picture_change()

        self._run_adb_action(
            cfg["label"],
            do,
            success,
            lambda: self._force_refresh_page("picturePage"),
        )

    def _ensure_color_gain_values(self):
        for key in ("picture_red_gain", "picture_green_gain", "picture_blue_gain"):
            if key in self.current_vals:
                continue
            try:
                self.current_vals[key] = int(self.query_setting_or_jni(key))
            except Exception:
                self.current_vals[key] = 1024

    def query_setting_or_jni(self, sk, check=False):
        def as_int(value, fallback=0):
            try:
                return int(value)
            except Exception:
                if check:
                    raise RuntimeError(f"{sk} 返回了无效值：{value}") from None
                return fallback

        if sk in ("picture_local_dimming", "tv_picture_video_local_dimming"):
            v = self.adb.jni_get("g_video__vid_local_dimming", check=check)
            return as_int(v)
        elif sk in ("tv_picture_advanced_video_color_space", "tv_picture_video_color_space"):
            v = self.adb.jni_get("g_video__vid_gamut_mapping_mode", check=check)
            return as_int(v)
        elif sk == "picture_response_time":
            v = self.adb.jni_get("g_video__vid_od_response_time", check=check)
            return as_int(v)
        elif sk == "freesync":
            src = self.adb.get("mitv.tvplayer.hdmi.last.source", check=check)
            src_id = as_int(src)
            if src_id in (29, 30):
                fs = self.adb.jni_get("g_video__dp_adaptive_sync", check=check)
                return 1 if as_int(fs) == 1 else 0
            else:
                fs = self.adb.jni_get("g_video__freesync_switch", check=check)
                return 1 if as_int(fs) == 3 else 0
        else:
            # Standard settings key
            v = self.adb.get(sk, check=check)
            return as_int(v, fallback=v)

    def _hdr_memory_enabled(self):
        return bool(load_settings().get("hdr_sdr_local_dimming_enabled", False))

    def _close_behavior_is_direct_exit(self):
        return load_settings().get("close_behavior", "tray") == "exit"

    def _toggle_hdr_local_dimming_memory(self, state):
        try:
            state_val = int(state)
        except Exception:
            state_val = getattr(state, "value", 0)
        enabled = state_val == Qt.CheckState.Checked.value
        update_settings({"hdr_sdr_local_dimming_enabled": enabled})
        if enabled:
            self._ensure_local_dimming_memory_defaults()
            self._hdr_last_state = None
            self._hdr_state_source = None
            self._hdr_windows_state = None
            self.log("HDR/SDR 分区控光记忆: 开启")
            self._update_hdr_memory_status_label("正在检测 Windows HDR")
            self._schedule_hdr_memory_check("enabled", delay_ms=80)
        else:
            self.log("HDR/SDR 分区控光记忆: 关闭")
            self._update_hdr_memory_status_label()

    def _freesync_mode_memory_enabled(self):
        return bool(load_settings().get("freesync_mode_memory_enabled", False))

    def _picture_mode_display_name(self, mode):
        try:
            mode_int = int(mode)
        except (TypeError, ValueError):
            return str(mode)
        group_name = self._picture_mode_group_name(mode_int)
        if group_name:
            return f"{group_name}（{mode_int}）"
        return f"{PICTURE_SCENE_NAMES.get(mode_int, '未知场景')}（{mode_int}）"

    def _get_freesync_memory_mode(self):
        try:
            return int(load_settings().get("freesync_previous_mode"))
        except (TypeError, ValueError):
            return None

    def _remember_freesync_previous_mode(self):
        """记录 FreeSync 开启前的画面模式（开启后显示器会自动切到游戏模式）"""
        try:
            mode = int(self.current_vals.get("picture_mode"))
        except (TypeError, ValueError):
            return
        update_settings({"freesync_previous_mode": mode})
        self._update_freesync_memory_status_label()
        self.log(f"FreeSync 模式记忆: 记录开启前模式 {self._picture_mode_display_name(mode)}")

    def _toggle_freesync_mode_memory(self, state):
        try:
            state_val = int(state)
        except Exception:
            state_val = getattr(state, "value", 0)
        enabled = state_val == Qt.CheckState.Checked.value
        update_settings({"freesync_mode_memory_enabled": enabled})
        self.log(f"FreeSync Pro 模式记忆: {'开启' if enabled else '关闭'}")
        self._update_freesync_memory_status_label()

    def _update_freesync_memory_status_label(self):
        label = getattr(self, "freesync_memory_status_label", None)
        if not label:
            return
        enabled = self._freesync_mode_memory_enabled()
        mode = self._get_freesync_memory_mode()
        is_freesync_on = getattr(self, "current_vals", {}).get("freesync") == 1
        
        if is_freesync_on:
            mode_text = self._picture_mode_display_name(mode) if mode is not None else "--"
            prefix = "已开启" if enabled else "已关闭"
            label.setText(f"FreeSync 模式记忆：{prefix}，已记录开启前模式：{mode_text}（关闭后切回）")
        else:
            current_mode = getattr(self, "current_vals", {}).get("picture_mode")
            mode_text = self._picture_mode_display_name(current_mode) if current_mode is not None else "--"
            prefix = "已开启" if enabled else "已关闭"
            label.setText(f"FreeSync 模式记忆：{prefix}，开启时将记录当前模式：{mode_text}")

    # ── 准星模式联动 ──────────────────────────────────────────────
    # 准星 front_sight_index 是设备级 settings，与 picture_mode 解耦，
    # 不主动清理的话切到标准/电影等模式后准星依然显示。

    def _crosshair_game_mode_only_enabled(self):
        return bool(load_settings().get("crosshair_game_mode_only", True))

    def _get_crosshair_memory(self):
        try:
            return int(load_settings().get("crosshair_memory"))
        except (TypeError, ValueError):
            return None

    def _save_crosshair_memory(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        update_settings({"crosshair_memory": value})
        self._update_crosshair_mode_status_label()

    def _clear_crosshair_memory(self):
        update_settings({"crosshair_memory": None})
        self._update_crosshair_mode_status_label()

    def _update_crosshair_mode_status_label(self):
        label = getattr(self, "crosshair_mode_status_label", None)
        if not label:
            return
        if not self._crosshair_game_mode_only_enabled():
            label.setText("准星模式联动：已关闭，准星在所有模式下都生效")
            return
        memory = self._get_crosshair_memory()
        memory_text = f"已记忆准星 {memory}" if memory else "暂无记忆"
        label.setText(f"准星模式联动：已开启（{memory_text}，离开游戏模式时自动隐藏）")

    def _toggle_crosshair_game_mode_only(self, state):
        try:
            state_val = int(state)
        except Exception:
            state_val = getattr(state, "value", 0)
        enabled = state_val == Qt.CheckState.Checked.value
        update_settings({"crosshair_game_mode_only": enabled})
        self.log(f"准星仅在游戏模式下生效: {'开启' if enabled else '关闭'}")
        self._update_crosshair_mode_status_label()
        if enabled:
            # 开启时立刻纠正一次当前状态（可能在非游戏模式下正显示着准星）
            self._reconcile_crosshair_mode_state()

    def _crosshair_value_from_vals(self):
        raw = getattr(self, "current_vals", {}).get("front_sight_index")
        if raw in (None, "", "null", "N/A"):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _reconcile_crosshair_mode_state(self):
        """按当前画面模式纠正准星：离开游戏模式记住并隐藏，进入游戏模式还原。

        由页面数据刷新驱动（含模式切换后的自动刷新），没有周期轮询——
        用显示器遥控器改模式时，会在下次刷新数据时纠正。

        隐藏是「只要在非游戏模式看到准星就清掉」；还原只在「模式跃迁进入
        游戏模式」时做一次，避免用户用显示器 OSD 主动关掉准星后又被打开。
        """
        if not self._crosshair_game_mode_only_enabled():
            return
        if not getattr(self, "adb_connected", False):
            return
        if getattr(self, "_crosshair_reconcile_busy", False):
            return
        current_vals = getattr(self, "current_vals", {})
        mode = current_vals.get("picture_mode")
        if mode in (None, "", "null", "N/A"):
            return
        crosshair = self._crosshair_value_from_vals()
        if crosshair is None:
            return

        previous_mode = getattr(self, "_crosshair_last_mode", None)
        self._crosshair_last_mode = mode
        in_game_mode = is_game_picture_mode(mode)

        if in_game_mode:
            entering_game_mode = (
                previous_mode is not None
                and not is_game_picture_mode(previous_mode)
            )
            memory = self._get_crosshair_memory()
            if entering_game_mode and crosshair == 0 and memory:
                self._apply_crosshair_mode_value(
                    memory, f"进入游戏模式，已恢复记忆的准星 {memory}"
                )
            return

        if crosshair != 0:
            self._save_crosshair_memory(crosshair)
            self._apply_crosshair_mode_value(
                0, f"离开游戏模式，已隐藏准星（记忆 {crosshair}）"
            )

    def _apply_crosshair_mode_value(self, value, message):
        self._crosshair_reconcile_busy = True
        self._mark_adb_busy(2.5)

        def operation():
            with self.adb.transaction():
                self.adb.put("front_sight_index", str(value), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(message)
            self.current_vals["front_sight_index"] = value
            self._update_crosshair_mode_status_label()
            # 同步按钮高亮；重入的 reconcile 由 busy 标记挡住
            self.values_signal.emit({"front_sight_index": value})
            self._crosshair_reconcile_busy = False

        def failure():
            self._crosshair_reconcile_busy = False

        self._run_adb_action("准星模式联动", operation, success, failure)

    def _mark_adb_busy(self, seconds=2.0):
        self._adb_busy_until = max(getattr(self, "_adb_busy_until", 0.0), time.monotonic() + seconds)

    def _adb_channel_busy(self):
        if time.monotonic() < getattr(self, "_adb_busy_until", 0.0):
            return True
        if getattr(self, "_page_loading", None):
            return True
        for timer in getattr(self, "_adjust_hotkey_timers", {}).values():
            if timer.isActive():
                return True
        for timer in getattr(self, "_cycle_hotkey_timers", {}).values():
            if timer.isActive():
                return True
        return False

    def _schedule_hdr_memory_check(self, reason="manual", delay_ms=500):
        QTimer.singleShot(delay_ms, lambda r=reason: self._poll_hdr_memory_state(r))

    def _query_windows_hdr_state(self):
        try:
            return query_windows_hdr_enabled(int(self.winId()))
        except Exception:
            return query_windows_hdr_enabled()

    def _poll_hdr_memory_state(self, reason="timer"):
        visible_interval = 3000
        background_interval = 8000
        target_interval = visible_interval if self.isVisible() and not self.isMinimized() else background_interval
        if hasattr(self, "hdr_memory_timer") and self.hdr_memory_timer.interval() != target_interval:
            self.hdr_memory_timer.setInterval(target_interval)

        win_state = self._query_windows_hdr_state()
        if win_state is None:
            self._hdr_windows_state = None
            self._hdr_last_state = None
            self._hdr_state_source = None
            self._update_hdr_memory_status_label("Windows HDR 状态未知")
            return
        self._on_hdr_memory_state_detected(win_state, "Windows HDR")

    def _on_hdr_memory_state_detected(self, state, source):
        if state is None:
            self._update_hdr_memory_status_label("信号状态未知")
            return
        self._hdr_windows_state = state
        self._reconcile_hdr_memory_state(source)

    def _reconcile_hdr_memory_state(self, source=None):
        windows_state = getattr(self, "_hdr_windows_state", None)
        if windows_state is None:
            self._hdr_last_state = None
            self._hdr_state_source = None
            self._update_hdr_memory_status_label(source or "信号状态未知")
            return
        state = windows_state

        previous = getattr(self, "_hdr_last_state", None)
        # previous 为 None 表示连接/启动后的首次检测：只是确立基线，并非真正的 HDR 切换。
        # 首次检测是否会走到“已连接”分支取决于轮询与连接完成的时序，之前靠这个竞争来决定
        # 要不要刷新，很不稳定；这里显式区分基线与真实切换，让行为确定。
        is_baseline = previous is None
        changed = state != previous
        self._hdr_last_state = state
        self._hdr_state_source = "Windows"
        self._update_hdr_memory_status_label(source)
        update_tone_visibility = getattr(self, "_update_hdr_tone_mapping_visibility", None)
        if callable(update_tone_visibility):
            update_tone_visibility()
        if changed:
            memory_enabled = self._hdr_memory_enabled()
            if memory_enabled:
                self._schedule_hdr_memory_apply(delay_ms=120)
            # 首次基线检测不当作“切换”：连接时已加载/预加载画面页，无需重复刷新。
            # 仅在真实切换(SDR↔HDR)、或开启记忆需要回读已应用结果时才刷新。
            if not is_baseline or memory_enabled:
                self._schedule_picture_refresh_after_hdr_change(
                    state,
                    initial_delay_ms=300 if memory_enabled else 0,
                    is_switch=not is_baseline,
                )

    def _schedule_picture_refresh_after_hdr_change(self, state, initial_delay_ms=0, is_switch=True):
        if not getattr(self, "adb_connected", False):
            return
        state_name = "HDR" if state else "SDR"
        if is_switch:
            self.log(f"检测到 Windows HDR 切换为 {state_name}，刷新画面数据...")
        else:
            self.log(f"检测到当前 Windows 为 {state_name}，应用 HDR 记忆并刷新画面数据...")
        self._page_loaded.discard("picturePage")
        QTimer.singleShot(initial_delay_ms, lambda: self._refresh_picture_data_after_hdr_change())
        timer = getattr(self, "_hdr_picture_refresh_timer", None)
        if timer:
            timer.stop()
            timer.start(3500)

    def _refresh_picture_data_after_hdr_change(self, retries=8):
        if not getattr(self, "adb_connected", False):
            return
        apply_timer = getattr(self, "_hdr_memory_apply_timer", None)
        if apply_timer and apply_timer.isActive():
            if retries > 0:
                QTimer.singleShot(400, lambda r=retries - 1: self._refresh_picture_data_after_hdr_change(r))
            return
        if self._adb_channel_busy():
            if retries > 0:
                QTimer.singleShot(400, lambda r=retries - 1: self._refresh_picture_data_after_hdr_change(r))
            return
        self._page_loaded.discard("picturePage")
        if "picturePage" in getattr(self, "_page_loading", set()):
            if retries > 0:
                QTimer.singleShot(400, lambda r=retries - 1: self._refresh_picture_data_after_hdr_change(r))
            return
        self._refresh_page_data("picturePage")

    def _update_hdr_memory_status_label(self, source=None):
        label = getattr(self, "hdr_memory_status_label", None)
        if not label:
            return
        enabled = self._hdr_memory_enabled()
        state = getattr(self, "_hdr_last_state", None)
        state_text = "未知" if state is None else ("HDR" if state else "SDR")
        state_source = getattr(self, "_hdr_state_source", None)
        state_source_text = f"（{state_source}）" if state_source and state is not None else ""
        memory = self._get_local_dimming_memory()
        sdr_val = memory.get("sdr")
        hdr_val = memory.get("hdr")
        sdr_text = LOCAL_DIMMING_NAMES.get(sdr_val, "--") if isinstance(sdr_val, int) else "--"
        hdr_text = LOCAL_DIMMING_NAMES.get(hdr_val, "--") if isinstance(hdr_val, int) else "--"
        prefix = "已开启" if enabled else "已关闭"
        source_text = f"，{source}" if source and state is None else ""
        runtime_note = ""
        if enabled and self._close_behavior_is_direct_exit():
            runtime_note = "；已选择直接退出，此功能仅在应用运行时生效"
            label.setTextColor(QColor(216, 59, 1), QColor(216, 59, 1))
        else:
            label.setTextColor(QColor(120, 120, 120), QColor(255, 255, 255, 140))
        label.setText(f"分区控光记忆：{prefix}，当前信号：{state_text}{state_source_text}，记忆模式：SDR={sdr_text}，HDR={hdr_text}{source_text}{runtime_note}")

    def _schedule_hdr_memory_apply(self, delay_ms=250):
        timer = getattr(self, "_hdr_memory_apply_timer", None)
        if not timer:
            return
        timer.stop()
        timer.start(delay_ms)

    def _apply_hdr_memory_for_current_state(self):
        if self.memories_suspended_by_preset():
            return
        if not self._hdr_memory_enabled() or not getattr(self, "adb_connected", False):
            return
        state = getattr(self, "_hdr_last_state", None)
        if state is None:
            return
        if self._adb_channel_busy():
            self._schedule_hdr_memory_apply()
            return
        bucket = "hdr" if state else "sdr"
        memory = self._get_local_dimming_memory()
        value = memory.get(bucket)
        if not isinstance(value, int):
            return
        try:
            current = int(self.current_vals.get("picture_local_dimming"))
        except Exception:
            current = None
        if current == value:
            return
        state_name = "HDR" if state else "SDR"
        value_name = LOCAL_DIMMING_NAMES.get(value, str(value))
        if getattr(self, "osd", None):
            self.osd.show_hud(f"{state_name} 精密控光", value_name)
        self._set_local_dimming_for_memory(value, f"{state_name} 精密控光记忆: {value_name}")

    def _set_local_dimming_for_memory(self, value, message):
        value = max(0, min(3, int(value)))
        self._mark_adb_busy(2.5)
        previous = self.current_vals.get("picture_local_dimming")
        self.current_vals["picture_local_dimming"] = value
        self.current_vals["tv_picture_video_local_dimming"] = value
        self.values_signal.emit({
            "picture_local_dimming": value,
            "tv_picture_video_local_dimming": value,
        })

        def operation():
            with self.adb.transaction():
                self.adb.jni_set("g_video__vid_local_dimming", value, check=True)
                self.adb.put("picture_local_dimming", str(value), check=True)
                self.adb.put("tv_picture_video_local_dimming", str(value), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(message)

        def failure():
            if previous is not None:
                self.current_vals["picture_local_dimming"] = previous
                self.current_vals["tv_picture_video_local_dimming"] = previous
                self.values_signal.emit({
                    "picture_local_dimming": previous,
                    "tv_picture_video_local_dimming": previous,
                })

        self._run_adb_action("HDR/SDR 精密控光", operation, success, failure)

    def _read_current_local_dimming_for_memory(self):
        if self._adb_channel_busy():
            self._schedule_hdr_memory_apply()
            return
        self._mark_adb_busy(1.5)

        def do():
            try:
                value = int(self.query_setting_or_jni("picture_local_dimming"))
            except Exception:
                return
            value = max(0, min(3, value))
            self.values_signal.emit({
                "picture_local_dimming": value,
                "tv_picture_video_local_dimming": value,
            })

        async_run(do)

    def _get_local_dimming_memory(self):
        memory = load_settings().get("local_dimming_memory", {})
        result = {}
        for key in ("sdr", "hdr"):
            try:
                value = int(memory.get(key))
                result[key] = max(0, min(3, value))
            except Exception:
                result[key] = None
        return result

    def _save_local_dimming_memory(self, memory):
        update_settings({"local_dimming_memory": memory})
        self._update_hdr_memory_status_label()

    def _ensure_local_dimming_memory_defaults(self):
        memory = self._get_local_dimming_memory()
        try:
            current = int(self.current_vals["picture_local_dimming"])
        except Exception:
            return
        current = max(0, min(3, current))
        changed = False
        for key in ("sdr", "hdr"):
            if not isinstance(memory.get(key), int):
                memory[key] = current
                changed = True
        if changed:
            self._save_local_dimming_memory(memory)

    def _remember_local_dimming_value(self, value, log_change=True, force_bucket=None):
        if not self._hdr_memory_enabled():
            return
        if self.memories_suspended_by_preset():
            # 预设生效期间记忆是冻结的：既不去改设备，也不吸收预设的值 ——
            # 否则停用预设后它会拿一堆预设的值当作"用户偏好"去还原。
            return
        if not log_change:
            return
        try:
            value = max(0, min(3, int(value)))
        except Exception:
            return
        if time.monotonic() < getattr(self, "_local_dimming_memory_suppress_until", 0.0):
            return
        if value == 0 and getattr(self, "_local_dimming_toggle_suspended", False):
            return
        bucket = force_bucket
        if bucket is None:
            state = getattr(self, "_hdr_last_state", None)
            if state is None:
                state = self._query_windows_hdr_state()
            if state is None:
                return
            bucket = "hdr" if state else "sdr"
        memory = self._get_local_dimming_memory()
        if memory.get(bucket) == value:
            return
        memory[bucket] = value
        self._save_local_dimming_memory(memory)
        if log_change:
            state_name = "HDR" if bucket == "hdr" else "SDR"
            self.log(f"已记忆 {state_name} 精密控光: {LOCAL_DIMMING_NAMES.get(value, value)}")

    def _highlight_btn(self, btn, is_active):
        if isinstance(btn, ToggleButton):
            btn.blockSignals(True)
            btn.setChecked(is_active)
            btn.blockSignals(False)
        else:
            if is_active:
                btn.setStyleSheet("""
                    QPushButton, PushButton {
                        background-color: #0078d4;
                        border: 1px solid #0078d4;
                        color: white;
                        font-weight: bold;
                        border-radius: 5px;
                    }
                    QPushButton:hover, PushButton:hover {
                        background-color: #0086f0;
                        border: 1px solid #0086f0;
                    }
                    QPushButton:pressed, PushButton:pressed {
                        background-color: #006cc0;
                        border: 1px solid #006cc0;
                    }
                """)
            else:
                btn.setStyleSheet("")

    def _highlight_mode(self, mode):
        try:
            mode_int = int(mode)
        except Exception:
            mode_int = mode
        for m, btn in self.mode_btns.items():
            group = PICTURE_MODE_GROUPS.get(m, {m})
            self._highlight_btn(btn, mode_int in group or str(m) == str(mode))
        self._update_picture_mode_hint(mode_int)
        self._update_hdr_tone_mapping_visibility(mode_int)
        self._update_freesync_memory_status_label()

    def _picture_mode_group_name(self, mode):
        for primary, name in ((14, "标准"), (10, "游戏"), (9, "电影")):
            if mode in PICTURE_MODE_GROUPS.get(primary, {primary}):
                return name
        return None

    def _active_picture_scene_mode(self):
        current_vals = getattr(self, "current_vals", {})
        try:
            scenario = int(current_vals.get("picture_preset_scenario"))
        except (TypeError, ValueError):
            scenario = None
        if scenario not in (None, 0):
            return scenario
        try:
            return int(current_vals.get("picture_mode"))
        except (TypeError, ValueError):
            return scenario

    def _is_game_mode_active(self):
        current_vals = getattr(self, "current_vals", {})
        return any(
            is_game_picture_mode(current_vals.get(key))
            for key in ("picture_mode", "picture_preset_scenario")
        )

    def _update_game_mode_hint(self):
        label = getattr(self, "game_mode_hint_label", None)
        if not label:
            return
        current_vals = getattr(self, "current_vals", {})
        mode_known = any(
            current_vals.get(key) not in (None, "", "null", "N/A")
            for key in ("picture_mode", "picture_preset_scenario")
        )
        if not mode_known:
            label.setText("当前画面模式未知；高亮值尚未确认是否生效。")
            label.setStyleSheet("color: #f0b85a; font-size: 12px;")
        elif DisplayFeaturesMixin._is_game_mode_active(self):
            label.setText("当前为游戏模式；下方高亮为当前生效值。")
            label.setStyleSheet("font-size: 12px;")
        else:
            label.setText("当前不是游戏模式；下方高亮为记忆值，功能当前未生效。")
            label.setStyleSheet("color: #f0b85a; font-size: 12px;")

    def _update_hdr_tone_mapping_visibility(self, mode=None):
        card = getattr(self, "hdr_tone_mapping_card", None)
        if not card:
            return
        if mode is None:
            mode = DisplayFeaturesMixin._active_picture_scene_mode(self)
        visible = is_hdr_tone_mapping_picture_mode(mode)
        card.setVisible(visible)

    def _update_picture_mode_hint(self, mode):
        label = getattr(self, "picture_mode_hint_label", None)
        if not label:
            return

        try:
            mode_int = int(mode)
        except Exception:
            mode_int = None

        if mode_int is None:
            label.setText(f"当前场景：未知（{mode}）")
            label.setStyleSheet("color: #f0b85a; font-size: 12px;")
            return

        group_name = self._picture_mode_group_name(mode_int)
        if group_name:
            label.setText(f"当前场景：{group_name}（{mode_int}）")
            FluentStyleSheet.LABEL.apply(label)
            return

        scene_name = PICTURE_SCENE_NAMES.get(mode_int)
        if scene_name:
            label.setText(f"当前场景：{scene_name}")
            FluentStyleSheet.LABEL.apply(label)
        else:
            label.setText(f"当前场景：未知场景（{mode_int}），不匹配上方模式按钮")
            label.setStyleSheet("color: #f0b85a; font-size: 12px;")

    def _set_mode(self, val, name):
        if not self.check_connection(): return
        self._mark_adb_busy(3.0)
        self._picture_mode_switch_seq += 1
        seq = self._picture_mode_switch_seq
        previous = self._take_control_previous("picture_mode")

        def operation():
            self.adb.put("picture_mode", str(val), check=True)

        def success():
            self._note_picture_change()
            self.current_vals["picture_mode"] = val
            self._highlight_mode(val)
            self.log(f"模式: {name}")
            self._page_loaded.discard("picturePage")
            self._reconcile_crosshair_mode_state()
            QTimer.singleShot(1200, lambda seq=seq, val=val: self._refresh_picture_page_after_mode_switch(seq, val))

        def failure():
            if previous is not None:
                self.current_vals["picture_mode"] = previous
                self._highlight_mode(previous)

        self._run_adb_action("画面模式", operation, success, failure)

    def _refresh_picture_page_after_mode_switch(self, seq, expected_mode):
        if seq != getattr(self, "_picture_mode_switch_seq", 0):
            return
        if not getattr(self, "adb_connected", False):
            return
        if str(self.current_vals.get("picture_mode")) != str(expected_mode):
            return
        if "picturePage" in self._page_loading:
            QTimer.singleShot(500, lambda seq=seq, expected_mode=expected_mode: self._refresh_picture_page_after_mode_switch(seq, expected_mode))
            return
        self._page_loaded.discard("picturePage")
        self._refresh_page_data("picturePage")

    def _reset_current_mode(self):
        if not self.check_connection(): return
        cur = self.current_vals.get("picture_mode")
        if cur not in self._MODE_NAMES:
            self.log("无法获取当前模式")
            return
        mode_name = self._MODE_NAMES[cur]
        w = MessageBox("恢复默认设置", f"你确定要恢复当前模式：{mode_name} 的默认设置吗？", self)
        accepted = w.exec()
        w.deleteLater()
        if accepted:
            self._mark_adb_busy(4.0)

            def operation():
                with self.adb.transaction():
                    self.adb.jni_set("g_fusion_picture__pic_reset_def_bypicmode", 0, check=True)
                    self.adb.refresh_pq(check=True)

            def success():
                self._note_picture_change()
                self.log(f"已恢复 {mode_name} 模式默认设置，等待生效...")
                self._page_loaded.discard("picturePage")
                QTimer.singleShot(3000, lambda: self._refresh_page_data("picturePage"))

            self._run_adb_action("恢复画面默认设置", operation, success)

    def _optimistic_highlight(self, key, val):
        if key in self.state_buttons:
            for v, btn in self.state_buttons[key].items():
                self._highlight_btn(btn, str(v) == str(val))
        if key == "picture_color_temperature":
            self._update_color_gain_visibility(val)

    def _update_color_gain_visibility(self, color_temp=None):
        is_custom = str(color_temp if color_temp is not None else self.current_vals.get("picture_color_temperature")) == str(CUSTOM_COLOR_TEMP_VALUE)
        for card in getattr(self, "color_gain_cards", []):
            card.setVisible(is_custom)

    def _set(self, k, v, m):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous(k)

        def operation():
            with self.adb.transaction():
                if k == "mitv.tvplayer.hdmi.last.source":
                    self.adb.shell("am force-stop com.xiaomi.mitv.tvplayer", check=True)
                    self.adb.shell(f"am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY -n com.xiaomi.mitv.tvplayer/.ExternalSourceActivity --ei input {v} -f 0x10000000", check=True)
                else:
                    self.adb.put(k, str(v), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(m)
            self.current_vals[k] = v
            self._optimistic_highlight(k, v)
            if k == "mitv.tvplayer.hdmi.last.source":
                self.source_var_text = self._source_names.get(v, f"未知 ({v})")
                self.source_label.setText(self.source_var_text)
                self._start_source_polling()

        def failure():
            if previous is not None:
                self._optimistic_highlight(k, previous)

        self._run_adb_action(m.split(":", 1)[0], operation, success, failure)

    def _confirm_switch_to_game_mode(self, feature_name):
        w = MessageBox(
            "需要游戏模式",
            f"当前不是游戏模式，{feature_name}的设置只能在游戏模式下生效。\n\n是否切换到游戏模式并继续？",
            self,
        )
        accepted = w.exec()
        w.deleteLater()
        return bool(accepted)

    def _confirm_game_feature_dependency(self, feature_name, dependency_name):
        w = MessageBox(
            "需要开启前置功能",
            f"{feature_name}需要先启用{dependency_name}才能生效。\n\n是否同时启用{dependency_name}？",
            self,
        )
        accepted = w.exec()
        w.deleteLater()
        return bool(accepted)

    def _set_game_feature(self, key, value, message, retrigger_game_mode=False):
        if not self.check_connection():
            return
        previous = self.current_vals.get(key, 0)
        previous_values = {key: previous}
        game_mode_active = DisplayFeaturesMixin._is_game_mode_active(self)
        switch_to_game = value != 0 and not game_mode_active
        feature_name = message.split(":", 1)[0]
        if switch_to_game:
            if not DisplayFeaturesMixin._confirm_switch_to_game_mode(self, feature_name):
                self._optimistic_highlight(key, previous)
                return

        updates = {}
        dependency = _GAME_FEATURE_DEPENDENCIES.get(key) if value != 0 else None
        if dependency:
            dependency_key, dependency_value, dependency_name = dependency
            try:
                dependency_enabled = int(self.current_vals.get(dependency_key, 0)) != 0
            except (TypeError, ValueError):
                dependency_enabled = False
            if not dependency_enabled:
                if not DisplayFeaturesMixin._confirm_game_feature_dependency(
                    self, feature_name, dependency_name,
                ):
                    self._optimistic_highlight(key, previous)
                    return
                previous_values[dependency_key] = self.current_vals.get(dependency_key, 0)
                updates[dependency_key] = dependency_value
        updates[key] = value

        self._mark_adb_busy(3.0 if switch_to_game else 2.5)
        if switch_to_game:
            self._picture_mode_switch_seq = getattr(self, "_picture_mode_switch_seq", 0) + 1

        def operation():
            with self.adb.transaction():
                for setting, setting_value in updates.items():
                    self.adb.put(setting, str(setting_value), check=True)
                if switch_to_game:
                    self.adb.put("picture_mode", "10", check=True)
                elif retrigger_game_mode and game_mode_active:
                    self.adb.put("picture_mode", "14", check=True)
                    time.sleep(0.5)
                    self.adb.put("picture_mode", "10", check=True)
                else:
                    self.adb.refresh_pq(check=True)

        def success():
            self.current_vals.update(updates)
            for setting, setting_value in updates.items():
                self._optimistic_highlight(setting, setting_value)
            self.log(message)
            if switch_to_game:
                self.current_vals["picture_mode"] = 10
                self._highlight_mode(10)
                self._update_game_mode_hint()
                self.log("已切换到游戏模式")
                self._refresh_pages(("picturePage", "gamePage"), delay_ms=1200, force=True)

        def failure():
            self.current_vals.update(previous_values)
            for setting, setting_value in previous_values.items():
                self._optimistic_highlight(setting, setting_value)

        self._run_adb_action(message.split(":", 1)[0], operation, success, failure)

    def _jni(self, jk, v, sk, m, osd_sk=None):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous(sk)

        def operation():
            with self.adb.transaction():
                self.adb.jni_set(jk, v, check=True)
                self.adb.put(sk, str(v), check=True)
                if osd_sk:
                    self.adb.put(osd_sk, str(v), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self._note_picture_change()
            self.log(m)
            self.current_vals[sk] = v
            if osd_sk:
                self.current_vals[osd_sk] = v
            self._optimistic_highlight(sk, v)
            if sk == "picture_local_dimming":
                self._local_dimming_toggle_suspended = False
                self._local_dimming_memory_suppress_until = 0.0
                self._remember_local_dimming_value(v)

        def failure():
            if previous is not None:
                self.current_vals[sk] = previous
                self._optimistic_highlight(sk, previous)

        self._run_adb_action(m.split(":", 1)[0], operation, success, failure)

    def _set_hdr_tone_mapping(self, ui_value, name):
        if not self.check_connection():
            return
        mtk_value = HDR_TONE_MAPPING_UI_TO_MTK.get(ui_value)
        if mtk_value is None:
            return

        self._mark_adb_busy(2.5)
        state_key = "settings_display_hdr_color_tone"
        previous = self._take_control_previous(state_key)

        def operation():
            with self.adb.transaction():
                self.adb.check_and_heal_jar()
                self.adb.hdr_tone_mapping(mtk_value, check=True)
                self.adb.put("picture_hdr_tone_mapping", str(mtk_value), check=True)
                self.adb.put(state_key, str(ui_value), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self._note_picture_change()
            self.log(f"HDR 色调映射: {name}")
            self.current_vals["picture_hdr_tone_mapping"] = mtk_value
            self.current_vals[state_key] = ui_value
            self._optimistic_highlight(state_key, ui_value)

        def failure():
            if previous is not None:
                self.current_vals[state_key] = previous
                self._optimistic_highlight(state_key, previous)

        self._run_adb_action("HDR 色调映射", operation, success, failure)

    def _set_color_temp(self, jv, sv, m):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous("picture_color_temperature")

        def operation():
            with self.adb.transaction():
                self.adb.jni_set("g_video__clr_temp", jv, check=True)
                self.adb.put("picture_color_temperature", str(sv), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self._note_picture_change()
            self.log(m)
            self.current_vals["picture_color_temperature"] = sv
            self._optimistic_highlight("picture_color_temperature", sv)

        def failure():
            if previous is not None:
                self._optimistic_highlight("picture_color_temperature", previous)

        self._run_adb_action("色温", operation, success, failure)

    def _set_color_gain(self, title, settings_key, jni_key, value):
        if not self.check_connection():
            return
        self._mark_adb_busy(3.0)
        previous_temp = self.current_vals.get("picture_color_temperature")
        previous_values = {
            key: self.current_vals.get(key, 1024)
            for key in ("picture_red_gain", "picture_green_gain", "picture_blue_gain")
        }
        if str(self.current_vals.get("picture_color_temperature")) != str(CUSTOM_COLOR_TEMP_VALUE):
            self.current_vals["picture_color_temperature"] = CUSTOM_COLOR_TEMP_VALUE
            self._optimistic_highlight("picture_color_temperature", CUSTOM_COLOR_TEMP_VALUE)

        gain_controls = [
            ("picture_red_gain", "red_gain", "g_video__clr_gain_r"),
            ("picture_green_gain", "green_gain", "g_video__clr_gain_g"),
            ("picture_blue_gain", "blue_gain", "g_video__clr_gain_b"),
        ]
        values = {}
        for setting, slider_name, _jni_key in gain_controls:
            if setting == settings_key:
                gain = value
            elif setting in self.current_vals:
                gain = self.current_vals.get(setting, 1024)
            elif slider_name in self.sliders:
                gain = self.sliders[slider_name][0].value()
            else:
                gain = self.current_vals.get(setting, 1024)
            try:
                gain = int(gain)
            except Exception:
                gain = 1024
            values[setting] = max(524, min(1524, gain))
        self.current_vals.update(values)

        def operation():
            with self.adb.transaction():
                self.adb.jni_set("g_video__clr_temp", XIAOMI_TO_MTK_COLOR_TEMP[CUSTOM_COLOR_TEMP_VALUE], check=True)
                self.adb.put("picture_color_temperature", str(CUSTOM_COLOR_TEMP_VALUE), check=True)
                self.adb.jni_set_color_gains(
                    values["picture_red_gain"],
                    values["picture_green_gain"],
                    values["picture_blue_gain"],
                    check=True,
                )
                for setting, _slider_name, _gain_jni_key in gain_controls:
                    self.adb.put(setting, str(values[setting]), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self._note_picture_change()
            self.log(f"{title}: {value}")

        def failure():
            self.current_vals.update(previous_values)
            if previous_temp is not None:
                self.current_vals["picture_color_temperature"] = previous_temp
            restored = dict(previous_values)
            if previous_temp is not None:
                restored["picture_color_temperature"] = previous_temp
            self.values_signal.emit(restored)

        self._run_adb_action(title, operation, success, failure)

    def _fs(self, v):
        # 用户在游戏模式下的主动选择同步进记忆：关掉即清记忆（避免下次回到
        # 游戏模式又被自动还原回来），选了样式则更新记忆值。
        if v == 0:
            self._clear_crosshair_memory()
        else:
            self._save_crosshair_memory(v)
        self._set_game_feature(
            "front_sight_index",
            v,
            f"准星: {'关' if v == 0 else v}",
            retrigger_game_mode=True,
        )

    def _get_input_source(self, check=False):
        """获取当前输入源"""
        return self.adb.get("mitv.tvplayer.hdmi.last.source", check=check)

    def _320(self, on):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous("mode_320")

        def operation():
            with self.adb.transaction():
                src = self._get_input_source(check=True)
                if src in ("29","30"): self.adb.jni_set("g_fusion_picture__dp_edid_version", 3 if on else 2, check=True)
                else: self.adb.jni_set("g_fusion_picture__hdmi_edid_version", 6 if on else 1, check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(f"320Hz: {'开' if on else '关'}")
            self.current_vals["mode_320"] = 1 if on else 0
            self._optimistic_highlight("mode_320", 1 if on else 0)
            # 切换 EDID 版本会连带影响刷新率 / FreeSync / 画面参数，延迟刷新画面页和游戏页以回读真实值
            self._refresh_pages(("picturePage", "gamePage"), delay_ms=1500, force=True)

        def failure():
            if previous is not None:
                self._optimistic_highlight("mode_320", previous)

        self._run_adb_action("320Hz", operation, success, failure)

    def _fsync(self, on):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous("freesync")

        # FreeSync Pro 模式记忆：开启前记录画面模式，关闭时切回（开启后显示器会自动切到游戏模式）
        restore_mode = None
        if self._freesync_mode_memory_enabled() and not self.memories_suspended_by_preset():
            if on:
                # 仅在从关到开时记录，避免重复开启把记忆覆盖成游戏模式
                if self.current_vals.get("freesync") != 1:
                    self._remember_freesync_previous_mode()
            elif self.current_vals.get("freesync") == 1:
                restore_mode = self._get_freesync_memory_mode()

        def operation():
            with self.adb.transaction():
                src = self._get_input_source(check=True)
                if src in ("29","30"): self.adb.jni_set("g_video__dp_adaptive_sync", 1 if on else 0, check=True)
                else: self.adb.jni_set("g_video__freesync_switch", 3 if on else 0, check=True)
                # 关闭 FreeSync 且开启模式记忆：先切回开启前的画面模式，再刷新数据（切换早于刷新）
                if not on and restore_mode is not None:
                    self.adb.put("picture_mode", str(restore_mode), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(f"FreeSync: {'开' if on else '关'}")
            self.current_vals["freesync"] = 1 if on else 0
            # FreeSync 在预设范围内，手动开关要写回当前预设
            self._note_picture_change()
            self._optimistic_highlight("freesync", 1 if on else 0)
            if not on and restore_mode is not None:
                self.current_vals["picture_mode"] = restore_mode
                self._highlight_mode(restore_mode)
                self.log(f"FreeSync 模式记忆: 已切回 {self._picture_mode_display_name(restore_mode)}")
            # FreeSync 改的是 adaptive sync / EDID 相关开关，开关后刷新画面页和游戏页以回读真实值
            self._refresh_pages(("picturePage", "gamePage"), delay_ms=1500, force=True)

        def failure():
            if previous is not None:
                self._optimistic_highlight("freesync", previous)

        self._run_adb_action("FreeSync", operation, success, failure)

    def _screen_light_int(self, key, default):
        try:
            return int(self.current_vals.get(key, default))
        except (TypeError, ValueError):
            return default

    def _commit_screen_light(self, message, updates):
        if not self.check_connection():
            return
        self._mark_adb_busy(2.0)
        previous = {key: self.current_vals.get(key) for key in updates}
        self.current_vals.update(updates)
        for key, val in updates.items():
            self._optimistic_highlight(key, val)

        mode = self._screen_light_int("atmosphere_light_switcher_pm2", 4)
        illumination = self._screen_light_int("atmosphere_light_illumination", 9)
        color_temp = self._screen_light_int("atmosphere_light_color_temp", 1)
        color_value = self._screen_light_int("atmosphere_light_color_value", 0)

        def operation():
            with self.adb.transaction():
                self.adb.put("atmosphere_light_switcher_pm2", str(mode), check=True)
                self.adb.put("atmosphere_light_illumination", str(illumination), check=True)
                self.adb.put("atmosphere_light_color_temp", str(color_temp), check=True)
                self.adb.put("atmosphere_light_color_value", str(color_value), check=True)
                if mode == 0:
                    self.adb.colorful_led("lighting", illumination, color_temp, check=True)
                elif mode == 1:
                    self.adb.colorful_led("ambient", check=True)
                elif mode == 2:
                    self.adb.colorful_led("solid", illumination, color_value, check=True)
                elif mode == 3:
                    self.adb.colorful_led("cycle", check=True)
                else:
                    self.adb.colorful_led("off", check=True)

        def success():
            self.log(message)

        def failure():
            restored = {key: val for key, val in previous.items() if val is not None}
            if restored:
                self.current_vals.update(restored)
                self.values_signal.emit(restored)

        self._run_adb_action("屏幕灯", operation, success, failure)

    def _set_screen_light_mode(self, val, name):
        self._commit_screen_light(f"屏幕灯模式: {name}", {"atmosphere_light_switcher_pm2": val})

    def _set_screen_light_illumination(self, ui_val):
        raw_val = max(0, min(14, int(ui_val) - 1))
        self._commit_screen_light(f"屏幕灯亮度挡位: {int(ui_val)}", {"atmosphere_light_illumination": raw_val})

    def _set_screen_light_color_temp(self, val, name):
        self._commit_screen_light(
            f"屏幕灯色温: {name}",
            {"atmosphere_light_switcher_pm2": 0, "atmosphere_light_color_temp": val}
        )

    def _set_screen_light_color_value(self, val, name):
        self._commit_screen_light(
            f"屏幕灯颜色: {name}",
            {"atmosphere_light_switcher_pm2": 2, "atmosphere_light_color_value": val}
        )

    def _key(self, kcode):
        if not self.check_connection(): return
        self._mark_adb_busy(1.0)

        def operation():
            self.adb.key(kcode, check=True)

        self._run_adb_action(
            "遥控器按键",
            operation,
            lambda: self.log(f"按键: {kcode}"),
        )
