# 🏗️ YT-Engine Pro: Enterprise YouTube Synchronizer

A high-availability, stateful, and resilient engineering solution for synchronizing massive YouTube content (including 2h+ 4K videos) to Telegram channels. Built with a focus on system reliability and long-term production stability.

---

## 🛠️ Engineering Architecture

### 🧠 AI-Powered Metadata
- **OpenRouter Integration**: Automatically generates professional, catchy titles and engaging descriptions using models like Gemini 2.0.
- **Enhanced Presentation**: Transform boring YouTube titles into viral-ready Telegram captions.

### 🗄️ Stateful Persistence
- **SQLite Backend**: Every request is logged in a persistent database. If the bot crashes, the job history is preserved.
- **Job State Machine**: Tasks transition through formal states (`PENDING` → `DOWNLOADING` → `PROCESSING` → `UPLOADING` → `COMPLETED`).

### 🩺 System Reliability
- **Health Monitoring**: Real-time disk space checks prevent the bot from crashing during massive downloads.
- **Graceful Shutdown**: Handles SIGINT/SIGTERM signals to ensure that ongoing tasks are handled appropriately before exit.
- **Exponential Error Handling**: Sophisticated retry logic for transient YouTube and Telegram API failures.

### ⚡ Performance Engineering
- **Asynchronous Worker Pool**: A multi-worker architecture that allows 5+ simultaneous 2GB video pipelines.
- **uvloop & MTProto**: Optimized IO loop and high-speed Telegram protocol integration.
- **Local Bot API Server**: Integrated local server to bypass standard API bottlenecks and handle ultra-long content.

---

## 🛠️ Technical Setup

### 1. Requirements
- **Hardware**: At least 1GB RAM recommended (for HD video processing).
- **Software**: Docker & Docker Compose (Recommended) OR Python 3.10+ with FFmpeg.

### 2. Environment Configuration
Create a `.env` file in the root directory:

```env
# --- Telegram Credentials ---
# Get these from https://my.telegram.org
API_ID=1234567
API_HASH=abcdef1234567890abcdef1234567890

# Get from @BotFather
BOT_TOKEN=123456789:ABCDefghIJKLmnopQRSTuvwxYZ

# --- Channel Configuration ---
# The ID of the channel where videos will be posted (e.g. -100123456789)
CHANNEL_ID=-100123456789
```

### 3. Deployment with Docker (Recommended)
```bash
# Build and start in background
docker-compose up -d --build

# View logs
docker-compose logs -f
```

---

## 📖 Detailed Documentation

### Core Logic (`bot.py`)
- **`YouTubeProcessor`**: The main engine that coordinates downloading, metadata extraction, and uploading.
- **`worker()`**: An asynchronous background loop that consumes tasks from the queue.
- **`get_metadata()`**: Uses `hachoir` to parse video headers and provide Telegram with the exact dimensions and duration.

### Scaling & Optimization
- To handle more simultaneous downloads, increase `MAX_CONCURRENT_TASKS` in `bot.py`.
- For heavy workloads, ensure your VPS has high outbound bandwidth.

### Troubleshooting
- **Error: FFmpeg not found**: If running locally, ensure FFmpeg is in your system PATH. In Docker, this is handled automatically.
- **Slow Uploads**: Ensure your `API_ID` and `API_HASH` are correct; using the Bot API alone is much slower than this MTProto implementation.
- **YouTube 403 Forbidden**: YouTube may occasionally rate-limit your IP. The bot will retry, but consider using a proxy if this persists.

---

## 🤝 Contributing
For custom modifications or feature requests, please refer to the source code docstrings.

**Author**: Antigravity AI  
**Version**: 2.0.0 (Production Stable)
