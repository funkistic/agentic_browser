import os
import asyncio
import atexit
import signal
import sys

try:
    from dotenv import load_dotenv
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(base_dir, ".env"), override=True)
except ImportError:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from coordinator import coordinator_instance
import uvicorn

# ─── Graceful Shutdown: Kill Chrome when the server exits ────────────────────
def _cleanup_browser():
    """Kill Chrome/chromedriver when the server process dies."""
    try:
        from agents import browser_agent as ba
        if ba.uc_driver is not None:
            print("[Nexus] Shutdown: killing browser process...")
            ba.uc_driver.quit()
    except Exception as e:
        print(f"[Nexus] Cleanup error (non-fatal): {e}")

atexit.register(_cleanup_browser)

# Handle SIGTERM (sent by Tauri's taskkill /T) cleanly
def _handle_sigterm(signum, frame):
    print("[Nexus] SIGTERM received. Running cleanup...")
    _cleanup_browser()
    sys.exit(0)

try:
    signal.signal(signal.SIGTERM, _handle_sigterm)
except Exception:
    pass  # Not all platforms support all signals in all contexts


import threading
import psutil
from contextlib import asynccontextmanager

def _parent_watchdog():
    """Thread: if our parent process (Tauri) dies, kill ourselves (and Chrome via atexit)."""
    parent_pid = os.getppid()
    print(f"[Nexus Watchdog] Monitoring parent PID: {parent_pid}")
    while True:
        import time
        time.sleep(2)
        try:
            if not psutil.pid_exists(parent_pid):
                print(f"[Nexus Watchdog] Parent PID {parent_pid} is gone. Self-terminating...")
                _cleanup_browser()
                os.kill(os.getpid(), signal.SIGTERM)
                break
        except Exception:
            break

@asynccontextmanager
async def lifespan(app):
    # Start parent watchdog thread
    t = threading.Thread(target=_parent_watchdog, daemon=True)
    t.start()
    # Start screenshot streaming loop
    from agents.browser_agent import _screenshot_stream_loop
    asyncio.create_task(_screenshot_stream_loop())
    yield
    # Cleanup handled by atexit


app = FastAPI(title="Agentic Workspace Sidecar", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],

    allow_headers=["*"],
)

class AgentRequest(BaseModel):
    prompt: str
    context: str = "workspace"

from fastapi.responses import Response
import logging

LIVE_LOGS: list = []

class UIStreamHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            LIVE_LOGS.append(msg)
            if len(LIVE_LOGS) > 200:
                LIVE_LOGS.pop(0)
        except Exception:
            pass

ui_handler = UIStreamHandler()
ui_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("browser_use").setLevel(logging.INFO)
logging.getLogger("browser_use").addHandler(ui_handler)
logging.getLogger("agent").setLevel(logging.INFO)
logging.getLogger("agent").addHandler(ui_handler)
# Also capture root logger so our own logging.info() calls show up
logging.getLogger().addHandler(ui_handler)

@app.get("/api/logs")
def get_live_logs():
    return {"logs": LIVE_LOGS[-30:]}

@app.get("/health")
def health_check():
    return {"status": "Agent Engine Online", "port": 14143}

@app.post("/api/execute")
async def execute_task(req: AgentRequest):
    """Main execution hook from the React UI."""
    result = await coordinator_instance.execute_goal(req)
    if isinstance(result, dict):
        return {
            "status": "success",
            "response": result.get("response", ""),
            "logs": result.get("logs", [])
        }
    return {"status": "success", "response": result, "logs": []}


class SettingsPayload(BaseModel):
    api_keys: list[dict]
    primary_model: str

@app.get("/api/settings")
def get_settings():
    from settings_manager import settings_db
    models = settings_db.get_models()
    return {
        "status": "success",
        "api_keys": settings_db.get_keys(),
        "primary_model": models["primary"],
    }

