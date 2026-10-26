import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp

from claude_translate.command_integration import CommandIntegration
from claude_translate.config import DEFAULT_CONFIG, TranslationConfig
from claude_translate.deep_seek_translator import DeepSeekTranslator
from claude_translate.hook_client import HookClient
from claude_translate.launcher import Launcher
from claude_translate.native_arguments import NativeArguments
from claude_translate.proxy_server import serve


async def doctor(config: TranslationConfig):
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(config, client)
        english = await translator.translate("请检查 `README.md`，不要修改文件。", "en")
        chinese = await translator.translate("The file was inspected. No changes were made.", "zh")
        print(
            json.dumps(
                {
                    "model": config.model,
                    "english": english,
                    "chinese": chinese,
                    "translation_calls": translator.calls,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def launch_main():
    parser = argparse.ArgumentParser(description="以翻译模式启动 Claude Code", add_help=False)
    parser.add_argument("--translation-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--translation-report", type=Path)
    args, remainder = parser.parse_known_args()
    if remainder[:1] == ["--"]:
        remainder = remainder[1:]
    try:
        command = NativeArguments.command(remainder)
        if command in {"install", "update", "upgrade"}:
            raise ValueError("请先运行 claude-translate disable，再执行原生安装或升级")
        management = {
            "auth",
            "mcp",
            "plugin",
            "plugins",
            "doctor",
            "agents",
            "logs",
            "stop",
            "kill",
            "rm",
            "setup-token",
            "auto-mode",
            "purge",
            "import",
            "attach",
            "gateway",
            "respawn",
            "ultrareview",
        }
        if remainder in (["--help"], ["-h"], ["--version"], ["-v"]) or (command in management):
            native = CommandIntegration.native_path()
            os.execv(native, [native, *remainder])
        return_code = Launcher.from_path(args.translation_config).run(
            remainder, args.translation_report
        )
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"翻译模式无法启动：{exc}", file=sys.stderr)
        return_code = 1
    raise SystemExit(return_code)


def main():
    if sys.argv[1:2] == ["launch"]:
        del sys.argv[1]
        launch_main()
        return
    parser = argparse.ArgumentParser(description="Claude Code 独立翻译组件")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve", help="启动本地翻译代理")
    serve_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    serve_parser.add_argument("--runtime", type=Path, required=True)
    hook = sub.add_parser("hook", help="Claude Code Hook 入口")
    hook.add_argument("kind", choices=["submit", "display", "subagent"])
    hook.add_argument("--runtime", type=Path, required=True)
    check = sub.add_parser("doctor", help="调用真实 DeepSeek API 检查双向翻译")
    check.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub.add_parser("enable", help="让 claude 命令默认启用翻译")
    sub.add_parser("disable", help="恢复 claude 原生命令")
    args = parser.parse_args()
    try:
        if args.command == "enable":
            CommandIntegration.default().enable()
            print("claude 已默认启用翻译；claude-original 可直接运行原版。")
        elif args.command == "disable":
            CommandIntegration.default().disable()
            print("claude 已恢复原版入口。")
        elif args.command == "hook":
            HookClient.run(args.kind, args.runtime)
        elif args.command == "doctor":
            asyncio.run(doctor(TranslationConfig.load(args.config)))
        else:
            asyncio.run(serve(TranslationConfig.load(args.config), args.runtime))
    except (ValueError, RuntimeError, OSError, aiohttp.ClientError) as exc:
        print(f"翻译组件错误：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
