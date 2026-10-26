# Claude Translate

为 Claude Code 提供可单独启停的中文输入、英文模型交互和中文回复显示。DeepSeek Flash 只负责翻译，原有 Claude 服务仍负责执行任务。

## 使用

安装后，在需要工作的项目目录运行：

```sh
claude
```

输入中文即可。输入翻译完成后才会发送给 Claude；英文回复按完成的行批次翻译显示。代码块、行内代码、可识别路径和链接保持原样。模型生成的文件仍遵循用户要求的语言。

常用 Claude 参数可以直接传入，例如 `claude --resume`、`claude -p '解释递归'`。可通过 `claude --translation-config /path/to/.env.local` 指定另一份翻译配置。`claude --help`、`claude --version` 和管理子命令直接进入原生程序，无需调用翻译 API。

诊断会调用真实 DeepSeek API，产生少量翻译费用：

```sh
claude-translate doctor
```

关闭当前会话即停止本次代理；下次运行 `claude` 会重新启用翻译。`claude-original` 可直接运行不带翻译的原版。翻译组件不修改全局 Hook、原模型、工具权限、shell alias 或后台启动项。

### 子代理

Claude Code 原生 `Agent` 子代理内部保持英文，不翻译内部输入、过程消息和工具调用。前台子代理完成后，主会话显示一份“子代理最终报告”的中文阅读副本；后台子代理则在结果通知回到主会话时显示。主模型收到的工具结果和交接内容仍保留原文，主会话的日常回复继续照常翻译。

支持识别 `SubagentHandback` 的真正报告，避免把结束语误当最终内容；相同交接通知会去重。翻译失败时保留原有英文结果，不阻断任务。格式或字面内容保护校验失败时，最多在原超时预算内重试一次。较长报告遵循 Claude 原生 Hook 输出的折叠和文件预览规则。

升级后需新启动 `claude` 才会加载新增 Hook；已有任务可以结束会话后用 `claude --resume` 继续。外部 CLI 自行启动的代理及 Agent Teams 的消息通道不属于这里的原生 Agent 交接范围。

## 安装与配置

依赖：macOS/Linux、Python 3.12 及以上、uv，以及支持 `MessageDisplay` 的 Claude Code。本机已在 Claude Code 2.1.299 上完成验证；Linux 尚未验证。

在本项目目录安装独立工具环境：

```sh
uv tool install --reinstall .
```

将 `.env.example` 中的配置填写到 `~/.config/claude-translate/.env.local`，目录权限设为 700，文件权限设为 600。`DEEPSEEK_API_KEY` 使用真实值，其他字段通常保留默认值。项目根 `.env.local` 可指向该文件，已加入忽略规则。

仓库只跟踪使用占位符的 `.env.example`。根目录的其他环境配置、翻译数据库及旁路文件、日志和临时运行 JSON 均已忽略；Python 缓存由各目录自己的 `.gitignore` 管理。真实密钥不要写入源码、文档或测试样本。

项目级私有 Claude 配置 `.claude/settings.local.json` 及其备份也不会入库；可共享的 `.claude/settings.json` 仍可提交，但其中不要填写真实凭据。

配置完成后启用默认命令接管：

```sh
claude-translate enable
```

接管仅替换 `~/.local/bin/claude` 的原生符号链接，原始链接目标记录在专用目录的 `command.json`，原生二进制不变。接管状态不一致或 `claude-original` 已存在时会报错，不覆盖其他修改。升级 Claude 原生版本前先停用接管，升级完成后可重新启用。

`deepseek-flash` 是官方 API 标识，2026-10-26 对应 DeepSeek-V4.1-Flash-1017；这是会随官方更新的模型别名，不是固定权重版本。翻译请求关闭思考模式。

默认从用户及项目 Claude settings 的 `env` 解析原上游地址。复杂配置或托管环境可在专用配置设置 `CLAUDE_TRANSLATE_UPSTREAM_BASE_URL`，明确原上游地址。仅支持 Anthropic Messages 格式；不支持 Bedrock、Vertex、Foundry、远程会话、后台运行、`--bare` 或额外 `--settings`。

## 停用与卸载

单次使用原版：运行 `claude-original`。

退出所有翻译会话后，恢复原来的 `claude` 命令：

```sh
claude-translate disable
```

确认恢复后，卸载命令和独立 Python 环境：

```sh
uv tool uninstall claude-translate
```

如果同时要删除翻译密钥和缓存，可以删除专用目录：

```sh
rm -r ~/.config/claude-translate
```

上述卸载不删除源码，也不更改 Claude Code 版本。此次按用户要求进行的 Claude Code 降级和关闭自动更新属于独立环境操作，不由翻译组件自动撤销。

## 数据和故障边界

- 自然语言输入和回复会发送给 DeepSeek。代码块等保护片段由本地占位符替换，不发送其内容给翻译模型。未加代码标记的日志、引用和代码无法保证自动识别，需用代码围栏或反引号明确标记。
- 用户原始输入仍由 Claude Code 按自身行为记录。Claude 的原始回复与会话 transcript 保留英文；中文仅用于显示。历史 transcript、权限确认菜单和工具结果不属于显示翻译范围。
- 输入译文保存在专用目录的 `prompt-cache.sqlite`，权限 600，键为原文哈希，值为英文译文。这样 `--resume` 可复用同一译文；旧的非翻译会话中从未登记过的中文历史不会被自动翻译。缓存包含对话内容，可随专用目录一起删除。
- 输入翻译失败会阻止本轮提交；回复翻译失败显示英文原文。输入替换采用精确匹配，不对协议内部文本做模糊替换。特殊客户端改写了输入包装时，需要补充兼容验证。
- 翻译会增加延迟。短回复通常增加一次输入翻译和一次输出翻译；长回复会产生多次显示翻译请求。
- 原 Claude 上游的状态码、流式响应及工具数据透传，DeepSeek 密钥不会发送给 Claude 上游。本地服务只监听随机 loopback 端口，并验证本次启动的随机令牌。

## 组件与验证

`config` 管理配置值；`DeepSeekTranslator` 管理翻译与缓存；`ProtectedText` 保护字面片段；`PromptRegistry` 登记及精确替换输入；`ProxyServer` 代理协议；`DisplayTranslator` 维护跨批次代码围栏；`HookClient` 接入 Claude hooks；`Launcher` 管理进程和临时配置；`CommandIntegration` 管理默认命令接管与恢复。HTTP 客户端及数据库连接通过构造参数显式传递。

`AgentContext` 区分主会话和子代理上下文，`SubagentReports` 管理交接时机和去重。状态接口提供 `subagent_inputs_skipped`、`subagent_display_skipped`、`subagent_reports_displayed`、`subagent_report_failures` 等计数。部分 Claude 运行方式原本就不对内部消息触发显示 Hook，此时跳过计数为零是正常的。

运行本地测试：

```sh
uv run pytest -q
```

运行真实 DeepSeek 与 Claude API 测试（产生费用）：

```sh
CLAUDE_TRANSLATE_LIVE=1 uv run pytest -q
```

测试使用真实 SQLite、真实 HTTP 服务和真实 CLI，不使用 mock、stub 或 fake。开发过程及验证证据见 `docs/`。
