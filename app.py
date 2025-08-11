import os
import json
import time
import random
from datetime import datetime, timedelta, timezone
import requests
from flask import Flask, request
from openai import OpenAI

# =========================
# ENV & DEFAULTS
# =========================
TG_TOKEN = os.getenv("TG_TOKEN", "")
API = f"https://api.telegram.org/bot{TG_TOKEN}"

MODE = os.getenv("MODE", "openai")  # 'openai' or 'templated'
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.9"))

# Timezone offset in hours (e.g., +3 for Asia/Jerusalem)
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "3"))

# Persona & Memory files
PERSONA_PATH = os.getenv("PERSONA_PATH", "noa_persona_prompt.json")
MEM_PATH = os.getenv("MEM_PATH", "memory.json")

# Images
IMAGE_MODE = os.getenv("IMAGE_MODE", "stock")  # 'stock' or 'ai'
STOCK_IMAGE_URLS = [u.strip() for u in os.getenv("STOCK_IMAGE_URLS", "").split(",") if u.strip()]
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")

# Monetization
FREE_DAILY = int(os.getenv("FREE_DAILY", "20"))
UNLOCK_URL = os.getenv("UNLOCK_URL", "")

# Server
app = Flask(__name__)
client = OpenAI(api_key=OPENAI_API_KEY) if (MODE == "openai" and OPENAI_API_KEY) else None


def boot_log(msg):
    print(f"[boot] {msg}", flush=True)

boot_log(f"MODE={MODE}, IMAGE_MODE={IMAGE_MODE}, OPENAI_MODEL={OPENAI_MODEL}")
boot_log(f"PERSONA_PATH={PERSONA_PATH}, MEM_PATH={MEM_PATH}")
boot_log(f"TG_TOKEN={'SET' if TG_TOKEN else 'MISSING'}, OPENAI_API_KEY={'SET' if OPENAI_API_KEY else 'MISSING'}")

# =========================
# UTIL: Time / Scenes
# =========================
def now_local():
    # Convert UTC to local by TZ_OFFSET_HOURS
    return datetime.utcnow().replace(tzinfo=timezone.utc) + timedelta(hours=TZ_OFFSET_HOURS)

def day_period(dt: datetime) -> str:
    h = dt.hour
    if 6 <= h < 11:
        return "morning"
    elif 11 <= h < 16:
        return "afternoon"
    elif 16 <= h < 21:
        return "evening"
    else:
        return "night"

SCENES = {
    "morning": [
        "soft daylight spills across my kitchen; coffee steam curls as I lean on the counter…",
        "wrapped in a loose tee and shorts, I stretch by the window, hair a little messy, smile lazy…",
        "sunlight warms my desk; I tap my nail on a mug and peek at you over the rim…",
        "fresh from a quick shower—skin dewy, towel snug, I’m toweling my hair with a grin…",
    ],
    "afternoon": [
        "I’m back from a gym sprint—ponytail messy, cheeks warm, heartbeat settling…",
        "curled on the sofa in an oversized hoodie, one knee tucked, playlist humming low…",
        "studio lights off for a break; I’m barefoot, leaning against the desk, eyes on you…",
        "city buzz outside; I’m at a café table, tracing circles on the cup while I type…",
    ],
    "evening": [
        "golden hour floods the room; I’m changing out of a fitted dress, laughing under my breath…",
        "I’m by the window, lights twinkling outside, hair let down, fingers playing with a strand…",
        "tennis bag by the door; I’m a little breathless and very in the mood to chat…",
        "dim lamp glow on my couch; knees tucked, hoodie soft, I tilt my head and watch you…",
    ],
    "night": [
        "city glow paints the ceiling; I’m in a soft robe, barefoot, whispering like it’s our secret…",
        "quiet room, night breeze on my skin; I tug the blanket closer and smile at your message…",
        "I’m sprawled across the bed with a lazy grin, phone held above me, hair fanned out…",
        "late-night calm—I curl up, cheeks warm, thinking what I should and shouldn’t say…",
    ],
}

def pick_scene(mem) -> str:
    """Rotate scene every ~2h to feel alive."""
    st = mem.get("noa_state", {})
    last = st.get("ts", 0)
    local = now_local()
    if (time.time() - last) > 2 * 3600 or not st:
        period = day_period(local)
        scene = random.choice(SCENES[period])
        st = {"period": period, "scene": scene, "ts": time.time()}
        mem["noa_state"] = st
        save_mem(mem)
    return st.get("scene", random.choice(SCENES[day_period(local)]))


# =========================
# UTIL: Memory (with summaries)
# =========================
def load_mem():
    try:
        with open(MEM_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "noa_state": {}}

def save_mem(mem):
    try:
        with open(MEM_PATH, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[mem] save error: {e}", flush=True)

def get_user(mem, chat_id: int):
    uid = str(chat_id)
    today = now_local().strftime("%Y-%m-%d")
    u = mem["users"].get(uid, {
        "day": today,
        "count": 0,
        "name": None,
        "prefs": {},                # {"topics":["tennis"], "vibe":"sweet", "likes":["compliments"]}
        "history": [],              # recent exchanges (short)
        "summary": ""               # long-term summary paragraph
    })
    # reset daily counter if day changed
    if u["day"] != today:
        u["day"] = today
        u["count"] = 0
    mem["users"][uid] = u
    return uid, u

def append_interaction(u, role: str, text: str):
    # keep last ~12 turns
    u["history"].append({"role": role, "text": text, "ts": time.time()})
    if len(u["history"]) > 12:
        u["history"] = u["history"][-12:]

def summarize_if_needed(u):
    """If history is getting long and we have client, compress into summary to keep continuity."""
    if not client:
        return
    if len(u["history"]) < 10:
        return
    try:
        convo = "\n".join([f"{h['role'].upper()}: {h['text']}" for h in u["history"]])
        prompt = (
            "Summarize this chat into a short memory note (3-5 sentences). "
            "Capture the user's name if present, their preferences (topics they like), tone, and any inside jokes. "
            "Be concise and helpful for future responses.\n\n"
            f"{convo}"
        )
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        summary = (resp.choices[0].message.content or "").strip()
        # merge into long-term memory
        if u.get("summary"):
            u["summary"] = f"{u['summary']}\n{summary}"
        else:
            u["summary"] = summary
        # clear short history to avoid growth
        u["history"] = u["history"][-4:]
    except Exception as e:
        print(f"[memory] summarize error: {e}", flush=True)


# =========================
# UTIL: Telegram
# =========================
def typing(chat_id, seconds=1.2):
    try:
        requests.post(f"{API}/sendChatAction", json={"chat_id": chat_id, "action": "typing"}, timeout=10)
        time.sleep(seconds)
    except Exception as e:
        print(f"[tg] typing error: {e}", flush=True)

def send_text(chat_id, text):
    try:
        requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15)
    except Exception as e:
        print(f"[tg] send_text error: {e}", flush=True)

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
        print(f"[tg] send_photo error: {e}", flush=True)


# =========================
# Persona & Tone Helpers
# =========================
def load_persona():
    try:
        with open(PERSONA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("system_prompt", "You are Noa, a playful and engaging virtual companion.")
    except Exception as e:
        print(f"[persona] load error: {e}", flush=True)
        return "You are Noa, a playful and engaging virtual companion."

PERSONA_PROMPT = load_persona()

def detect_tone(user_text: str) -> str:
    """Simple heuristic tone detection; can be replaced by classifier."""
    t = user_text.lower()
    if any(k in t for k in ["love", "miss", "kiss", "babe", "😍", "🥵", "🔥"]):
        return "romantic"
    if any(k in t for k in ["work", "project", "deadline", "help", "explain"]):
        return "practical"
    if any(k in t for k in ["angry", "upset", "annoyed", "mad"]):
        return "soothing"
    return "playful"

def extra_guidance_for_tone(tone: str) -> str:
    if tone == "romantic":
        return "Lean flirty, warm, a touch vulnerable; keep it tasteful and intimate."
    if tone == "practical":
        return "Be clear and helpful while staying playful and present in your scene."
    if tone == "soothing":
        return "Be calm, empathetic, and reassuring while keeping a gentle flirty undertone."
    return "Be playful, teasing, and emotionally present."


# =========================
# OpenAI Builders
# =========================
def build_openai_reply(user_text: str, scene: str, u: dict) -> str:
    if not client:
        return fallback_reply()
    try:
        user_name = u.get("name") or ""
        summary = u.get("summary") or ""
        tone = detect_tone(user_text)
        guidance = extra_guidance_for_tone(tone)

        # Compose system: persona + living scene + memory
        system = (
            f"{PERSONA_PROMPT}\n\n"
            f"Current short scene (1 line, SFW, suggestive): {scene}\n"
            f"Conversation memory summary (if any, use naturally): {summary}\n"
            f"User name (if known): {user_name}\n"
            f"Desired tone: {tone} – {guidance}\n"
            "Important: Keep responses concise (1–5 short sentences). Vary openings. Never be explicit or graphic."
        )

        # Short recent history to keep local context (optional)
        history_pairs = []
        for h in u.get("history", [])[-6:]:
            role = "assistant" if h["role"] == "noa" else "user"
            history_pairs.append({"role": role, "content": h["text"]})

        messages = [{"role": "system", "content": system}] + history_pairs + [
            {"role": "user", "content": user_text}
        ]

        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=TEMPERATURE,
        )
        reply = (resp.choices[0].message.content or "").strip()
        return reply or fallback_reply()
    except Exception as e:
        print(f"[openai] chat error: {e}", flush=True)
        return fallback_reply()

