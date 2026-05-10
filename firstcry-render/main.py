    import json
import os
import random
import re
import threading
import time
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
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

SCAN_PAGES = int(os.environ.get("SCAN_PAGES", "8"))
POLL_MIN_SECONDS = int(os.environ.get("POLL_MIN_SECONDS", "30"))
POLL_MAX_SECONDS = int(os.environ.get("POLL_MAX_SECONDS", "40"))

PROCESSED_UPDATES = set()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

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


def send_message(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": False},
            timeout=8,
        )
    except Exception as exc:
        print("send_message error:", exc)


def send_item(name, link, image, price=""):
    price_line = f"\nPrice: {price}" if price else ""
    caption = f"New FirstCry Item\n\n{name}{price_line}\n\n{link}"

    for chat_id in CHAT_IDS:
        try:
            if image:
                r = requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data={"chat_id": chat_id, "photo": image, "caption": caption[:1024]},
                    timeout=12,
                )
                if not r.ok:
                    print("sendPhoto failed:", r.status_code, r.text[:200])
                    send_message(chat_id, caption)
            else:
                send_message(chat_id, caption)
            print("Alert sent:", name, price)
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
        {"command": "test_notify", "description": "Send one test notification"},
        {"command": "slay", "description": "Owner roast command"},
        {"command": "help", "description": "Show commands"},
    ]
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands",
            json={"commands": commands},
            timeout=8,
        )
        print("Telegram commands updated")
    except Exception as exc:
        print("set commands error:", exc)


