import json
import os
import random
import re
import threading
import time

import requests
from flask import Flask


BOT_TOKEN = os.environ.get("BOT_TOKEN")
print("BOT TOKEN EXISTS:", BOT_TOKEN is not None)

BASE_URL = "https://www.firstcry.com"
PAGE_URL = "https://www.firstcry.com/Hot%20Wheels/0/0/113?q=as_hotwhee&asid=48299"

CHAT_IDS = ["-1003942411459"]
BOT_USERNAME = "@fcrestockerbot"
OWNER_ID = "7754819035"

SEEN_FILE = "seen_items.json"
ITEMS_FILE = "saved_items.json"
OFFSET_FILE = "telegram_offset.txt"

SCAN_PAGES = int(os.environ.get("SCAN_PAGES", "1"))
POLL_MIN_SECONDS = int(os.environ.get("POLL_MIN_SECONDS", "4"))
POLL_MAX_SECONDS = int(os.environ.get("POLL_MAX_SECONDS", "6"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "4"))
FIRSTCRY_BACKOFF_SECONDS = int(os.environ.get("FIRSTCRY_BACKOFF_SECONDS", "60"))

PROCESSED_UPDATES = set()
BOT_THREAD_STARTED = False
FIRSTCRY_BACKOFF_UNTIL = 0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}

FIRSTCRY_SESSION = requests.Session()
FIRSTCRY_SESSION.headers.update(HEADERS)

TELEGRAM_SESSION = requests.Session()

app = Flask(__name__)


@app.route("/")
def home():
    return "FirstCry bot running", 200


@app.route("/health")
def health():
    return "OK", 200


def load_json(path, default):
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_offset():
    try:
        if os.path.exists(OFFSET_FILE):
            return int(open(OFFSET_FILE, encoding="utf-8").read().strip() or 0)
    except Exception:
        pass
    return 0


def save_offset(offset):
    with open(OFFSET_FILE, "w", encoding="utf-8") as f:
        f.write(str(offset))


def clean_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def product_slug(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "product"


def format_price(value):
    if value in (None, ""):
        return ""
    try:
        number = float(str(value).replace(",", ""))
        if number.is_integer():
            return f"Rs. {int(number)}"
        return f"Rs. {number:.2f}"
    except Exception:
        return clean_text(value)


def firstcry_backoff_remaining():
    return max(0, int(FIRSTCRY_BACKOFF_UNTIL - time.time()))


def firstcry_get(url):
    global FIRSTCRY_BACKOFF_UNTIL

    remaining = firstcry_backoff_remaining()
    if remaining > 0:
        print("FirstCry backoff active:", remaining, "seconds")
        return None

    r = FIRSTCRY_SESSION.get(url, timeout=REQUEST_TIMEOUT_SECONDS)

    if r.status_code in (403, 429):
        retry_after = r.headers.get("Retry-After", "")
        try:
            delay = int(float(retry_after))
        except Exception:
            delay = FIRSTCRY_BACKOFF_SECONDS

        delay = max(delay, FIRSTCRY_BACKOFF_SECONDS)
        FIRSTCRY_BACKOFF_UNTIL = time.time() + delay
        print("FirstCry slow-down response:", r.status_code, "Backoff:", delay)

    return r


def send_message(chat_id, text):
    try:
        TELEGRAM_SESSION.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": text[:4000],
                "disable_web_page_preview": False,
            },
            timeout=4,
        )
    except Exception as exc:
        print("send_message error:", exc)


def send_item(name, link, image="", price=""):
    caption = f"New FirstCry Item\n\n{name}\n\n{link}"

    for chat_id in CHAT_IDS:
        try:
            send_message(chat_id, caption)
            print("Alert sent:", name)
        except Exception as exc:
            print("send_item error:", exc)


def set_bot_commands():
    commands = [
        {"command": "status", "description": "Bot status"},
        {"command": "saved", "description": "Saved count"},
        {"command": "items", "description": "Show saved items"},
        {"command": "last", "description": "Show last 5 items"},
        {"command": "remove_last", "description": "Remove last saved item"},
        {"command": "reset", "description": "Clear saved items"},
        {"command": "test_notify", "description": "Send a test stock alert"},
        {"command": "test_alert", "description": "Send a test stock alert"},
        {"command": "help", "description": "Show commands"},
    ]

    try:
        TELEGRAM_SESSION.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands",
            json={"commands": commands},
            timeout=8,
        )
        print("Telegram commands updated")
    except Exception as exc:
        print("set commands error:", exc)


