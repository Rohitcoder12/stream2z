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

# --- INTELLIGENT ID EXTRACTION ---
def extract_file_info(url):
    """
    Analyzes the URL to find the Video ID and constructs the necessary
    Referer URL to bypass the 403 Forbidden error.
    """
    parsed = urlparse(url)
    video_id = None
    
    # Pattern 1: URL ends with #ID (SmartKhabri)
    if parsed.fragment:
        video_id = parsed.fragment

    # Pattern 2: URL is like .../e/ID (Embed)
    elif "/e/" in parsed.path:
        parts = parsed.path.split('/')
        if len(parts) > 2:
            video_id = parts[2]

    # Pattern 3: URL is direct MP4 like .../ID/filename.mp4
    # Example: streama2z.pro/ds6yd9drzux3/0_1000073663.mp4
    elif ".mp4" in parsed.path:
        parts = parsed.path.split('/')
        # The ID is usually the folder right before the filename
        if len(parts) >= 2:
            potential_id = parts[-2]
            if len(potential_id) > 5: # IDs are usually long
                video_id = potential_id

    if video_id:
        # The Secret Sauce: The Referer MUST be the embed page
        return video_id, f"https://streama2z.pro/e/{video_id}"
    
    return None, None

# --- DOWNLOAD LOGIC ---
async def download_video(mp4_url, referer_url, filename):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Referer": referer_url,
        "Accept": "*/*",
        "Connection": "keep-alive"
    }
    
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # First, visit the Referer URL (Embed Page) to set cookies/verification
            # This is often required before the MP4 link allows connection
            async with session.get(referer_url) as meta_resp:
                await meta_resp.read() # Just consume it to set cookies

            # Now download the file
            async with session.get(mp4_url) as resp:
                if resp.status == 403:
                    return "FORBIDDEN"
                if resp.status != 200:
                    return f"HTTP_{resp.status}"
                
                # Check size
                try:
                    size = int(resp.headers.get('Content-Length', 0))
                    # 50MB Limit (approx 52428800 bytes)
                    if size > 52428800: 
                        return "TOO_BIG"
                except:
                    pass

                f = await aiofiles.open(filename, mode='wb')
                await f.write(await resp.read())
                await f.close()
                return "SUCCESS"
    except Exception as e:
        logger.error(f"Download Error: {e}")
        return str(e)

async def scrape_mp4_from_embed(video_id, referer_url):
    """If user sends news link, we need to find the MP4 link first."""
    scrape_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0 Safari/537.36",
        "Referer": "https://smartkhabrinews.com/"
    }
    embed_url = f"https://streama2z.pro/e/{video_id}"
    
    async with aiohttp.ClientSession(headers=scrape_headers) as session:
        async with session.get(embed_url) as resp:
            if resp.status != 200: return None
            html = await resp.text()
            
            # Find MP4
            match = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.mp4)["\']', html)
            if match: return match.group(1)
            
            match = re.search(r'["\'](https?://[^"\']+\.mp4)["\']', html)
            if match: return match.group(1)
            
    return None

# --- BOT LOGIC ---
async def process_request(message, url):
    status_msg = await message.answer("🔄 **Processing...**")
    
    # 1. Analyze Link
    video_id, correct_referer = extract_file_info(url)
    
    if not video_id:
        await status_msg.edit_text("❌ Could not detect Video ID from link.")
        return

    mp4_url = url
    
    # 2. If it's NOT a direct MP4 link, we need to scrape the MP4 link first
    if not url.endswith(".mp4"):
        await status_msg.edit_text(f"🔎 **Found ID:** `{video_id}`\nScraping video link...")
        mp4_url = await scrape_mp4_from_embed(video_id, correct_referer)
        if not mp4_url:
            await status_msg.edit_text("❌ Could not find video source in the page.")
            return

    # 3. Download
    filename = f"vid_{message.from_user.id}_{video_id}.mp4"
    await status_msg.edit_text("📥 **Downloading...**\n(Bypassing 403 Protection)")
    
    result = await download_video(mp4_url, correct_referer, filename)
    
    if result == "SUCCESS":
        await status_msg.edit_text("📤 **Uploading to Telegram...**")
        try:
            video_file = FSInputFile(filename)
            await message.answer_video(video_file, caption=f"✅ **Downloaded!**\nID: {video_id}")
        except Exception as e:
            await message.answer(f"❌ **Upload Error:** {e}")
    elif result == "FORBIDDEN":
        await status_msg.edit_text("❌ **Access Denied (403).**\nThe token in this link might have expired.")
    elif result == "TOO_BIG":
        await status_msg.edit_text(f"⚠️ **Video too large (>50MB).**\n\n🔗 [Direct Link]({mp4_url})")
    else:
        await status_msg.edit_text(f"❌ **Error:** {result}")

    # Cleanup
    await status_msg.delete()
    if os.path.exists(filename):
        os.remove(filename)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("👋 Send a SmartKhabri link OR a StreamA2Z link.")

@dp.message(F.text)
async def handle_text(message: types.Message):
    if "http" in message.text:
        await process_request(message, message.text.strip())
    else:
        await message.answer("Please send a valid URL.")

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