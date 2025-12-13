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
    return web.Response(text="Mobile Bot Running")

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

# --- BROWSER ENGINE: MOBILE MODE ---
async def extract_and_download(video_id, message):
    status_msg = await message.answer(f"📱 **Mobile Mode Active...**\nTarget ID: `{video_id}`")
    
    embed_url = f"https://streama2z.pro/e/{video_id}"
    local_video_file = f"{video_id}.mp4"
    recording_path = None
    found_url = None

    try:
        async with async_playwright() as p:
            # 1. Setup Mobile Emulation (Pixel 5)
            # This makes the bot look EXACTLY like your phone
            pixel_5 = p.devices['Pixel 5']
            
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            
            # Create Context with Video Recording
            context = await browser.new_context(
                **pixel_5, # Apply phone settings
                record_video_dir="recordings/",
                user_agent="Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36"
            )
            
            page = await context.new_page()
            
            # Anti-detection
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            # --- NETWORK SNIFFER ---
            async def handle_request(request):
                nonlocal found_url
                # Look for the video file request
                if ("streama2z" in request.url) and (".mp4" in request.url):
                    found_url = request.url
                    logger.info(f"SNIFFED: {found_url}")

            page.on("request", handle_request)
            # ------------------------

            await status_msg.edit_text("⏳ **Loading Page (Mobile View)...**")
            
            try:
                await page.goto(embed_url, referer="https://smartkhabrinews.com/")
                
                # Wait for initial load
                await page.wait_for_timeout(4000)

                # --- AD BYPASS LOGIC ---
                await status_msg.edit_text("👆 **Tapping Play (Attempt 1)...**")
                
                # Tap the center of the phone screen
                await page.mouse.click(196, 425) 
                
                # Wait 2 seconds (This usually triggers the popup/ad)
                await page.wait_for_timeout(2000)
                
                # Tap AGAIN (This usually starts the video after ad is cleared)
                await status_msg.edit_text("👆 **Tapping Play (Attempt 2)...**")
                await page.mouse.click(196, 425)
                
                # Wait for video network request
                await page.wait_for_timeout(5000)

            except Exception as e:
                logger.error(f"Browser Error: {e}")

            await context.close() 
            
            # Save recording for debugging
            files = os.listdir("recordings")
            if files:
                recording_path = os.path.join("recordings", files[0])

            await browser.close()

            # --- RESULT ---
            if found_url:
                await status_msg.edit_text(f"✅ **Link Sniffed!**\n`{found_url}`\n\nDownloading...")
                
                # Download logic
                headers = {
                    "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36",
                    "Referer": embed_url 
                }
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(found_url) as resp:
                        if resp.status == 200:
                            f = await aiofiles.open(local_video_file, mode='wb')
                            await f.write(await resp.read())
                            await f.close()
                            
                            vid_file = FSInputFile(local_video_file)
                            await message.answer_video(vid_file, caption="✅ **Downloaded (Mobile Mode)**")
                            os.remove(local_video_file)
                        else:
                            await message.answer("❌ Download Link Expired/Blocked.")
            else:
                # If failed, send video to see what happened on the phone screen
                if recording_path:
                    vid = FSInputFile(recording_path, filename="mobile_view.webm")
                    await message.answer_video(vid, caption="❌ **Failed.** Here is the Mobile View recording.")

            # Cleanup recording
            if recording_path and os.path.exists(recording_path):
                os.remove(recording_path)

    except Exception as e:
        await status_msg.edit_text(f"❌ **System Error:** {e}")
        if recording_path and os.path.exists(recording_path): os.remove(recording_path)
        if os.path.exists(local_video_file): os.remove(local_video_file)

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("📱 **Mobile Debug Mode**\nSend link.")

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