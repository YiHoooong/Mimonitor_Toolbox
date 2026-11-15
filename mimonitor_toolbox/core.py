"""配置、资源定位和跨功能常量。"""

import glob
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass

_settings_lock = threading.RLock()
_logger = logging.getLogger(__name__)
_verbose_log_cache = None

def get_app_data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.environ.get("USERPROFILE", os.path.expanduser("~")))
    else:
        base = os.path.expanduser("~")
    folder = os.path.join(base, ".gpro_controller")
    os.makedirs(folder, exist_ok=True)
    return folder

def get_settings_path():
    """获取跨平台、无需管理员权限的软件配置保存路径。

    ``MIMONITOR_SETTINGS_PATH`` 可覆盖（与 ADB_SERVER_PORT 同样用环境变量
    覆盖的写法）：测试用它把配置写到临时目录，避免碰到用户真实配置。
    """
    override = os.environ.get("MIMONITOR_SETTINGS_PATH")
    if override:
        folder = os.path.dirname(override)
        if folder:
            os.makedirs(folder, exist_ok=True)
        return override
    folder = get_app_data_dir()
    return os.path.join(folder, "config.json")

def get_log_dir():
    """日志目录，跟随配置放在用户可写的 app-data 下（避免 exe 装在无写权限目录时失败）"""
    return os.path.join(get_app_data_dir(), "logs")

def _as_list_of_dicts(raw):
    """列表型配置字段的兜底：非 list、或含非 dict 的项，都按项丢弃。

    只做结构校验；各项的**字段**语义（预设必带哪些键、时间段是否合法）
    由 presets.py 在读取时校验，那里才知道 schema。
    """
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _as_list_of_strings(raw, default=(), allow_ints=False):
    """列表型配置字段的兜底：非 list 时返回 default，过滤非字符串项。"""
    if not isinstance(raw, list):
        return list(default)
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append(item)
        elif allow_ints and isinstance(item, int) and not isinstance(item, bool):
            result.append(str(item))
    return result


# 「无预设」不是真预设，用一个不会和 new_id 生成的 id 撞车的保留值。
BASELINE_PRESET_ID = "__baseline__"
TRAY_PRESET_LIMIT = 4

CONNECTION_MODE_ADB = "adb"
CONNECTION_MODE_DAEMON = "daemon"
CONNECTION_MODE_OPTIONS = (
    (CONNECTION_MODE_ADB, "兼容模式 (ADB)"),
    (CONNECTION_MODE_DAEMON, "混合加速 (Daemon)"),
)
CONNECTION_MODE_LABELS = dict(CONNECTION_MODE_OPTIONS)
MTK_DAEMON_PORT = 27891


def normalize_connection_mode(mode):
    """规范化连接模式：仅识别 daemon，其余一律归为标准 adb。"""
    if str(mode or "").strip().lower() == CONNECTION_MODE_DAEMON:
        return CONNECTION_MODE_DAEMON
    return CONNECTION_MODE_ADB


def format_connection_mode_label(mode):
    """返回连接模式对应的界面显示文本。"""
    return CONNECTION_MODE_LABELS[normalize_connection_mode(mode)]


PAGE_DISPLAY_NAMES = {
    "picturePage": "画面设置",
    "gamePage": "游戏模式",
    "sourcePage": "信号源与音频",
    "lightPage": "屏幕灯",
}


def format_page_display_name(page_name):
    """将内部页面标识转换为界面展示用的中文页面名称。"""
    return PAGE_DISPLAY_NAMES.get(page_name, page_name)


def invalidate_verbose_log_cache():
    """清空 verbose_log 缓存（供写入配置或测试覆写后调用）。"""
    global _verbose_log_cache
    with _settings_lock:
        _verbose_log_cache = None


def _is_verbose_log_enabled():
    global _verbose_log_cache
    if load_settings is not _original_load_settings:
        return bool(load_settings().get("verbose_log", False))
    path = get_settings_path()
    try:
        st = os.stat(path)
        sig = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        sig = (path, None, None)
    with _settings_lock:
        if _verbose_log_cache is not None and _verbose_log_cache[:3] == sig:
            return _verbose_log_cache[3]
        enabled = bool(_load_settings_unlocked().get("verbose_log", False))
        _verbose_log_cache = (*sig, enabled)
        return enabled


def emit_detail_log(host, message):
    """写入详细日志：支持 log_detail 时按开关仅显示于详细模式（文件始终保留）。"""
    detail_fn = getattr(host, "log_detail", None)
    if callable(detail_fn):
        detail_fn(message)
        return
    if _is_verbose_log_enabled():
        log_fn = getattr(host, "log", None)
        if callable(log_fn):
            log_fn(message)


