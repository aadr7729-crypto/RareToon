#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
"""Telegram inline buttons with semantic button style values.

The bot uses python-telegram-bot for updates and polling, and serializes the
Bot API style field through api_kwargs.
"""

from telegram import InlineKeyboardButton as TelegramButton

_BUTTON_STYLE_VALUES = {
    "DEFAULT": "default",
    "PRIMARY": "primary",
    "DANGER": "danger",
    "SUCCESS": "success",
    "LINK": "link",
}


class Button(TelegramButton):
    """Inline button whose style is inferred from its label."""

    def __init__(self, text, callback_data=None, url=None, web_app=None,
                 login_url=None, user_id=None, switch_inline_query=None,
                 switch_inline_query_current_chat=None, callback_game=None,
                 requires_password=None, pay=None, copy_text=None,
                 icon_custom_emoji_id=None, style=None, **kwargs):
        if style is None:
            lower_text = text.lower()
            if any(x in lower_text for x in (
                "close", "cancel", "delete", "ban", "❌", "✖️", "back",
                "⬅️", "del", "remove", "➖", "off", "clear", "reset",
            )):
                style = "DANGER"
            elif any(x in lower_text for x in (
                "done", "download", "✅", "📥", "success", "add", "➕",
                "start", "joined", "on", "active", "payment", "buy", "join",
                "vip", "premium", "all episodes",
            )):
                style = "SUCCESS"
            elif any(x in lower_text for x in (
                "help", "about", "developer", "info", "settings", "preview",
                "prev", "next", "◀️", "▶️", "noop", "refresh", "clean",
                "manage", "format", "covers",
            )):
                style = "DEFAULT"
            else:
                style = "PRIMARY"

        if hasattr(style, "value"):
            style = style.value
        elif isinstance(style, str):
            style = _BUTTON_STYLE_VALUES.get(style.upper(), style.lower())

        api_kwargs = dict(kwargs.pop("api_kwargs", {}) or {})
        api_kwargs["style"] = style
        super().__init__(
            text=text,
            callback_data=callback_data,
            url=url,
            web_app=web_app,
            login_url=login_url,
            switch_inline_query=switch_inline_query,
            switch_inline_query_current_chat=switch_inline_query_current_chat,
            callback_game=callback_game,
            pay=pay,
            api_kwargs=api_kwargs,
        )