def api_url(page_no):
    return (
        "https://www.firstcry.com/svcs/ProductFilter.svc/GetSubcategoryWisePagingProducts"
        f"?PageNo={page_no}&PageSize=20&SortExpression=Popularity"
        "&SubCatId=&BrandId=113&Price=&Age=&Color=&OptionalFilter=&OutOfStock="
        "&Type1=&Type2=&Type3=&Type4=&Type5=&Type6=&Type7=&Type8=&Type9=&Type10="
        "&Type11=&Type12=&Type13=&Type14=&Type15=&combo=&discount=&searchwithincat="
        "&ProductidQstr=&searchrank=&pmonths=&cgen=&PriceQstr=&DiscountQstr="
        "&sorting=Popularity&rating=&offer=&CatId=5&skills=&material="
        "&curatedcollections=&measurement=&gender=&exclude=&p=&premium=&pcode="
        "&isclub=0&deliverytype=&author=&booktype=&character=&collection=&format="
        "&genre=&booklanguage=&publication=&skill="
    )


def parse_items_from_api_response(text):
    items = []

    try:
        outer = json.loads(text)
        inner = outer.get("ProductResponse", outer)
        if isinstance(inner, str):
            inner = json.loads(inner)
        products = inner.get("Products", [])
    except Exception as exc:
        print("API parse error:", exc)
        return []

    for product in products:
        name = clean_text(product.get("PNm", ""))
        brand = clean_text(product.get("BNm", ""))
        combined = f"{brand} {name}".lower()

        if "hot wheels" not in combined and "hotwheels" not in combined:
            continue

        pid = str(product.get("PId") or product.get("PInfId") or "").strip()
        if not pid:
            continue

        stock = 0
        try:
            stock = int(float(str(product.get("CrntStock") or "0")))
        except Exception:
            stock = 0

        if stock <= 0:
            continue

        link = f"{BASE_URL}/hot-wheels/{product_slug(name)}/{pid}/product-detail"
        price = (
            format_price(product.get("clubprice"))
            or format_price(product.get("discprice"))
            or format_price(product.get("MRP"))
        )

        items.append(
            {
                "id": pid,
                "name": name[:180] or "FirstCry Hot Wheels Item",
                "price": price,
                "link": link,
                "stock": stock,
            }
        )

    unique = {}
    for item in items:
        unique[item["id"]] = item

    return list(unique.values())


def get_items():
    all_items = []

    for page_no in range(1, SCAN_PAGES + 1):
        try:
            r = firstcry_get(api_url(page_no))
            if r is None:
                continue

            print("API:", page_no, r.status_code, "Size:", len(r.text))

            if r.ok:
                api_items = parse_items_from_api_response(r.text)
                print("In-stock API items:", page_no, len(api_items))
                all_items.extend(api_items)
        except Exception as exc:
            print("API error:", page_no, exc)

        if page_no < SCAN_PAGES:
            time.sleep(random.uniform(0.2, 0.5))

    unique = {}
    for item in all_items:
        unique[item["id"]] = item

    return list(unique.values())


def not_owner_reply(user_id):
    if user_id == OWNER_ID:
        return None
    return "Permission denied."


def item_line(i, item):
    return f"{i}. {item['name']}\n{item['link']}\n\n"


