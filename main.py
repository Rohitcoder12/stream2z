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
def get_video_id_from_url(url):
    """Extract video ID from any type of URL"""
    # From SmartKhabri URLs like: https://smartkhabrinews.com/.../#a67dnohmamur
    if '#' in url:
        return url.split('#')[-1]
    
    # From streama2z URLs like: /a67dnohmamur/video.mp4
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p and not p.endswith('.mp4')]
    
    for part in path_parts:
        if len(part) > 8 and part.isalnum():
            return part
    
    return None

def create_session():
    """Create a requests session with proper headers"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    })
    return session

# --- DOWNLOAD ENGINE ---
def download_video_from_smartkhabri(url, video_id=None):
    """
    Downloads video from SmartKhabri News site
    Steps:
    1. Visit SmartKhabri article page
    2. Extract streama2z embed URL
    3. Visit embed page to get actual video URL
    4. Download the video
    """
    session = create_session()
    
    try:
        # Step 1: Get video ID from URL
        if not video_id:
            video_id = get_video_id_from_url(url)
        
        if not video_id:
            return None, "Could not extract video ID from URL"
        
        logger.info(f"Extracted video ID: {video_id}")
        
        # Generate filename
        local_filename = f"{video_id}.mp4"
        
        # Step 2: Try to find the video URL
        # First, try the direct streama2z URL patterns
        possible_urls = [
            f"https://streama2z.pro/{video_id}/video.mp4",
            f"https://streama2z.pro/e/{video_id}",
            f"https://streama2z.com/{video_id}/video.mp4",
            f"https://streama2z.com/e/{video_id}",
        ]
        
        mp4_url = None
        
        # If it's a SmartKhabri URL, extract the embed
        if 'smartkhabrinews.com' in url:
            logger.info(f"Fetching SmartKhabri page: {url}")
            
            response = session.get(url, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Look for iframe with streama2z
                iframes = soup.find_all('iframe')
                for iframe in iframes:
                    src = iframe.get('src', '')
                    if 'streama2z' in src:
                        logger.info(f"Found streama2z embed: {src}")
                        # Visit the embed page
                        embed_response = session.get(src, timeout=30)
                        if embed_response.status_code == 200:
                            # Extract video URL from embed page
                            patterns = [
                                r'file\s*[:=]\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                                r'src\s*[:=]\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                                r'https?://[^"\'\s]+streama2z[^"\'\s]*\.mp4[^"\'\s]*',
                                r'https?://[^"\'\s<>]+\.mp4'
                            ]
                            
                            for pattern in patterns:
                                match = re.search(pattern, embed_response.text, re.IGNORECASE)
                                if match:
                                    mp4_url = match.group(1) if match.groups() else match.group(0)
                                    logger.info(f"Found video URL: {mp4_url}")
                                    break
                            
                            if mp4_url:
                                break
        
        # If no URL found yet, try direct URLs
        if not mp4_url:
            for test_url in possible_urls:
                logger.info(f"Trying URL: {test_url}")
                try:
                    # Try HEAD request first
                    head_response = session.head(test_url, timeout=10, allow_redirects=True)
                    if head_response.status_code == 200:
                        mp4_url = head_response.url
                        logger.info(f"Found working URL: {mp4_url}")
                        break
                except:
                    continue
        
        if not mp4_url:
            return None, "Could not find video URL. The video may be expired or removed."
        
        # Step 3: Download the video
        logger.info(f"Downloading from: {mp4_url}")
        
        download_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://smartkhabrinews.com/',
            'Origin': 'https://smartkhabrinews.com',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Range': 'bytes=0-',
        }
        
        response = session.get(mp4_url, headers=download_headers, stream=True, timeout=120)
        
        if response.status_code == 403:
            return None, "Video access forbidden (403). The link may be expired or IP-restricted."
        if response.status_code == 404:
            return None, "Video not found (404). The file may have been deleted."
        
        response.raise_for_status()
        
        # Check file size
        size = int(response.headers.get('Content-Length', 0))
        logger.info(f"Video size: {size / (1024*1024):.2f} MB")
        
        # Telegram limit is 50MB
        if size > 50 * 1024 * 1024:
            return None, f"Video too large ({size/(1024*1024):.1f}MB > 50MB). Direct link: {mp4_url}"
        
        if size == 0:
            return None, "Video file is empty or size unknown"
        
        # Download video with progress
        downloaded = 0
        with open(local_filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
        
        logger.info(f"Download complete: {downloaded / (1024*1024):.2f} MB")
        
        if downloaded == 0:
            os.remove(local_filename)
            return None, "Downloaded file is empty"
        
        return local_filename, None
        
    except requests.exceptions.Timeout:
        return None, "Download timeout. The server is too slow or not responding."
    except requests.exceptions.ConnectionError:
        return None, "Connection error. Could not reach the server."
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        return None, f"Download failed: {str(e)}"
    finally:
        session.close()

# --- ASYNC WRAPPER ---
async def process_video(url, message):
    """Process video download request"""
    video_id = get_video_id_from_url(url)
    
    if video_id:
        status_msg = await message.answer(
            f"🔄 **Processing video...**\n"
            f"ID: `{video_id}`\n\n"
            f"_Please wait 15-30 seconds..._",
            parse_mode="Markdown"
        )
    else:
        status_msg = await message.answer(
            f"🔄 **Processing video...**\n\n"
            f"_Please wait 15-30 seconds..._",
            parse_mode="Markdown"
        )
    
    # Run download in executor
    loop = asyncio.get_running_loop()
    filename, error = await loop.run_in_executor(None, download_video_from_smartkhabri, url, video_id)
    
    if error:
        if "Direct link:" in error or "link:" in error.lower():
            link = error.split("link: ")[-1].split()[0]
            await status_msg.edit_text(
                f"⚠️ **File too large for Telegram**\n\n"
                f"📦 Size: >50MB (Telegram limit)\n\n"
                f"🔗 **Download directly:**\n`{link}`\n\n"
                f"_Copy this link and open in browser_",
                parse_mode="Markdown"
            )
        else:
            await status_msg.edit_text(
                f"❌ **Error**\n\n{error}\n\n"
                f"_Try sending the SmartKhabri article link (not direct video link)_",
                parse_mode="Markdown"
            )
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
                logger.info(f"Cleaned up: {filename}")
            except:
                pass

# --- BOT HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 **Welcome to SmartKhabri Video Downloader!**\n\n"
        "📹 **How to use:**\n"
        "1. Go to SmartKhabri News article\n"
        "2. Copy the article URL (with #videoid at end)\n"
        "3. Send it to me\n"
        "4. I'll download and send the video!\n\n"
        "✅ **Supported links:**\n"
        "• `smartkhabrinews.com/.../#videoid`\n"
        "• `streama2z.pro/videoid/file.mp4`\n"
        "• Direct streama2z links\n\n"
        "⚠️ **Limits:**\n"
        "• Max file size: 50MB\n"
        "• Processing time: 15-30 seconds\n\n"
        "**Example:**\n"
        "`https://smartkhabrinews.com/.../article/#a67dnohmamur`"
    )
    await message.answer(welcome_text, parse_mode="Markdown")

@dp.message(F.text)
async def handle_url(message: types.Message):
    text = message.text.strip()
    
    # Check if message contains a URL
    if "http" in text.lower() or "smartkhabri" in text.lower() or "streama2z" in text.lower():
        # Extract URL from text
        urls = re.findall(r'https?://[^\s]+', text)
        if urls:
            await process_video(urls[0], message)
        elif '#' in text:
            # User might have sent just the video ID with #
            await message.answer(
                "⚠️ **Incomplete URL**\n\n"
                "Please send the full SmartKhabri article URL:\n"
                "`https://smartkhabrinews.com/.../article/#videoid`",
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                "❌ **No valid URL found**\n\n"
                "Send the SmartKhabri article link containing the video.",
                parse_mode="Markdown"
            )
    else:
        await message.answer(
            "❌ **Invalid input**\n\n"
            "Please send a SmartKhabri News article URL.\n\n"
            "**Example:**\n"
            "`https://smartkhabrinews.com/p25/article/#a67dnohmamur`",
            parse_mode="Markdown"
        )

# --- MAIN ---
async def main():
    """Main function to start the bot"""
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("🚀 SmartKhabri Video Downloader Bot started!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")