import os
import logging
from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.constants import ChatAction, ParseMode, ChatType
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# OpenAI SDK 1.x
from openai import OpenAI

# ---------------- ENV ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Grupul & topicul tău (au și valori default utile)
GROUP_ID  = int(os.getenv("GROUP_ID", "-1002343579283"))
THREAD_ID = int(os.getenv("THREAD_ID", "784"))

# opțional, numai pentru /ids (dacă vrei să vezi rapid id-urile)
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "0"))

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
    raise RuntimeError("Setează TELEGRAM_TOKEN și OPENAI_API_KEY în environment.")

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("asistent-comunitate")

# ---------------- OpenAI client ----------------
oai = OpenAI(api_key=OPENAI_API_KEY)

async def ask_openai(user_msg: str) -> str:
    """
    Apel sincronic la OpenAI rulat în thread separat ca să nu blocheze event loop-ul PTB.
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

    # rulează în executor (thread pool)
    from concurrent.futures import ThreadPoolExecutor
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(ThreadPoolExecutor(max_workers=4), _call)

# ---------------- Helpers ----------------
def in_allowed_place(update: Update) -> bool:
    """
    True doar dacă mesajul e în grupul tău + în topicul permis.
    """
    if not update.effective_chat or not update.effective_message:
        return False
    if update.effective_chat.id != GROUP_ID:
        return False
    # trebuie să fie mesaj de topic și thread-id să fie cel dorit
    if not getattr(update.effective_message, "is_topic_message", False):
        return False
    return update.effective_message.message_thread_id == THREAD_ID

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
    if not in_allowed_place(update): 
        return
    await update.message.reply_text(
        "Salut! Sunt *Asistentul Comunității*.\n"
        "• /ask <întrebare>\n"
        "• /anonask <întrebare> (în privat)\n"
        "• /resources\n• /privacy\n• /delete_me",
        parse_mode=ParseMode.MARKDOWN
    )

async def resources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_place(update): 
        return
    await update.message.reply_text(RESOURCES_TEXT, disable_web_page_preview=True)

async def privacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_place(update): 
        return
    await update.message.reply_text(PRIVACY_TEXT)

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_place(update): 
        return
    await update.message.reply_text("Nu stocăm istoricul conversațiilor în acest MVP. ✅")

async def ask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_place(update):
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
        await update.message.reply_text("A apărut o eroare. Te rog încearcă din nou.")

# /anonask se trimite în PRIVAT → botul postează răspunsul anonim în topicul comunității
async def anonask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text("Nu am putut posta în grup. Verifică dacă botul este admin în grup.")

# opțional – numai pentru tine (setează OWNER_USER_ID în env)
async def ids_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not OWNER_USER_ID or update.effective_user.id != OWNER_USER_ID:
        return
    info = (
        f"chat.id = {update.effective_chat.id}\n"
        f"message_thread_id = {getattr(update.effective_message, 'message_thread_id', None)}\n"
        f"is_topic_message = {getattr(update.effective_message, 'is_topic_message', None)}\n"
        f"date = {datetime.fromtimestamp(update.effective_message.date.timestamp())}"
    )
    await update.message.reply_text(f"```\n{info}\n```", parse_mode=ParseMode.MARKDOWN)

# Ignoră orice alt mesaj/comandă
async def ignore_everything(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return

# ---------------- Main ----------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler(["start", "help"], start_cmd))
    app.add_handler(CommandHandler("resources", resources_cmd))
    app.add_handler(CommandHandler("privacy", privacy_cmd))
    app.add_handler(CommandHandler("delete_me", delete_cmd))
    app.add_handler(CommandHandler("ask", ask_cmd))
    app.add_handler(CommandHandler("anonask", anonask_cmd))
    app.add_handler(CommandHandler("ids", ids_cmd))  # doar pt. OWNER_USER_ID

    # orice altceva ignorăm (asigură „tăcerea” în afara topicului)
    app.add_handler(MessageHandler(filters.ALL, ignore_everything))

    log.info("Botul pornește cu polling…")
    app.run_polling()

if __name__ == "__main__":
    main()
