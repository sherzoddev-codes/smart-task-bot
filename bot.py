import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
import pytz
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, 
    ContextTypes, ConversationHandler
)

# --- SOZLAMALAR ---
ADMIN_USERNAME = "coder_src"
TASHKENT_TZ = pytz.timezone('Asia/Tashkent')

# --- FLASK (Render uchun veb-server) ---
web_app = Flask(__name__)
@web_app.route('/')
def index():
    return "Bot is running perfectly!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# --- MA'LUMOTLAR BAZASI ---
def init_db():
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task TEXT, task_time TEXT, status TEXT, notified_10 INTEGER DEFAULT 0, notified_5 INTEGER DEFAULT 0, notified_0 INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS messages_to_admin (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, message_text TEXT, sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

# Holatlar
WAITING_FOR_TASK_TEXT, WAITING_FOR_TASK_TIME, WAITING_FOR_UPDATE_ID, WAITING_FOR_UPDATE_TEXT, WAITING_FOR_DONE_ID, WAITING_FOR_BROADCAST, WAITING_FOR_ADMIN_MESSAGE, WAITING_FOR_ADMIN_REPLY = range(8)
MENU_BUTTONS = ["➕ Vazifa qo'shish", "📋 Ro'yxatni ko'rish", "✏️ Vazifani yangilash", "✅ Bajarildi (O'chirish)", "📊 Statistika", "🗑 Hammasini tozalash", "👨‍💻 Adminga xabar yozish", "👑 Admin panel", "📢 Xabar tarqatish", "🔙 Asosiy menyu"]

def is_admin(user):
    return user.username and user.username.lower() == ADMIN_USERNAME.lower()

def get_admin_id():
    conn = sqlite3.connect('tasks.db')
    admin = conn.execute("SELECT user_id FROM users WHERE LOWER(username) = ?", (ADMIN_USERNAME.lower(),)).fetchone()
    conn.close()
    return admin[0] if admin else None

def get_main_keyboard(user):
    kb = [
        ["➕ Vazifa qo'shish", "📋 Ro'yxatni ko'rish"], 
        ["✏️ Vazifani yangilash", "✅ Bajarildi (O'chirish)"],
        ["📊 Statistika", "🗑 Hammasini tozalash"]
    ]
    # Agar foydalanuvchi admin bo'lmasa, "Adminga xabar yozish" tugmasini qo'shamiz
    if not is_admin(user):
        kb.append(["👨‍💻 Adminga xabar yozish"])
    
    if is_admin(user):
        kb.append(["👑 Admin panel"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# --- ESLATMALARNI TEKSHIRISH ---
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TASHKENT_TZ)
    time_now = now.strftime('%H:%M')
    time_in_5 = (now + timedelta(minutes=5)).strftime('%H:%M')
    time_in_10 = (now + timedelta(minutes=10)).strftime('%H:%M')

    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    tasks = cursor.execute("SELECT id, user_id, task, task_time, notified_10, notified_5, notified_0 FROM tasks WHERE status = 'pending'").fetchall()

    for t in tasks:
        task_id, user_id, task_text, task_time, n10, n5, n0 = t
        try:
            if task_time == time_in_10 and not n10:
                await context.bot.send_message(chat_id=user_id, text=f"⏰ **Eslatma!**\n\n\"{task_text}\" vazifasiga **10 daqiqa** qoldi!")
                cursor.execute("UPDATE tasks SET notified_10 = 1 WHERE id = ?", (task_id,))
            elif task_time == time_in_5 and not n5:
                await context.bot.send_message(chat_id=user_id, text=f"⚠️ **Tezroq bo'ling!**\n\n\"{task_text}\" vazifasiga **5 daqiqa** qoldi!")
                cursor.execute("UPDATE tasks SET notified_5 = 1 WHERE id = ?", (task_id,))
            elif task_time == time_now and not n0:
                await context.bot.send_message(chat_id=user_id, text=f"🚨 **Vaqt bo'ldi!**\n\n\"{task_text}\" vaqti keldi, bajarishni boshlang!")
                cursor.execute("UPDATE tasks SET notified_0 = 1 WHERE id = ?", (task_id,))
            conn.commit()
        except Exception:
            pass
    conn.close()

# --- BUYRUQLAR ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = user.username or ""
    
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
    if cursor.fetchone():
        cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user.id))
    else:
        cursor.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user.id, username))
    conn.commit()
    conn.close()

    welcome_text = "👋 **Smart Task Botga xush kelibsiz!**\n\nVazifalaringizni aniq vaqti bilan kiritib qo'ying, bot vaqti kelganda sizga eslatib turadi."
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(user), parse_mode="Markdown")
    return ConversationHandler.END

