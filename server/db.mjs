import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
export const root = path.resolve(process.env.AGENT_HOME || process.cwd());
mkdirSync(path.join(root, 'data'), { recursive: true });
export const db = new DatabaseSync(path.join(root, 'data', 'agent.sqlite'));
db.exec(`
 PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
 CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, content TEXT NOT NULL, source TEXT NOT NULL, updated_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, due_at TEXT NOT NULL, conversation_id TEXT, result TEXT, error TEXT, read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, label TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS tasks_schedule ON tasks(status, due_at);
`);
export const id = () => randomUUID();
export const now = () => new Date().toISOString();
export function setting(key, fallback = '') {
  return db.prepare('SELECT value FROM settings WHERE key=?').get(key)?.value ?? fallback;
}
export function setSetting(key, value) {
  db.prepare(
    'INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
  ).run(key, value);
}
export function config() {
  return {
    baseUrl: setting('baseUrl', process.env.MODEL_BASE_URL || ''),
    model: setting('model', process.env.MODEL_NAME || ''),
    apiKey: process.env.MODEL_API_KEY || '',
  };
}
export function addMessage(conversationId, role, content) {
  const row = { id: id(), conversation_id: conversationId, role, content, created_at: now() };
  db.prepare('INSERT INTO messages VALUES (@id,@conversation_id,@role,@content,@created_at)').run(
    row,
  );
  return row;
}
export function createTask({
  title,
  prompt,
  kind = 'agent',
  dueAt = now(),
  conversationId = null,
}) {
  const taskId = id();
  const time = now();
  db.prepare(
    'INSERT INTO tasks (id,title,prompt,kind,status,due_at,conversation_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
  ).run(taskId, title.slice(0, 100), prompt, kind, 'queued', dueAt, conversationId, time, time);
  return db.prepare('SELECT * FROM tasks WHERE id=?').get(taskId);
}
export function event(taskId, label, detail = '') {
  db.prepare('INSERT INTO events (task_id,label,detail,created_at) VALUES (?,?,?,?)').run(
    taskId,
    label,
    detail,
    now(),
  );
}
