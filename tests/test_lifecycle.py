import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def test_sigterm_cleans_real_cli_and_proxy(tmp_path):
    config = tmp_path / ".env.local"
    config.write_text("DEEPSEEK_API_KEY=unused-for-startup\n")
    config.chmod(0o600)
    executable = str(Path(sys.executable).parent / "claude-zh")
    process = subprocess.Popen(
        [
            executable,
            "--translation-config",
            str(config),
            "-p",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        start_new_session=True,
    )
    runtime = None
    path = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            files = list(tmp_path.glob("claude-translate-*/runtime.json"))
            if files:
                path = files[0]
                runtime = json.loads(path.read_text())
                if "pid" in runtime:
                    break
            assert process.poll() is None
            time.sleep(0.05)
        assert runtime and "pid" in runtime
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=10)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and path.exists():
            time.sleep(0.05)
        assert not path.exists(), "Launcher must remove its temporary runtime on SIGTERM"
        try:
            os.kill(runtime["pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("Proxy process survived launcher shutdown")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if runtime and "pid" in runtime:
            try:
                os.kill(runtime["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
