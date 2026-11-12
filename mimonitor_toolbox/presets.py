"""画面预设与自动任务的纯逻辑层。

这里只做「算出要写什么、按什么顺序写、写成功了没有」，不碰 Qt、不碰 UI，
`adb` 由调用方传进来（具备 put / jni_set / refresh_pq / jni_set_color_gains /
hdr_tone_mapping / check_and_heal_jar 即可），所以可以用假的 adb 直接单测。

写入顺序是强制固定的，见 PICTURE_ITEMS。**FreeSync 最先、画面模式紧随其后** ——
真机实测（2026-09-22）：写 picture_mode 会让设备重新应用该模式的整套参数
（实测切模式后色域 6→0、背光 52→40），所以参数必须写在模式之后，
否则会被模式的默认值冲掉。
"""

from dataclasses import dataclass, field

from .core import (
    CUSTOM_COLOR_TEMP_VALUE,
    HDR_TONE_MAPPING_UI_TO_MTK,
    XIAOMI_TO_MTK_COLOR_TEMP,
    is_hdr_tone_mapping_picture_mode,
)


# ===== 预设覆盖的键 =====
# 采集快照时按这两组键读设备。必须与 device_features._page_data_keys["picturePage"]
# 保持一致（有测试守着），但**不含** picture_preset_scenario —— 那是设备推导出的
# 实际场景（含 HDR 子模式），不是用户可设项，只用于界面显示。
PICTURE_PRESET_SETTINGS_KEYS = (
    "picture_mode",
    "tv_picture_light_sensor",
    "picture_backlight",
    "xiaomi_picture_backlight",
    "picture_brightness",
    "picture_contrast",
    "picture_saturation",
    "picture_hue",
    "picture_sharpness",
    "picture_color_temperature",
    "picture_red_gain",
    "picture_green_gain",
    "picture_blue_gain",
    "picture_local_dimming",
    "tv_picture_video_local_dimming",
    "picture_hdr_tone_mapping",
    "settings_display_hdr_color_tone",
    "picture_dynamic_definition",
    "picture_response_time",
    "tv_picture_advanced_video_color_space",
    "tv_picture_video_color_space",
)

PICTURE_PRESET_JNI_KEYS = (
    "g_disp__disp_back_light",
    "g_video__vid_gamut_mapping_mode",
    "g_video__clr_temp",
    "g_video__vid_local_dimming",
    "g_video__light_sensor_switch",
)


# FreeSync 在 DP/USB-C 和 HDMI 上走**不同的 MTK 键**，且"开"的取值也不同
# （HDMI 侧 3 = 开，DP 侧 1 = 开）。读写都要按当前输入源选键，不能写死。
FREESYNC_DP_SOURCES = ("29", "30")


def freesync_jni_key(source):
    """按输入源选 FreeSync 的 MTK 键。"""
    return ("g_video__dp_adaptive_sync"
            if str(source).strip() in FREESYNC_DP_SOURCES
            else "g_video__freesync_switch")


def freesync_on_value(jni_key):
    return 3 if jni_key.endswith("freesync_switch") else 1


def freesync_is_on(jni_key, raw):
    try:
        return int(str(raw).strip()) == freesync_on_value(jni_key)
    except (TypeError, ValueError):
        return False


def _as_int(values, key):
    raw = values.get(key)
    if raw is None or raw == "" or raw == "null" or raw == "N/A":
        raise ValueError(f"{key} 没有可用值（{raw!r}）")
    return int(raw)


# ===== 各分项的写序列 =====
# 每一项都复刻对应界面控件自身的完整写序列，不能只写 settings。

def _apply_freesync(adb, values):
    """FreeSync 与画面模式是绑在一起的：开着它，设备会被锁在游戏模式。

    所以它必须**排在画面模式之前**写 —— 不先把它设成目标值，后面写的画面
    模式会被它顶掉。设备行为见 display_features._fsync 的注释。
    """
    wanted = _as_int(values, "freesync") == 1
    source = adb.get("mitv.tvplayer.hdmi.last.source", check=True)
    key = freesync_jni_key(source)
    adb.jni_set(key, freesync_on_value(key) if wanted else 0, check=True)
    adb.refresh_pq(check=True)


