import os
import json
import logging
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS_FILE = os.path.join(BASE_DIR, ".agent_profile", "settings.json")

DEFAULT_SETTINGS = {
    "primary_model": "gemini-3.1-flash-lite-preview",
    "fallback_model": "gemini-2.0-flash",
    "api_keys": []
}

class SettingsManager:
    def __init__(self):
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        self.settings = self._load()

    def _load(self):
        if not os.path.exists(SETTINGS_FILE):
            return DEFAULT_SETTINGS.copy()
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            settings = {**DEFAULT_SETTINGS, **data}
            
            # MIGRATION: Convert raw strings (legacy) into robust dictionary objects
            migrated_keys = []
            raw_keys = settings.get("api_keys", [])
            for i, k in enumerate(raw_keys):
                if isinstance(k, str):
                    migrated_keys.append({"name": f"API Key #{i+1}", "key": k, "exhausted_until": None})
                elif isinstance(k, dict):
                    if "exhausted_until" not in k: k["exhausted_until"] = None
                    if "name" not in k: k["name"] = f"API Key #{i+1}"
                    migrated_keys.append(k)
            
            settings["api_keys"] = migrated_keys
            return settings
            
        except Exception as e:
            logging.error(f"Error loading settings: {e}")
            return DEFAULT_SETTINGS.copy()

    def save(self, data: dict):
        self.settings.update(data)
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
            return True
        except Exception as e:
            logging.error(f"Error saving settings: {e}")
            return False

    def get_keys(self) -> list:
        """Returns the raw list of dictionary keys."""
        keys = self.settings.get("api_keys", [])
        if not keys:
            env_key = os.getenv("GEMINI_API_KEY")
            if env_key:
                keys = [{"name": "Default ENV Key", "key": env_key, "exhausted_until": None}]
        return keys

    def get_available_keys(self) -> list:
        """Filters out keys that are currently under a quota timeout lock."""
        keys = self.get_keys()
        now = datetime.utcnow()
        available = []
        needs_save = False

        for k in keys:
            exhausted = k.get("exhausted_until")
            if exhausted:
                try:
                    exhausted_time = datetime.fromisoformat(exhausted)
                    if now < exhausted_time:
                        continue  # Skip this key, it's still locked
                    else:
                        k["exhausted_until"] = None  # Lock expired
                        needs_save = True
                except Exception:
                    k["exhausted_until"] = None
                    needs_save = True
            available.append(k)

        if needs_save:
            self.save({"api_keys": keys})
        
        return available

    def mark_exhausted(self, target_key: str, seconds: int = 43200):
        """Locks an API key out of rotation. Default 12-hour timeout."""
        keys = self.settings.get("api_keys", [])
        for k in keys:
            if k.get("key") == target_key:
                until = datetime.utcnow() + timedelta(seconds=seconds)
                k["exhausted_until"] = until.isoformat()
                logging.warning(f"🔒 API Key '{k.get('name')}' marked as exhausted until {until.isoformat()}!")
                break
        self.save({"api_keys": keys})

    def get_models(self) -> dict:
        return {
            "primary": self.settings.get("primary_model", "gemini-3.1-flash-lite-preview"),
            "fallback": self.settings.get("fallback_model", "gemini-2.0-flash")
        }

settings_db = SettingsManager()
