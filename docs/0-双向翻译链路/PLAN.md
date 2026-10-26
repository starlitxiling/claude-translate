# 实现计划

用户已在本轮授权“先实现这条链路”，本计划细化此前讨论的本地代理与显示 Hook 方案，按该授权执行。

## 接口依据

- 本机 Claude Code 版本为 2.1.307；支持 `--settings` 和 `--append-system-prompt`。
- 官方 `MessageDisplay` 将显示文本与模型上下文分离；`UserPromptSubmit` 支持追加上下文、阻止发送，不支持直接替换输入。
- DeepSeek 官方聊天 API 模型标识为 `deepseek-flash`，目前对应 V4.1-Flash-1017；使用 `thinking.type=disabled`。
- 文档：https://code.claude.com/docs/en/hooks 、https://api-docs.deepseek.com/api/create-chat-completion/ 。

## 组件划分

1. 配置：只保存地址、模型、超时等值；HTTP 客户端、缓存、代理句柄等运行时对象通过参数显式传递。
2. 翻译器：独立 DeepSeek 客户端；保护 fenced code、inline code、链接地址、路径与协议包裹；验证保护片段完整性；输入失败阻止提交，输出失败保留英文。
3. 输入登记：UserPromptSubmit 提交原始用户输入到本地服务并缓存英文译文；不把翻译追加为额外上下文。代理仅替换已登记的用户文本，工具结果、系统提示和其他协议字段保持原样。
4. 代理：仅监听随机 loopback 端口；本次启动生成访问令牌；代理 Anthropic 消息及流式响应，移除本地认证字段；保持原始英文响应。
5. 显示：MessageDisplay 将自然语言译成中文；按 message_id 跟踪跨批次代码围栏状态，避免跨批次破坏代码。
6. 启动器：`claude-zh` 启动本地服务、生成临时 settings 并启动真实 `claude`；现有工具权限保持；进程结束清理服务和临时文件。凭据不传入命令行。提供诊断和卸载说明。

## 验证顺序

先写纯逻辑失败测试，再实现：保护片段逐字保留、翻译结果截断检测、精确用户消息替换、工具结果不变、响应代码围栏跨批次、配置缺失立即失败、URL 路径拼接与请求头过滤。

使用真实 DeepSeek API 验证双向翻译及代码保护，多次运行固定样本；使用真实 Claude CLI、真实上游验证输入代理、英文原始回复和中文显示链路；测试取消、启动失败和资源清理。禁止 mock、stub、fake 或替代上游服务。无法执行的验证明确记录。

## 交付与边界

源码、中文使用文档、测试、受保护的本地配置、独立启动命令。默认只影响用 `claude-zh` 启动的会话。关闭命令即停用；卸载独立包并删除专用配置即可取消。

只翻译对话，不保证模型所有上下文均为英文；源码、工具输出、项目规则保持原始内容。`MessageDisplay` 不翻译权限菜单、工具结果及历史 transcript。翻译增加网络延迟，具体时延以实测为准。
