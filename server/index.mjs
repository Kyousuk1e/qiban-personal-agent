import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { db, id, now, config, setSetting, addMessage, createTask } from './db.mjs';
import { listFiles, readFile, writeFile } from './files.mjs';
import { validateBaseUrl } from './agent.mjs';
export const app = express();
app.disable('x-powered-by');
app.use((req, res, next) => {
  const host = (req.headers.host || '').split(':')[0];
  if (!['127.0.0.1', 'localhost'].includes(host))
    return res.status(403).json({ error: '仅允许本机访问。' });
  const origin = req.headers.origin;
  if (origin) {
    try {
      const u = new URL(origin);
      if (
        !['127.0.0.1', 'localhost'].includes(u.hostname) ||
        !['http:', 'https:'].includes(u.protocol)
      )
        throw new Error();
    } catch {
      return res.status(403).json({ error: '来源不允许。' });
    }
  }
  if (!['GET', 'HEAD'].includes(req.method) && req.headers['x-agent-client'] !== 'local-ui')
    return res.status(403).json({ error: '缺少本地客户端标记。' });
  res.set('X-Content-Type-Options', 'nosniff');
  res.set('Referrer-Policy', 'no-referrer');
  res.set(
    'Content-Security-Policy',
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
  );
  res.set('Cache-Control', 'no-store');
  next();
});
app.use(express.json({ limit: '300kb' }));
const text = (value, max = 12000) => {
  if (typeof value !== 'string' || !value.trim() || value.length > max)
    throw new Error('内容为空或超过长度限制。');
  return value.trim();
};
app.get('/api/state', (req, res) => {
  const cfg = config();
  const heartbeat = db
    .prepare("SELECT value FROM settings WHERE key='workerHeartbeat'")
    .get()?.value;
  res.json({
    conversations: db.prepare('SELECT * FROM conversations ORDER BY created_at DESC').all(),
    memories: db.prepare('SELECT * FROM memories ORDER BY updated_at DESC').all(),
    tasks: db.prepare('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 200').all(),
    files: listFiles(),
    config: {
      baseUrl: cfg.baseUrl,
      model: cfg.model,
      hasKey: Boolean(cfg.apiKey),
      mode: cfg.baseUrl && cfg.model ? 'live' : 'demo',
    },
    workerOnline: Boolean(heartbeat && Date.now() - Date.parse(heartbeat) < 15000),
  });
});
app.post('/api/conversations', (req, res) => {
  const row = { id: id(), title: '新的对话', created_at: now() };
  db.prepare('INSERT INTO conversations VALUES (@id,@title,@created_at)').run(row);
  res.status(201).json(row);
});
app.get('/api/conversations/:id/messages', (req, res) =>
  res.json(
    db.prepare('SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid').all(req.params.id),
  ),
);
app.post('/api/conversations/:id/messages', (req, res) => {
  const content = text(req.body.content);
  if (!db.prepare('SELECT id FROM conversations WHERE id=?').get(req.params.id))
    return res.status(404).json({ error: '对话不存在。' });
  if (
    db
      .prepare("SELECT id FROM tasks WHERE conversation_id=? AND status IN ('queued','running')")
      .get(req.params.id)
  )
    return res.status(409).json({ error: '当前对话仍有任务在处理，请稍候或取消。' });
  if (!db.prepare('SELECT id FROM messages WHERE conversation_id=? LIMIT 1').get(req.params.id))
    db.prepare('UPDATE conversations SET title=? WHERE id=?').run(
      content.slice(0, 24),
      req.params.id,
    );
  addMessage(req.params.id, 'user', content);
  res
    .status(202)
    .json(
      createTask({ title: content.slice(0, 60), prompt: content, conversationId: req.params.id }),
    );
});
app.post('/api/memories', (req, res) => {
  const content = text(req.body.content, 1000);
  if (db.prepare('SELECT COUNT(*) AS n FROM memories').get().n >= 100)
    throw new Error('最多保存 100 条记忆，请先整理。');
  const row = { id: id(), content, source: '用户确认', updated_at: now() };
  db.prepare('INSERT INTO memories VALUES (@id,@content,@source,@updated_at)').run(row);
  res.status(201).json(row);
});
app.patch('/api/memories/:id', (req, res) => {
  const changes = db
    .prepare('UPDATE memories SET content=?,updated_at=? WHERE id=?')
    .run(text(req.body.content, 1000), now(), req.params.id).changes;
  res.status(changes ? 200 : 404).json({ ok: Boolean(changes) });
});
app.delete('/api/memories/:id', (req, res) => {
  db.prepare('DELETE FROM memories WHERE id=?').run(req.params.id);
  res.json({ ok: true });
});
app.post('/api/tasks', (req, res) => {
  const title = text(req.body.title, 100);
  const due = Date.parse(req.body.dueAt);
  if (!Number.isFinite(due) || due <= Date.now()) throw new Error('请选择将来的提醒时间。');
  res
    .status(201)
    .json(
      createTask({ title, prompt: title, kind: 'reminder', dueAt: new Date(due).toISOString() }),
    );
});
app.get('/api/tasks/:id/events', (req, res) =>
  res.json(db.prepare('SELECT * FROM events WHERE task_id=? ORDER BY id').all(req.params.id)),
);
app.post('/api/tasks/:id/cancel', (req, res) => {
  db.prepare(
    "UPDATE tasks SET status='cancelled',updated_at=? WHERE id=? AND status IN ('queued','running')",
  ).run(now(), req.params.id);
  res.json({ ok: true });
});
app.post('/api/tasks/:id/read', (req, res) => {
  db.prepare('UPDATE tasks SET read=1 WHERE id=?').run(req.params.id);
  res.json({ ok: true });
});
app.put('/api/settings', (req, res) => {
  const baseUrl = typeof req.body.baseUrl === 'string' ? req.body.baseUrl.trim() : '';
  const model = typeof req.body.model === 'string' ? req.body.model.trim() : '';
  if (baseUrl) validateBaseUrl(baseUrl);
  if (baseUrl.length > 500 || model.length > 120 || Boolean(baseUrl) !== Boolean(model))
    throw new Error('请同时填写接口地址和模型名称，或同时清空以启用演示模式。');
  setSetting('baseUrl', baseUrl);
  setSetting('model', model);
  res.json({ ok: true });
});
app.get('/api/files', (req, res) => res.json(listFiles()));
app.post('/api/files', (req, res) => {
  const result = writeFile(req.body.name, req.body.content);
  res.status(201).json(result);
});
app.get('/api/files/:name', (req, res) => {
  res.type('text/plain').send(readFile(req.params.name));
});
const dist = fileURLToPath(new URL('../dist/', import.meta.url));
app.use(express.static(dist));
app.get('/{*path}', (req, res) => {
  if (req.path.startsWith('/api/')) return res.status(404).json({ error: '接口不存在。' });
  res.sendFile(path.join(dist, 'index.html'));
});
app.use((error, req, res, next) => {
  res
    .status(error.status || 400)
    .json({
      error:
        error.code === 'EEXIST'
          ? '同名文件已存在，请更换文件名。'
          : error.code === 'ENOENT'
            ? '文件不存在。'
            : error.message || '请求失败。',
    });
});
if (process.argv[1] === fileURLToPath(import.meta.url))
  app.listen(Number(process.env.PORT || 4317), '127.0.0.1', () =>
    console.log(`栖伴 API: http://127.0.0.1:${process.env.PORT || 4317}`),
  );