def normalize_tray_preset_ids(raw_ids, presets=None):
    """规范化托盘预设 id 列表：过滤无效 id、去重并限制最多 4 个。"""
    if not isinstance(raw_ids, (list, tuple)):
        return []
    valid = {BASELINE_PRESET_ID}
    if isinstance(presets, list):
        for preset in presets:
            if isinstance(preset, dict) and isinstance(preset.get("id"), str):
                valid.add(preset["id"])
    seen = set()
    normalized = []
    for preset_id in raw_ids:
        if not isinstance(preset_id, str) or preset_id not in valid or preset_id in seen:
            continue
        seen.add(preset_id)
        normalized.append(preset_id)
        if len(normalized) >= TRAY_PRESET_LIMIT:
            break
    return normalized


def _backup_corrupt_settings(path):
    try:
        if os.path.exists(path):
            shutil.copy2(path, f"{path}.corrupt.bak")
    except OSError:
        pass


def _read_settings_json(path):
    for attempt in range(3):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except PermissionError:
            if attempt >= 2:
                raise
            time.sleep(0.05 * (attempt + 1))


def _load_settings_unlocked(strict=False):
    defaults = {
        "close_behavior": "tray",
        "never_ask_close": False,
        "never_show_preset_edit_merged_notice": False,
        "verbose_log": False,
        "saved_ip": "",
        "connection_mode": CONNECTION_MODE_ADB,
        # 扫描网卡的高级覆盖：Hyper-V 外部虚拟交换机把物理网卡的 IP 搬到了
        # vEthernet 上（物理网卡会"非物理网卡"式地被过滤掉），这两项可以点名
        # 某块网卡 / 某个网段永远参与扫描。
        "scan_force_devices": [],
        "scan_block_devices": [],
        "scan_force_subnets": [],
        "hdr_sdr_local_dimming_enabled": False,
        "hdr_target_display_id": "",
        "local_dimming_memory": {"sdr": None, "hdr": None},
        "local_dimming_toggle_last_value": 3,
        "freesync_mode_memory_enabled": False,
        "freesync_previous_mode": None,
        "crosshair_game_mode_only": True,
        "crosshair_memory": None,
        "tray_items": ["picture_mode", "local_dimming", "backlight"],
        "tray_presets": [],
        "hotkey_countdown_enabled": True,
        "hotkey_countdown_seconds": 0.8,
        "show_volume_osd": False,
        # 预设：[{"id", "name", "values": {settings_key: value}}]
        "presets": [],
        # 自动任务：[{"id", "start": "HH:MM", "end": "HH:MM",
        #             "preset_id", "snapshot": {...}|None}]
        # snapshot 随任务自身存，不与 preset_snapshot 共用 —— 时间段内用户可能
        # 手动套用别的预设，共用槽位会让时间段结束时的还原回到错误的状态。
        "auto_tasks": [],
        # 「无预设」下那份值：从无预设切到某个预设时存下来，点无预设的应用就还原它。
        # None 表示当前没在用任何预设（= 无预设），此时画面页的改动不落进任何预设。
        "preset_snapshot": None,
        # 当前正在使用的预设 id；None = 无预设
        "active_preset_id": None,
    }
    path = get_settings_path()
    data = {}
    if os.path.exists(path):
        try:
            data = _read_settings_json(path)
        except OSError as exc:
            _logger.warning("读取配置文件失败 (%s): %s", path, exc)
            if strict:
                raise
            data = {}
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            _logger.warning("配置文件损坏，已备份并回退默认值 (%s): %s", path, exc)
            _backup_corrupt_settings(path)
            data = {}
    if not isinstance(data, dict):
        _logger.warning("配置文件根节点非对象，已备份并回退默认值 (%s)", path)
        _backup_corrupt_settings(path)
        data = {}
    merged = defaults.copy()
    merged.update(data)
    memory = merged.get("local_dimming_memory")
    if not isinstance(memory, dict):
        memory = {}
    merged["local_dimming_memory"] = {
        "sdr": memory.get("sdr"),
        "hdr": memory.get("hdr"),
    }
    merged["scan_force_devices"] = _as_list_of_strings(
        merged.get("scan_force_devices"), allow_ints=True
    )
    merged["scan_block_devices"] = _as_list_of_strings(
        merged.get("scan_block_devices"), allow_ints=True
    )
    merged["scan_force_subnets"] = _as_list_of_strings(merged.get("scan_force_subnets"))
    merged["tray_items"] = _as_list_of_strings(
        merged.get("tray_items"), default=defaults["tray_items"]
    )
    merged["presets"] = _as_list_of_dicts(merged.get("presets"))
    merged["tray_presets"] = normalize_tray_preset_ids(merged.get("tray_presets"), merged["presets"])
    merged["auto_tasks"] = _as_list_of_dicts(merged.get("auto_tasks"))
    snapshot = merged.get("preset_snapshot")
    merged["preset_snapshot"] = snapshot if isinstance(snapshot, dict) else None
    active = merged.get("active_preset_id")
    preset_ids = {preset.get("id") for preset in merged["presets"]
                  if isinstance(preset.get("id"), str)}
    merged["active_preset_id"] = active if isinstance(active, str) and active in preset_ids else None
    merged["connection_mode"] = normalize_connection_mode(merged.get("connection_mode"))
    merged["verbose_log"] = bool(merged.get("verbose_log", False))
    return merged

