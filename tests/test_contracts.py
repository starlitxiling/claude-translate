import copy
import sqlite3

import aiohttp
import pytest

from claude_translate.config import TranslationConfig
from claude_translate.deep_seek_translator import DeepSeekTranslator
from claude_translate.display_translator import DisplayTranslator, FenceState
from claude_translate.prompt_registry import PromptRegistry
from claude_translate.protected_text import ProtectedText
from claude_translate.proxy_server import clean_headers, proxy_url


def test_code_paths_and_links_are_restored_exactly():
    original = '检查 `/tmp/中文.py` 和 https://a.test/中文?q=1\n```python\nprint("你好")\n```\n继续'
    text = ProtectedText(original)
    assert "print(" not in text.masked
    assert "https://" not in text.masked
    assert text.restore(text.masked) == original
    translated = (
        text.masked.replace("检查", "Check").replace(" 和 ", " and ").replace("继续", "Continue")
    )
    assert 'print("你好")' in text.restore(translated)
    assert "/tmp/中文.py" in text.restore(translated)


def test_corrupt_protected_tokens_fail_closed():
    text = ProtectedText("检查 `keep_me`")
    with pytest.raises(ValueError):
        text.restore("Check missing token")
    with pytest.raises(ValueError):
        text.restore(text.masked + text.masked)


def test_unclosed_fence_is_preserved():
    text = ProtectedText('说明\n```sh\necho "中文"\n')
    assert "echo" not in text.masked
    assert text.restore(text.masked) == '说明\n```sh\necho "中文"\n'


def test_plain_paths_and_xml_reminders_are_protected():
    original = "读取 /tmp/中文.txt\n<system-reminder>保持中文</system-reminder>"
    text = ProtectedText(original)
    assert "/tmp/" not in text.masked
    assert "保持中文" not in text.masked
    assert text.restore(text.masked) == original


def test_registry_changes_only_registered_user_text():
    registry = PromptRegistry(sqlite3.connect(":memory:"))
    registry.register("检查代码", "Inspect the code")
    body = {
        "system": "检查代码",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "检查代码"},
                    {"type": "image", "source": {"data": "检查代码"}},
                ],
            },
            {"role": "assistant", "content": "检查代码"},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "content": "检查代码", "tool_use_id": "1"},
                    {"type": "text", "text": "检查代码"},
                ],
            },
        ],
    }
    before = copy.deepcopy(body)
    result, count = registry.rewrite(body)
    assert count == 1
    assert body == before
    assert result["system"] == before["system"]
    assert result["messages"][0]["content"][0]["text"] == "Inspect the code"
    assert result["messages"][0]["content"][1] == before["messages"][0]["content"][1]
    assert result["messages"][1:] == before["messages"][1:]


def test_registry_preserves_reminder_wrapping_and_no_substring_replacement():
    registry = PromptRegistry(sqlite3.connect(":memory:"))
    registry.register("你好", "Hello")
    body = {
        "messages": [
            {"role": "user", "content": "<system-reminder>你好</system-reminder>\n\n你好\n"},
            {"role": "user", "content": "文档里有你好这个词"},
        ]
    }
    result, count = registry.rewrite(body)
    assert count == 1
    assert result["messages"][0]["content"] == "<system-reminder>你好</system-reminder>\n\nHello\n"
    assert result["messages"][1] == body["messages"][1]


def test_fence_state_spans_display_batches():
    state = FenceState()
    assert state.split("Explanation\n```python\n") == [
        (False, "Explanation\n"),
        (True, "```python\n"),
    ]
    assert state.split('print("中文")\n') == [(True, 'print("中文")\n')]
    assert state.split("```\nDone\n") == [(True, "```\n"), (False, "Done\n")]
    assert state.marker is None


def test_fence_state_respects_delimiter_lengths():
    state = FenceState()
    assert state.split("````\n```\n") == [(True, "````\n```\n")]
    assert state.marker == "````"
    assert state.split("````\n") == [(True, "````\n")]
    assert state.marker is None


def test_missing_key_fails_at_config_construction():
    with pytest.raises(ValueError):
        TranslationConfig(api_key="")


def test_registry_reuses_translation_after_reopening(tmp_path):
    path = tmp_path / "cache.sqlite"
    with sqlite3.connect(path) as connection:
        registry = PromptRegistry(connection)
        registry.register("不要修改文件", "Do not modify files")
    with sqlite3.connect(path) as connection:
        registry = PromptRegistry(connection)
        assert registry.lookup("不要修改文件") == "Do not modify files"
        body = {"messages": [{"role": "user", "content": "不要修改文件"}]}
        rewritten, count = registry.rewrite(body)
        assert count == 1
        assert rewritten["messages"][0]["content"] == "Do not modify files"


def test_empty_text_is_not_replaced():
    registry = PromptRegistry(sqlite3.connect(":memory:"))
    registry.register("", "")
    assert registry.replace_text("\n\n") == ("\n\n", 0)


def test_header_filter_removes_connection_tokens_and_local_auth():
    result = clean_headers(
        {
            "Host": "local",
            "Connection": "keep-alive, X-Remove",
            "X-Remove": "secret",
            "X-Claude-Translate-Token": "local-token",
            "Authorization": "Bearer original",
            "anthropic-version": "2023-06-01",
        }
    )
    assert result == {"Authorization": "Bearer original", "anthropic-version": "2023-06-01"}


def test_proxy_path_keeps_prefix_and_query():
    assert proxy_url("https://example.com/anthropic", "/anthropic/v1/messages?beta=true") == (
        "https://example.com/anthropic/v1/messages?beta=true"
    )
    with pytest.raises(ValueError):
        proxy_url("https://example.com/anthropic", "//evil.test/x")


@pytest.mark.asyncio
async def test_invalid_final_display_event_leaves_no_state():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-validation"), client)
        display = DisplayTranslator(translator)
        with pytest.raises((KeyError, ValueError)):
            await display.translate({"message_id": "invalid", "index": 0, "final": True})
        assert not display.states
        assert translator.calls == 0
