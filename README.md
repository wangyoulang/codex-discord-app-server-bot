# Codex / Claude Discord Bot

这是一个把 Discord 论坛频道绑定到本地项目目录，并在论坛线程中与 Codex 或 Claude 交互的机器人。当前分支支持按 provider 暴露独立根命令。

## 环境准备

1. 安装 Python 3.11+ 与 `uv`
2. 如果使用 Codex，本机需要可直接执行 `codex`
3. 如果使用 Claude，本机需要可直接执行 `claude`
4. 在 Discord Developer Portal 创建应用与 Bot，并开启：
   - `MESSAGE CONTENT INTENT`
   - `GUILD MEMBERS INTENT`

## 快速启动

```bash
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run python scripts/register_commands.py
uv run python scripts/dev.py
```

## 配置说明

最少需要在 `.env` 中填写：

```env
DISCORD_BOT_TOKEN=
DISCORD_APPLICATION_ID=
```

只使用 Codex 时，常用配置通常只有：

- `ENABLE_CODEX_COMMAND=true`
- `CODEX_BIN`
- `CODEX_HOME`

启用 Claude 时，至少还需要：

- `ENABLE_CLAUDE_COMMAND=true`
- `CLAUDE_BIN`
- `CLAUDE_AUTH_MODE`
- `CLAUDE_API_KEY` 或 `CLAUDE_AUTH_TOKEN`

完整示例见 `.env.example`。

## 使用流程

1. 启动 bot 后，根据要使用的 provider 执行 `/codex project add` 或 `/claude project add`
2. 在目标论坛频道中新建线程
3. 在线程中执行 `/codex session new` 或 `/claude session new`
4. 如需查看当前线程状态，执行对应 provider 的 `session status`
5. 初始化完成后，直接在线程里发送文本消息或图片附件
6. 如需恢复历史会话，执行对应 provider 的 `session list` 与 `session resume`

## 可用命令

- `/codex project add`
- `/codex project list`
- `/codex session new`
- `/codex session status`
- `/codex session list`
- `/codex session resume`
- `/codex session detach`
- `/codex session archive`
- `/codex session unarchive`
- `/claude project add`
- `/claude project list`
- `/claude session new`
- `/claude session status`
- `/claude session list`
- `/claude session resume`
- `/claude session detach`
- `/claude session archive`
- `/claude session unarchive`
- `/codex admin sync`
- `/claude admin sync`

## 注意事项

- `/claude` 根命令只有在 `ENABLE_CLAUDE_COMMAND=true` 时才会注册
- 同一个线程在恢复其他历史会话前，通常需要先执行对应 provider 的 `session detach`
- 只有已初始化会话的线程才会把消息发送给当前 provider
- `review` 命令当前不要使用
