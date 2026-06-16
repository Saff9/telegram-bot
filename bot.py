import os
import asyncio
import time
import logging
import signal
import platform

if platform.system() != 'Windows':
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except ImportError:
        pass

# Fix for Pyrogram calling get_event_loop() at import time in Python 3.10+
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand

# Import modular components
from engine.config import (
    API_ID, API_HASH, BOT_TOKEN, CHANNEL_ID, OPENROUTER_API_KEY,
    DOWNLOAD_DIR, DB_PATH, MAX_CONCURRENT_TASKS, MIN_DISK_GB
)
from engine.database import DatabaseManager, JobStatus
from engine.utils import UIUtils
from engine.llm import LLMManager
from engine.downloader import download_video
from engine.processor import generate_thumbnail, extract_video_metadata

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("YT_ENGINE_FINAL")

# --- The Final Engine ---
class YouTubeEngineFinal:
    def __init__(self, bot, db, llm):
        self.bot, self.db, self.llm = bot, db, llm
        self.queue = asyncio.Queue()
        self.running = True

    async def worker(self, wid):
        while self.running:
            task = await self.queue.get()
            if not task: break
            jid, url, msg = task
            try: 
                await self._process(jid, url, msg)
            except Exception as e:
                logger.exception(f"Job #{jid} failed")
                try: 
                    await msg.edit_text(f"❌ **Job Failed**\n`{str(e)[:150]}`")
                except: 
                    pass
                await self.db.update_status(jid, JobStatus.FAILED.value)
            finally: 
                self.queue.task_done()

    async def _process(self, jid, url, msg):
        v_path, t_path = None, None
        try:
            # 1. Download
            await self.db.update_status(jid, JobStatus.DOWNLOADING.value)
            start_t = time.time()
            last_upd = 0

            def dl_hook(d):
                nonlocal last_upd
                if d['status'] == 'downloading' and time.time() - last_upd > 4:
                    prog_text = UIUtils.progress_bar(d.get('downloaded_bytes', 0), d.get('total_bytes', d.get('total_bytes_estimate', 1)), "Downloading Video", start_t)
                    asyncio.run_coroutine_threadsafe(msg.edit_text(prog_text), asyncio.get_event_loop())
                    last_upd = time.time()

            # Delegate to downloader module
            info, v_path = await download_video(url, jid, dl_hook, DOWNLOAD_DIR)

            # 2. AI Metadata
            await self.db.update_status(jid, JobStatus.AI_PROCESSING.value)
            await msg.edit_text("🧠 **AI Enhancing Metadata...**")
            ai_title, ai_caption = await self.llm.generate_metadata(info.get('title', 'Unknown'))

            # 3. Processing
            await self.db.update_status(jid, JobStatus.PROCESSING.value)
            t_path = f"{v_path}.jpg"
            await generate_thumbnail(v_path, t_path)

            # 4. Upload
            await self.db.update_status(jid, JobStatus.UPLOADING.value)
            up_start = time.time()
            last_upd_up = 0

            async def up_prog(cur, tot):
                nonlocal last_upd_up
                if time.time() - last_upd_up > 4:
                    prog_text = UIUtils.progress_bar(cur, tot, "Syncing to Channel", up_start)
                    try: 
                        await msg.edit_text(prog_text)
                    except: 
                        pass
                    last_upd_up = time.time()

            # Metadata parsing
            d, w, h = extract_video_metadata(v_path)

            await self.bot.send_video(
                chat_id=CHANNEL_ID, video=v_path, 
                thumb=t_path if os.path.exists(t_path) else None, 
                duration=d, width=w, height=h, 
                caption=ai_caption, progress=up_prog, 
                supports_streaming=True
            )
            await self.db.update_status(jid, JobStatus.COMPLETED.value)
            await msg.edit_text(f"✅ **Job #{jid} Complete**\n💎 **Title:** {ai_title}\n✨ HD Sync Success.")
        finally:
            for p in [v_path, t_path]:
                if p and os.path.exists(p): 
                    os.remove(p)

