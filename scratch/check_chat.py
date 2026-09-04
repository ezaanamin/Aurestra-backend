import requests
import os
from dotenv import load_dotenv

load_dotenv()
url = os.getenv('LLM_BASE_URL', 'https://contributor-local-into-identify.trycloudflare.com')
print("URL:", url)
try:
    res = requests.get(f"{url.rstrip('/')}/api/tags", timeout=5)
    print("Status:", res.status_code)
    print("Tags:", res.json())
except Exception as e:
    print("Tags error:", e)

try:
    res = requests.post(f"{url.rstrip('/')}/api/generate", json={
        "model": "qwen2.5:3b",
        "prompt": "Hello",
        "stream": False
    }, timeout=10)
    print("Generate Status:", res.status_code)
    print("Generate Reply:", res.json())
except Exception as e:
    print("Generate error:", e)
