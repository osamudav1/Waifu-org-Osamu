"""
modules/antispam.py — Spam penalty system

Tracks message frequency per user per group.
If a user sends more than SPAM_LIMIT messages within SPAM_WINDOW seconds → spam!

Penalty: $0.10 (10 cents) deducted from balance per spam detection.
Warning message sent once per cooldown period (don't flood warnings).

Exempt: owner, sudo users, bots.
Only active in registered groups.
"""
import time
from collections import defaultdict
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, MessageHandler, filters

from waifu import OWNER_ID, application, sudo_users, user_collection
from waifu.cache import db_op, invalidate_user

# ── Config ────────────────────────────────────────────────────────────────────

SPAM_LIMIT      = 15      # messages
SPAM_WINDOW     = 5       # seconds
PENALTY_CENTS   = 10      # $0.10
WARN_COOLDOWN   = 30      # seconds between warnings for same user

# ── State (in-memory, resets on restart — intentional) ────────────────────────

# (chat_id, user_id) → list of recent message timestamps
_msg_times: dict[tuple, list[float]] = defaultdict(list)

# (chat_id, user_id) → last warning timestamp
_last_warn: dict[tuple, float] = {}


def _usd(cents: int) -> str:
    return f"${cents / 100:.2f}"


# ── Handler ───────────────────────────────────────────────────────────────────

async def spam_check(update: Update, context: CallbackContext) -> None:
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not msg or not user or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        return
    if user.is_bot:
        return
    if user.id == OWNER_ID or user.id in sudo_users:
        return

    key = (chat.id, user.id)
    now = time.time()

    # Keep only timestamps within the window
    times = _msg_times[key]
    times.append(now)
    _msg_times[key] = [t for t in times if now - t <= SPAM_WINDOW]

    if len(_msg_times[key]) < SPAM_LIMIT:
        return  # not spamming

    # ── Spam detected! ────────────────────────────────────────────────────────

    # Clear window so penalty fires once per burst, not every message
    _msg_times[key] = []

    # Deduct $0.10
    async with db_op():
        result = await user_collection.find_one_and_update(
            {"id": user.id},
            {"$inc": {"coins": -PENALTY_CENTS}},
            return_document=True,
            upsert=False,
        )
    if result:
        invalidate_user(user.id)
        new_bal = result.get("coins", 0) - PENALTY_CENTS
    else:
        new_bal = 0

    # Warn user (once per cooldown)
    last = _last_warn.get(key, 0)
    if now - last < WARN_COOLDOWN:
        return
    _last_warn[key] = now

    mention = f'<a href="tg://user?id={user.id}">{escape(user.first_name)}</a>'
    await msg.reply_text(
        f"⚠️ {mention} Spam\n\n"
        f"💸 Penalty: <b>-{_usd(PENALTY_CENTS)}</b>\n"
        f"🏦 Balance: <b>{_usd(max(0, new_bal))}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── Register (group=1 so it runs after registration guard but for all msgs) ───

application.add_handler(
    MessageHandler(
        filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
        spam_check,
        block=False,
    ),
    group=1,
)
