"""Durable queue worker with exclusive lease, cancellation and atomic completion."""

import asyncio
import uuid
from contextlib import suppress
from datetime import datetime, timezone

from .agent import run_agent
from .routines import Routines, complete, run_briefing
from .storage import now, timestamp


def acquire(store):
    with store.connect(transaction=True) as db:
        previous = db.execute(
            "SELECT value FROM settings WHERE key='workerHeartbeat'"
        ).fetchone()
        if (
            previous
            and previous[0]
            and (datetime.now(timezone.utc) - timestamp(previous[0])).total_seconds()
            < 15
        ):
            raise RuntimeError("已有 Worker 在线，请停止旧服务或等待 15 秒后重启。")
        for row in db.execute("SELECT id FROM tasks WHERE status='running'").fetchall():
            db.execute(
                "UPDATE tasks SET status='failed',error=?,updated_at=? WHERE id=?",
                (
                    "执行器中断，可能已有部分操作完成。请检查记录后重新发起；系统不自动重放。",
                    now(),
                    row[0],
                ),
            )
            db.execute(
                "INSERT INTO events(task_id,label,detail,created_at) VALUES (?,?,?,?)",
                (row[0], "中断恢复", "任务标记失败，避免重复操作。", now()),
            )
        db.execute(
            "INSERT INTO settings VALUES ('workerHeartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (now(),),
        )


async def process_one(
    store, runner=run_agent, timeout=120, briefing_runner=run_briefing
):
    Routines(store)
    with store.connect(transaction=True) as db:
        row = db.execute(
            "UPDATE tasks SET status='running',updated_at=? WHERE id=(SELECT id FROM tasks WHERE status='queued' AND due_at<=? ORDER BY due_at LIMIT 1) RETURNING *",
            (now(), now()),
        ).fetchone()
    if row is None:
        return False
    task = dict(row)
    store.event(
        task["id"],
        "开始执行",
        "一次性提醒" if task["kind"] == "reminder" else "个人助手任务",
    )

    async def execute():
        if task["kind"] == "reminder":
            late = (
                datetime.now(timezone.utc) - timestamp(task["due_at"])
            ).total_seconds() > 60
            return f"提醒：{task['prompt']}" + (
                "\n（服务恢复后补发的提醒）" if late else ""
            )
        if task["kind"] == "briefing":
            return await briefing_runner(store, task)
        return await runner(store, task)

    work = asyncio.create_task(execute())

    async def monitor():
        while not work.done():
            if (
                store.one("SELECT status FROM tasks WHERE id=?", (task["id"],))[
                    "status"
                ]
                == "cancelled"
            ):
                work.cancel()
                return
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(monitor())
    result, error = None, None
    try:
        result = await asyncio.wait_for(work, timeout)
    except asyncio.CancelledError:
        work.cancel()
        # Cancellation from shutdown propagates; user cancellation preserves its state.
        if (
            store.one("SELECT status FROM tasks WHERE id=?", (task["id"],))["status"]
            != "cancelled"
        ):
            raise
        return True
    except TimeoutError:
        error = "任务超过执行时限，请缩小范围后重试。"
    except Exception as exc:
        error = (
            str(exc)
            if isinstance(exc, (ValueError, OSError))
            else "任务执行失败，请检查配置或运行日志。"
        )
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
    with store.connect(transaction=True) as db:
        changed = db.execute(
            "UPDATE tasks SET status=?,result=?,error=?,updated_at=? WHERE id=? AND status='running'",
            ("failed" if error else "succeeded", result, error, now(), task["id"]),
        ).rowcount
        if changed:
            complete(db, task["id"], not error)
            db.execute(
                "INSERT INTO events(task_id,label,detail,created_at) VALUES (?,?,?,?)",
                (task["id"], "执行失败" if error else "任务完成", error or "", now()),
            )
            if task["conversation_id"]:
                db.execute(
                    "INSERT INTO messages VALUES (?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        task["conversation_id"],
                        "assistant",
                        f"任务未完成：{error}" if error else result,
                        now(),
                    ),
                )
    return True


async def serve(store):
    async def heartbeat():
        while True:
            store.set_setting("workerHeartbeat", now())
            await asyncio.sleep(3)

    beat = asyncio.create_task(heartbeat())
    try:
        while True:
            Routines(store).tick()
            await process_one(store)
            await asyncio.sleep(0.5)
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
        store.set_setting("workerHeartbeat", "")