def _has_freesync(values):
    if "freesync" not in values:
        # 早于本功能的预设没记这一项。不能猜，保持设备现状并说明。
        return False, "这条预设没有记录 FreeSync（保存于旧版本），保持现状"
    return True, ""


def _apply_picture_mode(adb, values):
    adb.put("picture_mode", str(_as_int(values, "picture_mode")), check=True)


def _apply_light_sensor(adb, values):
    value = 1 if _as_int(values, "tv_picture_light_sensor") == 1 else 0
    # isUpdate 传 1（其余 JNI 写入用 3）；光感由 ContentObserver 立即生效，
    # 所以不跟 refresh_pq —— 这两点都与界面上的 _set_light_sensor 保持一致。
    adb.jni_set("g_video__light_sensor_switch", value, upd=1, check=True)
    adb.put("tv_picture_light_sensor", str(value), check=True)


def _apply_backlight(adb, values):
    value = _as_int(values, "picture_backlight")
    adb.jni_set("g_disp__disp_back_light", value, check=True)
    adb.refresh_pq(check=True)
    adb.put("picture_backlight", str(value), check=True)
    adb.put("xiaomi_picture_backlight", str(value), check=True)


def _plain_setting(key):
    """纯 settings 项（黑色级别/对比度/饱和度/色调/锐度）：只 put，不发 refresh_pq。"""
    def apply(adb, values):
        adb.put(key, str(_as_int(values, key)), check=True)
    return apply


def _apply_color_temp(adb, values):
    ui_value = _as_int(values, "picture_color_temperature")
    if ui_value not in XIAOMI_TO_MTK_COLOR_TEMP:
        raise ValueError(f"未知色温枚举 {ui_value}")
    adb.jni_set("g_video__clr_temp", XIAOMI_TO_MTK_COLOR_TEMP[ui_value], check=True)
    adb.put("picture_color_temperature", str(ui_value), check=True)
    adb.refresh_pq(check=True)


def _apply_color_gains(adb, values):
    """色增益必须三色成组提交，且会强制把色温切成「自定义」。"""
    red = _as_int(values, "picture_red_gain")
    green = _as_int(values, "picture_green_gain")
    blue = _as_int(values, "picture_blue_gain")
    for name in (red, green, blue):
        if not 524 <= name <= 1524:
            raise ValueError(f"色增益 {name} 超出 524-1524")
    adb.jni_set("g_video__clr_temp", XIAOMI_TO_MTK_COLOR_TEMP[CUSTOM_COLOR_TEMP_VALUE], check=True)
    adb.put("picture_color_temperature", str(CUSTOM_COLOR_TEMP_VALUE), check=True)
    adb.jni_set_color_gains(red, green, blue, check=True)
    adb.put("picture_red_gain", str(red), check=True)
    adb.put("picture_green_gain", str(green), check=True)
    adb.put("picture_blue_gain", str(blue), check=True)
    adb.refresh_pq(check=True)


def _jni_setting(jni_key, setting_key, osd_key=None):
    """JNI + settings(+旧 OSD 键) + refresh_pq 的通用模板。"""
    def apply(adb, values):
        value = _as_int(values, setting_key)
        adb.jni_set(jni_key, value, check=True)
        adb.put(setting_key, str(value), check=True)
        if osd_key:
            adb.put(osd_key, str(value), check=True)
        adb.refresh_pq(check=True)
    return apply


def _apply_hdr_tone_mapping(adb, values):
    """HDR 色调映射：两个 settings 键存的是**不同枚举**，不能混用。

    `picture_hdr_tone_mapping` 存 MTK 枚举，`settings_display_hdr_color_tone` 存 UI 枚举。
    """
    ui_value = _as_int(values, "settings_display_hdr_color_tone")
    if ui_value not in HDR_TONE_MAPPING_UI_TO_MTK:
        raise ValueError(f"未知 HDR 色调映射枚举 {ui_value}")
    mtk_value = HDR_TONE_MAPPING_UI_TO_MTK[ui_value]
    adb.check_and_heal_jar()
    adb.hdr_tone_mapping(mtk_value, check=True)
    adb.put("picture_hdr_tone_mapping", str(mtk_value), check=True)
    adb.put("settings_display_hdr_color_tone", str(ui_value), check=True)
    adb.refresh_pq(check=True)


