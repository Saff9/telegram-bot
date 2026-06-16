import aiosqlite
from enum import Enum
from datetime import datetime
from typing import Optional

class JobStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    AI_PROCESSING = "ai_processing"
    PROCESSING = "processing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"

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
