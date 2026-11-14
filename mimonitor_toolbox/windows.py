"""Windows HDR、原生消息和开机启动辅助。"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import uuid

# Native Windows Hotkey support variables
user32 = None
WM_HOTKEY = 0x0312
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_DISPLAYCHANGE = 0x007E
WM_SETTINGCHANGE = 0x001A
WM_POWERBROADCAST = 0x0218
PBT_APMRESUMEAUTOMATIC = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020 = 12
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001
ENUM_CURRENT_SETTINGS = -1
# 小米系显示器的 EDID PNP 厂商码（XMI3009 = 2026 款 G Pro 27U，XMI27B3 = 2025 款）。
# 只匹配厂商码、不锁具体型号：型号串随年头变，写死会让新机型多屏时识别不到。
HDR_TARGET_VENDOR_PREFIX = "XMI"


class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wt.WCHAR * 32),
        ("dmSpecVersion", wt.WORD),
        ("dmDriverVersion", wt.WORD),
        ("dmSize", wt.WORD),
        ("dmDriverExtra", wt.WORD),
        ("dmFields", wt.DWORD),
        ("dmPositionX", wt.LONG),
        ("dmPositionY", wt.LONG),
        ("dmDisplayOrientation", wt.DWORD),
        ("dmDisplayFixedOutput", wt.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wt.WCHAR * 32),
        ("dmLogPixels", wt.WORD),
        ("dmBitsPerPel", wt.DWORD),
        ("dmPelsWidth", wt.DWORD),
        ("dmPelsHeight", wt.DWORD),
        ("dmDisplayFlags", wt.DWORD),
        ("dmDisplayFrequency", wt.DWORD),
        ("dmICMMethod", wt.DWORD),
        ("dmICMIntent", wt.DWORD),
        ("dmMediaType", wt.DWORD),
        ("dmDitherType", wt.DWORD),
        ("dmReserved1", wt.DWORD),
        ("dmReserved2", wt.DWORD),
        ("dmPanningWidth", wt.DWORD),
        ("dmPanningHeight", wt.DWORD),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _RECTL(ctypes.Structure):
    _fields_ = [
        ("left", wt.LONG),
        ("top", wt.LONG),
        ("right", wt.LONG),
        ("bottom", wt.LONG),
    ]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("DeviceName", wt.WCHAR * 32),
        ("DeviceString", wt.WCHAR * 128),
        ("StateFlags", wt.DWORD),
        ("DeviceID", wt.WCHAR * 128),
        ("DeviceKey", wt.WCHAR * 128),
    ]


class _DXGI_OUTPUT_DESC1(ctypes.Structure):
    _fields_ = [
        ("DeviceName", wt.WCHAR * 32),
        ("DesktopCoordinates", _RECTL),
        ("AttachedToDesktop", wt.BOOL),
        ("Rotation", ctypes.c_int),
        ("Monitor", wt.HMONITOR),
        ("BitsPerColor", wt.UINT),
        ("ColorSpace", ctypes.c_int),
        ("RedPrimary", ctypes.c_float * 2),
        ("GreenPrimary", ctypes.c_float * 2),
        ("BluePrimary", ctypes.c_float * 2),
        ("WhitePoint", ctypes.c_float * 2),
        ("MinLuminance", ctypes.c_float),
        ("MaxLuminance", ctypes.c_float),
        ("MaxFullFrameLuminance", ctypes.c_float),
    ]


_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_GUID_POINTER = ctypes.POINTER(_GUID)
_VOID_POINTER_POINTER = ctypes.POINTER(ctypes.c_void_p)
_DXGI_OUTPUT_DESC1_POINTER = ctypes.POINTER(_DXGI_OUTPUT_DESC1)
_VTABLE_POINTER = ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
_RELEASE_PROTO = _WINFUNCTYPE(wt.ULONG, ctypes.c_void_p)
_ENUM_INDEXED_PROTO = _WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    wt.UINT,
    _VOID_POINTER_POINTER,
)
_QUERY_INTERFACE_PROTO = _WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    _GUID_POINTER,
    _VOID_POINTER_POINTER,
)
_GET_DESC1_PROTO = _WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    _DXGI_OUTPUT_DESC1_POINTER,
)


def _make_guid(value):
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _as_uint(hr):
    return hr & 0xFFFFFFFF


def _com_method(ptr, index, prototype):
    vtable = ctypes.cast(ptr, _VTABLE_POINTER).contents
    return prototype(vtable[index])


def _release_com(ptr):
    if not ptr:
        return
    _com_method(ptr, 2, _RELEASE_PROTO)(ptr)


if sys.platform == "win32":
    try:
        user32 = ctypes.windll.user32
    except Exception as e:
        print(f"Failed to load user32: {e}")


def dispatch_power_broadcast(message, power_event, on_resume):
    """Dispatch Windows resume notifications and report whether they were handled."""

    if (
        int(message) != WM_POWERBROADCAST
        or int(power_event) != PBT_APMRESUMEAUTOMATIC
    ):
        return False
    on_resume()
    return True


def list_windows_displays():
    """List attached monitors with stable interface IDs and their DXGI display names."""
    if sys.platform != "win32" or not user32:
        return []
    enum_devices = user32.EnumDisplayDevicesW
    enum_devices.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(_DISPLAY_DEVICEW), wt.DWORD]
    enum_devices.restype = wt.BOOL
    displays = []
    slot_occurrence = {}
    adapter_index = 0
    while True:
        adapter = _DISPLAY_DEVICEW()
        adapter.cb = ctypes.sizeof(adapter)
        if not enum_devices(None, adapter_index, ctypes.byref(adapter), 0):
            break
        adapter_index += 1
        if not adapter.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            continue
        monitor_index = 0
        while True:
            monitor = _DISPLAY_DEVICEW()
            monitor.cb = ctypes.sizeof(monitor)
            if not enum_devices(adapter.DeviceName, monitor_index, ctypes.byref(monitor),
                                EDD_GET_DEVICE_INTERFACE_NAME):
                break
            monitor_index += 1
            if monitor.DeviceID:
                display_name = adapter.DeviceName.rsplit("\\", 1)[-1]
                display_number = display_name.removeprefix("DISPLAY")
                slot_label = f"屏幕 {display_number}" if display_number.isdigit() else display_name
                # 槽位名来自适配器（\\.\DISPLAY1 -> 屏幕 1），而一个适配器下可能挂多台
                # monitor（MST 菊花链、虚拟屏），只用适配器名会出两条一模一样的
                # "屏幕 1"。第二台起补个序号，保证下拉里能区分。
                slot_occurrence[slot_label] = slot_occurrence.get(slot_label, 0) + 1
                if slot_occurrence[slot_label] > 1:
                    slot_label = f"{slot_label}-{slot_occurrence[slot_label]}"
                displays.append({
                    "device_name": adapter.DeviceName,
                    "device_id": monitor.DeviceID,
                    "label": _display_label(monitor.DeviceString, monitor.DeviceID, slot_label),
                })
    return displays


def display_product_code(device_id):
    """从设备接口名里取 EDID 产品码：``\\\\?\\DISPLAY#XMI3009#5&...`` -> ``XMI3009``。

    "屏幕 N" 只是 Windows 的显示槽位名，换接口就会变；两台都报
    "Generic PnP Monitor" 时，只有这个产品码能把它们分开。
    """
    parts = str(device_id or "").split("#")
    return parts[1].strip() if len(parts) > 1 else ""


# 系统给的占位名，装了通用驱动的显示器都长这样，写进下拉只是噪声（还带本地化变体）
_GENERIC_MONITOR_NAMES = {
    "generic pnp monitor",
    "generic monitor",
    "generic non-pnp monitor",
    "default monitor",
    "通用即插即用监视器",
    "通用非即插即用监视器",
    "显示器",
}


def _display_label(device_string, device_id, slot_label):
    """下拉项与「目标屏：」用的文案：型号（如果是真名） · 产品码 · 屏幕 N。

    三样都可能缺：占位名丢掉、产品码解析不到就不写，但屏幕槽位一定在。
    """
    name = str(device_string or "").strip()
    parts = []
    if name and name.lower() not in _GENERIC_MONITOR_NAMES:
        parts.append(name)
    code = display_product_code(device_id)
    if code:
        parts.append(code)
    parts.append(slot_label)
    return " · ".join(parts)


def resolve_hdr_target_display(displays, configured_id=None):
    """挑出要读 HDR 状态的那块屏；拿不准时返回 None，绝不退而求其次选别的屏。"""
    if configured_id:
        return next((item for item in displays if item["device_id"] == configured_id), None)
    # 按厂商码而不是具体型号匹配：作者在 2025 款（XMI27B3）上验证，2026 款是
    # XMI3009 —— 写死型号会让新机型在多屏时识别不到，联动静默暂停。
    matches = [item for item in displays
               if HDR_TARGET_VENDOR_PREFIX in item["device_id"].upper()]
    if len(matches) == 1:
        return matches[0]
    return displays[0] if len(displays) == 1 else None


def _select_hdr_output_state(outputs, target_device_name=None):
    r"""按 GDI 名字（``\\.\DISPLAY1``）挑那块屏的 HDR 状态。

    指定了名字却找不到就返回 None —— 不退回"任意一块开着 HDR 的屏"，那正是原来的 bug。
    """
    if target_device_name:
        return next((hdr for name, hdr in outputs if name == target_device_name), None)
    return any(hdr for name, hdr in outputs) if outputs else None


def query_windows_hdr_enabled(*, target_device_name=None):
    """Return True/False for the active Windows HDR color space, or None when unavailable.

    ``target_device_name`` 是要查的那块屏的 GDI 名字（``\\.\\DISPLAY1``），由调用方从
    上一次枚举结果里带过来 —— 本函数**自己不再枚举显示器**：它每 3 秒被轮询一次，
    再枚举一遍纯属浪费（而且原来的 ``window_handle``／``target_device_id`` 版本正是
    因为内部重新解析，才会在双屏下出现选错屏、多枚举一次这些问题）。

    不传名字时退回"任意一块开着 HDR 的屏"，仅供无目标场景的测试/兜底使用。
    """
    if sys.platform != "win32":
        return None
    try:
        dxgi = ctypes.WinDLL("dxgi")
        create_factory = dxgi.CreateDXGIFactory1
        create_factory.argtypes = [_GUID_POINTER, _VOID_POINTER_POINTER]
        create_factory.restype = ctypes.c_long

        iid_factory1 = _make_guid("770aae78-f26f-4dba-a829-253c83d1b387")
        iid_output6 = _make_guid("068346e8-aaec-4b84-add7-137f513f77a1")
        factory_ptr = ctypes.c_void_p()
        if create_factory(ctypes.byref(iid_factory1), ctypes.byref(factory_ptr)) != 0 or not factory_ptr.value:
            return None

        attached_outputs = []
        factory = factory_ptr.value
        try:
            enum_adapters1 = _com_method(factory, 12, _ENUM_INDEXED_PROTO)
            adapter_index = 0
            while True:
                adapter_ptr = ctypes.c_void_p()
                hr = enum_adapters1(factory, adapter_index, ctypes.byref(adapter_ptr))
                if _as_uint(hr) == DXGI_ERROR_NOT_FOUND:
                    break
                if hr != 0 or not adapter_ptr.value:
                    break
                adapter = adapter_ptr.value
                try:
                    enum_outputs = _com_method(adapter, 7, _ENUM_INDEXED_PROTO)
                    output_index = 0
                    while True:
                        output_ptr = ctypes.c_void_p()
                        hr = enum_outputs(adapter, output_index, ctypes.byref(output_ptr))
                        if _as_uint(hr) == DXGI_ERROR_NOT_FOUND:
                            break
                        if hr != 0 or not output_ptr.value:
                            break
                        output = output_ptr.value
                        try:
                            query_interface = _com_method(
                                output,
                                0,
                                _QUERY_INTERFACE_PROTO,
                            )
                            output6_ptr = ctypes.c_void_p()
                            if query_interface(output, ctypes.byref(iid_output6), ctypes.byref(output6_ptr)) == 0 and output6_ptr.value:
                                output6 = output6_ptr.value
                                try:
                                    desc = _DXGI_OUTPUT_DESC1()
                                    get_desc1 = _com_method(
                                        output6,
                                        27,
                                        _GET_DESC1_PROTO,
                                    )
                                    if get_desc1(output6, ctypes.byref(desc)) == 0 and desc.AttachedToDesktop:
                                        is_hdr = desc.ColorSpace == DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020
                                        attached_outputs.append((desc.DeviceName, is_hdr))
                                finally:
                                    _release_com(output6)
                        finally:
                            _release_com(output)
                        output_index += 1
                finally:
                    _release_com(adapter)
                adapter_index += 1
        finally:
            _release_com(factory)

        return _select_hdr_output_state(attached_outputs, target_device_name)
    except Exception:
        return None


def _query_windows_display_devmode(device_name):
    """读取指定 GDI 显示设备的当前 DEVMODEW；失败或不可用时返回 None。"""
    if sys.platform != "win32" or not user32 or not device_name:
        return None
    try:
        enum_settings = user32.EnumDisplaySettingsW
        enum_settings.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(_DEVMODEW)]
        enum_settings.restype = wt.BOOL
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        if not enum_settings(device_name, ENUM_CURRENT_SETTINGS & 0xFFFFFFFF, ctypes.byref(dm)):
            return None
        return dm
    except Exception:
        return None


def query_windows_display_refresh_rate(device_name):
    """查询指定 GDI 显示设备的当前 Windows 输出刷新率 (Hz)，失败或不可用时返回 None。"""
    dm = _query_windows_display_devmode(device_name)
    if dm is None:
        return None
    freq = int(dm.dmDisplayFrequency)
    return freq if freq > 1 else None


def query_windows_display_mode(device_name):
    """查询指定显示设备的当前分辨率与刷新率签名 (width, height, hz)，不可用时返回 None。"""
    dm = _query_windows_display_devmode(device_name)
    if dm is None:
        return None
    width = int(dm.dmPelsWidth)
    height = int(dm.dmPelsHeight)
    freq = int(dm.dmDisplayFrequency)
    if width <= 0 or height <= 0:
        return None
    return (width, height, freq if freq > 1 else None)


def query_target_display_refresh_rate(*, configured_id=None, target_device_name=None):
    """解析目标小米显示器并读取其当前 Windows 输出刷新率 (Hz)。

    主机读取的刷新率仅代表 Windows 桌面输出配置，在多信号源切换或 VRR 动态刷新下
    可能与显示器当前真实输入信号不一致，仅作辅助判断与提示使用。
    """
    if sys.platform != "win32" or not user32:
        return None
    device_name = target_device_name
    if not device_name:
        try:
            target = resolve_hdr_target_display(list_windows_displays(), configured_id)
        except Exception:
            target = None
        device_name = target["device_name"] if target else None
    if not device_name:
        return None
    return query_windows_display_refresh_rate(device_name)


def get_autostart_path():
    startup = os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )
    return os.path.join(startup, "RedmiToolbox.bat")


def get_executable_path():
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0])


def install_autostart(executable=None):
    executable = executable or get_executable_path()
    path = get_autostart_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as stream:
            stream.write(f'start /min "" "{executable}" --minimized\n')
        return True
    except OSError:
        return False


def remove_autostart():
    path = get_autostart_path()
    try:
        if os.path.exists(path):
            os.remove(path)
        return True
    except OSError:
        return False
