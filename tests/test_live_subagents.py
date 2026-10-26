import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CLAUDE_TRANSLATE_LIVE") != "1", reason="Explicit real-API opt-in required"
    ),
]


@pytest.mark.parametrize("mode", ["foreground", "background", "handback"])
def test_real_subagent_handoff_is_chinese_display_only(tmp_path, mode):
    background = mode == "background"
    handback = mode == "handback"
    observed = tmp_path / "observed-hooks.jsonl"
    capture = Path(__file__).parent / "fixtures/capture_hook.py"
    command = shlex.join([sys.executable, "-I", str(capture), str(observed)])
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": command}]}],
                    "PostToolUse": [
                        {
                            "matcher": "Agent|SubagentHandback",
                            "hooks": [{"type": "command", "command": command}],
                        }
                    ],
                }
            }
        )
    )
    (tmp_path / "probe.txt").write_text(
        "The blue notebook contains three pages. No files were changed.\n"
    )
    agents = {
        "translation-probe": {
            "description": "A read-only subagent for checking a short text fixture.",
            "prompt": "Use English only. Briefly announce the read, then read the file. "
            "Return two English sentences about the content. Do not modify anything.",
            "tools": ["Read"],
        }
    }
    agents_path = tmp_path / "agents.json"
    if handback:
        agents["translation-probe"]["tools"].append("SubagentHandback")
        agents["translation-probe"]["prompt"] += (
            " Deliver your final report through SubagentHandback, not just a text reply."
        )
    agents_path.write_text(json.dumps(agents))
    report_path = tmp_path / "translation-report.json"
    schedule = (
        "后台运行（run_in_background=true），然后等待任务完成"
        if background
        else "前台运行并等待完成"
    )
    prompt = (
        f"请用 Agent 工具调用一次 translation-probe 子代理，{schedule}，"
        f"让它读取 {tmp_path}/probe.txt。最终用一句话概括报告。不得修改文件。"
    )
    if handback:
        prompt += "让子代理使用 SubagentHandback 工具交回英文报告。"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "claude_translate",
            "launch",
            "--translation-report",
            str(report_path),
            "--agents",
            str(agents_path),
            "--tools",
            "Agent,Read,TaskOutput",
            "--allowedTools",
            "Agent",
            "Read",
            "TaskOutput",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--max-turns",
            "10",
            "--output-format",
            "stream-json",
            "--verbose",
            "--forward-subagent-text",
            "--no-session-persistence",
            *(["--permission-mode", "auto"] if handback else []),
            "-p",
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=tmp_path,
    )
    (tmp_path / "cli-output.jsonl").write_text(result.stdout)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    notices = [
        e
        for e in events
        if e.get("type") == "system"
        and e.get("subtype") == "informational"
        and "子代理最终报告" in e.get("content", "")
    ]
    assert len(notices) == 1, result.stdout[-6000:]
    translated_body = notices[0]["content"].split("：", 1)[1]
    assert len(re.findall(r"[\u3400-\u9fff]", translated_body)) >= 10, notices
    counts = json.loads(report_path.read_text())
    assert counts["subagent_reports_displayed"] == 1
    assert counts["subagent_report_failures"] == 0
    assert counts["display_failures"] == 0
    if handback:
        assert counts["subagent_handbacks_seen"] >= 1
        assert counts["subagent_notifications_passthrough"] >= 1
    if background:
        assert counts["subagent_background_launches"] >= 1
        assert counts["subagent_notifications_passthrough"] >= 1
    agent_results = []
    for event in events:
        if event.get("type") != "user" or event.get("parent_tool_use_id") is not None:
            continue
        for block in event.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                agent_results.append(json.dumps(block.get("content"), ensure_ascii=False))
    if background or handback:
        captured = [json.loads(line) for line in observed.read_text().splitlines()]
        parent_prompts = [
            e.get("prompt", "") for e in captured if e.get("hook_event_name") == "UserPromptSubmit"
        ]
        if background:
            assert any("blue notebook" in text.lower() for text in parent_prompts), parent_prompts
        else:
            handed_back = [
                e.get("tool_input", {}).get("message", "")
                for e in captured
                if e.get("tool_name") == "SubagentHandback"
            ]
            assert any("blue notebook" in text.lower() for text in handed_back), handed_back
            assert all("子代理最终报告" not in text for text in handed_back)
        assert all("子代理最终报告" not in text for text in parent_prompts)
    else:
        assert any("blue notebook" in text.lower() for text in agent_results), agent_results
    assert all("子代理最终报告" not in text for text in agent_results)
    internal_text = [
        block["text"]
        for event in events
        if event.get("type") == "assistant" and event.get("parent_tool_use_id")
        for block in event.get("message", {}).get("content", [])
        if block.get("type") == "text"
    ]
    assert internal_text
    assert all(not re.search(r"[\u3400-\u9fff]", text) for text in internal_text)
