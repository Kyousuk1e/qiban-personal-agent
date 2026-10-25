"""Regression tests use isolated databases and mock models; never the user's key."""

import asyncio
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from qiban.agent import run_agent, validate_url
from qiban.files import Files
from qiban.storage import Store
from qiban.web import create_app
from qiban.worker import acquire, process_one


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.files = Files(self.tmp.name)
        self.env = patch.dict(
            os.environ, {"MODEL_BASE_URL": "", "MODEL_NAME": "", "MODEL_API_KEY": ""}
        )
        self.env.start()
        self.client = TestClient(
            create_app(self.store, start_worker=False), base_url="http://127.0.0.1"
        )
        self.headers = {"X-Agent-Client": "local-ui"}

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_file_boundaries_and_no_overwrite(self):
        for name in [
            "../secret.txt",
            "a/b.md",
            "C:\\secret.txt",
            "a.txt:stream",
            "CON.txt",
            ".env",
            "x.html",
            "a.md.",
        ]:
            with self.assertRaises(ValueError):
                self.files.path(name)
        self.files.write("资料.md", "测试")
        self.assertEqual(self.files.read("资料.md"), "测试")
        with self.assertRaises(FileExistsError):
            self.files.write("资料.md", "覆盖")
        with self.assertRaises(ValueError):
            self.files.write("大文件.txt", "字" * 100000)

    def test_symlink_refused(self):
        outside = Path(self.tmp.name) / "secret.txt"
        outside.write_text("private")
        try:
            (self.files.root / "link.txt").symlink_to(outside)
        except OSError:
            self.skipTest("Windows symlinks require developer mode")
        with self.assertRaises(ValueError):
            self.files.read("link.txt")
        self.assertNotIn("link.txt", self.files.list())

    def test_form_csrf_and_origin(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        token = re.search(r'name="_csrf" value="([^"]+)"', response.text)[1]
        self.assertNotIn("<script", response.text)
        self.assertIn("script-src 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(
            self.client.post("/memory", data={"content": "bad"}).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                "/memory", data={"_csrf": token, "content": "中文回答"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.store.one("SELECT content FROM memories")["content"], "中文回答"
        )
        self.assertEqual(
            self.client.get(
                "/api/state", headers={"Origin": "https://evil.example"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/state", headers={"Host": "evil.example"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/api/memories", json={"content": "bad"}).status_code, 403
        )

    def test_memory_edit_delete_and_html_escape(self):
        memory = self.client.post(
            "/api/memories",
            headers=self.headers,
            json={"content": "<script>alert(1)</script>"},
        ).json()
        page = self.client.get("/memory").text
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page)
        self.client.patch(
            f"/api/memories/{memory['id']}",
            headers=self.headers,
            json={"content": "新偏好"},
        )
        self.assertEqual(
            self.store.one("SELECT content FROM memories")["content"], "新偏好"
        )
        self.client.delete(f"/api/memories/{memory['id']}", headers=self.headers)
        self.assertEqual(self.store.all("SELECT * FROM memories"), [])

    def test_browser_chat_origin_and_csrf(self):
        page = self.client.get("/")
        self.assertEqual(page.headers["Referrer-Policy"], "same-origin")
        token = re.search(r'name="_csrf" value="([^"]+)"', page.text)[1]
        form = {"_csrf": token, "content": "/files"}
        for origin in ["null", "https://evil.example"]:
            response = self.client.post(
                "/chat", data=form, headers={"Origin": origin}, follow_redirects=False
            )
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.store.all("SELECT * FROM tasks"), [])
        response = self.client.post(
            "/chat",
            data=form,
            headers={"Origin": "http://127.0.0.1"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        asyncio.run(process_one(self.store))
        self.assertIn("演示模式", self.client.get(response.headers["location"]).text)
        self.assertEqual(
            self.client.post(
                "/chat",
                data={"_csrf": "wrong", "content": "bad"},
                headers={"Origin": "http://127.0.0.1"},
            ).status_code,
            403,
        )

    def test_demo_chat_and_data_reopen(self):
        cid = self.store.conversation()["id"]
        task = self.store.submit(cid, "/note 测试笔记")
        with self.assertRaises(ValueError):
            self.store.submit(cid, "重复任务")
        asyncio.run(process_one(self.store))
        reopened = Store(self.tmp.name)
        self.assertEqual(
            reopened.one("SELECT status FROM tasks WHERE id=?", (task["id"],))[
                "status"
            ],
            "succeeded",
        )
        self.assertEqual(len(reopened.all("SELECT * FROM messages")), 2)
        self.assertEqual(len(self.files.list()), 1)
        self.assertIn("测试笔记", self.files.read(self.files.list()[0]))

    def test_reminders_cancel_and_once_only(self):
        due = self.store.task("提醒", "提醒", kind="reminder")
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.store.task("未来", "未来", kind="reminder", due_at=future)
        cancelled = self.store.task("取消", "取消")
        self.client.post(f"/api/tasks/{cancelled['id']}/cancel", headers=self.headers)
        self.assertTrue(asyncio.run(process_one(self.store)))
        self.assertFalse(asyncio.run(process_one(self.store)))
        self.assertEqual(
            self.store.one("SELECT status FROM tasks WHERE id=?", (due["id"],))[
                "status"
            ],
            "succeeded",
        )

    def test_worker_exclusive_lease_and_recovery(self):
        task = self.store.task("中断", "中断")
        self.store.execute(
            "UPDATE tasks SET status='running' WHERE id=?", (task["id"],)
        )
        acquire(self.store)
        self.assertEqual(
            self.store.one("SELECT status FROM tasks WHERE id=?", (task["id"],))[
                "status"
            ],
            "failed",
        )
        with self.assertRaises(RuntimeError):
            acquire(self.store)

    def test_model_tool_loop_and_usage(self):
        self.store.set_setting("baseUrl", "https://model.example/v1")
        self.store.set_setting("model", "mock")
        count = 0

        def mock(request):
            nonlocal count
            import json

            body = json.loads(request.content)
            count += 1
            if count == 1:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "a",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps(
                                    {"name": "模型.md", "content": "mock result"}
                                ),
                            },
                        }
                    ],
                }
            else:
                self.assertEqual(body["messages"][-1]["role"], "tool")
                message = {"role": "assistant", "content": "文件已创建。"}
            return httpx.Response(
                200,
                json={"choices": [{"message": message}], "usage": {"total_tokens": 12}},
            )

        async def runner(store, task):
            return await run_agent(store, task, httpx.MockTransport(mock))

        task = self.store.task("模型", "创建文件")
        asyncio.run(process_one(self.store, runner))
        self.assertEqual(count, 2)
        self.assertEqual(self.files.read("模型.md"), "mock result")
        self.assertEqual(
            self.store.one("SELECT result FROM tasks WHERE id=?", (task["id"],))[
                "result"
            ],
            "文件已创建。",
        )

    def test_running_cancellation_and_timeout(self):
        async def scenario():
            started = asyncio.Event()

            async def runner(store, task):
                started.set()
                await asyncio.sleep(10)
                raise AssertionError("cancelled task must never finish")

            task = self.store.task("取消", "取消")
            work = asyncio.create_task(process_one(self.store, runner))
            await started.wait()
            self.store.execute(
                "UPDATE tasks SET status='cancelled' WHERE id=?", (task["id"],)
            )
            await work
            self.assertIsNone(
                self.store.one("SELECT result FROM tasks WHERE id=?", (task["id"],))[
                    "result"
                ]
            )
            timed = self.store.task("超时", "超时")
            await process_one(self.store, runner, timeout=0.01)
            self.assertEqual(
                self.store.one("SELECT status FROM tasks WHERE id=?", (timed["id"],))[
                    "status"
                ],
                "failed",
            )

        asyncio.run(scenario())

    def test_all_pages_and_form_flow(self):
        home = self.client.get("/")
        csrf = re.search(r'name="_csrf" value="([^"]+)"', home.text)[1]
        response = self.client.post("/chat", data={"_csrf": csrf, "content": "/files"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('http-equiv="refresh"', response.text)
        asyncio.run(process_one(self.store))
        response = self.client.get(str(response.url))
        self.assertNotIn('http-equiv="refresh"', response.text)
        tid = self.store.one("SELECT id FROM tasks")["id"]
        for page in ["/tasks", "/tasks/" + tid, "/files", "/memory", "/settings"]:
            response = self.client.get(page)
            self.assertEqual(response.status_code, 200, page)
            self.assertNotIn("<script", response.text)

    def test_url_validation(self):
        self.assertEqual(
            validate_url("https://example.com/v1/"), "https://example.com/v1"
        )
        for url in [
            "http://remote.example",
            "https://secret@example.com",
            "file:///etc/passwd",
            "https://example.com/?secret=x",
        ]:
            with self.assertRaises(ValueError):
                validate_url(url)


if __name__ == "__main__":
    unittest.main()
