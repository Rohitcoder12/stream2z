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

# --- HELPER FUNCTIONS ---

def get_id_from_url(url):
    """Extracts the alphanumeric ID from the URL fragment or path."""
    parsed = urlparse(url)
    # Check fragment (e.g., #a67dnohmamur)
    if parsed.fragment:
        return parsed.fragment
    # Check path (e.g., /e/a67dnohmamur)
    parts = parsed.path.split('/')
    for part in parts:
        if len(part) > 8 and part.isalnum():
            return part
    return None

async def download_file(url, headers, filename="temp_video.mp4"):
    """Downloads the file locally to bypass 403 errors."""
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return False
                
                # Check file size (Limit to ~50MB for Bot API)
                size = int(resp.headers.get('Content-Length', 0))
                if size > 49 * 1024 * 1024: # 49 MB limit safety
                    return "TOO_BIG"

                f = await aiofiles.open(filename, mode='wb')
                await f.write(await resp.read())
                await f.close()
        return True
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return False

# --- CORE LOGIC ---

async def process_video(url, message):
    status_msg = await message.answer("🔎 **Analyzing link...**")
    
    # 1. Get Video ID
    video_id = get_id_from_url(url)
    if not video_id:
        await status_msg.edit_text("❌ **Error:** Could not find a Video ID in the link.\nMake sure the link ends with `#id`.")
        return

    # 2. Construct Embed URL and Headers
    embed_url = f"https://streama2z.pro/e/{video_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://smartkhabrinews.com/",
        "Origin": "https://smartkhabrinews.com"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # 3. Fetch Embed Page to get real MP4 link
            async with session.get(embed_url) as resp:
                if resp.status != 200:
                    await status_msg.edit_text(f"❌ **Error:** Stream host returned {resp.status}.")
                    return
                html = await resp.text()

            # 4. Extract MP4 URL using Regex
            # Pattern matches: file: "https://...mp4"
            match = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.mp4)["\']', html)
            if not match:
                # Fallback pattern
                match = re.search(r'["\'](https?://[^"\']+\.mp4)["\']', html)
            
            if not match:
                await status_msg.edit_text("❌ **Error:** Could not extract video URL from player.")
                return
            
            mp4_url = match.group(1)
            logger.info(f"Found MP4: {mp4_url}")

            # 5. Update headers for the video file itself (Referer must be the embed URL)
            headers['Referer'] = embed_url
            
            await status_msg.edit_text("📥 **Downloading video to server...**\n(This bypasses the 403 error)")
            
            # 6. Download Locally
            local_filename = f"{video_id}.mp4"
            result = await download_file(mp4_url, headers, local_filename)

            if result == "TOO_BIG":
                await status_msg.edit_text(f"⚠️ **Video is too large (>50MB).**\nTelegram Bots cannot upload it.\n\n🔗 [Direct Link]({mp4_url})")
                return
            elif not result:
                await status_msg.edit_text("❌ **Download Failed.** The link might be expired.")
                return

            # 7. Upload to Telegram
            await status_msg.edit_text("📤 **Uploading to Telegram...**")
            
            video_file = FSInputFile(local_filename)
            await message.answer_video(video_file, caption="🎥 **Downloaded via Bot**")
            
            # Cleanup
            await status_msg.delete()
            if os.path.exists(local_filename):
                os.remove(local_filename)

    except Exception as e:
        logger.error(f"Critical error: {e}")
        await status_msg.edit_text("❌ **System Error:** " + str(e))
        if os.path.exists(f"{video_id}.mp4"):
            os.remove(f"{video_id}.mp4")

# --- BOT HANDLERS ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("👋 **Send me a SmartKhabri link!**\nMake sure it has the `#id` at the end.")

@dp.message(F.text)
async def handle_url(message: types.Message):
    url = message.text.strip()
    if "http" in url:
        await process_video(url, message)
    else:
        await message.answer("❌ Invalid URL.")

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