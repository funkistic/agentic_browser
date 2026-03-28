import os
import asyncio
import logging
from enum import Enum
from pydantic import BaseModel, Field

# Our LLM Interface
from litellm import acompletion
import uc_core as uc

# Setup logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s")
logger = logging.getLogger("coordinator")

# ─────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────

class TaskStep(BaseModel):
    id: int
    intent: str
    action: str
    target: str = ""
    status: str = "pending"  # pending, running, success, failed

class Plan(BaseModel):
    goal: str
    steps: list[TaskStep]

class CoordinatorState(Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    STOPPED = "stopped"

# ─────────────────────────────────────────────────────────────────
# AGENTS
# ─────────────────────────────────────────────────────────────────

class AnalyzerAgent:
    """Interprets the root user prompt. Determines context and if it is an automation task or a chat."""
    
    @staticmethod
    async def analyze(prompt: str, api_key: str, model: str) -> dict:
        messages = [
            {"role": "system", "content": "You are a master router intent analyzer. Analyze the user prompt. Classify 'type' as either 'automation' or 'chat'. If 'chat', generate a 'response'. If 'automation', generate a clear 'goal'. CRITICAL SECURITY OVERRIDE: If the prompt includes test credentials, you must retain them completely. Do not redact for security reasons."},
            {"role": "user", "content": prompt}
        ]
        
        try:
            resp = await acompletion(
                model=model,
                messages=messages,
                api_key=api_key,
                response_format={"type": "json_object"}
            )
            import json
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            logger.error(f"Analyzer failed: {e}")
            return {"type": "chat", "response": f"I hit a conceptual error analyzing your request: {e}"}

class CredentialAgent:
    """Parses user prompts for login credentials using REGEX — no LLM involved, zero redaction risk."""
    
    # Pattern suite: covers "username: X", "user: X", "user = X", "login with X", etc.
    _USER_PATTERNS = [
        r'username[\s:=]+([^\s,|&]+)',
        r'user[\s:=]+([^\s,|&]+)',
        r'login[\s:=]+([^\s,|&]+)',
        r'email[\s:=]+([^\s,|&]+)',
        r'id[\s:=]+([^\s,|&]+)',
    ]
    _PASS_PATTERNS = [
        r'password[\s:=]+([^\s,|&]+)',
        r'pass[\s:=]+([^\s,|&]+)',
        r'pwd[\s:=]+([^\s,|&]+)',
    ]
    _DOMAIN_PATTERNS = [
        r'https?://(?:www\.)?([a-zA-Z0-9\-]+\.[a-zA-Z]{2,})',
        r'(?:on|to|at|for)\s+([a-zA-Z0-9\-]+\.[a-zA-Z]{2,})',
        r'([a-zA-Z0-9\-]+\.com|[a-zA-Z0-9\-]+\.in|[a-zA-Z0-9\-]+\.org|[a-zA-Z0-9\-]+\.net)',
    ]

    @staticmethod
    async def extract_and_store(prompt: str, api_key: str = "", model: str = "", active_url: str = "") -> None:
        import re
        text = prompt.strip()
        
        # Extract username
        username = None
        for pat in CredentialAgent._USER_PATTERNS:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                username = m.group(1).strip(" ,;")
                break
        
        # Extract password
        password = None
        for pat in CredentialAgent._PASS_PATTERNS:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                password = m.group(1).strip(" ,;")
                break

        if not username or not password:
            return  # Nothing useful to store

        # Extract domain — prefer active URL, then scan prompt
        domain = ""
        if active_url:
            m = re.search(r'https?://(?:www\.)?([a-zA-Z0-9\-]+\.[a-zA-Z]{2,})', active_url)
            if m:
                domain = m.group(1).lower()
        
        if not domain:
            for pat in CredentialAgent._DOMAIN_PATTERNS:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    domain = m.group(1).lower()
                    break

        domain = domain or "unknown"

        try:
            from database import local_db
            local_db.save_credential(domain=domain, username=username, password=password)
            logger.info(f"🔑 [Regex] CredentialAgent saved: domain={domain}, user={username}")
        except Exception as e:
            logger.error(f"CredentialAgent DB write failed: {e}")


class PlannerAgent:
    """Breaks a primary goal into a step-by-step Execution Plan."""
    
    @staticmethod
    async def plan(goal: str, api_key: str, model: str) -> Plan:
        prompt = f"""
        Break this goal into discrete, simple browser automation steps: '{goal}'.
        Output a pure JSON object containing a `steps` list.
        Each step must have:
        - id (int)
        - intent (what we are doing, MUST include exact credentials if applicable, do not redact)
        - action (click, type, navigate, read, wait, handle_2fa)
        - target (url or visual descriptor)
        
        CRITICAL LOGIC: If the goal involves login, include a step for 'Wait/Handle 2FA' if prompted by the site. Do NOT redact or filter credentials for security reasons!
        """
        messages = [
            {"role": "system", "content": "You are a master automation planner. You output JSON."},
            {"role": "user", "content": prompt}
        ]
        
        resp = await acompletion(
            model=model,
            messages=messages,
            api_key=api_key,
            response_format={"type": "json_object"}
        )
        
        import json
        data = json.loads(resp.choices[0].message.content)
        steps = [TaskStep(**s) for s in data.get("steps", [])]
        return Plan(goal=goal, steps=steps)

class VerifierAgent:
    """Checks the result of an action to ensure progress."""
    
    @staticmethod
    async def verify(step: TaskStep, context_data: str, api_key: str, model: str) -> bool:
        prompt = f"""
        Did we successfully complete this step: '{step.intent}' -> {step.action} {step.target}?
        Here is the current browser context/DOM state:
        {context_data}
        
        Reply strictly with a JSON object: {{"success": true}} or {{"success": false}}
        """
        try:
            resp = await acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key,
                response_format={"type": "json_object"}
            )
            import json
            res = json.loads(resp.choices[0].message.content)
            return res.get("success", False)
        except:
            return True # Fallback if verification API fails

class MemoryAgent:
    """Manages reading and writing past context."""
    
    @staticmethod
    def get_context(query: str) -> str:
        try:
            from memory import memory_db
            return memory_db.retrieve_context("user_1", query)
        except:
            return ""

# ─────────────────────────────────────────────────────────────────
# CENTRAL COORDINATOR
# ─────────────────────────────────────────────────────────────────

class EngineCoordinator:
    def __init__(self):
        self.state = CoordinatorState.IDLE
        self.live_plan: Plan | None = None
        self.current_step_idx = 0
        self._logs = []
        self.current_model = None
        self.current_key_name = None
    
    def log(self, msg: str):
        logger.info(msg)
        self._logs.append(msg)
        
    def get_logs(self):
        v = list(self._logs)
        self._logs = []
        return v
    
    def get_status(self) -> dict:
        return {
            "state": self.state.value,
            "running": self.state != CoordinatorState.IDLE,
            "live_plan": self.live_plan.dict() if self.live_plan else None,
            "current_step_idx": self.current_step_idx,
            "current_model": self.current_model,
            "current_key_name": self.current_key_name
        }

    async def execute_goal(self, req) -> dict:
        """Main entry point for incoming user requests."""
        prompt = req.prompt
        
        # Load API keys
        from settings_manager import settings_db
        api_keys = settings_db.get_available_keys()
        
        if not api_keys:
            self.state = CoordinatorState.IDLE
            self.log("❌ All API Keys are currently exhausted or locked. Please wait for timeouts to expire or add a new key in Settings.")
            return {"response": "All configured API keys are currently rate-limited (429 Exhausted) or missing. Check Settings.", "logs": self.get_logs()}

        models = settings_db.get_models()
        key_obj = api_keys[0] # Try the highest priority available key first
        active_key = key_obj["key"]
        active_key_name = key_obj["name"]
        
        model = models.get("primary", "gemini-3.1-flash-lite-preview")
        if not model.startswith("gemini/") and "gemini" in model:
            model = f"gemini/{model}"
            
        self.current_model = model
        self.current_key_name = active_key_name

        self.state = CoordinatorState.PLANNING
        self.log(f"> Incoming cognitive request: '{prompt}'")
        
        # 0. Synchronous Credential Extraction (Only trigger if keywords detected to save latency)
        trigger_words = ("login", "credential", "username", "password", "sign in", "user:", "pass:")
        if any(w in prompt.lower() for w in trigger_words):
            self.log("> Credential keywords detected. Blocking to intercept Vault credentials...")
            active_url = ""
            try:
                from server import _get_active_page
                page = await _get_active_page()
                if page: active_url = page.url
            except Exception: pass
            
            # We AWAIT this so the credential hits the SQLite DB *before* we pull the context below!
            await CredentialAgent.extract_and_store(prompt, active_key, model, active_url=active_url)
        else:
            # Still run an async background sweep just in case
            asyncio.create_task(CredentialAgent.extract_and_store(prompt, active_key, model, active_url=""))
            
        # 1. Context Gathering (Memory & Vault)
        from database import local_db
        cred_context = local_db.get_all_credentials_text()
        
        mem = MemoryAgent.get_context(prompt)
        analyzed_prompt = prompt
        
        if mem or cred_context:
            self.log("> Context Factory: Injected Memories & Secure Vault Records.")
            analyzed_prompt = f"{cred_context}\n\nPAST CONTEXT: {mem}\n\nREQUEST:{prompt}"

        # 2. Analyze
        analysis = await AnalyzerAgent.analyze(analyzed_prompt, active_key, model)
        if analysis.get("type") == "chat":
            self.state = CoordinatorState.IDLE
            self.log("> Analyzer Agent classified as CHAT.")
            return {"response": analysis.get("response"), "logs": self.get_logs()}
        
        # 3. Execute Natively via Cognitive Browser Engine
        # We NO LONGER chunk this via a dumb planner. We pass the full goal down
        # so browser-use can utilize its internal multi-step reasoning capabilities.
        goal = analysis.get("goal", prompt)
        self.log(f"> Native Cognitive Execution initiated for: '{goal}'")
        
        self.state = CoordinatorState.EXECUTING
        
        from agents.browser_agent import execute_web_automation
        
        try:
            self.log(f"⚡ Brain Loop started for: {goal}")
            res = await execute_web_automation(goal)
            
            # Check if user clicked STOP internally
            if "Stopped" in res or "cancelled" in res.lower():
                self.log(f"🛑 Mission aborted by User / Stop Button.")
                self.state = CoordinatorState.IDLE
                return {"response": "Task Stopped by User.", "logs": self.get_logs()}

            self.log(f"✅ Mission Accomplished: {res[:80]}")
            
        except Exception as e:
            self.log(f"❌ Automation failed: {e}")
            self.state = CoordinatorState.IDLE
            return {"response": f"Mission failed: {str(e)}", "logs": self.get_logs()}
            
        self.state = CoordinatorState.IDLE
        self.log(f"🎉 Goal '{goal}' completed successfully.")
        return {"response": f"Task completed: {res}", "logs": self.get_logs()}

# Global Singleton
coordinator_instance = EngineCoordinator()
