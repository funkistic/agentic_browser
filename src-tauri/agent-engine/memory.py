import logging
import json
import os

logging.basicConfig(level=logging.INFO)

class AgentMemoryStore:
    """
    JSON-backed persistent agentic memory.
    Stores historical context across sessions in the isolated .agent_profile directory.
    """
    def __init__(self):
        logging.info("Initialized Local JSON Memory Store")
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        profile_dir = os.path.join(base_dir, ".agent_profile")
        os.makedirs(profile_dir, exist_ok=True)
        self.memory_file = os.path.join(profile_dir, "memory.json")
        
        self.session_context = []
        self._load_memory()
        
    def _load_memory(self):
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r") as f:
                    self.session_context = json.load(f)
            except Exception as e:
                logging.error(f"Failed to load memory: {e}")
                self.session_context = []

    def _save_memory(self):
        try:
            with open(self.memory_file, "w") as f:
                json.dump(self.session_context, f, indent=4)
        except Exception as e:
            logging.error(f"Failed to save memory: {e}")

    def _sanitize(self, text: str) -> str:
        """Removes large JSON blobs, tracebacks, and ActionResult metadata."""
        # Strip internal browser-use object strings
        text = re.sub(r"ActionResult\(.*?\)", "[Result Object]", text)
        # Strip tracebacks or long error JSONs
        if len(text) > 300:
            text = text[:280] + "... [TRUNCATED]"
        return text

    def add_memory(self, session_id: str, fact: str, status: str = "success"):
        clean_fact = self._sanitize(fact)
        logging.info(f"Storing fact: {clean_fact} | Status: {status}")
        self.session_context.append({
            "session": session_id, 
            "fact": clean_fact, 
            "status": status,
            "timestamp": time.time()
        })
        if len(self.session_context) > 20: # Keep it lean
            self.session_context = self.session_context[-20:]
        self._save_memory()
        return True
        
    def retrieve_context(self, session_id: str, query: str) -> str:
        if not self.session_context:
            return ""
        
        # Return last 3 memories to keep prompt small
        relevant = self.session_context[-3:]
        context_parts = []
        for m in relevant:
            prefix = "✅ " if m.get("status") == "success" else "❌ "
            context_parts.append(f"{prefix}{m['fact']}")
            
        return "\n".join(context_parts)

import time
import re
# Singleton memory store
memory_db = AgentMemoryStore()
