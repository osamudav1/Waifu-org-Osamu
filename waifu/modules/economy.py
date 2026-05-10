"""
modules/economy.py — Dollar ($) economy system

Currency: cents integer (50 = $0.50, 12420 = $124.20)
  stored in user doc as "coins" field (backward-compatible)

Commands:
  /balance          — show $ balance
  /daily            — claim $0.50 daily
  /quicksell <id>   — sell char at fixed rarity price → char returns to drop pool
  /sell <id> <amt>  — list on market (price in cents, e.g. 150 = $1.50)
  /market           — browse market
  /buy <listing_id> — buy from market
  /delist <id>      — remove your listing
"""
import math
import time
from bson import ObjectId
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import KeyboardButtonStyle, ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, market_collection, collection
from waifu.config import Config
from waifu.cache import db_op, get_user, set_user, invalidate_user

_DAILY_COOLDOWN = 86_400   # 24 h
_LIST_FEE       = 10       # $0.10 listing fee (cents)
_ITEMS_PER_PAGE = 8

_RARITY_BADGE = {
    "⚪ Common":           "⚪",
    "🟣 Rare":             "🟣",
    "🟤 Medium":           "🟤",
    "🟡 Legendary":        "🟡",
    "🔮 Mythical":         "🔮",
    "🪞 Supreme":          "🪞",
    "💮 Special Edition":  "💮",
    "🌐 Divine":           "🌐",
    "✖️ CrossVerse":        "✖️",
    "🌌 Universal":        "🌌",
    "⚔️ Cataphract":       "⚔️",
}


def _usd(cents: int) -> str:
    """Format cents as USD string: 150 → '$1.50'"""
    return f"${cents / 100:.2f}"


def _fmt_time(secs: int) -> str:
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


async def _ensure_user(user_id: int, u) -> dict:
    doc = get_user(user_id)
    if doc is None:
        async with db_op():
            doc = await user_collection.find_one({"id": user_id})
    if not doc:
        doc = {
            "id": user_id, "username": u.username,
            "first_name": u.first_name, "characters": [],
            "coins": 0, "xp": 0, "wins": 0,
            "total_guesses": 0, "favorites": [],
        }
        async with db_op():
            await user_collection.insert_one(doc)
    set_user(user_id, doc)
    return doc


# ── Market list builder ───────────────────────────────────────────────────────

async def _build_list(page: int):
    total = await market_collection.count_documents({})
    if total == 0:
        return None

    total_pages = max(1, math.ceil(total / _ITEMS_PER_PAGE))
    page        = max(0, min(page, total_pages - 1))
    listings    = (
        await market_collection
        .find({})
        .sort("price", 1)
        .skip(page * _ITEMS_PER_PAGE)
        .limit(_ITEMS_PER_PAGE)
        .to_list(_ITEMS_PER_PAGE)
    )

    caption = (
        f"🏪 <b>Market</b>  [{total} listing{'s' if total != 1 else ''}]\n"
        f"<i>ကဒ်ကို နှိပ်ပြီး photo + info ကြည့်ဝယ်နိုင်</i>"
    )

    rows = []
    row  = []
    for lst in listings:
        char  = lst["char"]
        badge = _RARITY_BADGE.get(char.get("rarity", ""), "🎴")
        label = f"{badge} {char['name'][:14]}  {_usd(lst['price'])}"
        row.append(InlineKeyboardButton(
            label,
            callback_data=f"mkt:card:{lst['_id']}:{page}",
            style=KeyboardButtonStyle.PRIMARY,
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"mkt:list:{page-1}", style=KeyboardButtonStyle.PRIMARY))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop", style=KeyboardButtonStyle.PRIMARY))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"mkt:list:{page+1}", style=KeyboardButtonStyle.PRIMARY))
    if nav:
        rows.append(nav)

    return caption, InlineKeyboardMarkup(rows)


# ── Market card builder ───────────────────────────────────────────────────────

