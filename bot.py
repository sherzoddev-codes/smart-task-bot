import os
import re
import sqlite3
import threading
from datetime import datetime
import pytz
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
    # Adminga xabar yuborish limiti uchun yangi jadval
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages_to_admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message_text TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- O'ZINGIZNING TELEGRAM ID RAQAMINGIZNI SHU YERGA YOZING ---
ADMIN_ID = 123456789  # <--- O'zingizning haqiqiy ID raqamingizni yozing!
TASHKENT_TZ = pytz.timezone('Asia/Tashkent')

# Holatlar
WAITING_FOR_TASK_TEXT = 1
WAITING_FOR_TASK_TIME = 2
WAITING_FOR_UPDATE_ID = 3
WAITING_FOR_UPDATE_TEXT = 4
WAITING_FOR_DONE_ID = 5
WAITING_FOR_BROADCAST = 6
WAITING_FOR_ADMIN_MESSAGE = 7

MENU_BUTTONS = [
    "➕ Vazifa qo'shish", 
    "📋 Ro'yxatni ko'rish", 
    "✏️ Vazifani yangilash", 
    "✅ Bajarildi (O'chirish)",
    "📊 Statistika", 
    "🗑 Hammasini tozalash",
    "👨‍💻 Adminga xabar yozish",
    "👑 Admin panel",
    "📢 Xabar tarqatish",
    "🔙 Asosiy menyu"
]

bot_app_instance = None

def get_main_keyboard(user_id):
    kb = [
        ["➕ Vazifa qo'shish", "📋 Ro'yxatni ko'rish"], 
        ["✏️ Vazifani yangilash", "✅ Bajarildi (O'chirish)"],
        ["📊 Statistika", "🗑 Hammasini tozalash"],
        ["👨‍💻 Adminga xabar yozish"]
    ]
    if user_id == ADMIN_ID:
        kb.append(["👑 Admin panel"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def check_reminders():
    global bot_app_instance
    if not bot_app_instance:
        return

    # O'zbekiston vaqti bo'yicha hisoblash
    now = datetime.now(TASHKENT_TZ)
    current_total_minutes = now.hour * 60 + now.minute

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
            
            # Vaqt farqini hisoblash (yarim tundan o'tishni inobatga olgan holda)
            diff = (task_total_minutes - current_total_minutes) % 1440

            if diff == 10 and not n10:
                bot_app_instance.bot.send_message(chat_id=user_id, text=f"⏰ **Eslatma!**\n\n\"{task_text}\" vazifasiga **10 daqiqa** qoldi!")
                cursor.execute("UPDATE tasks SET notified_10 = 1 WHERE id = ?", (task_id,))
                conn.commit()
            elif diff == 5 and not n5:
                bot_app_instance.bot.send_message(chat_id=user_id, text=f"⚠️ **Tezroq bo'ling!**\n\n\"{task_text}\" vazifasiga **5 daqiqa** qoldi!")
                cursor.execute("UPDATE tasks SET notified_5 = 1 WHERE id = ?", (task_id,))
                conn.commit()
            elif diff == 0 and not n0:
                bot_app_instance.bot.send_message(chat_id=user_id, text=f"🚨 **Vaqt bo'ldi!**\n\n\"{task_text}\" vaqti keldi, bajarishni boshlang!")
                cursor.execute("UPDATE tasks SET notified_0 = 1 WHERE id = ?", (task_id,))
                conn.commit()
        except Exception as e:
            print(f"Xatolik: {e}")
    conn.close()

def register_user(user_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id)

    welcome_text = (
        "👋 **Smart Task Botga xush kelibsiz!**\n\n"
        "🤖 **Bot haqida:**\n"
        "Bu bot sizning shaxsiy yordamchingiz! Siz o'z kunlik vazifalaringizni (masalan: *Koreys tili darsi, kitob o'qish, mashg'ulot*) aniq vaqti bilan kiritib qo'yishingiz mumkin. "
        "Bot vazifa vaqti kelishidan 10 va 5 daqiqa oldin, hamda ayni vaqti kelganda sizga eslatma yuboradi.\n\n"
        "Quyidagi menyu orqali botdan foydalanishni boshlang!"
    )

    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(user.id), parse_mode="Markdown")
    return ConversationHandler.END

# 1. Vazifa qo'shish (Yangi usul - Qadam-baqadam)
async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 **1-qadam:** Vazifangiz nomini yozib yuboring.\n*(Masalan: Koreys tili 듣기)*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["🔙 Asosiy menyu"]], resize_keyboard=True)
    )
    return WAITING_FOR_TASK_TEXT

