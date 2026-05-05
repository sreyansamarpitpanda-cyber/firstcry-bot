import requests, json, time, os, random, re, threading
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from flask import Flask

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

app = Flask(__name__)

@app.route("/")
def home():
    return "FirstCry bot running ✅"

@app.route("/health")
def health():
    return "OK", 200

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

def send_message(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": text[:4000]},
            timeout=5
        )
    except:
        return

def send_item(name, link, image):
    caption = f"🚨 New FirstCry Item!\n\n{name}\n\n{link}"

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
            print("Alert sent:", name)
        except:
            return

def set_bot_commands():
    commands = [
        {"command": "status", "description": "Bot status"},
        {"command": "saved", "description": "Saved count"},
        {"command": "items", "description": "Show saved items"},
        {"command": "last", "description": "Show last 5 items"},
        {"command": "remove_last", "description": "Remove last saved item"},
        {"command": "reset", "description": "Clear saved items"},
        {"command": "test_notify", "description": "Send one test notification"},
        {"command": "slay", "description": "Owner roast command"},
        {"command": "help", "description": "Show commands"},
    ]

    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands",
            json={"commands": commands},
            timeout=5
        )
        print("Telegram commands updated")
    except:
        pass

def get_product_id(link):
    nums = re.findall(r"\d{5,}", link)
    if nums:
        return nums[-1]
    return link.split("?")[0].split("#")[0].rstrip("/").lower()

def get_items():
    r = requests.get(PAGE_URL, headers=HEADERS, timeout=8)
    print("Page:", r.status_code, "Size:", len(r.text))

    soup = BeautifulSoup(r.text, "html.parser")
    items = []

    for a in soup.find_all("a", href=True):
        link = urljoin("https://www.firstcry.com", a["href"])
        text = a.get_text(" ", strip=True)

        if "firstcry.com" not in link:
            continue

        lower_text = text.lower()
        lower_link = link.lower()

        if (
            "hot wheels" not in lower_text
            and "hotwheels" not in lower_text
            and "hot-wheels" not in lower_link
            and "hotwheels" not in lower_link
        ):
            continue

        if len(text) < 5:
            continue

        img = a.find("img")
        image = ""

        if img:
            image = img.get("src") or img.get("data-src") or img.get("data-original") or ""
            image = urljoin("https://www.firstcry.com", image)

        name = " ".join(text.split())
        name = name.split("ADD TO CART")[0].strip()

        if not name:
            name = "FirstCry Hot Wheels Item"

        items.append({
            "id": get_product_id(link),
            "name": name[:180],
            "link": link.split("#")[0],
            "image": image
        })

    unique = {}
    for item in items:
        unique[item["id"]] = item

    return list(unique.values())

def not_owner_reply(user_id):
    if user_id == OWNER_ID:
        return None

    return random.choice([
        "Are you owner? 🤨",
        "Bro thinks he owns the bot 💀",
        "Permission denied 😭 who invited you?",
        "Nice try, but you're not the boss 😤",
        "Access denied 🚫 go back to refreshing stock",
        "You? Owner? That's funny 😂",
        "Bot said NO 🗿",
        "Try again when you become owner 😭"
    ])

