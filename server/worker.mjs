import { db, now, event, addMessage, setSetting } from './db.mjs';
import { runAgent } from './agent.mjs';
import { fileURLToPath } from 'node:url';
export async function processOne() {
  const task = db
    .prepare(
      "UPDATE tasks SET status='running',updated_at=? WHERE id=(SELECT id FROM tasks WHERE status='queued' AND due_at<=? ORDER BY due_at LIMIT 1) RETURNING *",
    )
    .get(now(), now());
  if (!task) return false;
  event(task.id, '开始执行', task.kind === 'reminder' ? '一次性提醒' : '个人助手任务');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error('任务超过 120 秒限制。')), 120000);
  const check = setInterval(() => {
    if (db.prepare('SELECT status FROM tasks WHERE id=?').get(task.id)?.status === 'cancelled')
      controller.abort(new Error('任务已取消。'));
  }, 300);
  try {
    const result =
      task.kind === 'reminder'
        ? `提醒：${task.prompt}${Date.now() - Date.parse(task.due_at) > 60000 ? '\n（服务恢复后补发的提醒）' : ''}`
        : await runAgent(task, controller.signal);
    db.exec('BEGIN IMMEDIATE');
    try {
      const changed = db
        .prepare(
          "UPDATE tasks SET status='succeeded',result=?,updated_at=? WHERE id=? AND status='running'",
        )
        .run(result, now(), task.id).changes;
      if (changed) {
        if (task.conversation_id) addMessage(task.conversation_id, 'assistant', result);
        event(task.id, '任务完成');
      }
      db.exec('COMMIT');
    } catch (error) {
      db.exec('ROLLBACK');
      throw error;
    }
  } catch (error) {
    const message = controller.signal.aborted ? controller.signal.reason.message : error.message;
    db.exec('BEGIN IMMEDIATE');
    try {
      const changed = db
        .prepare(
          "UPDATE tasks SET status='failed',error=?,updated_at=? WHERE id=? AND status='running'",
        )
        .run(message, now(), task.id).changes;
      if (changed) {
        event(task.id, '执行失败', message);
        if (task.conversation_id)
          addMessage(task.conversation_id, 'assistant', `任务未完成：${message}`);
      }
      db.exec('COMMIT');
    } catch (err) {
      db.exec('ROLLBACK');
      throw err;
    }
  } finally {
    clearTimeout(timeout);
    clearInterval(check);
  }
  return true;
}
export function recoverInterrupted() {
  const stale = db.prepare("SELECT * FROM tasks WHERE status='running'").all();
  for (const task of stale) {
    db.prepare(
      "UPDATE tasks SET status='failed',error=?,updated_at=? WHERE id=? AND status='running'",
    ).run(
      '执行器中断，可能已有部分操作完成。请查看执行记录后重新发起，系统不会自动重复执行。',
      now(),
      task.id,
    );
    event(task.id, '中断恢复', '任务标记失败，防止重复产生外部效果。');
  }
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  db.exec('BEGIN IMMEDIATE');
  const heartbeat = db
    .prepare("SELECT value FROM settings WHERE key='workerHeartbeat'")
    .get()?.value;
  if (heartbeat && Date.now() - Date.parse(heartbeat) < 15000) {
    db.exec('ROLLBACK');
    console.error('已有 Worker 在线或刚刚中断，请等待 15 秒后重启。');
    process.exit(1);
  }
  recoverInterrupted();
  setSetting('workerHeartbeat', now());
  db.exec('COMMIT');
  const timer = setInterval(() => {
    setSetting('workerHeartbeat', now());
    db.prepare("UPDATE tasks SET updated_at=? WHERE status='running'").run(now());
  }, 3000);
  const stop = () => {
    clearInterval(timer);
    setSetting('workerHeartbeat', '');
    process.exit();
  };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
  console.log('栖伴 Worker 已启动，提醒与任务将在后台执行。');
  while (true) {
    try {
      await processOne();
    } catch (error) {
      console.error('Worker error:', error.message);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}