def load_settings():
    with _settings_lock:
        return _load_settings_unlocked()


_original_load_settings = load_settings


def _write_settings_unlocked(settings):
    path = get_settings_path()
    target_dir = os.path.dirname(path) or "."
    os.makedirs(target_dir, exist_ok=True)
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix="config.", suffix=".tmp", dir=target_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(4):
            try:
                os.replace(temp_path, path)
                break
            except PermissionError:
                if attempt >= 3:
                    raise
                time.sleep(0.05 * (attempt + 1))
        invalidate_verbose_log_cache()
        return True
    except Exception as exc:
        _logger.warning("写入配置文件失败 (%s): %s", path, exc)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def update_settings(changes):
    with _settings_lock:
        try:
            settings = _load_settings_unlocked(strict=True)
        except OSError as exc:
            _logger.warning("无法读取现有配置，跳过写入以免覆盖 (%s): %s", get_settings_path(), exc)
            return False
        settings.update(changes)
        return _write_settings_unlocked(settings)
def is_frozen_build():
    """打包态检测：PyInstaller 设置 sys.frozen，Nuitka 注入 __compiled__。"""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()

def get_app_base_dir():
    if is_frozen_build():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def bundled_resource_path(*parts):
    if hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, *parts)
        if os.path.exists(p):
            return p
    if "__compiled__" in globals():
        # Nuitka 打包：__file__ 位于解包目录的 mimonitor_toolbox/ 包内
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *parts)
        if os.path.exists(p):
            return p
    p = os.path.join(get_app_base_dir(), *parts)
    if os.path.exists(p):
        return p
    return None


def cleanup_stale_extract_dirs(temp_dir=None, min_age_seconds=300.0):
    """清理 %TEMP% 下的 onefile_/_MEI 解压残留目录。

    PyInstaller 与 Nuitka onefile 被强杀或崩溃时不会清理各自的解压目录，
    长期累积会占用上 GB 磁盘。运行中的实例必然加载着解压目录内的
    python DLL（Windows 对已加载 DLL 拒绝写打开），以此判断存活并跳过；
    不含 python DLL 的目录不属于标准解压布局，同样跳过。
    额外增加 min_age_seconds 保护，避免并发启动的新实例尚未加载 DLL 时被误删。
    """
    base = temp_dir or os.environ.get("TEMP") or tempfile.gettempdir()
    now = time.time()
    min_age = max(0.0, float(min_age_seconds))
    for prefix in ("onefile_", "_MEI"):
        try:
            candidates = glob.glob(os.path.join(base, prefix + "*"))
        except OSError:
            continue
        for path in candidates:
            if not os.path.isdir(path):
                continue
            try:
                names = os.listdir(path)
            except OSError:
                continue
            marker = next(
                (n for n in names if n.lower().startswith("python") and n.lower().endswith(".dll")),
                None,
            )
            if marker is None:
                continue
            marker_path = os.path.join(path, marker)
            if min_age > 0:
                try:
                    newest_mtime = max(os.stat(path).st_mtime, os.stat(marker_path).st_mtime)
                except OSError:
                    continue
                if now - newest_mtime < min_age:
                    continue
            try:
                with open(marker_path, "r+b"):
                    pass
            except OSError:
                continue  # DLL 被运行中的实例加载锁定
            shutil.rmtree(path, ignore_errors=True)
