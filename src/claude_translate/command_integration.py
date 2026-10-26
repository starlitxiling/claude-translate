import fcntl
import json
import os
import shlex
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


class CommandIntegration:
    marker = "# Managed by claude-translate."

    def __init__(self, entry: Path, state_path: Path, interpreter: Path):
        self.entry = entry.absolute()
        self.original = self.entry.with_name("claude-original")
        self.state_path = state_path.absolute()
        self.interpreter = interpreter.absolute()

    @classmethod
    def default(cls):
        return cls(
            Path.home() / ".local/bin/claude",
            Path.home() / ".config/claude-translate/command.json",
            Path(sys.executable),
        )

    @contextmanager
    def locked(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.state_path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    @staticmethod
    def atomic_text(path: Path, text: str, mode: int):
        fd, name = tempfile.mkstemp(prefix=".claude-translate-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w") as output:
                output.write(text)
            temporary.chmod(mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def read_state(self) -> dict:
        state = json.loads(self.state_path.read_text())
        if state["entry"] != str(self.entry):
            raise ValueError("命令备份不属于当前 claude 入口")
        return state

    def owns_entry(self, state: dict) -> bool:
        return (
            self.entry.is_file()
            and not self.entry.is_symlink()
            and self.entry.read_text() == state["wrapper"]
        )

    def enable(self):
        with self.locked():
            if self.state_path.exists():
                state = self.read_state()
                if (
                    self.owns_entry(state)
                    and self.original.is_symlink()
                    and (os.readlink(self.original) == state["native"])
                ):
                    return
                raise ValueError("现有接管状态与文件不一致，请检查后先停用，避免覆盖其他修改")
            if not self.entry.is_symlink():
                raise ValueError("只接管原生安装的 claude 符号链接，不覆盖已有启动脚本")
            if self.original.exists() or self.original.is_symlink():
                raise ValueError("claude-original 已存在，未覆盖")
            native = self.entry.resolve(strict=True)
            self.validate_native(native)
            target = os.readlink(self.entry)
            wrapper = (
                "#!/bin/sh\n"
                + self.marker
                + "\n"
                + "export CLAUDE_TRANSLATE_NATIVE="
                + shlex.quote(str(native))
                + "\n"
                + "exec "
                + shlex.quote(str(self.interpreter))
                + ' -I -m claude_translate launch "$@"\n'
            )
            state = {
                "entry": str(self.entry),
                "target": target,
                "native": str(native),
                "wrapper": wrapper,
            }
            self.atomic_text(self.state_path, json.dumps(state, indent=2) + "\n", 0o600)
            try:
                self.original.symlink_to(native)
                self.atomic_text(self.entry, wrapper, 0o755)
            except BaseException:
                if self.entry.is_symlink() and os.readlink(self.entry) == target:
                    if self.original.is_symlink() and os.readlink(self.original) == str(native):
                        self.original.unlink()
                    self.state_path.unlink()
                raise

    def disable(self):
        with self.locked():
            if not self.state_path.exists():
                return
            state = self.read_state()
            if (self.original.exists() or self.original.is_symlink()) and not (
                self.original.is_symlink() and os.readlink(self.original) == state["native"]
            ):
                raise ValueError("claude-original 已变更，保留当前入口和恢复记录")
            already_restored = (
                self.entry.is_symlink() and os.readlink(self.entry) == state["target"]
            )
            if not already_restored and not self.owns_entry(state):
                raise ValueError("claude 入口已变更，拒绝覆盖；可使用 claude-original")
            if not already_restored:
                fd, name = tempfile.mkstemp(prefix=".claude-restore-", dir=self.entry.parent)
                os.close(fd)
                temporary = Path(name)
                temporary.unlink()
                try:
                    temporary.symlink_to(state["target"])
                    os.replace(temporary, self.entry)
                finally:
                    temporary.unlink(missing_ok=True)
            if self.original.is_symlink() and os.readlink(self.original) == state["native"]:
                self.original.unlink()
            self.state_path.unlink()

    @classmethod
    def validate_native(cls, path: Path):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("原生 Claude 可执行文件不存在或无法执行")
        with path.open("rb") as source:
            if cls.marker.encode() in source.read(512):
                raise ValueError("原生入口不能指向翻译启动脚本")

    @classmethod
    def native_path(cls) -> str:
        return cls.default().resolve_native(
            os.environ.get("CLAUDE_TRANSLATE_NATIVE"), os.environ.get("PATH")
        )

    def resolve_native(self, override: str | None, search_path: str | None) -> str:
        if override:
            path = Path(override)
            self.validate_native(path)
            return str(path.resolve())
        with self.locked():
            if self.state_path.exists():
                path = Path(self.read_state()["native"])
            else:
                found = shutil.which("claude", path=search_path)
                if not found:
                    raise ValueError("未找到 claude 命令")
                path = Path(found)
            self.validate_native(path)
            return str(path.resolve())
