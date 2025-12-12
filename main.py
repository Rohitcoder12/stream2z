import os
import re
import asyncio
import logging
import cloudscraper
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

# --- HELPER: Get ID ---
def get_video_id(url):
    parsed = urlparse(url)
    if parsed.fragment: return parsed.fragment
    path_parts = parsed.path.split('/')
    for part in path_parts:
        if len(part) > 8 and part.isalnum(): return part
    return None

# --- BROWSER ENGINE (CLOUDSCRAPER) ---
def download_logic_sync(video_id):
    """
    This runs synchronously using Cloudscraper to mimic a real Chrome browser.
    It bypasses the 403 error.
    """
    # 1. Create a "Browser" instance
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
    
    embed_url = f"https://streama2z.pro/e/{video_id}"
    local_filename = f"{video_id}.mp4"
    
    # 2. Visit Embed Page (Bypass 403)
    headers = {
        "Referer": "https://smartkhabrinews.com/",
        "Origin": "https://smartkhabrinews.com"
    }
    
    try:
        # Get the HTML of the player
        response = scraper.get(embed_url, headers=headers)
        if response.status_code != 200:
            return None, f"Embed Page Blocked: {response.status_code}"
        
        html = response.text
        
        # 3. Extract the MP4 Link
        match = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.mp4)["\']', html)
        if not match:
             match = re.search(r'["\'](https?://[^"\']+\.mp4)["\']', html)
        
        if not match:
            return None, "Could not find video link in HTML."
        
        mp4_url = match.group(1)
        
        # 4. Download the File
        # IMPORTANT: Update referer to the embed page
        headers['Referer'] = embed_url
        
        with scraper.get(mp4_url, headers=headers, stream=True) as r:
            if r.status_code == 403:
                return None, "Video File Forbidden (403)"
            r.raise_for_status()
            
            # Check size
            size = int(r.headers.get('Content-Length', 0))
            if size > 49 * 1024 * 1024:
                return None, f"Video too large (>50MB). Link: {mp4_url}"
                
            with open(local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
        return local_filename, None
        
    except Exception as e:
        return None, str(e)

# --- ASYNC WRAPPER ---
async def process_video(url, message):
    video_id = get_video_id(url)
    if not video_id:
        await message.answer("❌ No ID found. Send the SmartKhabri link ending in `#id`")
        return

    status_msg = await message.answer(f"🛡️ **Bypassing Protection...**\nID: `{video_id}`")
    
    # Run the blocking cloudscraper code in a separate thread so the bot doesn't freeze
    loop = asyncio.get_running_loop()
    filename, error = await loop.run_in_executor(None, download_logic_sync, video_id)
    
    if error:
        # If error contains "Link:", it means file was too big, but we have the link
        if "Link:" in error:
            link = error.split("Link: ")[1]
            await status_msg.edit_text(f"⚠️ **File too big for Telegram.**\n\n🔗 [Click to Download]({link})")
        else:
            await status_msg.edit_text(f"❌ **Error:** {error}")
        return

    await status_msg.edit_text("📤 **Uploading...**")
    
    try:
        video_file = FSInputFile(filename)
        await message.answer_video(video_file, caption=f"✅ **Downloaded**")
    except Exception as e:
        await message.answer(f"❌ Upload Error: {e}")
    finally:
        await status_msg.delete()
        if os.path.exists(filename):
            os.remove(filename)

# --- BOT HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("👋 **Cloudscraper Mode Active**\nSend the **SmartKhabri News Link**.")

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