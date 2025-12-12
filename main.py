import os
import re
import asyncio
import logging
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import URLInputFile
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
TOKEN = os.environ.get("BOT_TOKEN") 
# If testing locally, uncomment below:
# TOKEN = "YOUR_BOT_TOKEN_HERE"

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Bot
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- WEB SERVER (REQUIRED FOR RENDER) ---
async def health_check(request):
    return web.Response(text="StreamA2Z Bot is Alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server running on port {port}")

# --- CORE EXTRACTION LOGIC ---
async def get_stream_url(session, initial_url):
    """
    If the user sends a smartkhabrinews link, we need to find the 
    streama2z iframe source first.
    """
    if "streama2z" in initial_url:
        return initial_url

    try:
        async with session.get(initial_url) as response:
            text = await response.text()
            soup = BeautifulSoup(text, 'html.parser')
            
            # Method 1: Look for iframe
            iframe = soup.find('iframe')
            if iframe and "streama2z" in iframe.get('src', ''):
                return iframe.get('src')
            
            # Method 2: Regex search in source code
            match = re.search(r'(https?://(?:www\.)?streama2z\.(?:pro|com|xyz|net)/[^\s"\']+)', text)
            if match:
                return match.group(1)
                
    except Exception as e:
        logger.error(f"Error finding iframe: {e}")
    
    return None

async def extract_mp4(target_url):
    """
    Visits the StreamA2Z link and extracts the .mp4 file path 
    hidden in the JavaScript player.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Referer": "https://smartkhabrinews.com/",  # Crucial for bypassing protection
        "Origin": "https://smartkhabrinews.com"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        # Step 1: Resolve the actual StreamA2Z URL (if wrapped in news site)
        stream_link = await get_stream_url(session, target_url)
        
        if not stream_link:
            return None, "Could not find a StreamA2Z video on that page."
        
        logger.info(f"Processing Stream Link: {stream_link}")

        # Step 2: Fetch the StreamA2Z page source
        # We update referer to itself to ensure it loads
        headers['Referer'] = stream_link 
        
        async with session.get(stream_link, headers=headers) as response:
            if response.status != 200:
                return None, f"Stream host returned error: {response.status}"
            
            html_content = await response.text()

        # Step 3: Regex Magic to find the MP4
        # Patterns often used by JWPlayer/P2P players on these sites:
        patterns = [
            r'file\s*:\s*["\'](https?://[^"\']+\.mp4)["\']',  # Standard file: "url"
            r'sources\s*:\s*\[\s*{\s*file\s*:\s*["\']([^"\']+\.mp4)["\']', # Nested source
            r'["\'](https?://[^"\']+\.mp4)["\']'  # Any raw mp4 link in quotes (fallback)
        ]

        mp4_url = None
        for pattern in patterns:
            match = re.search(pattern, html_content)
            if match:
                mp4_url = match.group(1)
                break
        
        if mp4_url:
            return mp4_url, None
        else:
            return None, "Could not extract the MP4 file. The site format might have changed."

# --- BOT HANDLERS ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "🎬 **StreamA2Z Downloader**\n\n"
        "Send me a link from `smartkhabrinews.com` OR `streama2z.pro`.\n"
        "I will extract the hidden video for you."
    )

@dp.message(F.text)
async def handle_video_request(message: types.Message):
    url = message.text.strip()
    
    # Basic validation
    if not any(x in url for x in ["smartkhabri", "streama2z", "http"]):
        await message.answer("❌ Invalid link. Please send a supported URL.")
        return

    status_msg = await message.answer("🔄 **Processing...** accessing the stream host.")

    try:
        # Run extraction
        direct_url, error = await extract_mp4(url)

        if error:
            await status_msg.edit_text(f"❌ **Error:** {error}")
            return

        await status_msg.edit_text("✅ **Found!** Downloading video to Telegram server...")

        # Attempt to upload video
        try:
            await message.answer_video(
                video=URLInputFile(direct_url, filename="video.mp4"),
                caption=f"🎥 **Video Downloaded**\n\n🔗 Source: StreamA2Z",
                supports_streaming=True
            )
            await status_msg.delete()
        except Exception as e:
            # Fallback if file is > 50MB (Telegram Bot API limit)
            await status_msg.edit_text(
                f"⚠️ **File is too large for Telegram Bot upload.**\n\n"
                f"🔗 **Direct Link:** {direct_url}\n\n"
                f"*(Click the link to download manually)*"
            )

    except Exception as e:
        logger.error(f"Critical error: {e}")
        await status_msg.edit_text("❌ An unexpected error occurred.")

# --- ENTRY POINT ---
async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")
