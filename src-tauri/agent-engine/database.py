import sqlite3
import os
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, ".agent_profile", "nexus_local.db")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nexus-db")

class LocalDatabase:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._init_schema()

    def _init_schema(self):
        # Create Credentials Vault
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                username TEXT,
                password TEXT
            )
        """)
        self.conn.commit()

    def save_credential(self, domain: str, username: str, password: str = ""):
        """Upsert a credential by domain + username."""
        self.cursor.execute("SELECT id FROM credentials WHERE domain=? AND username=?", (domain, username))
        existing = self.cursor.fetchone()
        
        if existing:
            self.cursor.execute("UPDATE credentials SET password=? WHERE id=?", (password, existing[0]))
            logger.info(f"Updated existing credential for '{domain}' ({username})")
        else:
            self.cursor.execute("INSERT INTO credentials (domain, username, password) VALUES (?, ?, ?)", 
                                (domain, username, password))
            logger.info(f"Saved new credential for '{domain}' ({username})")
        
        self.conn.commit()

    def get_credential(self, domain: str) -> dict | None:
        """Return username + password for a given domain, or None if not found."""
        self.cursor.execute(
            "SELECT username, password FROM credentials WHERE lower(domain)=lower(?)",
            (domain,)
        )
        row = self.cursor.fetchone()
        if row:
            return {"username": row[0], "password": row[1]}
        return None

    def list_credentials(self) -> list[dict]:
        """Return all stored credentials (for UI display — passwords masked)."""
        self.cursor.execute("SELECT id, domain, username FROM credentials ORDER BY domain")
        rows = self.cursor.fetchall()
        return [{"id": r[0], "domain": r[1], "username": r[2]} for r in rows]

    def delete_credential(self, credential_id: int):
        """Delete a stored credential entry by id."""
        self.cursor.execute("DELETE FROM credentials WHERE id=?", (credential_id,))
        self.conn.commit()
        logger.info(f"Deleted credential id={credential_id}")

    def get_all_credentials_text(self) -> str:
        """Returns a string formatted list of all stored credentials for LLM context injection."""
        self.cursor.execute("SELECT domain, username, password FROM credentials")
        rows = self.cursor.fetchall()
        
        if not rows:
            return ""
            
        context = "AVAILABLE SECURE CREDENTIALS:\n"
        for row in rows:
            context += f"- Domain: {row[0]} | User: {row[1]} | Pass: {row[2]}\n"
        return context

# Singleton instance
local_db = LocalDatabase()
