import re
import uuid


class ProtectedText:
    """Remove literal spans from translation and enforce lossless restoration."""

    pattern = re.compile(
        r"(?P<fence>^[ \t]{0,3}(?P<ticks>`{3,}|~{3,})[^\n]*\n"
        r".*?(?:^[ \t]{0,3}(?P=ticks)[ \t]*(?:\n|$)|\Z))"
        r"|(?P<inline>`+[^`\n]+`+)"
        r"|(?P<reminder><system-reminder>.*?</system-reminder>)"
        r'|(?P<url>https?://[^\s<>\[\]"\u3000-\u303f]+)'
        r"|(?P<path>(?<![\w:])(?:~/|\./|\.\./|/)[\w./@+~%:=\-]+)",
        re.MULTILINE | re.DOTALL,
    )

    def __init__(self, source: str):
        self.parts: dict[str, str] = {}
        prefix = f"CT_LITERAL_{uuid.uuid4().hex}_"

        def replace(match):
            token = f"{prefix}{len(self.parts)}_END"
            self.parts[token] = match.group()
            return token

        self.masked = self.pattern.sub(replace, source)

    def restore(self, translated: str) -> str:
        positions = []
        for token in self.parts:
            if translated.count(token) != 1:
                raise ValueError("翻译未完整保留代码或路径占位符")
            positions.append(translated.index(token))
        if positions != sorted(positions):
            raise ValueError("翻译改变了代码或路径的顺序")
        for token, original in self.parts.items():
            translated = translated.replace(token, original)
        return translated

    def prose(self) -> str:
        text = self.masked
        for token in self.parts:
            text = text.replace(token, "")
        return text
