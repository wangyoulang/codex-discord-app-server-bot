# Codex Discord App Server Bot

基于 `Discord + Codex app-server` 的机器人方案仓库。

当前仓库阶段：

- 已包含第一版项目骨架
- 已包含数据库模型、迁移、Discord 命令框架和 Codex worker 骨架
- 已包含最小可用闭环：工作区注册、会话初始化、线程消息触发最小 `run()` 调用
- 目标是以 `Discord` 作为交互前端，以 `codex app-server` 作为 Codex 原生后端接口

文档入口：

- [Discord + Codex app-server 方案设计](./docs/discord-codex-app-server-design.md)

建议启动方式：

```bash
uv sync
cp .env.example .env
uv run python scripts/register_commands.py
uv run python scripts/dev.py
```

当前已落文件层次：

- `src/codex_discord_bot/discord/`：Discord bot、命令、视图、消息处理
- `src/codex_discord_bot/codex/`：Codex worker、worker pool、会话路由
- `src/codex_discord_bot/persistence/`：数据库模型与 repository
- `src/codex_discord_bot/services/`：工作区、会话、审计等业务服务
- `migrations/`：Alembic 迁移
- `scripts/`：开发、命令同步、健康检查

当前功能边界：

- 已支持：工作区注册、会话初始化、线程内文本消息触发最小 `thread/run()` 调用
- 未支持：真正的流式 delta 渲染、`turn/steer`、审批按钮回写、附件输入、`review/start`

设计目标：

- 在 Discord 中以线程形式承载 Codex 会话
- 保留 Codex 原生 `thread / turn / item` 能力
- 支持流式输出、审批、恢复、审计与权限隔离
- 避免引入额外 ACP/OpenClaw 网关层带来的能力折损