GUARDIAN_ACCESSIBILITY_CLASS = "AdbGuardianAccessibilityService"
GUARDIAN_PACKAGE = "com.yihoooong.monitorguardian"
GUARDIAN_MAIN_ACTIVITY = f"{GUARDIAN_PACKAGE}/.MainActivity"
GUARDIAN_ACCESSIBILITY = f"{GUARDIAN_PACKAGE}/{GUARDIAN_PACKAGE}.AdbGuardianAccessibilityService"
# Android 里改包名等于换了一个新应用：旧包不会自动升级也不会消失。两者并存会
# 同时写 adb_enabled / persist.appcontrol_w_mask、都可能 ctl.restart adbd，互相
# 打架。所以检测到旧包时提示用户，确认后先清掉旧包再部署新包。
GUARDIAN_LEGACY_PACKAGE = "com.example.adbguardian"
GUARDIAN_LEGACY_ACCESSIBILITY = (
    f"{GUARDIAN_LEGACY_PACKAGE}/{GUARDIAN_LEGACY_PACKAGE}.AdbGuardianAccessibilityService"
)
GUARDIAN_APK_NAME = "adbguardian-signed.apk"
MTK_DIRECT_TOOL_NAME = "MtkDirectTool.jar"
COLORFUL_LED_TOOL_NAME = "ColorfulLedTool.jar"
MTK_BATCH_RESULT_FILE = "/sdcard/Download/Mimonitor_Toolbox/.mtk_batch_result.txt"
XIAOMI_TO_MTK_COLOR_TEMP = {0: 1, 1: 2, 2: 3, 3: 0, 4: 4, 5: 5, 8: 6}
MTK_TO_XIAOMI_COLOR_TEMP = {v: k for k, v in XIAOMI_TO_MTK_COLOR_TEMP.items()}
HDR_TONE_MAPPING_UI_TO_MTK = {0: 5, 1: 0, 2: 2, 3: 1}
HDR_TONE_MAPPING_MTK_TO_UI = {v: k for k, v in HDR_TONE_MAPPING_UI_TO_MTK.items()}
CUSTOM_COLOR_TEMP_VALUE = 3
SOUND_OUTPUT_SETTINGS_KEY = "mitv.settings.db.sound.device.output_device"
SOUND_OUTPUT_OPTIONS = [(0, "本机扬声器"), (8, "HDMI(eARC)"), (5, "耳机孔")]
SOUND_OUTPUT_ARC_COMPAT_VALUE = 3
SOUND_OUTPUT_EARC_VALUE = 8
SOUND_OUTPUT_NAMES = {
    0: "本机扬声器",
    8: "HDMI(eARC)",
    5: "耳机孔",
    SOUND_OUTPUT_ARC_COMPAT_VALUE: "HDMI(eARC)",
}
SOUND_OUTPUT_VALID_VALUES = frozenset(SOUND_OUTPUT_NAMES.keys())


