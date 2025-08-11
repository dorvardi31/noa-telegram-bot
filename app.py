
import os, json, requests
from flask import Flask, request

APP = Flask(__name__)

TOKEN = os.getenv("TG_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODE = os.getenv("MODE", "templated")
PERSONA_PATH = os.getenv("PERSONA_PATH", "noa_persona_prompt.json")
with open(PERSONA_PATH, "r", encoding="utf-8") as f:
    PERSONA = json.load(f)

API = f"https://api.telegram.org/bot{TOKEN}"

def send(chat_id, text):
    requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text})

def build_templated_reply(user_text):
    opener = "Hey you 😉"
    compliment = "You’ve been on my mind… and I love how bold you are."
    tease = "Tell me one thing you probably shouldn’t tell me 🔥"
    return f"{opener} {compliment}\n{tease}"

def build_openai_reply(user_text):
    try:
        import openai
        openai.api_key = OPENAI_API_KEY
        system_msg = PERSONA.get("system_prompt", "")
        resp = openai.ChatCompletion.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_text}
            ],
            temperature=float(os.getenv("TEMPERATURE", "0.9"))
        )
        return resp.choices[0].message["content"].strip()
    except Exception:
        return build_templated_reply(user_text)

@APP.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    msg = data.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "")

    if not chat_id:
        return "no chat", 200

    if MODE == "openai" and OPENAI_API_KEY:
        reply = build_openai_reply(text)
    else:
        reply = build_templated_reply(text)

    if any(k in text.lower() for k in ["minor", "underage", "14", "15", "16", "17"]):
        send(chat_id, "I only chat with adults and keep things safe. Let’s keep it classy. 💋")
    else:
        send(chat_id, reply)

    return "ok", 200
