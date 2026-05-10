"""
waifu/__main__.py  —  Entry point.
Run with:  python -m waifu

Mode selection (automatic):
  - Fly.io          → FLY_APP_NAME is set    → polling + health server on PORT
  - Koyeb           → KOYEB_APP_NAME is set  → polling + health server on PORT
  - Hugging Face    → SPACE_ID is set        → polling + health server on 7860
  - Render          → RENDER=true            → polling + health server on PORT (default 10000)
  - Replit VM       → REPLIT_DEPLOYMENT=1    → webhook mode (no Conflict)
  - Dev / local     → polling mode (no health server)
"""
import importlib
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import BotCommand
from telegram.constants import BotCommandScopeType
from telegram.error import NetworkError, TimedOut

from waifu import ALL_MODULES, LOGGER


async def _error_handler(update, context) -> None:
    """Silently swallow transient network errors; log the rest."""
    err = context.error
    if isinstance(err, (TimedOut, NetworkError)):
        LOGGER.warning("Transient network error (ignored): %s", err)
        return
    LOGGER.error("Unhandled exception", exc_info=err)


def _run_health_server(port: int = 8080) -> None:
    """Minimal HTTP health-check server — required by Koyeb / Replit deployments."""
    import json as _json
    class _Handler(BaseHTTPRequestHandler):
        def _respond(self, send_body: bool = True):
            if self.path == "/ping":
                body = b"pong"
                ct   = "text/plain"
            elif self.path == "/healthz":
                body = _json.dumps({"status": "ok"}).encode()
                ct   = "application/json"
            else:
                body = b"OK"
                ct   = "text/plain"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
        def do_GET(self):
            self._respond(send_body=True)
        def do_HEAD(self):
            self._respond(send_body=False)
        def log_message(self, *args):
            pass

    server = HTTPServer(("0.0.0.0", port), _Handler)
    LOGGER.info("Health-check server listening on port %d", port)
    server.serve_forever()


async def _migrate_indexes() -> None:
    from waifu import user_collection
    try:
        await user_collection.drop_index("user_id_1")
        LOGGER.info("Migration: dropped stale index users.user_id_1")
    except Exception:
        pass


_USER_COMMANDS = [
    BotCommand("start",     "✨ Check my availability"),
    BotCommand("help",      "📖 Get help"),
    BotCommand("harem",     "💞 Your harem / collection"),
    BotCommand("search",    "🔍 Search characters"),
    BotCommand("profile",   "🪪 View your harem profile"),
    BotCommand("top",       "🏆 Top harems in this chat"),
    BotCommand("ctop",      "🎖️ Top characters"),
    BotCommand("ranking",   "📊 Global ranking"),
    BotCommand("daily",     "🎁 Claim daily reward ($0.50)"),
    BotCommand("balance",   "💵 Check your balance"),
    BotCommand("quicksell", "💸 Quick sell a character"),
    BotCommand("market",    "🏪 Browse the market"),
    BotCommand("sell",      "🏷️ List a character on market"),
    BotCommand("buy",       "🛒 Buy a listing by ID"),
    BotCommand("delist",    "❌ Remove your market listing"),
    BotCommand("trade",     "🤝 Trade characters with someone"),
    BotCommand("gift",      "🎀 Send a character as gift"),
    BotCommand("duel",      "⚔️ Duel another user"),
    BotCommand("fav",       "⭐ Favourite a character"),
    BotCommand("favlist",   "📋 View your favourites"),
]

_OWNER_EXTRA_COMMANDS = [
    BotCommand("broadcast",         "📢 Broadcast a message"),
    BotCommand("stats",             "📈 Bot statistics"),
    BotCommand("groups",            "👥 List groups"),
    BotCommand("upload",            "⬆️ Upload a character"),
    BotCommand("uploadchar",        "🖼️ Upload character (alt)"),
    BotCommand("delete",            "🗑️ Delete a character"),
    BotCommand("forcedrop",         "💧 Force drop a character"),
    BotCommand("coins",             "🪙 Manage user coins"),
    BotCommand("setrate",           "⚙️ Set drop rate"),
    BotCommand("changetime",        "⏱️ Change spawn interval"),
    BotCommand("resetdropcount",    "🔄 Reset drop count"),
    BotCommand("setdropannounce",   "📣 Set drop announce channel"),
    BotCommand("cleardropannounce", "🔕 Clear drop announce"),
    BotCommand("setemoji",          "🎨 Map custom emoji"),
    BotCommand("delemoji",          "🗑️ Delete emoji mapping"),
    BotCommand("listemojis",        "📝 List emoji mappings"),
    BotCommand("clearemojis",       "🧹 Clear all emoji mappings"),
    BotCommand("addwelcomephoto",   "🖼️ Add welcome photo"),
    BotCommand("removewelcomephoto","❎ Remove welcome photo"),
    BotCommand("backup",            "💾 Backup database"),
    BotCommand("restore",           "♻️ Restore database"),
    BotCommand("ping",              "🏓 Ping the bot"),
    BotCommand("update",            "🔃 Update bot"),
    BotCommand("sh",                "🖥️ Run shell command"),
    BotCommand("charactervdadd",    "🎬 Add AMV video to a character"),
    BotCommand("deletevd",          "🗑️ Remove AMV video from a character"),
]


