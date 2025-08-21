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

# 👇 adăugată linia pentru model
OAI_MODEL = os.getenv("OAI_MODEL", "gpt-5-mini")

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
                model=OAI_MODEL,   # 👈 modificat să folosească variabila
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
# (restul rămâne exact la fel ca în fișierul tău)
