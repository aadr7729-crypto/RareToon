import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import handlers
from telegram.constants import ParseMode


class HelpFormattingTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_handler_ignores_updates_without_a_user(self):
        update = SimpleNamespace(effective_user=None, effective_message=None)

        await handlers.handle_message(update, None)

    async def test_help_message_has_balanced_markdown_bold_markers(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            message=message,
        )
        with patch.object(handlers, "_authorized", return_value=True):
            await handlers.cmd_help(update, None)

        text = message.reply_text.await_args.args[0]
        self.assertEqual(message.reply_text.await_args.kwargs["parse_mode"], ParseMode.MARKDOWN)
        self.assertEqual(text.count("*") % 2, 0)
        self.assertIn("*Workflow:*\n", text)


if __name__ == "__main__":
    unittest.main()