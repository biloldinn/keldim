import logging
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
import sqlite3
import json
import pytz
import os
from aiohttp import web

# Bot token va admin ID
API_TOKEN = '8019275951:AAH4bICBI9WfMyyG6rtsFu-QaeVwBUthXvA'
ADMIN_ID = 6762465157
BOT_USERNAME = "qulay_reklama_bot"
BOT_URL = f"https://t.me/{BOT_USERNAME}"

# Botni sozlash
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# Loglash
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Vaqt sozlamalari
TASHKENT_TZ = pytz.timezone('Asia/Tashkent')

# Ma'lumotlar bazasi
DB_NAME = 'bot_database.db'

# Tariflar (6 kunlik)
TARIFFS = {
    '50': {
        'price': 50000,
        'daily_limit': 2,
        'name': '50 minglik tarif (6 kun)',
        'duration_days': 6,
        'description': '6 kun davomida kunlik 2 ta reklama'
    },
    '70': {
        'price': 70000,
        'daily_limit': 3,
        'name': '70 minglik tarif (6 kun)',
        'duration_days': 6,
        'description': '6 kun davomida kunlik 3 ta reklama'
    }
}

# Foydalanuvchi holatlari
class UserStates(StatesGroup):
    waiting_for_ad_product_name = State()
    waiting_for_ad_product_description = State()
    waiting_for_ad_content = State()
    waiting_for_ad_phone = State()
    waiting_for_payment_confirmation = State()
    waiting_for_channel_invite = State()
    
    # Admin holatlari
    admin_waiting_for_channel_id = State()
    admin_waiting_for_invite_user_id = State()
    admin_waiting_for_invite_channel_name = State()

