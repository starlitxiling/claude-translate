import asyncio
import copy
import json
import sqlite3
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from claude_translate.agent_context import AgentContext
from claude_translate.config import TranslationConfig
from claude_translate.deep_seek_translator import DeepSeekTranslator
from claude_translate.display_translator import DisplayTranslator
from claude_translate.prompt_registry import PromptRegistry
from claude_translate.proxy_server import LOCAL_HEADER, ProxyServer
from claude_translate.subagent_reports import SubagentReports


def captured_event(name):
    data = json.loads((Path(__file__).parent / "fixtures/subagent_foreground.json").read_text())
    return next(e for e in data["events"] if e["hook_event_name"] == name)


def test_real_main_display_and_custom_root_agent_are_not_subagents():
    event = captured_event("MessageDisplay")
    assert not AgentContext.is_subagent(event)
    event["agent_type"] = "custom-root"
    assert not AgentContext.is_subagent(event)


def test_subagent_identity_and_transcript_fallback():
    event = captured_event("MessageDisplay")
    event["agent_id"] = "child-id"
    assert AgentContext.is_subagent(event)
    del event["agent_id"]
    event["transcript_path"] = "/transcripts/session/subagents/agent-child.jsonl"
    assert AgentContext.is_subagent(event)


@pytest.mark.asyncio
async def test_subagent_display_does_not_call_translation_api():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-skip"), client)
        display = DisplayTranslator(translator)
        event = captured_event("MessageDisplay")
        event["agent_id"] = "child-id"
        assert await display.translate(event) == event["delta"]
        assert translator.calls == 0
        assert not display.states


@pytest.mark.asyncio
async def test_foreground_report_is_display_only_and_duplicate_safe():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-literals"), client)
        reports = SubagentReports(translator)
        event = captured_event("PostToolUse")
        event["tool_response"]["content"] = [{"type": "text", "text": "`git status`"}]
        original = copy.deepcopy(event)
        result = await reports.handle(event)
        assert "git status" in result["systemMessage"]
        assert "hookSpecificOutput" not in result
        assert "additionalContext" not in result
        assert event == original
        assert await reports.handle(event) == {}
        assert reports.stats["subagent_reports_displayed"] == 1
        assert translator.calls == 0


@pytest.mark.asyncio
async def test_background_launch_waits_for_completion():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-literals"), client)
        reports = SubagentReports(translator)
        event = captured_event("PostToolUse")
        event["tool_response"]["status"] = "async_launched"
        event["tool_response"].pop("content")
        assert await reports.handle(event) == {}
        stopped = captured_event("SubagentStop")
        stopped["last_assistant_message"] = "`final_report`"
        assert await reports.handle(stopped) == {}
        result = await reports.handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": event["session_id"],
                "prompt": "Continue",
            }
        )
        assert "final_report" in result["systemMessage"]
        assert await reports.handle(stopped) == {}
        assert reports.stats["subagent_reports_displayed"] == 1


@pytest.mark.asyncio
async def test_handback_report_takes_priority_over_closing_text():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-literals"), client)
        reports = SubagentReports(translator)
        complete = captured_event("PostToolUse")
        child_id = complete["tool_response"]["agentId"]
        handback = {
            "hook_event_name": "PostToolUse",
            "session_id": complete["session_id"],
            "agent_id": child_id,
            "tool_name": "SubagentHandback",
            "tool_input": {"message": "`actual_report`"},
            "tool_use_id": "handback-call",
        }
        assert await reports.handle(handback) == {}
        complete["tool_response"]["content"] = [{"type": "text", "text": "`handed_back_notice`"}]
        result = await reports.handle(complete)
        assert "actual_report" in result["systemMessage"]
        assert "handed_back_notice" not in result["systemMessage"]


@pytest.mark.asyncio
async def test_nested_agent_result_and_unrelated_stop_are_not_translated():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-skip"), client)
        reports = SubagentReports(translator)
        nested = captured_event("PostToolUse")
        nested["agent_id"] = "parent-subagent"
        assert await reports.handle(nested) == {}
        assert await reports.handle(captured_event("SubagentStop")) == {}
        assert translator.calls == 0


@pytest.mark.asyncio
async def test_resumed_agent_can_deliver_same_text_again():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-literals"), client)
        reports = SubagentReports(translator)
        first = captured_event("PostToolUse")
        first["tool_response"]["content"] = [{"type": "text", "text": "`same_report`"}]
        await reports.handle(first)
        pre = captured_event("PreToolUse")
        pre["tool_use_id"] = "second-call"
        pre["tool_input"]["resume"] = first["tool_response"]["agentId"]
        assert await reports.handle(pre) == {}
        second = copy.deepcopy(first)
        second["tool_use_id"] = "second-call"
        assert "same_report" in (await reports.handle(second))["systemMessage"]
        assert reports.stats["subagent_reports_displayed"] == 2


