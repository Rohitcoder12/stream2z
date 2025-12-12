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

async def download_file_locally(url, referer, filename):
    """
    Downloads the file to the local disk using the correct Referer header.
    Returns: True if success, "TOO_BIG" if > 50MB, False if failed.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer,
        "Origin": "https://streama2z.pro" # Sometimes needed
    }
    
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.error(f"Download Error Status: {resp.status}")
                    return False
                
                # Check size (50MB limit for Bot API)
                try:
                    size = int(resp.headers.get('Content-Length', 0))
                    if size > 49 * 1024 * 1024: 
                        return "TOO_BIG"
                except:
                    pass # Content-length might be missing, try anyway

                f = await aiofiles.open(filename, mode='wb')
                await f.write(await resp.read())
                await f.close()
        return True
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return False

# --- CORE LOGIC ---

async def process_video(url, message):
    status_msg = await message.answer("🔎 **Processing...**")
    
    final_mp4_url = None
    referer_for_download = "https://smartkhabrinews.com/"

    # --- SCENARIO 1: Direct MP4 Link ---
    if url.endswith(".mp4"):
        final_mp4_url = url
        # If it's a direct link, the referer is usually the domain root
        referer_for_download = "https://streama2z.pro/"
    
    # --- SCENARIO 2: SmartKhabri / StreamA2Z Embed Link ---
    else:
        video_id = get_id_from_url(url)
        if not video_id:
            await status_msg.edit_text("❌ **Error:** Could not find a Video ID. Make sure URL ends with `#id`.")
            return

        embed_url = f"https://streama2z.pro/e/{video_id}"
        
        # Scrape the MP4 from the embed page
        scrape_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://smartkhabrinews.com/"
        }
        
        try:
            async with aiohttp.ClientSession(headers=scrape_headers) as session:
                async with session.get(embed_url) as resp:
                    if resp.status != 200:
                        await status_msg.edit_text(f"❌ **Error:** Stream host returned {resp.status}")
                        return
                    html = await resp.text()
            
            # Find MP4
            match = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.mp4)["\']', html)
            if not match:
                match = re.search(r'["\'](https?://[^"\']+\.mp4)["\']', html)
            
            if match:
                final_mp4_url = match.group(1)
                referer_for_download = embed_url # Important: Set referer to embed page
            else:
                await status_msg.edit_text("❌ **Error:** Could not extract video URL.")
                return
                
        except Exception as e:
            await status_msg.edit_text(f"❌ **Scrape Error:** {str(e)}")
            return

    # --- DOWNLOAD AND SEND ---
    
    if final_mp4_url:
        local_filename = f"vid_{message.from_user.id}.mp4"
        
        await status_msg.edit_text("📥 **Downloading to server...**\n(Bypassing 403 Forbidden)")
        
        result = await download_file_locally(final_mp4_url, referer_for_download, local_filename)
        
        if result == "TOO_BIG":
            await status_msg.edit_text(f"⚠️ **Video > 50MB.** Telegram Bots can't upload big files.\n\n🔗 [Direct Link]({final_mp4_url})")
            if os.path.exists(local_filename): os.remove(local_filename)
            return
        
        elif result is False:
            await status_msg.edit_text("❌ **Download Failed.** Host rejected connection.")
            if os.path.exists(local_filename): os.remove(local_filename)
            return

        # Upload
        await status_msg.edit_text("📤 **Uploading to Telegram...**")
        try:
            video_file = FSInputFile(local_filename)
            await message.answer_video(video_file, caption="✅ **Downloaded successfully**")
        except Exception as e:
            await message.answer(f"❌ **Upload Error:** {e}")
        finally:
            await status_msg.delete()
            if os.path.exists(local_filename):
                os.remove(local_filename)

# --- BOT HANDLERS ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("👋 Send me the link!")

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