import sys
import os
import traceback
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "src-tauri", "agent-engine")))
from agents.browser_agent import get_stateful_browser, execute_web_automation
import asyncio

async def test():
    try:
        browser = await get_stateful_browser()
        print("Browser spawned successfully:", browser)
    except Exception as e:
        traceback.print_exc()

asyncio.run(test())
