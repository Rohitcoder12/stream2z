import os
import re
import asyncio
import logging
import cloudscraper
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, BufferedInputFile
from urllib.parse import urlparse
import tempfile

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
def get_video_id(url):
    """Extract video ID from URL"""
    parsed = urlparse(url)
    if parsed.fragment:
        return parsed.fragment
    path_parts = parsed.path.split('/')
    for part in path_parts:
        if len(part) > 8 and part.isalnum():
            return part
    return None

def is_direct_video_url(url):
    """Check if URL is a direct video link"""
    return url.endswith('.mp4') or url.endswith('.mkv') or url.endswith('.avi')

# --- DOWNLOAD ENGINE ---
def download_video_sync(url, video_id=None):
    """
    Downloads video using cloudscraper to bypass protection.
    Handles both direct video URLs and embed pages.
    """
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
    
    # Generate filename
    if not video_id:
        video_id = url.split('/')[-1].split('.')[0][:20]
    local_filename = f"{video_id}.mp4"
    
    headers = {
        "Referer": "https://smartkhabrinews.com/",
        "Origin": "https://smartkhabrinews.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        mp4_url = url
        
        # If not a direct video URL, try to extract from embed page
        if not is_direct_video_url(url):
            embed_url = url
            if '/e/' not in url and video_id:
                embed_url = f"https://streama2z.pro/e/{video_id}"
            
            logger.info(f"Fetching embed page: {embed_url}")
            response = scraper.get(embed_url, headers=headers, timeout=30)
            
            if response.status_code != 200:
                return None, f"Embed page blocked: {response.status_code}"
            
            html = response.text
            
            # Try multiple patterns to find video URL
            patterns = [
                r'file\s*:\s*["\'](https?://[^"\']+\.mp4)["\']',
                r'src\s*:\s*["\'](https?://[^"\']+\.mp4)["\']',
                r'["\'](https?://[^"\']+streama2z[^"\']+\.mp4)["\']',
                r'["\'](https?://[^"\']+\.mp4)["\']'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    mp4_url = match.group(1)
                    logger.info(f"Found video URL: {mp4_url}")
                    break
            
            if mp4_url == url:
                return None, "Could not find video link in page"
            
            # Update referer for the actual video download
            headers['Referer'] = embed_url
        
        # Download the video file
        logger.info(f"Downloading from: {mp4_url}")
        
        with scraper.get(mp4_url, headers=headers, stream=True, timeout=60) as r:
            if r.status_code == 403:
                return None, "Video blocked (403 Forbidden)"
            if r.status_code == 404:
                return None, "Video not found (404)"
            
            r.raise_for_status()
            
            # Check file size
            size = int(r.headers.get('Content-Length', 0))
            logger.info(f"Video size: {size / (1024*1024):.2f} MB")
            
            # Telegram limit is 50MB for bots
            if size > 50 * 1024 * 1024:
                return None, f"Video too large ({size/(1024*1024):.1f}MB > 50MB). Direct link: {mp4_url}"
            
            # Download with progress tracking
            downloaded = 0
            with open(local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            
            logger.info(f"Download complete: {downloaded / (1024*1024):.2f} MB")
            
        return local_filename, None
        
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        return None, f"Download failed: {str(e)}"

# --- ASYNC WRAPPER ---
async def process_video(url, message):
    """Process video download request"""
    video_id = get_video_id(url)
    
    # Show initial status
    if video_id:
        status_msg = await message.answer(f"🔄 **Processing...**\nID: `{video_id}`")
    else:
        status_msg = await message.answer(f"🔄 **Processing video...**")
    
    # Run download in executor to avoid blocking
    loop = asyncio.get_running_loop()
    filename, error = await loop.run_in_executor(None, download_video_sync, url, video_id)
    
    if error:
        # Check if it's a "too large" error with direct link
        if "Direct link:" in error or "link:" in error.lower():
            link = error.split("link: ")[-1].split()[0]
            await status_msg.edit_text(
                f"⚠️ **File too large for Telegram (>50MB)**\n\n"
                f"🔗 **Direct Download Link:**\n`{link}`\n\n"
                f"_Copy and paste this link in your browser_",
                parse_mode="Markdown"
            )
        else:
            await status_msg.edit_text(f"❌ **Error:** {error}")
        return
    
    # Upload to Telegram
    await status_msg.edit_text("📤 **Uploading to Telegram...**")
    
    try:
        video_file = FSInputFile(filename)
        await message.answer_video(
            video_file,
            caption="✅ **Downloaded successfully!**",
            supports_streaming=True
        )
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        await status_msg.edit_text(f"❌ **Upload failed:** {str(e)}")
    finally:
        # Cleanup
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except:
                pass

# --- BOT HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 **Welcome to Video Downloader Bot!**\n\n"
        "📹 **How to use:**\n"
        "1. Send me any video link from supported sites\n"
        "2. I'll download and send it to you\n\n"
        "✅ **Supported:**\n"
        "• Direct .mp4 links\n"
        "• Streama2z embed links\n"
        "• SmartKhabri News links\n\n"
        "⚠️ **Note:** Max file size is 50MB"
    )
    await message.answer(welcome_text, parse_mode="Markdown")

@dp.message(F.text)
async def handle_url(message: types.Message):
    text = message.text.strip()
    
    # Check if message contains a URL
    if "http" in text.lower():
        # Extract URL from text
        urls = re.findall(r'https?://[^\s]+', text)
        if urls:
            await process_video(urls[0], message)
        else:
            await message.answer("❌ Could not find a valid URL in your message")
    else:
        await message.answer(
            "❌ **Invalid input**\n\n"
            "Please send a video URL. Example:\n"
            "`https://streama2z.pro/eoaex4q2vufz/video.mp4`",
            parse_mode="Markdown"
        )

# --- MAIN ---
async def main():
    """Main function to start the bot"""
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot started successfully!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        pass