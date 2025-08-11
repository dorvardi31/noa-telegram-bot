
import os, requests

TOKEN = os.getenv("TG_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "")
WEBHOOK_URL = f"{BASE_URL}/webhook"

if not TOKEN or not BASE_URL:
    raise SystemExit("Please set TG_TOKEN and BASE_URL in environment.")

resp = requests.get(f"https://api.telegram.org/bot{TOKEN}/setWebhook", params={"url": WEBHOOK_URL})
print("Telegram response:", resp.status_code, resp.text)
