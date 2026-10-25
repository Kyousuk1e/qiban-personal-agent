# 栖伴 · Python Personal Agent

本地个人助手：聊天、长期记忆、后台任务、一次性提醒和受限文件工具。应用逻辑全部使用 **Python**，网页采用 **Jinja2 HTML 模板 + CSS**。

**当前版本不包含 Java、JavaScript 或 TypeScript 源码，不依赖 React、Node.js 或 npm。** 表单不需要脚本，任务执行期间自动刷新进度。项目为独立实现，与 Muse 无官方关联。

## 快速开始

需要 Python **3.11+**。Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m qiban
```

macOS / Linux：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m qiban
```

打开 **http://127.0.0.1:4317**。一个 Python 进程同时运行网页、API 和异步任务循环，无需构建前端或单独启动 Worker。按 Ctrl+C 停止。

## 连接模型

1. 复制 `.env.example` 为 `.env`。
2. 填写 `MODEL_BASE_URL`、`MODEL_NAME` 和 `MODEL_API_KEY`。
3. 重启 Python 服务。网页设置可修改地址和模型名，网页设置优先于环境变量。

模型需支持 Chat Completions 工具调用格式。基础地址填写到 `/v1` 等路径，不包含 `/chat/completions`。远程接口必须 HTTPS，本地模型支持 HTTP。不跟随重定向，不使用系统 HTTP 代理转发模型凭据。

密钥仅由后端读取，不返回页面或保存进数据库。`.env` 已被 Git 忽略。**使用云模型时，相关对话、最近 30 条记忆和工具读取的文件片段会发送至对应服务商。**

未配置地址和模型时进入演示模式，以下操作真实保存到本地：

- `/files`：列出文件。
- `/note 内容`：创建 Markdown 笔记。
- `/remember 偏好`：提出记忆建议，在任务详情确认后保存。

## 已实现

| 能力 | 范围 |
|---|---|
| 聊天 | 多会话、SQLite 历史、异步任务和最终回复 |
| 模型工具 | 列文件、读文件、新建文件、记忆建议、创建提醒 |
| 个人记忆 | 确认、编辑、删除；最多 100 条，模型加载最新 30 条 |
| 后台任务 | 原子领取、取消、120 秒超时、最多 8 轮模型调用，每轮最多 6 个工具 |
| 提醒 | 一次性任务、站内未读状态、离线后补发；表单输入与展示均为北京时间 |
| 文件 | 专用单层目录；txt/md/csv/json；200 KB；禁止路径穿越、链接及覆盖 |
| 安全 | 本机绑定、来源检查、CSRF 表单校验、HTML 转义、CSP 禁止脚本 |
| 审计 | 任务进度、工具结果、错误和模型返回用量 |

聊天等待结果时禁用输入，每 3 秒刷新；任务详情也自动刷新。其他页面可手动刷新，避免打断表单输入。不包含 token 流式输出或浏览器推送通知。

## 迁移与数据

v0.2 直接兼容 v0.1 SQLite 表结构和 UTC 时间格式，**不清空旧数据**：

- `data/agent.sqlite`：会话、记忆、任务、设置和审计记录。
- `workspace/`：工作资料和生成结果。
- `.env`：本地模型凭据。

迁移时先停止旧服务，再启动 Python 服务。异常退出后等待 15 秒心跳过期再启动。中断的执行中任务被标记失败，不自动重放；请检查执行记录，部分操作可能已完成。

关闭网页不影响后台任务；电脑关机或休眠期间无法执行。恢复后补发到期提醒。取消不撤销已创建的文件或独立提醒。

备份应停止服务后复制整个 `data/` 和 `workspace/`，或使用 SQLite backup API。不要只复制运行中的 SQLite 主文件而遗漏 WAL。

本版本只支持单用户、单进程本机使用，不要启动多个 Uvicorn Worker，也不要直接暴露公网。工作目录及同机进程应可信；这不是 VM 沙箱。尚无联网搜索、浏览器控制、邮件、支付、任意 Shell 或重复日程能力。

## 测试

```sh
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall -q qiban tests
```

测试使用临时数据库和 mock 模型，不读取用户资料、不消耗模型额度。覆盖文件边界、CSRF/来源检查、HTML 转义、记忆生命周期、数据重开、任务去重、运行中取消、超时、中断恢复和工具循环。Windows 缺少符号链接权限时对应测试跳过；GitHub Linux CI 会执行该测试。

## 项目结构

```text
qiban/
  __main__.py       Python 启动入口
  web.py            FastAPI、表单、CSRF、服务端页面
  storage.py        SQLite 数据层与事务
  agent.py          模型请求、工具白名单、演示模式
  worker.py         后台队列、取消、超时、心跳
  files.py          文件访问边界
  templates/        Jinja2 HTML 模板（无脚本）
  static/           CSS 样式
 tests/             Python unittest
 requirements.txt   已验证的锁定依赖
 pyproject.toml     Python 项目元数据
```

当前分支已移除旧 JS/TS 源码、Node 依赖清单与构建配置。Git 历史保留旧提交以便追溯，不改写已发布历史。
