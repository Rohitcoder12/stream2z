import os
import re
import asyncio
import logging
import aiohttp
import aiofiles
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from urllib.parse import urlparse

# --- CONFIGURATION ---
TOKEN = os.environ.get("BOT_TOKEN") 

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Bot
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- WEB SERVER (Keep-Alive) ---
async def health_check(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server running on port {port}")

# --- HELPER: Extract ID ---
def get_video_id(url):
    """
    Extracts ID 'ds6yd9drzux3' from:
    https://streama2z.pro/ds6yd9drzux3/0_1000073663.mp4
    """
    parsed = urlparse(url)
    path_parts = parsed.path.split('/')
    
    # Logic: Find the part that looks like an ID (alphanumeric, 12 chars)
    for part in path_parts:
        if len(part) == 12 and part.isalnum():
            return part
            
    # Fallback: Check if fragment exists
    if parsed.fragment:
        return parsed.fragment
        
    return None

# --- DOWNLOAD ENGINE ---
async def process_video(url, message):
    video_id = get_video_id(url)
    
    if not video_id:
        await message.answer("❌ **Error:** Could not detect a valid Video ID in that link.")
        return

    status_msg = await message.answer(f"🚀 **Direct Mode Active**\nID: `{video_id}`\nBypassing Embed Page...")

    # THE TRICK:
    # We set the 'Referer' to the embed page, but we request the FILE directly.
    # This bypasses the page scrape (which is returning 403).
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://streama2z.pro/e/{video_id}", # Essential for download permission
        "Accept": "*/*"
    }
    
    local_filename = f"{video_id}.mp4"
    download_success = False

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            
            # 1. Attempt Direct Download of the user's link
            download_url = url
            
            # If user didn't send an MP4 link, we forced to scrape (might fail)
            if not url.endswith(".mp4"):
                 await status_msg.edit_text("⚠️ **Note:** Not a direct MP4 link. Trying to scrape (might fail due to IP block)...")
                 # Scrape logic would go here, but for your specific case, we assume MP4 input
                 download_url = f"https://streama2z.pro/{video_id}/v.mp4" # Guess default path

            async with session.get(download_url) as resp:
                if resp.status == 200:
                    # Check size
                    try:
                        size = int(resp.headers.get('Content-Length', 0))
                        if size > 49 * 1024 * 1024:
                            await status_msg.edit_text(f"⚠️ **File > 50MB.**\n\n🔗 [Direct Link]({download_url})")
                            return
                    except:
                        pass

                    f = await aiofiles.open(local_filename, mode='wb')
                    await f.write(await resp.read())
                    await f.close()
                    download_success = True
                
                elif resp.status == 403:
                    await status_msg.edit_text(f"❌ **Direct Download 403.**\nThe link you sent is expired or IP-locked.\n\nTry sending the **News Article Link** instead.")
                    return
                else:
                    await status_msg.edit_text(f"❌ **Error:** Server returned {resp.status}")
                    return

        if download_success:
            await status_msg.edit_text("📤 **Uploading...**")
            video_file = FSInputFile(local_filename)
            await message.answer_video(video_file, caption=f"✅ **Downloaded**")
            await status_msg.delete()

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ **System Error:** {e}")
    finally:
        if os.path.exists(local_filename):
            os.remove(local_filename)

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("👋 Send the **Direct MP4 Link**.")

@dp.message(F.text)
async def handle_url(message: types.Message):
    if "http" in message.text:
        await process_video(message.text.strip(), message)
    else:
        await message.answer("❌ Invalid URL")

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