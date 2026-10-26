import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from claude_translate.command_integration import CommandIntegration
from claude_translate.config import TranslationConfig, validate_url
from claude_translate.hook_client import HookClient
from claude_translate.proxy_server import LOCAL_HEADER, write_private_json

ENGLISH_DISPLAY = (
    "This session uses an external display translator. Write all conversational assistant "
    "messages in English; an external display hook translates them into Simplified Chinese. "
    "This applies to conversational explanations only and overrides conversational Chinese "
    "language preferences in CLAUDE.md. Preserve the requested language of generated files, "
    "documentation, quoted literals, code, commands and user-requested artifacts."
    " Keep subagent instructions, internal progress and handoff reports in English. "
    "A separate hook displays Chinese handoff reports to the user."
)


class Launcher:
    def __init__(self, config_path: Path, config: TranslationConfig, claude_path: str):
        if not claude_path:
            raise ValueError("未找到 claude 命令")
        self.config_path = config_path.resolve()
        self.config = config
        self.claude_path = claude_path

    @staticmethod
    def routing_environment(cwd: Path) -> dict:
        config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
        files = [config_dir / "settings.json"]
        project = cwd
        for directory in (cwd, *cwd.parents):
            if (directory / ".git").exists():
                project = directory
                break
        files.extend([project / ".claude/settings.json", project / ".claude/settings.local.json"])
        result = dict(os.environ)
        for path in files:
            if path.exists():
                values = json.loads(path.read_text()).get("env", {})
                result.update(values)
        return result

    def run(self, arguments: list[str], report_path: Path | None = None) -> int:
        previous = {}

        def terminate(signum, frame):
            raise SystemExit(128 + signum)

        for sig in (signal.SIGTERM, signal.SIGHUP):
            previous[sig] = signal.signal(sig, terminate)
        try:
            return self._run(arguments, report_path)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def _run(self, arguments: list[str], report_path: Path | None) -> int:
        unsupported = {
            "--settings",
            "--setting-sources",
            "--bare",
            "--cloud",
            "--remote",
            "--teleport",
            "--bg",
            "--background",
            "--agent-url",
        }
        if any(arg.split("=", 1)[0] in unsupported for arg in arguments):
            raise ValueError("翻译模式暂不支持自定义 settings、bare、后台或远程会话参数")
        routing = self.routing_environment(Path.cwd())
        for name in (
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
        ):
            if routing.get(name) not in (None, "", "0", "false"):
                raise ValueError("当前版本只支持 Anthropic Messages 格式的上游服务")
        upstream = validate_url(
            self.config.upstream_override
            or routing.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        )
        with tempfile.TemporaryDirectory(prefix="claude-translate-") as temporary:
            directory = Path(temporary)
            runtime_path = directory / "runtime.json"
            settings_path = directory / "settings.json"
            runtime = {
                "upstream": upstream,
                "token": secrets.token_urlsafe(32),
                "hook_timeout": self.config.timeout + 10,
                "cache_path": str(self.config_path.parent / "prompt-cache.sqlite"),
                "parent_pid": os.getpid(),
            }
            write_private_json(runtime_path, runtime)
            with (directory / "service.log").open("w+") as log:
                server = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-m",
                        "claude_translate",
                        "serve",
                        "--config",
                        str(self.config_path),
                        "--runtime",
                        str(runtime_path),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                child = None
                try:
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if server.poll() is not None:
                            raise RuntimeError("本地翻译服务启动失败")
                        runtime = json.loads(runtime_path.read_text())
                        if "url" in runtime:
                            break
                        time.sleep(0.05)
                    else:
                        raise RuntimeError("本地翻译服务启动超时")
                    hooks = {}
                    for event, kind in [
                        ("UserPromptSubmit", "submit"),
                        ("UserPromptSubmit", "subagent"),
                        ("MessageDisplay", "display"),
                        ("PreToolUse", "subagent"),
                        ("PostToolUse", "subagent"),
                        ("SubagentStop", "subagent"),
                    ]:
                        command = shlex.join(
                            [
                                sys.executable,
                                "-I",
                                "-m",
                                "claude_translate",
                                "hook",
                                kind,
                                "--runtime",
                                str(runtime_path),
                            ]
                        )
                        hooks.setdefault(event, []).append(
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": command,
                                        "timeout": runtime["hook_timeout"] + 5,
                                        "onFailure": "block" if kind == "submit" else "continue",
                                    }
                                ]
                            }
                        )
                        if event == "PreToolUse":
                            hooks[event][-1]["matcher"] = "Agent"
                        elif event == "PostToolUse":
                            hooks[event][-1]["matcher"] = "Agent|SubagentHandback"
                    custom = routing.get("ANTHROPIC_CUSTOM_HEADERS", "")
                    if any(
                        line.lower().startswith(LOCAL_HEADER.lower() + ":")
                        for line in custom.splitlines()
                    ):
                        raise ValueError("检测到嵌套翻译代理，请退出现有翻译模式后启动")
                    settings = {
                        "env": {
                            "ANTHROPIC_BASE_URL": runtime["url"]
                            + urlsplit(upstream).path.rstrip("/"),
                            "ANTHROPIC_CUSTOM_HEADERS": custom
                            + ("\n" if custom else "")
                            + f"{LOCAL_HEADER}: {runtime['token']}",
                        },
                        "hooks": hooks,
                    }
                    write_private_json(settings_path, settings)
                    command = [
                        self.claude_path,
                        "--settings",
                        str(settings_path),
                        "--append-system-prompt",
                        ENGLISH_DISPLAY,
                        *arguments,
                    ]
                    print("翻译模式已开启：DeepSeek Flash；退出本会话即停用。", file=sys.stderr)
                    child = subprocess.Popen(command)
                    while child.poll() is None:
                        if server.poll() is not None:
                            child.terminate()
                            raise RuntimeError("本地翻译服务已退出，已停止当前 Claude 进程")
                        try:
                            child.wait(timeout=0.2)
                        except subprocess.TimeoutExpired:
                            pass
                        except KeyboardInterrupt:
                            # Claude shares the terminal group and handles the same SIGINT.
                            continue
                    if report_path:
                        write_private_json(report_path, HookClient(runtime).request("status"))
                    return child.returncode
                finally:
                    if child is not None and child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait()
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()

    @classmethod
    def from_path(cls, path: Path):
        return cls(path, TranslationConfig.load(path), CommandIntegration.native_path())
