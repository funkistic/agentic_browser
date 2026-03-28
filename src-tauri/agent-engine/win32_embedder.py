"""
win32_embedder.py  –  Physical Win32 Reparenting Engine v2
===========================================================
Key improvements:
 - Screen-coordinate aware: converts React DOM rect → screen → client
 - Finds Tauri HWND by window class (WebView2) not title (avoids VS Code conflicts)
 - Aggressive polling: embeds Chrome the instant its HWND appears
 - No more "black screen": uses layered repaint after reparent
"""
import ctypes
import ctypes.wintypes
from ctypes import windll
import logging

user32    = windll.user32
dwmapi    = ctypes.WinDLL("dwmapi")

try:
    # Per-monitor DPI awareness (Windows 10+)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

GWL_STYLE     = -16
GWL_EXSTYLE   = -20
WS_POPUP      = 0x80000000
WS_CAPTION    = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU    = 0x00080000
WS_CHILD      = 0x40000000
WS_VISIBLE    = 0x10000000
WS_CLIPSIBLINGS = 0x04000000
WS_CLIPCHILDREN = 0x02000000
WS_EX_TOOLWINDOW = 0x00000080

SWP_SHOWWINDOW   = 0x0040
SWP_FRAMECHANGED = 0x0020
SWP_NOZORDER     = 0x0004
SWP_NOACTIVATE   = 0x0010
SW_SHOW          = 5

# State
_cached_tauri_hwnd    = 0
_cached_chromium_hwnd = 0
_cached_chrome_pid    = 0
_is_reparented        = False   # track once so we don't re-parent every loop


def set_chrome_pid(pid: int):
    global _cached_chrome_pid, _is_reparented, _cached_chromium_hwnd
    _cached_chrome_pid    = pid
    _is_reparented        = False   # reset so new chrome gets embedded
    _cached_chromium_hwnd = 0       # force fresh HWND lookup
    logging.info(f"[Win32] Chrome PID registered: {pid}")


def reset():
    """Call when browser is reset/killed so next boot re-embeds."""
    global _is_reparented, _cached_chromium_hwnd
    _is_reparented        = False
    _cached_chromium_hwnd = 0


# ── HWND Finders ─────────────────────────────────────────────────────────────

def _get_tauri_hwnd() -> int:
    """Find the Tauri window by its WebView2 class name — much more reliable than title."""
    global _cached_tauri_hwnd
    if _cached_tauri_hwnd and user32.IsWindow(_cached_tauri_hwnd):
        return _cached_tauri_hwnd

    found = 0
    WEBVIEW2_CLASS = "Chrome_WidgetWin_1"  # WebView2 top-level window class

    def enum_cb(hwnd, _):
        nonlocal found
        if not user32.IsWindowVisible(hwnd):
            return True
        # Check window class
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, 256)
        cls = class_buf.value

        # WebView2 windows have Chrome_WidgetWin_1 class
        if WEBVIEW2_CLASS in cls:
            # Make sure it's NOT the chrome window itself (chrome has a title with site name)
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                title_buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title_buf, length + 1)
                title = title_buf.value.lower()
                # Our Tauri app title is "tauri-app"
                if "tauri" in title or "nexus" in title:
                    found = hwnd
                    return False
        return True

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumWindows(EnumProc(enum_cb), 0)

    if not found:
        # Fallback: any visible Chrome_WidgetWin_1 that isn't obviously Chrome
        def enum_cb2(hwnd, _):
            nonlocal found
            if not user32.IsWindowVisible(hwnd):
                return True
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            if "Chrome_WidgetWin_1" in class_buf.value:
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    title_buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, title_buf, length + 1)
                    title = title_buf.value.lower()
                    if "tauri" in title:
                        found = hwnd
                        return False
            return True
        user32.EnumWindows(EnumProc(enum_cb2), 0)

    _cached_tauri_hwnd = found
    return found


def _get_chromium_hwnd_by_pid(pid: int) -> int:
    """Walk all top-level windows and find Chrome's main window by PID."""
    global _cached_chromium_hwnd
    if _cached_chromium_hwnd and user32.IsWindow(_cached_chromium_hwnd):
        win_pid = ctypes.wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(_cached_chromium_hwnd, ctypes.byref(win_pid))
        if win_pid.value == pid:
            return _cached_chromium_hwnd
        else:
            _cached_chromium_hwnd = 0  # stale

    found = 0
    best_area = 0

    def enum_cb(hwnd, _):
        nonlocal found, best_area
        win_pid = ctypes.wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
        if win_pid.value != pid:
            return True

        # Skip invisible windows
        if not user32.IsWindowVisible(hwnd):
            return True

        # Skip windows that are children (we want top-level)
        parent = user32.GetParent(hwnd)
        if parent:
            return True

        # Of all Chrome windows for this PID, grab the largest (main browser window)
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area > best_area:
            best_area = area
            found = hwnd
        return True

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumWindows(EnumProc(enum_cb), 0)
    _cached_chromium_hwnd = found
    return found


