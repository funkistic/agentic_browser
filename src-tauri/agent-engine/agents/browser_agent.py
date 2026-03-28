import logging
import os
import asyncio
import time
import requests
import re
from enum import Enum
from browser_use import Agent, Browser
from browser_use.llm.google.chat import ChatGoogle
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import uc_core as uc

logging.basicConfig(level=logging.INFO)

# ─── HITL State Machine ───────────────────────────────────────────────────────
class AgentState(Enum):
    IDLE         = "idle"
    RUNNING      = "running"
    PAUSED       = "paused"
    STOPPED      = "stopped"
    WAITING_2FA  = "waiting_2fa"

AGENT_STATE: AgentState = AgentState.IDLE

# Pause context
PAUSE_CONTEXT: dict = {}
RESUME_CONTEXT: str = ""
_running_agent_task: asyncio.Task | None = None
_global_agent_instance = None

# Telemetry tracking for UI status bar
_current_model:    str | None = None
_current_key_name: str | None = None

# 2FA coordination primitives
_2FA_CODE:  str | None = None   # set by /api/submit-2fa
_2FA_EVENT: asyncio.Event = asyncio.Event()  # signals code arrived

# High-performance signaling
_NETWORK_SETTLE_EVENT: asyncio.Event = asyncio.Event()
_NETWORK_LOGS: list = []  # last 20 requests/responses

class BrowserMonitor:
    """Listens to browser events for 'Sense & Act' speed.
    Allows the agent to stop waiting for page loads the instant a signal appears.
    """
    @staticmethod
    def on_console(msg):
        text = msg.text.lower()
        if any(w in text for w in ("success", "done", "logged in", "error", "fail")):
            logging.info(f"💡 Console Signal: {msg.text}")

    @staticmethod
    def on_response(response):
        # We track API calls that might indicate a successful login
        url = response.url.lower()
        if any(w in url for w in ("/api/v1/auth", "/login/success", "/graphql")):
            _NETWORK_SETTLE_EVENT.set()
            logging.info(f"🌐 Network Settle Signal: {response.url}")


# Global browser singletons
_global_browser = None
uc_driver        = None

def is_paused()  -> bool: return AGENT_STATE == AgentState.PAUSED
def is_running() -> bool: return AGENT_STATE == AgentState.RUNNING
def is_stopped() -> bool: return AGENT_STATE == AgentState.STOPPED

def _set_state(state: AgentState, log_msg: str = ""):
    global AGENT_STATE
    AGENT_STATE = state
    if log_msg:
        logging.info(log_msg)

