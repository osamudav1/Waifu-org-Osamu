"""
modules/emoji_settings.py

Bot message ထဲက emoji တွေကို Telegram Premium custom emoji ID နဲ့ အစားထိုး

Commands (owner DM only):
  /setemoji <emoji> <premium_id>   — emoji ကို premium ID နဲ့ map
  /delemoji <emoji>                — mapping ဖျက်
  /listemojis                      — map ထားတဲ့ emoji တွေ ကြည့်

Example:
  /setemoji 🪷 5368324170671202286
  → bot message မှာ 🪷 ပါတိုင်း premium animated emoji အဖြစ် ပြမယ်
"""
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, OWNER_ID, bot_settings_collection, LOGGER

_DB_ID = "premium_emoji_map"
_CACHE: dict[str, str] | None = None  # unicode_emoji → premium_id


async def _load() -> dict[str, str]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    doc = await bot_settings_collection.find_one({"_id": _DB_ID}) or {}
    _CACHE = doc.get("map", {})
    return _CACHE


def _invalidate() -> None:
    global _CACHE
    _CACHE = None


async def apply_emojis(text: str) -> str:
    """
    Bot message ထဲမှာ unicode emoji ကို premium emoji tag နဲ့ ပြောင်း။
    ParseMode.HTML နဲ့ send တဲ့ message တွေမှာ သုံးပါ။
    """
    emap = await _load()
    if not emap:
        return text
    for char, eid in emap.items():
        if char in text:
            text = text.replace(char, f'<tg-emoji emoji-id="{eid}">{char}</tg-emoji>')
    return text


def _is_owner_pm(update: Update) -> bool:
    return (
        update.effective_user.id == OWNER_ID
        and update.effective_chat.type == "private"
    )


# ── /setemoji <emoji> <premium_id> ────────────────────────────────────────────

async def setemoji(update: Update, context: CallbackContext) -> None:
    if not _is_owner_pm(update):
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "📝 <b>သုံးနည်း:</b>\n"
            "<code>/setemoji 🪷 5368324170671202286</code>\n\n"
            "Bot message ထဲမှာ 🪷 ပါတိုင်း premium emoji အဖြစ် ပြမယ်\n\n"
            "Premium emoji ID ရှာနည်း:\n"
            "① Telegram Premium emoji pack ထဲကနေ copy\n"
            "② @like bot ကနေ emoji ကို forward လုပ်ပြီး ID ကြည့်",
            parse_mode=ParseMode.HTML,
        )
        return

    emoji_char = args[0]
    emoji_id   = args[1].strip()

    if not emoji_id.isdigit():
        await update.message.reply_text(
            "❌ Premium emoji ID ဟာ ဂဏန်းသာ ဖြစ်ရမယ်\n"
            "ဥပမာ: <code>5368324170671202286</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await bot_settings_collection.update_one(
        {"_id": _DB_ID},
        {"$set": {f"map.{emoji_char}": emoji_id}},
        upsert=True,
    )
    _invalidate()
    LOGGER.info("Premium emoji set: %s → %s", emoji_char, emoji_id)

    preview = f'<tg-emoji emoji-id="{emoji_id}">{emoji_char}</tg-emoji>'
    await update.message.reply_text(
        f"✅ Map ထားပြီ!\n\n"
        f"{emoji_char} → {preview}\n\n"
        f"<i>Bot message မှာ {emoji_char} ပါတိုင်း premium version ပြမယ်</i>",
        parse_mode=ParseMode.HTML,
    )


# ── /delemoji <emoji> ─────────────────────────────────────────────────────────

async def delemoji(update: Update, context: CallbackContext) -> None:
    if not _is_owner_pm(update):
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: <code>/delemoji 🪷</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    emoji_char = args[0]
    await bot_settings_collection.update_one(
        {"_id": _DB_ID},
        {"$unset": {f"map.{emoji_char}": ""}},
    )
    _invalidate()

    await update.message.reply_text(
        f"✅ {emoji_char} mapping ဖျက်ပြီ — unicode ပြန်ဖြစ်သွားမယ်"
    )


# ── /listemojis ───────────────────────────────────────────────────────────────

async def listemojis(update: Update, context: CallbackContext) -> None:
    if not _is_owner_pm(update):
        return

    emap = await _load()

    if not emap:
        await update.message.reply_text(
            "📭 Premium emoji map မရှိသေးဘူး\n\n"
            "ထည့်ရန်: <code>/setemoji 🪷 5368324170671202286</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [f"🎨 <b>Premium Emoji Map ({len(emap)} ခု)</b>\n"]
    for char, eid in emap.items():
        preview = f'<tg-emoji emoji-id="{eid}">{char}</tg-emoji>'
        lines.append(f"• {char} → {preview}  <code>{eid}</code>")

    lines.append("\nဖျက်ရန်: <code>/delemoji &lt;emoji&gt;</code>")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


# ── /clearemojis ──────────────────────────────────────────────────────────────

async def clearemojis(update: Update, context: CallbackContext) -> None:
    if not _is_owner_pm(update):
        return

    await bot_settings_collection.update_one(
        {"_id": _DB_ID},
        {"$unset": {"map": ""}},
    )
    _invalidate()
    await update.message.reply_text("✅ Premium emoji map အကုန် ရှင်းပြီ")


# ── Register ──────────────────────────────────────────────────────────────────

application.add_handler(CommandHandler("setemoji",   setemoji,   block=False))
application.add_handler(CommandHandler("delemoji",   delemoji,   block=False))
application.add_handler(CommandHandler("listemojis", listemojis, block=False))
application.add_handler(CommandHandler("clearemojis",clearemojis,block=False))
