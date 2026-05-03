import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import storage
import utils


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "bot.db")
        storage.init_db(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_model_history_trim_and_clear(self) -> None:
        self.assertIsNone(storage.get_user_model(1))

        storage.set_user_model(1, "gemini-2.5-flash")
        self.assertEqual(storage.get_user_model(1), "gemini-2.5-flash")

        storage.append_turn(1, "gemini-2.5-flash", "hello", "hi")
        history = storage.load_history(1, 20)
        self.assertEqual([item.role for item in history], ["user", "model"])
        self.assertEqual(history[0].parts[0].text, "hello")
        self.assertEqual(history[1].parts[0].text, "hi")

        for index in range(25):
            storage.append_turn(
                1,
                "gemini-2.5-flash",
                f"user {index}",
                f"model {index}",
            )

        history = storage.load_history(1, 20)
        self.assertEqual(len(history), 40)
        self.assertEqual(history[0].role, "user")
        self.assertEqual(history[-1].role, "model")
        self.assertEqual(history[-1].parts[0].text, "model 24")

        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE user_id = 1"
            ).fetchone()[0]
        self.assertEqual(count, 40)

        storage.clear_user_history(1)
        self.assertEqual(storage.get_user_model(1), "gemini-2.5-flash")
        self.assertEqual(storage.load_history(1, 20), [])


class FakeChat:
    def __init__(self, model: str, history: list | None = None):
        self.model = model
        self.history = history or []

    def get_history(self) -> list:
        return self.history


class FakeChats:
    def create(self, model: str, history: list | None = None) -> FakeChat:
        return FakeChat(model, history)


class FakeAio:
    chats = FakeChats()


class FakeClient:
    aio = FakeAio()


class UtilsSqliteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        storage.init_db(os.path.join(self.tmp.name, "bot.db"))
        utils.chat_dict.clear()
        utils.client = FakeClient()

    async def asyncTearDown(self) -> None:
        utils.chat_dict.clear()
        utils.client = None
        self.tmp.cleanup()

    async def test_restore_switch_clear_and_image_summary(self) -> None:
        session = await utils.init_user(7)
        self.assertIsNone(session["model"])
        self.assertIsNone(session["chat"])

        await utils.select_model(7, "gemini-2.5-flash")
        await utils.save_turn(7, "hello", "hi")
        await utils.save_turn(7, [object(), "what is this?"], "an image answer")
        await utils.save_turn(7, "empty", "   ")

        history = storage.load_history(7, 20)
        self.assertEqual(
            [item.parts[0].text for item in history],
            ["hello", "hi", "[Image] what is this?", "an image answer"],
        )

        utils.chat_dict.clear()
        restored = await utils.init_user(7)
        self.assertEqual(restored["model"], "gemini-2.5-flash")
        self.assertEqual(restored["chat"].model, "gemini-2.5-flash")
        self.assertEqual(len(restored["chat"].history), 4)

        await utils.select_model(7, "gemini-2.5-pro")
        switched = utils.chat_dict[7]
        self.assertEqual(switched["model"], "gemini-2.5-pro")
        self.assertEqual(switched["chat"].model, "gemini-2.5-pro")
        self.assertEqual(len(switched["chat"].history), 4)

        await utils.clear_history(7)
        self.assertEqual(storage.get_user_model(7), "gemini-2.5-pro")
        self.assertEqual(storage.load_history(7, 20), [])
        self.assertEqual(utils.chat_dict[7]["chat"].model, "gemini-2.5-pro")


if __name__ == "__main__":
    unittest.main()