async def add_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in MENU_BUTTONS:
        await start(update, context)
        return ConversationHandler.END

    context.user_data['new_task'] = text
    await update.message.reply_text(
        "⏰ **2-qadam:** Vazifa vaqtini yuboring.\n*(Masalan: 13:00, 09:30)*",
        parse_mode="Markdown"
    )
    return WAITING_FOR_TASK_TIME

async def add_task_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in MENU_BUTTONS:
        await start(update, context)
        return ConversationHandler.END

    match = re.search(r'^([01]?\d|2[0-3]):([0-5]\d)$', text)
    if not match:
        await update.message.reply_text("❌ Noto'g'ri vaqt formati! Iltimos, **Soat:Daqiqa** (Masalan: 13:00) ko'rinishida kiriting:")
        return WAITING_FOR_TASK_TIME

    time_str = f"{match.group(1).zfill(2)}:{match.group(2)}"
    task_text = context.user_data['new_task']
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('tasks.db')
    conn.execute("INSERT INTO tasks (user_id, task, task_time, status) VALUES (?, ?, ?, ?)", (user_id, task_text, time_str, "pending"))
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"✅ **Vazifa qo'shildi!**\n\n📌 Vazifa: {task_text}\n⏰ Vaqti: {time_str}", 
        parse_mode="Markdown", 
        reply_markup=get_main_keyboard(user_id)
    )
    return ConversationHandler.END

# 2. Ro'yxatni ko'rish
async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('tasks.db')
    tasks = conn.execute("SELECT id, task, task_time FROM tasks WHERE user_id = ? AND status = 'pending'", (user_id,)).fetchall()
    conn.close()
    
    if not tasks:
        await update.message.reply_text("📭 Hozircha bajarilmagan vazifalaringiz yo'q.")
    else:
        text = "📋 **Sizning vazifalaringiz:**\n\n"
        for t in tasks:
            text += f"🔹 **ID: {t[0]}** — {t[1]} (⏰ {t[2]})\n"
        text += "\n*(Vazifani bajarib bo'lgach, menyudan 'Bajarildi' ni bosib o'chirishingiz mumkin)*"
        await update.message.reply_text(text, parse_mode="Markdown")

# 3. Statistika (To'g'rilangan)
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('tasks.db')
    total = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'pending'", (user_id,)).fetchone()[0]
    completed = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'completed'", (user_id,)).fetchone()[0]
    conn.close()
    
    text = (
        f"📊 **Sizning statistikangiz:**\n\n"
        f"📌 Jami qo'shilgan vazifalar: `{total}`\n"
        f"⏳ Hali bajarilmagan: `{pending}`\n"
        f"🎉 Bajarilgan (Tamomlangan): `{completed}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# 4. Bajarildi / O'chirish (To'g'rilangan - endi bazadan o'chmaydi, completed bo'ladi)
async def ask_task_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔢 Bajarib bo'lgan vazifangizning **ID raqamini** yuboring:",
        reply_markup=ReplyKeyboardMarkup([["🔙 Asosiy menyu"]], resize_keyboard=True)
    )
    return WAITING_FOR_DONE_ID

async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in MENU_BUTTONS:
        await start(update, context)
        return ConversationHandler.END

    try:
        task_id = int(text)
        user_id = update.effective_user.id
        
        conn = sqlite3.connect('tasks.db')
        # DELETE o'rniga UPDATE qilamiz, shunda statistika to'g'ri ishlaydi
        cursor = conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ? AND user_id = ? AND status = 'pending'", (task_id, user_id))
        
        if cursor.rowcount == 0:
            await update.message.reply_text("❌ Bunday ID raqamli faol vazifa topilmadi.", reply_markup=get_main_keyboard(user_id))
        else:
            await update.message.reply_text(f"🎉 Barakalla! {task_id}-raqamli vazifa bajarildi deb belgilandi!", reply_markup=get_main_keyboard(user_id))
            
        conn.commit()
        conn.close()
    except ValueError:
        await update.message.reply_text("❌ Faqat raqam kiriting.")
        return WAITING_FOR_DONE_ID
        
    return ConversationHandler.END

# 5. Adminga xabar yuborish (Yangi)
async def contact_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    # So'nggi 24 soat ichida nechta xabar yuborganini tekshiramiz
    cursor.execute("SELECT COUNT(*) FROM messages_to_admin WHERE user_id = ? AND sent_at >= datetime('now', '-1 day')", (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    
    if count >= 2:
        await update.message.reply_text("⚠️ Kechirasiz, sizda adminga xabar yozish bo'yicha kunlik limit tugagan (Maksimum: 2 ta). Iltimos, 24 soatdan so'ng qayta urinib ko'ring.")
        return ConversationHandler.END
        
    await update.message.reply_text(
        "✍️ **Adminga xabar yuborish:**\n\nSavolingiz, taklifingiz yoki muammoni yozib yuboring (Iltimos, barchasini bitta xabarga sig'diring):",
        reply_markup=ReplyKeyboardMarkup([["🔙 Asosiy menyu"]], resize_keyboard=True)
    )
    return WAITING_FOR_ADMIN_MESSAGE

async def contact_admin_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = update.effective_user
    
    if text in MENU_BUTTONS:
        await start(update, context)
        return ConversationHandler.END

    conn = sqlite3.connect('tasks.db')
    conn.execute("INSERT INTO messages_to_admin (user_id, message_text) VALUES (?, ?)", (user.id, text))
    conn.commit()
    conn.close()
    
    admin_msg = f"📩 **Foydalanuvchidan yangi xabar!**\n\n👤 Ism: {user.first_name}\n🆔 ID: `{user.id}`\n\n💬 Xabar: {text}"
    
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, parse_mode="Markdown")
        await update.message.reply_text("✅ Xabaringiz adminga muvaffaqiyatli yuborildi!", reply_markup=get_main_keyboard(user.id))
    except Exception:
        await update.message.reply_text("❌ Xato yuz berdi. Admin botni bloklagan bo'lishi mumkin.", reply_markup=get_main_keyboard(user.id))
        
    return ConversationHandler.END

# 6. Admin Panel & Broadcast
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
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
        f"👥 Jami bot a'zolari: `{total_users}`\n"
        f"📌 Jami yaratilgan vazifalar: `{total_tasks}`\n"
        f"⏳ Bajarilishi kutilayotganlar: `{pending_tasks}`"
    )
    await update.message.reply_text(text, reply_markup=ReplyKeyboardMarkup(admin_kb, resize_keyboard=True), parse_mode="Markdown")

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
    await update.message.reply_text("📢 Barcha foydalanuvchilarga yubormoqchi bo'lgan matnli xabarni yozing:", reply_markup=ReplyKeyboardMarkup([["🔙 Asosiy menyu"]], resize_keyboard=True))
    return WAITING_FOR_BROADCAST

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # MUAMMO YECHIMI: Agar adashib tugma bosilsa, uni xabar sifatida tarqatib yubormaydi
    if text in MENU_BUTTONS:
        await start(update, context)
        return ConversationHandler.END

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

    await update.message.reply_text(f"✅ Xabar muvaffaqiyatli **{success}** ta foydalanuvchiga yuborildi!", reply_markup=get_main_keyboard(ADMIN_ID))
    return ConversationHandler.END

# Qolgan funksiyalar (Vazifani yangilash va Tozalash) - oldingidek qoladi
async def update_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✏️ Yangilamoqchi bo'lgan vazifangizning **ID raqamini** yuboring:", reply_markup=ReplyKeyboardMarkup([["🔙 Asosiy menyu"]], resize_keyboard=True))
    return WAITING_FOR_UPDATE_ID

