"""
modules/profile.py — /profile command
New format: box-style with rarity breakdown + global position.
"""
import math
import random
from collections import defaultdict
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection as waifu_collection, user_collection, PHOTO_URL


# ── XP helpers ────────────────────────────────────────────────────────────────

def _xp_for_level(level: int) -> int:
    return int(200 * (level ** 1.5))


def _calc_level(xp: int) -> tuple[int, int, int]:
    level = 1
    while _xp_for_level(level + 1) <= xp:
        level += 1
    floor = _xp_for_level(level)
    nxt   = _xp_for_level(level + 1)
    return level, xp - floor, nxt - floor


def _bar(value: int, maximum: int, length: int = 10) -> str:
    filled = int(length * value / max(maximum, 1))
    return "▰" * filled + "▱" * (length - filled)


# ── Rarity order (highest → lowest) ──────────────────────────────────────────

_RARITIES = [
    ("⚔️ Cataphract",        "⚔️", "ᴄᴀᴛᴀᴘʜʀᴀᴄᴛ"),
    ("🌌 Universal",         "🌌", "ᴜɴɪᴠᴇʀsᴀʟ"),
    ("✖️ CrossVerse",        "✖️", "ᴄʀᴏssᴠᴇʀsᴇ"),
    ("🌐 Divine",            "🌐", "ᴅɪᴠɪɴᴇ"),
    ("💮 Special Edition",   "💮", "sᴘᴇᴄɪᴀʟ ᴇᴅɪᴛɪᴏɴ"),
    ("🪞 Supreme",           "🪞", "sᴜᴘʀᴇᴍᴇ"),
    ("🔮 Mythical",          "🔮", "ᴍʏᴛʜɪᴄᴀʟ"),
    ("🟡 Legendary",         "🟡", "ʟᴇɢᴇɴᴅᴀʀʏ"),
    ("🟤 Medium",            "🟤", "ᴍᴇᴅɪᴜᴍ"),
    ("🟣 Rare",              "🟣", "ʀᴀʀᴇ"),
    ("⚪ Common",            "⚪", "ᴄᴏᴍᴍᴏɴ"),
]

_LINE = "─" * 19


# ── command ───────────────────────────────────────────────────────────────────

async def profile(update: Update, context: CallbackContext) -> None:
    # Resolve target user
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        u_doc  = await user_collection.find_one({"id": target.id})
    elif context.args:
        username = context.args[0].lstrip("@")
        u_doc    = await user_collection.find_one({"username": username})
    else:
        target = update.effective_user
        u_doc  = await user_collection.find_one({"id": target.id})

    if not u_doc:
        await update.message.reply_text("❌ That user hasn't played yet.")
        return

    uid        = u_doc["id"]
    first_name = escape(u_doc.get("first_name", "User"))
    chars      = u_doc.get("characters", [])
    xp         = u_doc.get("xp", 0)

    total_count  = len(chars)
    unique_ids   = {c["id"] for c in chars}
    unique_count = len(unique_ids)

    # Total chars in DB
    total_waifus = await waifu_collection.count_documents({})
    if total_waifus == 0:
        total_waifus = 1
    harem_pct = unique_count / total_waifus * 100

    # Level & XP bar
    level, xp_in, xp_need = _calc_level(xp)
    bar = _bar(xp_in, xp_need, 10)

    # Rarity breakdown
    rar_total  = defaultdict(int)   # total catches per rarity key
    rar_unique = defaultdict(set)   # unique char ids per rarity key
    for c in chars:
        key = c.get("rarity", "")
        rar_total[key]  += 1
        rar_unique[key].add(c["id"])

    rar_lines = []
    for (key, emoji, label) in _RARITIES:
        tot = rar_total.get(key, 0)
        uniq = len(rar_unique.get(key, set()))
        if tot > 0:
            rar_lines.append(
                f"├─➩ {emoji} 𝙍𝘼𝙍𝙄𝙏𝙔: {label}: {uniq} ({tot})"
            )
    if not rar_lines:
        rar_lines = ["├─➩ 📭 No characters yet"]

    # Global position (rank by unique char count)
    pos_cursor = user_collection.aggregate([
        {"$project": {
            "char_ids": {
                "$map": {
                    "input": {"$ifNull": ["$characters", []]},
                    "as":    "c",
                    "in":    "$$c.id",
                }
            }
        }},
        {"$project": {
            "unique_count": {"$size": {"$setUnion": ["$char_ids", []]}}
        }},
        {"$match": {"unique_count": {"$gt": unique_count}}},
        {"$count": "ahead"},
    ])
    pos_data   = await pos_cursor.to_list(1)
    global_pos = (pos_data[0]["ahead"] + 1) if pos_data else 1

    # ── Build caption ─────────────────────────────────────────────────────────
    caption = (
        f"╭──「 🎗️ <b>Cᴀᴛᴄʜᴇʀ Pʀᴏғɪʟᴇ</b> 🎗️ 」\n"
        f"├─➩ 👤 ᴜsᴇʀ: {first_name}\n"
        f"├─➩ 🔩 ᴜsᴇʀ ɪᴅ: <code>{uid}</code>\n"
        f"├─➩ ⚡ ᴛᴏᴛᴀʟ ᴄʜᴀʀᴀᴄᴛᴇʀ: {total_count} ({unique_count})\n"
        f"├─➩ 🫧 ʜᴀʀᴇᴍ: {unique_count}/{total_waifus} ({harem_pct:.3f}%)\n"
        f"├─➩ ℹ️ ᴇxᴘᴇʀɪᴇɴᴄᴇ ʟᴇᴠᴇʟ: {level}\n"
        f"├─➩ 📈 ᴘʀᴏɢʀᴇss ʙᴀʀ:\n"
        f"╰         {bar}\n"
        f"\n"
        f"╭{_LINE}\n"
        + "\n".join(rar_lines) + "\n"
        f"╰{_LINE}\n"
        f"\n"
        f"╭{_LINE}\n"
        f"├─➩ 🌍 ɢʟᴏʙᴀʟ ᴘᴏsɪᴛɪᴏɴ: <b>#{global_pos}</b>\n"
        f"╰{_LINE}"
    )

    # ── Profile image: last caught → favourite → random ───────────────────────
    photo: str | None = None

    if chars:
        photo = chars[-1].get("img_url")

    if not photo:
        fav_id = (u_doc.get("favorites") or [None])[0]
        if fav_id:
            fav_char = next((c for c in chars if c["id"] == fav_id), None)
            photo    = (fav_char or {}).get("img_url")

    if not photo and PHOTO_URL:
        photo = random.choice(PHOTO_URL)

    if photo:
        try:
            await update.message.reply_photo(
                photo, caption=caption, parse_mode=ParseMode.HTML
            )
            return
        except Exception:
            pass

    await update.message.reply_text(caption, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("profile", profile, block=False))
