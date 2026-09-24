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
MONITOR_DEFAULTTONEAREST = 2
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001


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
                displays.append({
                    "device_name": adapter.DeviceName,
                    "device_id": monitor.DeviceID,
                    "label": f"{monitor.DeviceString or '显示器'} · {slot_label}",
                })
    return displays


def resolve_hdr_target_display(displays, configured_id=None):
    """Never substitute another screen when a configured target disappears."""
    if configured_id:
        return next((item for item in displays if item["device_id"] == configured_id), None)
    matches = [item for item in displays if "XMI27B3" in item["device_id"].upper()]
    if len(matches) == 1:
        return matches[0]
    return displays[0] if len(displays) == 1 else None


def _select_hdr_output_state(outputs, target_device_name=None, target_monitor=None):
    if target_device_name:
        return next((hdr for name, monitor, hdr in outputs
                     if name == target_device_name), None)
    if target_monitor:
        return next((hdr for name, monitor, hdr in outputs
                     if monitor == target_monitor), None)
    return any(hdr for name, monitor, hdr in outputs) if outputs else None


def query_windows_hdr_enabled(window_handle=None, *, target_device_id=None):
    """Return True/False for the active Windows HDR color space, or None when unavailable."""
    if sys.platform != "win32":
        return None
    try:
        target_monitor = None
        target_device_name = None
        if target_device_id:
            target = resolve_hdr_target_display(list_windows_displays(), target_device_id)
            if target is None:
                return None
            target_device_name = target["device_name"]
        if not target_device_name and window_handle and user32:
            target_monitor = user32.MonitorFromWindow(wt.HWND(int(window_handle)), MONITOR_DEFAULTTONEAREST)
            try:
                target_monitor = int(target_monitor or 0)
            except Exception:
                target_monitor = None

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
                                        try:
                                            monitor = int(desc.Monitor or 0)
                                        except Exception:
                                            monitor = None
                                        attached_outputs.append((desc.DeviceName, monitor, is_hdr))
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

        return _select_hdr_output_state(attached_outputs, target_device_name, target_monitor)
    except Exception:
        return None


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