async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 **1-qadam:** Vazifa nomini yozing (Masalan: Kitob o'qish):", reply_markup=ReplyKeyboardMarkup([["🔙 Asosiy menyu"]], resize_keyboard=True))
    return WAITING_FOR_TASK_TEXT

async def add_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BUTTONS: return await start(update, context)
    context.user_data['new_task'] = update.message.text
    await update.message.reply_text("⏰ **2-qadam:** Vaqtni yuboring (Masalan: 13:00):")
    return WAITING_FOR_TASK_TIME

async def add_task_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BUTTONS: return await start(update, context)
    match = re.search(r'^([01]?\d|2[0-3]):([0-5]\d)$', update.message.text)
    if not match:
        await update.message.reply_text("❌ Noto'g'ri format! Soat:Daqiqa (Masalan: 13:00) ko'rinishida kiriting:")
        return WAITING_FOR_TASK_TIME
    time_str = f"{match.group(1).zfill(2)}:{match.group(2)}"
    conn = sqlite3.connect('tasks.db')
    conn.execute("INSERT INTO tasks (user_id, task, task_time, status) VALUES (?, ?, ?, ?)", (update.effective_user.id, context.user_data['new_task'], time_str, "pending"))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Vazifa qo'shildi!\n📌 {context.user_data['new_task']}\n⏰ {time_str}", reply_markup=get_main_keyboard(update.effective_user))
    return ConversationHandler.END

async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('tasks.db')
    tasks = conn.execute("SELECT id, task, task_time FROM tasks WHERE user_id = ? AND status = 'pending'", (update.effective_user.id,)).fetchall()
    conn.close()
    if not tasks: await update.message.reply_text("📭 Vazifalar yo'q.")
    else:
        text = "📋 **Vazifalar:**\n\n" + "\n".join([f"🔹 **ID: {t[0]}** — {t[1]} (⏰ {t[2]})" for t in tasks])
        await update.message.reply_text(text, parse_mode="Markdown")

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('tasks.db')
    t = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (update.effective_user.id,)).fetchone()[0]
    p = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'pending'", (update.effective_user.id,)).fetchone()[0]
    c = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'completed'", (update.effective_user.id,)).fetchone()[0]
    conn.close()
    await update.message.reply_text(f"📊 **Statistika:**\n\n📌 Jami: `{t}`\n⏳ Bajarilmagan: `{p}`\n🎉 Bajarilgan: `{c}`", parse_mode="Markdown")

async def ask_task_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔢 Bajarilgan vazifa ID raqamini yozing:", reply_markup=ReplyKeyboardMarkup([["🔙 Asosiy menyu"]], resize_keyboard=True))
    return WAITING_FOR_DONE_ID

async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BUTTONS: return await start(update, context)
    try:
        conn = sqlite3.connect('tasks.db')
        cursor = conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ? AND user_id = ? AND status = 'pending'", (int(update.message.text), update.effective_user.id))
        conn.commit()
        conn.close()
        if cursor.rowcount == 0: await update.message.reply_text("❌ ID topilmadi.", reply_markup=get_main_keyboard(update.effective_user))
        else: await update.message.reply_text("🎉 Bajarildi deb belgilandi!", reply_markup=get_main_keyboard(update.effective_user))
    except ValueError:
        await update.message.reply_text("❌ Faqat raqam kiriting.")
        return WAITING_FOR_DONE_ID
    return ConversationHandler.END