# ─── Chrome Boot ─────────────────────────────────────────────────────────────
def spawn_uc():
    global uc_driver
    if uc_driver is None:
        logging.info("🚀 Booting Undetected-Chromedriver (use_subprocess=True)...")
        for attempt in range(3):
            try:
                options = uc.ChromeOptions()
                base_dir    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                profile_dir = os.path.join(base_dir, ".agent_profile")
                os.makedirs(profile_dir, exist_ok=True)
                options.add_argument(f"--user-data-dir={profile_dir}")
                options.add_argument("--window-size=1280,900")
                options.add_argument("--disable-infobars")
                options.add_argument("--hide-crash-restore-bubble")
                options.add_argument("--no-first-run")
                options.add_argument("--disable-default-apps")
                
                # CRITICAL for Win32 Native Embedding (Prevents "Black Screen" occlusion bugs)
                options.add_argument("--disable-features=CalculateNativeWinOcclusion")
                options.add_argument("--disable-gpu")  # Fallback for Windows hardware composite tearing

                # Added use_subprocess for stability on Windows
                local_chrome_dir = os.path.join(base_dir, "local_browser")
                local_chrome_exe = os.path.join(local_chrome_dir, "chrome.exe")
                
                # If local browser doesn't exist, try to copy it from the system
                if not os.path.exists(local_chrome_exe):
                    logging.info("Installing local Chrome copy...")
                    import shutil
                    # Common Windows Chrome paths
                    system_paths = [
                        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application"),
                        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application"),
                        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application")
                    ]
                    src_dir = None
                    for p in system_paths:
                        if os.path.exists(os.path.join(p, "chrome.exe")):
                            src_dir = p
                            break
                    
                    if src_dir:
                        os.makedirs(local_chrome_dir, exist_ok=True)
                        logging.info(f"Copying Chrome from {src_dir} to {local_chrome_dir} (This may take a moment)...")
                        # We use powershell to copy the directory cleanly
                        os.system(f'powershell -Command "Copy-Item -Path \'{src_dir}\\*\' -Destination \'{local_chrome_dir}\' -Recurse -Force"')
                        logging.info("✅ Local Chrome copy complete.")
                    else:
                        logging.warning("⚠️ Could not find system Chrome to copy. Will attempt default fallback.")

                if os.path.exists(local_chrome_exe):
                    uc_driver = uc.Chrome(
                        options=options, 
                        use_subprocess=True, 
                        headless=False, 
                        version_main=146,
                        browser_executable_path=local_chrome_exe
                    )
                else:
                    # Fallback if copy failed
                    uc_driver = uc.Chrome(options=options, use_subprocess=True, headless=False, version_main=146)
                uc_driver.get("https://www.google.com")
                
                # Register the Chrome PID for win32_embedder
                try:
                    from win32_embedder import set_chrome_pid
                    set_chrome_pid(uc_driver.service.process.pid)
                    logging.info(f"✅ PID {uc_driver.service.process.pid} registered.")
                except Exception as e:
                    logging.warning(f"Could not register PID: {e}")
                
                logging.info("✅ Browser Window Ready.")
                break
            except Exception as e:
                logging.warning(f"⚠️ Boot attempt {attempt+1} failed: {e}. Retrying…")
                time.sleep(3)
                uc_driver = None
    return uc_driver

async def get_stateful_browser():
    global _global_browser, uc_driver
    if _global_browser is None:
        logging.info("🔍 Initializing stateful browser bridge...")
        await asyncio.to_thread(spawn_uc)
        if uc_driver is None:
            raise RuntimeError("CRITICAL: Failed to spawn browser window.")

        debugger_address = uc_driver.capabilities.get("goog:chromeOptions", {}).get("debuggerAddress")
        cdp_url = f"http://{debugger_address}"
        logging.info(f"🔍 Checking CDP Socket: {cdp_url}")
        
        # Increased attempts for slow patches
        for attempt in range(20):
            try:
                r = requests.get(f"{cdp_url}/json/version", timeout=2)
                if r.status_code == 200:
                    logging.info("✅ CDP Socket reached.")
                    break
            except Exception:
                pass
            if attempt % 5 == 0: logging.info(f"   (Waiting for socket... {attempt}/20)")
            await asyncio.sleep(1)
        else:
            raise RuntimeError(f"CDP Socket Timeout: {cdp_url}")

        _global_browser = Browser(cdp_url=cdp_url, keep_alive=True)
        try:
            await _global_browser.start()
            logging.info("✅ Playwright bridge active.")
        except Exception as e:
            logging.error(f"Playwright start error: {e}")
            _global_browser = None
            raise

        if _global_browser.agent_focus_target_id is None:
            try:
                for t in requests.get(f"{cdp_url}/json/list", timeout=3).json():
                    if t.get("type") == "page":
                        _global_browser.agent_focus_target_id = t["id"]
                        logging.info(f"✅ Context Target synced: {t['id']}")
                        break
            except Exception as e:
                logging.error(f"CDP Sync Failure: {e}")

        await asyncio.sleep(2)

    return _global_browser