async def _set_commands(bot, owner_id: int) -> None:
    from telegram import BotCommandScopeDefault, BotCommandScopeChat
    try:
        # All users — user commands only
        await bot.set_my_commands(_USER_COMMANDS, scope=BotCommandScopeDefault())
        # Owner — user commands + admin commands
        await bot.set_my_commands(
            _USER_COMMANDS + _OWNER_EXTRA_COMMANDS,
            scope=BotCommandScopeChat(chat_id=owner_id),
        )
        LOGGER.info("Bot command menus set (user + owner).")
    except Exception as e:
        LOGGER.warning("Could not set bot commands: %s", e)


async def _post_init(application) -> None:
    from waifu.modules.inlinequery import create_indexes
    from waifu.config import Config as _Cfg
    await _migrate_indexes()
    await create_indexes()
    LOGGER.info("DB indexes ensured.")
    await _set_commands(application.bot, _Cfg.OWNER_ID)
    # Always clear any leftover webhook so polling gets all updates
    try:
        await application.bot.delete_webhook(drop_pending_updates=False)
        LOGGER.info("Webhook cleared — polling will receive all updates.")
    except Exception as e:
        LOGGER.warning("Could not clear webhook: %s", e)


def main() -> None:
    # ── Detect platform FIRST so health server starts before module loading ────
    is_fly      = bool(os.environ.get("FLY_APP_NAME"))
    is_koyeb    = bool(os.environ.get("KOYEB_APP_NAME"))
    is_hf       = bool(os.environ.get("SPACE_ID"))
    is_render   = os.environ.get("RENDER", "").lower() == "true"
    is_deployed = os.environ.get("REPLIT_DEPLOYMENT", "0") == "1"

    # Start health server immediately on cloud platforms so health checks pass
    # even while heavy module loading / DB connection happens below.
    if is_render or is_fly or is_koyeb or is_hf:
        _early_port = int(os.environ.get("PORT", "10000"))
        _ht = threading.Thread(target=_run_health_server, args=(_early_port,), daemon=True)
        _ht.start()
        LOGGER.info("Health server started early on port %d", _early_port)

    from waifu.config import Config
    Config.validate()
    LOGGER.info("Using Token: %s***", Config.TOKEN[:5])
    LOGGER.info("Loading %d module(s)…", len(ALL_MODULES))
    for name in ALL_MODULES:
        try:
            importlib.import_module(f"waifu.modules.{name}")
            LOGGER.debug("  ✓ %s", name)
        except Exception as exc:
            LOGGER.error("  ✗ %s — %s", name, exc, exc_info=True)
            raise
    LOGGER.info("All modules loaded.")

    from waifu import application
    application.post_init = _post_init
    application.add_error_handler(_error_handler)

    _ALLOWED_UPDATES = [
        "message", "edited_message", "callback_query",
        "inline_query", "chosen_inline_result",
        "chat_member", "my_chat_member",
        "pre_checkout_query", "shipping_query",
    ]
    _POLLING_KWARGS = dict(
        drop_pending_updates=True,
        allowed_updates=_ALLOWED_UPDATES,
    )

    if is_fly:
        # ── Fly.io: health server already started early above ─────────────────
        LOGGER.info("Fly.io mode: polling + health server on port %d", _early_port)
        application.run_polling(**_POLLING_KWARGS)

    elif is_koyeb:
        # ── Koyeb: health server already started early above ──────────────────
        LOGGER.info("Koyeb mode: polling + health server on port %d", _early_port)
        application.run_polling(**_POLLING_KWARGS)

    elif is_hf:
        # ── Hugging Face Spaces: health server already started early above ─────
        LOGGER.info("Hugging Face Spaces mode: polling + health server on port %d", _early_port)
        application.run_polling(**_POLLING_KWARGS, bootstrap_retries=-1)

    elif is_render:
        # ── Render: health server already started early above ─────────────────
        LOGGER.info("Render mode: polling + health server on port %d", _early_port)
        application.run_polling(**_POLLING_KWARGS)

    elif is_deployed:
        # ── Replit VM deployment ───────────────────────────────────────────────
        port    = int(os.environ.get("PORT", "8080"))
        domains = os.environ.get("REPLIT_DOMAINS", "")
        domain  = domains.split(",")[0].strip() if domains else ""
        token   = os.environ.get("BOT_TOKEN", "")

        if domain and token:
            url_path    = token
            webhook_url = f"https://{domain}/{url_path}"
            LOGGER.info("Replit webhook mode: port=%d url=https://%s/...", port, domain)
            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=url_path,
                webhook_url=webhook_url,
                drop_pending_updates=True,
                allowed_updates=_ALLOWED_UPDATES,
            )
        else:
            LOGGER.warning("REPLIT_DOMAINS/TOKEN empty — falling back to polling + health server on port %d", port)
            t = threading.Thread(target=_run_health_server, args=(port,), daemon=True)
            t.start()
            application.run_polling(**_POLLING_KWARGS)

    else:
        # ── Local dev: simple polling ─────────────────────────────────────────
        LOGGER.info("Starting bot (polling)…")
        application.run_polling(**_POLLING_KWARGS)


if __name__ == "__main__":
    main()
