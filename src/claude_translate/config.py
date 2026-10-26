import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

DEFAULT_CONFIG = Path.home() / ".config" / "claude-translate" / ".env.local"


def validate_url(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme not in {"https", "http"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("服务地址必须是没有凭据、查询参数或片段的 HTTP(S) URL")
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("非本机服务必须使用 HTTPS")
    return value.rstrip("/")


@dataclass(frozen=True)
class TranslationConfig:
    api_key: str = field(repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    timeout: float = 25
    upstream_override: str = ""

    def __post_init__(self):
        if not self.api_key.strip() or self.api_key.startswith("your_"):
            raise ValueError("请在专用配置中设置 DEEPSEEK_API_KEY")
        validate_url(self.base_url)
        if not self.model.strip() or not 1 <= self.timeout <= 120:
            raise ValueError("模型不能为空，超时必须在 1–120 秒之间")
        if self.upstream_override:
            validate_url(self.upstream_override)

    @classmethod
    def load(cls, path: Path):
        if path.exists() and path.stat().st_mode & 0o077:
            raise ValueError("翻译配置文件权限必须为 600")
        values = {**dotenv_values(path), **os.environ}
        return cls(
            api_key=values.get("DEEPSEEK_API_KEY", ""),
            base_url=values.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=values.get("DEEPSEEK_MODEL", "deepseek-flash"),
            timeout=float(values.get("TRANSLATION_TIMEOUT", "25")),
            upstream_override=values.get("CLAUDE_TRANSLATE_UPSTREAM_BASE_URL", ""),
        )
