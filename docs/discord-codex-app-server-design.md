# Discord + Codex app-server 方案设计

版本：v0.1 评审版  
日期：2026-03-21

## 1. 背景

目标是建设一个面向团队协作的 Discord Bot，使用户能够直接在 Discord 中发起、继续、审批和审计 Codex 会话。

本方案明确选择：

- 前端渠道：Discord
- Codex 接入面：`codex app-server`
- 后端语言：Python
- Discord SDK：`discord.py`
- Codex SDK：`codex-app-server-sdk`

不选择 `OpenClaw + Codex ACP` 作为主方案的原因是：

- `app-server` 是 Codex 当前仓库中的原生 rich interface 接口，协议语义完整
- `OpenClaw + ACP` 属于桥接链路，会在会话重建、审批、MCP、工具历史和流式细节上产生能力折损
- 需要长期可维护、可审计、可扩展时，直接面向 `app-server` 更稳

## 2. 调研结论

### 2.1 Codex 官方能力面

根据当前仓库文档，Codex 当前对外最适合本场景的接口是 `app-server`：

- `thread/start`
- `thread/resume`
- `thread/fork`
- `thread/read`
- `turn/start`
- `turn/steer`
- `turn/interrupt`
- `review/start`
- `item/commandExecution/requestApproval`
- `item/fileChange/requestApproval`

同时，Python SDK 已直接面向 `app-server v2` 封装，适合真实脚本和应用使用。

### 2.2 Discord 生态

当前 Discord 生态中，成熟方案主要分为两类：

- `discord.js` 为主的 TypeScript 机器人
- `discord.py` 为主的 Python 机器人

虽然 Discord 生态整体上 `discord.js` 更流行，但在本项目中，Codex 官方高层 SDK 在 Python 侧更贴近 `app-server`，因此选 Python 更能减少协议层重复开发。

### 2.3 结论

最佳落地路径是：

- `Discord` 负责交互与展示
- `discord.py` 负责 Discord 事件接入
- `codex-app-server-sdk` 负责与 `codex app-server` 通信
- 每个活跃 Discord 会话绑定一个独立 worker，避免共享 turn 消费器冲突

## 3. 设计原则

### 3.1 能力原生优先

尽量使用 Codex 原生 `thread / turn / item` 语义，不再引入第二套会话协议。

### 3.2 安全默认保守

默认使用 `workspaceWrite` 或更小权限范围，不默认开放高危命令执行。

### 3.3 会话容器与业务容器解耦

Discord thread 是用户界面容器，Codex thread 是 AI 会话容器，两者通过状态库稳定映射。

### 3.4 事件驱动而不是轮询驱动

所有回复、审批、状态变化、turn 生命周期都基于 `app-server` 事件流驱动。

### 3.5 易恢复

worker 可以被销毁；会话状态不能丢。

## 4. 总体架构

```text
Discord Guild
  -> Category: codex
  -> Forum Channel: 一个项目 / 仓库
  -> Forum Thread: 一个 Discord 会话线程

Discord Bot Service
  -> discord.py Client
  -> Session Router
  -> Worker Manager
  -> Approval Router
  -> Artifact Manager
  -> State DB

Worker Runtime
  -> AsyncCodex / AppServerClient
  -> `codex app-server --listen stdio://`
  -> thread / turn / review / approval 调用

Storage
  -> SQLite/Postgres：状态与审计
  -> Local FS / Object Storage：日志、补丁、附件、快照