def parse_sound_output_value(value):
    """解析声音输出设备原始值为整数，空值或非法值返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "n/a"):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def normalize_sound_output_value(value):
    """规范化声音输出设备值：将兼容值 3 归类到 8 (HDMI(eARC))，无效值返回 None。"""
    parsed = parse_sound_output_value(value)
    if parsed == SOUND_OUTPUT_ARC_COMPAT_VALUE:
        return SOUND_OUTPUT_EARC_VALUE
    return parsed


def format_sound_output_label(value):
    """返回声音输出设备值对应的展示文本。"""
    parsed = parse_sound_output_value(value)
    if parsed is None:
        return "未知"
    return SOUND_OUTPUT_NAMES.get(parsed, f"未知 ({parsed})")
HOTKEY_MODIFIERS = ["无", "Ctrl + Alt", "Ctrl + Shift", "Alt + Shift", "Win + Shift"]
HOTKEY_KEYS = ["无"] + [f"F{i}" for i in range(1, 13)] + [str(i) for i in range(0, 10)] + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["+", "-", "PageUp", "PageDown", "↑", "↓", "←", "→"]
HOTKEY_EXTRA_VK = {"+": 0xBB, "-": 0xBD, "PageUp": 0x21, "PageDown": 0x22, "↑": 0x26, "↓": 0x28, "←": 0x25, "→": 0x27}
LOCAL_DIMMING_NAMES = {0: "关", 1: "低", 2: "中", 3: "高"}
ADJUSTABLE_HOTKEY_PARAMS = {
    "backlight": {
        "label": "背光", "setting": "picture_backlight", "settings": ["picture_backlight", "xiaomi_picture_backlight"],
        "jni": "g_disp__disp_back_light", "slider": "backlight", "min": 1, "max": 100, "step": 5,
    },
    "black_level": {
        "label": "黑色级别", "setting": "picture_brightness", "settings": ["picture_brightness"],
        "slider": "black_level", "min": 0, "max": 100, "step": 5,
    },
    "contrast": {
        "label": "对比度", "setting": "picture_contrast", "settings": ["picture_contrast"],
        "slider": "contrast", "min": 0, "max": 100, "step": 5,
    },
    "saturation": {
        "label": "饱和度", "setting": "picture_saturation", "settings": ["picture_saturation"],
        "slider": "saturation", "min": 0, "max": 100, "step": 5,
    },
    "hue": {
        "label": "色调", "setting": "picture_hue", "settings": ["picture_hue"],
        "slider": "hue", "min": 0, "max": 100, "step": 5,
    },
    "sharpness": {
        "label": "锐度", "setting": "picture_sharpness", "settings": ["picture_sharpness"],
        "slider": "sharpness", "min": 0, "max": 100, "step": 1,
    },
    "red_gain": {
        "label": "红色增益", "setting": "picture_red_gain", "slider": "red_gain",
        "jni": "g_video__clr_gain_r", "min": 524, "max": 1524, "step": 10, "color_gain": True,
    },
    "green_gain": {
        "label": "绿色增益", "setting": "picture_green_gain", "slider": "green_gain",
        "jni": "g_video__clr_gain_g", "min": 524, "max": 1524, "step": 10, "color_gain": True,
    },
    "blue_gain": {
        "label": "蓝色增益", "setting": "picture_blue_gain", "slider": "blue_gain",
        "jni": "g_video__clr_gain_b", "min": 524, "max": 1524, "step": 10, "color_gain": True,
    },
    "atmosphere_illumination": {
        "label": "屏幕灯亮度", "setting": "atmosphere_light_illumination", "slider": "atmosphere_illumination",
        "min": 1, "max": 15, "step": 1, "ui_offset": 1, "screen_light": True,
    },
    "volume": {
        "label": "音量", "setting": "volume_music_speaker", "slider": "volume_music_speaker",
        "min": 0, "max": 100, "step": 5, "default": 15, "media_volume": True,
    },
}
PICTURE_MODE_GROUPS = {
    14: {14, 64, 65, 66, 67, 68},
    10: {10, 25, 26, 27, 28, 29},
    9: {9},
}
GAME_FEATURE_KEYS = (
    "front_sight_index",
    "mt_game_dynamic_ft",
    "mt_game_scope",
    "mt_game_scope_night",
)
MONITOR_MENU_KEYS = (
    "monitor_menu_fps_counter",
    "monitor_menu_stopwatch",
    "monitor_menu_timer",
)
SCREEN_LIGHT_KEYS = (
    "atmosphere_light_switcher_pm2",
    "atmosphere_light_illumination",
    "atmosphere_light_color_temp",
    "atmosphere_light_color_value",
)
SCREEN_LIGHT_DEFAULTS = {
    "atmosphere_light_switcher_pm2": 4,
    "atmosphere_light_illumination": 9,
    "atmosphere_light_color_temp": 1,
    "atmosphere_light_color_value": 0,
}


def write_screen_light_values(adb, mode, illumination, color_temp, color_value):
    """写入屏幕灯四项 Settings 并通过 ColorfulLedTool 立即生效。"""
    mode = int(mode)
    illumination = int(illumination)
    color_temp = int(color_temp)
    color_value = int(color_value)
    if mode not in {0, 1, 2, 3, 4}:
        raise ValueError(f"屏幕灯模式 {mode} 超出 0-4")
    if not 0 <= illumination <= 14:
        raise ValueError(f"屏幕灯亮度 {illumination} 超出 0-14")
    if color_temp not in {0, 1, 2}:
        raise ValueError(f"屏幕灯色温 {color_temp} 超出 0-2")
    if not 0 <= color_value <= 4:
        raise ValueError(f"屏幕灯颜色 {color_value} 超出 0-4")
    adb.put("atmosphere_light_switcher_pm2", str(mode), check=True)
    adb.put("atmosphere_light_illumination", str(illumination), check=True)
    adb.put("atmosphere_light_color_temp", str(color_temp), check=True)
    adb.put("atmosphere_light_color_value", str(color_value), check=True)
    if mode == 0:
        adb.colorful_led("lighting", illumination, color_temp, check=True)
    elif mode == 1:
        adb.colorful_led("ambient", check=True)
    elif mode == 2:
        adb.colorful_led("solid", illumination, color_value, check=True)
    elif mode == 3:
        adb.colorful_led("cycle", check=True)
    else:
        adb.colorful_led("off", check=True)
GAME_PICTURE_MODES = frozenset(PICTURE_MODE_GROUPS[10]) | {4, 15, 19}

MEMC_SETTINGS_KEY = "tv_picture_advance_video_mjc_effect"
MEMC_GAME_SETTINGS_KEY = "tv_picture_game_mode_with_memc_switch"
MEMC_GAME_PREF_KEY = "tv_picture_advanced_settings_game_mode_with_memc"
MEMC_SAVE_KEY = "com.xiaomi.mitv.settings.SAVE_MEMC_VALUE"
MEMC_JNI_KEY = "g_video__vid_mjc_effect"
MEMC_GAME_JNI_KEY = "g_video__vid_game_mode_with_memc_switch"
MEMC_OPTIONS = [(0, "关"), (1, "低"), (2, "中"), (3, "高")]
MEMC_NAMES = {0: "关", 1: "低", 2: "中", 3: "高", 4: "自定义"}
# SystemControlManager.setMemcValue/getMemcValue 使用 MTK 场景枚举（10/13/16 + 64..68 等游戏子场景）
MEMC_GAME_PICTURE_MODES = frozenset(PICTURE_MODE_GROUPS[10]) | {4, 13, 15, 16, 19, 64, 65, 66, 67, 68}
# 标准模式 / PC 色域预设（MTK 14, 18, 30..34, 69..73 及 Xiaomi 2, 22, 34..43）在 OSD 禁用 MEMC
MEMC_DISALLOWED_PICTURE_MODES = frozenset({2, 14, 18, 22, *range(30, 44), *range(69, 74)})
MEMC_CONDITION_JNI_KEYS = (
    "g_video__picture_mode",
    "g_video__freesync_switch",
    "g_video__dp_adaptive_sync",
    "g_fusion_picture__hdmi_edid_version",
    "g_fusion_picture__dp_edid_version",
    "g_video__dlg_vrr_switch",
    "g_video__vid_content_adaptive",
)


def is_game_picture_mode(value):
    try:
        return int(value) in GAME_PICTURE_MODES
    except (TypeError, ValueError):
        return False


def is_memc_game_picture_mode(value):
    try:
        return int(value) in MEMC_GAME_PICTURE_MODES
    except (TypeError, ValueError):
        return False


def is_memc_supported_picture_mode(value):
    try:
        return int(value) not in MEMC_DISALLOWED_PICTURE_MODES
    except (TypeError, ValueError):
        return False


def memc_jni_key_for_mode(mode):
    return MEMC_GAME_JNI_KEY if is_memc_game_picture_mode(mode) else MEMC_JNI_KEY


def format_memc_label(value):
    try:
        return MEMC_NAMES.get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value)


def write_memc_value(adb, mode, value):
    """按当前画面模式（普通 vs 游戏）写入 MEMC 寄存器、Settings 与保存标记并刷新 PQ。"""
    val_str = str(value)
    if is_memc_game_picture_mode(mode):
        adb.jni_set(MEMC_GAME_JNI_KEY, value, check=True)
        adb.put(MEMC_GAME_SETTINGS_KEY, val_str, check=True)
        adb.put(MEMC_GAME_PREF_KEY, val_str, check=True)
    else:
        adb.jni_set(MEMC_JNI_KEY, value, check=True)
        adb.put(MEMC_SETTINGS_KEY, val_str, check=True)
    adb.put(MEMC_SAVE_KEY, val_str, check=True)
    adb.refresh_pq(check=True)


PICTURE_SCENE_NAMES = {
    9: "电影",
    10: "游戏",
    11: "Dolby Vision 明亮",
    12: "Dolby Vision 暗场",
    13: "Dolby Vision 自定义",
    14: "标准",
    15: "HDR 游戏",
    16: "HDR 图片",
    17: "HDR 电影",
    18: "Dolby Vision IQ",
    19: "Dolby Vision 游戏",
    21: "Filmmaker",
    22: "HDR 显示器",
    23: "HDR Filmmaker",
    24: "SDR 游戏 FPS",
    25: "SDR 游戏 RPG",
    26: "SDR 游戏 RTS",
    27: "SDR 游戏 MOBA",
    28: "SDR 游戏 SPT",
    29: "HDR 游戏 FPS",
    30: "HDR 游戏 RPG",
    31: "HDR 游戏 RTS",
    32: "HDR 游戏 MOBA",
    33: "HDR 游戏 SPT",
    34: "SDR PC AdobeRGB",
    35: "SDR PC DCI-P3",
    36: "SDR PC CG",
    37: "SDR PC 暗房",
    38: "SDR PC sRGB",
    39: "HDR PC AdobeRGB",
    40: "HDR PC DCI-P3",
    41: "HDR PC CG",
    42: "HDR PC 暗房",
    43: "HDR PC sRGB",
    44: "HDR Vivid",
    64: "标准预设",
    65: "标准预设",
    66: "标准预设",
    67: "标准预设",
    68: "标准预设",
}
HDR_TONE_MAPPING_PICTURE_MODES = {
    11, 12, 13, 15, 16, 17, 18, 19,
    22, 23,
    29, 30, 31, 32, 33,
    39, 40, 41, 42, 43, 44,
}

def is_hdr_tone_mapping_picture_mode(mode):
    try:
        return int(mode) in HDR_TONE_MAPPING_PICTURE_MODES
    except Exception:
        return False


def memc_mode_display_name(mode):
    """返回 MEMC 条件提示中使用的画面模式名称。"""
    try:
        mode_int = int(mode)
    except (TypeError, ValueError):
        return "未知模式"
    special = {
        2: "标准模式",
        5: "Dolby Vision IQ",
        9: "电影模式",
        10: "游戏模式",
        14: "标准模式",
        18: "HDR 标准",
        22: "HDR 显示器",
    }
    if mode_int in special:
        return special[mode_int]
    if 64 <= mode_int <= 68:
        return f"游戏模式（{mode_int}）"
    if 69 <= mode_int <= 73:
        return f"SDR PC 模式（{mode_int}）"
    if 30 <= mode_int <= 34:
        return f"HDR PC 模式（{mode_int}）"
    return PICTURE_SCENE_NAMES.get(mode_int, f"模式 {mode_int}")


@dataclass(frozen=True)
class MemcConditionItem:
    key: str
    status: str  # "ok" | "fail" | "info"
    title: str
    detail: str


@dataclass(frozen=True)
class MemcConditionReport:
    mode: object
    mode_name: str
    mode_supported: bool
    case: str  # "A" | "B" | "C"
    items: tuple
    blocking_reasons: tuple


def _pick_cond_int(jni_vals, settings_vals, jni_key, setting_key):
    for mapping, key in ((jni_vals, jni_key), (settings_vals, setting_key)):
        if not key or not mapping or key not in mapping:
            continue
        raw = mapping[key]
        if raw in (None, "", "null", "N/A"):
            continue
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            continue
    return None


def evaluate_memc_conditions(settings_vals, jni_vals=None, host_refresh_hz=None):
    """评估开启运动补偿（MEMC）的运行时条件，返回 Case A / B / C 判定与清单。"""
    settings_vals = settings_vals or {}
    jni_vals = jni_vals or {}

    mode = _pick_cond_int(jni_vals, settings_vals, "g_video__picture_mode", "picture_mode")
    if mode is None:
        mode = _pick_cond_int(jni_vals, settings_vals, None, "picture_preset_scenario")
    mode_name = memc_mode_display_name(mode)
    mode_supported = mode is not None and is_memc_supported_picture_mode(mode)

    source_raw = settings_vals.get("mitv.tvplayer.hdmi.last.source", "")
    is_dp = str(source_raw).strip() in ("29", "30")

    # 切勿在 HDMI 源下读取未激活的 dp_adaptive_sync
    if is_dp:
        dp_fs = _pick_cond_int(jni_vals, settings_vals, "g_video__dp_adaptive_sync", None)
        freesync_on = (
            dp_fs == 1
            if dp_fs is not None
            else (_pick_cond_int(jni_vals, settings_vals, None, "freesync") == 1)
        )
        dp_edid = _pick_cond_int(jni_vals, settings_vals, "g_fusion_picture__dp_edid_version", None)
        edid_320_on = (
            dp_edid == 3
            if dp_edid is not None
            else (_pick_cond_int(jni_vals, settings_vals, None, "mode_320") == 1)
        )
    else:
        hdmi_fs = _pick_cond_int(jni_vals, settings_vals, "g_video__freesync_switch", None)
        hdmi_edid = _pick_cond_int(jni_vals, settings_vals, "g_fusion_picture__hdmi_edid_version", None)
        fs_flag = (
            hdmi_fs == 3
            if hdmi_fs is not None
            else (_pick_cond_int(jni_vals, settings_vals, None, "freesync") == 1)
        )
        freesync_on = fs_flag or (hdmi_edid in (3, 5))
        edid_320_on = (
            hdmi_edid in (5, 6)
            if hdmi_edid is not None
            else (_pick_cond_int(jni_vals, settings_vals, None, "mode_320") == 1)
        )

    dlg_on = _pick_cond_int(jni_vals, settings_vals, "g_video__dlg_vrr_switch", None) == 1

    items = [
        MemcConditionItem(
            key="picture_mode",
            status="ok" if mode_supported else "fail",
            title="画面模式支持",
            detail=(
                f"当前为「{mode_name}」"
                if mode_supported
                else f"当前为「{mode_name}」（需电影 / 游戏等非标准模式）"
            ),
        ),
        MemcConditionItem(
            key="freesync",
            status="fail" if freesync_on else "ok",
            title="FreeSync 已关闭",
            detail="FreeSync 已开启，需关闭" if freesync_on else "已关闭",
        ),
    ]

    if edid_320_on and dlg_on:
        hr_detail = "320Hz 模式与 DLG 高帧率已开启，需关闭"
    elif edid_320_on:
        hr_detail = "320Hz 模式已开启，需关闭"
    elif dlg_on:
        hr_detail = "DLG 高帧率已开启，需关闭"
    else:
        hr_detail = "已关闭"
    items.append(
        MemcConditionItem(
            key="high_refresh",
            status="fail" if (edid_320_on or dlg_on) else "ok",
            title="320Hz / DLG 模式已关闭",
            detail=hr_detail,
        )
    )
    try:
        refresh_hz = int(host_refresh_hz) if host_refresh_hz is not None else None
    except (TypeError, ValueError):
        refresh_hz = None
    if refresh_hz is not None and refresh_hz <= 0:
        refresh_hz = None

    if refresh_hz is not None:
        if refresh_hz > 120:
            rr_status = "fail"
            rr_detail = (
                f"Windows 读取当前约 {refresh_hz}Hz（> 120Hz；获取值仅供参考，多信号源或 VRR 下可能不准，请调至 ≤ 120Hz）"
            )
        else:
            rr_status = "ok"
            rr_detail = (
                f"Windows 读取当前约 {refresh_hz}Hz（获取值仅供参考，多信号源或 VRR 下实际以显示器信号为准）"
            )
    else:
        rr_status = "info"
        rr_detail = "未能读取当前显示器刷新率，请确认输入信号为 120Hz 及以下"

    items.append(
        MemcConditionItem(
            key="refresh_rate",
            status=rr_status,
            title="输入信号刷新率 ≤ 120Hz",
            detail=rr_detail,
        )
    )
    if mode == 5:
        ca_on = (
            _pick_cond_int(
                jni_vals, settings_vals, "g_video__vid_content_adaptive", "picture_content_adaptive"
            )
            == 1
        )
        items.append(
            MemcConditionItem(
                key="content_adaptive",
                status="fail" if ca_on else "ok",
                title="内容自适应已关闭",
                detail="内容自适应已开启，需关闭" if ca_on else "已关闭",
            )
        )

    blocking = tuple(item.detail for item in items if item.key != "picture_mode" and item.status == "fail")
    if not mode_supported:
        case = "A"
    elif blocking:
        case = "B"
    else:
        case = "C"

    return MemcConditionReport(
        mode=mode,
        mode_name=mode_name,
        mode_supported=mode_supported,
        case=case,
        items=tuple(items),
        blocking_reasons=blocking,
    )


def format_memc_condition_dialog(report, target_value):
    """生成 MEMC 条件检测弹窗的 (title, body_text)。"""
    symbols = {"ok": "✓", "fail": "✗", "info": "ℹ"}
    checklist = "\n".join(
        f"{symbols.get(item.status, '•')} {item.title}：{item.detail}"
        for item in report.items
    )
    if report.case == "A":
        title = "当前画面模式不支持开启运动补偿"
        intro = (
            f"当前处于「{report.mode_name}」，系统在该模式下禁用运动补偿（MEMC）。"
            "请先切换至「电影模式」或「游戏模式」后再开启。"
        )
    else:
        memc_label = format_memc_label(target_value)
        title = f"已保存「{memc_label}」档位，部分条件未满足"
        intro = (
            "运动补偿档位已保存，但以下条件未满足，当前暂不生效；"
            "关闭冲突项并确保输入信号 ≤ 120Hz 后将自动生效："
        )
    return title, f"{intro}\n\n{checklist}"

def get_guardian_apk_path():
    return bundled_resource_path("assets", "adb_guardian", GUARDIAN_APK_NAME) or os.path.join(get_app_base_dir(), "assets", "adb_guardian", GUARDIAN_APK_NAME)

def _guardian_accessibility_aliases(package):
    """同一个组件在设备上可能以短形式（pkg/.Class）或全形式（pkg/pkg.Class）存在。

    实测旧包在 ``enabled_accessibility_services`` 里同时留了这两种写法，所以清理
    时要按包名整体过滤，不能只删一条。
    """
    return (f"{package}/.{GUARDIAN_ACCESSIBILITY_CLASS}",
            f"{package}/{package}.{GUARDIAN_ACCESSIBILITY_CLASS}")

def strip_guardian_package_from_accessibility(services, package):
    """从 accessibility 列表里剔除某个包的条目，返回 ``(新列表, 是否发生变化)``。"""
    if not services or services == "null":
        return services, False
    entries = [e for e in services.split(":") if e]
    kept = [e for e in entries if not e.startswith(f"{package}/")]
    if len(kept) == len(entries):
        return services, False
    return ":".join(kept), True


HDMI_EDID_STATE_MATRIX = {
    (False, False): 1,
    (False, True): 3,
    (True, False): 6,
    (True, True): 5,
}
HDMI_EDID_KNOWN_VALUES = frozenset({1, 3, 5, 6})


def hdmi_edid_for(mode_320, freesync):
    """返回 HDMI 输入源下 (320Hz, FreeSync) 组合对应的 EDID 枚举值。"""
    return HDMI_EDID_STATE_MATRIX[(bool(mode_320), bool(freesync))]


def parse_hdmi_edid_int(raw):
    """将 JNI 返回的整型文本解析为 int，失败返回 None。"""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def parse_hdmi_edid_flags(hdmi_edid, freesync_switch=None):
    """结合 HDMI EDID 版本与 freesync_switch 解析 (mode_320, freesync) 状态。"""
    edid = parse_hdmi_edid_int(hdmi_edid)
    fs = parse_hdmi_edid_int(freesync_switch)
    mode_320 = 1 if edid in (5, 6) else 0
    if edid in HDMI_EDID_KNOWN_VALUES:
        freesync = 1 if edid in (3, 5) else 0
    else:
        freesync = 1 if fs == 3 else 0
    return mode_320, freesync


def read_hdmi_edid_and_freesync(adb, check=True):
    """尽量通过 adb 现读 HDMI EDID 与 freesync_switch，兼容简化的测试替身。"""
    keys = ["g_fusion_picture__hdmi_edid_version", "g_video__freesync_switch"]
    if hasattr(adb, "jni_batch_get_or_single"):
        vals = adb.jni_batch_get_or_single(keys, check=check) or {}
        return (
            vals.get("g_fusion_picture__hdmi_edid_version"),
            vals.get("g_video__freesync_switch"),
        )
    if hasattr(adb, "jni_get"):
        return (
            adb.jni_get("g_fusion_picture__hdmi_edid_version", check=check),
            adb.jni_get("g_video__freesync_switch", check=check),
        )
    return None, None


def get_mtk_direct_tool_path():
    return bundled_resource_path("assets", "runtime", MTK_DIRECT_TOOL_NAME)

def get_colorful_led_tool_path():
    return bundled_resource_path("assets", "runtime", COLORFUL_LED_TOOL_NAME)
