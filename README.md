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

## 队友与首次引导（v0.6）

首次启动自动创建一位待配置的队友；重复访问、刷新或重启不会重复创建。左上角“＋”用于添加新队友，每位队友都有独立的名字、设定和主对话。原有会话迁移到第一位队友名下，不删除历史资料。

首次引导可以直接选择 Haru、Sunny、栖伴，或输入自然语言描述，例如“你的名字叫做「佳佳」，个性乐观有活力，主要帮我整理资料”。常见“名字叫做／名字是／你叫”句式会提取名字，其余描述原样作为角色设定保存；复杂表达未识别时，可通过右上角“队友设定”明确修改名称。

每次模型调用只加载当前队友的角色设定及当前会话历史。角色设定不会增加工具权限。个人记忆和手动创建的工作文本目前仍是用户级共享，尚未实现每位队友独立的长期记忆库。固定岗位预览已移除；新增队友是持久化实体，不是新增一个空白聊天入口。

资料面板按需展开，会话分支仍保留在所属队友下。全部交互使用 Python 服务端模板和原生表单，无 JavaScript。

## Google 应用连接（v0.7）

打开某位队友，点击右上角 **连接应用**。支持搜索、分类、已连接筛选、账号显示及断开当前队友授权。

| 应用 | 当前工具 | OAuth 权限 |
|---|---|---|
| Gmail | 搜索最多 10 封邮件、按 ID 读取纯文本（最多 16000 字符，无附件） | `gmail.readonly` |
| Google Calendar | 读取主日历指定时间范围最多 30 条事件 | `calendar.events.readonly` |
| Google Drive | 按名称搜索最多 20 个文件的名称、类型、时间和链接，不读取正文 | `drive.metadata.readonly` |

结果会标记是否还有更多记录；此版本不自动遍历下一页，不声称搜索结果完整。三种连接均额外申请 `userinfo.email`，用于显示当前授权账号。连接不会自动读取资料；用户请求时，相关只读工具结果会发送到配置的模型提供商。

### 首次配置

