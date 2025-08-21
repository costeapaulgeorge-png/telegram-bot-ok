import os
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from telegram import Update
from telegram.constants import ChatAction, ParseMode, ChatType
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# OpenAI SDK 1.x
from openai import OpenAI

# ---------------- ENV ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# Grupul & topicul tău (au și valori default utile pt. test)
GROUP_ID  = int(os.getenv("GROUP_ID", "-1002343579283"))
THREAD_ID = int(os.getenv("THREAD_ID", "784"))

# /ids este permis DOAR acestui user dacă e setat. Dacă 0/nesetat -> blocăm /ids (sau doar în DEBUG răspundem)
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "0"))

# Mod debug (mesaje extinse de diagnostic în chat)
DEBUG = os.getenv("DEBUG", "0") == "1"

SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", (
    "Ești Asistentul Comunității pentru grupul lui Paul. Rol 100% educațional și de ghidaj.\n"
    "Ce faci: explici relația emoții–corp în cadrul (5LB/NMG, Recall Healing, spiritual), "
    "oferi pași de reflecție, întrebări de jurnal, exerciții simple. Ton empatic, clar, concis (5–8 rânduri), în pași/bullet-uri.\n"
    "Ce NU faci: nu pui diagnostic, nu recomanzi tratamente/medicamente/doze/investigații, nu promiți vindecare, "
    "nu inventa titluri de meditații; folosește doar ce există în Knowledge. Dacă nu ai destule date, spune asta și propune 3–5 întrebări de jurnal.\n"
    "Dacă utilizatorul cere diagnostic/tratament sau apar semne de urgență: "
    "«Nu pot oferi diagnostic sau indicații medicale. Pentru probleme medicale, adresează-te unui specialist sau 112.»\n"
    "Dacă folosești web, ai voie DOAR pe site-urile aprobate NMG: learninggnm.com, leyesbiologicas.com, "
    "germanische-heilkunde.at, amici-di-dirk.com, ghk-academy.info, newmedicine.ca"
))

RESOURCES_TEXT = os.getenv("RESOURCES_TEXT",
    "📚 **Resursele comunității**\n"
    "• Meditații: (adaugi linkurile tale)\n"
    "• Ghid întrebări de jurnal: (link)\n"
    "• Glosar: (link)\n"
)

PRIVACY_TEXT = os.getenv("PRIVACY_TEXT",
    "🔒 **Confidențialitate**\n"
    "Botul este strict educațional; nu oferă diagnostic sau tratament medical. "
    "Nu stocăm istoricul conversațiilor în acest MVP. Poți cere ștergerea cu /delete_me."
)

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Setează TELEGRAM_TOKEN și OPENAI_API_KEY în environment (Railway → Variables).")

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("asistent-comunitate")

# În DEBUG afișăm contexte utile (fără secrete)
if DEBUG:
    log.info("DEBUG ON | GROUP_ID=%s THREAD_ID=%s OWNER_USER_ID=%s", GROUP_ID, THREAD_ID, OWNER_USER_ID)

# ---------------- OpenAI client ----------------
oai = OpenAI(api_key=OPENAI_API_KEY)
_executor = ThreadPoolExecutor(max_workers=4)


async def ask_openai(user_msg: str) -> str:
    """
    Apel OpenAI în thread separat ca să nu blocăm event-loop-ul PTB.
    """
    def _call():
        r = oai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.4,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg.strip()},
            ],
        )
        return r.choices[0].message.content.strip()

    from asyncio import get_event_loop
    loop = get_event_loop()
    return await loop.run_in_executor(_executor, _call)

# ---------------- Helpers ----------------
def in_allowed_place(update: Update) -> bool:
    """
    True doar dacă mesajul e în grupul tău + în topicul permis.
    """
    if not update.effective_chat or not update.effective_message:
        return False

    # Doar în grupul specificat
    if update.effective_chat.id != GROUP_ID:
        if DEBUG:
            log.info("Mesaj refuzat: chat.id=%s != GROUP_ID=%s", update.effective_chat.id, GROUP_ID)
        return False

    # Trebuie să fie topic & thread specific
    msg = update.effective_message
    is_topic = getattr(msg, "is_topic_message", False)
    thread_id = getattr(msg, "message_thread_id", None)

    if not is_topic:
        if DEBUG:
            log.info("Mesaj refuzat: nu e topic_message")
        return False

    if thread_id != THREAD_ID:
        if DEBUG:
            log.info("Mesaj refuzat: thread_id=%s != THREAD_ID=%s", thread_id, THREAD_ID)
        return False

    return True


async def send_typing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.TYPING
        )
    except Exception:
        pass

