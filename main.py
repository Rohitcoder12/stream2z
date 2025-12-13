import os
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from urllib.parse import urlparse

# Import Playwright (The Browser Engine)
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
TOKEN = os.environ.get("BOT_TOKEN") 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- WEB SERVER ---
async def health_check(request):
    return web.Response(text="Browser Bot Running")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server running on port {port}")

# --- HELPER ---
def get_video_id(url):
    parsed = urlparse(url)
    path_parts = parsed.path.split('/')
    for part in path_parts:
        if 8 < len(part) < 20 and part.isalnum():
            return part
    if parsed.fragment: return parsed.fragment
    return None

# --- BROWSER LOGIC ---
async def open_browser_and_capture(video_id, message):
    status_msg = await message.answer(f"🖥️ **Launching Chrome...**\nOpening ID: `{video_id}`")
    
    embed_url = f"https://streama2z.pro/e/{video_id}"
    screenshot_file = "screenshot.png"
    html_file = "page_source.html"

    try:
        async with async_playwright() as p:
            # Launch Chromium (Chrome Engine)
            # headless=True means it runs without a visible UI window (required for servers)
            browser = await p.chromium.launch(headless=True)
            
            # Create a context with a real User Agent
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            
            await status_msg.edit_text("⏳ **Loading Page...**\nWaiting for Cloudflare/Loading...")
            
            # Go to the URL
            try:
                # We refer from the news site to look legitimate
                await page.goto(embed_url, referer="https://smartkhabrinews.com/")
                
                # Wait 10 seconds to let any "Just a moment" checks finish
                await page.wait_for_timeout(10000) 
                
            except Exception as e:
                logger.error(f"Page Load Error: {e}")

            # TAKE SCREENSHOT
            await page.screenshot(path=screenshot_file, full_page=True)
            
            # GRAB HTML
            content = await page.content()
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(content)
            
            await browser.close()

            # --- SEND RESULTS TO USER ---
            await status_msg.edit_text("📸 **Here is what the bot sees:**")
            
            # Send Screenshot
            if os.path.exists(screenshot_file):
                photo = FSInputFile(screenshot_file)
                await message.answer_photo(photo, caption=f"🖼️ **Screenshot of {embed_url}**")
            
            # Send HTML Source (as a document)
            if os.path.exists(html_file):
                doc = FSInputFile(html_file)
                await message.answer_document(doc, caption="📄 **HTML Source Code**")

    except Exception as e:
        await status_msg.edit_text(f"❌ **Browser Error:** {e}")
        logger.error(f"Browser Crash: {e}")
    finally:
        # Cleanup
        if os.path.exists(screenshot_file): os.remove(screenshot_file)
        if os.path.exists(html_file): os.remove(html_file)

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("🖥️ **Real Browser Mode**\nSend the link. I will open it in Chrome and send you a screenshot.")

@dp.message(F.text)
async def handle_url(message: types.Message):
    if "http" in message.text:
        video_id = get_video_id(message.text.strip())
        if video_id:
            await open_browser_and_capture(video_id, message)
        else:
            await message.answer("❌ No ID found.")

# --- MAIN ---
async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass