import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowUp,
  Plus,
  MessageSquare,
  ListTodo,
  Brain,
  FolderOpen,
  Settings,
  Sparkles,
  Check,
  X,
  ChevronRight,
  Clock3,
  Circle,
  RefreshCw,
  ExternalLink,
  Trash2,
} from 'lucide-react';
import './style.css';
type Task = {
  id: string;
  title: string;
  kind: string;
  status: string;
  due_at: string;
  conversation_id: string | null;
  result: string | null;
  error: string | null;
  read: number;
};
type Memory = { id: string; content: string; source: string; updated_at: string };
type State = {
  conversations: { id: string; title: string }[];
  memories: Memory[];
  tasks: Task[];
  files: string[];
  config: { baseUrl: string; model: string; hasKey: boolean; mode: string };
  workerOnline: boolean;
};
type Event = { id: number; label: string; detail: string; created_at: string };
type Message = { id: string; role: string; content: string };
type Page = 'chat' | 'tasks' | 'memory' | 'files' | 'settings';
const api = async (url: string, method = 'GET', body?: unknown) => {
  const res = await fetch(`/api${url}`, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Agent-Client': 'local-ui' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || '请求失败');
  return data;
};
const labels: Record<string, string> = {
  queued: '待执行',
  running: '执行中',
  succeeded: '已完成',
  failed: '未完成',
  cancelled: '已取消',
};
const date = (value: string) =>
  new Date(value).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
function Avatar({ small = false }: { small?: boolean }) {
  return (
    <div className={`avatar ${small ? 'small' : ''}`}>
      <span />
      <span />
    </div>
  );
}
function App() {
  const [state, setState] = useState<State | null>(null);
  const [page, setPage] = useState<Page>('chat');
  const [active, setActive] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Task | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [memory, setMemory] = useState('');
  const [editMemory, setEditMemory] = useState<string | null>(null);
  const [fileName, setFileName] = useState('');
  const [fileContent, setFileContent] = useState('');
  const [reminder, setReminder] = useState('');
  const [due, setDue] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [notice, setNotice] = useState('');
  async function refresh() {
    const data: State = await api('/state');
    setState(data);
    return data;
  }
  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      try {
        const data = await api('/state');
        if (!stopped) setState(data);
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    };
    void poll();
    const t = setInterval(poll, 2000);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, []);
  useEffect(() => {
    if (state && !settingsLoaded) {
      setBaseUrl(state.config.baseUrl);
      setModel(state.config.model);
      setSettingsLoaded(true);
    }
  }, [state, settingsLoaded]);
  useEffect(() => {
    let stopped = false;
    if (!active) {
      setMessages([]);
      return;
    }
    const fetchMessages = async () => {
      try {
        const data = await api(`/conversations/${active}/messages`);
        if (!stopped) setMessages(data);
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    };
    void fetchMessages();
    const t = setInterval(fetchMessages, 1200);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [active]);
  useEffect(() => {
    let stopped = false;
    if (!selected) {
      setEvents([]);
      return;
    }
    const load = async () => {
      try {
        const data = await api(`/tasks/${selected.id}/events`);
        if (!stopped) setEvents(data);
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    };
    void load();
    const t = setInterval(load, 1500);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [selected?.id]);
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const send = () =>
    act(async () => {
      if (!draft.trim()) return;
      let cid = active;
      if (!cid) {
        const c = await api('/conversations', 'POST', {});
        cid = c.id;
        setActive(cid);
      }
      await api(`/conversations/${cid}/messages`, 'POST', { content: draft });
      setDraft('');
      setMessages(await api(`/conversations/${cid}/messages`));
    });
  const currentTask = state?.tasks.find(
    (t) => t.conversation_id === active && ['queued', 'running'].includes(t.status),
  );
  const running = state?.tasks.filter((t) => ['queued', 'running'].includes(t.status)) || [];
  const unread =
    state?.tasks.filter((t) => t.kind === 'reminder' && t.status === 'succeeded' && !t.read) || [];
  const selectedTask = state?.tasks.find((t) => t.id === selected?.id) || selected;
  function openTask(task: Task) {
    setSelected(task);
    if (task.status === 'succeeded' && !task.read)
      void act(async () => {
        await api(`/tasks/${task.id}/read`, 'POST', {});
      });
  }
  const navigation = [
    { id: 'chat', label: '与栖伴对话', icon: MessageSquare },
    { id: 'tasks', label: '任务与提醒', icon: ListTodo },
    { id: 'memory', label: '关于你的记忆', icon: Brain },
    { id: 'files', label: '工作文件', icon: FolderOpen },
  ] as const;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <Avatar small />
          <div>
            栖伴<span>PERSONAL AGENT</span>
          </div>
        </div>
        <button
          className="new-chat"
          onClick={() => {
            setActive(null);
            setPage('chat');
            setDraft('');
          }}
        >
          <Plus size={17} /> 开始新的对话
        </button>
        <nav>
          {navigation.map((n) => (
            <button
              key={n.id}
              className={page === n.id ? 'active' : ''}
              onClick={() => setPage(n.id)}
            >
              <n.icon size={18} />
              {n.label}
              {n.id === 'tasks' && running.length > 0 && <b>{running.length}</b>}
            </button>
          ))}
        </nav>
        <div className="history-label">最近的对话</div>
        <div className="history">
          {state?.conversations.slice(0, 12).map((c) => (
            <button
              className={active === c.id && page === 'chat' ? 'chosen' : ''}
              key={c.id}
              onClick={() => {
                setActive(c.id);
                setPage('chat');
              }}
            >
              <MessageSquare size={14} />
              <span>{c.title}</span>
            </button>
          ))}
          {!state?.conversations.length && <p>每一个想法，都可以从这里开始。</p>}
        </div>
        <div className="sidebar-bottom">
          <div className="local-label">
            <span className={state?.workerOnline ? 'dot' : 'dot offline'} />
            {state?.workerOnline ? '后台助手在线' : '后台助手离线'}
          </div>
          <button
            onClick={() => setPage('settings')}
            className={page === 'settings' ? 'active' : ''}
          >
            <Settings size={17} /> 模型与设置
          </button>
          <div className="profile">
            <div className="profile-icon">我</div>
            <div>
              我的私人空间<small>本地存储 · 单用户版本</small>
            </div>
            <span className="version">v0.1</span>
          </div>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <span className="breadcrumb">我的空间</span>
            <ChevronRight size={14} />
            <span>
              {
                {
                  chat: '对话',
                  tasks: '任务与提醒',
                  memory: '个人记忆',
                  files: '工作文件',
                  settings: '设置',
                }[page]
              }
            </span>
          </div>
          <span className={`mode ${state?.config.mode === 'live' ? 'live' : ''}`}>
            <span className="dot" />
            {state?.config.mode === 'live' ? state.config.model : '演示模式'}
          </span>
        </header>
        {error && (
          <div className="alert error" role="alert">
            {error}
            <button aria-label="关闭错误" onClick={() => setError('')}>
              <X size={16} />
            </button>
          </div>
        )}
        {notice && (
          <div className="alert" role="status">
            {notice}
          </div>
        )}
        {unread.length > 0 && (
          <button
            className="reminder-banner"
            onClick={() => {
              setPage('tasks');
              openTask(unread[0]);
            }}
          >
            <Clock3 size={16} />
            你有 {unread.length} 条提醒：{unread[0].title}
            <ChevronRight size={16} />
          </button>
        )}
        {page === 'chat' && (
          <div className="chat-page">
            {!messages.length && !active ? (
              <div className="welcome">
                <div className="eyebrow">
                  <span /> 留一点时间，给重要的事
                </div>
                <Avatar />
                <h1>
                  你好，我是栖伴<span>一起把想法，变成进展。</span>
                </h1>
                <p>
                  记住你的偏好，整理手边的资料，照看那些待办。
                  <br />
                  从一件小事开始，我陪你慢慢完成。
                </p>
                <div className="suggestions">
                  {[
                    {
                      icon: FolderOpen,
                      title: '整理我的资料',
                      text: '先看看工作空间里有哪些文件',
                      prompt: state?.config.mode === 'live' ? '列出我的工作目录文件。' : '/files',
                    },
                    {
                      icon: Brain,
                      title: '记住我的偏好',
                      text: '让每一次交流都更懂你一点',
                      prompt:
                        state?.config.mode === 'live'
                          ? '请记住：我喜欢中文、简洁的回答。'
                          : '/remember 我喜欢中文、简洁的回答。',
                    },
                    {
                      icon: Sparkles,
                      title: '留住一个想法',
                      text: '把灵感保存成一份工作笔记',
                      prompt:
                        state?.config.mode === 'live'
                          ? '创建一份想法.md，内容是：搭建属于自己的个人助手。'
                          : '/note # 我的第一个想法\n搭建属于自己的个人助手。',
                    },
                  ].map((s) => (
                    <button key={s.title} onClick={() => setDraft(s.prompt)}>
                      <s.icon size={21} />
                      <strong>{s.title}</strong>
                      <span>{s.text}</span>
                      <ChevronRight size={16} />
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="messages">
                {messages.map((m) => (
                  <div className={`message ${m.role}`} key={m.id}>
                    {m.role === 'assistant' && <Avatar small />}
                    <div>
                      <small>{m.role === 'assistant' ? '栖伴' : '你'}</small>
                      <div className="message-body">{m.content}</div>
                    </div>
                  </div>
                ))}
                {currentTask && (
                  <button className="working" onClick={() => openTask(currentTask)}>
                    <RefreshCw size={16} className="spin" />
                    {currentTask.status === 'queued' ? '任务已排队' : '正在处理你的任务'} · 查看进度
                  </button>
                )}
              </div>
            )}
            <div className="composer-wrap">
              <form
                className="composer"
                onSubmit={(e) => {
                  e.preventDefault();
                  void send();
                }}
              >
                <textarea
                  aria-label="给栖伴发消息"
                  placeholder="有什么想交给我？"
                  value={draft}
                  maxLength={12000}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      if (!busy && !currentTask) void send();
                    }
                  }}
                />
                <div className="composer-bottom">
                  <span>
                    <FolderOpen size={14} /> 专属工作空间<span className="divider">·</span>
                    {state?.config.mode === 'live' ? '已连接模型' : '连接模型后解锁自然语言任务'}
                  </span>
                  <button
                    aria-label="发送消息"
                    type="submit"
                    disabled={busy || !!currentTask || !draft.trim()}
                  >
                    <ArrowUp size={20} />
                  </button>
                </div>
              </form>
              <p className="composer-note">
                {state?.config.mode === 'demo'
                  ? '演示模式不调用 AI；文件、记忆与提醒操作真实保存到本地。'
                  : '相关对话、记忆及调用的文件内容会发送至你配置的模型服务。'}
              </p>
            </div>
          </div>
        )}
        {page === 'tasks' && (
          <section className="content-page">
            <div className="page-title">
              <div className="eyebrow">YOUR MOMENTUM</div>
              <h1>每件事，都有进展。</h1>
              <p>查看执行过程，把需要记得的事放心放在这里。</p>
            </div>
            <div className="stats">
              <div>
                <strong>{running.length}</strong>
                <span>进行中的任务</span>
              </div>
              <div>
                <strong>{state?.tasks.filter((t) => t.status === 'succeeded').length || 0}</strong>
                <span>已经完成</span>
              </div>
              <div>
                <strong>
                  {state?.tasks.filter((t) => t.kind === 'reminder' && t.status === 'queued')
                    .length || 0}
                </strong>
                <span>待触发提醒</span>
              </div>
            </div>
            <form
              className="panel reminder-form"
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await api('/tasks', 'POST', {
                    title: reminder,
                    dueAt: new Date(due).toISOString(),
                  });
                  setReminder('');
                  setDue('');
                  setNotice('提醒已安排。服务运行时触发，关闭页面后仍会保留。');
                });
              }}
            >
              <div>
                <Clock3 size={19} />
                <strong>安排一个提醒</strong>
              </div>
              <input
                aria-label="提醒内容"
                placeholder="有什么事需要提醒你？"
                required
                maxLength={100}
                value={reminder}
                onChange={(e) => setReminder(e.target.value)}
              />
              <input
                aria-label="提醒时间"
                type="datetime-local"
                required
                value={due}
                onChange={(e) => setDue(e.target.value)}
              />
              <button className="primary" disabled={busy}>
                添加提醒
              </button>
              <small>输入时间使用浏览器本地时区；任务展示为北京时间。电脑需保持运行。</small>
            </form>
            <div className="section-label">
              全部任务 <span>{state?.tasks.length || 0}</span>
            </div>
            <div className="task-list">
              {state?.tasks.map((t) => (
                <button className="task-row" key={t.id} onClick={() => openTask(t)}>
                  <div className={`task-icon ${t.status}`}>
                    {t.kind === 'reminder' ? (
                      <Clock3 size={19} />
                    ) : t.status === 'succeeded' ? (
                      <Check size={19} />
                    ) : (
                      <Circle size={19} />
                    )}
                  </div>
                  <div>
                    <strong>{t.title}</strong>
                    <small>
                      {t.kind === 'reminder' ? '提醒' : '助手任务'} · {date(t.due_at)}
                    </small>
                  </div>
                  <span className={`badge ${t.status}`}>{labels[t.status]}</span>
                  <ChevronRight size={17} />
                </button>
              ))}
              {!state?.tasks.length && (
                <div className="empty">还没有任务。聊一个想法，或者安排第一个提醒。</div>
              )}
            </div>
          </section>
        )}
        {page === 'memory' && (
          <section className="content-page">
            <div className="page-title">
              <div className="eyebrow">A LITTLE MORE ABOUT YOU</div>
              <h1>慢慢了解，认真记住。</h1>
              <p>这里的每条记忆都由你决定。可随时编辑或删除。</p>
            </div>
            <form
              className="panel"
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await api(
                    editMemory ? `/memories/${editMemory}` : '/memories',
                    editMemory ? 'PATCH' : 'POST',
                    { content: memory },
                  );
                  setMemory('');
                  setEditMemory(null);
                  setNotice('记忆已保存。');
                });
              }}
            >
              <label>
                {editMemory ? '编辑这条记忆' : '有什么希望我记住？'}
                <textarea
                  required
                  maxLength={1000}
                  value={memory}
                  onChange={(e) => setMemory(e.target.value)}
                  placeholder="例如：我喜欢简洁的中文回答；我正在学习 Python。"
                />
              </label>
              <div className="form-actions">
                {editMemory && (
                  <button
                    type="button"
                    onClick={() => {
                      setEditMemory(null);
                      setMemory('');
                    }}
                  >
                    取消编辑
                  </button>
                )}
                <button className="primary" disabled={busy}>
                  保存记忆
                </button>
              </div>
            </form>
            <div className="memory-grid">
              {state?.memories.map((m) => (
                <article className="memory-card" key={m.id}>
                  <Brain size={20} />
                  <p>{m.content}</p>
                  <small>
                    {m.source} · {date(m.updated_at)}
                  </small>
                  <div>
                    <button
                      onClick={() => {
                        setMemory(m.content);
                        setEditMemory(m.id);
                      }}
                    >
                      编辑
                    </button>
                    <button
                      aria-label="删除记忆"
                      onClick={() => {
                        if (window.confirm('删除这条记忆？删除后将不再加载到新的模型请求。'))
                          void act(async () => {
                            await api(`/memories/${m.id}`, 'DELETE');
                          });
                      }}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </article>
              ))}
            </div>
            {!state?.memories.length && (
              <div className="empty">从一个偏好开始，不必一次告诉我全部。</div>
            )}
          </section>
        )}
        {page === 'files' && (
          <section className="content-page">
            <div className="page-title">
              <div className="eyebrow">ROOM FOR YOUR IDEAS</div>
              <h1>资料与成果，在这里。</h1>
              <p>助手只读写专用工作目录。支持 txt、md、csv、json，单文件最大 200 KB。</p>
            </div>
            <form
              className="panel"
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await api('/files', 'POST', { name: fileName, content: fileContent });
                  setFileName('');
                  setFileContent('');
                  setNotice('文件已创建。');
                });
              }}
            >
              <label>
                文件名称
                <input
                  required
                  value={fileName}
                  onChange={(e) => setFileName(e.target.value)}
                  placeholder="我的资料.md"
                />
              </label>
              <label>
                文件内容
                <textarea
                  className="file-editor"
                  value={fileContent}
                  onChange={(e) => setFileContent(e.target.value)}
                  placeholder="粘贴资料，或者写下一个想法……"
                />
              </label>
              <div className="form-actions">
                <button className="primary" disabled={busy}>
                  创建文件
                </button>
              </div>
            </form>
            <div className="file-list">
              {state?.files.map((f) => (
                <a
                  key={f}
                  href={`/api/files/${encodeURIComponent(f)}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <FolderOpen size={21} />
                  <span>{f}</span>
                  <ExternalLink size={16} />
                </a>
              ))}
            </div>
            {!state?.files.length && (
              <div className="empty">工作空间还是空的，添加第一份资料吧。</div>
            )}
          </section>
        )}
        {page === 'settings' && (
          <section className="content-page">
            <div className="page-title">
              <div className="eyebrow">MAKE IT YOURS</div>
              <h1>连接你的智能助手。</h1>
              <p>选择兼容 Chat Completions 工具调用格式的模型服务。</p>
            </div>
            <form
              className="panel settings-panel"
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await api('/settings', 'PUT', { baseUrl, model });
                  setNotice('配置已保存。发送一条消息即可验证模型连接。');
                });
              }}
            >
              <label>
                接口基础地址
                <input
                  placeholder="https://你的服务商地址/v1"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
                <small>
                  填写到 /v1 等基础路径，不包含 /chat/completions。仅 HTTPS 或本机 HTTP。
                </small>
              </label>
              <label>
                模型名称
                <input
                  placeholder="填写服务商提供的模型 ID"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                />
              </label>
              <div className="key-status">
                <span className={state?.config.hasKey ? 'dot' : 'dot offline'} />
                {state?.config.hasKey ? '已从本地环境加载 API 密钥' : '未配置 API 密钥'}
              </div>
              <div className="setup-note">
                在项目根目录复制 <code>.env.example</code> 为 <code>.env</code>，填写{' '}
                <code>MODEL_API_KEY</code> 后重启服务。密钥不会显示在页面或存入数据库。
                <br />
                <br />
                清空接口地址与模型名称即可回到演示模式。连接模型后，对话、已加载记忆及工具读取的资料会发送到该服务商。
              </div>
              <button className="primary" disabled={busy}>
                保存连接设置
              </button>
            </form>
            <div className="panel info-panel">
              <h3>你的数据由你保管</h3>
              <p>
                聊天、任务和记忆保存在本机 data/agent.sqlite；工作文件保存在
                workspace/。这两个目录和 .env 均不会上传至 GitHub。
              </p>
              <p>
                这是本地单用户原型，尚未实现外网登录、多用户隔离、联网搜索、邮件、浏览器控制或重复日程。请勿直接暴露到公网。
              </p>
            </div>
          </section>
        )}
      </main>
      {selectedTask && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label="任务详情"
            onClick={(e) => e.stopPropagation()}
          >
            <button className="modal-close" aria-label="关闭详情" onClick={() => setSelected(null)}>
              <X size={20} />
            </button>
            <div className="eyebrow">TASK DETAILS</div>
            <h2>{selectedTask.title}</h2>
            <span className={`badge ${selectedTask.status}`}>{labels[selectedTask.status]}</span>
            <div className="timeline">
              {events.map((ev) => (
                <div key={ev.id}>
                  <span className="timeline-dot" />
                  <strong>{ev.label}</strong>
                  <small>{date(ev.created_at)}</small>
                  {ev.detail && <p>{ev.detail}</p>}
                  {ev.label === '待确认记忆' && (
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          await api('/memories', 'POST', { content: ev.detail });
                          setNotice('已保存到个人记忆。');
                          setSelected(null);
                        })
                      }
                    >
                      保存这条记忆
                    </button>
                  )}
                </div>
              ))}
            </div>
            {selectedTask.result && <pre className="task-result">{selectedTask.result}</pre>}
            {selectedTask.error && <div className="alert error">{selectedTask.error}</div>}
            {['queued', 'running'].includes(selectedTask.status) && (
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await api(`/tasks/${selectedTask.id}/cancel`, 'POST', {});
                  })
                }
              >
                取消任务
              </button>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
