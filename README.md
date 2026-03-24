# Codex Discord App Server Bot

基于 `Discord + Codex app-server` 的机器人方案仓库。

当前仓库能力：

- 已支持工作区注册、会话初始化、线程内流式输出
- 已支持按当前工作区列出和恢复历史 Codex 会话
- 已支持运行中 turn 的 `turn/steer`
- 已支持运行中 turn 的 `turn/interrupt`
- 已支持复用本地 `codex` CLI、自身 `config.toml` 和项目 `AGENTS.md`

文档入口：

- [Discord + Codex app-server 方案设计](./docs/discord-codex-app-server-design.md)

运行原则：

- bot 只负责把 Discord 论坛频道绑定到本地项目目录
- Codex 行为配置由你本机已有的 `codex` 配置接管
- 只要 `/codex project add` 传入的是项目根目录，Codex 会按原生机制读取该项目的 `AGENTS.md`

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
3. 在 Discord 服务器里执行 `/codex project add`，把论坛频道绑定到项目根目录。
4. 在该论坛频道中新建线程，执行 `/codex session new`。
5. 若需要恢复历史会话，可执行 `/codex session list` 和 `/codex session resume`。
6. 若需要会话整理，可执行 `/codex session detach`、`/codex session archive`、`/codex session unarchive`。
7. 直接在论坛线程里发文本消息或图片附件即可。
8. 如果 Codex 正在执行，再发新消息会走 `turn/steer`；需要停止时可以点击“打断”按钮。

目录结构：

- `src/codex_discord_bot/discord/`：Discord bot、命令、视图、消息处理
- `src/codex_discord_bot/codex/`：Codex worker、worker pool、会话路由
- `src/codex_discord_bot/persistence/`：数据库模型与 repository
- `src/codex_discord_bot/services/`：工作区、会话、审计等业务服务
- `migrations/`：Alembic 迁移
- `scripts/`：开发、命令同步、健康检查

当前功能边界：

- 已支持：工作区注册、历史会话列出与恢复、会话解绑与归档管理、流式消息渲染、图片附件输入、`turn/steer`、`turn/interrupt`
- 未支持：非图片附件输入、`review/start`

设计目标：

- 在 Discord 中以线程形式承载 Codex 会话
- 保留 Codex 原生 `thread / turn / item` 能力
- 支持流式输出、恢复、审计与权限隔离
- 避免引入额外 ACP/OpenClaw 网关层带来的能力折损