# ADMIN FUNKSIYALAR
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user): return
    conn = sqlite3.connect('tasks.db')
    u = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    t = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    conn.close()
    await update.message.reply_text(f"👑 **Admin Panel**\n\n👥 Foydalanuvchilar: `{u}`\n📌 Jami vazifalar: `{t}`", reply_markup=ReplyKeyboardMarkup([["📢 Xabar tarqatish"], ["🔙 Asosiy menyu"]], resize_keyboard=True), parse_mode="Markdown")

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user): return
    await update.message.reply_text("📢 Yubormoqchi bo'lgan xabaringizni yozing:", reply_markup=ReplyKeyboardMarkup([["🔙 Asosiy menyu"]], resize_keyboard=True))
    return WAITING_FOR_BROADCAST

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BUTTONS: return await start(update, context)
    conn = sqlite3.connect('tasks.db')
    users = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    for u in users:
        try: await context.bot.send_message(chat_id=u[0], text=f"📢 **E'lon:**\n\n{update.message.text}", parse_mode="Markdown")
        except: pass
    await update.message.reply_text("✅ Xabar hammaga yuborildi!", reply_markup=get_main_keyboard(update.effective_user))
    return ConversationHandler.END

# --- ADMIN BILAN BOG'LANISH (24 soatlik 2 ta xabar limiti bilan) ---
async def contact_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM messages_to_admin WHERE user_id = ? AND sent_at >= datetime('now', '-1 day')", (user_id,))
    msg_count = cursor.fetchone()[0]
    conn.close()
    
    if msg_count >= 2:
        await update.message.reply_text(
            "⚠️ **Xabar yuborish limiti tugadi!**\n\nSiz 24 soat ichida eng ko'pi bilan 2 ta xabar yoza olasiz. Iltimos, keyinroq yana urinib ko'ring.",
            reply_markup=get_main_keyboard(update.effective_user),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    await update.message.reply_text("✍️ Adminga xabaringizni yozing (Limit: 24 soatda 2 ta):", reply_markup=ReplyKeyboardMarkup([["🔙 Asosiy menyu"]], resize_keyboard=True))
    return WAITING_FOR_ADMIN_MESSAGE

async def contact_admin_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BUTTONS: return await start(update, context)
    
    user_id = update.effective_user.id
    text = update.message.text
    
    conn = sqlite3.connect('tasks.db')
    conn.execute("INSERT INTO messages_to_admin (user_id, message_text) VALUES (?, ?)", (user_id, text))
    conn.commit()
    conn.close()

    admin_id = get_admin_id()
    if not admin_id:
        await update.message.reply_text("❌ Admin hali botni ishga tushirmagan, xabar yuborib bo'lmaydi.", reply_markup=get_main_keyboard(update.effective_user))
        return ConversationHandler.END
        
    admin_msg = f"📩 **Yangi xabar!**\n👤 Ism: {update.effective_user.first_name}\n🆔 ID: `{user_id}`\n💬 Xabar: {text}"
    
    # Admin uchun javob berish va e'tiborsiz qoldirish tugmalari
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 Javob yozish", callback_data=f"reply_{user_id}"),
            InlineKeyboardButton("❌ E'tiborsiz qoldirish", callback_data="ignore")
        ]
    ])
    
    try:
        await context.bot.send_message(chat_id=admin_id, text=admin_msg, reply_markup=keyboard, parse_mode="Markdown")
        await update.message.reply_text("✅ Xabaringiz adminga muvaffaqiyatli yuborildi!", reply_markup=get_main_keyboard(update.effective_user))
    except Exception:
        await update.message.reply_text("❌ Xatolik yuz berdi.")
    return ConversationHandler.END

# --- ADMIN JAVOB BERISH VA IGNORE TUGMALARI ---
async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data == "ignore":
        await query.edit_message_text("❌ Xabar e'tiborsiz qoldirildi.")
        return ConversationHandler.END
        
    if data.startswith("reply_"):
        target_user_id = int(data.split("_")[1])
        context.user_data['reply_user_id'] = target_user_id
        await query.message.reply_text("✍️ Foydalanuvchiga yubormoqchi bo'lgan javobingizni yozing:")
        return WAITING_FOR_ADMIN_REPLY

async def send_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BUTTONS: return await start(update, context)
    
    target_user_id = context.user_data.get('reply_user_id')
    reply_text = update.message.text
    
    try:
        await context.bot.send_message(chat_id=target_user_id, text=f"👑 **Admin javobi:**\n\n{reply_text}", parse_mode="Markdown")
        await update.message.reply_text("✅ Javob foydalanuvchiga muvaffaqiyatli yuborildi!", reply_markup=get_main_keyboard(update.effective_user))
    except Exception:
        await update.message.reply_text("❌ Foydalanuvchiga xabar yuborib bo'lmadi (botni bloklagan bo'lishi mumkin).", reply_markup=get_main_keyboard(update.effective_user))
        
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)
    return ConversationHandler.END

# YANGILASH VA TOZALASH
async def update_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✏️ Yangilamoqchi bo'lgan vazifangiz ID raqamini yuboring:", reply_markup=ReplyKeyboardMarkup([["🔙 Asosiy menyu"]], resize_keyboard=True))
    return WAITING_FOR_UPDATE_ID

async def update_task_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BUTTONS: return await start(update, context)
    try:
        task_id = int(update.message.text)
        context.user_data['update_id'] = task_id
        await update.message.reply_text("📝 Yangi matn va vaqtni bitta xabarda yozing (Masalan: Kitob 15:00):")
        return WAITING_FOR_UPDATE_TEXT
    except ValueError:
        await update.message.reply_text("❌ Faqat raqam kiriting.")
        return WAITING_FOR_UPDATE_ID

async def update_task_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BUTTONS: return await start(update, context)
    match = re.search(r'(\d{1,2}):(\d{2})', update.message.text)
    new_time = f"{match.group(1).zfill(2)}:{match.group(2)}" if match else None
    conn = sqlite3.connect('tasks.db')
    conn.execute("UPDATE tasks SET task = ?, task_time = ?, notified_10 = 0, notified_5 = 0, notified_0 = 0 WHERE id = ? AND user_id = ?", (update.message.text, new_time, context.user_data.get('update_id'), update.effective_user.id))
    conn.commit()
    conn.close()
    await update.message.reply_text("✅ Vazifa yangilandi!", reply_markup=get_main_keyboard(update.effective_user))
    return ConversationHandler.END

async def clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('tasks.db')
    conn.execute("DELETE FROM tasks WHERE user_id = ?", (update.effective_user.id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("🗑 Barcha vazifalar tozalandi.")

def run_bot():
    TOKEN = "8703509119:AAFV0lziPzWSLeGaQoEE_pN8LlNWolshclI"
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.job_queue.run_repeating(check_reminders, interval=60, first=5)

    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex(r"^➕ Vazifa qo'shish$"), add_task_start)], states={WAITING_FOR_TASK_TEXT: [MessageHandler(filters.TEXT, add_task_text)], WAITING_FOR_TASK_TIME: [MessageHandler(filters.TEXT, add_task_time)]}, fallbacks=[CommandHandler("start", cancel)]))
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex(r"^✏️ Vazifani yangilash$"), update_task_start)], states={WAITING_FOR_UPDATE_ID: [MessageHandler(filters.TEXT, update_task_get_id)], WAITING_FOR_UPDATE_TEXT: [MessageHandler(filters.TEXT, update_task_save)]}, fallbacks=[CommandHandler("start", cancel)]))
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex(r"^✅ Bajarildi \(O'chirish\)$"), ask_task_id)], states={WAITING_FOR_DONE_ID: [MessageHandler(filters.TEXT, remove_task)]}, fallbacks=[CommandHandler("start", cancel)]))
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex(r"^📢 Xabar tarqatish$"), broadcast_start)], states={WAITING_FOR_BROADCAST: [MessageHandler(filters.TEXT, broadcast_send)]}, fallbacks=[CommandHandler("start", cancel)]))
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex(r"^👨‍💻 Adminga xabar yozish$"), contact_admin_start)], states={WAITING_FOR_ADMIN_MESSAGE: [MessageHandler(filters.TEXT, contact_admin_send)], WAITING_FOR_ADMIN_REPLY: [MessageHandler(filters.TEXT, send_admin_reply)]}, fallbacks=[CommandHandler("start", cancel)]))
    
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(reply_|ignore)"))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(r"^📋 Ro'yxatni ko'rish$"), show_tasks))
    app.add_handler(MessageHandler(filters.Regex(r"^📊 Statistika$"), show_stats))
    app.add_handler(MessageHandler(filters.Regex(r"^🗑 Hammasini tozalash$"), clear_all))
    app.add_handler(MessageHandler(filters.Regex(r"^👑 Admin panel$"), admin_panel))
    app.add_handler(MessageHandler(filters.Regex(r"^🔙 Asosiy menyu$"), start))
    
    app.run_polling(stop_signals=None, drop_pending_updates=True)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    run_flask()
