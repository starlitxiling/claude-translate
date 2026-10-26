import asyncio
import hmac
import json
import os
import signal
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from claude_translate.config import TranslationConfig, validate_url
from claude_translate.deep_seek_translator import DeepSeekTranslator, TranslationError
from claude_translate.display_translator import DisplayTranslator
from claude_translate.prompt_registry import PromptRegistry

LOCAL_HEADER = "X-Claude-Translate-Token"
HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    LOCAL_HEADER.lower(),
}


def clean_headers(headers) -> dict[str, str]:
    connection = next((v for k, v in headers.items() if k.lower() == "connection"), "")
    excluded = HOP_HEADERS | {x.strip().lower() for x in connection.split(",")}
    return {k: v for k, v in headers.items() if k.lower() not in excluded}


def proxy_url(upstream: str, raw_path: str) -> str:
    parts = urlsplit(validate_url(upstream))
    if not raw_path.startswith("/") or raw_path.startswith("//"):
        raise ValueError("Invalid proxy path")
    prefix = parts.path.rstrip("/")
    if prefix and not raw_path.startswith(prefix + "/"):
        raise ValueError("Request path is outside the upstream prefix")
    return f"{parts.scheme}://{parts.netloc}{raw_path}"


def write_private_json(path: Path, data: dict):
    temporary = path.with_suffix(".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(data, output)
    os.replace(temporary, path)


class ProxyServer:
    def __init__(
        self,
        upstream: str,
        token: str,
        client: aiohttp.ClientSession,
        translator: DeepSeekTranslator,
        registry: PromptRegistry,
        display: DisplayTranslator,
    ):
        self.upstream = validate_url(upstream)
        if not token or any(x is None for x in (client, translator, registry, display)):
            raise ValueError("All runtime dependencies and the local access token are required")
        self.token = token
        self.client = client
        self.translator = translator
        self.registry = registry
        self.display = display
        self.stats = {
            "registered": 0,
            "rewritten_blocks": 0,
            "model_requests": 0,
            "display_batches": 0,
            "display_failures": 0,
        }

    def application(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/{path:.*}", self.handle)
        return app

    async def handle(self, request: web.Request) -> web.StreamResponse:
        supplied = request.headers.get(LOCAL_HEADER, "")
        if not hmac.compare_digest(supplied, self.token):
            return web.json_response({"error": "Local authentication required"}, status=401)
        if request.path.startswith("/__translate/"):
            try:
                return await self.internal(request)
            except TranslationError as exc:
                return web.json_response({"error": str(exc)}, status=502)
            except (ValueError, TypeError, KeyError):
                return web.json_response({"error": "Invalid hook payload"}, status=400)
        return await self.forward(request)

    async def internal(self, request: web.Request) -> web.Response:
        if request.path == "/__translate/status" and request.method == "GET":
            return web.json_response(
                {
                    **self.stats,
                    "translation_calls": self.translator.calls,
                    "cache_hits": self.translator.cache_hits,
                }
            )
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(request.method, ["POST"])
        event = await request.json()
        if request.path == "/__translate/register":
            prompt = event["prompt"]
            if not isinstance(prompt, str):
                raise ValueError("Prompt must be text")
            translated = self.registry.lookup(prompt)
            if translated is None:
                translated = await self.translator.translate(prompt, "en")
            self.registry.register(prompt, translated)
            self.stats["registered"] += 1
            return web.json_response({"ok": True})
        if request.path == "/__translate/display":
            self.stats["display_batches"] += 1
            try:
                translated = await self.display.translate(event)
                return web.json_response({"text": translated})
            except TranslationError:
                self.stats["display_failures"] += 1
                return web.json_response({"text": event["delta"], "fallback": True})
        raise web.HTTPNotFound()

    async def forward(self, request: web.Request) -> web.StreamResponse:
        response = None
        try:
            url = proxy_url(self.upstream, request.raw_path)
            body = await request.read()
            headers = clean_headers(request.headers)
            headers.pop("Accept-Encoding", None)
            headers["Accept-Encoding"] = "identity"
            if request.method == "POST" and (
                request.path.endswith("/messages")
                or request.path.endswith("/messages/count_tokens")
            ):
                if request.headers.get("Content-Encoding", "identity") != "identity":
                    return web.json_response(
                        {"error": "Encoded request body is unsupported"}, status=415
                    )
                payload = json.loads(body)
                payload, count = self.registry.rewrite(payload)
                self.stats["rewritten_blocks"] += count
                self.stats["model_requests"] += int(request.path.endswith("/messages"))
                body = json.dumps(payload, ensure_ascii=False).encode()
            async with self.client.request(
                request.method,
                url,
                data=body,
                headers=headers,
                allow_redirects=False,
                auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300),
            ) as upstream:
                response = web.StreamResponse(
                    status=upstream.status, headers=clean_headers(upstream.headers)
                )
                await response.prepare(request)
                try:
                    async for chunk in upstream.content.iter_any():
                        await response.write(chunk)
                    await response.write_eof()
                except (ConnectionError, asyncio.CancelledError):
                    upstream.close()
                    raise
                return response
        except (ValueError, TypeError):
            return web.json_response({"error": "Invalid upstream request"}, status=400)
        except (TimeoutError, aiohttp.ClientError):
            if response is not None and response.prepared:
                if request.transport is not None:
                    request.transport.close()
                return response
            return web.json_response({"error": "Claude upstream connection failed"}, status=502)


async def serve(config: TranslationConfig, runtime_path: Path):
    runtime = json.loads(runtime_path.read_text())
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)

    async def watch_parent():
        while not stopped.is_set():
            if os.getppid() != runtime["parent_pid"]:
                stopped.set()
                return
            try:
                await asyncio.wait_for(stopped.wait(), timeout=1)
            except TimeoutError:
                pass

    cache_path = Path(runtime["cache_path"])
    fd = os.open(cache_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    if cache_path.stat().st_mode & 0o077:
        raise ValueError("翻译缓存文件权限必须为 600")
    database = sqlite3.connect(cache_path)
    async with aiohttp.ClientSession() as client:
        translator = DeepSeekTranslator(config, client)
        proxy = ProxyServer(
            runtime["upstream"],
            runtime["token"],
            client,
            translator,
            PromptRegistry(database),
            DisplayTranslator(translator),
        )
        runner = web.AppRunner(
            proxy.application(), access_log=None, shutdown_timeout=2, handler_cancellation=True
        )
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = runner.addresses[0][1]
            runtime["url"] = f"http://127.0.0.1:{port}"
            runtime["pid"] = os.getpid()
            write_private_json(runtime_path, runtime)
            await watch_parent()
        finally:
            await runner.cleanup()
            database.close()
