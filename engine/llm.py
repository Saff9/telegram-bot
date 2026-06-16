import aiohttp

class LLMManager:
    def __init__(self, key): 
        self.key = key

    async def generate_metadata(self, title):
        if not self.key: 
            return title, f"🎥 **{title}**"
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
        except: 
            pass
        return title, f"🎥 **{title}**"
