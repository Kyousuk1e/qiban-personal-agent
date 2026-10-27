"""FastAPI API and Jinja pages. No client-side JavaScript is required."""

import asyncio
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .agent import validate_url
from .extract import MAX_UPLOAD
from .files import Files
from .google import SERVICES, Google
from .storage import Store, future, now, text, timestamp
from .uploads import Uploads
from .worker import acquire, serve

PROJECT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT / ".env", encoding="utf-8-sig")


def create_app(store=None, start_worker=True):
    store = store or Store()
    files = Files(store.root)
    uploads = Uploads(store)
    first_bot = store.create_bot(first=True)
    google = Google(store)
    csrf = secrets.token_urlsafe(32)
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    templates.env.filters["localtime"] = lambda value: (
        timestamp(value)
        .astimezone(timezone(timedelta(hours=8)))
        .strftime("%m月%d日 %H:%M")
        if value
        else ""
    )

    @asynccontextmanager
    async def lifespan(app):
        worker = None
        if start_worker:
            acquire(store)
            worker = asyncio.create_task(serve(store))
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker

    app = FastAPI(
        title="栖伴 Python API",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store

    def state():
        cfg = store.config()
        beat = store.setting("workerHeartbeat")
        try:
            online = (
                bool(beat)
                and (datetime.now(timezone.utc) - timestamp(beat)).total_seconds() < 15
            )
        except ValueError:
            online = False
        return {
            "conversations": store.all(
                "SELECT c.*, b.parent_id, cb.bot_id FROM conversations c LEFT JOIN conversation_branches b ON b.conversation_id=c.id LEFT JOIN conversation_bots cb ON cb.conversation_id=c.id ORDER BY c.created_at DESC"
            ),
            "memories": store.all("SELECT * FROM memories ORDER BY updated_at DESC"),
            "tasks": store.all(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT 200"
            ),
            "files": files.list(),
            "uploads": uploads.all(),
            "workerOnline": online,
            "config": {
                "baseUrl": cfg["baseUrl"],
                "model": cfg["model"],
                "hasKey": bool(cfg["apiKey"]),
                "mode": "live" if cfg["baseUrl"] and cfg["model"] else "demo",
            },
        }

    @app.middleware("http")
    async def security(request, call_next):
        if urlsplit("//" + request.headers.get("host", "")).hostname not in {
            "127.0.0.1",
            "localhost",
        }:
            return JSONResponse({"error": "仅允许本机访问。"}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.hostname not in {
                "localhost",
                "127.0.0.1",
            } or parsed.scheme not in {"http", "https"}:
                return JSONResponse({"error": "来源不允许。"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                limit = (
                    MAX_UPLOAD + 65536
                    if request.url.path == "/files/upload"
                    else 300000
                )
                if len(body) > limit:
                    return JSONResponse(
                        {
                            "error": "上传请求超过 20 MB。"
                            if request.url.path == "/files/upload"
                            else "请求超过 300 KB。"
                        },
                        status_code=413,
                    )
            request._body = bytes(body)
            if request.url.path.startswith("/api/"):
                valid = request.headers.get("x-agent-client") == "local-ui"
            else:
                try:
                    form = await request.form()
                    valid = secrets.compare_digest(
                        str(form.get("_csrf", "")), csrf
                    ) and secrets.compare_digest(
                        request.cookies.get("qiban_csrf", ""), csrf
                    )
                    await form.close()
                except Exception:
                    valid = False
            if not valid:
                return JSONResponse(
                    {"error": "请求校验失败，请刷新页面再试。"}, status_code=403
                )
        response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                # no-referrer makes browser form POSTs use Origin: null, which
                # our origin guard correctly rejects. Preserve same-origin
                # metadata without sending referrers to external sites.
                "Referrer-Policy": "same-origin",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    def render(request, page="chat", status_code=200, **extra):
        data = state()
        roster = store.all("SELECT * FROM bots ORDER BY created_at")
        current_bot = extra.get("current_bot") or (
            store.bot_for(extra.get("active")) if extra.get("active") else None
        )
        query = request.query_params.get("q", "").strip()[:100]
        if current_bot and not query:
            data["conversations"] = [
                c for c in data["conversations"] if c.get("bot_id") == current_bot["id"]
            ]
        if query:
            data["conversations"] = [
                c
                for c in data["conversations"]
                if query.casefold() in c["title"].casefold()
            ]
        context = {
            "roster": roster,
            "preview": None,
            "current_bot": current_bot,
            "query": query,
            "apps": [],
            "google_ready": False,
            "google_redirect": "",
            "app_filter": "all",
            "app_query": "",
            "notice": None,
            "request": request,
            "page": page,
            "state": data,
            "csrf": csrf,
            "messages": [],
            "active": None,
            "task": None,
            "events": [],
            "pending": None,
            "error": None,
            "uploaded": None,
            "conversation": None,
            "context_files": [],
            "parent": None,
            "last_task": None,
            "labels": {
                "queued": "待执行",
                "running": "执行中",
                "succeeded": "已完成",
                "failed": "未完成",
                "cancelled": "已取消",
            },
            **extra,
        }
        response = templates.TemplateResponse(
            request=request, name="app.html", context=context, status_code=status_code
        )
        response.set_cookie("qiban_csrf", csrf, httponly=True, samesite="strict")
        return response

    @app.exception_handler(ValueError)
    @app.exception_handler(OSError)
    async def invalid(request, error):
        message = (
            "同名文件已存在，请换一个名称。"
            if isinstance(error, FileExistsError)
            else "文件不存在。"
            if isinstance(error, FileNotFoundError)
            else str(error)
        )
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": message}, status_code=400)
        return render(
            request,
            "files" if request.url.path.startswith("/files") else "chat",
            error=message,
            status_code=400,
        )

    def save_settings(body):
        base, model = body.get("baseUrl", "").strip(), body.get("model", "").strip()
        if bool(base) != bool(model) or len(base) > 500 or len(model) > 120:
            raise ValueError("请同时填写接口地址与模型名，或同时清空进入演示模式。")
        if base:
            validate_url(base)
        store.set_setting("baseUrl", base)
        store.set_setting("model", model)

    def cancel(task_id):
        store.execute(
            "UPDATE tasks SET status='cancelled',updated_at=? WHERE id=? AND status IN ('queued','running')",
            (now(), task_id),
        )

    @app.get("/api/state")
    def api_state():
        return state()

    @app.post("/api/conversations", status_code=201)
    def api_conversation():
        return store.conversation()

    @app.get("/api/conversations/{cid}/messages")
    def api_messages(cid: str):
        return store.all(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid", (cid,)
        )

    @app.post("/api/conversations/{cid}/messages", status_code=202)
    async def api_send(cid: str, request: Request):
        return store.submit(cid, (await request.json()).get("content"))

    @app.post("/api/memories", status_code=201)
    async def api_memory(request: Request):
        return store.memory((await request.json()).get("content"))

    @app.patch("/api/memories/{mid}")
    async def api_edit_memory(mid: str, request: Request):
        changed = store.execute(
            "UPDATE memories SET content=?,updated_at=? WHERE id=?",
            (text((await request.json()).get("content"), 1000), now(), mid),
        )
        return JSONResponse({"ok": bool(changed)}, status_code=200 if changed else 404)

    @app.delete("/api/memories/{mid}")
    def api_delete_memory(mid: str):
        store.execute("DELETE FROM memories WHERE id=?", (mid,))
        return {"ok": True}

    @app.post("/api/tasks", status_code=201)
    async def api_reminder(request: Request):
        body = await request.json()
        title = text(body.get("title"), 100)
        return store.task(
            title, title, kind="reminder", due_at=future(body.get("dueAt"))
        )

    @app.get("/api/tasks/{tid}/events")
    def api_events(tid: str):
        return store.all("SELECT * FROM events WHERE task_id=? ORDER BY id", (tid,))

    @app.post("/api/tasks/{tid}/cancel")
    def api_cancel(tid: str):
        cancel(tid)
        return {"ok": True}

    @app.post("/api/tasks/{tid}/read")
    def api_read(tid: str):
        store.execute("UPDATE tasks SET read=1 WHERE id=?", (tid,))
        return {"ok": True}

    @app.put("/api/settings")
    async def api_settings(request: Request):
        save_settings(await request.json())
        return {"ok": True}

    @app.get("/api/files")
    def api_files():
        return files.list()

    @app.post("/api/files", status_code=201)
    async def api_write(request: Request):
        body = await request.json()
        return files.write(body.get("name"), body.get("content"))

    @app.get("/api/files/{name}")
    def api_file(name: str):
        return PlainTextResponse(files.read(name))

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return chat(first_bot["conversation_id"], request)

    @app.get("/bots/{bid}/apps")
    def apps_page(bid: str, request: Request):
        bot = store.one("SELECT * FROM bots WHERE id=?", (bid,))
        if not bot:
            raise ValueError("队友不存在。")
        connected = {row["service"]: row for row in google.connections(bid)}
        category = request.query_params.get("category", "all")
        query = request.query_params.get("search", "").strip()[:100]
        apps = [
            {"id": key, **value, "connection": connected.get(key)}
            for key, value in SERVICES.items()
        ]
        apps = [
            item
            for item in apps
            if (category != "connected" or item["connection"])
            and (category not in ("通信", "生产力") or item["category"] == category)
            and query.casefold() in (item["name"] + item["description"]).casefold()
        ]
        notices = {
            "connected": "Google 应用已连接到当前队友。",
            "cancelled": "已取消授权，未新增连接。",
            "disconnected": "已移除当前队友的本地授权。",
            "configured": "OAuth 应用配置已保存，可以开始连接。",
            "failed": "Google 授权未完成，请检查配置、网络及所选权限后重试。",
        }
        return render(
            request,
            "apps",
            current_bot=bot,
            apps=apps,
            google_ready=google.configured(),
            google_redirect=google.config()["redirect_uri"],
            app_filter=category,
            app_query=query,
            notice=notices.get(request.query_params.get("notice")),
        )

    @app.post("/bots/{bid}/apps/google/configure")
    async def google_configure(bid: str, request: Request):
        if not store.one("SELECT id FROM bots WHERE id=?", (bid,)):
            raise ValueError("队友不存在。")
        form = await request.form()
        google.save_config(
            str(form.get("client_id", "")), str(form.get("client_secret", ""))
        )
        return RedirectResponse(
            "/bots/" + bid + "/apps?notice=configured", status_code=303
        )

    @app.post("/bots/{bid}/apps/{service}/connect")
    def google_start(bid: str, service: str, request: Request):
        if request.url.hostname != "127.0.0.1" or request.url.port != 4317:
            raise ValueError(
                "请使用 http://127.0.0.1:4317 打开助手后开始 Google 授权。"
            )
        binding = request.cookies.get("qiban_google_oauth") or secrets.token_urlsafe(32)
        response = RedirectResponse(
            google.start(bid, service, binding), status_code=303
        )
        response.set_cookie(
            "qiban_google_oauth",
            binding,
            httponly=True,
            samesite="lax",
            max_age=600,
            path="/",
        )
        return response

    @app.get("/oauth/google/callback")
    async def google_callback(request: Request):
        row = google.consume(
            request.query_params.get("state", ""),
            request.cookies.get("qiban_google_oauth", ""),
        )
        notice = "cancelled" if request.query_params.get("error") else "connected"
        if notice == "connected":
            try:
                code = request.query_params.get("code", "")
                if not code:
                    raise ValueError("缺少授权码。")
                await google.exchange(row, code)
            except ValueError:
                notice = "failed"
        return RedirectResponse(
            "/bots/" + row["bot_id"] + "/apps?notice=" + notice, status_code=303
        )

    @app.post("/bots/{bid}/apps/{service}/disconnect")
    def google_disconnect(bid: str, service: str):
        google.disconnect(bid, service)
        return RedirectResponse(
            "/bots/" + bid + "/apps?notice=disconnected", status_code=303
        )

    @app.post("/bots/new")
    def new_bot():
        bot = store.create_bot()
        return RedirectResponse("/bots/" + bot["id"], status_code=303)

    @app.get("/bots/{bid}")
    def bot_page(bid: str, request: Request):
        bot = store.one("SELECT * FROM bots WHERE id=?", (bid,))
        if not bot:
            raise ValueError("队友不存在。")
        return chat(bot["conversation_id"], request)

    @app.post("/bots/{bid}/configure")
    async def configure_bot(bid: str, request: Request):
        form = await request.form()
        bot = store.configure_bot(
            bid, form.get("prompt", ""), form.get("choice") or form.get("name", "")
        )
        return RedirectResponse("/bots/" + bot["id"], status_code=303)

    @app.get("/chat/{cid}", response_class=HTMLResponse)
    def chat(cid: str, request: Request):
        pending = store.one(
            "SELECT * FROM tasks WHERE conversation_id=? AND status IN ('queued','running')",
            (cid,),
        )
        conversation = store.one("SELECT * FROM conversations WHERE id=?", (cid,))
        if not conversation:
            raise ValueError("对话不存在。")
        return render(
            request,
            active=cid,
            conversation=conversation,
            last_task=store.one(
                "SELECT * FROM tasks WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1",
                (cid,),
            ),
            context_files=store.context_files(cid),
            parent=store.one(
                "SELECT parent_id FROM conversation_branches WHERE conversation_id=?",
                (cid,),
            ),
            messages=api_messages(cid),
            pending=pending,
        )

    @app.post("/conversations/new")
    def new_conversation():
        return RedirectResponse("/chat/" + store.conversation()["id"], status_code=303)

    @app.post("/chat/{cid}/fork")
    def fork_conversation(cid: str):
        return RedirectResponse("/chat/" + store.fork(cid), status_code=303)

    @app.post("/chat/{cid}/context")
    async def attach_context(cid: str, request: Request):
        form = await request.form()
        if form.get("remove"):
            store.execute(
                "DELETE FROM conversation_context WHERE conversation_id=? AND upload_id=?",
                (cid, form.get("upload_id")),
            )
        else:
            store.attach(cid, form.get("upload_id"))
        return RedirectResponse("/chat/" + quote(cid), status_code=303)

    @app.post("/chat/{cid}/rename")
    async def rename_conversation(cid: str, request: Request):
        store.execute(
            "UPDATE conversations SET title=? WHERE id=?",
            (text((await request.form()).get("title"), 100), cid),
        )
        return RedirectResponse("/chat/" + quote(cid), status_code=303)

    @app.post("/chat")
    async def send(request: Request):
        form = await request.form()
        content = text(form.get("content"))
        cid = form.get("conversation_id") or store.conversation()["id"]
        store.submit(cid, content)
        return RedirectResponse(f"/chat/{quote(cid)}", status_code=303)

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks(request: Request):
        return render(request, "tasks")

    @app.get("/tasks/{tid}", response_class=HTMLResponse)
    def detail(tid: str, request: Request):
        task = store.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if not task:
            raise ValueError("任务不存在。")
        return render(request, "task", task=task, events=api_events(tid))

    @app.post("/tasks")
    async def reminder(request: Request):
        form = await request.form()
        title = text(form.get("title"), 100)
        # The no-JavaScript form explicitly uses Beijing time, independent of browser timezone.
        due = future(str(form.get("dueAt", "")) + "+08:00")
        result = store.task(title, title, kind="reminder", due_at=due)
        return RedirectResponse("/tasks/" + result["id"], status_code=303)

    @app.post("/tasks/{tid}/cancel")
    def cancel_form(tid: str):
        cancel(tid)
        return RedirectResponse("/tasks/" + tid, status_code=303)

    @app.post("/tasks/{tid}/read")
    def read_form(tid: str):
        api_read(tid)
        return RedirectResponse("/tasks/" + tid, status_code=303)

    @app.get("/memory", response_class=HTMLResponse)
    def memories(request: Request):
        return render(request, "memory")

    @app.post("/memory")
    async def memory_form(request: Request):
        store.memory((await request.form()).get("content"))
        return RedirectResponse("/memory", status_code=303)

    @app.post("/memory/{mid}/edit")
    async def edit_form(mid: str, request: Request):
        form = await request.form()
        store.execute(
            "UPDATE memories SET content=?,updated_at=? WHERE id=?",
            (text(form.get("content"), 1000), now(), mid),
        )
        return RedirectResponse("/memory", status_code=303)

    @app.post("/memory/{mid}/delete")
    def delete_form(mid: str):
        api_delete_memory(mid)
        return RedirectResponse("/memory", status_code=303)

    @app.get("/files", response_class=HTMLResponse)
    def files_page(request: Request):
        return render(
            request,
            "files",
            uploaded=uploads.get(request.query_params.get("uploaded", "")),
        )

    @app.post("/files/upload")
    async def upload_form(request: Request):
        async with request.form(max_files=1, max_fields=5) as form:
            uploaded = form.get("file")
            if not isinstance(uploaded, UploadFile) or not uploaded.filename:
                raise ValueError("请先选择一个文件。")
            cid = form.get("conversation_id", "")
            if cid and not store.one("SELECT id FROM conversations WHERE id=?", (cid,)):
                raise ValueError("对话不存在。")
            if cid and len(store.context_files(cid)) >= 20:
                raise ValueError("每个对话最多关联 20 份资料。")
            content = await uploaded.read(MAX_UPLOAD + 1)
            result = await run_in_threadpool(
                uploads.import_file, uploaded.filename, content
            )
            if form.get("start_chat"):
                cid = store.conversation()["id"]
            if cid:
                store.attach(cid, result["id"])
                return RedirectResponse("/chat/" + quote(cid), status_code=303)
        return RedirectResponse("/files?uploaded=" + result["id"], status_code=303)

    @app.get("/files/original/{uid}")
    def original_file(uid: str):
        item = uploads.get(uid)
        if not item:
            raise ValueError("原文件不存在。")
        return FileResponse(
            uploads.root / item["stored_name"],
            filename=item["name"],
            media_type="application/octet-stream",
            content_disposition_type="attachment",
        )

    @app.post("/files/summarize/{uid}")
    def summarize_upload(uid: str):
        item = uploads.get(uid)
        if not item or item["status"] != "ready":
            raise ValueError("这个文件尚无可读文字，不能生成内容总结。")
        cid = store.conversation()["id"]
        store.attach(cid, uid)
        store.submit(
            cid,
            f"请读取工作目录中的 {item['text_name']}（原文件名：{item['name']}），总结其主要内容和可执行事项。必要时使用 offset 分段读取。请说明提取限制，不推测未读到的图片或缺失页面。",
        )
        return RedirectResponse("/chat/" + cid, status_code=303)

    @app.post("/files")
    async def file_form(request: Request):
        form = await request.form()
        files.write(form.get("name"), form.get("content", ""))
        return RedirectResponse("/files", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return render(request, "settings")

    @app.post("/settings")
    async def settings_form(request: Request):
        save_settings(await request.form())
        return RedirectResponse("/settings", status_code=303)

    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )
    return app
