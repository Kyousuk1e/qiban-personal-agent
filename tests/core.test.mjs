import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
const home = mkdtempSync(path.join(tmpdir(), 'personal-agent-test-'));
process.env.AGENT_HOME = home;
const { db, id, now, createTask, setSetting, addMessage } = await import('../server/db.mjs');
const { filePath, writeFile, readFile, listFiles, workspace } = await import('../server/files.mjs');
const { processOne, recoverInterrupted } = await import('../server/worker.mjs');
const { validateBaseUrl } = await import('../server/agent.mjs');
const { app } = await import('../server/index.mjs');
const server = await new Promise((resolve) => {
  const s = app.listen(0, '127.0.0.1', () => resolve(s));
});
const base = `http://127.0.0.1:${server.address().port}`;
const request = (url, method = 'GET', body, headers = {}) =>
  fetch(base + url, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Agent-Client': 'local-ui', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
after(async () => {
  await new Promise((resolve) => server.close(resolve));
  db.close();
  rmSync(home, { recursive: true, force: true });
});
test('file boundary blocks traversal, alternate streams, device names and overwrite', () => {
  for (const name of [
    '../secret.txt',
    'C:\\secret.txt',
    'a/b.md',
    'secret.txt:stream',
    'CON.txt',
    '.env',
    'page.html',
  ])
    assert.throws(() => filePath(name));
  writeFile('资料.md', '# hello');
  assert.equal(readFile('资料.md'), '# hello');
  assert.throws(() => writeFile('资料.md', 'replace'));
  assert.deepEqual(listFiles(), ['资料.md']);
});
test('symbolic links cannot escape workspace', (t) => {
  const outside = path.join(home, 'secret.txt');
  writeFileSync(outside, 'secret');
  try {
    symlinkSync(outside, path.join(workspace, 'link.txt'));
  } catch (e) {
    if (e.code === 'EPERM') {
      t.skip('Windows symlinks require developer mode');
      return;
    }
    throw e;
  }
  assert.throws(() => readFile('link.txt'));
  assert.ok(!listFiles().includes('link.txt'));
});
test('API blocks foreign origins and missing mutation marker', async () => {
  assert.equal(
    (
      await fetch(base + '/api/memories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: 'bad' }),
      })
    ).status,
    403,
  );
  assert.equal(
    (await request('/api/state', 'GET', undefined, { Origin: 'https://evil.example' })).status,
    403,
  );
  assert.equal((await request('/api/memories', 'POST', { content: '合法偏好' })).status, 201);
});
test('memory lifecycle persists edits and deletes', async () => {
  const memory = await (await request('/api/memories', 'POST', { content: '旧偏好' })).json();
  await request(`/api/memories/${memory.id}`, 'PATCH', { content: '新偏好' });
  assert.equal(
    db.prepare('SELECT content FROM memories WHERE id=?').get(memory.id).content,
    '新偏好',
  );
  await request(`/api/memories/${memory.id}`, 'DELETE');
  assert.equal(db.prepare('SELECT * FROM memories WHERE id=?').get(memory.id), undefined);
});
test('demo chat queues a durable task and creates a real file', async () => {
  setSetting('baseUrl', '');
  setSetting('model', '');
  const conversation = await (await request('/api/conversations', 'POST', {})).json();
  const response = await request(`/api/conversations/${conversation.id}/messages`, 'POST', {
    content: '/note 测试笔记',
  });
  assert.equal(response.status, 202);
  assert.equal(
    (await request(`/api/conversations/${conversation.id}/messages`, 'POST', { content: '重复' }))
      .status,
    409,
  );
  const task = await response.json();
  await processOne();
  assert.equal(db.prepare('SELECT status FROM tasks WHERE id=?').get(task.id).status, 'succeeded');
  assert.equal(
    db
      .prepare("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=? AND role='assistant'")
      .get(conversation.id).n,
    1,
  );
  assert.ok(listFiles().some((f) => f.startsWith('note-')));
});
test('reminder fires only once; future and cancelled tasks stay unexecuted', async () => {
  const due = createTask({ title: '到期', prompt: '到期', kind: 'reminder' });
  const future = createTask({
    title: '未来',
    prompt: '未来',
    kind: 'reminder',
    dueAt: new Date(Date.now() + 3600000).toISOString(),
  });
  const cancelled = createTask({ title: '取消', prompt: '取消', kind: 'reminder' });
  await request(`/api/tasks/${cancelled.id}/cancel`, 'POST', {});
  await processOne();
  assert.equal(await processOne(), false);
  assert.equal(db.prepare('SELECT status FROM tasks WHERE id=?').get(due.id).status, 'succeeded');
  assert.equal(db.prepare('SELECT status FROM tasks WHERE id=?').get(future.id).status, 'queued');
  assert.equal(
    db.prepare('SELECT status FROM tasks WHERE id=?').get(cancelled.id).status,
    'cancelled',
  );
});
test('interrupted tasks are failed rather than replayed', () => {
  const task = createTask({ title: '中断', prompt: '中断' });
  db.prepare("UPDATE tasks SET status='running' WHERE id=?").run(task.id);
  recoverInterrupted();
  assert.equal(db.prepare('SELECT status FROM tasks WHERE id=?').get(task.id).status, 'failed');
});
test('model adapter executes tool calls and records usage against a local mock', async () => {
  let calls = 0;
  const mock = (await import('node:http')).createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const payload = JSON.parse(raw);
    calls++;
    assert.ok(payload.messages[0].content.includes('栖伴'));
    res.setHeader('Content-Type', 'application/json');
    if (calls === 1)
      res.end(
        JSON.stringify({
          choices: [
            {
              message: {
                role: 'assistant',
                content: null,
                tool_calls: [
                  {
                    id: 'tool1',
                    type: 'function',
                    function: {
                      name: 'write_file',
                      arguments: JSON.stringify({
                        name: '模型笔记.md',
                        content: '通过 mock 工具调用创建',
                      }),
                    },
                  },
                ],
              },
            },
          ],
          usage: { total_tokens: 50 },
        }),
      );
    else {
      assert.equal(payload.messages.at(-1).role, 'tool');
      res.end(
        JSON.stringify({
          choices: [{ message: { role: 'assistant', content: '文件已经创建。' } }],
        }),
      );
    }
  });
  await new Promise((resolve) => mock.listen(0, '127.0.0.1', resolve));
  try {
    setSetting('baseUrl', `http://127.0.0.1:${mock.address().port}/v1`);
    setSetting('model', 'mock');
    const task = createTask({ title: '模型工具测试', prompt: '创建笔记' });
    await processOne();
    assert.equal(
      db.prepare('SELECT status FROM tasks WHERE id=?').get(task.id).status,
      'succeeded',
    );
    assert.equal(readFile('模型笔记.md'), '通过 mock 工具调用创建');
    assert.equal(calls, 2);
  } finally {
    setSetting('baseUrl', '');
    setSetting('model', '');
    await new Promise((resolve) => mock.close(resolve));
  }
});
test('provider URL disallows insecure remote endpoints and credentials', () => {
  assert.equal(validateBaseUrl('https://example.com/v1/'), 'https://example.com/v1');
  assert.throws(() => validateBaseUrl('http://example.com'));
  assert.throws(() => validateBaseUrl('https://secret@example.com'));
  assert.throws(() => validateBaseUrl('file:///etc/passwd'));
});

