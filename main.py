import os
import re
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from urllib.parse import urlparse
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import requests
from pathlib import Path

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
    # For URLs like /eoaex4q2vufz/video.mp4
    path_parts = [p for p in parsed.path.split('/') if p]
    for part in path_parts:
        if len(part) > 8 and not part.endswith('.mp4'):
            return part
    return None

def is_direct_video_url(url):
    """Check if URL is a direct video link"""
    return url.endswith('.mp4') or url.endswith('.mkv') or url.endswith('.avi')

# --- SELENIUM DOWNLOAD ENGINE ---
def download_with_selenium(url, video_id=None):
    """
    Downloads video using Selenium with undetected-chromedriver
    to bypass bot detection and 403 errors
    """
    driver = None
    
    try:
        # Setup undetected Chrome options
        options = uc.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        
        # Initialize undetected Chrome driver
        logger.info("Initializing Chrome driver...")
        driver = uc.Chrome(options=options, version_main=131)
        
        # Generate filename
        if not video_id:
            video_id = url.split('/')[-1].split('.')[0][:20]
        local_filename = f"{video_id}.mp4"
        
        mp4_url = url
        
        # If not a direct video URL, extract from page
        if not is_direct_video_url(url):
            logger.info(f"Loading page: {url}")
            driver.get(url)
            
            # Wait for video element to load
            wait = WebDriverWait(driver, 30)
            
            try:
                # Try to find video element
                video_element = wait.until(
                    EC.presence_of_element_located((By.TAG_NAME, "video"))
                )
                mp4_url = video_element.get_attribute("src")
                logger.info(f"Found video URL via video tag: {mp4_url}")
            except:
                # If no video tag, try to find it in page source
                page_source = driver.page_source
                
                # Try multiple patterns to extract video URL
                patterns = [
                    r'file\s*[:=]\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                    r'src\s*[:=]\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                    r'["\']([^"\']*streama2z[^"\']*\.mp4[^"\']*)["\']',
                    r'https?://[^"\'\s]+\.mp4[^"\'\s]*'
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, page_source, re.IGNORECASE)
                    if match:
                        mp4_url = match.group(1) if match.groups() else match.group(0)
                        logger.info(f"Found video URL via regex: {mp4_url}")
                        break
            
            if mp4_url == url:
                driver.quit()
                return None, "Could not find video URL in page"
        
        # Close driver before downloading
        driver.quit()
        driver = None
        
        # Download the video
        logger.info(f"Downloading from: {mp4_url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Referer': url if not is_direct_video_url(url) else 'https://streama2z.pro/',
            'Accept': 'video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'identity',
            'Range': 'bytes=0-'
        }
        
        response = requests.get(mp4_url, headers=headers, stream=True, timeout=60)
        
        if response.status_code == 403:
            return None, "Video blocked (403 Forbidden) - Server rejected request"
        if response.status_code == 404:
            return None, "Video not found (404)"
        
        response.raise_for_status()
        
        # Check file size
        size = int(response.headers.get('Content-Length', 0))
        logger.info(f"Video size: {size / (1024*1024):.2f} MB")
        
        # Telegram limit is 50MB
        if size > 50 * 1024 * 1024:
            return None, f"Video too large ({size/(1024*1024):.1f}MB > 50MB). Direct link: {mp4_url}"
        
        # Download video
        downloaded = 0
        with open(local_filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
        
        logger.info(f"Download complete: {downloaded / (1024*1024):.2f} MB")
        return local_filename, None
        
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        return None, f"Download failed: {str(e)}"
    
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

# --- ASYNC WRAPPER ---
async def process_video(url, message):
    """Process video download request"""
    video_id = get_video_id(url)
    
    if video_id:
        status_msg = await message.answer(f"🔄 **Processing...**\nID: `{video_id}`\n\n_This may take 30-60 seconds..._")
    else:
        status_msg = await message.answer(f"🔄 **Processing video...**\n\n_This may take 30-60 seconds..._")
    
    # Run download in executor
    loop = asyncio.get_running_loop()
    filename, error = await loop.run_in_executor(None, download_with_selenium, url, video_id)
    
    if error:
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
        "1. Send me a streama2z.pro video link\n"
        "2. I'll download and send it to you\n\n"
        "✅ **Supported formats:**\n"
        "• Direct .mp4 links\n"
        "• Streama2z embed links\n"
        "• Video IDs\n\n"
        "⚠️ **Note:**\n"
        "• Max file size is 50MB\n"
        "• Processing takes 30-60 seconds\n"
        "• Uses Selenium to bypass protection"
    )
    await message.answer(welcome_text, parse_mode="Markdown")

@dp.message(F.text)
async def handle_url(message: types.Message):
    text = message.text.strip()
    
    if "http" in text.lower() or "streama2z" in text.lower():
        urls = re.findall(r'https?://[^\s]+', text)
        if urls:
            await process_video(urls[0], message)
        else:
            await message.answer("❌ Could not find a valid URL")
    else:
        await message.answer(
            "❌ **Invalid input**\n\n"
            "Please send a streama2z.pro video URL.\n\n"
            "**Example:**\n"
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