async def _inject_credentials_direct(domain_hint: str = "") -> bool:
    """
    Directly injects credentials into the current browser page using raw Playwright .fill().
    No LLM involved — the LLM cannot redact or interfere with this path.
    Returns True if credentials were found and injected.
    """
    global _global_browser
    if _global_browser is None:
        return False

    try:
        from database import local_db
        page = await _global_browser.get_current_page()
        if page is None:
            return False

        # Determine the best domain to look up
        current_url = page.url or ""
        import re
        m = re.search(r'https?://(?:www\.)?([a-zA-Z0-9\-]+\.[a-zA-Z]{2,})', current_url)
        domain = m.group(1).lower() if m else domain_hint.lower()

        # Check if there's a login form on screen (look for password input)
        pw_input = await page.query_selector("input[type='password']")
        if pw_input is None:
            return False  # Not a login page

        # Look up credentials for this domain
        cred = local_db.get_credential(domain)
        if not cred:
            # Fallback: try without subdomain, e.g., "www.instagram.com" -> "instagram.com"
            parts = domain.split(".")
            if len(parts) > 2:
                cred = local_db.get_credential(".".join(parts[-2:]))

        if not cred:
            logging.warning(f"🔑 No credentials stored for domain: {domain}")
            return False

        username, password = cred["username"], cred["password"]
        logging.info(f"✅ Direct Playwright injection for '{domain}' as '{username}'")

        # Find the username field — common selectors
        user_selectors = [
            "input[name='username']", "input[name='email']", "input[name='user']",
            "input[type='email']", "input[type='text']",
            "input[autocomplete='username']", "input[autocomplete='email']",
        ]
        user_input = None
        for sel in user_selectors:
            user_input = await page.query_selector(sel)
            if user_input:
                break

        if user_input:
            await user_input.click()
            await user_input.fill("")
            await page.keyboard.type(username, delay=50)
            logging.info(f"  ↳ Typed username into {sel}")

        await pw_input.click()
        await pw_input.fill("")
        await page.keyboard.type(password, delay=50)
        logging.info(f"  ↳ Typed password into password field")

        # Auto-submit login immediately to prevent LLM from hallucinating intermediate actions
        await page.keyboard.press("Enter")
        logging.info(f"  ↳ Pressed Enter to attempt auto-submit")
        
        # Robustly force-click physical log in button (Critical for React/Mobile apps)
        await page.evaluate('''() => {
            const btns = Array.from(document.querySelectorAll('button, div[role="button"], a[role="button"], input[type="submit"]'));
            const loginBtn = btns.find(b => {
                const t = b.innerText.toLowerCase().trim();
                return t === 'log in' || t === 'login' || t === 'sign in';
            });
            if (loginBtn) {
                loginBtn.click();
            }
        }''')
        logging.info(f"  ↳ Evaluated fallback JS click on 'Log In' button")
        
        # Wait for SPA transition unconditionally. 
        # (MutationObserver is too flaky here because the login button turns into a spinner, triggering early return)
        logging.info(f"  ↳ Waiting for visually reflected DOM changes (4 seconds)...")
        try:
            await page.wait_for_timeout(4000)
        except Exception as wait_e:
            logging.debug(f"DOM wait timeout: {wait_e}")

        return True

    except Exception as e:
        logging.error(f"Direct credential injection failed: {e}")
        return False


# ─── 2FA Detection & HITL Pause ──────────────────────────────────────────────

# Common selectors that indicate a 2FA / OTP / verification page
_2FA_SELECTORS = [
    "input[name*='otp']", "input[name*='code']", "input[name*='token']",
    "input[name*='verification']", "input[name*='2fa']", "input[name*='mfa']",
    "input[placeholder*='code' i]", "input[placeholder*='OTP' i]",
    "input[placeholder*='verification' i]", "input[maxlength='6']",
    "input[autocomplete='one-time-code']",
]

