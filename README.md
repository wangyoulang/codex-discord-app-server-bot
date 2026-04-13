# Codex Discord App Server Bot

这是一个把 Discord 论坛频道绑定到本地项目目录，并在论坛线程中与 Codex 交互的机器人。

## 环境准备

1. 安装 Python 3.11+ 与 `uv`
2. 本机可直接执行 `codex`
3. 在 Discord Developer Portal 创建应用与 Bot，并开启：
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

常用可选项：

- `DISCORD_GUILD_ID`：只向指定服务器同步命令时使用
- `CODEX_HOME`：让 bot 使用指定的 Codex 配置目录
- `DATABASE_URL`：修改数据库位置

完整示例见 `.env.example`。

## 使用流程

1. 启动 bot 后，在 Discord 服务器中执行 `/codex project add`
2. 在目标论坛频道中新建线程
3. 在线程中执行 `/codex session new`
4. 执行 `/codex session status` 确认当前线程已绑定会话
5. 如需恢复历史会话，执行 `/codex session list` 后再用 `/codex session resume`
6. 初始化完成后，直接在线程里发送文本消息或图片附件

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
- `/codex admin sync`

## 注意事项

- 同一个线程在恢复其他历史会话前，通常需要先执行 `/codex session detach`
- 只有已初始化会话的线程才会把消息发送给 Codex
- Codex 运行中再次发送消息时，会进入当前会话的继续交互流程
- `review` 命令当前不要使用