1. 在 [Google Cloud Console](https://console.cloud.google.com/apis/dashboard) 创建项目，启用 Gmail API、Google Calendar API、Google Drive API。
2. 配置 OAuth 同意屏幕和所需 scope；测试模式下将自己的 Google 账号加入测试用户。
3. 创建 **Web 应用** OAuth 客户端，将重定向 URI 精确设置为 `http://127.0.0.1:4317/oauth/google/callback`。
4. 在助手的“连接应用 → 首次配置 Google 登录”输入 Client ID 和 Client Secret。配置加密写入 `data/google-client.enc`。也可通过 `.env` 的 `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` 配置后重启；网页保存的配置优先。
5. 使用 **http://127.0.0.1:4317** 打开助手，点击目标应用的“连接”，在 Google 页面完成账号选择和授权。回调成功后才显示已连接。

完整授权需要用户自己的 Google OAuth 应用与同意操作。本仓库不附带客户端凭据。Gmail 和 Drive 的相关权限可能涉及 Google 验证要求；公开发布前请阅读 [OAuth Web 应用指南](https://developers.google.com/identity/protocols/oauth2/web-server) 和各 API scope 文档。Google 登录页面如不允许内嵌浏览器，请改用 Chrome 或 Edge。

若本机需要代理访问 Google，可在 `.env` 设置 `GOOGLE_HTTP_PROXY=http://127.0.0.1:7897`，随后重启；端口应按自己的代理配置填写。代理只应用于 Google OAuth / API 请求，模型请求不受影响。

### 授权边界与存储

- 每位 Bot 的每项服务单独保存账号与令牌；创建新队友或给其他队友授权不会自动共享。会话分支继承所属 Bot 的应用访问权限。
- OAuth 使用一次性 state、10 分钟有效期、浏览器绑定和 PKCE；拒绝缺失权限、错误浏览器、过期和重放请求。新授权替代旧授权请求，断开连接阻止尚未完成的回调重新连接。
- Access token / refresh token 使用 Fernet 加密存入 SQLite；密钥在 `data/google-token.key`。备份应同时包含数据库与此密钥。加密不能抵御能读取整个本机 data 目录的攻击者。
- 页面、`/api/state` 和模型工具结果不返回令牌或客户端密钥。Uvicorn access log 已关闭，避免 OAuth 回调 URL 中的授权码进入访问日志。
- 断开连接删除此 Bot 的本地凭据及未完成授权，不自动撤销 Google 账号对整个 OAuth 应用的授权；可在 [Google 账号连接管理](https://myaccount.google.com/connections) 撤销整个应用，这可能影响其他 Bot。既有聊天引用不会自动删除。
- 只读工具没有发送邮件、修改日历、删除或共享云文件的能力。授权失效后标记为需要重新授权，网络失败不返回敏感原始响应。

## 已实现

### 围绕资料持续对话（v0.4）

- 左侧列出独立会话和带 `↳` 标记的分支；中间区分每轮用户提问与助手回答，并显示时间。
- 右侧“对话背景”支持直接上传，或从资料库关联已有文件。每个会话最多 20 份资料，刷新、重启和后续追问保持关联。
- 首页上传会创建一个新会话并关联资料；资料库支持“仅存入资料库”和“上传并开始对话”。
- **新建空白对话**不继承历史消息或附件。**会话选项 → 创建对话分支**复制当前全部消息与关联资料，后续独立交流，可返回原对话；执行中禁止创建分支。
- 每次模型请求加载最近 16 条消息、个人记忆及每份关联资料的前 1800 字符，长文通过工具分页读取。资料是背景数据，不是系统指令。未关联的导入资料不会出现在该会话的模型文件列表，也不能通过工具直接读取；手动创建的工作文本仍为全局共享。
- “移出会话”仅取消附件关联，原文件保留在资料库。旧回答中引用的内容仍属于聊天历史；需要完全干净的上下文时请新建空白对话。个人记忆仍跨会话生效。
- 旧数据库自动增加关联表，保留已有消息和文件；旧上传资料可手动关联到任意会话。
- 界面参考 [Grok Bot 官方设计说明](https://x.ai/news/designing-grok-bot) 的持续助手与按需侧栏思路；本项目独立实现，不包含 Grok Bot 的云端电脑或多 Bot 系统。

### 上传项目资料

打开左侧 **工作文件**，选择文件并点击 **上传并提取文字**。每次上传一个文件，原文件最大 **20 MB**，无需 JavaScript。

支持：

- PDF：文字层提取，保留页码，最多前 100 页。
- Word `.docx`：正文与表格。
- Excel `.xlsx`：最多前 20 个工作表、每表前 1000 行和 40 列；保留公式文本，不执行计算。
- PowerPoint `.pptx`：最多前 100 张幻灯片的文本和表格。
- `.txt`、`.md`、`.csv`、`.json`、`.py`、`.toml`、`.yaml`、`.yml`、`.ini`、`.log`：UTF-8 或 GB18030 文本，不执行代码。

原件存入本机 `data/uploads/`，提取文字存入 `workspace/import-<唯一ID>.md`。同名上传各自保留，绝不覆盖。页面可下载原件、查看提取文字、点击 **让助手总结**。上传与解析均在本地进行；只有请求助手读取或总结时，相关提取文字才发送至配置的模型。

提取文字最多 180 KB，超出时明确标注截断。长文本工具支持按字符 offset 分段读取，不默默丢弃超出单次工具返回长度的部分。Office 容器限制解压大小与条目数，解析在单独进程执行，30 秒超时终止；这不是完整的 OS 沙箱。

**尚不支持 OCR、图片理解、旧版 `.doc/.xls/.ppt`、压缩包、音视频或任意二进制文件。** 扫描 PDF、图片、图表等未读取内容会明确提示；无可读文字时不提供自动总结按钮。PDF 加密需先解除密码，旧版 Office 请转换到新格式。原件与提取结果不上传 GitHub。

| 能力 | 范围 |
|---|---|
| 聊天 | 多会话、SQLite 历史、异步任务和最终回复 |
| 模型工具 | 列文件、读文件、新建文件、记忆建议、创建提醒 |
| 个人记忆 | 确认、编辑、删除；最多 100 条，模型加载最新 30 条 |
| 后台任务 | 原子领取、取消、120 秒超时、最多 8 轮模型调用，每轮最多 6 个工具 |
| 提醒 | 一次性任务、站内未读状态、离线后补发；表单输入与展示均为北京时间 |
| 文件 | 专用单层目录；txt/md/csv/json；200 KB；禁止路径穿越、链接及覆盖 |
| 资料上传 | PDF、DOCX、XLSX、PPTX 和常见文本源码；保留原件、提取文字、一键总结 |
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

本版本只支持单用户、单进程本机使用，不要启动多个 Uvicorn Worker，也不要直接暴露公网。工作目录及同机进程应可信；这不是 VM 沙箱。尚无通用联网搜索、浏览器控制、邮件发送、支付、任意 Shell 或重复日程能力。Google 邮件阅读需另行授权。

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
  uploads.py        原文件保存、导入记录与解析进程管理
  extract.py        PDF、Office 和文本提取
  templates/        Jinja2 HTML 模板（无脚本）
  static/           CSS 样式
 tests/             Python unittest
 requirements.txt   已验证的锁定依赖
 pyproject.toml     Python 项目元数据
```

当前分支已移除旧 JS/TS 源码、Node 依赖清单与构建配置。Git 历史保留旧提交以便追溯，不改写已发布历史。
