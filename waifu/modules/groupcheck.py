"""
modules/groupcheck.py

Auto member check when bot is added to a group.
  < 200 members  → warn + leave
  ≥ 200 members  → send "registration required" message + Contact Owner button
                   (bot stays but all commands blocked until owner registers)
Owner/sudo bypass: can add bot to any group without member check.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import CallbackContext, ChatMemberHandler

from waifu import LOGGER, OWNER_ID, application, sudo_users

MIN_MEMBERS = 200


async def on_bot_chat_member(update: Update, context: CallbackContext) -> None:
    result = update.my_chat_member
    if not result:
        return

    chat       = result.chat
    new_status = result.new_chat_member.status

    if chat.type not in ("group", "supergroup"):
        return

    if new_status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        return

    added_by = result.from_user
    if added_by and (added_by.id == OWNER_ID or added_by.id in sudo_users):
        LOGGER.info("Bot added to %s by owner/sudo — auto-registering", chat.id)
        try:
            from waifu.modules.group_register import _add
            await _add(chat.id)
            await context.bot.send_message(
                chat.id,
                f"✅ <b>Bot activated!</b>\n\n"
                f"Group ID: <code>{chat.id}</code>\n"
                f"<i>Owner ထည့်သွင်းတာကြောင့် auto-registered ✓</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            LOGGER.warning("Auto-register failed for %s: %s", chat.id, e)
        return

    try:
        count = await context.bot.get_chat_member_count(chat.id)
    except Exception as e:
        LOGGER.warning("Could not get member count for %s: %s", chat.id, e)
        return

    # ── Too few members → leave ───────────────────────────────────────────────
    if count < MIN_MEMBERS:
        LOGGER.info("Group %s has %d members (< %d) — leaving", chat.id, count, MIN_MEMBERS)
        try:
            await context.bot.send_message(
                chat.id,
                f"<blockquote>"
                f"⚠️ <b>Not enough members</b>\n\n"
                f"This bot requires at least <b>{MIN_MEMBERS}</b> members.\n"
                f"This group has only <b>{count}</b> members."
                f"</blockquote>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        try:
            await context.bot.leave_chat(chat.id)
        except Exception as e:
            LOGGER.warning("Could not leave %s: %s", chat.id, e)
        return

    # ── Enough members → registration required ────────────────────────────────
    LOGGER.info("Bot added to %s — %d members ✓ (awaiting registration)", chat.id, count)

    contact_btn = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📩 Contact Owner to Register",
            url=f"tg://user?id={OWNER_ID}",
        )
    ]])

    try:
        await context.bot.send_message(
            chat.id,
            f"<blockquote>"
            f"👋 <b>Thanks for adding me!</b>\n\n"
            f"✅ This group has <b>{count}</b> members — requirement met!\n\n"
            f"⚠️ <b>Registration Required</b>\n"
            f"This group needs owner approval before the bot can be used here.\n\n"
            f"Please contact the owner to register this group.\n"
            f"Group ID: <code>{chat.id}</code>"
            f"</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=contact_btn,
        )
    except Exception as e:
        LOGGER.warning("Could not send registration message to %s: %s", chat.id, e)


application.add_handler(
    ChatMemberHandler(on_bot_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
)