test('cancelling an in-flight model request prevents later tool writes', async () => {
  let started;
  const received = new Promise((resolve) => {
    started = resolve;
  });
  const mock = (await import('node:http')).createServer((req, res) => {
    req.resume();
    started();
    const timer = setTimeout(() => {
      res.setHeader('Content-Type', 'application/json');
      res.end(
        JSON.stringify({ choices: [{ message: { role: 'assistant', content: 'late reply' } }] }),
      );
    }, 2000);
    res.on('close', () => clearTimeout(timer));
  });
  await new Promise((resolve) => mock.listen(0, '127.0.0.1', resolve));
  try {
    setSetting('baseUrl', `http://127.0.0.1:${mock.address().port}/v1`);
    setSetting('model', 'mock');
    const task = createTask({ title: '取消执行中任务', prompt: '等待响应' });
    const work = processOne();
    await received;
    await request(`/api/tasks/${task.id}/cancel`, 'POST', {});
    await work;
    const saved = db.prepare('SELECT status,result FROM tasks WHERE id=?').get(task.id);
    assert.equal(saved.status, 'cancelled');
    assert.equal(saved.result, null);
  } finally {
    setSetting('baseUrl', '');
    setSetting('model', '');
    mock.closeAllConnections();
    await new Promise((resolve) => mock.close(resolve));
  }
});