# ---------------- Commands ----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /start nu e restricționat; răspundem oricui, dar scurt
    await update.message.reply_text(
        "Salut! Sunt *Asistentul Comunității*.\n"
        "• /ping\n"
        "• /whoami (debug)\n"
        "• /ask <întrebare> (în topicul comunității)\n"
        "• /resources\n• /privacy\n• /delete_me",
        parse_mode=ParseMode.MARKDOWN
    )

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong ✅")

async def whoami_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Mică comandă de diagnostic – utile ID-urile
    msg = update.effective_message
    info = (
        f"user_id = {update.effective_user.id}\n"
        f"chat_id = {update.effective_chat.id}\n"
        f"thread_id = {getattr(msg, 'message_thread_id', None)}\n"
        f"is_topic_message = {getattr(msg, 'is_topic_message', None)}"
    )
    await update.message.reply_text(f"```\n{info}\n```", parse_mode=ParseMode.MARKDOWN)

async def resources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_place(update):
        if DEBUG:
            await update.message.reply_text("Comanda /resources este permisă doar în topicul comunității.")
        return
    await update.message.reply_text(RESOURCES_TEXT, disable_web_page_preview=True)

async def privacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_place(update):
        if DEBUG:
            await update.message.reply_text("Comanda /privacy este permisă doar în topicul comunității.")
        return
    await update.message.reply_text(PRIVACY_TEXT)

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_place(update):
        if DEBUG:
            await update.message.reply_text("Comanda /delete_me este permisă doar în topicul comunității.")
        return
    await update.message.reply_text("Nu stocăm istoricul conversațiilor în acest MVP. ✅")

async def ask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_place(update):
        if DEBUG:
            await update.message.reply_text("Comanda /ask este permisă doar în topicul comunității.")
        return

    q = " ".join(context.args).strip()
    if not q:
        return await update.message.reply_text("Scrie: `/ask întrebarea ta`", parse_mode=ParseMode.MARKDOWN)

    await send_typing(update, context)
    try:
        ans = await ask_openai(q)
        await update.message.reply_text(ans, disable_web_page_preview=True)
    except Exception as e:
        log.exception("OpenAI error: %s", e)
        if DEBUG:
            await update.message.reply_text(f"Eroare OpenAI: {e}")
        else:
            await update.message.reply_text("A apărut o eroare. Te rog încearcă din nou.")

async def anonask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Folosit în PRIVATE – botul postează în topicul comunității
    if update.effective_chat.type != ChatType.PRIVATE:
        return await update.message.reply_text("Trimite-mi /anonask în privat, te rog. 😊")

    q = " ".join(context.args).strip()
    if not q:
        return await update.message.reply_text("Scrie: `/anonask întrebarea ta`", parse_mode=ParseMode.MARKDOWN)

    try:
        ans = await ask_openai(q)
        await context.bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=THREAD_ID,
            text=f"*(Întrebare anonimă)*\n\n{ans}",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        await update.message.reply_text("Am postat răspunsul anonim în topicul comunității. ✅")
    except Exception as e:
        log.exception("Post to group error: %s", e)
        if DEBUG:
            await update.message.reply_text(f"Nu am putut posta în grup: {e}")
        else:
            await update.message.reply_text("Nu am putut posta în grup. Verifică dacă botul este admin în grup.")

async def ids_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Doar pentru OWNER_USER_ID (dacă e setat)
    if not OWNER_USER_ID or update.effective_user.id != OWNER_USER_ID:
        if DEBUG:
            await update.message.reply_text("Comanda /ids este permisă doar OWNER_USER_ID-ului configurat.")
        return
    msg = update.effective_message
    info = (
        f"chat.id = {update.effective_chat.id}\n"
        f"message_thread_id = {getattr(msg, 'message_thread_id', None)}\n"
        f"is_topic_message = {getattr(msg, 'is_topic_message', None)}\n"
        f"date = {datetime.fromtimestamp(msg.date.timestamp())}"
    )
    await update.message.reply_text(f"```\n{info}\n```", parse_mode=ParseMode.MARKDOWN)

# Ignoră orice alt mesaj/comandă
async def ignore_everything(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return

# ---------------- Main ----------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Comenzi generale
    app.add_handler(CommandHandler(["start", "help"], start_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("whoami", whoami_cmd))

    # Comenzi funcționale (legate de topic)
    app.add_handler(CommandHandler("resources", resources_cmd))
    app.add_handler(CommandHandler("privacy", privacy_cmd))
    app.add_handler(CommandHandler("delete_me", delete_cmd))
    app.add_handler(CommandHandler("ask", ask_cmd))
    app.add_handler(CommandHandler("anonask", anonask_cmd))
    app.add_handler(CommandHandler("ids