async def detect_and_handle_2fa() -> str | None:
    """
    Scans the current page for 2FA input fields.
    If found: pauses state to WAITING_2FA, signals frontend, waits for user code.
    Returns the OTP code entered by user, or None if no 2FA detected.
    """
    global _global_browser, AGENT_STATE, _2FA_CODE, _2FA_EVENT

    if _global_browser is None:
        return None
    try:
        page = await _global_browser.get_current_page()
        if page is None:
            return None

        otp_field = None
        for sel in _2FA_SELECTORS:
            otp_field = await page.query_selector(sel)
            if otp_field:
                logging.info(f"🔐 2FA field detected: {sel}")
                break

        if otp_field is None:
            return None  # No 2FA on this page

        # Transition to WAITING_2FA state — pauses main execution
        _2FA_EVENT.clear()
        _2FA_CODE = None
        _set_state(AgentState.WAITING_2FA,
                   "🔐 2FA required — waiting for user to enter OTP code…")

        # Wait up to 5 minutes for the user to paste the code
        try:
            await asyncio.wait_for(_2FA_EVENT.wait(), timeout=300)
        except asyncio.TimeoutError:
            _set_state(AgentState.IDLE)
            logging.warning("⏰ 2FA timed out — user did not submit code within 5 minutes.")
            return None

        code = _2FA_CODE
        _2FA_CODE = None
        _set_state(AgentState.RUNNING, f"✅ 2FA code received — continuing…")

        if code:
            # Type the OTP directly via Playwright
            await otp_field.click()
            await otp_field.fill("")
            await page.keyboard.type(code, delay=80)
            logging.info(f"  ↳ OTP '{code}' typed into 2FA field")

            # Press Enter to gently submit, avoiding aggressive button-click double-submits on SPAs
            await page.keyboard.press("Enter")
            logging.info(f"  ↳ Gently pressed Enter to submit 2FA")
            
            # Wait for visually reflected DOM change / auto-submits to settle
            try:
                await page.wait_for_timeout(4000)
            except Exception as wait_e:
                logging.debug(f"2FA DOM wait timeout: {wait_e}")

        return code

    except Exception as e:
        logging.error(f"2FA detection error: {e}")
        return None


async def fast_page_setup(page) -> None:
    """
    Applies performance-first Playwright page settings:
    - domcontentloaded wait strategy (vs networkidle)
    - Reduced timeouts
    - Analytics + Tracker blocking
    - Console/Network signaling
    """
    try:
        # Use domcontentloaded — don't wait for all images/fonts/3rd-party scripts
        page.set_default_navigation_timeout(20_000)   # 20s max nav
        page.set_default_timeout(10_000)               # 10s for selectors

        # Attach high-speed monitors
        page.on("console", BrowserMonitor.on_console)
        page.on("response", BrowserMonitor.on_response)

        # Intercept slow 3rd-party tracking/analytics - block them for speed
        BLOCK_PATTERNS = [
            "*.google-analytics.com*", "*.googletagmanager.com*", "*.googleadservices.com*",
            "*.doubleclick.net*", "*.facebook.net/tr*", "*.fbcdn.net*", "*.hotjar.com*",
            "*.intercom.com*", "*.segment.io*", "*.mixpanel.com*", "*.amplitude.com*",
            "*.clarity.ms*", "*.scorecardresearch.com*",
        ]
        await page.route("**/*", lambda route: (
            route.abort() if any(
                p.strip("*") in route.request.url.lower()
                for p in BLOCK_PATTERNS
            ) else route.continue_()
        ))
        logging.info("⚡ Fast-page mode active: trackers blocked, monitoring console/network signals.")
    except Exception as e:
        logging.warning(f"fast_page_setup skipped: {e}")



LATEST_SCREENSHOT_B64: str = ""   # latest PNG as base64, served to WebSocket clients
_SCREENSHOT_CLIENTS: set = set()  # connected WebSocket clients

