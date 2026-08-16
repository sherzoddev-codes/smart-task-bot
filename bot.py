import os
import sqlite3
import threading
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)

# --- FLASK (Render uchun veb-server) ---
web_app = Flask(__name__)
@web_app.route('/')
def index():
    return "Smart Task Ultimate Bot is running!", 200

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
            category TEXT,
            task TEXT, 
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Holatlar (States)
WAITING_FOR_TASK = 1
WAITING_FOR_DONE_ID = 2
WAITING_FOR_UPDATE_ID = 3
WAITING_FOR_UPDATE_TEXT = 4

# --- BOT MENYUSI ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        ["➕ Vazifa qo'shish", "📋 Ro'yxatni ko'rish"], 
        ["✏️ Vazifani yangilash", "✅ Bajarildi (O'chirish)"],
        ["📊 Statistika", "🗑 Hammasini tozalash"]
    ]
    await update.message.reply_text(
        "👋 **Smart Task Ultimate Bot**ga xush kelibsiz!\n"
        "Kategoriyalar va vazifalaringizni qulay boshqaring.", 
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# --- 1. KATEGORIYA TANLAB VAZIFA QO'SHISH ---
async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📚 O'qish", callback_data="cat_O'qish")],
        [InlineKeyboardButton("💻 Dasturlash", callback_data="cat_Dasturlash")],
        [InlineKeyboardButton("🛡 Kiberxavfsizlik", callback_data="cat_Kiberxavfsizlik")],
        [InlineKeyboardButton("👤 Shaxsiy", callback_data="cat_Shaxsiy")]
    ]
    await update.message.reply_text("📁 Vazifa uchun **kategoriyani** tanlang:", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.replace("cat_", "")
    context.user_data['selected_category'] = category
    
    await query.message.reply_text(f"📝 Tanlangan kategoriya: **{category}**\n\nEndi vazifa matnini (vaqti bilan birga) yuboring:")
    return WAITING_FOR_TASK

async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_text = update.message.text
    category = context.user_data.get('selected_category', 'Umumiy')
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('tasks.db')
    conn.execute("INSERT INTO tasks (user_id, category, task, status) VALUES (?, ?, ?, ?)", (user_id, category, task_text, "pending"))
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"✅ Vazifa muvaffaqiyatli qo'shildi!\n📁 Kategoriya: *{category}*", parse_mode="Markdown")
    return ConversationHandler.END

# --- 2. RO'YXATNI KO'RISH (KATEGORIYALAR BILAN) ---
async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('tasks.db')
    tasks = conn.execute("SELECT id, category, task FROM tasks WHERE user_id = ? AND status = 'pending'", (user_id,)).fetchall()
    conn.close()
    
    if not tasks:
        await update.message.reply_text("📭 Hozircha bajarilmagan vazifalaringiz yo'q.")
    else:
        text = "📋 **Sizning vazifalaringiz:**\n\n"
        for t in tasks:
            text += f"🔹 **ID: {t[0]}** | [{t[1]}] — {t[2]}\n"
        text += "\n*(Vazifani bajarish uchun ID raqamini kiriting)*"
        await update.message.reply_text(text, parse_mode="Markdown")

# --- 3. STATISTIKA ---
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('tasks.db')
    total = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'pending'", (user_id,)).fetchone()[0]
    conn.close()
    
    completed = total - pending
    text = (
        f"📊 **Sizning statistikalaringiz:**\n\n"
        f"📌 Jami vazifalar: `{total}`\n"
        f"⏳ Bajarilishi kerak: `{pending}`\n"
        f"🎉 Bajarilganlar: `{completed}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# --- 4. VAZIFANI YANGILASH ---
async def update_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✏️ Yangilamoqchi bo'lgan vazifangizning **ID raqamini** yuboring:")
    return WAITING_FOR_UPDATE_ID

async def update_task_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        task_id = int(update.message.text)
        user_id = update.effective_user.id
        
        conn = sqlite3.connect('tasks.db')
        task = conn.execute("SELECT task FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).fetchone()
        conn.close()
        
        if not task:
            await update.message.reply_text("❌ Bunday ID raqamli vazifa topilmadi.")
            return ConversationHandler.END
        
        context.user_data['update_id'] = task_id
        await update.message.reply_text(f"📝 Eski vazifa: *{task[0]}*\n\nEndi yangi matnni yuboring:")
        return WAITING_FOR_UPDATE_TEXT
    except ValueError:
        await update.message.reply_text("❌ Faqat raqam kiriting.")
        return WAITING_FOR_UPDATE_ID

async def update_task_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_text = update.message.text
    task_id = context.user_data.get('update_id')
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('tasks.db')
    conn.execute("UPDATE tasks SET task = ? WHERE id = ? AND user_id = ?", (new_text, task_id, user_id))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ Vazifa muvaffaqiyatli yangilandi!")
    return ConversationHandler.END

# --- 5. BAJARILDI / O'CHIRISH ---
async def ask_task_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔢 Bajarib bo'lgan vazifangizning **ID raqamini** yuboring:")
    return WAITING_FOR_DONE_ID

async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        task_id = int(update.message.text)
        user_id = update.effective_user.id
        
        conn = sqlite3.connect('tasks.db')
        conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"🎉 {task_id}-raqamli vazifa bajarildi va ro'yxatdan olib tashlandi!")
    except ValueError:
        await update.message.reply_text("❌ Faqat raqam kiriting.")
    return ConversationHandler.END

# --- 6. HAMMASINI TOZALASH ---
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
    TOKEN = "8703509119:AAFV0lziPzWSLeGaQoEE_pN8LlNWolshclI"
    
    app = ApplicationBuilder().token(TOKEN).build()

    # Vazifa qo'shish (Kategoriya bilan)
    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Vazifa qo'shish$"), add_task_start)],
        states={WAITING_FOR_TASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_task)]},
        fallbacks=[CommandHandler("start", cancel)]
    )

    # Vazifani yangilash zanjiri
    update_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^✏️ Vazifani yangilash$"), update_task_start)],
        states={
            WAITING_FOR_UPDATE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_task_get_id)],
            WAITING_FOR_UPDATE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_task_save)]
        },
        fallbacks=[CommandHandler("start", cancel)]
    )

    # Bajarildi qilish zanjiri
    done_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^✅ Bajarildi \(O'chirish\)$"), ask_task_id)],
        states={WAITING_FOR_DONE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_task)]},
        fallbacks=[CommandHandler("start", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(r"^📋 Ro'yxatni ko'rish$"), show_tasks))
    app.add_handler(MessageHandler(filters.Regex(r"^📊 Statistika$"), show_stats))
    app.add_handler(MessageHandler(filters.Regex(r"^🗑 Hammasini tozalash$"), clear_all))
    
    app.add_handler(add_conv)
    app.add_handler(update_conv)
    app.add_handler(done_conv)
    
    app.add_handler(CallbackQueryHandler(category_callback, pattern=r"^cat_"))

    print("Smart Task Ultimate Bot ishga tushdi...", flush=True)
    app.run_polling(stop_signals=None)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    run_flask()
