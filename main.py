import os
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from urllib.parse import urlparse
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
async def capture_screenshot(video_id, message):
    status_msg = await message.answer(f"📸 **Opening Browser...**\nTarget ID: `{video_id}`")
    
    embed_url = f"https://streama2z.pro/e/{video_id}"
    screenshot_file = f"snap_{video_id}.png"

    try:
        async with async_playwright() as p:
            # Launch Browser (Headless)
            browser = await p.chromium.launch(headless=True)
            
            # Mimic Real Device
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 720}
            )
            
            page = await context.new_page()
            
            await status_msg.edit_text("⏳ **Navigating to page...**")
            
            try:
                # Set Referer to look real
                await page.goto(embed_url, referer="https://smartkhabrinews.com/")
                
                # Wait 8 seconds for Cloudflare/Loading
                await page.wait_for_timeout(8000)
                
            except Exception as e:
                logger.error(f"Nav Error: {e}")

            # Take Screenshot
            await page.screenshot(path=screenshot_file, full_page=True)
            await browser.close()

            # Send to User
            await status_msg.edit_text("📤 **Sending Screenshot...**")
            photo = FSInputFile(screenshot_file)
            await message.answer_photo(photo, caption=f"🖼️ **View of {embed_url}**")

    except Exception as e:
        await status_msg.edit_text(f"❌ **Error:** {e}")
    finally:
        if os.path.exists(screenshot_file):
            os.remove(screenshot_file)

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("📸 **Screenshot Mode**\nSend the link to see what the bot sees.")

@dp.message(F.text)
async def handle_url(message: types.Message):
    if "http" in message.text:
        video_id = get_video_id(message.text.strip())
        if video_id:
            await capture_screenshot(video_id, message)
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