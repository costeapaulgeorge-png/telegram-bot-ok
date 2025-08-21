# -*- coding: utf-8 -*-
"""
Asistentul Comunității – Bot Telegram (PTB v20+)

ENV necesar:
  TELEGRAM_TOKEN   = tokenul botului de la BotFather
  OPENAI_API_KEY   = cheia OpenAI
  GROUP_ID         = -100xxxxxxxxxx  (chat id-ul grupului)
  THREAD_ID        = <id-ul topicului din grup> (int)
  OWNER_USER_ID    = <id-ul tău telegram> (opțional, pt /ids)

Comenzi:
  /ping               => răspunde "pong ✅" (test rapid că botul rulează)
  /ids                => afișează chat.id & thread_id (doar OWNER_USER_ID, dacă setat)
  /ask <întrebare>    => răspunde cu OpenAI DOAR în grupul + topicul permis
"""

import os
import logging
from datetime import datetime

from telegram import Update
from telegram.constants import ChatAction, ParseMode, ChatType
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# OpenAI SDK 1.x
from openai import OpenAI

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("asistent-comunitate")

# ---------------- ENV ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# Grupul & topicul unde botul are voie să răspundă
GROUP_ID = int(os.getenv("GROUP_ID", "0"))
THREAD_ID = int(os.getenv("THREAD_ID", "0"))

# opțional – numai pt. comanda /ids
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "0"))

if not TELEGRAM_TOKEN:
    raise RuntimeError("Lipsea TELEGRAM_TOKEN în environment.")
if not OPENAI_API_KEY:
    raise RuntimeError("Lipsea OPENAI_API_KEY în environment.")
if not GROUP_ID or not THREAD_ID:
    log.warning("Atenție: GROUP_ID sau THREAD_ID nu sunt setate. /ask va refuza în afara locului permis.")

# ---------------- OpenAI client ----------------
oai = OpenAI(api_key=OPENAI_API_KEY)

# Prompt de sistem – succint și sigur
SYSTEM_PROMPT = (
    "Ești Asistentul Comunității pentru grupul lui Paul. Răspunzi exclusiv în scop educațional, "
    "explicând legături emoții–corp (5LB/NMG, Recall Healing) în mod empatic și clar, 5–8 rânduri. "
    "NU oferi diagnostic/indicații medicale/medicație/doze/investigații, nu promiți vindecare. "
    "Dacă nu ai destule date, spune asta și propune 3–5 întrebări de jurnal scurte."
)

# ---------------- Helpers ----------------
def in_allowed_place(update: Update) -> bool:
    """
    Întoarce True doar dacă mesajul vine din grupul & topicul permise.
    """
    if not update.effective_chat or not update.effective_message:
        return False

    # trebuie să fim în grupul corect
    if update.effective_chat.id != GROUP_ID:
        return False

    # trebuie să fie topic message & thread id corect
    msg = update.effective_message
    if not getattr(msg, "is_topic_message", False):
        return False

    return getattr(msg, "message_thread_id", None) == THREAD_ID


async def send_typing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.TYPING
        )
    except Exception:
        pass


# ---------------- OpenAI wrapper ----------------
async def ask_openai(user_msg: str) -> str:
    """
    Apel la OpenAI, executat non-blocant.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _call():
        r = oai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.4,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg.strip()},
            ],
        )
        return (r.choices[0].message.content or "").strip()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(ThreadPoolExecutor(max_workers=4), _call)


# ---------------- Commands ----------------
async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Test simplu – nu implică OpenAI. Dacă răspunde, botul rulează ok.
    """
    await update.message.reply_text("pong ✅")


async def ids_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Afișează rapid id-urile utile (doar pt OWNER_USER_ID dacă e setat).
    """
    if OWNER_USER_ID and update.effective_user.id != OWNER_USER_ID:
        return

    msg = update.effective_message
    info = (
        f"chat.id = {update.effective_chat.id}\n"
        f"is_topic_message = {getattr(msg, 'is_topic_message', None)}\n"
        f"message_thread_id = {getattr(msg, 'message_thread_id', None)}\n"
        f"date = {datetime.fromtimestamp(msg.date.timestamp())}"
    )
    await update.message.reply_text(f"```\n{info}\n```", parse_mode=ParseMode.MARKDOWN)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Salut! Sunt *Asistentul Comunității*.\n\n"
        "Comenzi utile:\n"
        "• /ping – test rapid că botul e online\n"
        "• /ids – (admin) afișează chat.id & thread_id\n"
        "• /ask <întrebare> – răspunde *doar* în topicul comunității\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def ask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Întrebări către modelul OpenAI – **doar** în grupul/tema permise.
    """
    # răspunde doar în locul permis
    if not in_allowed_place(update):
        # în privat și în alte locuri – tăcere sau mesaj prietenos
        if update.effective_chat.type == ChatType.PRIVATE:
            await update.message.reply_text(
                "Folosește comanda în topicul comunității. Acest bot răspunde doar acolo. 😊"
            )
        return

    question = " ".join(context.args).strip()
    if not question:
        return await update.message.reply_text(
            "Scrie: `/ask întrebarea ta`", parse_mode=ParseMode.MARKDOWN
        )

    await send_typing(update, context)

    try:
        answer = await ask_openai(question)
        if not answer:
            answer = "Nu am reușit să formulez un răspuns. Te rog încearcă din nou."

        await update.message.reply_text(answer, disable_web_page_preview=True)

    except Exception as e:
        log.exception("Eroare la OpenAI: %s", e)
        await update.message.reply_text("A apărut o eroare. Te rog încearcă din nou.")


# ---------------- Main ----------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Comenzi
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))   # test viață
    app.add_handler(CommandHandler("ids", ids_cmd))     # debug id-uri
    app.add_handler(CommandHandler("ask", ask_cmd))     # întrebări OpenAI

    log.info("Botul pornește cu polling…")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
