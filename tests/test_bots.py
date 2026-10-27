import asyncio
import json
import re
import tempfile
import unittest

import httpx
from fastapi.testclient import TestClient

from qiban.agent import run_agent
from qiban.storage import Store
from qiban.web import create_app


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.old = self.store.conversation()["id"]
        self.client = TestClient(
            create_app(self.store, start_worker=False), base_url="http://127.0.0.1"
        )
        self.csrf = re.search(
            r'name="_csrf" value="([^"]+)"', self.client.get("/").text
        )[1]

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_first_bot_is_idempotent_and_preserves_old_chats(self):
        for _ in range(3):
            self.assertIn("你想怎么称呼我", self.client.get("/").text)
        self.assertEqual(len(self.store.all("SELECT * FROM bots")), 1)
        bot = self.store.create_bot(first=True)
        self.assertEqual(self.store.bot_for(self.old)["id"], bot["id"])
        self.assertEqual(self.store.create_bot(first=True)["id"], bot["id"])
        self.assertEqual(len(self.store.all("SELECT * FROM conversations")), 2)

    def test_create_configure_reload_and_fork(self):
        self.assertEqual(self.client.post("/bots/new").status_code, 403)
        response = self.client.post("/bots/new", data={"_csrf": self.csrf})
        bid = response.url.path.split("/")[-1]
        prompt = "你的名字叫做「佳佳」，个性乐观有活力，很会替人打气。"
        response = self.client.post(
            "/bots/" + bid + "/configure", data={"_csrf": self.csrf, "prompt": prompt}
        )
        bot = self.store.one("SELECT * FROM bots WHERE id=?", (bid,))
        self.assertEqual(bot["name"], "佳佳")
        self.assertEqual(bot["prompt"], prompt)
        self.assertNotIn("你想怎么称呼我", response.text)
        self.assertIn("发消息给佳佳", response.text)
        self.assertEqual(
            Store(self.tmp.name).bot_for(bot["conversation_id"])["name"], "佳佳"
        )
        fork = self.store.fork(bot["conversation_id"])
        self.assertEqual(self.store.bot_for(fork)["id"], bid)
        self.assertEqual(len(self.store.all("SELECT * FROM bots")), 2)

    def test_delete_bot_is_confirmed_scoped_and_last_bot_recovers(self):
        first = self.store.create_bot(first=True)
        other = self.store.create_bot()
        cid = first["conversation_id"]
        fork = self.store.fork(cid)
        self.store.execute(
            "INSERT INTO google_connections VALUES (?,?,?,?,?,?)",
            (first["id"], "gmail", "test@example.com", "encrypted", "connected", 1),
        )
        path = "/bots/" + first["id"] + "/delete"
        self.assertIn("永久删除队友", self.client.get(path).text)
        self.assertEqual(
            self.client.post(path, data={"confirmation": first["name"]}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                path, data={"_csrf": self.csrf, "confirmation": "wrong"}
            ).status_code,
            400,
        )
        response = self.client.post(
            path, data={"_csrf": self.csrf, "confirmation": first["name"]}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.store.bot_for(fork))
        self.assertIsNone(
            self.store.one("SELECT id FROM conversations WHERE id=?", (cid,))
        )
        self.assertFalse(self.store.all("SELECT * FROM google_connections"))
        self.assertIsNotNone(self.store.bot_for(other["conversation_id"]))
        self.store.delete_bot(other["id"], other["name"])
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(len(self.store.all("SELECT * FROM bots")), 1)

    def test_persona_is_in_model_context_and_other_bots_are_not(self):
        self.store.set_setting("baseUrl", "https://model.example/v1")
        self.store.set_setting("model", "test")
        first = self.store.create_bot(first=True)
        second = self.store.create_bot()
        self.store.configure_bot(first["id"], "ONLY_FIRST_PERSONA", "Haru")
        self.store.configure_bot(second["id"], "ONLY_SECOND_PERSONA", "Sunny")
        bodies = []

        def respond(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "Hello"}}]
                },
            )

        for bot in [first, second]:
            task = self.store.submit(bot["conversation_id"], "你好")
            asyncio.run(
                run_agent(self.store, task, transport=httpx.MockTransport(respond))
            )
        self.assertIn("ONLY_FIRST_PERSONA", json.dumps(bodies[0]))
        self.assertNotIn("ONLY_SECOND_PERSONA", json.dumps(bodies[0]))
        self.assertIn("ONLY_SECOND_PERSONA", json.dumps(bodies[1]))
        self.assertNotIn("ONLY_FIRST_PERSONA", json.dumps(bodies[1]))
