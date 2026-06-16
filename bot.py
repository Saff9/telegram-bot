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
        self.active_jobs = {}

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
                if jid in self.active_jobs:
                    self.active_jobs[jid].update({
                        "status": "failed",
                        "error": str(e),
                        "timestamp": time.time()
                    })
            finally: 
                self.queue.task_done()

    async def _process(self, jid, url, msg):
        v_path, t_path = None, None
        
        # Clean up old jobs if there are too many
        if len(self.active_jobs) > 50:
            sorted_jobs = sorted(self.active_jobs.items(), key=lambda x: x[1].get("timestamp", 0))
            for k, v in sorted_jobs[:-50]:
                del self.active_jobs[k]

        self.active_jobs[jid] = {
            "url": url,
            "status": "pending",
            "downloaded": 0,
            "total": 0,
            "start_time": time.time(),
            "timestamp": time.time()
        }
        
        try:
            # 1. Download
            await self.db.update_status(jid, JobStatus.DOWNLOADING.value)
            self.active_jobs[jid].update({
                "status": "downloading",
                "start_time": time.time(),
                "timestamp": time.time()
            })
            start_t = time.time()
            last_upd = 0
            
            loop = asyncio.get_running_loop()
            
            async def safe_edit(text):
                try:
                    await msg.edit_text(text)
                except Exception as e:
                    logger.warning(f"Failed to edit message for job {jid}: {e}")

            def dl_hook(d):
                nonlocal last_upd
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                self.active_jobs[jid].update({
                    "status": "downloading",
                    "downloaded": downloaded,
                    "total": total,
                    "timestamp": time.time()
                })
                
                if d['status'] == 'downloading' and time.time() - last_upd > 4:
                    prog_text = UIUtils.progress_bar(downloaded, total if total > 0 else 1, "Downloading Video", start_t)
                    asyncio.run_coroutine_threadsafe(safe_edit(prog_text), loop)
                    last_upd = time.time()

            # Delegate to downloader module
            info, v_path = await download_video(url, jid, dl_hook, DOWNLOAD_DIR)

            # 2. AI Metadata
            await self.db.update_status(jid, JobStatus.AI_PROCESSING.value)
            self.active_jobs[jid].update({
                "status": "ai_processing",
                "timestamp": time.time()
            })
            await safe_edit("🧠 **AI Enhancing Metadata...**")
            ai_title, ai_caption = await self.llm.generate_metadata(info.get('title', 'Unknown'))

            # 3. Processing
            await self.db.update_status(jid, JobStatus.PROCESSING.value)
            self.active_jobs[jid].update({
                "status": "processing",
                "timestamp": time.time()
            })
            t_path = f"{v_path}.jpg"
            await generate_thumbnail(v_path, t_path)

            # 4. Upload
            await self.db.update_status(jid, JobStatus.UPLOADING.value)
            self.active_jobs[jid].update({
                "status": "uploading",
                "downloaded": 0,
                "total": 0,
                "start_time": time.time(),
                "timestamp": time.time()
            })
            up_start = time.time()
            last_upd_up = 0

            async def up_prog(cur, tot):
                nonlocal last_upd_up
                self.active_jobs[jid].update({
                    "downloaded": cur,
                    "total": tot,
                    "timestamp": time.time()
                })
                if time.time() - last_upd_up > 4:
                    prog_text = UIUtils.progress_bar(cur, tot, "Syncing to Channel", up_start)
                    try:
                        await msg.edit_text(prog_text)
                    except Exception as e:
                        logger.warning(f"Failed to edit upload progress for job {jid}: {e}")
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
            self.active_jobs[jid].update({
                "status": "completed",
                "timestamp": time.time()
            })
            await safe_edit(f"✅ **Job #{jid} Complete**\n💎 **Title:** {ai_title}\n✨ HD Sync Success.")
        except Exception as e:
            self.active_jobs[jid].update({
                "status": "failed",
                "error": str(e),
                "timestamp": time.time()
            })
            raise e
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