async def _build_card(listing_id_str: str, back_page: int):
    try:
        oid = ObjectId(listing_id_str)
    except Exception:
        return None

    lst = await market_collection.find_one({"_id": oid})
    if not lst:
        return None

    char   = lst["char"]
    badge  = _RARITY_BADGE.get(char.get("rarity", ""), "🎴")
    rarity = char.get("rarity", "?")

    caption = (
        f"🏪 <b>Market Card</b>\n\n"
        f"{badge} <b>{escape(char['name'])}</b>\n"
        f"📺 Aɴɪᴍᴇ: {escape(char.get('anime', '?'))}\n"
        f"✨ Rᴀʀɪᴛʏ: {rarity}\n\n"
        f"💵 Price: <b>{_usd(lst['price'])}</b>\n"
        f"👤 Seller: <b>{escape(lst['seller_name'])}</b>\n"
        f"🆔 <code>{lst['_id']}</code>"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🛒 Buy  —  {_usd(lst['price'])}",
            callback_data=f"mkt:buy:{lst['_id']}:{back_page}",
            style=KeyboardButtonStyle.SUCCESS,
        )],
        [InlineKeyboardButton("🔙 Market ပြန်", callback_data=f"mkt:list:{back_page}", style=KeyboardButtonStyle.PRIMARY)],
    ])

    return caption, char.get("img_url"), kb


# ── /balance ──────────────────────────────────────────────────────────────────

async def balance(update: Update, context: CallbackContext) -> None:
    u   = update.effective_user
    doc = await _ensure_user(u.id, u)
    bal = doc.get("coins", 0)
    await update.message.reply_text(
        f"💵 <b>{escape(u.first_name)}'s Balance</b>\n\n"
        f"<b>{_usd(bal)}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── /daily ────────────────────────────────────────────────────────────────────

async def daily(update: Update, context: CallbackContext) -> None:
    u   = update.effective_user
    doc = await _ensure_user(u.id, u)
    now  = time.time()
    last = doc.get("last_daily", 0)

    if now - last < _DAILY_COOLDOWN:
        remaining = int(_DAILY_COOLDOWN - (now - last))
        await update.message.reply_text(
            f"⏳ Daily already claimed!\nCome back in <b>{_fmt_time(remaining)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    reward = Config.DAILY_COINS  # 50 cents = $0.50
    async with db_op():
        await user_collection.update_one(
            {"id": u.id},
            {"$inc": {"coins": reward}, "$set": {"last_daily": now}},
        )
    invalidate_user(u.id)
    new_bal = doc.get("coins", 0) + reward
    await update.message.reply_text(
        f"🎁 <b>Daily reward!</b>\n\n"
        f"You received <b>{_usd(reward)}</b>\n"
        f"Current balance: <b>{_usd(new_bal)}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── /quicksell ────────────────────────────────────────────────────────────────

_QUICKSELL_PRICES_LINES = "\n".join(
    f"  {rarity}: <b>{_usd(price)}</b>"
    for rarity, price in Config.RARITY_SELL_PRICE.items()
)

_POWERED_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton(
        "✧ POWERED BY OSAMU ✧",
        callback_data="qs:powered",
        style=KeyboardButtonStyle.PRIMARY,
    )
]])


