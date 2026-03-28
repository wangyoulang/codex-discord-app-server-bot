# Codex / Claude Discord Bot

基于 `Discord + Codex app-server / Claude Code SDK` 的机器人方案仓库。

当前仓库能力：

- 已支持按工作区注册本地项目目录
- 已支持 `/codex` 与 `/claude` 双根命令
- 已支持线程内流式输出、会话列表/恢复、打断、审批与图片输入
- 已支持 Codex 原生 `turn/steer`、`turn/interrupt`
- 已支持 Claude 基于本地 `claude` CLI 的流式会话与会话恢复
- 已支持 Claude 默认走 API Key 模式，并可选接第三方兼容网关 `base_url`
- 已支持通过 `.env` 显式控制 Claude `thinking`、额外 env 透传与额外 CLI 参数透传
- 已支持为 Claude 配置无人工审批模式，并切换到 bot 托管的独立 settings 文件

文档入口：

- [Discord + Codex app-server 方案设计](./docs/discord-codex-app-server-design.md)

运行原则：

- bot 只负责把 Discord 论坛频道绑定到本地项目目录
- Codex 行为配置由你本机已有的 `codex` 配置接管
- Claude 运行目录来自工作区 `cwd`，并按 `CLAUDE_SETTING_SOURCES` 加载用户/项目/本地配置
- 只要 `/codex project add` 或 `/claude project add` 传入的是项目根目录，Provider 会按各自原生机制读取项目级规则文件

建议启动方式：

```bash
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run python scripts/register_commands.py
uv run python scripts/dev.py
```

使用说明：

1. 在 `.env` 里配置 Discord token 和 application id。
2. 可选设置 `CODEX_HOME`；不设置时会直接复用当前机器上 `codex` 默认使用的 home 和 `config.toml`。
3. 若要启用 Claude，请设置 `ENABLE_CLAUDE_COMMAND=true`，并至少配置 `CLAUDE_API_KEY`。
4. 若要让 Claude 走第三方网关，可额外设置 `CLAUDE_BASE_URL` 与 `CLAUDE_CUSTOM_HEADERS_JSON`。
5. 若要让 Claude 工具调用完全不经过 Discord 人工审批，请设置 `CLAUDE_APPROVAL_POLICY=auto_allow`，并同时设置 `CLAUDE_SETTINGS_MODE=managed`。
6. 当 `CLAUDE_SETTINGS_MODE=managed` 时，bot 会生成并使用独立的 Claude settings 文件；可选通过 `CLAUDE_MANAGED_SETTINGS_PATH` 指定输出路径。
7. 若要显式覆盖本机 Claude 的思考配置，可设置 `CLAUDE_THINKING_MODE`，当值为 `enabled` 时还需设置 `CLAUDE_THINKING_BUDGET_TOKENS`。
8. 若要把额外环境变量或 CLI 标志同步到 bot，可设置 `CLAUDE_EXTRA_ENV_JSON` 与 `CLAUDE_EXTRA_ARGS_JSON`。
9. 在 Discord 服务器里执行 `/codex project add` 或 `/claude project add`，把论坛频道绑定到项目根目录。
10. 在该论坛频道中新建线程，执行 `/codex session new` 或 `/claude session new`。
11. 若需要恢复历史会话，可执行对应 provider 的 `session list` 与 `session resume`。
12. 若需要会话整理，可执行对应 provider 的 `session detach`、`session archive`、`session unarchive`。
13. 直接在论坛线程里发文本消息或图片附件即可。
14. 如果 Codex 正在执行，再发新消息会走 `turn/steer`；Claude 当前默认不支持运行中追加输入，需等待完成或先打断。

目录结构：

- `src/codex_discord_bot/discord/`：Discord bot、命令、视图、消息处理
- `src/codex_discord_bot/codex/`：Codex worker、worker pool、会话路由
- `src/codex_discord_bot/claude/`：Claude worker、CLI/SDK 配置与流式适配
- `src/codex_discord_bot/providers/`：Provider 中立类型、事件与运行时
- `src/codex_discord_bot/persistence/`：数据库模型与 repository
- `src/codex_discord_bot/services/`：工作区、会话、审计等业务服务
- `migrations/`：Alembic 迁移
- `scripts/`：开发、命令同步、健康检查

当前功能边界：

- 已支持：工作区注册、历史会话列出与恢复、会话解绑与归档管理、流式消息渲染、图片附件输入、Codex `turn/steer`、Codex/Claude `interrupt`
- 未支持：非图片附件输入、`review/start`、Claude 运行中追加输入

设计目标：

- 在 Discord 中以线程形式承载 Codex / Claude 会话
- 保留 Codex 原生 `thread / turn / item` 能力，同时让 Claude 保持官方 SDK 语义
- 支持流式输出、恢复、审计与权限隔离
- 避免引入额外 ACP/OpenClaw 网关层带来的能力折损
