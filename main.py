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
    return web.Response(text="Stealth Bot Running")

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

# --- BROWSER ENGINE: STEALTH MODE ---
async def extract_and_download(video_id, message):
    status_msg = await message.answer(f"🥷 **Stealth Mode Active...**\nTarget ID: `{video_id}`")
    
    embed_url = f"https://streama2z.pro/e/{video_id}"
    screenshot_file = f"debug_{video_id}.png"
    local_video_file = f"{video_id}.mp4"
    found_url = None

    try:
        async with async_playwright() as p:
            # 1. Launch with Stealth Arguments
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled", # Hides "controlled by automation"
                    "--no-sandbox",
                    "--disable-infobars"
                ]
            )
            
            # 2. Mimic a Real PC
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-US"
            )
            
            page = await context.new_page()
            
            # 3. Inject Script to Remove 'navigator.webdriver' property (Crucial for Cloudflare)
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            # --- NETWORK LISTENER ---
            async def handle_request(request):
                nonlocal found_url
                if ".mp4" in request.url and "streama2z" in request.url:
                    found_url = request.url
                    logger.info(f"SNIFFED: {found_url}")

            page.on("request", handle_request)
            # ------------------------

            await status_msg.edit_text("⏳ **Bypassing Cloudflare...**\n(Moving mouse & waiting)")
            
            try:
                # Go to page
                await page.goto(embed_url, referer="https://smartkhabrinews.com/")
                
                # 4. Human Behavior Simulation (Wait + Mouse Wiggle)
                for i in range(10):
                    # Move mouse randomly to prove we are human
                    x = random.randint(100, 700)
                    y = random.randint(100, 500)
                    await page.mouse.move(x, y)
                    await page.wait_for_timeout(1500) # Wait 1.5s between moves
                    
                    # If we found the url early, stop waiting
                    if found_url: break

            except Exception as e:
                logger.error(f"Nav Error: {e}")

            # 5. Take Screenshot of what happened after waiting
            await page.screenshot(path=screenshot_file, full_page=True)

            # 6. Click Play if URL not found yet
            if not found_url:
                await status_msg.edit_text("▶️ **Clicking Play...**")
                try:
                    # Click center
                    await page.mouse.click(640, 360)
                    await page.wait_for_timeout(4000)
                except: pass

            await browser.close()

            # --- PROCESS RESULT ---
            if found_url:
                await status_msg.edit_text("✅ **Success! Video Link Found.**\nDownloading...")
                
                # Download
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
                            
                            await status_msg.edit_text("📤 **Uploading...**")
                            vid = FSInputFile(local_video_file)
                            await message.answer_video(vid, caption="✅ **Downloaded via Stealth Mode**")
                        else:
                            await status_msg.edit_text("❌ Download Failed (Link Expired/Blocked).")
            else:
                # Send screenshot of failure
                await status_msg.edit_text("❌ **Still Stuck.** See screenshot.")
                if os.path.exists(screenshot_file):
                    photo = FSInputFile(screenshot_file)
                    await message.answer_photo(photo, caption="📸 **Current Screen**")

    except Exception as e:
        await status_msg.edit_text(f"❌ **Error:** {e}")
    finally:
        if os.path.exists(screenshot_file): os.remove(screenshot_file)
        if os.path.exists(local_video_file): os.remove(local_video_file)

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("🥷 **Stealth Mode**\nSend link to test.")

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