@app.post("/api/settings")
def update_settings(payload: SettingsPayload):
    from settings_manager import settings_db
    
    # Ensure all objects have required fields
    clean_keys = []
    for i, k in enumerate(payload.api_keys):
        clean_keys.append({
            "name": k.get("name", f"API Key #{i+1}").strip(),
            "key": k.get("key", "").strip(),
            "exhausted_until": k.get("exhausted_until", None)
        })
        
    settings_db.save({
        "api_keys": [k for k in clean_keys if k["key"]],
        "primary_model": payload.primary_model
    })
    return {"status": "success"}

# ─── Credential Vault Endpoints ───────────────────────────────────────────────

@app.get("/api/credentials")
def list_credentials():
    from database import local_db
    return {"status": "success", "credentials": local_db.list_credentials()}

class CredentialPayload(BaseModel):
    domain: str
    username: str
    password: str = ""

@app.post("/api/credentials")
def save_credential(payload: CredentialPayload):
    from database import local_db
    local_db.save_credential(
        domain=payload.domain.lower().strip(),
        username=payload.username.strip(),
        password=payload.password
    )
    return {"status": "success"}

@app.delete("/api/credentials/{cred_id}")
def delete_credential(cred_id: int):
    from database import local_db
    local_db.delete_credential(cred_id)
    return {"status": "success"}

# ─── HITL Control Endpoints ───────────────────────────────────────────────────


@app.post("/api/pause")
async def pause_agent():
    """HITL: Pause the running agent — captures current browser context."""
    try:
        import agents.browser_agent as ba
        from agents.browser_agent import AgentState

        if ba.AGENT_STATE != AgentState.RUNNING:
            return {"status": "noop", "detail": f"Agent is {ba.AGENT_STATE.value}, not running."}

        # Snapshot before pausing
        ctx = {}
        try:
            page = await _get_active_page()
            if page:
                ctx = {"url": page.url, "title": await page.title()}
        except Exception:
            pass
        ba.PAUSE_CONTEXT = ctx
        ba._set_state(AgentState.PAUSED)

        ctx = ba.PAUSE_CONTEXT
        ctx_str = f" (Browser: {ctx.get('title','?')} @ {ctx.get('url','?')})" if ctx else ""
        msg = f"⏸️ Agent PAUSED{ctx_str}. Browser session preserved. Send a new command or click Resume."
        LIVE_LOGS.append(msg)
        logging.info(msg)

        return {"status": "paused", "browser_context": ctx}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/api/resume")
async def resume_agent():
    """HITL: Resume — re-injects browser context into the LLM prompt chain."""
    try:
        import agents.browser_agent as ba
        from agents.browser_agent import AgentState

        if ba.AGENT_STATE != AgentState.PAUSED:
            return {"status": "noop", "detail": f"Agent is {ba.AGENT_STATE.value}, not paused."}

        # Transition back to IDLE so execute() can pick it up
        ba._set_state(AgentState.IDLE)
        msg = "▶️ Agent RESUMED. Browser context will be re-injected on next task."
        LIVE_LOGS.append(msg)
        logging.info(msg)

        return {"status": "resumed"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/api/stop")
async def stop_agent():
    """HITL: Hard stop — cancels the running asyncio task immediately."""
    try:
        import agents.browser_agent as ba
        from agents.browser_agent import AgentState

        task = ba._running_agent_task
        if task and not task.done():
            task.cancel()
            LIVE_LOGS.append("🛑 Agent STOPPED by user. Task cancelled.")
        else:
            LIVE_LOGS.append("🛑 Stop requested — no active task to cancel.")

        ba._set_state(AgentState.IDLE)
        ba.PAUSE_CONTEXT  = {}
        ba.RESUME_CONTEXT = ""

        return {"status": "stopped"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/api/status")
async def get_agent_status():
    """Returns full agent state for the UI including 2FA flag."""
    try:
        import agents.browser_agent as ba
        raw_state = ba.AGENT_STATE.value  # Use browser_agent as single source of truth
        
        ctx = {}
        try:
            page = await _get_active_page()
            if page:
                ctx = {"url": page.url, "title": await page.title()}
        except:
            pass
            
        # Build dynamic reasoning HUD natively from agent's internal history
        live_plan = None
        current_step_idx = 0
        try:
            agent = getattr(ba, "_global_agent_instance", None)
            if agent and hasattr(agent, "history") and hasattr(agent.history, "history"):
                hist_steps = agent.history.history
                steps = []
                for idx, h in enumerate(hist_steps):
                    intent = "Analyzing page structure..."
                    target_str = ""
                    try:
                        out = h.model_output
                        if out and hasattr(out, "current_state"):
                            state_obj = out.current_state
                            intent = getattr(state_obj, "next_goal", "") or getattr(state_obj, "evaluation_previous_goal", "Thinking...")
                        
                        if out and hasattr(out, "action") and isinstance(out.action, list) and len(out.action) > 0:
                            act = out.action[0]
                            # Sometimes dict, sometimes class
                            if isinstance(act, dict):
                                target_str = list(act.keys())[0]
                            else:
                                target_str = act.__class__.__name__
                    except Exception:
                        pass
                        
                    steps.append({
                        "id": idx + 1,
                        "intent": str(intent)[:120] + ("..." if len(str(intent)) > 120 else ""),
                        "action": "running",
                        "target": target_str,
                        "status": "success" if idx < len(hist_steps) - 1 else "success" # It finished this evaluation ring
                    })
                
                # Append active 'thinking' block ahead of the history
                if raw_state == "running" or raw_state == "waiting_2fa":
                    steps.append({
                        "id": len(steps) + 1,
                        "intent": "🧠 Analyzing DOM & generating next action..." if raw_state == "running" else "⏸️ Waiting for OTP...",
                        "action": "evaluating",
                        "target": "",
                        "status": "running"
                    })
                
                live_plan = {"goal": "Multi-Step Cognitive Execution Trail", "steps": steps}
                current_step_idx = len(steps) - 1 if steps else 0
        except Exception as hud_e:
            import logging
            logging.debug(f"HUD build fault: {hud_e}")
            pass
            
        return {
            "state": raw_state,
            "paused":      raw_state == "paused",
            "running":     raw_state == "running",
            "stopped":     raw_state == "stopped",
            "waiting_2fa": raw_state == "waiting_2fa",
            "browser_context": ctx,
            "current_model":    getattr(ba, "_current_model", None),
            "current_key_name": getattr(ba, "_current_key_name", None),
            "live_plan": live_plan,
            "current_step_idx": current_step_idx,
        }
    except Exception:
        return {"state": "idle", "paused": False, "running": False, "stopped": False,
                "waiting_2fa": False, "browser_context": {}}


class TwoFAPayload(BaseModel):
    code: str

@app.post("/api/submit-2fa")
async def submit_2fa(payload: TwoFAPayload):
    """Receives OTP code from user and unblocks the waiting agent."""
    try:
        import agents.browser_agent as ba
        from agents.browser_agent import AgentState
        if ba.AGENT_STATE != AgentState.WAITING_2FA:
            return {"status": "error", "detail": "Agent is not waiting for 2FA."}
        # Set the code and fire the event to unblock the agent coroutine
        ba._2FA_CODE = payload.code.strip()
        ba._2FA_EVENT.set()
        LIVE_LOGS.append(f"🔐 2FA code submitted by user — resuming agent.")
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ─── Legacy embed-rect (kept for compat but no longer used for Win32) ────────
GLOBAL_EMBED_RECT = {"x": 0, "y": 0, "w": 0, "h": 0}

class EmbedRectPayload(BaseModel):
    x: float
    y: float
    width: float
    height: float

@app.post("/api/embed-rect")
def update_embed_rect(payload: EmbedRectPayload):
    return {"status": "noop"}  # No longer used


# ─── WebSocket Screenshot Stream ─────────────────────────────────────────────
@app.websocket("/ws/browser-stream")
async def browser_stream_ws(websocket: WebSocket):
    """Streams live browser screenshots to the React frontend."""
    await websocket.accept()
    try:
        from agents.browser_agent import _SCREENSHOT_CLIENTS, LATEST_SCREENSHOT_B64
        import agents.browser_agent as ba
        ba._SCREENSHOT_CLIENTS.add(websocket)
        logging.info(f"📺 Browser stream client connected.")
        # Send latest frame immediately so client doesn't wait
        if ba.LATEST_SCREENSHOT_B64:
            await websocket.send_text(ba.LATEST_SCREENSHOT_B64)
        # Keep connection alive until client disconnects
        while True:
            try:
                await websocket.receive_text()  # drain any pings
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.warning(f"Browser stream WS error: {e}")
    finally:
        try:
            import agents.browser_agent as ba
            ba._SCREENSHOT_CLIENTS.discard(websocket)
        except Exception:
            pass
        logging.info("📺 Browser stream client disconnected.")


# ─── Shared page accessor ────────────────────────────────────────────────────
async def _get_active_page():
    """Return the currently active Playwright page via browser_use API."""
    try:
        from agents.browser_agent import _global_browser
        if _global_browser is None:
            return None
        return await _global_browser.get_current_page()
    except Exception:
        return None


async def _get_scaling(page):
    try:
        w = await page.evaluate("window.innerWidth")
        h = await page.evaluate("window.innerHeight")
        return w, h
    except Exception:
        vp = page.viewport_size
        return (vp["width"], vp["height"]) if vp else (1280, 900)

class ClickPayload(BaseModel):
    x_ratio: float
    y_ratio: float

@app.post("/api/browser-click")
async def browser_click(payload: ClickPayload):
    page = await _get_active_page()
    if not page: return {"status": "no_page"}
    try:
        w, h = await _get_scaling(page)
        await page.mouse.click(int(payload.x_ratio * w), int(payload.y_ratio * h))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


class MovePayload(BaseModel):
    x_ratio: float
    y_ratio: float

@app.post("/api/browser-move")
async def browser_move(payload: MovePayload):
    page = await _get_active_page()
    if not page: return {"status": "no_page"}
    try:
        w, h = await _get_scaling(page)
        await page.mouse.move(int(payload.x_ratio * w), int(payload.y_ratio * h))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


class ScrollPayload(BaseModel):
    delta_x: float
    delta_y: float

@app.post("/api/browser-scroll")
async def browser_scroll(payload: ScrollPayload):
    page = await _get_active_page()
    if not page: return {"status": "no_page"}
    try:
        await page.mouse.wheel(payload.delta_x, payload.delta_y)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


class KeyPayload(BaseModel):
    key: str
    modifiers: dict = {}

_KEY_MAP = {
    "Enter": "Enter", "Backspace": "Backspace", "Delete": "Delete",
    "Tab": "Tab", "Escape": "Escape", "ArrowUp": "ArrowUp",
    "ArrowDown": "ArrowDown", "ArrowLeft": "ArrowLeft", "ArrowRight": "ArrowRight",
    "Home": "Home", "End": "End", "PageUp": "PageUp", "PageDown": "PageDown",
    " ": "Space",
}

@app.post("/api/browser-key")
async def browser_key(payload: KeyPayload):
    page = await _get_active_page()
    if not page: return {"status": "no_page"}
    try:
        key = payload.key
        if key in _KEY_MAP:
            mods = []
            if payload.modifiers.get("ctrl"):  mods.append("Control")
            if payload.modifiers.get("shift"): mods.append("Shift")
            if payload.modifiers.get("alt"):   mods.append("Alt")
            chord = "+".join(mods + [_KEY_MAP[key]]) if mods else _KEY_MAP[key]
            await page.keyboard.press(chord)
        elif len(key) == 1:
            await page.keyboard.type(key)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logging.error(f"Global UI Server Error: {exc}")
    return Response(content=f"Internal Server Error: {str(exc)}", status_code=500)

if __name__ == "__main__":
    try:
        uvicorn.run(app, host="127.0.0.1", port=14143, log_level="info", access_log=False)
    except Exception as e:
        print(f"FAILED TO START AGENT ENGINE: {e}")