async def quicksell(update: Update, context: CallbackContext) -> None:
    """Sell a character at fixed rarity price; char returns to the drop pool."""
    u = update.effective_user

    if not context.args:
        await update.message.reply_text(
            "💵 <b>Quick Sell — Fixed Rarity Prices</b>\n\n"
            f"{_QUICKSELL_PRICES_LINES}\n\n"
            "Usage: <code>/quicksell &lt;char_id&gt;</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=_POWERED_KB,
        )
        return

    char_id = context.args[0].strip()
    doc = await user_collection.find_one({"id": u.id})
    if not doc:
        await update.message.reply_text("❌ You have no characters.")
        return

    char = next((c for c in doc.get("characters", []) if c["id"] == char_id), None)
    if not char:
        await update.message.reply_text(
            f"❌ Character <code>{char_id}</code> မင်းဆီ မရှိဘူး.",
            parse_mode=ParseMode.HTML,
        )
        return

    rarity    = char.get("rarity", "")
    sell_price = Config.RARITY_SELL_PRICE.get(rarity)
    if sell_price is None:
        await update.message.reply_text(
            f"❌ <b>{escape(char['name'])}</b> ရဲ့ rarity ({rarity}) price မသတ်မှတ်ရသေးဘူး.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Remove from user harem
    async with db_op():
        await user_collection.update_one(
            {"id": u.id},
            {
                "$pull": {"characters": {"id": char_id}},
                "$inc":  {"coins": sell_price},
            },
        )
    invalidate_user(u.id)

    # Return char to drop pool: decrement claimed_count
    async with db_op():
        await collection.update_one(
            {"id": char_id},
            {"$inc": {"claimed_count": -1}},
        )

    new_bal    = doc.get("coins", 0) + sell_price
    badge      = _RARITY_BADGE.get(rarity, "🎴")
    tier_name  = rarity.split(" ", 1)[-1]   # e.g. "🔮 Mythical" → "Mythical"

    await update.message.reply_text(
        f"💵 <b>Transaction Successful</b>\n\n"
        f"{badge} <b>{escape(char['name'])}</b> — {tier_name} Tier\n\n"
        f"💰 +{_usd(sell_price)} credited\n"
        f"🏦 Balance: <b>{_usd(new_bal)}</b>\n\n"
        f"♻️ Character re-entered into the drop pool.",
        parse_mode=ParseMode.HTML,
    )


# ── /sell (list on market) ────────────────────────────────────────────────────

async def sell(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: <code>/sell [char_id] [price]</code>\n"
            f"Price unit: cents  (e.g. 150 = $1.50)\n"
            f"<i>Listing fee: {_usd(_LIST_FEE)}</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id, price_str = context.args
    if not price_str.isdigit() or int(price_str) <= 0:
        await update.message.reply_text("❌ Price must be a positive number (cents).")
        return
    price = int(price_str)

    doc = await user_collection.find_one({"id": u.id})
    if not doc:
        await update.message.reply_text("❌ You have no characters.")
        return

    coins = doc.get("coins", 0)
    if coins < _LIST_FEE:
        await update.message.reply_text(
            f"❌ Listing fee မလုံ!\n"
            f"Listing fee: <b>{_usd(_LIST_FEE)}</b>  |  သင့်ရှိငွေ: <b>{_usd(coins)}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    char = next((c for c in doc.get("characters", []) if c["id"] == char_id), None)
    if not char:
        await update.message.reply_text("❌ That character isn't in your collection.")
        return

    async with db_op():
        await user_collection.update_one(
            {"id": u.id},
            {
                "$pull": {"characters": {"id": char_id}},
                "$inc":  {"coins": -_LIST_FEE},
            },
        )
    invalidate_user(u.id)

    listing = {
        "seller_id":   u.id,
        "seller_name": u.first_name,
        "char_id":     char_id,
        "char":        char,
        "price":       price,
        "listed_at":   time.time(),
    }
    result = await market_collection.insert_one(listing)

    await update.message.reply_text(
        f"🏪 <b>{escape(char['name'])}</b> listed for <b>{_usd(price)}</b>!\n"
        f"<i>Listing fee: -{_usd(_LIST_FEE)}</i>\n"
        f"Listing ID: <code>{result.inserted_id}</code>",
        parse_mode=ParseMode.HTML,
    )


# ── /market ───────────────────────────────────────────────────────────────────

async def market(update: Update, context: CallbackContext) -> None:
    total  = await market_collection.count_documents({})
    mp_btn = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏪 Market Place", switch_inline_query_current_chat="market", style=KeyboardButtonStyle.PRIMARY),
    ]])

    if total == 0:
        await update.message.reply_text(
            "🏪 <b>Market</b>\n\n<i>ဈေးကွက်မှာ listing မရှိသေးဘူး</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=mp_btn,
        )
        return

    latest = await market_collection.find_one({}, sort=[("listed_at", -1)])
    char   = latest.get("char", {})
    img    = char.get("img_url", "")
    name   = escape(char.get("name",  "Unknown"))
    anime  = escape(char.get("anime", "Unknown"))
    rarity = char.get("rarity", "?")
    price  = latest.get("price", 0)
    seller = escape(latest.get("seller_name", "?"))

    cap = (
        f"🏪 <b>Market</b>  [{total} listing{'s' if total != 1 else ''}]\n\n"
        f"🌸 <b>{name}</b>\n"
        f"📺 {anime}\n"
        f"💎 {rarity}\n\n"
        f"💵 Price: <b>{_usd(price)}</b>\n"
        f"👤 Seller: <b>{seller}</b>\n\n"
        f"<blockquote>More Waifu Market Click\nMarket Place Button Now</blockquote>"
    )

    if img and not img.startswith("http"):
        await update.message.reply_photo(
            photo=img, caption=cap,
            parse_mode=ParseMode.HTML, reply_markup=mp_btn,
        )
    else:
        await update.message.reply_text(cap, parse_mode=ParseMode.HTML, reply_markup=mp_btn)


