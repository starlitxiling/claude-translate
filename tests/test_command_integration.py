import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from claude_translate.command_integration import CommandIntegration
from claude_translate.native_arguments import NativeArguments


@pytest.fixture
def native():
    path = Path.home() / ".local/share/claude/versions/2.1.299"
    if not path.is_file():
        pytest.skip("Real Claude Code 2.1.299 is required")
    return path


def integration_at(tmp_path):
    return CommandIntegration(
        tmp_path / "bin/claude", tmp_path / "config/command.json", Path(sys.executable)
    )


@pytest.mark.parametrize(
    "arguments, expected",
    [
        (["--append-system-prompt", "update", "-p", "Explain code"], "Explain code"),
        (["--tools", "Read", "Edit", "--verbose", "update"], "update"),
        (["--resume", "session-id", "-p", "Hello"], "Hello"),
        (["--", "update"], None),
        (["-nupdate", "-p", "Hello"], "Hello"),
        (["-cn", "review-test", "update"], "update"),
    ],
)
def test_native_option_values_are_not_commands(arguments, expected):
    assert NativeArguments.command(arguments) == expected


def test_option_arity_matches_real_native_help(native):
    result = subprocess.run(
        [str(native), "--help"], capture_output=True, text=True, check=True, timeout=15
    )
    for line in result.stdout.splitlines():
        if not line.startswith("  -"):
            continue
        spec = line[2:].split("  ", 1)[0]
        flags = re.findall(r"(?<![\w-])--?[A-Za-z][\w-]*", spec)
        if "<" in spec:
            expected = NativeArguments.variadic if "..." in spec else NativeArguments.required
            assert set(flags) <= expected, spec
        elif "[" in spec:
            assert set(flags) <= NativeArguments.optional, spec


def test_native_resolution_obeys_transition_lock(tmp_path, native):
    item = integration_at(tmp_path)
    item.entry.parent.mkdir()
    item.entry.symlink_to(native)
    item.enable()
    script = (
        "from pathlib import Path; import sys; "
        "from claude_translate.command_integration import CommandIntegration; "
        "item=CommandIntegration(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.executable)); "
        "print('ready', flush=True); print(item.resolve_native(None, sys.argv[3]), flush=True)"
    )
    process = None
    try:
        with item.locked():
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    script,
                    str(item.entry),
                    str(item.state_path),
                    str(item.entry.parent),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert process.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=0.2)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert stdout.strip() == str(native)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
        item.disable()


def test_enable_and_disable_restore_real_native_link(tmp_path, native):
    item = integration_at(tmp_path)
    item.entry.parent.mkdir()
    item.entry.symlink_to(native)
    before = os.readlink(item.entry)
    item.enable()
    assert not item.entry.is_symlink()
    assert item.original.resolve() == native
    result = subprocess.run(
        [str(item.entry), "--version"], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert "2.1.299" in result.stdout
    assert "翻译模式" not in result.stderr
    result = subprocess.run(
        [str(item.entry), "--translation-config", str(tmp_path / "absent"), "--version"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    with pytest.raises(ValueError):
        item.validate_native(item.entry)
    item.enable()
    item.disable()
    assert item.entry.is_symlink()
    assert os.readlink(item.entry) == before
    assert not item.original.is_symlink()
    assert not item.state_path.exists()
    item.disable()


def test_conflicting_original_alias_is_not_overwritten(tmp_path, native):
    item = integration_at(tmp_path)
    item.entry.parent.mkdir()
    item.entry.symlink_to(native)
    item.original.write_text("existing user file")
    with pytest.raises(ValueError):
        item.enable()
    assert item.entry.resolve() == native
    assert item.original.read_text() == "existing user file"


def test_disable_refuses_changed_launcher(tmp_path, native):
    item = integration_at(tmp_path)
    item.entry.parent.mkdir()
    item.entry.symlink_to(native)
    item.enable()
    item.entry.write_text("user replacement")
    with pytest.raises(ValueError):
        item.disable()
    assert item.entry.read_text() == "user replacement"
    assert item.state_path.exists()


def test_disable_preserves_state_when_original_alias_changes(tmp_path, native):
    item = integration_at(tmp_path)
    item.entry.parent.mkdir()
    item.entry.symlink_to(native)
    item.enable()
    item.original.unlink()
    item.original.write_text("user replacement")
    with pytest.raises(ValueError):
        item.disable()
    assert item.state_path.exists()
    assert item.original.read_text() == "user replacement"


@pytest.mark.parametrize(
    "prefix",
    [
        ["--verbose"],
        ["--model", "opus"],
        ["-cn", "review-test"],
        ["-cnreview-test"],
        ["-cdapi"],
    ],
)
def test_update_guard_applies_after_global_options(tmp_path, native, prefix):
    item = integration_at(tmp_path)
    item.entry.parent.mkdir()
    item.entry.symlink_to(native)
    item.enable()
    try:
        result = subprocess.run(
            [str(item.entry), *prefix, "update", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 1
        assert "claude-translate disable" in result.stderr
    finally:
        item.disable()


@pytest.mark.parametrize("command", ["attach", "gateway", "respawn", "ultrareview"])
def test_management_help_bypasses_translation(tmp_path, native, command):
    item = integration_at(tmp_path)
    item.entry.parent.mkdir()
    item.entry.symlink_to(native)
    item.enable()
    try:
        result = subprocess.run(
            [
                str(item.entry),
                "--translation-config",
                str(tmp_path / "absent"),
                "--verbose",
                command,
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert "Usage:" in result.stdout
        assert "翻译模式" not in result.stderr
    finally:
        item.disable()


def test_plain_user_executable_is_not_replaced(tmp_path):
    item = integration_at(tmp_path)
    item.entry.parent.mkdir()
    item.entry.write_text("user launcher")
    with pytest.raises(ValueError):
        item.enable()
    assert item.entry.read_text() == "user launcher"


def test_workspace_module_cannot_shadow_command_package(tmp_path, native):
    item = integration_at(tmp_path)
    item.entry.parent.mkdir()
    item.entry.symlink_to(native)
    (tmp_path / "claude_translate.py").write_text(
        'raise RuntimeError("Workspace module must not be imported by the launcher")\n'
    )
    item.enable()
    try:
        result = subprocess.run(
            [str(item.entry), "--version"], capture_output=True, text=True, timeout=15, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        assert "2.1.299" in result.stdout
    finally:
        item.disable()


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("CLAUDE_TRANSLATE_LIVE") != "1", reason="Explicit real-API opt-in required"
)
def test_real_default_command_translation(tmp_path, native):
    item = integration_at(tmp_path)
    item.entry.parent.mkdir()
    item.entry.symlink_to(native)
    (tmp_path / "claude_translate.py").write_text(
        'raise RuntimeError("Workspace module must not be imported by translation hooks")\n'
    )
    report = tmp_path / "report.json"
    item.enable()
    try:
        result = subprocess.run(
            [
                str(item.entry),
                "--translation-report",
                str(report),
                "-p",
                "请用一句话解释命令行，不要使用工具。",
                "--tools",
                "",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--max-turns",
                "1",
                "--no-session-persistence",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert re.search(r"[\u3400-\u9fff]", result.stdout)
        counts = json.loads(report.read_text())
        assert counts["registered"] >= 1 and counts["rewritten_blocks"] >= 1
        assert counts["display_batches"] >= 1 and counts["display_failures"] == 0
    finally:
        item.disable()