def get_web_ui():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YT-Engine Live Control Room</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(20, 26, 43, 0.6);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-color: #f3f4f6;
            --text-muted: #9ca3af;
            --primary: #6366f1;
            --primary-glow: rgba(99, 102, 241, 0.4);
            --success: #10b981;
            --success-glow: rgba(16, 185, 129, 0.3);
            --danger: #ef4444;
            --warning: #f59e0b;
            --info: #3b82f6;
            --purple: #8b5cf6;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Outfit', sans-serif;
        }

        body {
            background-color: var(--bg-color);
            background-image: 
                radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 0%, rgba(139, 92, 246, 0.15) 0px, transparent 50%);
            background-attachment: fixed;
            color: var(--text-color);
            min-height: 100vh;
            padding: 2rem 1rem;
            display: flex;
            flex-direction: column;
            align-items: center;
        }

        .container {
            width: 100%;
            max-width: 900px;
            display: flex;
            flex-direction: column;
            gap: 2rem;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--border-color);
        }

        .logo-section {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .logo-dot {
            width: 12px;
            height: 12px;
            background-color: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 12px var(--success);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { transform: scale(1); box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }

        h1 {
            font-size: 1.75rem;
            font-weight: 700;
            letter-spacing: -0.025em;
            background: linear-gradient(to right, #a5b4fc, #c084fc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .version {
            font-size: 0.8rem;
            background: rgba(255, 255, 255, 0.05);
            padding: 0.25rem 0.6rem;
            border-radius: 9999px;
            border: 1px solid var(--border-color);
            color: var(--text-muted);
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 1rem;
            width: 100%;
        }

        .stat-card {
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 1rem;
            padding: 1.25rem;
            text-align: center;
            transition: transform 0.2s;
        }

        .stat-card:hover {
            transform: translateY(-2px);
        }

        .stat-val {
            font-size: 2rem;
            font-weight: 700;
            margin-top: 0.25rem;
        }

        .stat-label {
            font-size: 0.85rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .section-title {
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 1rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .jobs-container {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .job-card {
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 1rem;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 1rem;
            position: relative;
            overflow: hidden;
            transition: border-color 0.3s;
        }

        .job-card.downloading { border-color: rgba(99, 102, 241, 0.4); }
        .job-card.completed { border-color: rgba(16, 185, 129, 0.3); }
        .job-card.failed { border-color: rgba(239, 68, 68, 0.3); }

        .job-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 1rem;
        }

        .job-title-sec {
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
            flex: 1;
            min-width: 0;
        }

        .job-id {
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--primary);
        }

        .job-url {
            font-size: 0.95rem;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            color: var(--text-color);
            text-decoration: none;
        }

        .job-url:hover {
            text-decoration: underline;
        }

        .badge {
            font-size: 0.75rem;
            font-weight: 600;
            padding: 0.3rem 0.75rem;
            border-radius: 9999px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .badge.pending { background: rgba(255, 255, 255, 0.05); color: var(--text-muted); }
        .badge.downloading { background: rgba(59, 130, 246, 0.15); color: var(--info); }
        .badge.ai_processing { background: rgba(139, 92, 246, 0.15); color: var(--purple); }
        .badge.processing { background: rgba(99, 102, 241, 0.15); color: var(--primary); }
        .badge.uploading { background: rgba(6, 182, 212, 0.15); color: #06b6d4; }
        .badge.completed { background: rgba(16, 185, 129, 0.15); color: var(--success); }
        .badge.failed { background: rgba(239, 68, 68, 0.15); color: var(--danger); }

        .progress-container {
            width: 100%;
            height: 8px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 9999px;
            overflow: hidden;
        }

        .progress-bar {
            height: 100%;
            width: 0%;
            background: linear-gradient(to right, var(--primary), var(--purple));
            border-radius: 9999px;
            transition: width 0.3s ease;
            box-shadow: 0 0 8px var(--primary-glow);
        }

        .progress-bar.success {
            background: var(--success);
            box-shadow: 0 0 8px var(--success-glow);
        }

        .progress-bar.danger {
            background: var(--danger);
        }

        .job-details {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 1rem;
            font-size: 0.85rem;
        }

        .detail-item {
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }

        .detail-label {
            color: var(--text-muted);
            font-size: 0.75rem;
            text-transform: uppercase;
        }

        .detail-val {
            font-weight: 500;
        }

        .error-message {
            background: rgba(239, 68, 68, 0.08);
            border: 1px solid rgba(239, 68, 68, 0.2);
            border-radius: 0.5rem;
            padding: 0.75rem;
            font-size: 0.85rem;
            color: #fca5a5;
            word-break: break-all;
        }

        .empty-state {
            text-align: center;
            padding: 4rem 2rem;
            background: var(--card-bg);
            border-radius: 1rem;
            border: 1px dashed var(--border-color);
            color: var(--text-muted);
        }

        .empty-icon {
            font-size: 3rem;
            margin-bottom: 1rem;
        }

        footer {
            margin-top: auto;
            padding-top: 3rem;
            font-size: 0.85rem;
            color: var(--text-muted);
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-section">
                <div class="logo-dot"></div>
                <h1>YT-Engine Control Room</h1>
                <span class="version">v4.0 Live</span>
            </div>
            <div>
                <a href="https://t.me/vidup54_bot" target="_blank" style="color: var(--primary); text-decoration: none; font-weight: 600; font-size: 0.95rem;">Open Telegram Bot ↗</a>
            </div>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Active Jobs</div>
                <div class="stat-val" id="stat-active">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Pending Queue</div>
                <div class="stat-val" id="stat-pending">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-val" id="stat-completed" style="color: var(--success);">0</div>
                <div class="stat-label" style="margin-top: 0.25rem;">Completed</div>
            </div>
            <div class="stat-card">
                <div class="stat-val" id="stat-failed" style="color: var(--danger);">0</div>
                <div class="stat-label" style="margin-top: 0.25rem;">Failed</div>
            </div>
        </div>

        <div>
            <h2 class="section-title">⚡ Live Download Queue</h2>
            <div id="jobs-list" class="jobs-container">
                <div class="empty-state">
                    <div class="empty-icon">📥</div>
                    <p>No active download tasks. Send a video link to the Telegram Bot to get started.</p>
                </div>
            </div>
        </div>

        <footer>
            <p>© 2026 YT-Engine. Serving high-speed content delivery.</p>
        </footer>
    </div>

    <script>
        async function fetchJobs() {
            try {
                const response = await fetch('/api/jobs');
                if (!response.ok) throw new Error('Network response was not ok');
                const jobs = await response.json();
                updateUI(jobs);
            } catch (error) {
                console.error('Error fetching jobs:', error);
            }
        }

        function updateUI(jobs) {
            let active = 0;
            let pending = 0;
            let completed = 0;
            let failed = 0;

            jobs.forEach(job => {
                if (['downloading', 'ai_processing', 'processing', 'uploading'].includes(job.status)) active++;
                else if (job.status === 'pending') pending++;
                else if (job.status === 'completed') completed++;
                else if (job.status === 'failed') failed++;
            });

            document.getElementById('stat-active').innerText = active;
            document.getElementById('stat-pending').innerText = pending;
            document.getElementById('stat-completed').innerText = completed;
            document.getElementById('stat-failed').innerText = failed;

            const listContainer = document.getElementById('jobs-list');
            if (jobs.length === 0) {
                listContainer.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-icon">📥</div>
                        <p>No active download tasks. Send a video link to the Telegram Bot to get started.</p>
                    </div>
                `;
                return;
            }

            let html = '';
            jobs.forEach(job => {
                let badgeClass = job.status;
                let statusText = job.status.replace('_', ' ');
                let progBarClass = '';
                if (job.status === 'completed') progBarClass = 'success';
                else if (job.status === 'failed') progBarClass = 'danger';

                let errorHtml = '';
                if (job.status === 'failed' && job.error) {
                    errorHtml = '<div class="error-message">⚠️ ' + escapeHTML(job.error) + '</div>';
                }

                let percent = job.percent.toFixed(1);

                html += `
                    <div class="job-card ${job.status}">
                        <div class="job-header">
                            <div class="job-title-sec">
                                <span class="job-id">Job #${job.id}</span>
                                <a href="${job.url}" target="_blank" class="job-url">${escapeHTML(job.url)}</a>
                            </div>
                            <span class="badge ${badgeClass}">${statusText}</span>
                        </div>

                        ${['downloading', 'uploading'].includes(job.status) || job.status === 'completed' || job.status === 'failed' ? `
                            <div class="progress-container">
                                <div class="progress-bar ${progBarClass}" style="width: ${percent}%"></div>
                            </div>
                        ` : ''}

                        <div class="job-details">
                            <div class="detail-item">
                                <span class="detail-label">Progress</span>
                                <span class="detail-val">${percent}%</span>
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">Speed</span>
                                <span class="detail-val">${job.speed_str}</span>
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">Size / ETA</span>
                                <span class="detail-val">${job.downloaded_str} of ${job.total_str} (${job.eta_str})</span>
                            </div>
                        </div>

                        ${errorHtml}
                    </div>
                `;
            });

            listContainer.innerHTML = html;
        }

        function escapeHTML(str) {
            return str.replace(/[&<>'"]/g, 
                tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
            );
        }

        setInterval(fetchJobs, 1000);
        fetchJobs();
    </script>
</body>
</html>
"""

async def start_web_server(engine):
    from aiohttp import web
    
    async def handle_home(request):
        return web.Response(text=get_web_ui(), content_type='text/html')
        
    async def handle_api_jobs(request):
        jobs_data = []
        for jid, job in engine.active_jobs.items():
            current = job.get("downloaded", 0)
            total = job.get("total", 0)
            status = job.get("status", "pending")
            start_time = job.get("start_time", time.time())
            
            elapsed = time.time() - start_time
            speed = current / elapsed if elapsed > 0 else 0
            eta = (total - current) / speed if (speed > 0 and total > current) else 0
            
            jobs_data.append({
                "id": jid,
                "url": job.get("url"),
                "status": status,
                "downloaded": current,
                "total": total,
                "downloaded_str": UIUtils.humanbytes(current),
                "total_str": UIUtils.humanbytes(total),
                "percent": (current / total * 100) if total > 0 else 0,
                "speed_str": f"{UIUtils.humanbytes(speed)}/s" if speed > 0 else "0 B/s",
                "eta_str": UIUtils.time_formatter(int(eta)) if eta > 0 else "N/A",
                "error": job.get("error", ""),
                "timestamp": job.get("timestamp", time.time())
            })
            
        jobs_data.sort(key=lambda x: x["id"], reverse=True)
        return web.json_response(jobs_data)

    async def handle_ping(request):
        return web.Response(text="Bot is running!")
    
    web_app = web.Application()
    web_app.router.add_get('/', handle_home)
    web_app.router.add_get('/api/jobs', handle_api_jobs)
    web_app.router.add_get('/health', handle_ping)
    
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server started on port {port}")

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
    await start_web_server(engine)

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