def clean_text(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\bADD TO CART\b.*", "", text, flags=re.I).strip()
    text = re.sub(r"\bNotify Me\b.*", "", text, flags=re.I).strip()
    return text


def normalize_price(text):
    if not text:
        return ""
    match = re.search(r"(?:₹|Rs\.?|MRP)\s*[\d,]+(?:\.\d+)?", text, re.I)
    if not match:
        return ""
    price = match.group(0)
    price = price.replace("MRP", "Rs.").replace("Rs ", "Rs. ")
    return clean_text(price)


def format_price(value):
    if value in (None, ""):
        return ""
    try:
        number = float(str(value).replace(",", ""))
        if number.is_integer():
            return f"Rs. {int(number)}"
        return f"Rs. {number:.2f}"
    except Exception:
        return normalize_price(str(value)) or str(value)


def product_slug(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "product"


def get_product_id(link):
    link = link.split("?")[0].split("#")[0].rstrip("/")
    nums = re.findall(r"\d{5,}", link)
    if nums:
        return nums[-1]
    return link.lower()


def looks_like_product_link(link):
    lower = link.lower()
    bad = ["javascript:", "#", "/cart", "/login", "/wishlist", "whatsapp", "facebook", "instagram"]
    if any(x in lower for x in bad):
        return False
    return "firstcry.com" in lower and (
        "/catalog/productdetails" in lower
        or "/product-detail" in lower
        or "/p/" in lower
        or re.search(r"/[a-z0-9-]+/\d{5,}", lower)
    )


def find_image(block, page_url):
    if not block:
        return ""
    img = block.find("img")
    if not img:
        return ""
    src = (
        img.get("src")
        or img.get("data-src")
        or img.get("data-original")
        or img.get("data-lazy")
        or img.get("data-original-src")
        or ""
    )
    return urljoin(page_url, src) if src else ""


def find_name(block, anchor):
    candidates = []
    if anchor:
        candidates.append(anchor.get_text(" ", strip=True))
        candidates.append(anchor.get("title", ""))
    if block:
        for attr in ["title", "data-name", "data-product-name"]:
            candidates.append(block.get(attr, ""))
        for selector in [
            ".prod-name",
            ".product-name",
            ".p-name",
            ".li_txt1",
            ".li_txt2",
            ".prod-title",
            "h2",
            "h3",
        ]:
            found = block.select_one(selector)
            if found:
                candidates.append(found.get_text(" ", strip=True))
        img = block.find("img")
        if img:
            candidates.append(img.get("alt", ""))

    for value in candidates:
        name = clean_text(value)
        if not name:
            continue
        lower = name.lower()
        if any(x in lower for x in ["logo", "banner", "payment", "whatsapp"]):
            continue
        if "hot wheels" in lower or "hotwheels" in lower or len(name) > 12:
            return name[:180]
    return "FirstCry Hot Wheels Item"


def find_product_block(anchor):
    block = anchor
    best = anchor
    for _ in range(8):
        parent = block.find_parent()
        if not parent:
            break
        block = parent
        text = parent.get_text(" ", strip=True).lower()
        if any(x in text for x in ["add to cart", "mrp", "₹", "rs.", "hot wheels", "hotwheels"]):
            best = parent
        if len(text) > 80 and ("₹" in text or "rs" in text or "mrp" in text):
            break
    return best


def parse_items_from_html(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for anchor in soup.find_all("a", href=True):
        link = urljoin(BASE_URL, anchor["href"]).split("#")[0]
        if not looks_like_product_link(link):
            continue

        block = find_product_block(anchor)
        block_text = block.get_text(" ", strip=True) if block else anchor.get_text(" ", strip=True)
        link_text = anchor.get_text(" ", strip=True)
        combined = f"{link_text} {block_text} {link}"
        lower = combined.lower()

        if "hot wheels" not in lower and "hotwheels" not in lower:
            continue

        name = find_name(block, anchor)
        price = normalize_price(block_text)
        image = find_image(block, page_url)

        items.append(
            {
                "id": get_product_id(link),
                "name": name,
                "price": price,
                "link": link,
                "image": image,
            }
        )

    unique = {}
    for item in items:
        unique[item["id"]] = item
    return list(unique.values())


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

        link = f"{BASE_URL}/hot-wheels/{product_slug(name)}/{pid}/product-detail"

        image = ""
        images = str(product.get("Images") or "").split(";")
        first_image = next((x.strip() for x in images if x.strip()), "")
        if first_image:
            image = f"https://cdn.fcglcdn.com/brainbees/images/products/219x265/{first_image}"

        price = (
            format_price(product.get("clubprice"))
            or format_price(product.get("discprice"))
            or format_price(product.get("MRP"))
        )

        stock = 0
        try:
            stock = int(float(str(product.get("CrntStock") or "0")))
        except Exception:
            stock = 0

        items.append(
            {
                "id": pid,
                "name": name[:180] or "FirstCry Hot Wheels Item",
                "price": price,
                "link": link,
                "image": image,
                "stock": stock,
            }
        )

    unique = {}
    for item in items:
        unique[item["id"]] = item
    return list(unique.values())


def page_url(page_no):
    if page_no <= 1:
        return PAGE_URL
    sep = "&" if "?" in PAGE_URL else "?"
    return f"{PAGE_URL}{sep}page={page_no}"


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


def get_items():
    all_items = []

    for page_no in range(1, SCAN_PAGES + 1):
        url = page_url(page_no)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            print("Page:", page_no, r.status_code, "Size:", len(r.text))
            all_items.extend(parse_items_from_html(r.text, url))
        except Exception as exc:
            print("Page error:", page_no, exc)

        try:
            api = api_url(page_no)
            r = requests.get(api, headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"}, timeout=15)
            print("API:", page_no, r.status_code, "Size:", len(r.text))
            api_items = parse_items_from_api_response(r.text)
            print("API items:", page_no, len(api_items))
            all_items.extend(api_items)
        except Exception as exc:
            print("API error:", page_no, exc)

        time.sleep(random.uniform(0.7, 1.5))

    unique = {}
    for item in all_items:
        unique[item["id"]] = item

    return list(unique.values())


def not_owner_reply(user_id):
    if user_id == OWNER_ID:
        return None
    return random.choice(
        [
            "Are you owner?",
            "Permission denied.",
            "Nice try, but you're not the boss.",
            "Access denied.",
        ]
    )


def item_line(i, item):
    price = f"\nPrice: {item.get('price')}" if item.get("price") else ""
    return f"{i}. {item['name']}{price}\n{item['link']}\n\n"


def command_reply(text, seen, saved_items, user_id):
    t = text.lower().strip()
    cmd = t.split()[0] if t else ""

    if "hello" in t or "helo" in t or t == "hi":
        if user_id == OWNER_ID:
            return "Yo boss, FirstCry bot is running."
        return "Bot is running."

    if cmd == "/status":
        deny = not_owner_reply(user_id)
        if deny:
            return deny
        return f"FirstCry bot running\nSaved items: {len(seen)}\nScan pages: {SCAN_PAGES}\nTracking:\n{PAGE_URL}"

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
        return "Reset done. Next scan will silently save current items again."

    if cmd == "/test_notify":
        deny = not_owner_reply(user_id)
        if deny:
            return deny
        items = list(saved_items.values())
        if not items:
            return "No saved items to test."
        item = items[-1]
        send_item(item["name"], item["link"], item.get("image", ""), item.get("price", ""))
        return f"Test notification sent:\n{item['name']}"

    if cmd == "/slay":
        deny = not_owner_reply(user_id)
        if deny:
            return deny
        target = text[5:].strip()
        if not target:
            return "Use like: /slay udit"
        return random.choice(
            [
                f"{target}, relax bro. The bot is working.",
                f"{target}, refreshing 900 times will not summon the stock.",
                f"{target}, even FirstCry is tired of seeing you reload.",
            ]
        )

    if cmd == "/help":
        deny = not_owner_reply(user_id)
        if deny:
            return deny
        return (
            "Owner Commands:\n"
            "/status\n/saved\n/items\n/last\n/remove_last\n/reset\n"
            "/test_notify\n/slay name\n/help"
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
        data = requests.get(
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
                        send_item(item["name"], item["link"], item.get("image", ""), item.get("price", ""))
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


if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
