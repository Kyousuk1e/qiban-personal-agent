import asyncio
import base64
import json
import re
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient

from qiban.agent import run_agent
from qiban.google import SERVICES, Google
from qiban.storage import Store
from qiban.web import create_app


class GoogleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.bot = self.store.create_bot(first=True)
        self.google = Google(self.store)
        self.google.save_config("test.apps.googleusercontent.com", "client-secret-test")
        self.client = TestClient(
            create_app(self.store, start_worker=False), base_url="http://127.0.0.1:4317"
        )
        self.csrf = re.search(
            r'name="_csrf" value="([^"]+)"', self.client.get("/").text
        )[1]
        self.path = "/bots/" + self.bot["id"] + "/apps"

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def begin(self, service="gmail"):
        url = self.google.start(self.bot["id"], service, "browser-binding")
        params = parse_qs(urlsplit(url).query)
        return params, self.google.consume(params["state"][0], "browser-binding")

    def transport(self, service="gmail", missing_scope=False):
        def respond(request):
            if request.url.host == "oauth2.googleapis.com":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "ACCESS-PRIVATE",
                        "refresh_token": "REFRESH-PRIVATE",
                        "expires_in": 3600,
                        "scope": "" if missing_scope else SERVICES[service]["scope"],
                    },
                )
            if request.url.path.endswith("userinfo"):
                return httpx.Response(200, json={"email": "demo@example.com"})
            if request.url.path.endswith("/messages/abc"):
                return httpx.Response(
                    200,
                    json={
                        "payload": {
                            "mimeType": "text/plain",
                            "headers": [{"name": "Subject", "value": "Test"}],
                            "body": {
                                "data": base64.urlsafe_b64encode(b"Email body").decode()
                            },
                        }
                    },
                )
            return httpx.Response(200, json={"messages": [{"id": "abc"}]})

        return httpx.MockTransport(respond)

    def connect(self):
        _, row = self.begin()
        self.google.transport = self.transport()
        asyncio.run(self.google.exchange(row, "code"))

    def test_state_pkce_binding_expiry_and_replay(self):
        url = self.google.start(self.bot["id"], "gmail", "binding")
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertIn(SERVICES["gmail"]["scope"], params["scope"][0])
        self.assertNotIn(SERVICES["drive"]["scope"], params["scope"][0])
        state = params["state"][0]
        with self.assertRaises(ValueError):
            self.google.consume(state, "wrong-browser")
        self.google.consume(state, "binding")
        with self.assertRaises(ValueError):
            self.google.consume(state, "binding")
        state = parse_qs(
            urlsplit(self.google.start(self.bot["id"], "gmail", "binding")).query
        )["state"][0]
        self.store.execute(
            "UPDATE google_oauth_states SET expires=0 WHERE state=?", (state,)
        )
        with self.assertRaises(ValueError):
            self.google.consume(state, "binding")

    def test_encryption_bot_isolation_read_and_disconnect(self):
        self.connect()
        row = self.store.one("SELECT * FROM google_connections")
        self.assertNotIn("REFRESH-PRIVATE", row["secret"])
        self.assertNotIn(
            "client-secret-test",
            (self.store.root / "data/google-client.enc").read_text(),
        )
        page = self.client.get(self.path)
        self.assertIn("demo@example.com", page.text)
        self.assertNotIn("ACCESS-PRIVATE", page.text)
        result = asyncio.run(
            self.google.execute(
                self.bot["id"], "google_read_mail", {"message_id": "abc"}
            )
        )
        self.assertEqual(result["text"], "Email body")
        other = self.store.create_bot()
        with self.assertRaises(ValueError):
            asyncio.run(
                self.google.execute(
                    other["id"], "google_search_mail", {"query": "test"}
                )
            )
        self.google.disconnect(self.bot["id"], "gmail")
        with self.assertRaises(ValueError):
            asyncio.run(self.google.access(self.bot["id"], "gmail"))

    def test_missing_scope_and_cancelled_inflight_authorization(self):
        _, row = self.begin()
        self.google.transport = self.transport(missing_scope=True)
        with self.assertRaises(ValueError):
            asyncio.run(self.google.exchange(row, "code"))
        self.assertEqual(self.google.connections(self.bot["id"]), [])
        _, row = self.begin()
        self.google.transport = self.transport()
        self.google.disconnect(self.bot["id"], "gmail")
        with self.assertRaises(ValueError):
            asyncio.run(self.google.exchange(row, "code"))
        self.assertEqual(self.google.connections(self.bot["id"]), [])

    def test_callback_csrf_cookie_and_success(self):
        self.assertEqual(
            self.client.post(self.path + "/gmail/connect").status_code, 403
        )
        result = self.client.post(
            self.path + "/gmail/connect",
            data={"_csrf": self.csrf},
            follow_redirects=False,
        )
        self.assertEqual(result.status_code, 303)
        self.assertIn("SameSite=lax", result.headers["set-cookie"])
        state = parse_qs(urlsplit(result.headers["location"]).query)["state"][0]
        transport = self.transport()

        def client(_self):
            return httpx.AsyncClient(transport=transport, trust_env=False)

        with patch.object(Google, "client", client):
            response = self.client.get(
                "/oauth/google/callback", params={"state": state, "code": "test"}
            )
        self.assertIn("Google 应用已连接", response.text)
        self.assertEqual(
            self.client.get(
                "/oauth/google/callback", params={"state": state, "code": "test"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(self.path + "/gmail/disconnect").status_code, 403
        )
        response = self.client.post(
            self.path + "/gmail/disconnect", data={"_csrf": self.csrf}
        )
        self.assertIn("已移除当前队友", response.text)

    def test_refresh_and_revoked_grant(self):
        self.connect()
        tokens = self.google.unseal(
            self.store.one("SELECT secret FROM google_connections")["secret"]
        )
        tokens["expires_at"] = time.time() - 60
        self.store.execute(
            "UPDATE google_connections SET secret=?", (self.google.seal(tokens),)
        )
        self.assertEqual(
            asyncio.run(self.google.access(self.bot["id"], "gmail")), "ACCESS-PRIVATE"
        )
        self.store.execute(
            "UPDATE google_connections SET secret=?", (self.google.seal(tokens),)
        )
        self.google.transport = httpx.MockTransport(
            lambda request: httpx.Response(400, json={"error": "invalid_grant"})
        )
        with self.assertRaises(ValueError):
            asyncio.run(self.google.access(self.bot["id"], "gmail"))
        self.assertEqual(self.google.connections(self.bot["id"])[0]["status"], "reauth")

    def test_agent_exposes_only_current_bot_connected_tools(self):
        self.connect()
        self.store.set_setting("baseUrl", "https://model.example/v1")
        self.store.set_setting("model", "test")
        captured = []

        def respond(request):
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            )

        other = self.store.create_bot()
        for bot in [self.bot, other]:
            task = self.store.submit(bot["conversation_id"], "你好")
            asyncio.run(
                run_agent(self.store, task, transport=httpx.MockTransport(respond))
            )
        names = [item["function"]["name"] for item in captured[0]["tools"]]
        self.assertIn("google_read_mail", names)
        self.assertNotIn("google_list_events", names)
        self.assertNotIn(
            "google_read_mail",
            [item["function"]["name"] for item in captured[1]["tools"]],
        )
        self.assertNotIn("REFRESH-PRIVATE", json.dumps(captured))
