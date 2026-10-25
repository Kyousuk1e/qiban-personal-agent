import asyncio
import json
import re
import tempfile
import unittest

import httpx
from fastapi.testclient import TestClient

from qiban.agent import execute_tool, run_agent
from qiban.storage import Store
from qiban.uploads import Uploads
from qiban.web import create_app


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.uploads = Uploads(self.store)
        self.client = TestClient(
            create_app(self.store, start_worker=False), base_url="http://127.0.0.1"
        )
        self.csrf = re.search(
            r'name="_csrf" value="([^"]+)"', self.client.get("/").text
        )[1]

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_upload_to_chat_fork_and_detach(self):
        result = self.client.post(
            "/files/upload",
            data={"_csrf": self.csrf, "start_chat": "1"},
            files={"file": ("brief.txt", b"Launch date is Friday")},
        )
        self.assertEqual(result.status_code, 200)
        cid = result.url.path.split("/")[-1]
        item = self.store.context_files(cid)[0]
        self.assertIn("brief.txt", result.text)
        task = self.store.submit(cid, "What is the date?")
        with self.assertRaises(ValueError):
            self.store.fork(cid)
        self.store.execute(
            "UPDATE tasks SET status='succeeded' WHERE id=?", (task["id"],)
        )
        fork = self.store.fork(cid)
        self.assertEqual(len(self.store.context_files(fork)), 1)
        self.assertEqual(
            self.store.one(
                "SELECT content FROM messages WHERE conversation_id=?", (fork,)
            )["content"],
            "What is the date?",
        )
        self.client.post(
            "/chat/" + fork + "/context",
            data={"_csrf": self.csrf, "upload_id": item["id"], "remove": "1"},
        )
        self.assertEqual(self.store.context_files(fork), [])
        self.assertEqual(len(self.store.context_files(cid)), 1)
        blank = self.client.post("/conversations/new", data={"_csrf": self.csrf})
        self.assertNotIn("已关联 · 文字可读", blank.text)
        self.assertEqual(self.store.context_files(blank.url.path.split("/")[-1]), [])
        self.assertEqual(self.client.get("/chat/missing").status_code, 400)
        self.assertEqual(len(Store(self.tmp.name).context_files(cid)), 1)

    def test_background_sent_each_turn_and_tools_scoped(self):
        self.store.set_setting("baseUrl", "https://model.example/v1")
        self.store.set_setting("model", "test")
        item = self.uploads.import_file("brief.txt", b"CONFIDENTIAL_DATE_FRIDAY")
        cid = self.store.conversation()["id"]
        self.store.attach(cid, item["id"])
        captured = []

        def respond(request):
            captured.append(json.loads(request.content)["messages"])
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "Friday"}}]
                },
            )

        for prompt in ["Which day?", "What should I prepare?"]:
            task = self.store.submit(cid, prompt)
            asyncio.run(
                run_agent(self.store, task, transport=httpx.MockTransport(respond))
            )
            self.store.execute(
                "UPDATE tasks SET status='succeeded' WHERE id=?", (task["id"],)
            )
        self.assertTrue(
            all(
                "CONFIDENTIAL_DATE_FRIDAY" in json.dumps(messages)
                for messages in captured
            )
        )
        fresh = self.store.conversation()["id"]
        task = self.store.submit(fresh, "Hello")
        asyncio.run(run_agent(self.store, task, transport=httpx.MockTransport(respond)))
        self.assertNotIn("CONFIDENTIAL_DATE_FRIDAY", json.dumps(captured[-1]))
        self.assertNotIn("Which day?", json.dumps(captured[-1]))
        self.assertEqual(
            execute_tool(self.store, task, "list_files", {})["uploads"], []
        )
        with self.assertRaises(ValueError):
            execute_tool(self.store, task, "read_file", {"name": item["text_name"]})
