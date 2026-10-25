"""SQLite persistence. Schema and UTC timestamps remain compatible with v0.1."""

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("时间必须包含时区。")
    return result


def future(value):
    try:
        result = timestamp(value)
        if result <= datetime.now(timezone.utc):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("请选择将来的明确时间。") from None
    return (
        result.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def text(value, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("内容为空或超过长度限制。")
    return value.strip()


class Store:
    def __init__(self, root=None):
        self.root = Path(
            root or os.getenv("AGENT_HOME") or Path(__file__).resolve().parent.parent
        ).resolve()
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        self.path = self.root / "data" / "agent.sqlite"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, content TEXT NOT NULL, source TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, due_at TEXT NOT NULL, conversation_id TEXT, result TEXT, error TEXT, read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, label TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conversation_context (conversation_id TEXT NOT NULL, upload_id TEXT NOT NULL, PRIMARY KEY(conversation_id,upload_id));
                CREATE TABLE IF NOT EXISTS conversation_branches (conversation_id TEXT PRIMARY KEY, parent_id TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS tasks_schedule ON tasks(status, due_at);
            """)

    @contextmanager
    def connect(self, transaction=False):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if transaction:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if transaction:
                db.commit()
        except BaseException:
            if transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def all(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.connect() as db:
            return db.execute(sql, args).rowcount

    def setting(self, key, default=""):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def config(self):
        return {
            "baseUrl": self.setting("baseUrl", os.getenv("MODEL_BASE_URL", "")),
            "model": self.setting("model", os.getenv("MODEL_NAME", "")),
            "apiKey": os.getenv("MODEL_API_KEY", ""),
        }

    def event(self, task_id, label, detail=""):
        self.execute(
            "INSERT INTO events(task_id,label,detail,created_at) VALUES (?,?,?,?)",
            (task_id, label, detail, now()),
        )

    def task(self, title, prompt, kind="agent", due_at=None, conversation_id=None):
        task_id, time = str(uuid.uuid4()), now()
        self.execute(
            "INSERT INTO tasks(id,title,prompt,kind,status,due_at,conversation_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                title[:100],
                prompt,
                kind,
                "queued",
                due_at or time,
                conversation_id,
                time,
                time,
            ),
        )
        return self.one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def conversation(self):
        row = {"id": str(uuid.uuid4()), "title": "新的对话", "created_at": now()}
        self.execute("INSERT INTO conversations VALUES (:id,:title,:created_at)", row)
        return row

    def submit(self, conversation_id, content):
        content = text(content)
        task_id, time = str(uuid.uuid4()), now()
        with self.connect(transaction=True) as db:
            if not db.execute(
                "SELECT id FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone():
                raise ValueError("对话不存在。")
            if db.execute(
                "SELECT id FROM tasks WHERE conversation_id=? AND status IN ('queued','running')",
                (conversation_id,),
            ).fetchone():
                raise ValueError("当前对话仍有任务在处理，请稍候或取消。")
            if not db.execute(
                "SELECT id FROM messages WHERE conversation_id=?", (conversation_id,)
            ).fetchone():
                db.execute(
                    "UPDATE conversations SET title=? WHERE id=?",
                    (content[:24], conversation_id),
                )
            db.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), conversation_id, "user", content, time),
            )
            db.execute(
                "INSERT INTO tasks(id,title,prompt,kind,status,due_at,conversation_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    content[:60],
                    content,
                    "agent",
                    "queued",
                    time,
                    conversation_id,
                    time,
                    time,
                ),
            )
        return self.one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def memory(self, content):
        row = {
            "id": str(uuid.uuid4()),
            "content": text(content, 1000),
            "source": "用户确认",
            "updated_at": now(),
        }
        with self.connect(transaction=True) as db:
            if db.execute("SELECT count(*) FROM memories").fetchone()[0] >= 100:
                raise ValueError("最多保存 100 条记忆，请先整理。")
            db.execute(
                "INSERT INTO memories VALUES (:id,:content,:source,:updated_at)", row
            )
        return row

    def context_files(self, cid):
        if not self.one("SELECT name FROM sqlite_master WHERE name='uploads'"):
            return []
        return self.all(
            "SELECT u.* FROM uploads u JOIN conversation_context c ON c.upload_id=u.id WHERE c.conversation_id=? ORDER BY u.created_at",
            (cid,),
        )

    def attach(self, cid, uid):
        if not self.one("SELECT id FROM conversations WHERE id=?", (cid,)):
            raise ValueError("对话不存在。")
        if not self.one("SELECT id FROM uploads WHERE id=?", (uid,)):
            raise ValueError("资料不存在。")
        if len(self.context_files(cid)) >= 20:
            raise ValueError("每个对话最多关联 20 份资料。")
        self.execute(
            "INSERT OR IGNORE INTO conversation_context VALUES (?,?)", (cid, uid)
        )

    def fork(self, cid):
        with self.connect(transaction=True) as db:
            original = db.execute(
                "SELECT * FROM conversations WHERE id=?", (cid,)
            ).fetchone()
            if not original:
                raise ValueError("对话不存在。")
            if db.execute(
                "SELECT id FROM tasks WHERE conversation_id=? AND status IN ('queued','running')",
                (cid,),
            ).fetchone():
                raise ValueError("请等待当前回答结束后再创建分支。")
            new_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO conversations VALUES (?,?,?)",
                (new_id, original["title"] + " · 分支", now()),
            )
            db.execute("INSERT INTO conversation_branches VALUES (?,?)", (new_id, cid))
            for row in db.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid", (cid,)
            ).fetchall():
                db.execute(
                    "INSERT INTO messages VALUES (?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        new_id,
                        row["role"],
                        row["content"],
                        row["created_at"],
                    ),
                )
            db.execute(
                "INSERT INTO conversation_context SELECT ?,upload_id FROM conversation_context WHERE conversation_id=?",
                (new_id, cid),
            )
        return new_id
