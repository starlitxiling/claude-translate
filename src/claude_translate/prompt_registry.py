import copy
import hashlib
import re
import sqlite3


class PromptRegistry:
    """Only replace text explicitly registered by a user-submit hook."""

    def __init__(self, database: sqlite3.Connection):
        if database is None:
            raise TypeError("Database connection is required")
        self.database = database
        database.execute(
            "CREATE TABLE IF NOT EXISTS translations "
            "(source_hash TEXT PRIMARY KEY, translated TEXT NOT NULL)"
        )
        database.commit()

    @staticmethod
    def source_hash(text: str) -> str:
        return hashlib.sha256(text.strip().encode()).hexdigest()

    def lookup(self, original: str) -> str | None:
        row = self.database.execute(
            "SELECT translated FROM translations WHERE source_hash = ?",
            (self.source_hash(original),),
        ).fetchone()
        return row[0] if row else None

    def register(self, original: str, translated: str):
        if not original.strip():
            return
        self.database.execute(
            "INSERT OR REPLACE INTO translations VALUES (?, ?)",
            (self.source_hash(original), translated.strip()),
        )
        self.database.commit()

    def replace_text(self, text: str) -> tuple[str, int]:
        translated = self.lookup(text)
        if translated is not None:
            prefix = text[: len(text) - len(text.lstrip())]
            suffix = text[len(text.rstrip()) :]
            return prefix + translated + suffix, int(translated != text.strip())
        segments = re.split(r"(<system-reminder>.*?</system-reminder>)", text, flags=re.DOTALL)
        count = 0
        for index in range(0, len(segments), 2):
            segment = segments[index]
            if self.lookup(segment) is not None:
                segments[index], changes = self.replace_text(segment)
                count += changes
        return "".join(segments), count

    def rewrite(self, payload: dict) -> tuple[dict, int]:
        result = copy.deepcopy(payload)
        count = 0
        for message in result.get("messages", []):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                message["content"], changes = self.replace_text(content)
                count += changes
            elif isinstance(content, list):
                if any(block.get("type") == "tool_result" for block in content):
                    continue
                for block in content:
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        block["text"], changes = self.replace_text(block["text"])
                        count += changes
        return result, count
