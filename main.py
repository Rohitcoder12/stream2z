import os
import re
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from urllib.parse import urlparse, unquote
import requests
from bs4 import BeautifulSoup
import time

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
def extract_video_id(url):
    """Extract video ID from various URL formats"""
    # From SmartKhabri: https://smartkhabrinews.com/.../article/#a67dnohmamur
    if '#' in url:
        video_id = url.split('#')[-1]
        if len(video_id) > 8:
            return video_id
    
    # From streama2z direct: https://streama2z.pro/a67dnohmamur/video.mp4
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p and not p.endswith('.mp4')]
    
    for part in path_parts:
        # Skip 'e' (embed indicator)
        if part != 'e' and len(part) > 8 and part.replace('_', '').isalnum():
            return part
    
    return None

def create_session():
    """Create requests session with browser-like headers"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    })
    return session

# --- MAIN DOWNLOAD FUNCTION ---
def download_streama2z_video(url, video_id=None):
    """
    Download video from streama2z using the proper technique:
    1. Visit embed page (/e/VIDEO_ID) to get actual video URL
    2. Extract video URL from embed page JavaScript
    3. Download using proper referer headers
    """
    session = create_session()
    
    try:
        # Step 1: Extract video ID
        if not video_id:
            video_id = extract_video_id(url)
        
        if not video_id:
            return None, "❌ Could not extract video ID from URL"
        
        logger.info(f"Video ID: {video_id}")
        
        filename = f"{video_id}.mp4"
        
        # Step 2: Build embed URL (this is the KEY - must visit embed page first!)
        embed_url = f"https://streama2z.pro/e/{video_id}"
        
        logger.info(f"🔗 Visiting embed page: {embed_url}")
        
        # Visit embed page with proper headers
        embed_headers = {
            'Referer': 'https://smartkhabrinews.com/',
            'Origin': 'https://smartkhabrinews.com'
        }
        session.headers.update(embed_headers)
        
        response = session.get(embed_url, timeout=30)
        
        if response.status_code != 200:
            return None, f"❌ Embed page returned {response.status_code}. Video may be deleted or expired."
        
        html = response.text
        logger.info(f"✅ Embed page loaded successfully")
        
        # Step 3: Extract video URL from JavaScript
        # Pattern 1: Look for file: "URL" or file:"URL"
        patterns = [
            r'file\s*[:=]\s*["\']([^"\']+\.mp4[^"\']*)["\']',
            r'src\s*[:=]\s*["\']([^"\']+\.mp4[^"\']*)["\']',
            r'source\s*[:=]\s*["\']([^"\']+\.mp4[^"\']*)["\']',
            r'["\']([^"\']*https?://[^"\']*streama2z[^"\']*\.mp4[^"\']*)["\']',
            r'(https?://[^\s\'"<>]+\.mp4[^\s\'"<>]*)'
        ]
        
        video_url = None
        for pattern in patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                # Get the first match that looks like a valid URL
                for match in matches:
                    if 'http' in match and '.mp4' in match:
                        video_url = match
                        logger.info(f"✅ Found video URL: {video_url}")
                        break
                if video_url:
                    break
        
        if not video_url:
            return None, "❌ Could not extract video URL from embed page. Video may be protected or deleted."
        
        # Step 4: Download the video with proper referer
        logger.info(f"📥 Downloading video...")
        
        # CRITICAL: Use embed URL as referer (this prevents 403!)
        download_headers = {
            'Referer': embed_url,
            'Origin': 'https://streama2z.pro',
            'Accept': '*/*',
            'Accept-Encoding': 'identity;q=1, *;q=0',
            'Range': 'bytes=0-',
        }
        session.headers.update(download_headers)
        
        video_response = session.get(video_url, stream=True, timeout=120)
        
        if video_response.status_code == 403:
            return None, "❌ Video download blocked (403). This happens when:\n• Link is expired\n• IP restriction\n• Direct access not allowed"
        
        if video_response.status_code == 404:
            return None, "❌ Video not found (404). The file may have been deleted."
        
        video_response.raise_for_status()
        
        # Check file size
        content_length = video_response.headers.get('Content-Length', 0)
        if content_length:
            size_mb = int(content_length) / (1024 * 1024)
            logger.info(f"📦 Video size: {size_mb:.2f} MB")
            
            # Telegram bot limit is 50MB
            if size_mb > 50:
                return None, f"⚠️ Video too large ({size_mb:.1f}MB > 50MB limit)\n\n🔗 Direct link:\n{video_url}"
        
        # Download with progress
        downloaded = 0
        with open(filename, 'wb') as f:
            for chunk in video_response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
        
        downloaded_mb = downloaded / (1024 * 1024)
        logger.info(f"✅ Download complete: {downloaded_mb:.2f} MB")
        
        if downloaded == 0:
            if os.path.exists(filename):
                os.remove(filename)
            return None, "❌ Downloaded file is empty. Video may be corrupted or deleted."
        
        return filename, None
        
    except requests.exceptions.Timeout:
        return None, "❌ Download timeout. Server is not responding. Try again later."
    
    except requests.exceptions.ConnectionError:
        return None, "❌ Connection error. Check your internet connection."
    
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return None, f"❌ Download failed: {str(e)}"
    
    finally:
        session.close()

# --- ASYNC WRAPPER ---
async def process_video(url, message):
    """Process video download request"""
    video_id = extract_video_id(url)
    
    status_msg = await message.answer(
        f"🔄 **Processing video...**\n" +
        (f"ID: `{video_id}`\n\n" if video_id else "\n") +
        f"_This may take 20-40 seconds..._\n\n"
        f"**Steps:**\n"
        f"1️⃣ Extracting video ID\n"
        f"2️⃣ Visiting embed page\n"
        f"3️⃣ Finding video URL\n"
        f"4️⃣ Downloading video",
        parse_mode="Markdown"
    )
    
    # Run download in executor to avoid blocking
    loop = asyncio.get_running_loop()
    filename, error = await loop.run_in_executor(None, download_streama2z_video, url, video_id)
    
    if error:
        # Check if error contains a direct link
        if "Direct link:" in error or "🔗" in error:
            await status_msg.edit_text(error, parse_mode="Markdown")
        else:
            await status_msg.edit_text(
                f"{error}\n\n"
                f"**💡 Tips:**\n"
                f"• Make sure the link is from SmartKhabri News\n"
                f"• Check if video is still available\n"
                f"• Try a different video",
                parse_mode="Markdown"
            )
        return
    
    # Upload to Telegram
    await status_msg.edit_text("📤 **Uploading to Telegram...**")
    
    try:
        video_file = FSInputFile(filename)
        await message.answer_video(
            video_file,
            caption="✅ **Downloaded successfully!**\n\n_Enjoy your video!_",
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
                logger.info(f"🗑️ Cleaned up: {filename}")
            except:
                pass

# --- BOT HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 **Welcome to SmartKhabri Video Downloader!**\n\n"
        "🎬 **I can download videos from:**\n"
        "• SmartKhabri News articles\n"
        "• Streama2z direct links\n"
        "• Streama2z embed pages\n\n"
        "📝 **How to use:**\n"
        "1. Visit SmartKhabri News\n"
        "2. Open an article with video\n"
        "3. Copy the full URL (with #videoid)\n"
        "4. Send it to me!\n\n"
        "📋 **Supported formats:**\n"
        "✅ `smartkhabrinews.com/.../article/#videoid`\n"
        "✅ `streama2z.pro/videoid/file.mp4`\n"
        "✅ `streama2z.pro/e/videoid`\n\n"
        "⚠️ **Limitations:**\n"
        "• Max size: 50MB (Telegram limit)\n"
        "• Processing: 20-40 seconds\n"
        "• Must visit embed page first (handled automatically)\n\n"
        "**Example:**\n"
        "`https://smartkhabrinews.com/p25/article/#a67dnohmamur`"
    )
    await message.answer(welcome_text, parse_mode="Markdown")

@dp.message(F.text)
async def handle_message(message: types.Message):
    text = message.text.strip()
    
    # Check if message contains URL or video ID
    if "http" in text.lower():
        # Extract URL
        urls = re.findall(r'https?://[^\s]+', text)
        if urls:
            await process_video(urls[0], message)
        else:
            await message.answer(
                "❌ **Could not find URL**\n\n"
                "Please send a valid SmartKhabri or Streama2z link.",
                parse_mode="Markdown"
            )
    elif '#' in text and len(text.split('#')[-1]) > 8:
        # User sent something like #videoid
        await message.answer(
            "⚠️ **Need full URL**\n\n"
            "Please send the complete SmartKhabri article URL, not just the video ID.\n\n"
            "**Example:**\n"
            "`https://smartkhabrinews.com/p25/article/#a67dnohmamur`",
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "❌ **Invalid input**\n\n"
            "Please send a SmartKhabri News article link or Streama2z video URL.\n\n"
            "**Examples:**\n"
            "• `https://smartkhabrinews.com/p25/article/#videoid`\n"
            "• `https://streama2z.pro/videoid/video.mp4`\n"
            "• `https://streama2z.pro/e/videoid`",
            parse_mode="Markdown"
        )

# --- MAIN ---
async def main():
    """Start the bot"""
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("🚀 SmartKhabri Video Downloader Bot started!")
    logger.info("📋 Ready to download videos from SmartKhabri News")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⛔ Bot stopped by user")