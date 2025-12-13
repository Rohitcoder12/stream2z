import os
import asyncio
import logging
import aiohttp
import aiofiles
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from urllib.parse import urlparse
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
TOKEN = os.environ.get("BOT_TOKEN") 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- WEB SERVER ---
async def health_check(request):
    return web.Response(text="Sniffer Bot Running")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# --- HELPER: GET ID ---
def get_video_id(url):
    parsed = urlparse(url)
    path_parts = parsed.path.split('/')
    for part in path_parts:
        if 8 < len(part) < 20 and part.isalnum():
            return part
    if parsed.fragment: return parsed.fragment
    return None

# --- BROWSER ENGINE: SNIFFER MODE ---
async def extract_and_download(video_id, message):
    status_msg = await message.answer(f"🕵️ **Sniffing Network...**\nTarget ID: `{video_id}`")
    
    embed_url = f"https://streama2z.pro/e/{video_id}"
    screenshot_file = f"play_{video_id}.png"
    local_video_file = f"{video_id}.mp4"
    
    found_url = None

    try:
        async with async_playwright() as p:
            # Launch Headless Chrome
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            # --- NETWORK LISTENER ---
            # This function runs every time the browser makes a request
            async def handle_request(request):
                nonlocal found_url
                url = request.url
                # We look for the video file
                if ".mp4" in url and "streama2z" in url:
                    found_url = url
                    logger.info(f"CAPTURED MP4: {found_url}")

            page.on("request", handle_request)
            # ------------------------

            await status_msg.edit_text("⏳ **Loading Player...**")
            
            # 1. Go to page
            try:
                await page.goto(embed_url, referer="https://smartkhabrinews.com/")
                await page.wait_for_timeout(5000) # Wait for load
            except:
                pass

            # 2. CLICK THE PLAY BUTTON
            # We click the center of the screen to start the video
            await status_msg.edit_text("▶️ **Clicking Play Button...**")
            try:
                # Try specific JWPlayer button first, else center screen
                if await page.query_selector('.jw-display-icon-container'):
                    await page.click('.jw-display-icon-container')
                else:
                    await page.mouse.click(640, 360) # Click center of 1280x720
                
                await page.wait_for_timeout(5000) # Wait for network request
            except Exception as e:
                logger.error(f"Click error: {e}")

            # 3. Take Screenshot (To prove video started)
            await page.screenshot(path=screenshot_file, full_page=True)
            
            await browser.close()

            # --- PROCESS RESULT ---
            if found_url:
                await status_msg.edit_text(f"✅ **Link Sniffed!**\n`{found_url}`\n\nDownloading...")
                
                # Send screenshot of video playing
                if os.path.exists(screenshot_file):
                    photo = FSInputFile(screenshot_file)
                    await message.answer_photo(photo, caption="📸 **Video Started**")

                # DOWNLOAD THE FILE (Using standard Python now that we have the direct link)
                # We use the EMBED URL as referer because that's where we found it
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": embed_url 
                }
                
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(found_url) as resp:
                        if resp.status == 200:
                            # Size check
                            try:
                                size = int(resp.headers.get('content-length', 0))
                                if size > 49 * 1024 * 1024:
                                    await status_msg.edit_text(f"⚠️ **Video > 50MB.**\nTelegram Limit Exceeded.\n\n🔗 [Direct Download Link]({found_url})")
                                    return
                            except: pass

                            f = await aiofiles.open(local_video_file, mode='wb')
                            await f.write(await resp.read())
                            await f.close()
                            
                            await status_msg.edit_text("📤 **Uploading...**")
                            vid = FSInputFile(local_video_file)
                            await message.answer_video(vid, caption="✅ **Downloaded!**")
                        else:
                            await status_msg.edit_text("❌ Failed to download the sniffed link.")
            else:
                # If no link found, send screenshot to debug
                await status_msg.edit_text("❌ **Could not catch MP4 request.**\nSee screenshot below.")
                if os.path.exists(screenshot_file):
                    photo = FSInputFile(screenshot_file)
                    await message.answer_photo(photo, caption="📸 **Stuck Here**")

    except Exception as e:
        await status_msg.edit_text(f"❌ **System Error:** {e}")
    finally:
        if os.path.exists(screenshot_file): os.remove(screenshot_file)
        if os.path.exists(local_video_file): os.remove(local_video_file)

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("🕵️ **Sniffer Mode**\nSend the link. I will play the video and catch the download link.")

@dp.message(F.text)
async def handle_url(message: types.Message):
    if "http" in message.text:
        video_id = get_video_id(message.text.strip())
        if video_id:
            await extract_and_download(video_id, message)
        else:
            await message.answer("❌ No ID found.")

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