def command_reply(text, seen, saved_items, user_id):
    t = text.lower().strip()
    cmd = t.split()[0] if t else ""

    if "hello" in t or "helo" in t or t == "hi":
        return "Yo boss, FirstCry bot is running." if user_id == OWNER_ID else "Bot is running."

    if cmd == "/status":
        deny = not_owner_reply(user_id)
        if deny:
            return deny
        return (
            f"FirstCry bot running\n"
            f"Saved items: {len(seen)}\n"
            f"Scan pages: {SCAN_PAGES}\n"
            f"Poll: {POLL_MIN_SECONDS}-{POLL_MAX_SECONDS}s\n"
            f"Request timeout: {REQUEST_TIMEOUT_SECONDS}s\n"
            f"FirstCry backoff: {firstcry_backoff_remaining()}s\n"
            f"Photos: off\n"
            f"Tracking:\n{PAGE_URL}"
        )

    if cmd == "/saved":
        deny = not_owner_reply(user_id)
        if deny:
            return deny
        return f"Saved items: {len(seen)}"

    if cmd == "/items":
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        items = list(saved_items.values())
        if not items:
            return "No saved items yet."

        msg = f"Saved items ({len(items)}):\n\n"
        for i, item in enumerate(items[:20], 1):
            msg += item_line(i, item)
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

        msg = "Last 5 saved items:\n\n"
        for i, item in enumerate(items, 1):
            msg += item_line(i, item)
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

        return f"Removed last saved item:\n{last_item['name']}"

    if cmd == "/reset":
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        seen.clear()
        saved_items.clear()
        save_json(SEEN_FILE, [])
        save_json(ITEMS_FILE, {})

        return "Reset done. Next scan will silently save current in-stock items again."

    if cmd in {"/test_notify", "/test_alert"}:
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        items = list(saved_items.values())
        item = items[-1] if items else {
            "name": "TEST ALERT - FirstCry Hot Wheels",
            "link": PAGE_URL,
            "price": "",
        }

        send_item(item["name"], item["link"])
        return f"Test notification sent:\n{item['name']}"

    if cmd == "/help":
        deny = not_owner_reply(user_id)
        if deny:
            return deny

        return (
            "Owner Commands:\n"
            "/status\n/saved\n/items\n/last\n/remove_last\n/reset\n"
            "/test_notify\n/test_alert\n/help"
        )

    return "Use /help"


def should_handle_message(text, chat_id, user_id):
    if not text:
        return False, ""

    is_private = chat_id == user_id
    bot_name = BOT_USERNAME.lower().lstrip("@")

    first = text.strip().split()[0] if text.strip() else ""
    command_match = re.match(r"^/([a-zA-Z0-9_]+)(?:@([a-zA-Z0-9_]+))?", first)

    if command_match:
        mentioned_bot = command_match.group(2)

        if mentioned_bot and mentioned_bot.lower() != bot_name:
            return False, ""

        if not is_private and not mentioned_bot:
            return False, ""

        clean_first = "/" + command_match.group(1)
        clean = text.replace(first, clean_first, 1).strip()
        return True, clean

    if BOT_USERNAME.lower() not in text.lower():
        return False, ""

    clean = re.sub(re.escape(BOT_USERNAME), "", text, flags=re.I).strip()
    return True, clean or "/help"


def check_telegram(seen, saved_items):
    offset = load_offset()
    reset_done = False

    try:
        data = TELEGRAM_SESSION.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": offset + 1, "timeout": 1},
            timeout=8,
        ).json()
    except Exception as exc:
        print("Telegram error:", exc)
        return False

    for update in data.get("result", []):
        update_id = update["update_id"]

        if update_id in PROCESSED_UPDATES:
            continue

        PROCESSED_UPDATES.add(update_id)
        if len(PROCESSED_UPDATES) > 1000:
            PROCESSED_UPDATES.clear()

        save_offset(update_id)

        msg = update.get("message", {})
        text = msg.get("text", "")
        chat_id = str(msg.get("chat", {}).get("id", ""))
        user_id = str(msg.get("from", {}).get("id", ""))

        if chat_id not in CHAT_IDS:
            continue

        handle, clean = should_handle_message(text, chat_id, user_id)
        if not handle:
            continue

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
            print("Found in-stock:", len(items))

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
                        send_item(item["name"], item["link"])
                        seen.add(item["id"])

                    saved_items[item["id"]] = item

                save_json(SEEN_FILE, list(seen))
                save_json(ITEMS_FILE, saved_items)

                print("New count:", new_count)
                print("Saved total:", len(seen))

        except Exception as exc:
            print("Main error:", exc)

        wait = random.randint(POLL_MIN_SECONDS, POLL_MAX_SECONDS)
        print("Waiting:", wait)

        for remaining in range(wait, 0, -1):
            if remaining % 5 == 0:
                reset_done = check_telegram(seen, saved_items)
                if reset_done:
                    first_run = True

            time.sleep(1)


def start_bot():
    print("Starting bot loop...")
    bot_loop()


BOT_THREAD_STARTED = False


def ensure_bot_started():
    global BOT_THREAD_STARTED
    if BOT_THREAD_STARTED:
        return
    BOT_THREAD_STARTED = True
    threading.Thread(target=start_bot, daemon=True).start()


ensure_bot_started()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