async def update_task_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in MENU_BUTTONS:
        return await start(update, context)

    try:
        task_id = int(text)
        user_id = update.effective_user.id
        conn = sqlite3.connect('tasks.db')
        task = conn.execute("SELECT task FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).fetchone()
        conn.close()
        
        if not task:
            await update.message.reply_text("❌ Bunday ID topilmadi.")
            return WAITING_FOR_UPDATE_ID
        
        context.user_data['update_id'] = task_id
        await update.message.reply_text("📝 Yangi matnni va yangi vaqtni bitta xabarda yuboring (Masalan: Kitob 15:00):")
        return WAITING_FOR_UPDATE_TEXT
    except ValueError:
        await update.message.reply_text("❌ Raqam kiriting.")
        return WAITING_FOR_UPDATE_ID

async def update_task_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_text = update.message.text
    if new_text in MENU_BUTTONS:
        return await start(update, context)

    task_id = context.user_data.get('update_id')
    user_id = update.effective_user.id
    
    match = re.search(r'(\d{1,2}):(\d{2})', new_text)
    extracted_time = None
    if match:
        extracted_time = f"{match.group(1).zfill(2)}:{match.group(2)}"
    
    conn = sqlite3.connect('tasks.db')
    conn.execute("UPDATE tasks SET task = ?, task_time = ?, notified_10 = 0, notified_5 = 0, notified_0 = 0 WHERE id = ? AND user_id = ?", (new_text, extracted_time, task_id, user_id))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ Vazifa yangilandi!", reply_markup=get_main_keyboard(user_id))
    return ConversationHandler.END

async def clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('tasks.db')
    conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("🗑 Barcha vazifalaringiz tozalandi.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)
    return ConversationHandler.END

# ----------------- MAIN -----------------
def run_bot():
    global bot_app_instance
    TOKEN = "8703509119:AAFV0lziPzWSLeGaQoEE_pN8LlNWolshclI" # <--- TOKENNI O'ZGARTIRISHNI UNUTMANG!
    
    app = ApplicationBuilder().token(TOKEN).build()
    bot_app_instance = app

    scheduler = BackgroundScheduler(timezone=TASHKENT_TZ)
    scheduler.add_job(check_reminders, 'interval', minutes=1)
    scheduler.start()

    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Vazifa qo'shish$"), add_task_start)],
        states={
            WAITING_FOR_TASK_TEXT: [MessageHandler(filters.TEXT, add_task_text)],
            WAITING_FOR_TASK_TIME: [MessageHandler(filters.TEXT, add_task_time)]
        },
        fallbacks=[CommandHandler("start", cancel)]
    )

    update_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^✏️ Vazifani yangilash$"), update_task_start)],
        states={
            WAITING_FOR_UPDATE_ID: [MessageHandler(filters.TEXT, update_task_get_id)],
            WAITING_FOR_UPDATE_TEXT: [MessageHandler(filters.TEXT, update_task_save)]
        },
        fallbacks=[CommandHandler("start", cancel)]
    )

    done_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^✅ Bajarildi \(O'chirish\)$"), ask_task_id)],
        states={WAITING_FOR_DONE_ID: [MessageHandler(filters.TEXT, remove_task)]},
        fallbacks=[CommandHandler("start", cancel)]
    )

    broadcast_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^📢 Xabar tarqatish$"), broadcast_start)],
        states={WAITING_FOR_BROADCAST: [MessageHandler(filters.TEXT, broadcast_send)]},
        fallbacks=[CommandHandler("start", cancel)]
    )

    admin_msg_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^👨‍💻 Adminga xabar yozish$"), contact_admin_start)],
        states={WAITING_FOR_ADMIN_MESSAGE: [MessageHandler(filters.TEXT, contact_admin_send)]},
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
    app.add_handler(admin_msg_conv)

    print("Bot muvaffaqiyatli ishga tushdi...", flush=True)
    app.run_polling(stop_signals=None)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    run_flask()