```

## 5. 会话模型设计

## 5.1 Discord 对象映射

- `Guild`：团队边界
- `Category`：Codex 管理边界
- `Forum Channel`：一个工作区或一个仓库
- `Forum Thread`：一个具体 Codex 会话

推荐原因：

- 与 Discord 协作模型天然贴合
- 用户对“一个讨论串对应一个任务”有直觉
- 方便按项目划分工作目录、默认模型、审批权限和审计范围

## 5.2 Codex 对象映射

- 一个 Discord Forum Thread 对应一个显式初始化的 Codex 会话入口
- 只有执行 `/codex session new` 或 `/codex session resume` 成功后，后续用户消息才会触发 `turn`
- 如果上一个 turn 仍在运行，则新消息走 `turn/steer`

## 5.3 状态映射

| Discord | Codex | 含义 |
|---|---|---|
| Forum Thread | Thread | 持久会话 |
| 普通用户消息 | User input item | 用户输入 |
| Bot 正在编辑的消息 | agentMessage delta 聚合面板 | 运行中输出 |
| 按钮审批消息 | server request | 审批交互 |

## 6. 组件设计

## 6.1 Discord Bot 层

职责：

- 接收 slash command
- 接收线程内普通消息
- 管理按钮点击与 modal 提交
- 在 Discord 渠道中输出状态、摘要、审批和附件

建议命令：

- `/codex project add`
- `/codex project list`
- `/codex new`
- `/codex status`
- `/codex interrupt`
- `/codex review`
- `/codex settings`
- `/codex archive`

## 6.2 Session Router

职责：

- 识别当前 Discord thread 属于哪个 workspace
- 读取或创建 `discord_thread_id -> codex_thread_id` 映射
- 判断当前线程是否已初始化，并在已初始化时决定消息应调用 `turn/start` 还是 `turn/steer`

关键逻辑：

- 若线程未初始化：拒绝普通消息并提示先执行 `/codex session new` 或 `/codex session resume`
- 若有 Codex thread 且无 active turn：`turn/start`
- 若有 active turn：`turn/steer`

## 6.3 Worker Manager

职责：

- 为活跃 Discord 会话租用独立 worker
- 控制 worker 的生命周期、回收与恢复

原因：

Codex Python SDK 当前实验实现中，一个 client 同时只适合一个 active turn consumer，因此不能把所有 Discord 会话硬塞进同一个 client。

建议策略：

- 每个活跃 Discord thread 对应一个 worker
- worker 空闲 10 到 20 分钟自动回收
- 下一次会话恢复时重新 `thread/resume`

## 6.4 Approval Router

职责：

- 消费 `item/commandExecution/requestApproval`
- 消费 `item/fileChange/requestApproval`
- 将 `requestId` 与 Discord 消息按钮绑定
- 把 Discord 按钮决策回写到 `app-server`

展示策略：

- 命令审批显示：命令、cwd、原因、附加权限
- 文件审批显示：变更文件、diff 摘要、原因

## 6.5 Artifact Manager

职责：

- 保存长日志
- 保存 diff 文件
- 保存运行快照
- 按需将结果转成 Discord 附件

原因：

Discord 消息长度有限，不适合承载完整 shell 输出和大 diff。

## 6.6 Audit & Observability

职责：

- 审批留痕
- 指令留痕
- 会话状态留痕
- worker 崩溃和恢复留痕

输出建议：

- 结构化 JSON 日志
- Sentry 或等价错误采集
- Prometheus 指标或等价监控

## 7. 与 app-server 的交互设计

## 7.1 初始化

worker 启动后：

1. 启动 `codex app-server --listen stdio://`
2. 发送 `initialize`
3. 发送 `initialized`

## 7.2 新会话

1. Discord 创建 Forum Thread
2. Bot 根据 workspace 配置调用 `thread/start`
3. 记录 `discord_thread_id -> codex_thread_id`

## 7.3 发送消息

### 普通 turn

1. 用户发消息
2. Bot 创建一条“运行中”消息
3. 调用 `turn/start`
4. 消费事件流
5. 聚合 `item/agentMessage/delta`
6. 节流编辑运行中消息
7. `turn/completed` 后固化结果

### 追加 steer

1. 当前 active turn 未结束
2. 用户追加消息
3. 调用 `turn/steer`
4. 继续消费同一 turn 的事件流

## 7.4 审批流

1. 收到 `item/commandExecution/requestApproval` 或 `item/fileChange/requestApproval`
2. 在线程中发送审批消息和按钮
3. 用户点击按钮
4. Bot 将 decision 回写
5. 监听 `serverRequest/resolved`
6. 监听最终 `item/completed`

## 7.5 中断

1. 用户执行 `/codex interrupt`
2. Bot 调用 `turn/interrupt`
3. 等待 `turn/completed(status=interrupted)`

## 7.6 Code Review

1. 用户执行 `/codex review`
2. Bot 调用 `review/start`
3. 流式回传 review 内容

## 8. 数据模型

## 8.1 workspaces

字段建议：

- `id`
- `guild_id`
- `forum_channel_id`
- `name`
- `cwd`
- `default_model`
- `default_reasoning_effort`
- `sandbox_policy_json`
- `approval_policy`
- `created_at`
- `updated_at`

## 8.2 discord_sessions

字段建议：

- `discord_thread_id`
- `workspace_id`
- `codex_thread_id`
- `active_turn_id`
- `status`
- `last_bot_message_id`
- `created_at`
- `updated_at`

## 8.3 pending_requests

字段建议：

- `request_id`
- `discord_thread_id`
- `codex_thread_id`
- `turn_id`
- `item_id`
- `request_type`
- `available_decisions_json`
- `message_id`
- `created_at`

## 8.4 artifacts

字段建议：

- `id`
- `codex_thread_id`
- `turn_id`
- `kind`
- `path`
- `size`
- `created_at`

