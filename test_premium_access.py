import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import database
import handlers
from keyboards import premium_duration_keyboard
from telegram.ext import ApplicationHandlerStop


class PremiumDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mongo_patch = patch.object(database, "_mongo", return_value=None)
        self.path_patch = patch.object(
            database, "DATABASE_PATH", os.path.join(self.temp_dir.name, "premium.db")
        )
        self.mongo_patch.start()
        self.path_patch.start()
        database.init_db()

    def tearDown(self):
        self.path_patch.stop()
        self.mongo_patch.stop()
        self.temp_dir.cleanup()

    def test_grants_extend_expire_and_can_be_removed(self):
        user_id = 987654321
        first_expiry = database.grant_premium(user_id, 7, 111)
        next_expiry = database.grant_premium(user_id, 15, 111)

        self.assertTrue(database.has_premium(user_id))
        self.assertGreaterEqual(
            next_expiry - first_expiry, 15 * 24 * 60 * 60 - 1
        )
        self.assertEqual(
            [row["user_id"] for row in database.list_premiums()],
            [user_id],
        )

        conn = database._get()
        conn.execute(
            "UPDATE premiums SET expires_at=? WHERE user_id=?",
            (time.time() - 1, user_id),
        )
        conn.commit()
        conn.close()

        self.assertFalse(database.has_premium(user_id))
        self.assertEqual(database.list_premiums(), [])
        self.assertTrue(database.remove_premium(user_id))
        self.assertFalse(database.remove_premium(user_id))

    def test_grant_duration_is_bounded(self):
        with self.assertRaises(ValueError):
            database.grant_premium(123, 0, 1)
        with self.assertRaises(ValueError):
            database.grant_premium(123, 3651, 1)


