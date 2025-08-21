import os
import logging
from datetime import datetime
from typing import Optional, Tuple

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

# opțional, numai pentru /ids și debug (dacă vrei să vezi rapid id-urile)
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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("asistent-comunitate")

# ---------------- OpenAI client ----------------
def _init_openai() -> OpenAI:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        return client
    except Exception as e:
        log.exception("OpenAI init failed: %s", e)
        raise

oai = _init_openai()

# ---------------- Helpers ----------------
def mask(s: Optional[str], keep: int = 4) -> str:
    if not s:
        return ""
    return (s[:keep] + "…" + s[-keep:]) if len(s) > keep*2 else "•"*len(s)

def place_check(update: Update) -> Tuple[bool, str]:
    """
    Verifică dacă mesajul e în grupul + topicul permis.
    Returnează (ok, motiv_dacă_nu).
    """
    if not update.effective_chat or not update.effective_message:
        return False, "no_effective_chat_or_message"

    if update.effective_chat.id != GROUP_ID:
        return False, f"wrong_chat_id: got {update.effective_chat.id}, expected {GROUP_ID}"

    if not getattr(update.effective_message, "is_topic_message", False):
        return False, "not_a_topic_message"

    m_thread = getattr(update.effective_message, "message_thread_id", None)
    if m_thread != THREAD_ID:
        return False, f"wrong_thread_id: got {m_thread}, expected {THREAD_ID}"

    return True, "ok"

def in_allowed_place(update: Update) -> bool:
    ok, reason = place_check(update)
    if not ok:
        log.warning("Blocked message outside allowed place: %s", reason)
    return ok

async def send_typing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.TYPING
        )
    except Exception as e:
        log.debug("send_typing failed: %s", e)

async def reply_owner_only(update: Update, text_public: str, text_owner: str):
    """Dacă expeditorul e OWNER → arată mesajul complet; altfel arată mesaj generic."""
    if OWNER_USER_ID and update.effective_user and update.effective_user.id == OWNER_USER_ID:
        await update.message.reply_text(text_owner, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text_public, parse_mode=ParseMode.MARKDOWN)

# ---------------- OpenAI call ----------------
async def ask_openai(user_msg: str) -> str:
    """
    Apel OpenAI rulat în thread separat ca să nu blocheze event loop-ul PTB.
    """
    def _call():
        try:
            r = oai.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.4,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg.strip()},
                ],
            )
            return (r.choices[0].message.content or "").strip()
        except Exception as e:
            # re-lansăm ca să fie prins mai sus
            raise RuntimeError(f"OpenAI API error: {e}")

    from concurrent.futures import ThreadPoolExecutor
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(ThreadPoolExecutor(max_workers=4), _call)

