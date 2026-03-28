import asyncio
import sys
sys.path.append('agent-engine')
from agents.browser_agent import execute_web_automation

async def main():
    print(await execute_web_automation("open instgra"))

if __name__ == "__main__":
    asyncio.run(main())
