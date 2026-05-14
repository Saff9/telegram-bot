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

# --- Performance: uvloop Integration ---
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

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
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
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
        await self.db.commit()

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

            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s',
                'quiet': True, 'noprogress': True, 'concurrent_fragment_downloads': 10,
                'progress_hooks': [dl_hook], 'retries': 10, 'nocheckcertificate': True
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(url, download=True))
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

@app.on_message(filters.regex(r"(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/.+") & filters.private)
async def handle(c, m):
    # Duplicate Check
    existing = await db_mgr.get_job_by_url(m.text)
    if existing: return await m.reply_text("⚠️ This video is already in queue or processed.")
    
    s = await m.reply_text("🔍 **Adding to Queue...**")
    jid = await db_mgr.add_job(m.text, m.chat.id, s.id)
    await engine.queue.put((jid, m.text, s))

async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    await db_mgr.init()
    for i in range(MAX_CONCURRENT_TASKS): asyncio.create_task(engine.worker(i))
    await app.start()
    await app.set_bot_commands([BotCommand("start", "Main Menu"), BotCommand("status", "System Stats")])
    
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: stop_event.set())
    
    await stop_event.wait()
    logger.warning("Graceful Shutdown...")
    engine.running = False
    for _ in range(MAX_CONCURRENT_TASKS): await engine.queue.put(None)
    await app.stop()

if __name__ == "__main__": asyncio.run(main())
