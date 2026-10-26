import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from claude_translate.proxy_server import LOCAL_HEADER


class HookClient:
    def __init__(self, runtime: dict):
        self.url = runtime["url"]
        self.token = runtime["token"]
        self.timeout = runtime["hook_timeout"]

    def request(self, route: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.url + "/__translate/" + route,
            data=data,
            headers={LOCAL_HEADER: self.token, "Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                reason = json.loads(exc.read()).get("error", f"HTTP {exc.code}")
            except ValueError:
                reason = f"HTTP {exc.code}"
            raise RuntimeError(reason) from None

    @classmethod
    def run(cls, kind: str, runtime_path: Path):
        try:
            event = json.load(sys.stdin)
            client = cls(json.loads(runtime_path.read_text()))
            if kind == "submit":
                client.request("register", event)
                print("{}")
            elif kind == "subagent":
                print(json.dumps(client.request("subagent", event), ensure_ascii=False))
            else:
                result = client.request("display", event)
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "MessageDisplay",
                                "displayContent": result["text"],
                            }
                        },
                        ensure_ascii=False,
                    )
                )
                if result.get("fallback"):
                    print("翻译暂不可用，本批显示英文原文。", file=sys.stderr)
        except Exception:
            if kind == "submit":
                print(
                    json.dumps(
                        {
                            "decision": "block",
                            "reason": "输入翻译失败，请检查 claude-translate doctor 后重试。",
                        },
                        ensure_ascii=False,
                    )
                )
            elif kind == "display":
                print("{}")
                print("显示翻译连接失败，保留英文原文。", file=sys.stderr)
            else:
                print("{}")
                print("子代理交接翻译不可用，保留英文原报告。", file=sys.stderr)
