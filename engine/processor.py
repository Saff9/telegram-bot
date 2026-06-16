import os
import asyncio
from PIL import Image
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser

async def generate_thumbnail(v_path: str, t_path: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-hide_banner', '-loglevel', 'error', 
            '-i', v_path, '-ss', '1', '-vframes', '1', t_path, '-y'
        )
        await proc.wait()
        if os.path.exists(t_path):
            with Image.open(t_path) as img:
                img.thumbnail((320, 320))
                img.save(t_path, "JPEG")
            return True
    except Exception:
        pass
    return False

def extract_video_metadata(v_path: str) -> tuple:
    d, w, h = 0, 0, 0
    try:
        parser = createParser(v_path)
        if parser:
            parsed = extractMetadata(parser)
            if parsed:
                d = int(parsed.get("duration").seconds) if parsed.has("duration") else 0
                w = int(parsed.get("width")) if parsed.has("width") else 0
                h = int(parsed.get("height")) if parsed.has("height") else 0
    except Exception:
        pass
    return d, w, h
