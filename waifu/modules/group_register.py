"""
modules/group_register.py — Group Registration System

Flow:
  Bot added → member check (groupcheck.py)
    ├─ < 200 members  → leave
    └─ ≥ 200 members  → send "contact owner to register" message (stay but blocked)

  Owner approves:
    • Inside group: /register
    • DM: /register <group_id>

  Unregistered groups:
    • All commands/messages silently blocked (registration guard, group=-1)
    • /register by owner passes through

  Owner extras:
    • /unregister <group_id | inside group>
    • /reglist — list all registered groups
"""
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from waifu import LOGGER, OWNER_ID, application, bot_settings_collection

_DB_ID = "registered_groups"
_cache: set[int] | None = None   # None = not loaded yet


# ── Cache helpers ─────────────────────────────────────────────────────────────

async def _load() -> set[int]:
    global _cache
    if _cache is not None:
        return _cache
    doc = await bot_settings_collection.find_one({"_id": _DB_ID}) or {}
    _cache = set(doc.get("groups", []))
    return _cache


def _bust() -> None:
    global _cache
    _cache = None


async def is_registered(chat_id: int) -> bool:
    return chat_id in await _load()


# ── DB writers ────────────────────────────────────────────────────────────────

async def _add(chat_id: int) -> None:
    await bot_settings_collection.update_one(
        {"_id": _DB_ID},
        {"$addToSet": {"groups": chat_id}},
        upsert=True,
    )
    _bust()


async def _remove(chat_id: int) -> None:
    await bot_settings_collection.update_one(
        {"_id": _DB_ID},
        {"$pull": {"groups": chat_id}},
    )
    _bust()


# ── Registration guard (runs before all handlers) ─────────────────────────────

async def guard(update: Update, context: CallbackContext) -> None:
    """
    Block messages/callbacks from unregistered groups.
    Exceptions:
      • Owner typing /register in the group (so they can approve from inside)
    """
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return                          # private → always pass

    if await is_registered(chat.id):
        return                          # registered → pass

    # Allow owner to run /register even in an unregistered group
    msg = update.effective_message
    if msg and msg.text:
        txt = msg.text.strip().split()[0].lower().lstrip("/").split("@")[0]
        if txt == "register" and update.effective_user and update.effective_user.id == OWNER_ID:
            return

    raise ApplicationHandlerStop        # block everything else


# ── /register ─────────────────────────────────────────────────────────────────

async def register_group(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if u.id != OWNER_ID:
        return

    chat = update.effective_chat

    # ── Used inside a group ───────────────────────────────────────────────────
    if chat.type in ("group", "supergroup"):
        await _add(chat.id)
        LOGGER.info("Group registered: %d (%s)", chat.id, chat.title)
        await update.message.reply_text(
            f"✅ <b>{escape(chat.title or str(chat.id))}</b>\n"
            f"<code>{chat.id}</code>\n\n"
            "Registration approved — bot ကို ဒီ group မှာ အသုံးပြုလို့ရပြီ ✓",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Used in DM with group_id ──────────────────────────────────────────────
    if context.args:
        raw = context.args[0]
        try:
            gid = int(raw)
        except ValueError:
            await update.message.reply_text(
                "❌ Group ID မှားနေတယ်\n"
                "ဥပမာ: <code>/register -1001234567890</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        await _add(gid)
        LOGGER.info("Group registered via DM: %d", gid)

        try:
            chat_info = await context.bot.get_chat(gid)
            gtitle = escape(chat_info.title or str(gid))
        except Exception:
            gtitle = str(gid)

        await update.message.reply_text(
            f"✅ <b>{gtitle}</b> register လုပ်ပြီ!\n"
            f"<code>{gid}</code>\n\n"
            "Bot ကို ဒီ group မှာ အသုံးပြုလို့ရပြီ ✓",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── No args in DM ─────────────────────────────────────────────────────────
    await update.message.reply_text(
        "📋 <b>Group Register</b>\n\n"
        "Group ထဲမှာ:\n  <code>/register</code>\n\n"
        "DM မှာ:\n  <code>/register -1001234567890</code>",
        parse_mode=ParseMode.HTML,
    )


# ── /unregister ───────────────────────────────────────────────────────────────

async def unregister_group(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if u.id != OWNER_ID:
        return

    chat = update.effective_chat

    if chat.type in ("group", "supergroup"):
        await _remove(chat.id)
        await update.message.reply_text("✅ Group unregistered.")
        LOGGER.info("Group unregistered: %d", chat.id)
        return

    if context.args:
        try:
            gid = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid group ID.", parse_mode=ParseMode.HTML)
            return
        await _remove(gid)
        await update.message.reply_text(
            f"✅ <code>{gid}</code> unregistered.", parse_mode=ParseMode.HTML
        )
        LOGGER.info("Group unregistered via DM: %d", gid)
        return

    await update.message.reply_text(
        "Usage: <code>/unregister</code> (in group)  or  "
        "<code>/unregister -1001234567890</code> (DM)",
        parse_mode=ParseMode.HTML,
    )


# ── /reglist ──────────────────────────────────────────────────────────────────

async def reglist(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if u.id != OWNER_ID:
        return

    groups = await _load()
    if not groups:
        await update.message.reply_text("📭 Register လုပ်ထားတဲ့ group မရှိသေးဘူး")
        return

    lines = [f"📋 <b>Registered Groups ({len(groups)})</b>\n"]
    for gid in sorted(groups):
        try:
            info = await context.bot.get_chat(gid)
            name = escape(info.title or str(gid))
        except Exception:
            name = str(gid)
        lines.append(f"• <b>{name}</b>  <code>{gid}</code>")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── Register handlers ─────────────────────────────────────────────────────────

# Guard — group=-1 so it fires BEFORE all default (group=0) handlers
application.add_handler(
    MessageHandler(filters.ChatType.GROUPS, guard),
    group=-1,
)
application.add_handler(
    CallbackQueryHandler(guard, pattern=r".*"),
    group=-1,
)

application.add_handler(CommandHandler("register",   register_group,   block=False))
application.add_handler(CommandHandler("unregister", unregister_group, block=False))
application.add_handler(CommandHandler("reglist",    reglist,          block=False))
