import time
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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
