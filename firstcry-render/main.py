import requests, json, time, os, random, re, threading
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from flask import Flask

# ================= CONFIG =================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
print("BOT TOKEN EXISTS:", BOT_TOKEN is not None)

PAGE_URL = "https://www.firstcry.com/hotwheels/5/0/113?sort=Popularity&q=ard_hotwheels%20&ref2=q_ard_hotwheels%20&asid=53241"

CHAT_IDS = ["-5253322080"]
BOT_USERNAME = "@fcrestockerbot"
OWNER_ID = "7754819035"

SEEN_FILE = "seen_items.json"
ITEMS_FILE = "saved_items.json"
OFFSET_FILE = "telegram_offset.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-IN,en;q=0.9",
}

# ================= FLASK =================

app = Flask(__name__)

@app.route("/")
def home():
    return "FirstCry bot running ✅"

@app.route("/health")
def health():
    return "OK", 200

# ================= FILE =================

def load_json(path, default):
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def load_offset():
    try:
        if os.path.exists(OFFSET_FILE):
            return int(open(OFFSET_FILE).read().strip() or 0)
    except:
        pass
    return 0

def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))

# ================= TELEGRAM =================

def send_message(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": text[:4000]},
        timeout=5
    )

def send_item(name, link, image):
    caption = f"🚨 New Item!\n\n{name}\n\n{link}"

    for chat_id in CHAT_IDS:
        try:
            if image:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data={"chat_id": chat_id, "photo": image, "caption": caption},
                    timeout=8
                )
            else:
                send_message(chat_id, caption)

            print("📩 Sent:", name)

        except Exception as e:
            print("Send error:", e)

# ================= SCRAPER =================

def get_product_id(link):
    nums = re.findall(r"\d{5,}", link)
    if nums:
        return nums[-1]
    return link.split("?")[0]

def get_items():
    r = requests.get(PAGE_URL, headers=HEADERS, timeout=8)
    print("Scanning page:", r.status_code)

    soup = BeautifulSoup(r.text, "html.parser")
    items = []

    for a in soup.find_all("a", href=True):
        link = urljoin("https://www.firstcry.com", a["href"])
        text = a.get_text(" ", strip=True)

        if "firstcry.com" not in link:
            continue

        if "hot" not in text.lower():
            continue

        if len(text) < 5:
            continue

        img = a.find("img")
        image = ""
        if img:
            image = img.get("src") or img.get("data-src") or ""

        name = " ".join(text.split())

        items.append({
            "id": get_product_id(link),
            "name": name,
            "link": link,
            "image": image
        })

    return list({i["id"]: i for i in items}.values())

# ================= OWNER CHECK =================

def not_owner_reply(user_id):
    if user_id == OWNER_ID:
        return None

    roasts = [
        "Are you owner? 🤨",
        "Bro thinks he owns the bot 💀",
        "Permission denied 😭",
        "Nice try, but you're not the boss 😤"
    ]
    return random.choice(roasts)

# ================= COMMANDS =================

def command_reply(text, seen, saved_items, user_id):
    t = text.lower().strip()

    if "hello" in t:
        if user_id == OWNER_ID:
            return "👑 Bot running perfectly."
        return "🤨 I am running bro, why you want more?"

    if t.startswith("/status"):
        deny = not_owner_reply(user_id)
        if deny: return deny
        return f"Saved: {len(seen)}"

    if t.startswith("/items"):
        deny = not_owner_reply(user_id)
        if deny: return deny

        msg = ""
        for i, item in enumerate(list(saved_items.values())[:10], 1):
            msg += f"{i}. {item['name']}\n{item['link']}\n\n"
        return msg or "No items"

    if t.startswith("/remove_last"):
        deny = not_owner_reply(user_id)
        if deny: return deny

        if not saved_items:
            return "Nothing to remove"

        last_key = list(saved_items.keys())[-1]
        last_item = saved_items[last_key]

        saved_items.pop(last_key)
        seen.discard(last_key)

        save_json(SEEN_FILE, list(seen))
        save_json(ITEMS_FILE, saved_items)

        return f"Removed:\n{last_item['name']}"

    if t.startswith("/slay"):
        deny = not_owner_reply(user_id)
        if deny: return deny

        target = text[5:].strip()
        if not target:
            return "Use: /slay name"

        return f"{target}, bro stop refreshing like crazy 😂"

    return "Use /status /items"

# ================= TELEGRAM LISTENER =================

def check_telegram(seen, saved_items):
    offset = load_offset()

    try:
        data = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": offset + 1, "timeout": 1},
            timeout=3
        ).json()
    except:
        return

    for update in data.get("result", []):
        offset = update["update_id"]
        save_offset(offset)

        msg = update.get("message", {})
        text = msg.get("text", "")
        chat_id = str(msg.get("chat", {}).get("id", ""))
        user_id = str(msg.get("from", {}).get("id", ""))

        if chat_id not in CHAT_IDS or not text:
            continue

        if text.startswith("/") or BOT_USERNAME in text:
            clean = text.replace(BOT_USERNAME, "").strip()
            reply = command_reply(clean, seen, saved_items, user_id)
            send_message(chat_id, reply)

# ================= BOT LOOP =================

def bot_loop():
    if not BOT_TOKEN:
        print("BOT_TOKEN missing")
        return

    seen = set(load_json(SEEN_FILE, []))
    saved_items = load_json(ITEMS_FILE, {})
    first_run = len(seen) == 0

    print("Bot loop started")

    while True:
        try:
            check_telegram(seen, saved_items)

            items = get_items()

            if first_run:
                for item in items:
                    seen.add(item["id"])
                    saved_items[item["id"]] = item

                save_json(SEEN_FILE, list(seen))
                save_json(ITEMS_FILE, saved_items)
                first_run = False

            else:
                for item in items:
                    if item["id"] not in seen:
                        send_item(item["name"], item["link"], item["image"])
                        seen.add(item["id"])
                        saved_items[item["id"]] = item

                        save_json(SEEN_FILE, list(seen))
                        save_json(ITEMS_FILE, saved_items)

        except Exception as e:
            print("Error:", e)

        wait = random.randint(30, 40)
print("Waiting:", wait)

for _ in range(wait):
    check_telegram(seen, saved_items)
    time.sleep(1)

# ================= START =================

def start_bot():
    print("Starting bot loop...")
    bot_loop()

if __name__ == "__main__":
    threading.Thread(target=start_bot).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
