# Codex Discord App Server Bot

基于 `Discord + Codex app-server` 的机器人方案仓库。

当前仓库阶段：

- 仅包含方案设计文档
- 尚未开始业务代码实现
- 目标是以 `Discord` 作为交互前端，以 `codex app-server` 作为 Codex 原生后端接口

文档入口：

- [Discord + Codex app-server 方案设计](./docs/discord-codex-app-server-design.md)

设计目标：

- 在 Discord 中以线程形式承载 Codex 会话
- 保留 Codex 原生 `thread / turn / item` 能力
- 支持流式输出、审批、恢复、审计与权限隔离
- 避免引入额外 ACP/OpenClaw 网关层带来的能力折损