# ── Coordinate Translation ────────────────────────────────────────────────────

def _get_webview_offset(tauri_hwnd: int):
    """
    Find the WebView2 child HWND inside Tauri and return its top-left offset
    in Tauri client coordinates — needed to translate DOM rects accurately.
    """
    webview_hwnd = user32.FindWindowExW(tauri_hwnd, 0, "Chrome_WidgetWin_1", None)
    if not webview_hwnd:
        return 0, 0
    pt = ctypes.wintypes.POINT(0, 0)
    user32.ClientToScreen(webview_hwnd, ctypes.byref(pt))
    # Convert that screen point to Tauri's client space
    user32.ScreenToClient(tauri_hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def _dom_to_client(tauri_hwnd: int, dom_x: int, dom_y: int, dom_w: int, dom_h: int):
    """
    Convert a DOM bounding rect (WebView-viewport-relative) to
    coordinates relative to the Tauri HWND client area.
    """
    wv_x, wv_y = _get_webview_offset(tauri_hwnd)
    return dom_x + wv_x, dom_y + wv_y, dom_w, dom_h


# ── Core Reparent + Resize ────────────────────────────────────────────────────

def handle_resize(tauri_hwnd: int, chromium_hwnd: int):
    """Position the embedded Chrome window to exactly cover the #browser-mount div."""
    try:
        import server
        rect = server.GLOBAL_EMBED_RECT
        dom_x = rect.get("x", 0)
        dom_y = rect.get("y", 0)
        dom_w = rect.get("w", 0)
        dom_h = rect.get("h", 0)
    except Exception:
        dom_x, dom_y, dom_w, dom_h = 0, 0, 0, 0

    if dom_w == 0 or dom_h == 0:
        # Fallback: place it occupying ~60% of the right panel
        win_rect = ctypes.wintypes.RECT()
        user32.GetClientRect(tauri_hwnd, ctypes.byref(win_rect))
        tauri_w = win_rect.right - win_rect.left
        tauri_h = win_rect.bottom - win_rect.top
        dom_x, dom_y = 0, 70
        dom_w = tauri_w
        dom_h = tauri_h - 70
    
    # Translate DOM (WebView-relative) → Tauri client coords
    x, y, w, h = _dom_to_client(tauri_hwnd, dom_x, dom_y, dom_w, dom_h)

    user32.SetWindowPos(
        chromium_hwnd, 0,
        x, y, w, h,
        SWP_SHOWWINDOW | SWP_NOZORDER | SWP_NOACTIVATE
    )
    # Force a repaint to avoid black regions on resize
    user32.RedrawWindow(chromium_hwnd, None, None, 0x0001 | 0x0100)   # RDW_INVALIDATE | RDW_ALLCHILDREN


def embed_chromium_into_nexus() -> bool:
    global _is_reparented

    tauri_hwnd = _get_tauri_hwnd()
    if not tauri_hwnd:
        return False

    chromium_hwnd = 0
    if _cached_chrome_pid:
        chromium_hwnd = _get_chromium_hwnd_by_pid(_cached_chrome_pid)

    if not chromium_hwnd:
        return False

    # ── Initial Reparent (only once per browser boot) ──────────────────────
    if not _is_reparented:
        # Strip decorations and set WS_CHILD
        style = user32.GetWindowLongW(chromium_hwnd, GWL_STYLE)
        new_style = (
            (style & ~WS_POPUP & ~WS_CAPTION & ~WS_THICKFRAME & ~WS_SYSMENU)
            | WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN
        )
        user32.SetWindowLongW(chromium_hwnd, GWL_STYLE, new_style)

        # Remove the "tool window" extended style so it can become a child
        ex_style = user32.GetWindowLongW(chromium_hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(chromium_hwnd, GWL_EXSTYLE, ex_style & ~WS_EX_TOOLWINDOW)

        # Reparent into Tauri
        user32.SetParent(chromium_hwnd, tauri_hwnd)
        user32.SetWindowPos(chromium_hwnd, 0, 0, 0, 0, 0,
                            SWP_FRAMECHANGED | SWP_NOZORDER | SWP_NOACTIVATE)
        user32.ShowWindow(chromium_hwnd, SW_SHOW)

        _is_reparented = True
        logging.info(f"✅ [Win32] Reparented Chrome HWND {chromium_hwnd} → Tauri HWND {tauri_hwnd}")

    # ── Always keep Chrome flush with the mount div ──────────────────────────
    handle_resize(tauri_hwnd, chromium_hwnd)
    return True