# ---------------- Commands ----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, reason = place_check(update)
    if not ok:
        # oferă un hint util ca să verifici rapid ID-urile (mai ales când tastezi în alt topic)
        await reply_owner_only(
            update,
            "Salut! Scrie-mi în topicul dedicat din grup pentru a funcționa. 🙂",
            f"Salut! Nu ești în locul permis (`{reason}`).\n"
            f"Tip: folosește /ids în topicul corect ca să verifici ID-urile."
        )
        return

    await update.message.reply_text(
        "Salut! Sunt *Asistentul Comunității*.\n"
        "• /ask <întrebare>\n"
        "• /anonask <întrebare> (în privat)\n"
        "• /resources | /privacy | /delete_me\n"
        "• /ids (debug loc curent) | /ping | /debug_env | /test_openai | /test_group_post",
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

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    when = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    await update.message.reply_text(f"PONG 🏓 {when}")

async def debug_env_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not OWNER_USER_ID or update.effective_user.id != OWNER_USER_ID:
        return
    txt = (
        "*Config curent (mascat)*\n"
        f"- TELEGRAM_TOKEN: `{mask(TELEGRAM_TOKEN)}`\n"
        f"- OPENAI_API_KEY: `{mask(OPENAI_API_KEY)}`\n"
        f"- GROUP_ID: `{GROUP_ID}`\n"
        f"- THREAD_ID: `{THREAD_ID}`\n"
        f"- OWNER_USER_ID: `{OWNER_USER_ID}`\n"
        f"- SYSTEM_PROMPT: {len(SYSTEM_PROMPT)} chars"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def test_openai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test minimal pentru conectivitatea la OpenAI, fără promptul mare."""
    try:
        def _call():
            r = oai.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0,
                messages=[{"role": "user", "content": "Say 'ok'"}],
            )
            return (r.choices[0].message.content or "").strip()

        from concurrent.futures import ThreadPoolExecutor
        import asyncio
        loop = asyncio.get_event_loop()
        ans = await loop.run_in_executor(ThreadPoolExecutor(max_workers=2), _call)

        await update.message.reply_text(f"OpenAI OK ✅ Răspuns: `{ans}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.exception("test_openai failed: %s", e)
        await reply_owner_only(
            update,
            "OpenAI NU răspunde ❌ (vezi logs pe server).",
            f"OpenAI NU răspunde ❌\n```{e}```"
        )

async def test_group_post_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Testează dacă botul reușește să posteze în topicul setat (drepturi, id corect)."""
    try:
        msg = await context.bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=THREAD_ID,
            text=f"Mesaj de test ✅ {datetime.utcnow().isoformat(timespec='seconds')}Z",
            disable_web_page_preview=True
        )
        await update.message.reply_text(f"Postare în topic reușită ✅ (msg_id={msg.message_id})")
    except Exception as e:
        log.exception("test_group_post failed: %s", e)
        await reply_owner_only(
            update,
            "Nu pot posta în topicul setat ❌. Verifică dacă botul e *admin* în grup și că THREAD_ID e corect.",
            f"Nu pot posta în topicul setat ❌\n```{e}```"
        )

async def ask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_place(update):
        ok, reason = place_check(update)
        await reply_owner_only(
            update,
            "Folosește /ask în topicul dedicat din grup. 🙂",
            f"/ask blocat: `{reason}`"
        )
        return

    q = " ".join(context.args).strip()
    if not q:
        return await update.message.reply_text("Scrie: `/ask întrebarea ta`", parse_mode=ParseMode.MARKDOWN)

    await send_typing(update, context)
    try:
        ans = await ask_openai(q)
        if not ans:
            raise RuntimeError("Răspuns gol de la OpenAI.")
        await update.message.reply_text(ans, disable_web_page_preview=True)
    except Exception as e:
        log.exception("OpenAI error on /ask: %s", e)
        await reply_owner_only(
            update,
            "A apărut o eroare. Te rog încearcă din nou.",
            f"A apărut o eroare la OpenAI:\n```{e}```"
        )

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
        await reply_owner_only(
            update,
            "Nu am putut posta în grup (verifică drepturile botului).",
            f"Nu am putut posta în grup:\n```{e}```"
        )

# opțional – numai pentru tine (setează OWNER_USER_ID în env)
async def ids_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not OWNER_USER_ID or update.effective_user.id != OWNER_USER_ID:
        return
    info = (
        f"chat.id = {update.effective_chat.id}\n"
        f"message_thread_id = {getattr(update.effective_message, 'message_thread_id', None)}\n"
        f"is_topic_message = {getattr(update.effective_message, 'is_topic_message', None)}\n"
        f"chat.type = {update.effective_chat.type}\n"
        f"date = {datetime.fromtimestamp(update.effective_message.date.timestamp())}"
    )
    await update.message.reply_text(f"```\n{info}\n```", parse_mode=ParseMode.MARKDOWN)

# Ignoră orice alt mesaj/comandă (dar loghează sumar pentru debug)
async def ignore_everything(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id if update.effective_user else None
        cid = update.effective_chat.id if update.effective_chat else None
        log.info("Ignored message from user=%s chat=%s type=%s", uid, cid,
                 update.effective_chat.type if update.effective_chat else None)
    except Exception:
        pass
    return

# --------------- Global error handler ---------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error: %s (update=%s)", context.error, update)
    try:
        if isinstance(update, Update) and update.effective_message:
            await reply_owner_only(
                update,
                "Eroare internă. Te rog reîncearcă.",
                f"Eroare internă:\n```{context.error}```"
            )
    except Exception:
        pass

# ---------------- Main ----------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Comenzi “normale”
    app.add_handler(CommandHandler(["start", "help"], start_cmd))
    app.add_handler(CommandHandler("resources", resources_cmd))
    app.add_handler(CommandHandler("privacy", privacy_cmd))
    app.add_handler(CommandHandler("delete_me", delete_cmd))
    app.add_handler(CommandHandler("ask", ask_cmd))
    app.add_handler(CommandHandler("anonask", anonask_cmd))

    # Debug / test
    app.add_handler(CommandHandler("ids", ids_cmd))  # doar pt. OWNER_USER_ID
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("debug_env", debug_env_cmd))
    app.add_handler(CommandHandler("test_openai", test_openai_cmd))
    app.add_handler(CommandHandler("test_group_post", test_group_post_cmd))

    # orice altceva ignorăm (dar logăm)
    app.add_handler(MessageHandler(filters.ALL, ignore_everything))

    # Global error handler
    app.add_error_handler(error_handler)

    log.info("Botul pornește cu polling…")
    log.info("Config: GROUP_ID=%s, THREAD_ID=%s, OWNER_USER_ID=%s", GROUP_ID, THREAD_ID, OWNER_USER_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
