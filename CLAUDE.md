# Claude Translate

独立的 Claude Code 双向翻译组件。用户通过 `claude-zh` 开启，本地代理与 Hook 仅作用于本次会话。

## 维护约束

- 配置对象只放配置值；HTTP 客户端、数据库连接、缓存与进程等运行时资源显式传参。
- 保留原有 Claude 上游、权限与工具协议。只替换已由输入 Hook 登记的用户文本。
- 凭据只在被忽略且权限为 600 的 `.env.local`；禁止把密钥写入源码、测试、日志、文档或提交。
- 用 uv 管理依赖；先写契约测试再实现。不得使用 mock、stub、fake 或替代命令模拟外部依赖。
- 本地验证：`uv run pytest -q`、`uv run ruff check .`。真实 API 用例通过 `CLAUDE_TRANSLATE_LIVE=1` 显式开启，会产生费用。
- 文档用简体中文，代码注释、docstring 和提交消息用英文。
