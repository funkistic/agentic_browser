import sys
import ctypes
from ctypes import wintypes, windll
sys.path.append(r"k:\agent_browser\src-tauri\agent-engine")
from win32_embedder import embed_chromium_into_nexus, _get_tauri_hwnd, _get_chromium_hwnd_by_title
user32 = windll.user32

def enum_cb(hwnd, _):
    if user32.IsWindowVisible(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            print("WINDOW TITLE:", buf.value)
    return True

EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
user32.EnumWindows(EnumProc(enum_cb), 0)
