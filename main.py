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

# --- HELPER: GET ID ---
def get_video_id(url):
    """Robustly extracts the ID from various link formats."""
    parsed = urlparse(url)
    
    # 1. Check Fragment (SmartKhabri #ID)
    if parsed.fragment:
        return parsed.fragment
    
    # 2. Check Path for Embed code (/e/ID)
    path_parts = parsed.path.split('/')
    if 'e' in path_parts:
        try:
            return path_parts[path_parts.index('e') + 1]
        except:
            pass

    # 3. Check Path for File ID (e.g. /ds6yd9drzux3/...)
    # We look for the part that is alphanumeric and length > 8
    for part in path_parts:
        if len(part) > 8 and re.match(r'^[a-zA-Z0-9]+$', part):
            return part
            
    return None

# --- CORE LOGIC: FRESH DOWNLOAD ---
async def process_and_download(video_id, message):
    status_msg = await message.answer(f"🔄 **Processing ID:** `{video_id}`\nGenerating fresh link...")
    
    # We use a single session for both getting the link and downloading
    # This ensures Cookies are preserved!
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0 Safari/537.36",
        "Referer": "https://smartkhabrinews.com/",
    }
    
    embed_url = f"https://streama2z.pro/e/{video_id}"
    local_filename = f"{video_id}.mp4"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            
            # STEP 1: Visit Embed Page to get Cookies and Fresh Link
            async with session.get(embed_url) as resp:
                if resp.status != 200:
                    await status_msg.edit_text("❌ **Error:** Video not found (Page 404/403).")
                    return
                html = await resp.text()

            # STEP 2: Extract Fresh MP4 Link
            # Pattern: file: "https://...."
            match = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.mp4)["\']', html)
            if not match:
                # Fallback Pattern
                match = re.search(r'["\'](https?://[^"\']+\.mp4)["\']', html)
            
            if not match:
                await status_msg.edit_text("❌ **Error:** Could not extract video URL from source.")
                return
            
            fresh_mp4_url = match.group(1)
            logger.info(f"Fresh URL: {fresh_mp4_url}")
            
            # STEP 3: Download using the SAME session (Important!)
            # We must update Referer to the Embed URL now
            download_headers = headers.copy()
            download_headers['Referer'] = embed_url
            
            await status_msg.edit_text("📥 **Downloading fresh video...**")
            
            async with session.get(fresh_mp4_url, headers=download_headers) as dl_resp:
                if dl_resp.status == 403:
                    await status_msg.edit_text("❌ **Forbidden (403):** IP Protection is active.")
                    return
                if dl_resp.status != 200:
                    await status_msg.edit_text(f"❌ **Error:** Download failed with status {dl_resp.status}")
                    return
                
                # Size Check
                try:
                    size = int(dl_resp.headers.get('Content-Length', 0))
                    if size > 49 * 1024 * 1024: # 49MB Safety Limit
                        await status_msg.edit_text(f"⚠️ **Video Too Large.**\n\n🔗 [Direct Link]({fresh_mp4_url})")
                        return
                except:
                    pass

                f = await aiofiles.open(local_filename, mode='wb')
                await f.write(await dl_resp.read())
                await f.close()

        # STEP 4: Upload to Telegram
        await status_msg.edit_text("📤 **Uploading to Telegram...**")
        video_file = FSInputFile(local_filename)
        await message.answer_video(video_file, caption=f"✅ **Downloaded**\nID: {video_id}")
        await status_msg.delete()

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ **System Error:** {str(e)}")
    finally:
        if os.path.exists(local_filename):
            os.remove(local_filename)

# --- BOT HANDLERS ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("👋 Send any **StreamA2Z** or **SmartKhabri** link.")

@dp.message(F.text)
async def handle_url(message: types.Message):
    url = message.text.strip()
    
    if "http" not in url:
        await message.answer("❌ Please send a valid link.")
        return

    video_id = get_video_id(url)
    
    if video_id:
        await process_and_download(video_id, message)
    else:
        await message.answer("❌ Could not find a Video ID in that link.")

# --- ENTRY POINT ---
async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass