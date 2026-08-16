import os
import re
import sqlite3
import threading
from datetime import datetime
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from apscheduler.schedulers.background import BackgroundScheduler

# --- FLASK (Render uchun veb-server) ---
web_app = Flask(__name__)
@web_app.route('/')
def index():
    return "Smart Task Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# --- MA'LUMOTLAR BAZASI (SQLite) ---
def init_db():
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id INTEGER, 
            task TEXT, 
            task_time TEXT,
            status TEXT,
            notified_10 INTEGER DEFAULT 0,
            notified_5 INTEGER DEFAULT 0,
            notified_0 INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Adminning Telegram usernami
ADMIN_USERNAME = "coder_src"

# Holatlar
WAITING_FOR_TASK = 1
WAITING_FOR_UPDATE_ID = 2
WAITING_FOR_UPDATE_TEXT = 3
WAITING_FOR_DONE_ID = 4
WAITING_FOR_BROADCAST = 5

MENU_BUTTONS = [
    "➕ Vazifa qo'shish", 
    "📋 Ro'yxatni ko'rish", 
    "✏️ Vazifani yangilash", 
    "✅ Bajarildi (O'chirish)",
    "📊 Statistika", 
    "🗑 Hammasini tozalash",
    "👑 Admin panel",
    "📢 Xabar tarqatish"
]

bot_app_instance = None

# --- VAZIFA VAQTINI AJRATIB OLISH ---
def extract_time(text):
    match = re.search(r'(\d{1,2}):(\d{2})', text)
    if match:
        hours = match.group(1).zfill(2)
        minutes = match.group(2)
        return f"{hours}:{minutes}"
    return None

# --- FONDA ESLATISH ---
def check_reminders():
    global bot_app_instance
    if not bot_app_instance:
        return

    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute
    current_total_minutes = current_hour * 60 + current_minute

    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    tasks = cursor.execute(
        "SELECT id, user_id, task, task_time, notified_10, notified_5, notified_0 FROM tasks WHERE status = 'pending' AND task_time IS NOT NULL"
    ).fetchall()

    for t in tasks:
        task_id, user_id, task_text, task_time, n10, n5, n0 = t
        try:
            t_hour, t_min = map(int, task_time.split(':'))
            task_total_minutes = t_hour * 60 + t_min
            diff = task_total_minutes - current_total_minutes

            if diff == 10 and not n10:
                bot_app_instance.bot.send_message(chat_id=user_id, text=f"⏰ **Eslatma!**\n\n\"{task_text}\" vazifasiga **10 daqiqa** qoldi!")
                cursor.execute("UPDATE tasks SET notified_10 = 1 WHERE id = ?", (task_id,))
                conn.commit()
            elif diff == 5 and not n5:
                bot_app_instance.bot.send_message(chat_id=user_id, text=f"⚠️ **Tezroq bo'ling!**\n\n\"{task_text}\" vazifasiga **5 daqiqa** qoldi!")
                cursor.execute("UPDATE tasks SET notified_5 = 1 WHERE id = ?", (task_id,))
                conn.commit()
            elif diff == 0 and not n0:
                bot_app_instance.bot.send_message(chat_id=user_id, text=f"🚨 **Vaqt bo'ldi!**\n\n\"{task_text}\" vaqti keldi!")
                cursor.execute("UPDATE tasks SET notified_0 = 1 WHERE id = ?", (task_id,))
                conn.commit()
        except Exception as e:
            print(f"Xatolik: {e}")
    conn.close()

