import os
import json
import base64
import requests
from flask import Flask, request

# ========= Flask =========
APP = Flask(__name__)

# ========= ENV =========
TOKEN = os.getenv("TG_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"

MODE = os.getenv("MODE", "templated")  # 'templated' or 'openai'

PERSONA_PATH = os.getenv("PERSONA_PATH", "noa_persona_prompt.json")
with open(PERSONA_PATH, "r", encoding="utf-8") as f:
    PERSONA = json.load(f)

# OpenAI (SDK v1)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.9"))

try:
    from openai import OpenAI
    OPENAI_CLIENT = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    OPENAI_CLIENT = None  # יפעיל fallback אם אין SDK/מפתח

# Images
IMAGE_MODE = os.getenv("IMAGE_MODE", "stock")  # 'stock' or 'ai'
STOCK_IMAGE_URLS = [u.strip() for u in os.getenv("STOCK_IMAGE_URLS", "").split(",") if u.strip()]
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")

# ========= Telegram helpers =========
def send(chat_id, text):
    try:
        requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception as e:
        print(f"send error: {e}", flush=True)

def send_photo(chat_id, photo_url=None, caption=None, photo_bytes=None):
    try:
        if photo_bytes is not None:
            files = {"photo": ("noa.jpg", photo_bytes)}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
            requests.post(f"{API}/sendPhoto", data=data, files=files, timeout=60)
        else:
            payload = {"chat_id": chat_id, "photo": photo_url}
            if caption:
                payload["caption"] = caption
            requests.post(f"{API}/sendPhoto", json=payload, timeout=30)
    except Exception as e:
        print(f"send_photo error: {e}", flush=True)

# ========= Reply builders =========
def build_templated_reply(user_text, username=None):
    opener = "Hey you 😉"
    compliment = "You’ve been on my mind… and I love how bold you are."
    tease = "Tell me one thing you probably shouldn’t tell me 🔥"
    return f"{opener} {compliment}\n{tease}"

def build_openai_reply(user_text):
    if not OPENAI_CLIENT:
        return build_templated_reply(user_text)
    try:
        system_msg = PERSONA.get("system_prompt", "")
        resp = OPENAI_CLIENT.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_text},
            ],
            temperature=TEMPERATURE,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"OpenAI chat error: {e}", flush=True)
        return build_templated_reply(user_text)

def image_prompt_from_persona():
    # SFW, עקבי עם התיאור של Noa
    return (
        "Portrait photo of 'Noa': wavy dark hair, deep green eyes, toned fit body, stylish and subtly sexy outfit "
        "(crop top or elegant dress), soft studio lighting, warm color grading, confident playful pose, tasteful, 4k."
    )

def generate_ai_image(prompt):
    if not OPENAI_CLIENT or not OPENAI_API_KEY:
        return None
    try:
        resp = OPENAI_CLIENT.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=prompt,
            size="1024x1024"
        )
        b64 = resp.data[0].b64_json
        return base64.b64decode(b64)
    except Exception as e:
        print(f"OpenAI image error: {e}", flush=True)
        return None

# ========= Routes =========
@APP.route("/", methods=["GET"])
def home():
    return "Noa bot is running."

@APP.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    msg = data.get("message") or data.get("edited_message") or {}
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = msg.get("text", "") or ""

    if not chat_id:
        return "no chat", 200

    t = text.lower().strip()

    # --- Safety: minimal guardrails (הרחב כרצונך) ---
    banned = ["minor", "underage", "child", "14", "15", "16", "17"]
    if any(k in t for k in banned):
        send(chat_id, "I only chat with adults and keep things safe. Let’s keep it classy. 💋")
        return "ok", 200

    # --- Photo triggers ---
    want_photo = t.startswith("/photo") or any(k in t for k in ["photo", "pic", "image", "תמונה", "תמונות"])
    if want_photo:
        caption = "Should I send you more? 😉"
        if IMAGE_MODE == "stock" and STOCK_IMAGE_URLS:
            import random
            url = random.choice(STOCK_IMAGE_URLS)
            send_photo(chat_id, photo_url=url, caption=caption)
            return "ok", 200
        elif IMAGE_MODE == "ai":
            img_bytes = generate_ai_image(image_prompt_from_persona())
            if img_bytes:
                send_photo(chat_id, photo_bytes=img_bytes, caption=caption)
            else:
                send(chat_id, "I tried to create a new photo for you but something went wrong. Want a classic one instead?")
            return "ok", 200

    # --- Build text reply ---
    if MODE == "openai" and OPENAI_CLIENT:
        reply = build_openai_reply(text)
    else:
        reply = build_templated_reply(text, chat.get("username"))

    send(chat_id, reply)
    return "ok", 200