# --- DATABASE FUNCTIONS ---

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, registration_date TEXT, tariff_type TEXT, tariff_start_date TEXT, tariff_end_date TEXT, daily_ads_used INTEGER DEFAULT 0, total_ads_ordered INTEGER DEFAULT 0, last_ad_date TEXT, is_active INTEGER DEFAULT 1, has_discount INTEGER DEFAULT 0, discount_reason TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS ads (ad_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product_name TEXT, product_description TEXT, content_type TEXT, content_files TEXT, phone_number TEXT, status TEXT DEFAULT 'pending', created_date TEXT, published_date TEXT, tariff_type TEXT, sent_to_channels TEXT DEFAULT '[]', sent_to_users TEXT DEFAULT '[]', views INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS payments (payment_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER, original_amount INTEGER, discount_amount INTEGER DEFAULT 0, tariff_type TEXT, status TEXT DEFAULT 'pending', payment_date TEXT, confirmed_date TEXT, confirmed_by INTEGER, screenshot_file_id TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS bot_channels (channel_id TEXT PRIMARY KEY, channel_name TEXT, channel_type TEXT, member_count INTEGER DEFAULT 0, added_date TEXT, is_active INTEGER DEFAULT 1)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS channel_invites (invite_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, channel_id TEXT, channel_name TEXT, status TEXT DEFAULT 'pending', invited_date TEXT, responded_date TEXT, discount_applied INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS ad_statistics (stat_id INTEGER PRIMARY KEY AUTOINCREMENT, ad_id INTEGER, target_id TEXT, target_type TEXT, sent_date TEXT, views INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

async def add_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    current_time = datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, registration_date) VALUES (?, ?, ?, ?, ?)', (user_id, username, first_name, last_name, current_time))
    conn.commit()
    conn.close()

async def get_user_info(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        columns = ['user_id', 'username', 'first_name', 'last_name', 'registration_date', 'tariff_type', 'tariff_start_date', 'tariff_end_date', 'daily_ads_used', 'total_ads_ordered', 'last_ad_date', 'is_active', 'has_discount', 'discount_reason']
        return dict(zip(columns, user))
    return None

async def reset_user_daily_limit(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET daily_ads_used = 0, last_ad_date = ? WHERE user_id = ?', (datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d'), user_id))
    conn.commit()
    conn.close()

async def check_tariff_active(user_id):
    user = await get_user_info(user_id)
    if user and user['tariff_type'] and user['tariff_end_date']:
        try:
            end_date = datetime.strptime(user['tariff_end_date'], '%Y-%m-%d').date()
            today = datetime.now(TASHKENT_TZ).date()
            if end_date >= today:
                if user['last_ad_date'] == str(today):
                    if user['daily_ads_used'] < TARIFFS[user['tariff_type']]['daily_limit']:
                        return True, "active"
                    return False, "daily_limit_exceeded"
                else:
                    await reset_user_daily_limit(user_id)
                    return True, "active"
            return False, "tariff_expired"
        except: return False, "error"
    return False, "no_tariff"

async def add_advertisement(user_id, product_name, description, content_type, content_files, phone):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d %H:%M:%S')
    today_str = datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d')
    cursor.execute('SELECT tariff_type FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    tariff_type = user[0] if user else None
    cursor.execute('INSERT INTO ads (user_id, product_name, product_description, content_type, content_files, phone_number, created_date, tariff_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (user_id, product_name, description, content_type, json.dumps(content_files), phone, now_str, tariff_type))
    ad_id = cursor.lastrowid
    cursor.execute('UPDATE users SET total_ads_ordered = total_ads_ordered + 1, daily_ads_used = daily_ads_used + 1, last_ad_date = ? WHERE user_id = ?', (today_str, user_id))
    conn.commit()
    conn.close()
    return ad_id

async def add_payment(user_id, amount, original_amount, discount_amount, tariff_type, screenshot_file_id=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT INTO payments (user_id, amount, original_amount, discount_amount, tariff_type, payment_date, screenshot_file_id) VALUES (?, ?, ?, ?, ?, ?, ?)', (user_id, amount, original_amount, discount_amount, tariff_type, now_str, screenshot_file_id))
    pid = cursor.lastrowid
    conn.commit()
    conn.close()
    return pid

async def activate_tariff(user_id, tariff_type):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    start_date = datetime.now(TASHKENT_TZ)
    end_date = start_date + timedelta(days=6)
    cursor.execute('UPDATE users SET tariff_type = ?, tariff_start_date = ?, tariff_end_date = ?, daily_ads_used = 0, last_ad_date = ? WHERE user_id = ?', (tariff_type, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'), datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d'), user_id))
    conn.commit()
    conn.close()
    return end_date

async def get_bot_channels():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM bot_channels WHERE is_active = 1')
    ch = cursor.fetchall()
    conn.close()
    return [dict(zip(['channel_id', 'channel_name', 'channel_type', 'member_count', 'added_date', 'is_active'], r)) for r in ch]

async def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE is_active = 1')
    us = [r[0] for r in cursor.fetchall()]
    conn.close()
    return us

async def get_user_channel_invites(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM channel_invites WHERE user_id = ? AND status = 'pending'", (user_id,))
    inv = cursor.fetchall()
    conn.close()
    return [dict(zip(['invite_id', 'user_id', 'channel_id', 'channel_name', 'status', 'invited_date', 'responded_date', 'discount_applied'], r)) for r in inv]

async def accept_channel_invite(invite_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("UPDATE channel_invites SET status = 'accepted', responded_date = ?, discount_applied = 1 WHERE invite_id = ?", (now_str, invite_id))
    cursor.execute("SELECT user_id FROM channel_invites WHERE invite_id = ?", (invite_id,))
    row = cursor.fetchone()
    if row:
        uid = row[0]
        cursor.execute("UPDATE users SET has_discount = 1, discount_reason = 'Kanalga admin qilish' WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()
    return True

async def distribute_advertisement(ad_id):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM ads WHERE ad_id = ?', (ad_id,))
        ad = cursor.fetchone()
        if not ad: return 0, 0, 0
        columns = ['ad_id', 'user_id', 'product_name', 'product_description', 'content_type', 'content_files', 'phone_number', 'status', 'created_date', 'published_date', 'tariff_type', 'sent_to_channels', 'sent_to_users', 'views', 'clicks']
        ad_dict = dict(zip(columns, ad))
        cursor.execute('SELECT first_name, username FROM users WHERE user_id = ?', (ad_dict['user_id'],))
        u = cursor.fetchone()
        uname = f" @{u[1]}" if u and u[1] else ""
        ad_text = f"🎯 **{ad_dict['product_name']}**\n\n📝 *Tavsif:* {ad_dict['product_description']}\n\n📞 *Aloqa:* `{ad_dict['phone_number']}`\n👤 *Reklama beruvchi:* {u[0]}{uname}\n\n━━━━━━━━━━━━━━━━━━━━\n📢 **Reklama berish uchun:** @{BOT_USERNAME}\n✨ *Qulay reklama xizmati*\n🔖 #Reklama #Sotuv #Marketplace"
        bot_channels = await get_bot_channels()
        sent_ch = []
        files = json.loads(ad_dict['content_files'])
        ctype = ad_dict['content_type']
        
        for ch in bot_channels:
            try:
                if files:
                    if ctype == 'photo':
                        media = [types.InputMediaPhoto(media=f, caption=ad_text if i==0 else "", parse_mode="Markdown") for i, f in enumerate(files[:3])]
                        msgs = await bot.send_media_group(chat_id=ch['channel_id'], media=media)
                    else:
                        msgs = [await bot.send_video(chat_id=ch['channel_id'], video=files[0], caption=ad_text, parse_mode="Markdown")]
                else:
                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Reklama berish", url=BOT_URL)]])
                    msgs = [await bot.send_message(chat_id=ch['channel_id'], text=ad_text, parse_mode="Markdown", reply_markup=kb)]
                for m in msgs: cursor.execute('INSERT INTO ad_statistics (ad_id, target_id, target_type, sent_date) VALUES (?, ?, ?, ?)', (ad_id, str(m.message_id), 'channel_message', datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d %H:%M:%S')))
                sent_ch.append(ch['channel_id'])
            except Exception as e:
                logger.error(f"Error sending to channel {ch['channel_id']}: {e}")
                
        all_us = await get_all_users()
        sent_us = []
        for uid in all_us:
            if uid != ad_dict['user_id']:
                try:
                    if files:
                        if ctype == 'photo': await bot.send_media_group(chat_id=uid, media=[types.InputMediaPhoto(media=f, caption=ad_text if i==0 else "", parse_mode="Markdown") for i, f in enumerate(files[:3])])
                        else: await bot.send_video(chat_id=uid, video=files[0], caption=ad_text, parse_mode="Markdown")
                    else:
                        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Reklama berish", url=BOT_URL)]])
                        await bot.send_message(chat_id=uid, text=ad_text, parse_mode="Markdown", reply_markup=kb)
                    sent_us.append(uid)
                except: pass
                
        pub_time = datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('UPDATE ads SET status = "published", published_date = ?, sent_to_channels = ?, sent_to_users = ?, views = ? WHERE ad_id = ?', (pub_time, json.dumps(sent_ch), json.dumps(sent_us), len(sent_ch) + len(sent_us), ad_id))
        conn.commit()
        conn.close()
        return len(sent_ch), len(sent_us), len(sent_ch) + len(sent_us)
    except Exception as e:
        logger.error(f"Distribute error: {e}")
        return 0, 0, 0

async def get_pending_payments():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT p.*, u.first_name, u.username FROM payments p LEFT JOIN users u ON p.user_id = u.user_id WHERE p.status = "pending" ORDER BY p.payment_date DESC')
    res = cursor.fetchall()
    conn.close()
    return [dict(zip(['payment_id', 'user_id', 'amount', 'original_amount', 'discount_amount', 'tariff_type', 'status', 'payment_date', 'confirmed_date', 'confirmed_by', 'screenshot_file_id', 'first_name', 'username'], r)) for r in res]

# --- KEYBOARDS ---

async def main_menu(user_id):
    kb = [
        [InlineKeyboardButton(text="📢 Reklama berish", callback_data="place_ad"), InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
        [InlineKeyboardButton(text="ℹ️ Mening tarifim", callback_data="my_tariff"), InlineKeyboardButton(text="💰 Tarif sotib olish", callback_data="buy_tariff")]
    ]
    invites = await get_user_channel_invites(user_id)
    if invites: kb.append([InlineKeyboardButton(text="🎁 Kanal takliflari", callback_data="channel_invites")])
    if user_id == ADMIN_ID: kb.append([InlineKeyboardButton(text="👑 Admin panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def tariffs_menu(user_info=None):
    kb = []
    for k, t in TARIFFS.items():
        price = t['price']
        txt = f"{t['name']} - {price:,} so'm"
        if user_info and user_info['has_discount']: txt += f" (Chegirma bilan: {max(price-20000,0):,} so'm)"
        kb.append([InlineKeyboardButton(text=txt, callback_data=f"tariff_{k}")])
    kb.append([InlineKeyboardButton(text="Orqaga", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- AUTOMATIC CHANNEL DETECTION ---

@router.my_chat_member()
async def on_my_chat_member(update: types.ChatMemberUpdated):
    cid = str(update.chat.id)
    cname = update.chat.title or update.chat.full_name or "Noma'lum"
    ctype = update.chat.type
    
    status = update.new_chat_member.status
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    if status in ["administrator", "member"]:
        cursor.execute("INSERT OR REPLACE INTO bot_channels (channel_id, channel_name, channel_type, added_date, is_active) VALUES (?, ?, ?, ?, ?)", 
                       (cid, cname, ctype, datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d %H:%M:%S'), 1))
        logger.info(f"Yangi chat qo'shildi: {cname} ({cid})")
    elif status in ["left", "kicked"]:
        cursor.execute("UPDATE bot_channels SET is_active = 0 WHERE channel_id = ?", (cid,))
        logger.info(f"Chatdan chiqarildi: {cname} ({cid})")
        
    conn.commit()
    conn.close()

# --- HANDLERS ---

@router.message(Command("start"))
async def cmd_start(message: Message):
    await add_user(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    active, _ = await check_tariff_active(message.from_user.id)
    invites = await get_user_channel_invites(message.from_user.id)
    txt = f"🤖 **@{BOT_USERNAME}** - Mukammal reklama platformasi\n\n👋 Assalomu alaykum, {message.from_user.first_name}!\n\n✨ **Bot imkoniyatlari:**\n• 📢 Reklamangiz barcha kanallarimizga va guruhlarimizga va faol foydalanuvchilarimizga yuboriladi\n• 📊 Har bir reklama statistikasini ko'ring\n• ⏳ Tarif davomiyligi: 6 kun\n\n📅 **Tarif holati:** {'✅ Aktiv' if active else '❌ Aktiv emas'}\n\n[Bot manzili]({BOT_URL})"
    if invites: txt += "\n\n🎁 **Sizda kanal takliflari bor!**"
    await message.answer(txt, reply_markup=await main_menu(message.from_user.id), parse_mode="Markdown")

# --- ADMIN COMMANDS ---

@router.callback_query(F.data == "admin_panel")
async def admin_panel(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = await get_pending_payments()
    ch = await get_bot_channels()
    txt = f"👑 **Admin Panel**\n\n💰 Kutilayotgan to'lovlar: {len(p)} ta\n📢 Faol kanallar: {len(ch)} ta"
    if ch:
        txt += "\n\n📋 **Kanallar ro'yxati:**"
        for i, channel in enumerate(ch, 1):
            txt += f"\n{i}. {channel['channel_name']} (`{channel['channel_id']}`)"
            
    kb = [
        [InlineKeyboardButton(text="💰 To'lovlar", callback_data="check_payments")],
        [InlineKeyboardButton(text="📢 Kanal qo'shish (Manual)", callback_data="admin_add_channel")],
        [InlineKeyboardButton(text="🎁 Taklif yaratish", callback_data="admin_create_invite")]
    ]
    await c.message.answer(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")
    await c.answer()

@router.callback_query(F.data == "admin_add_channel")
async def admin_add_channel_start(c: CallbackQuery, state: FSMContext):
    await c.message.answer("Kanal yoki guruh ID sini yuboring (masalan: -100...):")
    await state.set_state(UserStates.admin_waiting_for_channel_id)
    await c.answer()

@router.message(UserStates.admin_waiting_for_channel_id)
async def admin_add_channel_finalize(m: Message, state: FSMContext):
    cid = m.text.strip()
    try:
        chat = await bot.get_chat(cid)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO bot_channels (channel_id, channel_name, channel_type, added_date, is_active) VALUES (?, ?, ?, ?, ?)", 
                       (str(chat.id), chat.title or chat.full_name, chat.type, datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d %H:%M:%S'), 1))
        conn.commit()
        conn.close()
        await m.answer(f"✅ Kanal qo'shildi: {chat.title} ({chat.id})")
        await state.clear()
    except Exception as e:
        await m.answer(f"❌ Xatolik: {e}\nBot kanalga qo'shilgan va admin ekanligiga ishonch hosil qiling.")

@router.callback_query(F.data == "admin_create_invite")
async def admin_create_invite_start(c: CallbackQuery, state: FSMContext):
    await c.message.answer("Foydalanuvchi ID sini yuboring:")
    await state.set_state(UserStates.admin_waiting_for_invite_user_id)
    await c.answer()

@router.message(UserStates.admin_waiting_for_invite_user_id)
async def admin_create_invite_uid(m: Message, state: FSMContext):
    if m.text.isdigit():
        await state.update_data(target_uid=int(m.text))
        await m.answer("Kanal nomini yuboring (chegirma uchun shart):")
        await state.set_state(UserStates.admin_waiting_for_invite_channel_name)
    else:
        await m.answer("Iltimos faqat raqamli ID yuboring.")

@router.message(UserStates.admin_waiting_for_invite_channel_name)
async def admin_create_invite_finalize(m: Message, state: FSMContext):
    d = await state.get_data()
    uid = d['target_uid']
    cname = m.text.strip()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO channel_invites (user_id, channel_id, channel_name, invited_date) VALUES (?, ?, ?, ?)", (uid, "manual", cname, datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()
    await m.answer(f"✅ Foydalanuvchi {uid} uchun taklif yaratildi.")
    try:
        await bot.send_message(uid, f"🎁 Sizga yangi kanal taklifi keldi! {cname} kanaliga admin qilib qo'shsangiz 20,000 so'm chegirma olasiz.", reply_markup=await main_menu(uid))
    except: pass
    await state.clear()

# --- OTHER HANDLERS ---

@router.callback_query(F.data == "channel_invites")
async def show_invites(c: CallbackQuery):
    inv = await get_user_channel_invites(c.from_user.id)
    if not inv: return await c.answer("Takliflar yo'q!")
    for i in inv:
        kb = [[InlineKeyboardButton(text="✅ Qabul qilish", callback_data=f"accept_inv_{i['invite_id']}"), InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_inv_{i['invite_id']}")]]
        await c.message.answer(f"📢 Kanal: {i['channel_name']}\n🎁 Chegirma: 20,000 so'm", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await c.answer()

@router.callback_query(F.data.startswith("accept_inv_"))
async def accept_inv(c: CallbackQuery):
    iid = int(c.data.split("_")[2])
    await accept_channel_invite(iid)
    await c.message.edit_text("✅ Taklif qabul qilindi! Endi tarif sotib olayotganda chegirma qo'llaniladi.")
    await c.answer()

@router.callback_query(F.data == "buy_tariff")
async def buy_tariff_cb(c: CallbackQuery):
    u = await get_user_info(c.from_user.id)
    await c.message.answer("💰 **Tariflar (6 kun)**\n\nTanlang:", reply_markup=tariffs_menu(u), parse_mode="Markdown")
    await c.answer()

@router.callback_query(F.data == "stats")
async def stats_cb(c: CallbackQuery):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*), SUM(views), SUM(clicks) FROM ads WHERE user_id = ? AND status = "published"', (c.from_user.id,))
    s = cursor.fetchone()
    txt = f"📊 **Statistika**\n\n📢 Jami: {s[0] or 0} ta\n👁️ Ko'rishlar: {s[1] or 0} ta\n👆 Bosishlar: {s[2] or 0} ta"
    await c.message.answer(txt, parse_mode="Markdown")
    await c.answer()

@router.callback_query(F.data == "my_tariff")
async def my_tariff_cb(c: CallbackQuery):
    u = await get_user_info(c.from_user.id)
    if not u or not u['tariff_type']: return await c.answer("Tarif yo'q!", show_alert=True)
    txt = f"📋 **Tarif:** {TARIFFS[u['tariff_type']]['name']}\n📅 Tugaydi: {u['tariff_end_date']}\n📊 Bugun: {u['daily_ads_used']}/{TARIFFS[u['tariff_type']]['daily_limit']}"
    await c.message.answer(txt)
    await c.answer()

@router.callback_query(F.data == "place_ad")
async def place_ad_cb(c: CallbackQuery, state: FSMContext):
    active, status = await check_tariff_active(c.from_user.id)
    if not active:
        msg = "❌ Tarif yo'q!" if status == "no_tariff" else "❌ Muddat tugadi!" if status == "tariff_expired" else "⚠️ Limit tugadi!"
        await c.message.answer(msg, reply_markup=await main_menu(c.from_user.id))
        return await c.answer()
    await c.message.answer("1️⃣ Mahsulot nomini kiriting:")
    await state.set_state(UserStates.waiting_for_ad_product_name)
    await c.answer()

@router.message(UserStates.waiting_for_ad_product_name)
async def ad_name_handler(m: Message, state: FSMContext):
    await state.update_data(product_name=m.text)
    await m.answer("2️⃣ Tavsif kiriting:")
    await state.set_state(UserStates.waiting_for_ad_product_description)

@router.message(UserStates.waiting_for_ad_product_description)
async def ad_desc_handler(m: Message, state: FSMContext):
    await state.update_data(product_description=m.text)
    await m.answer("3️⃣ Rasm/video yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Rasmsiz", callback_data="no_content")]]))
    await state.set_state(UserStates.waiting_for_ad_content)

@router.callback_query(F.data == "no_content", UserStates.waiting_for_ad_content)
async def no_content_handler(c: CallbackQuery, state: FSMContext):
    await state.update_data(content_type="none", content_files=[])
    await c.message.answer("4️⃣ Telefon raqam:")
    await state.set_state(UserStates.waiting_for_ad_phone)
    await c.answer()

@router.message(UserStates.waiting_for_ad_content, F.photo | F.video)
async def ad_file_handler(m: Message, state: FSMContext):
    d = await state.get_data()
    files = d.get('content_files', [])
    if m.photo:
        files.append(m.photo[-1].file_id)
        await state.update_data(content_files=files, content_type='photo')
        await m.answer(f"✅ Rasm ({len(files)}/3). 'Tayyor' deb yozing.")
    elif m.video and not files:
        await state.update_data(content_files=[m.video.file_id], content_type='video')
        await m.answer("✅ Video. 'Tayyor' deb yozing.")

@router.message(UserStates.waiting_for_ad_content, F.text.lower() == "tayyor")
async def ad_ready_handler(m: Message, state: FSMContext):
    await m.answer("4️⃣ Telefon raqam:")
    await state.set_state(UserStates.waiting_for_ad_phone)

@router.message(UserStates.waiting_for_ad_phone)
async def ad_phone_handler(m: Message, state: FSMContext):
    await state.update_data(phone=m.text)
    d = await state.get_data()
    txt = f"📋 **Reklama**\n\n📦 {d['product_name']}\n📝 {d['product_description']}\n📞 {m.text}\n\n📢 Barcha kanallar va guruhlarga yuboriladi."
    await m.answer(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="confirm_ad"), InlineKeyboardButton(text="❌ Bekor", callback_data="back_to_main")]]), parse_mode="Markdown")

@router.callback_query(F.data == "confirm_ad")
async def confirm_ad_handler(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    aid = await add_advertisement(c.from_user.id, d['product_name'], d['product_description'], d.get('content_type', 'none'), d.get('content_files', []), d['phone'])
    ch, us, tot = await distribute_advertisement(aid)
    await c.message.edit_text(f"✅ Tarqatildi!\n📢 Kanallar: {ch}\n👥 Foydalanuvchilar: {us}\n📨 Jami: {tot}")
    await state.clear()
    await c.answer()

@router.callback_query(F.data == "check_payments")
async def check_payments_admin(c: CallbackQuery):
    p = await get_pending_payments()
    if not p: return await c.answer("To'lovlar yo'q!")
    for pay in p:
        await c.message.answer(f"💰 To'lov #{pay['payment_id']}\n👤 @{pay['username']}\n💰 {pay['amount']:,} so'm\n📦 {pay['tariff_type']}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅", callback_data=f"confirm_pay_{pay['payment_id']}"), InlineKeyboardButton(text="❌", callback_data=f"reject_pay_{pay['payment_id']}")]]))
    await c.answer()

@router.callback_query(F.data.startswith("confirm_pay_"))
async def confirm_pay_admin(c: CallbackQuery):
    pid = int(c.data.split("_")[2])
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, tariff_type, amount FROM payments WHERE payment_id = ?", (pid,))
    pay = cursor.fetchone()
    if pay:
        uid, tkey, amt = pay
        await activate_tariff(uid, tkey)
        cursor.execute("UPDATE payments SET status = 'confirmed' WHERE payment_id = ?", (pid,))
        cursor.execute("UPDATE users SET has_discount = 0 WHERE user_id = ?", (uid,))
        conn.commit()
        await bot.send_message(uid, f"✅ To'lov tasdiqlandi! Tarif faol (6 kun).")
        await c.message.edit_text(f"✅ To'lov #{pid} tasdiqlandi.")
    conn.close()
    await c.answer()

@router.callback_query(F.data == "back_to_main")
async def back_cb(c: CallbackQuery):
    await c.message.answer("Asosiy menyu:", reply_markup=await main_menu(c.from_user.id))
    await c.answer()

# --- SYSTEM ---

async def handle_ping(request): return web.Response(text="Alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080))).start()

async def main():
    init_db()
    asyncio.create_task(start_web_server())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