# ── Market callbacks ──────────────────────────────────────────────────────────

async def market_cb(update: Update, context: CallbackContext) -> None:
    q     = update.callback_query
    uid   = q.from_user.id
    parts = q.data.split(":")

    if parts[1] == "list":
        await q.answer()
        page   = int(parts[2])
        result = await _build_list(page)
        if not result:
            try:
                await q.edit_message_text("🏪 The market is empty right now.")
            except Exception:
                pass
            return
        caption, kb = result
        try:
            await q.edit_message_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            try:
                await q.edit_message_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            except Exception:
                pass
        return

    if parts[1] == "card":
        await q.answer()
        result = await _build_card(parts[2], int(parts[3]))
        if not result:
            await q.answer("❌ Listing not found!", show_alert=True)
            return
        caption, photo, kb = result
        if photo:
            try:
                await q.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=caption, parse_mode=ParseMode.HTML),
                    reply_markup=kb,
                )
                return
            except Exception:
                pass
        try:
            await q.edit_message_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            try:
                await q.edit_message_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            except Exception:
                pass
        return

    if parts[1] == "buy":
        listing_id_str = parts[2]
        back_page      = int(parts[3]) if len(parts) > 3 else 0

        try:
            oid = ObjectId(listing_id_str)
        except Exception:
            await q.answer("❌ Invalid listing.", show_alert=True)
            return

        listing = await market_collection.find_one({"_id": oid})
        if not listing:
            await q.answer("❌ ရောင်းပြီးသို့ မရှိတော့ပါ!", show_alert=True)
            await _refresh_list(q, back_page)
            return

        if listing["seller_id"] == uid:
            await q.answer("❌ မိမိပစ္စည်း မဝယ်နိုင်!", show_alert=True)
            return

        buyer = await user_collection.find_one({"id": uid})
        coins = buyer.get("coins", 0) if buyer else 0
        if coins < listing["price"]:
            await q.answer(
                f"❌ Balance မလုံ!\n{_usd(coins)} / {_usd(listing['price'])}",
                show_alert=True,
            )
            return

        async with db_op():
            await user_collection.update_one(
                {"id": uid},
                {"$inc": {"coins": -listing["price"]}, "$push": {"characters": listing["char"]}},
            )
        async with db_op():
            await user_collection.update_one(
                {"id": listing["seller_id"]},
                {"$inc": {"coins": listing["price"]}},
            )
        invalidate_user(uid)
        invalidate_user(listing["seller_id"])
        await market_collection.delete_one({"_id": oid})

        char = listing["char"]
        await q.answer(
            f"✅ {char['name']} ဝယ်ပြီး!  -{_usd(listing['price'])}",
            show_alert=True,
        )
        await _refresh_list(q, back_page)
        return


