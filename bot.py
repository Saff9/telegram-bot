import os
import asyncio
import time
import logging
import shutil
import sqlite3
import signal
import aiohttp
from enum import Enum
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

import platform
import socket
import subprocess
import random
import string

# --- Senior Dev: God-Mode Cobalt Engine ---
# Cobalt is the only tool currently bypassing YouTube's hardware-level IP blocks.
async def get_cobalt_stream(url):
    # Public high-performance cobalt instances
    instances = [
        "https://cobalt.lucataco.com",
        "https://api.cobalt.tools",
        "https://cobalt-api.vkrdown.com",
        "https://cobalt.sh"
    ]
    random.shuffle(instances)
    
    for api in instances:
        try:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            payload = {
                "url": url,
                "videoQuality": "720", # Stable quality for Telegram
                "audioFormat": "mp3",
                "isNoTTWatermark": True
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{api}/api/json", json=payload, headers=headers, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") == "stream" or data.get("status") == "redirect":
                            return data.get("url"), data.get("filename") or "video.mp4"
        except:
            continue
    return None, None

# --- Performance: uvloop Integration ---
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
from pyrogram.errors import FloodWait, MessageNotModified
import yt_dlp
import aiosqlite
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

# --- Configuration & Constants ---
API_ID = os.getenv("API_ID")
if API_ID:
    API_ID = API_ID.strip().strip('"').strip("'")
    if API_ID.isdigit():
        API_ID = int(API_ID)

API_HASH = os.getenv("API_HASH")
if API_HASH:
    API_HASH = API_HASH.strip().strip('"').strip("'")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if BOT_TOKEN:
    BOT_TOKEN = BOT_TOKEN.strip().strip('"').strip("'")

CHANNEL_ID = os.getenv("CHANNEL_ID")
if CHANNEL_ID:
    CHANNEL_ID = CHANNEL_ID.strip().strip('"').strip("'")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
DOWNLOAD_DIR = "downloads"
DB_PATH = "bot_data.db"
MAX_CONCURRENT_TASKS = 5
MIN_DISK_GB = 2

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("YT_ENGINE_FINAL")

class JobStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    AI_PROCESSING = "ai_processing"
    PROCESSING = "processing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"

# --- UI & Formatting Utility ---
class UIUtils:
    @staticmethod
    def progress_bar(current, total, status, start_time):
        percent = (current / total) * 100 if total > 0 else 0
        elapsed = time.time() - start_time
        speed = current / elapsed if elapsed > 0 else 0
        eta = (total - current) / speed if speed > 0 else 0
        
        bar_len = 12
        filled = int(bar_len * current // total) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_len - filled)
        
        return (
            f"⚡ **{status}**\n"
            f"[{bar}] `{percent:.1f}%`\n"
            f"🚀 **Speed:** `{UIUtils.humanbytes(speed)}/s`\n"
            f"📦 **Size:** `{UIUtils.humanbytes(current)}` / `{UIUtils.humanbytes(total)}`\n"
            f"⏳ **Time Left:** `{UIUtils.time_formatter(int(eta))}`"
        )

    @staticmethod
    def humanbytes(size):
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024: return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"

    @staticmethod
    def time_formatter(seconds):
        m, s = divmod(max(0, seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")

    @staticmethod
    def start_keyboard():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Stats", callback_data="status"), InlineKeyboardButton("📖 Help", callback_data="help")]
        ])

# --- Database Manager: Thread-Safe State ---
class DatabaseManager:
    def __init__(self, path):
        self.path = path
        self.db = None

    async def init(self):
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, url TEXT, status TEXT, chat_id INTEGER, msg_id INTEGER, created_at TIMESTAMP)")
        await self.db.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')
        await self.db.commit()

    async def save_cookies(self, content: str):
        await self.db.execute('INSERT OR REPLACE INTO config (key, value) VALUES ("cookies", ?)', (content,))
        await self.db.commit()

    async def get_cookies(self) -> Optional[str]:
        async with self.db.execute('SELECT value FROM config WHERE key = "cookies"') as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def add_job(self, url, cid, mid) -> int:
        c = await self.db.execute("INSERT INTO jobs (url, status, chat_id, msg_id, created_at) VALUES (?, ?, ?, ?, ?)", (url, JobStatus.PENDING.value, cid, mid, datetime.now()))
        await self.db.commit()
        return c.lastrowid

    async def get_job_by_url(self, url):
        c = await self.db.execute("SELECT status FROM jobs WHERE url = ? AND status != 'failed' LIMIT 1", (url,))
        return await c.fetchone()

    async def update_status(self, jid, status):
        await self.db.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, jid))
        await self.db.commit()

    async def get_stats(self):
        stats = {}
        for s in JobStatus:
            c = await self.db.execute("SELECT COUNT(*) FROM jobs WHERE status = ?", (s.value,))
            row = await c.fetchone()
            stats[s.value] = row[0]
        return stats

