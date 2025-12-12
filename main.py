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
# TOKEN = "YOUR_TOKEN" # Uncomment for local testing

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Bot
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- WEB SERVER (Keep-Alive for Render) ---
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

# --- 1. INTELLIGENT ID EXTRACTION ---
def get_video_id(url):
    """
    Extracts ID 'hufu71926gty' from:
    - https://streama2z.pro/hufu71926gty/filename.mp4
    - https://streama2z.pro/e/hufu71926gty
    - https://smartkhabrinews.com/...#hufu71926gty
    """
    parsed = urlparse(url)
    path = parsed.path

    # Case 1: Fragment (SmartKhabri)
    if parsed.fragment:
        return parsed.fragment

    # Case 2: Embed URL (/e/ID)
    if "/e/" in path:
        parts = path.split("/e/")
        if len(parts) > 1:
            return parts[1].split('/')[0]

    # Case 3: Direct File URL (/ID/filename.mp4) <--- THIS FIXES YOUR LINK
    # Look for the pattern: /alphanumeric_string/something.mp4
    match = re.search(r'/([a-z0-9]{10,15})/', path)
    if match:
        return match.group(1)
        
    # Case 4: Fallback (Split by slash)
    parts = path.split('/')
    for part in parts:
        # IDs are usually 12 chars long alphanumeric
        if len(part) == 12 and part.isalnum():
            return part
            
    return None

# --- 2. DOWNLOAD ENGINE ---
async def process_download(video_id, message):
    status_msg = await message.answer(f"🔎 **ID:** `{video_id}`\nConnecting to StreamA2Z...")
    
    # We use ONE session for the whole process to keep cookies valid
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://smartkhabrinews.com/",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    embed_url = f"https://streama2z.pro/e/{video_id}"
    local_filename = f"{video_id}.mp4"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            
            # STEP A: Visit the Embed Page (Authorization)
            async with session.get(embed_url) as resp:
                if resp.status != 200:
                    await status_msg.edit_text(f"❌ **Error:** Embed page returned {resp.status}")
                    return
                html = await resp.text()

            # STEP B: Find the REAL video link inside the HTML
            # The regex looks for: file: "https://..."
            video_match = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.mp4)["\']', html)
            
            if not video_match:
                # Fallback: Look for any string ending in .mp4 inside quotes
                video_match = re.search(r'["\'](https?://[^"\']+\.mp4)["\']', html)

            if not video_match:
                await status_msg.edit_text("❌ **Error:** Could not scrape video link from player.")
                return
            
            real_mp4_url = video_match.group(1)
            logger.info(f"Resolved URL: {real_mp4_url}")

            # STEP C: Download the video
            # IMPORTANT: Change Referer to the Embed URL
            download_headers = headers.copy()
            download_headers['Referer'] = embed_url 
            
            await status_msg.edit_text("📥 **Downloading to server...**")
            
            async with session.get(real_mp4_url, headers=download_headers) as dl_resp:
                if dl_resp.status == 403:
                    await status_msg.edit_text("❌ **403 Forbidden:** The server blocked the bot IP.")
                    return
                elif dl_resp.status != 200:
                    await status_msg.edit_text(f"❌ **Download Error:** Status {dl_resp.status}")
                    return
                
                # Check File Size
                try:
                    size = int(dl_resp.headers.get('Content-Length', 0))
                    # 50 MB limit (Telegram restriction)
                    if size > 50 * 1024 * 1024:
                        await status_msg.edit_text(f"⚠️ **File too large (>50MB).**\n\n🔗 [Direct Link]({real_mp4_url})")
                        return
                except:
                    pass

                f = await aiofiles.open(local_filename, mode='wb')
                await f.write(await dl_resp.read())
                await f.close()

        # STEP D: Upload to Telegram
        await status_msg.edit_text("📤 **Uploading to Telegram...**")
        video = FSInputFile(local_filename)
        await message.answer_video(video, caption=f"✅ **Downloaded**\nID: `{video_id}`")
        await status_msg.delete()

    except Exception as e:
        logger.error(f"System Error: {e}")
        await status_msg.edit_text(f"❌ **Error:** {str(e)}")
    finally:
        # Cleanup file
        if os.path.exists(local_filename):
            os.remove(local_filename)

# --- BOT HANDLERS ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("👋 Send me a **StreamA2Z** link to download.")

@dp.message(F.text)
async def handle_url(message: types.Message):
    url = message.text.strip()
    
    if "streama2z" not in url and "smartkhabri" not in url:
        await message.answer("❌ Please send a valid StreamA2Z URL.")
        return

    video_id = get_video_id(url)
    
    if video_id:
        await process_download(video_id, message)
    else:
        await message.answer("❌ Could not extract Video ID from this link.")

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