# --- FOYDALANUVCHINI BAZAGA QO'SHISH ---
def register_user(user_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

# --- BOT MENYUSI ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id)

    kb = [
        ["➕ Vazifa qo'shish", "📋 Ro'yxatni ko'rish"], 
        ["✏️ Vazifani yangilash", "✅ Bajarildi (O'chirish)"],
        ["📊 Statistika", "🗑 Hammasini tozalash"]
    ]
    
    if user.username and f"@{user.username.lower()}" == f"@{ADMIN_USERNAME.lower()}":
        kb.append(["👑 Admin panel"])

    await update.message.reply_text(
        "👋 **Smart Task Bot**ga xush kelibsiz!\n"
        "Vazifangizni va vaqtini erkin yozib qoldirishingiz mumkin (masalan: *Dasturlash 15:00*).", 
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# 1. Vazifa qo'shish
async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 Vazifangiz va vaqtini yozib yuboring\n*(Masalan: Dasturlash 15:00)*:")
    return WAITING_FOR_TASK

async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Agar foydalanuvchi boshqa menyu tugmasini bosib yuborsa
    if text in MENU_BUTTONS:
        await update.message.reply_text("⚠️ Vazifa kiritish to'xtatildi.")
        return ConversationHandler.END

    user_id = update.effective_user.id
    extracted_time = extract_time(text)
    
    conn = sqlite3.connect('tasks.db')
    conn.execute("INSERT INTO tasks (user_id, task, task_time, status) VALUES (?, ?, ?, ?)", (user_id, text, extracted_time, "pending"))
    conn.commit()
    conn.close()
    
    msg = "✅ Vazifa muvaffaqiyatli ro'yxatga qo'shildi!"
    if extracted_time:
        msg += f"\n⏰ Eslatma vaqti belgilandi: **{extracted_time}**"

    await update.message.reply_text(msg, parse_mode="Markdown")
    return ConversationHandler.END

# 2. Ro'yxatni ko'rish
async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('tasks.db')
    tasks = conn.execute("SELECT id, task FROM tasks WHERE user_id = ? AND status = 'pending'", (user_id,)).fetchall()
    conn.close()
    
    if not tasks:
        await update.message.reply_text("📭 Hozircha bajarilmagan vazifalaringiz yo'q.")
    else:
        text = "📋 **Sizning vazifalaringiz:**\n\n"
        for t in tasks:
            text += f"🔹 **ID: {t[0]}** — {t[1]}\n"
        text += "\n*(Vazifani bajarib bo'lgach, ID raqami orqali o'chirishingiz mumkin)*"
        await update.message.reply_text(text, parse_mode="Markdown")

# 3. Statistika
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('tasks.db')
    total = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'pending'", (user_id,)).fetchone()[0]
    conn.close()
    
    completed = total - pending
    text = (
        f"📊 **Statistika:**\n\n"
        f"📌 Jami vazifalar: `{total}`\n"
        f"⏳ Bajarilishi kerak: `{pending}`\n"
        f"🎉 Bajarilganlar: `{completed}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# 4. Vazifani yangilash
async def update_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✏️ Yangilamoqchi bo'lgan vazifangizning **ID raqamini** yuboring:")
    return WAITING_FOR_UPDATE_ID

async def update_task_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in MENU_BUTTONS:
        await update.message.reply_text("⚠️ Amaliyot bekor qilindi.")
        return ConversationHandler.END

    try:
        task_id = int(text)
        user_id = update.effective_user.id
        
        conn = sqlite3.connect('tasks.db')
        task = conn.execute("SELECT task FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).fetchone()
        conn.close()
        
        if not task:
            await update.message.reply_text("❌ Bunday ID raqamli vazifa topilmadi.")
            return ConversationHandler.END
        
        context.user_data['update_id'] = task_id
        await update.message.reply_text(f"📝 Eski vazifa: *{task[0]}*\n\nEndi yangi matn va vaqtni yuboring:")
        return WAITING_FOR_UPDATE_TEXT
    except ValueError:
        await update.message.reply_text("❌ Faqat raqam kiriting.")
        return WAITING_FOR_UPDATE_ID

async def update_task_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_text = update.message.text
    if new_text in MENU_BUTTONS:
        await update.message.reply_text("⚠️ Amaliyot bekor qilindi.")
        return ConversationHandler.END

    task_id = context.user_data.get('update_id')
    user_id = update.effective_user.id
    extracted_time = extract_time(new_text)
    
    conn = sqlite3.connect('tasks.db')
    conn.execute("UPDATE tasks SET task = ?, task_time = ?, notified_10 = 0, notified_5 = 0, notified_0 = 0 WHERE id = ? AND user_id = ?", (new_text, extracted_time, task_id, user_id))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ Vazifa muvaffaqiyatli yangilandi!")
    return ConversationHandler.END

# 5. Bajarildi / O'chirish
async def ask_task_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔢 Bajarib bo'lgan vazifangizning **ID raqamini** yuboring:")
    return WAITING_FOR_DONE_ID

async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in MENU_BUTTONS:
        await update.message.reply_text("⚠️ Amaliyot bekor qilindi.")
        return ConversationHandler.END

    try:
        task_id = int(text)
        user_id = update.effective_user.id
        
        conn = sqlite3.connect('tasks.db')
        conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"🎉 {task_id}-raqamli vazifa bajarildi va ro'yxatdan olib tashlandi!")
    except ValueError:
        await update.message.reply_text("❌ Faqat raqam kiriting.")
    return ConversationHandler.END

# 6. Hammasini tozalash
async def clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('tasks.db')
    conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("🗑 Barcha vazifalaringiz tozalandi.")

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user.username or f"@{user.username.lower()}" != f"@{ADMIN_USERNAME.lower()}":
        return

    conn = sqlite3.connect('tasks.db')
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    pending_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending'").fetchone()[0]
    conn.close()

    admin_kb = [
        ["📢 Xabar tarqatish"],
        ["🔙 Asosiy menyu"]
    ]

    text = (
        f"👑 **Admin Boshqaruv Paneli**\n\n"
        f"👥 Jami foydalanuvchilar: `{total_users}`\n"
        f"📌 Jami vazifalar: `{total_tasks}`\n"
        f"⏳ Bajarilishi kerak bo'lganlar: `{pending_tasks}`"
    )
    await update.message.reply_text(text, reply_markup=ReplyKeyboardMarkup(admin_kb, resize_keyboard=True), parse_mode="Markdown")

# --- XABAR TARQATISH (BROADCAST) ---
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user.username or f"@{user.username.lower()}" != f"@{ADMIN_USERNAME.lower()}":
        return
    await update.message.reply_text("📢 Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yuboring:")
    return WAITING_FOR_BROADCAST

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🔙 Asosiy menyu":
        return await start(update, context)

    conn = sqlite3.connect('tasks.db')
    users = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()

    success = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u[0], text=f"📢 **E'lon:**\n\n{text}", parse_mode="Markdown")
            success += 1
        except Exception:
            pass

    await update.message.reply_text(f"✅ Xabar muvaffaqiyatli **{success}** ta foydalanuvchiga yuborildi!")
    return await admin_panel(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)
    return ConversationHandler.END

# ----------------- MAIN -----------------
def run_bot():
    global bot_app_instance
    TOKEN = "8703509119:AAFV0lziPzWSLeGaQoEE_pN8LlNWolshclI"
    
    app = ApplicationBuilder().token(TOKEN).build()
    bot_app_instance = app

    scheduler = BackgroundScheduler()
    scheduler.add_job(check_reminders, 'interval', minutes=1)
    scheduler.start()

    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Vazifa qo'shish$"), add_task_start)],
        states={
            WAITING_FOR_TASK: [
                MessageHandler(filters.Regex(r"^➕ Vazifa qo'shish$"), add_task_start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_task)
            ]
        },
        fallbacks=[CommandHandler("start", cancel), MessageHandler(filters.Regex(r"^🔙 Asosiy menyu$"), cancel)]
    )

    update_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^✏️ Vazifani yangilash$"), update_task_start)],
        states={
            WAITING_FOR_UPDATE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_task_get_id)],
            WAITING_FOR_UPDATE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_task_save)]
        },
        fallbacks=[CommandHandler("start", cancel)]
    )

    done_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^✅ Bajarildi \(O'chirish\)$"), ask_task_id)],
        states={WAITING_FOR_DONE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_task)]},
        fallbacks=[CommandHandler("start", cancel)]
    )

    broadcast_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^📢 Xabar tarqatish$"), broadcast_start)],
        states={WAITING_FOR_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)]},
        fallbacks=[CommandHandler("start", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(r"^📋 Ro'yxatni ko'rish$"), show_tasks))
    app.add_handler(MessageHandler(filters.Regex(r"^📊 Statistika$"), show_stats))
    app.add_handler(MessageHandler(filters.Regex(r"^🗑 Hammasini tozalash$"), clear_all))
    app.add_handler(MessageHandler(filters.Regex(r"^👑 Admin panel$"), admin_panel))
    app.add_handler(MessageHandler(filters.Regex(r"^🔙 Asosiy menyu$"), start))
    
    app.add_handler(add_conv)
    app.add_handler(update_conv)
    app.add_handler(done_conv)
    app.add_handler(broadcast_conv)

    print("Smart Task Bot to'g'irlandi va ishga tushdi...", flush=True)
    app.run_polling(stop_signals=None)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    run_flask()
