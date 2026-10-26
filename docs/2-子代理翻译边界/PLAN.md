# 实现计划

## 已核实接口

- Claude Code 2.1.299 真实只读子代理探针中，主输入触发 UserPromptSubmit，Agent 前后触发工具 Hook，SubagentStop 含 agent_id 和最终回复，前台 PostToolUse 的 tool_response.status 为 completed，content 包含最终文本。
- 该探针中 subagent 内部文本没有触发 MessageDisplay；仍加入明确的子代理上下文过滤，避免其他运行方式或后续版本把内部消息送去翻译。
- 官方另有 SubagentHandback 交接路径，真正报告位于 tool_input.message；Agent 的 content 可能只是交接提示，不能将其误当报告。
- systemMessage 是显示给用户的 Hook 输出，不使用 additionalContext 或 updatedToolOutput 修改模型上下文。

## 实现

1. 单独封装子代理上下文识别和最终报告处理。配置仍只包含配置值，翻译器及报告处理对象作为显式运行时依赖传递。
2. 带 agent_id 或明确子代理 transcript 路径的输入/显示事件直接跳过翻译，保留英文原文。agent_type 单独出现不作为判断依据，以兼容主会话的自定义 agent。
3. 顶层 PostToolUse Agent 的 completed 结果只翻译最终 text blocks。async_launched 只登记，不翻译启动状态；后台 SubagentStop 或 SubagentHandback 先保存英文报告，由主会话下一次 UserPromptSubmit 显示中文副本。该子代理结束 Hook 的 systemMessage 不会显示在主会话，已用真实后台任务核实。
4. 保存 Handback 的真实报告，前台在 Agent 交回时显示，后台在主会话收到结果时显示；不翻译内部工具结果或已登记的后台 task-notification 输入。相同交接去重，避免完成 Hook 重复收费和重复显示。输入翻译与后台报告显示使用两个并行 Hook，避免串行叠加超时。
5. 新增跳过次数、交接译文成功/失败计数，便于实际会话只读检查。翻译失败保留英文，不阻止子代理或主会话。
6. 使用带碰撞检查的短随机字面片段占位符，降低模型复制出错概率；交接报告的格式校验失败最多重试一次，两个请求共同受原翻译超时预算限制。

## 验证

先写契约测试：主会话、自定义主 agent、子代理内部、完成结果、后台启动、Handback、重复交接和失败路径。使用真实捕获事件作为样本；禁止 mock/stub/fake。

使用真实 Claude 子代理及 DeepSeek 验证：内部文本英文；最终报告中文提示；主模型仍看到英文结果；子代理内部翻译调用为零。验证正常和可执行的后台/Handback 路径，并明确未覆盖范围。完成独立审查、本地提交和安装；不重启用户正在执行任务的会话，新启动会话加载新增 Hook。