## 8.5 audit_events

字段建议：

- `id`
- `guild_id`
- `discord_thread_id`
- `actor_id`
- `action`
- `payload_json`
- `created_at`

## 9. 安全设计

## 9.1 默认权限

默认建议：

- `sandboxPolicy = workspaceWrite`
- `writableRoots` 仅包含项目目录
- 不开放 `dangerFullAccess`
- 不开放 `thread/shellCommand`

原因：

`thread/shellCommand` 是 unsandboxed full access，不继承 thread sandbox，不适合作为 Discord 对外能力默认开放。

## 9.2 Discord 侧权限

建议至少区分：

- `codex-admin`
- `codex-maintainer`
- `codex-user`

建议策略：

- 普通用户可发起会话、继续会话
- 只有 `admin / maintainer` 可审批高风险动作
- 只有 `admin` 可修改项目工作区配置

## 9.3 Credential 隔离

建议：

- Bot 使用专用 `CODEX_HOME`
- 不直接复用个人日常 `~/.codex`
- 认证文件、日志、状态目录与运行项目隔离

## 9.4 审计

所有这些动作都需要审计：

- 创建项目
- 创建会话
- 发起 turn
- 执行 interrupt
- 接受 / 拒绝审批
- 修改默认模型与权限策略

## 10. 性能与响应设计

## 10.1 响应速度目标

目标不是“最小总耗时”，而是“更快让用户感知系统已开始工作”。

建议策略：

- 用户消息到达后立即回复一个运行中占位消息
- 每 800ms 到 1500ms 节流更新正文
- 长输出按块写入附件或摘要，不强行单消息承载

## 10.2 worker 策略

建议：

- 热 worker 仅保留活跃会话
- 冷恢复依赖 `thread/resume`
- 避免创建全局单 client 瓶颈

## 11. 部署设计

## 11.1 部署形态

建议：

- 单机单实例起步
- Python bot 进程 + 本地 Codex CLI
- SQLite 起步，后续可迁移到 Postgres

## 11.2 目录约定

建议：

```text
runtime/
  state/
  artifacts/
  logs/
  codex-home/
```

## 11.3 进程模型

建议：

- 主进程：Discord Bot + 调度器
- 子进程：每个 worker 启动自己的 `codex app-server`

## 12. MVP 范围

第一阶段只做：

- 项目注册
- 会话创建
- `turn/start`
- `turn/steer`
- 流式文本回显
- 命令审批
- 文件审批
- 中断
- SQLite 状态保存

第二阶段再做：

- `/review`
- 图片输入
- 附件解析
- 审计查询
- 多实例部署

## 13. 风险

### 13.1 SDK 稳定性风险

Codex Python SDK 当前仍是 experimental，需要控制版本并避免盲目升级。

### 13.2 Discord 消息长度与频率限制

需要对编辑频率、文本长度、附件策略做严格节流。

### 13.3 worker 数量膨胀

高并发下需限制同一 guild / workspace 的最大活跃 worker 数。

### 13.4 本地权限外溢

若工作区、`CODEX_HOME`、artifact 路径未隔离，可能造成越权访问。

## 14. 为什么不是 OpenClaw + Codex ACP

本项目不采用 `OpenClaw + Codex ACP` 作为主方案，原因如下：

- 会话层多一层 ACP/Gateway bridge，链路更长
- tool/system history 重建不完整
- 审批模型不是 Codex 原生 `app-server` 审批模型
- 安全边界更依赖宿主与 OpenClaw 运行时
- 未来若要完整暴露 Codex 专属能力，仍需回到原生接口

OpenClaw 适合：

- 快速验证
- 低代码接入
- 已有 OpenClaw 基建的团队

本方案更适合：

- 长期产品化
- 团队协作开发机器人
- 强审计与强权限控制场景

## 15. 实施建议

实施顺序建议：

1. 先完成状态库与 Session Router
2. 再完成 worker 生命周期
3. 再完成流式消息回显
4. 最后补审批与审计

不要一开始就做：

- 多租户
- 多实例分布式调度
- 复杂 MCP 网关
- 过于激进的命令能力开放

## 16. 参考资料

### Codex

- OpenAI Codex `app-server` README
- OpenAI Codex Python `app-server` SDK README
- OpenAI Codex Python SDK getting started

### Discord

- Discord Application Commands
- Discord Message Components
- Discord Channel / Thread / Forum 相关文档

### 社区参考

- `discodex`
- `luna-chat`

---

本设计文档是评审版。若评审通过，下一步建议输出：

- 目录结构设计
- 数据库 schema 初稿
- 模块职责图
- 首批实现任务拆解