# --- AI Manager: OpenRouter ---
class LLMManager:
    def __init__(self, key): self.key = key
    async def generate_metadata(self, title):
        if not self.key: return title, f"🎥 **{title}**"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post("https://openrouter.ai/api/v1/chat/completions", 
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={"model": "google/gemini-2.0-flash-001", "messages": [{"role": "user", "content": f"Title: '{title}'. Format: TITLE: [title]\nDESC: [description]"}]}) as r:
                    if r.status == 200:
                        content = (await r.json())['choices'][0]['message']['content']
                        t, d = title, ""
                        for l in content.split('\n'):
                            if l.startswith("TITLE:"): t = l.replace("TITLE:", "").strip()
                            if l.startswith("DESC:"): d = l.replace("DESC:", "").strip()
                        return t, f"🌟 **{t}**\n\n📝 {d}"
        except: pass
        return title, f"🎥 **{title}**"

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
            try: await self._process(jid, url, msg)
            except Exception as e:
                logger.exception(f"Job #{jid} failed")
                try: await msg.edit_text(f"❌ **Job Failed**\n`{str(e)[:150]}`")
                except: pass
                await self.db.update_status(jid, JobStatus.FAILED.value)
            finally: self.queue.task_done()

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

            # --- God-Mode Cobalt Extraction ---
            # This completely ignores local IP blocks by using a proxy-api network
            info = None
            stream_url, filename = await get_cobalt_stream(url)
            
            if stream_url:
                logger.info("💎 Cobalt Proxy Stream acquired. Downloading directly...")
                v_path = f"{DOWNLOAD_DIR}/{jid}_{filename}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(stream_url, timeout=300) as resp:
                        if resp.status == 200:
                            with open(v_path, 'wb') as f:
                                f.write(await resp.read())
                            info = {'title': filename, 'ext': 'mp4'} # Mock info for next steps
            
            # Step 2: Fallback to yt-dlp with Cookies if Cobalt fails
            if not info:
                logger.warning("⚠️ Cobalt failed. Falling back to yt-dlp with cookies...")
                ydl_opts = {
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s',
                    'quiet': True, 'no_warnings': True, 'nocheckcertificate': True,
                    'progress_hooks': [dl_hook], 'retries': 3,
                    'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
                }
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                except Exception as e:
                    raise Exception(f"All extraction methods failed. YouTube has successfully blocked this IP and session.\nError: {str(e)[:100]}")


                v_path = ydl.prepare_filename(info)
                if not os.path.exists(v_path):
                    base = os.path.splitext(v_path)[0]
                    for e in ['.mp4', '.mkv', '.webm']:
                        if os.path.exists(base + e): v_path = base + e; break

            # 2. AI Metadata
            await self.db.update_status(jid, JobStatus.AI_PROCESSING.value)
            await msg.edit_text("🧠 **AI Enhancing Metadata...**")
            ai_title, ai_caption = await self.llm.generate_metadata(info.get('title', 'Unknown'))

            # 3. Processing
            await self.db.update_status(jid, JobStatus.PROCESSING.value)
            t_path = f"{v_path}.jpg"
            proc = await asyncio.create_subprocess_exec('ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', v_path, '-ss', '1', '-vframes', '1', t_path, '-y')
            await proc.wait()
            if os.path.exists(t_path):
                with Image.open(t_path) as img: img.thumbnail((320, 320)); img.save(t_path, "JPEG")

            # 4. Upload
            await self.db.update_status(jid, JobStatus.UPLOADING.value)
            up_start = time.time()
            last_upd_up = 0

            async def up_prog(cur, tot):
                nonlocal last_upd_up
                if time.time() - last_upd_up > 4:
                    prog_text = UIUtils.progress_bar(cur, tot, "Syncing to Channel", up_start)
                    try: await msg.edit_text(prog_text)
                    except: pass
                    last_upd_up = time.time()

            # Metadata
            parsed = extractMetadata(createParser(v_path))
            d, w, h = 0, 0, 0
            if parsed:
                d = int(parsed.get("duration").seconds) if parsed.has("duration") else 0
                w = int(parsed.get("width")) if parsed.has("width") else 0
                h = int(parsed.get("height")) if parsed.has("height") else 0

            await self.bot.send_video(chat_id=CHANNEL_ID, video=v_path, thumb=t_path if os.path.exists(t_path) else None, duration=d, width=w, height=h, caption=ai_caption, progress=up_prog, supports_streaming=True)
            await self.db.update_status(jid, JobStatus.COMPLETED.value)
            await msg.edit_text(f"✅ **Job #{jid} Complete**\n💎 **Title:** {ai_title}\n✨ HD Sync Success.")
        finally:
            for p in [v_path, t_path]:
                if p and os.path.exists(p): os.remove(p)

# --- Handlers ---
app = Client("FINAL_ENGINE", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=100)
db_mgr = DatabaseManager(DB_PATH)
llm_mgr = LLMManager(OPENROUTER_API_KEY)
engine = YouTubeEngineFinal(app, db_mgr, llm_mgr)

@app.on_message(filters.command("start") & filters.private)
async def start(c, m): await m.reply_text("👋 **YT-Engine Final v4.0**\nOnline and Ready.", reply_markup=UIUtils.start_keyboard())

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
        await m.reply_text("✅ **Cookies Saved to Database!**\nThey will now persist even if the bot restarts on Render.")
    else:
        await m.reply_text("❓ Please upload a file named `cookies.txt` to update bot cookies.")

@app.on_message(filters.regex(r"(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/.+") & filters.private)
async def handle(c, m):
    # Duplicate Check
    existing = await db_mgr.get_job_by_url(m.text)
    if existing: return await m.reply_text("⚠️ This video is already in queue or processed.")
    
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
        
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    await db_mgr.init()
    for i in range(MAX_CONCURRENT_TASKS): asyncio.create_task(engine.worker(i))
    
    # 3. Start Pyrogram
    try:
        await app.start()
        await app.set_bot_commands([BotCommand("start", "Main Menu"), BotCommand("status", "System Stats")])
    except Exception as e:
        logger.error(f"Failed to start Pyrogram: {e}")
        logger.error("Your API_ID or API_HASH might still be invalid. Please make sure you are NOT using someone else's or putting random text.")
        # We don't exit here so the Render web server stays alive to show the error in logs instead of endless restarts
    
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
    for _ in range(MAX_CONCURRENT_TASKS): await engine.queue.put(None)
    await app.stop()

if __name__ == "__main__": 
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