class PremiumHandlerTests(unittest.IsolatedAsyncioTestCase):
    def test_duration_keyboard_has_requested_choices(self):
        keyboard = premium_duration_keyboard(123456)
        labels = [row[0].text for row in keyboard.inline_keyboard]
        callback_data = [
            row[0].callback_data for row in keyboard.inline_keyboard
        ]

        self.assertEqual(labels, ["7 Days", "15 Days", "30 Days", "Custom"])
        self.assertEqual(
            callback_data,
            [
                "premium:duration:123456:7",
                "premium:duration:123456:15",
                "premium:duration:123456:30",
                "premium:duration:123456:custom",
            ],
        )

    async def test_nonpremium_user_gets_purchase_instructions(self):
        message = SimpleNamespace(
            text="/upload naruto",
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_message=message,
            callback_query=None,
        )
        with patch.object(handlers, "_authorized", return_value=False):
            with self.assertRaises(ApplicationHandlerStop):
                await handlers.premium_access_gate(update, None)

        message.reply_text.assert_awaited_once_with(
            handlers.PREMIUM_REQUIRED_MESSAGE
        )
        self.assertIn("7 Days Premium - 20rs", handlers.PREMIUM_REQUIRED_MESSAGE)
        self.assertIn("15 Days Premium - 40rs", handlers.PREMIUM_REQUIRED_MESSAGE)
        self.assertIn("1 Month Premium - 80rs", handlers.PREMIUM_REQUIRED_MESSAGE)
        self.assertIn("@shivam_anime", handlers.PREMIUM_REQUIRED_MESSAGE)

    async def test_cancel_remains_available_without_premium(self):
        message = SimpleNamespace(text="/cancel", reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_message=message,
            callback_query=None,
        )
        with patch.object(handlers, "_authorized", return_value=False):
            await handlers.premium_access_gate(update, None)

        message.reply_text.assert_not_awaited()

    async def test_addpremium_displays_the_four_duration_buttons(self):
        admin_id, user_id = 11, 123456
        handlers.user_state.pop(admin_id, None)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=admin_id),
            message=message,
        )
        context = SimpleNamespace(args=[str(user_id)])
        with patch.object(handlers, "_admin_authorized", return_value=True):
            await handlers.cmd_addpremium(update, context)

        reply = message.reply_text.await_args
        self.assertEqual(
            [row[0].text for row in reply.kwargs["reply_markup"].inline_keyboard],
            ["7 Days", "15 Days", "30 Days", "Custom"],
        )
        self.assertEqual(
            handlers.user_state[admin_id]["pending_premium_target"], user_id
        )
        handlers.user_state.pop(admin_id, None)

    async def test_nonadmin_cannot_use_admin_settings_or_grant_premium(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            message=message,
        )
        context = SimpleNamespace(args=["123456"])
        with patch.object(handlers, "_admin_authorized", return_value=False):
            await handlers.cmd_quality(update, context)
            await handlers.cmd_addpremium(update, context)

        self.assertEqual(
            [call.args[0] for call in message.reply_text.await_args_list],
            ["Owner/admin only.", "Owner/admin only."],
        )

    async def test_preset_button_grants_selected_duration(self):
        admin_id, user_id = 11, 123456
        handlers.user_state[admin_id] = {"pending_premium_target": user_id}
        query_message = SimpleNamespace(edit_text=AsyncMock())
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=admin_id),
            data=f"premium:duration:{user_id}:15",
            message=query_message,
            answer=AsyncMock(),
        )
        context = SimpleNamespace()
        expiry = time.time() + 15 * 24 * 60 * 60
        with (
            patch.object(handlers, "_admin_authorized", return_value=True),
            patch.object(
                handlers, "_grant_premium", new=AsyncMock(return_value=expiry)
            ) as grant,
        ):
            await handlers.cb_premium_duration(
                SimpleNamespace(callback_query=query), context
            )

        grant.assert_awaited_once_with(context, admin_id, user_id, 15)
        self.assertIn("15 days", query_message.edit_text.await_args.args[0])
        self.assertNotIn(
            "pending_premium_target", handlers.user_state[admin_id]
        )
        handlers.user_state.pop(admin_id, None)

    async def test_custom_duration_button_requests_days_and_grants_them(self):
        admin_id, user_id = 11, 123456
        handlers.user_state[admin_id] = {"pending_premium_target": user_id}
        query_message = SimpleNamespace(edit_text=AsyncMock())
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=admin_id),
            data=f"premium:duration:{user_id}:custom",
            message=query_message,
            answer=AsyncMock(),
        )
        with patch.object(handlers, "_admin_authorized", return_value=True):
            await handlers.cb_premium_duration(
                SimpleNamespace(callback_query=query), SimpleNamespace()
            )

        self.assertTrue(
            handlers.user_state[admin_id]["awaiting_custom_premium_days"]
        )
        self.assertIn("whole number of days", query_message.edit_text.await_args.args[0])

        message = SimpleNamespace(
            text="12",
            forward_from_chat=None,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=admin_id),
            message=message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        expiry = time.time() + 12 * 24 * 60 * 60
        with (
            patch.object(handlers, "_admin_authorized", return_value=True),
            patch.object(
                handlers, "_grant_premium", new=AsyncMock(return_value=expiry)
            ) as grant,
        ):
            await handlers.handle_message(update, context)

        grant.assert_awaited_once_with(context, admin_id, user_id, 12)
        self.assertNotIn(
            "awaiting_custom_premium_days", handlers.user_state[admin_id]
        )
        self.assertNotIn(
            "pending_premium_target", handlers.user_state[admin_id]
        )
        handlers.user_state.pop(admin_id, None)

    async def test_removal_and_active_list_commands(self):
        admin_id, user_id = 11, 123456
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=admin_id),
            message=message,
        )
        context = SimpleNamespace(
            args=[str(user_id)],
            bot=SimpleNamespace(send_message=AsyncMock()),
        )
        with (
            patch.object(handlers, "_admin_authorized", return_value=True),
            patch.object(handlers.db, "remove_premium", return_value=True),
        ):
            await handlers.cmd_rempremium(update, context)

        context.bot.send_message.assert_awaited_once_with(
            user_id, "Your RareAnimes premium access has been removed."
        )
        self.assertIn("Premium removed", message.reply_text.await_args.args[0])

        expiry = time.time() + 7 * 24 * 60 * 60
        with (
            patch.object(handlers, "_admin_authorized", return_value=True),
            patch.object(
                handlers.db,
                "list_premiums",
                return_value=[{"user_id": user_id, "expires_at": expiry}],
            ),
        ):
            await handlers.cmd_listpremium(update, SimpleNamespace())

        self.assertIn(str(user_id), message.reply_text.await_args.args[0])
        self.assertIn("Active premium users", message.reply_text.await_args.args[0])

if __name__ == "__main__":
    unittest.main()