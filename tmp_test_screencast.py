import asyncio
from playwright.async_api import async_playwright
import base64

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto("https://react.dev")
        
        # Inject CSS animation to force screen updates
        await page.evaluate("""
            const div = document.createElement('div');
            div.style.cssText = 'position:fixed;top:0;left:0;width:50px;height:50px;background:red;z-index:9999;animation:spin 1s linear infinite';
            const style = document.createElement('style');
            style.innerHTML = '@keyframes spin { 100% { transform: rotate(360deg); } }';
            document.head.appendChild(style);
            document.body.appendChild(div);
        """)
        
        cdp = await page.context.new_cdp_session(page)
        frames_received = 0
        
        async def handle_frame(event):
            nonlocal frames_received
            frames_received += 1
            if frames_received % 10 == 0:
                print(f"Received frame {frames_received}, size {len(event['data'])}")
            await cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

        cdp.on("Page.screencastFrame", handle_frame)
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 50, "everyNthFrame": 1})
        
        print("Screencast started. Waiting for 3 seconds...")
        await asyncio.sleep(3)
        print(f"Total frames received: {frames_received}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