async def _refresh_list(q, back_page: int) -> None:
    result = await _build_list(back_page)
    if not result:
        try:
            await q.edit_message_text("🏪 The market is empty right now.")
        except Exception:
            pass
        return
    caption, kb = result
    try:
        await q.edit_message_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        try:
            await q.edit_message_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass


# ── /buy (direct) ─────────────────────────────────────────────────────────────

async def buy(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: <code>/buy [listing_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        oid = ObjectId(context.args[0])
    except Exception:
        await update.message.reply_text("❌ Invalid listing ID.")
        return

    listing = await market_collection.find_one({"_id": oid})
    if not listing:
        await update.message.reply_text("❌ Listing not found or already sold.")
        return
    if listing["seller_id"] == u.id:
        await update.message.reply_text("❌ You can't buy your own listing.")
        return

    buyer = await user_collection.find_one({"id": u.id})
    if not buyer or buyer.get("coins", 0) < listing["price"]:
        await update.message.reply_text(
            f"❌ Balance မလုံ. Need <b>{_usd(listing['price'])}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    await user_collection.update_one(
        {"id": u.id},
        {"$inc": {"coins": -listing["price"]}, "$push": {"characters": listing["char"]}},
    )
    await user_collection.update_one(
        {"id": listing["seller_id"]},
        {"$inc": {"coins": listing["price"]}},
    )
    await market_collection.delete_one({"_id": oid})

    char = listing["char"]
    await update.message.reply_text(
        f"✅ You bought <b>{escape(char['name'])}</b> for <b>{_usd(listing['price'])}</b>!",
        parse_mode=ParseMode.HTML,
    )


# ── /delist ───────────────────────────────────────────────────────────────────

async def delist(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: <code>/delist [listing_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        oid = ObjectId(context.args[0])
    except Exception:
        await update.message.reply_text("❌ Invalid listing ID.")
        return

    listing = await market_collection.find_one({"_id": oid})
    if not listing:
        await update.message.reply_text("❌ Listing not found.")
        return
    if listing["seller_id"] != u.id:
        await update.message.reply_text("❌ That's not your listing.")
        return

    await market_collection.delete_one({"_id": oid})
    await user_collection.update_one(
        {"id": u.id},
        {"$push": {"characters": listing["char"]}, "$inc": {"coins": _LIST_FEE}},
    )
    await update.message.reply_text(
        f"✅ Listing ဖျက်ပြီး — Character ပြန်ရပြီ + {_usd(_LIST_FEE)} refund"
    )


# ── "Powered by Osamu" popup callback ─────────────────────────────────────────

async def powered_cb(update: Update, context: CallbackContext) -> None:
    await update.callback_query.answer(
        "✧ POWERED BY OSAMU ✧\n\nWaifu Catcher Bot\n© 2026 Osamu. All Rights Reserved.",
        show_alert=True,
    )


# ── Register ──────────────────────────────────────────────────────────────────

application.add_handler(CommandHandler("balance",   balance,   block=False))
application.add_handler(CommandHandler("daily",     daily,     block=False))
application.add_handler(CommandHandler("quicksell", quicksell, block=False))
application.add_handler(CommandHandler("sell",      sell,      block=False))
application.add_handler(CommandHandler("market",    market,    block=False))
application.add_handler(CommandHandler("buy",       buy,       block=False))
application.add_handler(CommandHandler("delist",    delist,    block=False))
application.add_handler(CallbackQueryHandler(market_cb,   pattern=r"^mkt:",      block=False))
application.add_handler(CallbackQueryHandler(powered_cb,  pattern=r"^qs:powered", block=False))