@dataclass(frozen=True)
class PictureItem:
    """一个可纳入预设的画面页项。

    - `label`：失败/跳过时汇报给人看的名字
    - `apply`：真正下发；抛异常即视为该项失败
    - `condition`：返回 False 表示本次应用跳过该项（返回 (bool, 原因)）
    """
    id: str
    label: str
    apply: object
    condition: object = None


def _needs_custom_color_temp(values):
    if _as_int(values, "picture_color_temperature") != CUSTOM_COLOR_TEMP_VALUE:
        return False, "色温不是「自定义」，色增益不生效"
    return True, ""


def _needs_hdr_mode(values):
    # 画面模式排在最前几位写入，所以这里读的是**预设自己的模式**，不是设备当前
    # 模式 —— 这正是把模式放在最前写的好处之一。
    if not is_hdr_tone_mapping_picture_mode(_as_int(values, "picture_mode")):
        return False, "预设的模式不是 HDR/Dolby Vision，该项不存在"
    return True, ""


# 顺序即写入顺序。**FreeSync 第一、画面模式第二**，理由见模块文档字符串。
PICTURE_ITEMS = (
    # FreeSync 排最前：它开着会把设备锁在游戏模式，必须先把它设成目标值
    PictureItem("freesync", "FreeSync", _apply_freesync, _has_freesync),
    PictureItem("picture_mode", "画面模式", _apply_picture_mode),
    PictureItem("light_sensor", "自动调整亮度", _apply_light_sensor),
    PictureItem("backlight", "背光", _apply_backlight),
    PictureItem("black_level", "黑色级别", _plain_setting("picture_brightness")),
    PictureItem("contrast", "对比度", _plain_setting("picture_contrast")),
    PictureItem("saturation", "饱和度", _plain_setting("picture_saturation")),
    PictureItem("hue", "色调", _plain_setting("picture_hue")),
    PictureItem("sharpness", "锐度", _plain_setting("picture_sharpness")),
    PictureItem("color_temp", "色温", _apply_color_temp),
    PictureItem("color_gains", "色增益", _apply_color_gains, _needs_custom_color_temp),
    PictureItem(
        "local_dimming", "精密控光",
        _jni_setting("g_video__vid_local_dimming", "picture_local_dimming",
                     "tv_picture_video_local_dimming"),
    ),
    PictureItem("hdr_tone_mapping", "HDR 色调映射", _apply_hdr_tone_mapping, _needs_hdr_mode),
    PictureItem(
        "dynamic_definition", "动态清晰度",
        _jni_setting("g_video__vid_insert_black", "picture_dynamic_definition"),
    ),
    PictureItem(
        "response_time", "灰阶响应时间",
        _jni_setting("g_video__vid_od_response_time", "picture_response_time"),
    ),
    PictureItem(
        "gamut", "色域",
        _jni_setting("g_video__vid_gamut_mapping_mode",
                     "tv_picture_advanced_video_color_space",
                     "tv_picture_video_color_space"),
    ),
)


@dataclass
class ApplyResult:
    applied: list = field(default_factory=list)
    failed: list = field(default_factory=list)      # [(label, 错误文本)]
    skipped: list = field(default_factory=list)     # [(label, 原因)]

    @property
    def ok(self):
        return not self.failed

    def summary(self):
        parts = [f"成功 {len(self.applied)} 项"]
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 项")
        if self.failed:
            parts.append(f"失败 {len(self.failed)} 项：" +
                         "、".join(name for name, _ in self.failed))
        return "，".join(parts)


def apply_preset(adb, values, on_item=None):
    """按固定顺序把 `values` 逐项写进设备。

    单项失败**不中断**后续项（这是产品决定：尽量多写成功，最后汇报哪几项失败），
    所以每项的异常都收敛在它自己的边界上。注意 `adb` 调用内部仍是 check=True ——
    一项的写序列中途失败即整项判失败，不做半项的猜测性修补。
    """
    result = ApplyResult()
    for item in PICTURE_ITEMS:
        if item.condition is not None:
            try:
                should_apply, reason = item.condition(values)
            except Exception as exc:
                result.skipped.append((item.label, f"条件判断失败：{exc}"))
                continue
            if not should_apply:
                result.skipped.append((item.label, reason))
                if on_item:
                    on_item(item, "skipped", reason)
                continue
        try:
            item.apply(adb, values)
        except Exception as exc:
            result.failed.append((item.label, str(exc)))
            if on_item:
                on_item(item, "failed", str(exc))
            continue
        result.applied.append(item.label)
        if on_item:
            on_item(item, "applied", "")
    return result