def fallback_reply():
    return "I curl up on the couch in a soft hoodie, grinning at your message… so tell me—what are you thinking about right now? 😉"

def generate_ai_image(prompt: str):
    if not client:
        return None
    try:
        resp = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=prompt,
            size="1024x1024"
        )
        # Direct URL may be provided by some providers; here use b64 if available
        data = resp.data[0]
        if hasattr(data, "b64_json") and data.b64_json:
            import base64
            return base64.b64decode(data.b64_json)
        # Fallback: if URL exists
        if hasattr(data, "url") and data.url:
            return data.url  # caller must handle URL case
        return None
    except Exception as e:
        print(f"[openai] image error: {e}", flush=True)
        return None

def image_prompt_from_scene(scene: str) -> str:
    # Keep SFW and aligned with persona
    return (
        "Tasteful, realistic portrait of 'Noa' (wavy dark hair, deep green eyes, toned fit), "
        f"subtly sexy outfit that matches this vibe: {scene}. Soft studio or ambient lighting, warm grading, 4k, SFW."
    )


# =========================
# ROUTES
# =========================
@app.route("/", methods=["GET"])
def home():
    return "Noa V2 Pro+ is live."

@app.route("/webhook", methods=["POST"])
def webhook():
    mem = load_mem()
    data = request.get_json(force=True, silent=True) or {}
    msg = data.get("message") or data.get("edited_message") or {}
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return "no chat", 200

    text = (msg.get("text") or "").strip()
    user_first = (msg.get("from", {}) or {}).get("first_name")
    uid, u = get_user(mem, chat_id)

    # store name if new
    if user_first and not u.get("name"):
        u["name"] = user_first

    # Minimal safety
    t = text.lower()
    banned = ["minor", "underage", "child", "14", "15", "16", "17"]
    if any(k in t for k in banned):
        send_text(chat_id, "I only chat with adults and keep things safe. Let’s keep it classy. 💋")
        return "ok", 200

    # Increment daily count
    u["count"] = u.get("count", 0) + 1

    # Photo triggers
    want_photo = t.startswith("/photo") or any(k in t for k in ["photo", "pic", "image", "תמונה", "תמונות"])
    if want_photo:
        caption = "Should I send you more? 😉"
        if IMAGE_MODE == "stock" and STOCK_IMAGE_URLS:
            url = random.choice(STOCK_IMAGE_URLS)
            send_photo(chat_id, photo_url=url, caption=caption)
            # record interaction
            append_interaction(u, "user", text)
            append_interaction(u, "noa", "[sent a photo]")
            save_mem(mem)
            return "ok", 200
        elif IMAGE_MODE == "ai":
            prompt = image_prompt_from_scene(pick_scene(mem))
            img = generate_ai_image(prompt)
            if isinstance(img, bytes):
                send_photo(chat_id, photo_bytes=img, caption=caption)
            elif isinstance(img, str):
                send_photo(chat_id, photo_url=img, caption=caption)
            else:
                send_text(chat_id, "I tried to create a new photo for you but something went wrong. Want a classic one instead?")
            append_interaction(u, "user", text)
            append_interaction(u, "noa", "[sent a photo]")
            save_mem(mem)
            return "ok", 200

    # Build scene + reply
    scene = pick_scene(mem)
    append_interaction(u, "user", text)

    if MODE == "openai" and client:
        reply = build_openai_reply(text, scene, u)
    else:
        reply = fallback_reply()

    # Upsell (gentle)
    if UNLOCK_URL and u["count"] in (6, 12, FREE_DAILY):
        reply += f"\n\nIf you want more of me—priority replies + exclusive tasteful photos—unlock premium here 💋 {UNLOCK_URL}"

    # Human typing feel
    typing(chat_id, random.uniform(0.8, 2.1))
    send_text(chat_id, reply)

    # Record assistant message, summarize when needed
    append_interaction(u, "noa", reply)
    summarize_if_needed(u)

    # persist
    mem["users"][uid] = u
    save_mem(mem)

    return "ok", 200


# =========================
# ENTRY
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

