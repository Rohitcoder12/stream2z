import os
import re
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from urllib.parse import urlparse

# Import the special browser simulator
from curl_cffi.requests import AsyncSession

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
    return web.Response(text="Bot is Live and Running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server started on port {port}")

# --- HELPER: Extract ID ---
def get_video_id(url):
    """
    Extracts the ID from various link formats.
    """
    parsed = urlparse(url)
    # 1. Check path for direct MP4 link (e.g. /ID/filename.mp4)
    # Your link: /a67dnohmamur/5_6181726238491548482.mp4
    path_parts = parsed.path.split('/')
    for part in path_parts:
        # Look for alphanumeric ID between 8 and 20 chars
        if 8 < len(part) < 20 and part.isalnum():
            return part
            
    # 2. Check fragment (#ID)
    if parsed.fragment:
        return parsed.fragment
        
    return None

# --- BROWSER ENGINE (CURL_CFFI) ---
async def download_with_browser(video_id, message):
    status_msg = await message.answer(f"🛡️ **Bypassing Cloudflare...**\nID: `{video_id}`")
    
    embed_url = f"https://streama2z.pro/e/{video_id}"
    local_filename = f"{video_id}.mp4"
    
    # We pretend to be Chrome 120
    # verify=False helps avoid some SSL errors on scraped sites
    async with AsyncSession(impersonate="chrome120", verify=False) as session:
        
        # STEP 1: Visit Embed Page (Sets Cookies & Solves Challenge)
        headers = {
            "Referer": "https://smartkhabrinews.com/",
            "Origin": "https://smartkhabrinews.com",
        }
        
        try:
            logger.info(f"Visiting Embed: {embed_url}")
            resp = await session.get(embed_url, headers=headers)
            
            if resp.status_code == 403:
                # If 403 here, Cloudflare is VERY strict.
                await status_msg.edit_text("❌ **Cloudflare Blocked.** Try sending the link again in 1 minute.")
                return

            html = resp.text

            # STEP 2: Find the internal MP4 link
            # Regex to find: file: "https://..."
            match = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.mp4)["\']', html)
            if not match:
                match = re.search(r'["\'](https?://[^"\']+\.mp4)["\']', html)
            
            if not match:
                await status_msg.edit_text("❌ **Error:** Video link not found in HTML.")
                return
            
            fresh_mp4_url = match.group(1)
            logger.info(f"Found MP4: {fresh_mp4_url}")

            # STEP 3: Download the File
            # IMPORTANT: We use the SAME session to keep the Cloudflare clearance cookies
            headers['Referer'] = embed_url
            
            await status_msg.edit_text("📥 **Downloading Video...**")
            
            # Streaming download
            response = await session.get(fresh_mp4_url, headers=headers, stream=True)
            
            if response.status_code != 200:
                await status_msg.edit_text(f"❌ **Download Error:** {response.status_code}")
                return

            # Size check
            try:
                size = int(response.headers.get('content-length', 0))
                if size > 49 * 1024 * 1024:
                    await status_msg.edit_text(f"⚠️ **File too big (>50MB).**\n\n🔗 [Direct Link]({fresh_mp4_url})")
                    return
            except:
                pass

            # Write to file
            with open(local_filename, "wb") as f:
                async for chunk in response.aiter_content():
                    f.write(chunk)

            # STEP 4: Upload
            await status_msg.edit_text("📤 **Uploading to Telegram...**")
            video_file = FSInputFile(local_filename)
            await message.answer_video(video_file, caption=f"✅ **Downloaded via Cloudflare Bypass**")
            await status_msg.delete()

        except Exception as e:
            logger.error(f"Scrape Error: {e}")
            await status_msg.edit_text(f"❌ **System Error:** {str(e)}")
        finally:
            if os.path.exists(local_filename):
                os.remove(local_filename)

# --- BOT HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    # This is your "I am live" message for the user
    await message.answer("🟢 **Bot is Online & Ready!**\n\nI am using `Chrome 120` simulation to bypass Cloudflare.\nSend me a link.")

@dp.message(F.text)
async def handle_url(message: types.Message):
    url = message.text.strip()
    
    if "http" in url:
        video_id = get_video_id(url)
        if video_id:
            await download_with_browser(video_id, message)
        else:
            await message.answer("❌ Could not find ID in link.")
    else:
        await message.answer("❌ Invalid URL")

# --- MAIN ENTRY POINT ---
async def main():
    # 1. Start Web Server
    await start_web_server()
    
    # 2. Log that we are live (Console Log)
    print("✅✅✅ BOT IS LIVE NOW! SEND MESSAGES! ✅✅✅")
    
    # 3. Start Bot
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass