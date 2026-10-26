import asyncio
import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass

from claude_translate.agent_context import AgentContext
from claude_translate.deep_seek_translator import DeepSeekTranslator, TranslationError


@dataclass
class ReportRun:
    lock: asyncio.Lock
    users: int = 0
    tool_id: str | None = None
    background: bool = False
    label: str = ""
    handback: str | None = None
    stopped: str | None = None
    delivered: str | None = None
    completed: bool = False


class SubagentReports:
    def __init__(self, translator: DeepSeekTranslator):
        if translator is None:
            raise TypeError("Translator is required")
        self.translator = translator
        self.runs: OrderedDict[tuple[str, str], ReportRun] = OrderedDict()
        self.stats = {
            "subagent_reports_displayed": 0,
            "subagent_report_failures": 0,
            "subagent_report_duplicates": 0,
            "subagent_events_ignored": 0,
            "subagent_background_launches": 0,
            "subagent_handbacks_seen": 0,
            "subagent_report_retries": 0,
        }

    def is_known_notification(self, event: dict) -> bool:
        prompt = event.get("prompt", "")
        if not isinstance(prompt, str):
            return False
        match = re.match(r"\s*<task-notification>\s*<task-id>([^<\r\n]+)</task-id>", prompt)
        if not match:
            match = re.match(r"""\s*<agent-message\s+from=["']([^"'<>\r\n]+)["']\s*>""", prompt)
        if not match:
            return False
        run = self.runs.get((event.get("session_id"), match.group(1)))
        return run is not None and run.tool_id is not None

    def get_run(self, session: str, agent: str) -> ReportRun | None:
        key = (session, agent)
        if key not in self.runs:
            if len(self.runs) >= 128:
                removable = next(
                    (
                        k
                        for k, r in self.runs.items()
                        if r.users == 0
                        and (r.tool_id is None or r.delivered or (r.completed and not r.background))
                    ),
                    None,
                )
                if removable is None:
                    self.stats["subagent_events_ignored"] += 1
                    return None
                self.runs.pop(removable)
            self.runs[key] = ReportRun(asyncio.Lock())
        self.runs.move_to_end(key)
        return self.runs[key]

    @staticmethod
    def start_run(run: ReportRun, tool_id: str, reset: bool):
        if run.tool_id != tool_id:
            if reset or run.tool_id is not None:
                run.handback = run.stopped = run.delivered = None
            run.tool_id = tool_id
            run.background = False
            run.completed = False

    async def emit(self, run: ReportRun, text: str | None) -> dict:
        if not text or not text.strip() or not run.tool_id:
            return {}
        fingerprint = hashlib.sha256((run.tool_id + "\0" + text).encode()).hexdigest()
        if run.delivered == fingerprint:
            self.stats["subagent_report_duplicates"] += 1
            return {}
        try:
            async with asyncio.timeout(self.translator.config.timeout):
                try:
                    translated = await self.translator.translate(text, "zh")
                except TranslationError as exc:
                    if not exc.retryable:
                        raise
                    self.stats["subagent_report_retries"] += 1
                    translated = await self.translator.translate(text, "zh")
        except (TranslationError, TimeoutError):
            self.stats["subagent_report_failures"] += 1
            return {}
        run.delivered = fingerprint
        self.stats["subagent_reports_displayed"] += 1
        label = f"（{run.label}）" if run.label else ""
        return {"systemMessage": f"子代理最终报告{label}：\n{translated}"}

    async def handle(self, event: dict) -> dict:
        kind = event.get("hook_event_name")
        tool = event.get("tool_name")
        session = event.get("session_id")
        if not isinstance(session, str) or not session:
            return {}
        if kind == "UserPromptSubmit" and not AgentContext.is_subagent(event):
            return await self.flush_background(session)
        root_agent = (
            kind in {"PreToolUse", "PostToolUse"}
            and tool == "Agent"
            and not AgentContext.is_subagent(event)
        )
        handback = kind == "PostToolUse" and tool == "SubagentHandback"
        stopped = kind == "SubagentStop"
        response = event.get("tool_response", {})
        inputs = event.get("tool_input", {})
        if not isinstance(inputs, dict):
            return {}
        tool_id = event.get("tool_use_id")
        if root_agent:
            if not isinstance(tool_id, str) or not tool_id:
                return {}
            if kind == "PreToolUse":
                agent = inputs.get("resume")
            else:
                if not isinstance(response, dict) or response.get("status") not in {
                    "completed",
                    "async_launched",
                }:
                    return {}
                agent = response.get("agentId")
        elif handback or stopped:
            agent = event.get("agent_id")
        else:
            return {}
        if not isinstance(agent, str) or not agent:
            return {}
        run = self.get_run(session, agent)
        if run is None:
            return {}
        run.users += 1
        try:
            async with run.lock:
                if root_agent:
                    self.start_run(run, tool_id, reset=kind == "PreToolUse")
                    if kind == "PreToolUse":
                        return {}
                    run.label = str(
                        response.get("agentType") or inputs.get("subagent_type") or agent
                    )
                    background = response["status"] == "async_launched"
                    if background and not run.background:
                        self.stats["subagent_background_launches"] += 1
                    run.background = background
                    if run.background:
                        return await self.emit(run, run.handback or run.stopped)
                    run.completed = True
                    content = response.get("content", [])
                    text = "\n\n".join(
                        b["text"]
                        for b in content
                        if isinstance(b, dict)
                        and b.get("type") == "text"
                        and isinstance(b.get("text"), str)
                    )
                    return await self.emit(run, run.handback or text)
                value = inputs.get("message") if handback else event.get("last_assistant_message")
                if not isinstance(value, str) or not value.strip():
                    return {}
                if len(value) > 64000:
                    self.stats["subagent_report_failures"] += 1
                    return {}
                if handback:
                    self.stats["subagent_handbacks_seen"] += 1
                    run.handback = value
                else:
                    run.stopped = value
                return {}
        finally:
            run.users -= 1

    async def flush_background(self, session: str) -> dict:
        async def emit_ready(run: ReportRun):
            run.users += 1
            try:
                async with run.lock:
                    if run.background:
                        return await self.emit(run, run.handback or run.stopped)
                    return {}
            finally:
                run.users -= 1

        ready = [
            run
            for (sid, _), run in self.runs.items()
            if sid == session and run.background and (run.handback or run.stopped)
        ]
        results = await asyncio.gather(*(emit_ready(run) for run in ready))
        notices = [value["systemMessage"] for value in results if value.get("systemMessage")]
        return {"systemMessage": "\n\n".join(notices)} if notices else {}
