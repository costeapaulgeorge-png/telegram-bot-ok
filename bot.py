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
from openai import (
    APIError, APIConnectionError, AuthenticationError,
    NotFoundError, RateLimitError
)

# ---------------- ENV ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

GROUP_ID  = int(os.getenv("GROUP_ID", "-1002343579283"))
THREAD_ID = int(os.getenv("THREAD_ID", "784"))
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
def init_openai() -> OpenAI:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        return client
    except Exception as e:
        log.exception("OpenAI init failed: %s", e)
        raise

oai = init_openai()

# ---------------- Debug state (runtime owner) ---------------
_runtime_owner_id: Optional[int] = None

def is_owner(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    return user_id == OWNER_USER_ID or user_id == _runtime_owner_id

def mask(s: Optional[str], keep: int = 4) -> str:
    if not s:
        return ""
    return (s[:keep] + "…" + s[-keep:]) if len(s) > keep*2 else "•"*len(s)

# ---------------- Place checks ----------------
def place_check(update: Update) -> Tuple[bool, str]:
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

# --------------- OpenAI helpers ----------------
def explain_openai_error(e: Exception) -> str:
    # Rezumă cauza probabilă pentru user
    if isinstance(e, AuthenticationError):
        return "Auth error (401): cheie invalidă sau proiect greșit."
    if isinstance(e, NotFoundError):
        return "Model error (404): modelul nu există / nu ai acces. Încearcă `gpt-4o-mini`, `gpt-4.1-mini` sau verifică permisiunile."
    if isinstance(e, RateLimitError):
        return "Limită/credit (429): ai depășit cota sau nu ai fonduri."
    if isinstance(e, APIConnectionError):
        return "Conexiune (network/TLS): egress blocat sau DNS/SSL."
    if isinstance(e, APIError):
        return f"API error ({getattr(e, 'status_code', 'n/a')}): serviciul a răspuns cu eroare."
    return f"Eroare: {type(e).__name__}: {e}"

async def call_openai(messages, temperature=0.4) -> str:
    def _call():
        try:
            r = oai.chat.completions.create(
                model="gpt-4o-mini",
                temperature=temperature,
                messages=messages,
            )
            return (r.choices[0].message.content or "").strip()
        except Exception as e:
            raise e

    from concurrent.futures import ThreadPoolExecutor
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(ThreadPoolExecutor(max_workers=4), _call)

# ---------------- Commands ----------------
async def whoami_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    c = update.effective_chat
    m = update.effective_message
    txt = (
        f"*User*\n- id: `{u.id}`\n- username: @{u.username}\n\n"
        f"*Chat*\n- id: `{c.id}`\n- type: `{c.type}`\n"
        f"- message_thread_id: `{getattr(m,'message_thread_id', None)}`\n"
        f"- is_topic_message: `{getattr(m,'is_topic_message', None)}`"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def debug_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _runtime_owner_id
    _runtime_owner_id = update.effective_user.id
    await update.message.reply_text("Debug ON ✅ – ești OWNER pentru sesiunea curentă.")

async def debug_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _runtime_owner_id
    _runtime_owner_id = None
    await update.message.reply_text("Debug OFF ✅")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, reason = place_check(update)
    if not ok:
        await update.message.reply_text(
            "Salut! Scrie-mi în topicul dedicat din grup pentru a funcționa. 🙂\n"
            "Comenzi utile: /whoami, /test_openai, /models, /test_group_post"
        )
        return
    await update.message.reply_text(
        "Salut! Sunt *Asistentul Comunității*.\n"
        "• /ask <întrebare>\n"
        "• /anonask <întrebare> (în privat)\n"
        "• /resources | /privacy | /delete_me\n"
        "• /whoami | /ping | /debug_on | /debug_env | /test_openai | /models | /test_group_post",
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
    if not is_owner(update.effective_user.id):
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

async def models_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Enumeră câteva modele pentru a verifica accesul
    try:
        models = oai.models.list().data  # tipic disponibil în SDK 1.x
        names = [m.id for m in models if "gpt" in m.id][:10]
        if names:
            await update.message.reply_text("Modele disponibile (primele 10):\n- " + "\n- ".join(names))
        else:
            await update.message.reply_text("Nu am primit niciun model. (verifică permisiunile/proiectul)")
    except Exception as e:
        await update.message.reply_text(f"Nu pot lista modelele: {explain_openai_error(e)}")

async def test_openai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Arată detaliat cauza, fără să necesite OWNER
    try:
        ans = await call_openai(
            [{"role": "user", "content": "Spune doar: ok"}],
            temperature=0
        )
        await update.message.reply_text(f"OpenAI OK ✅ Răspuns: `{ans}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.exception("/test_openai failed: %s", e)
        await update.message.reply_text(
            f"OpenAI NU răspunde ❌\n{explain_openai_error(e)}",
            parse_mode=ParseMode.MARKDOWN
        )

async def test_group_post_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text(
            f"Nu pot posta în topicul setat ❌\n{e}"
        )

async def ask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_place(update):
        return await update.message.reply_text("Folosește /ask în topicul dedicat din grup. 🙂")

    q = " ".join(context.args).strip()
    if not q:
        return await update.message.reply_text("Scrie: `/ask întrebarea ta`", parse_mode=ParseMode.MARKDOWN)

    await send_typing(update, context)
    try:
        ans = await call_openai(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": q},
            ],
            temperature=0.4
        )
        if not ans:
            raise RuntimeError("Răspuns gol de la OpenAI.")
        await update.message.reply_text(ans, disable_web_page_preview=True)
    except Exception as e:
        log.exception("OpenAI error on /ask: %s", e)
        await update.message.reply_text(
            f"A apărut o eroare la OpenAI. {explain_openai_error(e)}"
        )

async def anonask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != ChatType.PRIVATE:
        return await update.message.reply_text("Trimite-mi /anonask în privat, te rog. 😊")

    q = " ".join(context.args).strip()
    if not q:
        return await update.message.reply_text("Scrie: `/anonask întrebarea ta`", parse_mode=ParseMode.MARKDOWN)

    try:
        ans = await call_openai(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": q},
            ],
            temperature=0.4
        )
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
        await update.message.reply_text(
            f"Nu am putut posta în grup (după apel OpenAI). {explain_openai_error(e)}"
        )

async def ids_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    info = (
        f"chat.id = {update.effective_chat.id}\n"
        f"message_thread_id = {getattr(update.effective_message, 'message_thread_id', None)}\n"
        f"is_topic_message = {getattr(update.effective_message, 'is_topic_message', None)}\n"
        f"chat.type = {update.effective_chat.type}\n"
        f"date = {datetime.fromtimestamp(update.effective_message.date.timestamp())}"
    )
    await update.message.reply_text(f"```\n{info}\n```", parse_mode=ParseMode.MARKDOWN)

async def ignore_everything(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id if update.effective_user else None
        cid = update.effective_chat.id if update.effective_chat else None
        log.info("Ignored message from user=%s chat=%s type=%s", uid, cid,
                 update.effective_chat.type if update.effective_chat else None)
    except Exception:
        pass
    return

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error: %s (update=%s)", context.error, update)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Eroare internă. Reîncearcă.")
    except Exception:
        pass

# ---------------- Main ----------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler(["start", "help"], start_cmd))
    app.add_handler(CommandHandler("resources", resources_cmd))
    app.add_handler(CommandHandler("privacy", privacy_cmd))
    app.add_handler(CommandHandler("delete_me", delete_cmd))
    app.add_handler(CommandHandler("ask", ask_cmd))
    app.add_handler(CommandHandler("anonask", anonask_cmd))

    # Debug / test
    app.add_handler(CommandHandler("whoami", whoami_cmd))
    app.add_handler(CommandHandler("debug_on", debug_on_cmd))
    app.add_handler(CommandHandler("debug_off", debug_off_cmd))
    app.add_handler(CommandHandler("debug_env", debug_env_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("test_openai", test_openai_cmd))
    app.add_handler(CommandHandler("test_group_post", test_group_post_cmd))
    app.add_handler(CommandHandler("models", models_cmd))
    app.add_handler(CommandHandler("ids", ids_cmd))

    app.add_handler(MessageHandler(filters.ALL, ignore_everything))
    app.add_error_handler(error_handler)

    log.info("Botul pornește cu polling…")
    log.info("Config: GROUP_ID=%s, THREAD_ID=%s, OWNER_USER_ID=%s", GROUP_ID, THREAD_ID, OWNER_USER_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