async def _screenshot_stream_loop():
    """Continuously stream browser frames to React using high-speed Playwright polling."""
    global LATEST_SCREENSHOT_B64, _SCREENSHOT_CLIENTS
    _frame_count = 0

    logging.info("📸 High-speed Playwright frame loop started.")

    async def broadcast_loop():
        """Dedicated high-speed task to push frames to WebSockets without locking up the capture."""
        global LATEST_SCREENSHOT_B64, _SCREENSHOT_CLIENTS
        last_b64 = None
        while True:
            b64 = LATEST_SCREENSHOT_B64
            if b64 and b64 != last_b64:
                last_b64 = b64
                dead = set()
                for ws in list(_SCREENSHOT_CLIENTS):
                    try:
                        await ws.send_text(b64)
                    except Exception as e:
                        dead.add(ws)
                if dead:
                    _SCREENSHOT_CLIENTS.difference_update(dead)
            # Push at max ~40 FPS to prevent starving the ASGI event loop
            await asyncio.sleep(0.025)

    # Launch the decoupled WebSocket broadcaster
    asyncio.create_task(broadcast_loop())

    # Capture loop using reliable, navigation-safe page.screenshot
    while True:
        try:
            b64 = None
            import base64
            
            # Primary: Playwright CDP capture (Handles cross-origin navigations seamlessly)
            if _global_browser is not None:
                try:
                    page = await _global_browser.get_current_page()
                    if page:
                        # Use JPEG for ~60% lower latency/bandwidth
                        img_bytes = await page.screenshot(type="jpeg", quality=40, full_page=False, timeout=3000)
                        b64 = base64.b64encode(img_bytes).decode('ascii')
                except Exception as e:
                    # Ignore normal page timeouts during navigation loads
                    b64 = None

            # Fallback: UC/Selenium screenshot
            if not b64 and uc_driver is not None:
                try:
                    b64 = await asyncio.to_thread(uc_driver.get_screenshot_as_base64)
                    if b64:
                        b64 = b64.strip().replace("\n", "").replace("\r", "")
                except Exception as e:
                    logging.debug(f"UC screenshot fallback failed: {e}")
                    b64 = None

            if b64:
                LATEST_SCREENSHOT_B64 = b64
                _frame_count += 1
                if _frame_count == 1:
                    logging.info("✅ First frame captured — stream is live.")
                elif _frame_count % 30 == 0:
                    logging.info(f"📸 Frame {_frame_count}: len={len(b64)}, prefix=/9j/{b64[4:20]}")

        except Exception as e:
            logging.error(f"Screenshot watcher loop error: {e}")

        # Polling rate: 0.04s = ~25 FPS
        await asyncio.sleep(0.04)


