import os
import shutil
import random
import aiohttp
import asyncio
import logging
import yt_dlp

logger = logging.getLogger("YT_ENGINE_DOWNLOADER")

# --- Cobalt Stream Extraction ---
async def get_cobalt_stream(url):
    instances = ["https://cobalt.lucataco.com", "https://api.cobalt.tools", "https://cobalt-api.vkrdown.com"]
    random.shuffle(instances)
    for api in instances:
        try:
            async with aiohttp.ClientSession() as session:
                payload = {"url": url, "videoQuality": "720", "audioFormat": "mp3", "isNoTTWatermark": True}
                async with session.post(f"{api}/api/json", json=payload, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("url"): 
                            return data.get("url"), data.get("filename") or "video.mp4"
        except: 
            continue
    return None, None

# --- Socks5 Proxy Scraper ---
async def get_fresh_proxies():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=5000&country=all&ssl=all&anonymity=all") as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return [f"socks5://{p.strip()}" for p in text.splitlines() if p.strip()]
    except: 
        pass
    return []

# --- Consolidated Video Downloader ---
async def download_video(url, jid, dl_hook, download_dir):
    info = None
    v_path = None

    # 1. Cobalt (Fastest)
    stream_url, filename = await get_cobalt_stream(url)
    if stream_url:
        logger.info("💎 Cobalt Stream acquired.")
        v_path = f"{download_dir}/{jid}_video.mp4"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(stream_url, timeout=300) as resp:
                    if resp.status == 200:
                        with open(v_path, 'wb') as f: 
                            f.write(await resp.read())
                        info = {'title': filename or 'Video', 'ext': 'mp4'}
        except Exception as e:
            logger.warning(f"Failed downloading from Cobalt stream: {e}")
            if os.path.exists(v_path):
                os.remove(v_path)
            v_path = None

    # 2. yt-dlp with Cookies (Authenticated)
    if not info:
        logger.info("🛡️ Trying yt-dlp + Cookies...")
        aria2_available = shutil.which('aria2c') is not None
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': f'{download_dir}/%(id)s.%(ext)s',
            'quiet': True,
            'nocheckcertificate': True,
            'progress_hooks': [dl_hook],
            'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
            'extractor_args': {
                'youtube': {
                    'player_client': ['web', 'mweb', 'ios', 'android'],
                }
            },
        }
        if aria2_available:
            ydl_opts['external_downloader'] = 'aria2c'
            ydl_opts['external_downloader_args'] = ['--min-split-size=1M', '--max-connection-per-server=16', '--split=16']
        
        if ydl_opts['cookiefile']:
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                    if info:
                        v_path = ydl.prepare_filename(info)
                        logger.info("✅ Standard yt-dlp with cookies succeeded!")
            except Exception as e:
                logger.error(f"Standard yt-dlp with cookies failed: {e}", exc_info=True)

    # 2b. yt-dlp WITHOUT Cookies (Cookie-less fallback)
    if not info:
        logger.info("🛡️ Trying yt-dlp WITHOUT Cookies...")
        ydl_opts_nocookies = ydl_opts.copy()
        ydl_opts_nocookies['cookiefile'] = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts_nocookies) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                if info:
                    v_path = ydl.prepare_filename(info)
                    logger.info("✅ Standard yt-dlp WITHOUT cookies succeeded!")
        except Exception as e:
            logger.error(f"Standard yt-dlp WITHOUT cookies failed: {e}", exc_info=True)

    # 3. Proxy-Brute Force (The Hammer)
    if not info:
        logger.warning("☢️ Standard methods failed. Launching Nuclear Proxy Brute-Force...")
        proxies = await get_fresh_proxies()
        random.shuffle(proxies)
        
        for proxy in proxies[:15]:
            logger.info(f"🔄 Retrying with Proxy: {proxy}")
            
            # Create a proxy-specific copy of ydl_opts
            ydl_opts_proxy = ydl_opts.copy()
            ydl_opts_proxy['proxy'] = proxy
            
            # Disable aria2c for proxies because aria2c doesn't support socks5
            if 'external_downloader' in ydl_opts_proxy:
                del ydl_opts_proxy['external_downloader']
            if 'external_downloader_args' in ydl_opts_proxy:
                del ydl_opts_proxy['external_downloader_args']
            
            # Try with cookies if they exist
            if ydl_opts_proxy['cookiefile']:
                try:
                    with yt_dlp.YoutubeDL(ydl_opts_proxy) as ydl:
                        info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                        if info:
                            v_path = ydl.prepare_filename(info)
                            logger.info(f"✅ Proxy {proxy} with cookies succeeded!")
                            break
                except Exception as e:
                    logger.warning(f"Proxy {proxy} with cookies failed: {e}")
            
            # Try without cookies on the same proxy
            try:
                ydl_opts_nocookies = ydl_opts_proxy.copy()
                ydl_opts_nocookies['cookiefile'] = None
                with yt_dlp.YoutubeDL(ydl_opts_nocookies) as ydl:
                    info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                    if info:
                        v_path = ydl.prepare_filename(info)
                        logger.info(f"✅ Proxy {proxy} WITHOUT cookies succeeded!")
                        break
            except Exception as e:
                logger.warning(f"Proxy {proxy} WITHOUT cookies failed: {e}")
                continue

    if not info:
        raise Exception("💀 All download strategies failed. YouTube's bot-detection blocked this download. Please upload a fresh 'cookies.txt' file to verify you are not a bot.")

    # Resolve actual downloaded file path if it was changed (e.g. merged to .mkv)
    if v_path and not os.path.exists(v_path):
        base = os.path.splitext(v_path)[0]
        for e in ['.mp4', '.mkv', '.webm']:
            if os.path.exists(base + e):
                v_path = base + e
                break

    return info, v_path