def command_reply(text, seen, saved_items, user_id):
    t = text.lower().strip()
    cmd = t.split()[0] if t else ""

    if "hello" in t or "helo" in t or t == "hi":
        if user_id == OWNER_ID:
            return "👑 Yo boss, bot is running perfectly."
        return "🤨 I am running bro, why you want more? Are you scalping?"

    if cmd == "/status":
        deny = not_owner_reply(user_id)
        if deny:
            return deny
        return f"✅ Bot running\nSaved items: {len(seen)}\nTracking:\n{PAGE_URL}"

    if cmd == "/saved":
        deny = not_owner_reply(user_id)
        if deny:
            return deny
        return f"📦 Saved items: {len(seen)}"

    if cmd == "/items":
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        items = list(saved_items.values())
        if not items:
            return "No saved items yet."

        msg = f"📦 Saved items ({len(items)}):\n\n"
        for i, item in enumerate(items[:20], 1):
            msg += f"{i}. {item['name']}\n{item['link']}\n\n"

        if len(items) > 20:
            msg += f"...and {len(items) - 20} more."

        return msg

    if cmd == "/last":
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        items = list(saved_items.values())[-5:]
        if not items:
            return "No saved items yet."

        msg = "🆕 Last 5 saved items:\n\n"
        for i, item in enumerate(items, 1):
            msg += f"{i}. {item['name']}\n{item['link']}\n\n"

        return msg

    if cmd == "/remove_last":
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        if not saved_items:
            return "No items to remove."

        last_key = list(saved_items.keys())[-1]
        last_item = saved_items[last_key]

        saved_items.pop(last_key, None)
        seen.discard(last_key)

        save_json(SEEN_FILE, list(seen))
        save_json(ITEMS_FILE, saved_items)

        return f"🗑️ Removed last saved item:\n{last_item['name']}"

    if cmd == "/reset":
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        seen.clear()
        saved_items.clear()

        save_json(SEEN_FILE, [])
        save_json(ITEMS_FILE, {})

        return "♻️ Reset done. Next scan will silently save current items again."

    if cmd == "/test_notify":
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        items = list(saved_items.values())
        if not items:
            return "No saved items to test."

        item = items[-1]
        send_item(item["name"], item["link"], item["image"])

        return f"✅ Test notification sent:\n{item['name']}"

    if cmd == "/slay":
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        target = text[5:].strip()
        if not target:
            return "Use like: /slay udit"

        return random.choice([
            f"{target}, relax bro 😭 the bot is faster than your refresh finger.",
            f"{target}, refreshing 900 times will not summon the stock 💀",
            f"{target}, even FirstCry is tired of seeing you reload the page 😂",
            f"{target}, bro is stalking Hot Wheels like it owes him money 🤨",
            f"{target}, calm down. The bot is working, your panic is not helping 😭",
            f"{target}, stock hunting is fine, but you are acting like final boss scalper 💀",
            f"{target}, let the bot cook. You go drink water first 🫡",
            f"{target}, your refresh button needs medical insurance at this point 😂"
        ])

    if cmd == "/help":
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        return (
            "🤖 Owner Commands:\n"
            "/status\n"
            "/saved\n"
            "/items\n"
            "/last\n"
            "/remove_last\n"
            "/reset\n"
            "/test_notify\n"
            "/slay name\n"
            "/help"
        )

    return "Use /help"

def check_telegram(seen, saved_items):
    offset = load_offset()
    reset_done = False

    try:
        data = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": offset + 1, "timeout": 1},
            timeout=5
        ).json()
    except:
        return False

    for update in data.get("result", []):
        offset = update["update_id"]
        save_offset(offset)

        msg = update.get("message", {})
        text = msg.get("text", "")
        chat_id = str(msg.get("chat", {}).get("id", ""))
        user_id = str(msg.get("from", {}).get("id", ""))

        if chat_id not in CHAT_IDS or not text:
            continue

        is_command = text.startswith("/")
        is_tagged = BOT_USERNAME.lower() in text.lower()

        if not is_command and not is_tagged:
            continue

        clean = text.replace(BOT_USERNAME, "").strip()
        reply = command_reply(clean, seen, saved_items, user_id)
        send_message(chat_id, reply)

        clean_cmd = clean.lower().strip().split()[0] if clean.strip() else ""

        if clean_cmd == "/reset" and user_id == OWNER_ID:
            reset_done = True

    return reset_done

def bot_loop():
    if not BOT_TOKEN:
        print("BOT_TOKEN missing. Add it in Render Environment Variables.")
        return

    seen = set(load_json(SEEN_FILE, []))
    saved_items = load_json(ITEMS_FILE, {})
    first_run = len(seen) == 0

    print("Bot loop started")
    set_bot_commands()

    while True:
        try:
            reset_done = check_telegram(seen, saved_items)
            if reset_done:
                first_run = True

            print("Scanning FirstCry...")
            items = get_items()
            print("Found:", len(items))

            if first_run:
                print("First run silent save")

                for item in items:
                    seen.add(item["id"])
                    saved_items[item["id"]] = item

                save_json(SEEN_FILE, list(seen))
                save_json(ITEMS_FILE, saved_items)

                first_run = False
                print("Saved total:", len(seen))

            else:
                new_count = 0

                for item in items:
                    if item["id"] not in seen:
                        new_count += 1

                        send_item(item["name"], item["link"], item["image"])

                        seen.add(item["id"])
                        saved_items[item["id"]] = item

                        save_json(SEEN_FILE, list(seen))
                        save_json(ITEMS_FILE, saved_items)

                print("New count:", new_count)
                print("Saved total:", len(seen))

        except Exception as e:
            print("Main error:", e)

        wait = random.randint(30, 40)
        print("Waiting:", wait)

        for _ in range(wait):
            reset_done = check_telegram(seen, saved_items)
            if reset_done:
                first_run = True
            time.sleep(1)

def start_bot():
    print("Starting bot loop...")
    bot_loop()

if __name__ == "__main__":
    threading.Thread(target=start_bot).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