@pytest.mark.asyncio
async def test_real_http_service_skips_subagent_input_and_display():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-skip"), client)
        with sqlite3.connect(":memory:") as database:
            registry = PromptRegistry(database)
            server = ProxyServer(
                "https://api.anthropic.com",
                "local-test-token",
                client,
                translator,
                registry,
                DisplayTranslator(translator),
                SubagentReports(translator),
            )
            runner = web.AppRunner(server.application(), access_log=None)
            await runner.setup()
            try:
                await web.TCPSite(runner, "127.0.0.1", 0).start()
                base = f"http://127.0.0.1:{runner.addresses[0][1]}/__translate/"
                headers = {LOCAL_HEADER: "local-test-token"}
                async with client.post(
                    base + "register",
                    headers=headers,
                    json={"agent_id": "child", "prompt": "内部输入保持原样"},
                ) as res:
                    assert (await res.json())["skipped"]
                event = captured_event("MessageDisplay")
                event["agent_id"] = "child"
                async with client.post(base + "display", headers=headers, json=event) as res:
                    data = await res.json()
                    assert data["skipped"] and data["text"] == event["delta"]
                assert translator.calls == 0
                assert registry.lookup("内部输入保持原样") is None
                assert server.stats["subagent_inputs_skipped"] == 1
                assert server.stats["subagent_display_skipped"] == 1
            finally:
                await runner.cleanup()


@pytest.mark.asyncio
async def test_concurrent_duplicate_report_uses_one_notification():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-literals"), client)
        reports = SubagentReports(translator)
        event = captured_event("PostToolUse")
        event["tool_response"]["content"] = [{"type": "text", "text": "`report`"}]
        results = await asyncio.gather(reports.handle(event), reports.handle(event))
        assert sum("systemMessage" in value for value in results) == 1
        assert reports.stats["subagent_reports_displayed"] == 1


@pytest.mark.asyncio
async def test_background_handback_and_stop_deliver_one_report():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-literals"), client)
        reports = SubagentReports(translator)
        launch = captured_event("PostToolUse")
        launch["tool_response"]["status"] = "async_launched"
        await reports.handle(launch)
        handback = {
            "hook_event_name": "PostToolUse",
            "session_id": launch["session_id"],
            "agent_id": launch["tool_response"]["agentId"],
            "tool_name": "SubagentHandback",
            "tool_input": {"message": "`delivered_report`"},
            "tool_use_id": "handback-call",
        }
        assert await reports.handle(handback) == {}
        notification = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": launch["session_id"],
            "prompt": "Continue",
        }
        assert "delivered_report" in (await reports.handle(notification))["systemMessage"]
        stopped = captured_event("SubagentStop")
        stopped["last_assistant_message"] = "`closing_text`"
        assert await reports.handle(stopped) == {}
        assert await reports.handle(notification) == {}
        assert reports.stats["subagent_reports_displayed"] == 1


@pytest.mark.asyncio
async def test_known_background_notification_is_protocol_not_human_input():
    data = json.loads((Path(__file__).parent / "fixtures/subagent_background.json").read_text())
    launch = next(
        e
        for e in data["events"]
        if e.get("tool_name") == "Agent" and e.get("hook_event_name") == "PostToolUse"
    )
    notification = next(
        e for e in data["events"] if e.get("prompt", "").startswith("<task-notification>")
    )
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-skip"), client)
        reports = SubagentReports(translator)
        assert not reports.is_known_notification(notification)
        await reports.handle(launch)
        assert reports.is_known_notification(notification)
        other = copy.deepcopy(notification)
        other["session_id"] = "another-session"
        assert not reports.is_known_notification(other)
        other = copy.deepcopy(notification)
        other["prompt"] = "请帮我检查任务状态"
        assert not reports.is_known_notification(other)


@pytest.mark.asyncio
async def test_oversized_report_falls_back_without_model_calls():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-size-limit"), client)
        reports = SubagentReports(translator)
        event = captured_event("PostToolUse")
        event["tool_response"]["content"] = [{"type": "text", "text": "A report line. " * 5000}]
        original = copy.deepcopy(event)
        assert await reports.handle(event) == {}
        assert event == original
        assert translator.calls == 0
        assert reports.stats["subagent_report_failures"] == 1
        assert reports.stats["subagent_report_retries"] == 0


@pytest.mark.asyncio
async def test_known_handback_envelope_is_not_translated_as_user_input():
    data = json.loads((Path(__file__).parent / "fixtures/subagent_handback.json").read_text())
    returned = next(e for e in data["events"] if e.get("tool_name") == "Agent")
    returned["tool_response"]["content"] = [{"type": "text", "text": "`original_report`"}]
    notification = next(
        e for e in data["events"] if e.get("prompt", "").startswith("<agent-message")
    )
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-protocol"), client)
        reports = SubagentReports(translator)
        assert not reports.is_known_notification(notification)
        await reports.handle(returned)
        assert reports.is_known_notification(notification)
        assert translator.calls == 0


@pytest.mark.asyncio
async def test_failed_foreground_reports_do_not_exhaust_run_capacity():
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(TranslationConfig(api_key="unused-for-size-limit"), client)
        reports = SubagentReports(translator)
        event = captured_event("PostToolUse")
        event["tool_response"]["content"] = [{"type": "text", "text": "A report line. " * 5000}]
        for index in range(128):
            event["tool_use_id"] = f"call-{index}"
            event["tool_response"]["agentId"] = f"agent-{index}"
            assert await reports.handle(event) == {}
        event["tool_use_id"] = "recovered-call"
        event["tool_response"]["agentId"] = "recovered-agent"
        event["tool_response"]["content"] = [{"type": "text", "text": "`recovered_report`"}]
        result = await reports.handle(event)
        assert "recovered_report" in result["systemMessage"]
        assert len(reports.runs) == 128
        assert translator.calls == 0
