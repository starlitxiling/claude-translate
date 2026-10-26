# 需求

用户要求给终端 Claude Code 加双向翻译：中文请求先翻成英文发送，英文回复翻成中文显示。使用 DeepSeek-V4.1-Flash-1017；用户已提供 API 凭据并明确授权先实现链路。

组件必须独立，方便取消。不得将密钥写入版本控制、日志或文档。原有 Claude 模型与服务保留，DeepSeek 只负责翻译。

已决：采用本地请求代理和回复显示 Hook；使用独立启动入口，避免持久改写用户现有 Claude 设置；项目落在 `/Users/wujie/Work/claude-translate`。