# ─── Main Execution with Quota Retry Logic ───────────────────────────────
async def execute_web_automation(prompt: str) -> str:
    global AGENT_STATE, PAUSE_CONTEXT, RESUME_CONTEXT, _running_agent_task

    # ── Auto-Resume context injection ─────────────────────────────────────
    if AGENT_STATE == AgentState.PAUSED:
        ctx = PAUSE_CONTEXT
        if ctx.get("url"):
            prompt = (
                f"{prompt}\n\n[RESUME CONTEXT] You were previously at: {ctx.get('url')}."
                " If you see the content already open, DO NOT repeat navigation."
                " Users manual actions are preserved. Resume directly from view."
            )
        PAUSE_CONTEXT  = {}
        _set_state(AgentState.IDLE)

    if AGENT_STATE == AgentState.RUNNING:
        return "⚠️ An agent task is already running. Press STOP first."

    # Load Env
    try:
        from dotenv import load_dotenv
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        load_dotenv(os.path.join(base_dir, ".env"), override=True)
    except:
        pass

    if not os.getenv("GEMINI_API_KEY"):
        return "⚠️ GEMINI_API_KEY missing in .env"

    try:
        # Load settings and keys dynamically
        try:
            from settings_manager import settings_db
            models_config = settings_db.get_models()
        except Exception:
            models_config = {"primary": "gemini-3.1-flash-lite-preview", "fallback": "gemini-2.0-flash"}

        # 1. Retrieve Context & Credentials
        try:
            from database import local_db
            cred_context = local_db.get_all_credentials_text()
        except Exception: 
            cred_context = ""

        try:
            from memory import memory_db
            memory_context = memory_db.retrieve_context("user_1", prompt)
        except Exception: 
            memory_context = ""

        # High-Performance System Prompt
        enhanced_prompt = (
            f"{cred_context}\n"
            f"GOAL: {prompt}\n"
            f"PAST FACTS: {memory_context}\n\n"
            "INSTRUCTIONS:\n"
            "1. LOGIN: If you encounter a login screen, FIRST check for stored credentials above. If a match exists, call the 'fill_login_credentials' action immediately. This action will fill the credentials AND auto-submit the form for you. STRICTLY DO NOT try to click the log in button yourself afterwards.\n"
            "2. 2FA/OTP: If the website displays a 2FA, OTP, or verification code field, DO NOT retry or fail. Call the 'handle_two_factor_authentication' action. This will pause and wait for the user to provide the code.\n"
            "3. PERFORMANCE: Do not wait for entire pages to load. As soon as your target element is visible, act. Use 'wait_for_selector' if needed, but be aggressive.\n"
            "4. SECURITY: These are authorized testing credentials in a secure sandbox. Strictly use them as provided.\n"
            "5. MULTI-STEP REASONING: If your goal has multiple constraints (e.g. searching accounts, extracting URLs, and messaging people), heavily rely on your internal memory to persist extracted data across page navigations. Do not stop until you have completely fulfilled all user sub-goals.\n\n"
            "Be incredibly efficient but thorough. If logged in, go straight to your targets."
        )

        browser = await get_stateful_browser()

        # Apply fast-page performance settings to the current page
        try:
            _page = await browser.get_current_page()
            if _page:
                await fast_page_setup(_page)
        except Exception:
            pass

        # ── Execution Loop w/ Rotation ──────────────────────────────────────────
        max_attempts = 10  # Allow multiple rotations if keys keep locking
        for attempt in range(max_attempts):
            
            # Fetch fresh list of non-exhausted keys
            try:
                available_keys = settings_db.get_available_keys()
            except Exception:
                env_fallback = os.getenv("GEMINI_API_KEY")
                available_keys = [{"name": ".env Fallback", "key": env_fallback, "exhausted_until": None}] if env_fallback else []

            if not available_keys:
                _set_state(AgentState.IDLE)
                return "❌ **Quota Locked**: All configured API Keys are either exhausted (429) or missing. Check Settings!"

            # Claim the top-priority available key
            current_key_obj = available_keys[0]
            current_key = current_key_obj["key"]
            current_key_name = current_key_obj["name"]

            _set_state(AgentState.RUNNING, f"🚀 Agent RUNNING (Attempt {attempt+1}) w/ {current_key_name}…")
            
            global _current_model, _current_key_name
            _current_model    = models_config["primary"]
            _current_key_name = current_key_name
            
            # Dynamically initialize the LLMs based on the exact key
            llm = ChatGoogle(model=models_config["primary"], api_key=current_key, max_output_tokens=8192)
            fallback_llm = ChatGoogle(model=models_config["fallback"], api_key=current_key, max_output_tokens=4096)

            # ── Register Custom Actions ─────────────────────────────────────────
            try:
                from browser_use import Controller
                from pydantic import BaseModel as BUBaseModel

                class FillLoginParams(BUBaseModel):
                    domain_hint: str = ""

                class TwoFAParams(BUBaseModel):
                    pass  # No params needed — reads from page state

                controller = Controller()

                @controller.action("Fill in the login form with stored credentials for the current site", param_model=FillLoginParams)
                async def fill_login_credentials(params: FillLoginParams):
                    success = await _inject_credentials_direct(domain_hint=params.domain_hint)
                    if success:
                        return "✅ Credentials injected and form auto-submitted. IMPORTANT: DO NOT execute any javascript or click any log in buttons yourself. Wait to see if 2FA is needed."
                    return "⚠️ No matching credentials found in vault for this domain."

                @controller.action("Handle two-factor authentication or OTP verification code required by the website", param_model=TwoFAParams)
                async def handle_two_factor_authentication(params: TwoFAParams):
                    code = await detect_and_handle_2fa()
                    if code:
                        return f"✅ 2FA code '{code}' entered and submitted successfully."
                    return "⚠️ No 2FA field detected or timed out waiting for user code."

                agent = Agent(
                    task=enhanced_prompt,
                    llm=llm,
                    browser=browser,
                    fallback_llm=fallback_llm,
                    controller=controller
                )
                
                # Monkey-patch step to support HITL Pausing natively
                original_step = agent.step
                async def _paused_step(*args, **kwargs):
                    while AGENT_STATE == AgentState.PAUSED:
                        await asyncio.sleep(0.5)
                    return await original_step(*args, **kwargs)
                agent.step = _paused_step
            except Exception as ctrl_err:
                logging.warning(f"Controller registration failed: {ctrl_err}")
                agent = Agent(task=enhanced_prompt, llm=llm, browser=browser, fallback_llm=fallback_llm)
                
                original_step = agent.step
                async def _paused_step(*args, **kwargs):
                    while AGENT_STATE == AgentState.PAUSED:
                        await asyncio.sleep(0.5)
                    return await original_step(*args, **kwargs)
                agent.step = _paused_step

            global _global_agent_instance
            _global_agent_instance = agent

            _running_agent_task = asyncio.create_task(agent.run())

            try:
                history = await _running_agent_task
                
                # Precise Result Parsing
                final_thought = "Task processed."
                if hasattr(history, 'history') and history.history:
                    last_step = history.history[-1]
                    if hasattr(last_step, 'result') and last_step.result:
                        final_thought = re.sub(r'ActionResult\(.*?\)', '', str(last_step.result)).strip()
                        if not final_thought: final_thought = "Action completed."

                history_str = str(history)
                if "429" in history_str or "RESOURCE_EXHAUSTED" in history_str:
                    raise Exception("QUOTA_EXHAUSTED_429")

                _set_state(AgentState.IDLE, "✅ Task Success.")
                _global_agent_instance = None
                
                try:
                    from memory import memory_db
                    memory_db.add_memory("user_1", f"Goal: {prompt[:50]} | Result: {final_thought[:100]}", status="success")
                except Exception: pass

                return f"✅ **Goal Reached**: {final_thought}\n\nBrowser state preserved."

            except asyncio.CancelledError:
                _set_state(AgentState.IDLE, "🛑 Stopped.")
                _global_agent_instance = None
                return "🛑 Task Stopped."

            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "QUOTA" in err_msg or "QUOTA_EXHAUSTED" in err_msg:
                    # ✅ Intelligent Key Rotation with 12-hour timeout locks
                    wait_seconds = 43200  # Default 12-hour timeout
                    match = re.search(r"retryDelay': '(\d+)s'", err_msg)
                    if match: 
                        wait_seconds = int(match.group(1))

                    logging.warning(f"🔄 Account Quota Hit! Locking '{current_key_name}' for {wait_seconds}s...")
                    _set_state(AgentState.RUNNING, f"🔄 Quota reached on '{current_key_name}'. Locking for {wait_seconds}s and routing…")
                    
                    try:
                        settings_db.mark_exhausted(current_key, wait_seconds)
                    except Exception as e_mark:
                        logging.error(f"Failed to lock exhausted key: {e_mark}")
                    
                    # Continue straight into the next loop to fetch the remaining available keys!
                    continue
                    
                else:
                    _set_state(AgentState.IDLE)
                    _global_agent_instance = None
                    try:
                        from memory import memory_db
                        memory_db.add_memory("user_1", f"Failed: {prompt[:50]} | Reason: {err_msg[:80]}", status="failed")
                    except Exception: pass
                    return f"❌ **Task Failed**: {err_msg[:120]}"

        return f"❌ Failed after {max_attempts} rotation attempts. All keys potentially exhausted."

    except Exception as e:
        _set_state(AgentState.IDLE)
        _global_agent_instance = None
        return f"❌ System Error: {str(e)}"
