import os
import logging
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile

# --- CONFIGURATION ---
TOKEN = os.environ.get("BOT_TOKEN") 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- WEB SERVER ---
async def health_check(request):
    return web.Response(text="Diagnostic Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# --- DIAGNOSTIC LOGIC ---
async def debug_link(url, message):
    status_msg = await message.answer("🕵️ **Investigating Link...**\nConnecting as a browser...")
    
    # Headers to mimic a browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        # We purposely leave Referer empty to see what happens, or you can set it to smartkhabri
        "Referer": "https://smartkhabrinews.com/" 
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, allow_redirects=True) as resp:
                
                # 1. Get Status Code
                status_code = resp.status
                
                # 2. Read Content
                content_type = resp.headers.get('Content-Type', '')
                try:
                    text_content = await resp.text()
                except:
                    text_content = "Binary Data (Video File) - Cannot read as text."

                # 3. Report to User
                report = (
                    f"📊 **Diagnostic Report**\n"
                    f"🔗 **URL:** `{url}`\n"
                    f"🔢 **Status Code:** `{status_code}`\n"
                    f"📄 **Content-Type:** `{content_type}`\n\n"
                    f"**Analysis:**\n"
                )

                if status_code == 200:
                    report += "✅ Connection Successful. The server allowed the bot."
                elif status_code == 403:
                    report += "❌ **403 Forbidden.** The server blocked the bot (Anti-Bot/Cloudflare)."
                elif status_code == 404:
                    report += "❌ **404 Not Found.** The file does not exist at this link."
                else:
                    report += f"⚠️ Unknown Status: {status_code}"

                await status_msg.edit_text(report)

                # 4. Create a 'Screenshot' (HTML File)
                # If it's an error page, we save the HTML so you can open it and see the error.
                if "text" in content_type or "html" in content_type:
                    filename = "what_bot_saw.html"
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(f"<!-- Source URL: {url} -->\n")
                        f.write(text_content)
                    
                    # Send the file
                    file = FSInputFile(filename)
                    await message.answer_document(
                        file, 
                        caption="📂 **Open this HTML file** in your browser to see exactly what the bot saw."
                    )
                    os.remove(filename)

    except Exception as e:
        await status_msg.edit_text(f"❌ **Connection Error:** {e}")

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("👨‍💻 **Debug Mode**\nSend me the `streama2z` link, and I will tell you what the server says.")

@dp.message(F.text)
async def handle_url(message: types.Message):
    if "http" in message.text:
        await debug_link(message.text.strip(), message)
    else:
        await message.answer("❌ Send a valid link.")

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