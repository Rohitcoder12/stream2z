import os
import re
import asyncio
import logging
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import URLInputFile
from urllib.parse import urlparse

# --- CONFIGURATION ---
TOKEN = os.environ.get("BOT_TOKEN") 
# TOKEN = "YOUR_TOKEN_HERE" # For local testing

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Bot
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- WEB SERVER (REQUIRED FOR RENDER) ---
async def health_check(request):
    return web.Response(text="Bot is Active")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server running on port {port}")

# --- HELPER: EXTRACT VIDEO ID ---
def get_video_id_from_url(url):
    """
    Extracts the ID 'a67dnohmamur' from:
    1. https://smartkhabrinews.com/.../#a67dnohmamur
    2. https://streama2z.pro/e/a67dnohmamur
    """
    parsed = urlparse(url)
    
    # Check if ID is in the hash (anchor) #a67dnohmamur
    if parsed.fragment:
        return parsed.fragment
    
    # Check if ID is in the path for streama2z links
    path_parts = parsed.path.split('/')
    for part in path_parts:
        if len(part) > 10 and part.isalnum(): # IDs are usually alphanumeric long strings
            return part
            
    return None

# --- CORE LOGIC ---
async def extract_mp4(target_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://smartkhabrinews.com/", 
        "Origin": "https://smartkhabrinews.com"
    }

    # CASE 1: USER SENT A DIRECT MP4 LINK
    if target_url.endswith(".mp4"):
        return target_url, None

    async with aiohttp.ClientSession(headers=headers) as session:
        # CASE 2: USER SENT SMARTKHABRI LINK
        video_id = get_video_id_from_url(target_url)
        
        if not video_id:
            # Fallback: Try to scrape the page if no ID found in URL
            try:
                async with session.get(target_url) as resp:
                    text = await resp.text()
                    # Look for embed URL in page source
                    match = re.search(r'streama2z\.(?:pro|com)/e/([a-zA-Z0-9]+)', text)
                    if match:
                        video_id = match.group(1)
            except:
                pass

        if not video_id:
             return None, "Could not identify Video ID. Please ensure URL ends with #ID"

        # Construct the embed URL
        embed_url = f"https://streama2z.pro/e/{video_id}"
        logger.info(f"Scraping Embed URL: {embed_url}")

        # Update headers for the embed page
        headers['Referer'] = embed_url
        
        async with session.get(embed_url, headers=headers) as response:
            if response.status != 200:
                return None, f"Stream host returned error: {response.status}"
            
            html_content = await response.text()

        # Extract MP4 using Regex
        # 1. Look for 'file': 'url'
        # 2. Look for just any http link ending in .mp4
        patterns = [
            r'file\s*:\s*["\'](https?://[^"\']+\.mp4)["\']',
            r'["\'](https?://[^"\']+\.mp4)["\']'
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content)
            if match:
                return match.group(1), None

        return None, "Could not extract MP4 link. The video might be deleted."

# --- BOT HANDLERS ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("👋 Send me the **SmartKhabri** link (ending with `#id`) or the **StreamA2Z** link.")

@dp.message(F.text)
async def handle_request(message: types.Message):
    url = message.text.strip()
    status_msg = await message.answer("🔎 **Processing...**")

    try:
        mp4_url, error = await extract_mp4(url)

        if error:
            await status_msg.edit_text(f"❌ **Error:** {error}")
            return

        await status_msg.edit_text("✅ **Found!** Uploading...")

        # Create a custom input file that includes Referer headers for the download
        # Note: aiogram URLInputFile doesn't support custom headers easily, 
        # so if this fails, we send the link.
        try:
            await message.answer_video(
                video=URLInputFile(mp4_url, filename="video.mp4"),
                caption=f"🎥 **Video Downloaded**\n\n🔗 [Direct Link]({mp4_url})",
                parse_mode="Markdown"
            )
            await status_msg.delete()
        except Exception as e:
            await status_msg.edit_text(
                f"⚠️ **Telegram Upload Failed** (File too big?)\n\n"
                f"🔗 **Click here to watch/download:**\n{mp4_url}"
            )

    except Exception as e:
        logger.error(f"Critical: {e}")
        await status_msg.edit_text("❌ System Error.")

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