# --- Handlers ---
app = Client("FINAL_ENGINE", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=100)
db_mgr = DatabaseManager(DB_PATH)
llm_mgr = LLMManager(OPENROUTER_API_KEY)
engine = YouTubeEngineFinal(app, db_mgr, llm_mgr)

@app.on_message(filters.command("start") & filters.private)
async def start(c, m): 
    await m.reply_text("👋 **YT-Engine Final v4.0**\nOnline and Ready.", reply_markup=UIUtils.start_keyboard())

@app.on_message(filters.command("status") & filters.private)
async def status(c, m):
    stats = await db_mgr.get_stats()
    text = "📊 **Bot Statistics**\n" + "\n".join([f"• {k.capitalize()}: `{v}`" for k, v in stats.items()])
    await m.reply_text(text)

@app.on_message(filters.document & filters.private)
async def handle_cookies(c, m):
    if m.document.file_name == "cookies.txt":
        path = await m.download(file_name="cookies.txt")
        with open(path, 'r') as f:
            content = f.read()
        await db_mgr.save_cookies(content)
        # Write to disk immediately so it's active without restart
        with open("cookies.txt", "w") as f:
            f.write(content)
        await m.reply_text("✅ **Cookies Saved and Activated!**\nThey will now persist and are active immediately without restarting.")
    else:
        await m.reply_text("❓ Please upload a file named `cookies.txt` to update bot cookies.")

@app.on_message(filters.regex(r"(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/.+") & filters.private)
async def handle(c, m):
    # Duplicate Check
    existing = await db_mgr.get_job_by_url(m.text)
    if existing: 
        return await m.reply_text("⚠️ This video is currently being processed or downloading.")
    
    s = await m.reply_text("🔍 **Adding to Queue...**")
    jid = await db_mgr.add_job(m.text, m.chat.id, s.id)
    await engine.queue.put((jid, m.text, s))

async def start_web_server():
    from aiohttp import web
    async def handle_ping(request):
        return web.Response(text="Bot is running!")
    
    web_app = web.Application()
    web_app.router.add_get('/', handle_ping)
    web_app.router.add_get('/health', handle_ping)
    
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Dummy web server started on port {port}")

async def main():
    # Initialize DB first
    await db_mgr.init()
    
    # 1. Load Cookies from DB (Priority) or Env
    db_cookies = await db_mgr.get_cookies()
    env_cookies = os.getenv("COOKIES_CONTENT")
    
    final_cookies = db_cookies or env_cookies
    if final_cookies:
        with open("cookies.txt", "w") as f:
            f.write(final_cookies)
        logger.info("✅ Cookies loaded and active.")

    # 2. Start Dummy Web Server for Render IMMEDIATELY
    if os.getenv("PORT"):
        await start_web_server()

    # 3. Check variables
    if not API_ID or not API_HASH:
        logger.error("API_ID or API_HASH is missing! Please get them from my.telegram.org and set them in your environment variables.")
        
    if not os.path.exists(DOWNLOAD_DIR): 
        os.makedirs(DOWNLOAD_DIR)
        
    for i in range(MAX_CONCURRENT_TASKS): 
        asyncio.create_task(engine.worker(i))
    
    # 3. Start Pyrogram and verify connection in logs
    try:
        await app.start()
        me = await app.get_me()
        logger.info(f"✅ Bot successfully connected to Telegram API as @{me.username}")
        await app.set_bot_commands([BotCommand("start", "Main Menu"), BotCommand("status", "System Stats")])
    except Exception as e:
        logger.error(f"❌ Bot failed to connect to Telegram API: {e}")
        logger.error("Your API_ID or API_HASH might still be invalid. Please make sure you are NOT using someone else's or putting random text.")
    
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: stop_event.set())
    except NotImplementedError:
        pass  # Windows does not support add_signal_handler
    
    await stop_event.wait()
    logger.warning("Graceful Shutdown...")
    engine.running = False
    for _ in range(MAX_CONCURRENT_TASKS): 
        await engine.queue.put(None)
    await app.stop()

if __name__ == "__main__": 
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