# 「无预设」不是真预设，用一个不会和 new_id 生成的 id 撞车的保留值。
# 放这里而不是 pages.py：自动任务的调度也用它（选「无预设」= 这段时间不用预设）。
BASELINE_PRESET_ID = "__baseline__"


# ===== 预设的查找与构造 =====

def find_preset(presets_list, preset_id):
    """按稳定 id 查找预设；找不到返回 None。

    自动任务引用的是 **id 而不是名字** —— 名字可以改，按名字引用会让改名
    悄悄打断已有任务。
    """
    for preset in presets_list or ():
        if isinstance(preset, dict) and preset.get("id") == preset_id:
            return preset
    return None


def new_id(existing, prefix):
    """生成一个在 `existing` 里不重复的短 id。"""
    used = {item.get("id") for item in existing or () if isinstance(item, dict)}
    index = 1
    while f"{prefix}{index}" in used:
        index += 1
    return f"{prefix}{index}"


def unique_name(existing, wanted):
    """预设重名时补后缀，避免列表里出现两个同名项。"""
    taken = {item.get("name") for item in existing or () if isinstance(item, dict)}
    if wanted not in taken:
        return wanted
    index = 2
    while f"{wanted} ({index})" in taken:
        index += 1
    return f"{wanted} ({index})"


def missing_keys(values):
    """预设里缺少哪些必需项 —— 用于保存时提示、应用时预警。"""
    needed = {
        "picture_mode", "tv_picture_light_sensor", "picture_backlight",
        "picture_brightness", "picture_contrast", "picture_saturation",
        "picture_hue", "picture_sharpness", "picture_color_temperature",
        "picture_red_gain", "picture_green_gain", "picture_blue_gain",
        "picture_local_dimming", "picture_dynamic_definition",
        "picture_response_time", "tv_picture_advanced_video_color_space",
        "settings_display_hdr_color_tone",
    }
    return sorted(key for key in needed if key not in (values or {}))


# ===== 自动任务的时间段判定 =====

def parse_hhmm(text):
    """'HH:MM' -> 当天第几分钟；非法返回 None。"""
    if not isinstance(text, str) or len(text) != 5 or text[2] != ":":
        return None
    digits = text[:2] + text[3:]
    if not digits.isdigit():
        return None
    hour, minute = int(text[:2]), int(text[3:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def format_hhmm(minutes):
    minutes = int(minutes) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def is_in_period(now_minutes, start_minutes, end_minutes):
    """判断当前时刻是否落在 [start, end) 内；start > end 视为跨午夜。

    start == end 视为整天命中 —— 保存时会拦住这种输入，这里只是防御性兜底。
    """
    if start_minutes == end_minutes:
        return True
    if start_minutes < end_minutes:
        return start_minutes <= now_minutes < end_minutes
    return now_minutes >= start_minutes or now_minutes < end_minutes


def valid_task(task):
    """任务字段是否完整合法（时间段可解析、引用了预设）。"""
    if not isinstance(task, dict):
        return False
    if not all(isinstance(task.get(k), str) and task.get(k)
               for k in ("id", "preset_id")):
        return False
    start = parse_hhmm(task.get("start"))
    end = parse_hhmm(task.get("end"))
    return start is not None and end is not None and start != end


def find_active_task(tasks, now_minutes):
    """返回当前命中的任务；多个重叠时**后者覆盖前者**（取列表里最后一个命中项）。"""
    active = None
    for task in tasks or ():
        if not valid_task(task):
            continue
        if not task.get("enabled", True):
            continue
        start = parse_hhmm(task["start"])
        end = parse_hhmm(task["end"])
        if is_in_period(now_minutes, start, end):
            active = task
    return active
