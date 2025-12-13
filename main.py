import os
import asyncio
import logging
import random
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
    return web.Response(text="Recording Bot Running")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# --- HELPER ---
def get_video_id(url):
    parsed = urlparse(url)
    path_parts = parsed.path.split('/')
    for part in path_parts:
        if 8 < len(part) < 20 and part.isalnum():
            return part
    if parsed.fragment: return parsed.fragment
    return None

# --- BROWSER ENGINE: VIDEO RECORDING MODE ---
async def extract_and_download(video_id, message):
    status_msg = await message.answer(f"🎥 **Recording Session...**\nTarget ID: `{video_id}`")
    
    embed_url = f"https://streama2z.pro/e/{video_id}"
    local_video_file = f"{video_id}.mp4"
    recording_path = None # Will store path to recorded .webm
    found_url = None

    try:
        async with async_playwright() as p:
            # 1. Launch Browser
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            
            # 2. Enable Video Recording
            # We save videos to a folder named "recordings"
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                record_video_dir="recordings/" 
            )
            
            page = await context.new_page()
            
            # Anti-detection script
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            # --- NETWORK SNIFFER ---
            async def handle_request(request):
                nonlocal found_url
                # Look for MP4 or M3U8 (sometimes they use HLS)
                if ("streama2z" in request.url) and (".mp4" in request.url or ".m3u8" in request.url):
                    found_url = request.url
                    logger.info(f"SNIFFED: {found_url}")

            page.on("request", handle_request)
            # ------------------------

            await status_msg.edit_text("⏳ **Loading Page...**")
            
            try:
                await page.goto(embed_url, referer="https://smartkhabrinews.com/")
                
                # Wait & Wiggle Mouse (to show in video)
                for _ in range(5):
                    await page.mouse.move(random.randint(100, 1000), random.randint(100, 600))
                    await page.wait_for_timeout(1000)

                # CLICK LOGIC
                await status_msg.edit_text("▶️ **Clicking Play...**")
                
                # 1. Try clicking the big play button class if it exists
                if await page.query_selector(".jw-display-icon-container"):
                     await page.click(".jw-display-icon-container")
                # 2. Fallback: Click Dead Center
                else:
                    await page.mouse.click(640, 360)

                # Wait for video to start loading
                await page.wait_for_timeout(5000)

            except Exception as e:
                logger.error(f"Browser Error: {e}")

            # Close context to save the video file
            await context.close() 
            
            # Find the recorded video file (Playwright generates random names)
            # We look in the 'recordings' folder
            files = os.listdir("recordings")
            if files:
                recording_path = os.path.join("recordings", files[0])

            await browser.close()

            # --- SEND RECORDING TO USER ---
            await status_msg.edit_text("📤 **Sending Session Recording...**")
            if recording_path and os.path.exists(recording_path):
                vid = FSInputFile(recording_path, filename="bot_view.webm")
                await message.answer_video(vid, caption="👀 **This is what the bot saw.**")
                
                # Clean up recording
                os.remove(recording_path)

            # --- PROCESS RESULT ---
            if found_url:
                await message.answer(f"✅ **Link Found!**\n`{found_url}`\n\nAttempting Download...")
                
                # Download logic
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": embed_url 
                }
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(found_url) as resp:
                        if resp.status == 200:
                            f = await aiofiles.open(local_video_file, mode='wb')
                            await f.write(await resp.read())
                            await f.close()
                            
                            vid_file = FSInputFile(local_video_file)
                            await message.answer_video(vid_file, caption="✅ **Final Video**")
                            os.remove(local_video_file)
                        else:
                            await message.answer("❌ Download Link Expired/Blocked.")
            else:
                await message.answer("❌ **Still could not sniff the link.** Check the video above to see why!")

    except Exception as e:
        await status_msg.edit_text(f"❌ **System Error:** {e}")
        # Cleanup
        if recording_path and os.path.exists(recording_path): os.remove(recording_path)
        if os.path.exists(local_video_file): os.remove(local_video_file)

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("🎥 **Debug Mode**\nSend link to see a video of the bot's attempt.")

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
    if not os.path.exists("recordings"):
        os.makedirs("recordings")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass