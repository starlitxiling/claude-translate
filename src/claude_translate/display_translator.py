import asyncio
import re
from dataclasses import dataclass

from claude_translate.deep_seek_translator import DeepSeekTranslator


@dataclass
class FenceState:
    marker: str | None = None

    def split(self, text: str) -> list[tuple[bool, str]]:
        spans: list[tuple[bool, str]] = []
        for line in text.splitlines(keepends=True):
            match = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})(.*?)(?:\n)?$", line)
            literal = self.marker is not None
            if match:
                marker, tail = match.groups()
                if self.marker is None:
                    self.marker = marker
                    literal = True
                elif (
                    marker[0] == self.marker[0]
                    and len(marker) >= len(self.marker)
                    and not tail.strip()
                ):
                    self.marker = None
            if spans and spans[-1][0] == literal:
                spans[-1] = literal, spans[-1][1] + line
            else:
                spans.append((literal, line))
        return spans


@dataclass
class DisplayState:
    fence: FenceState
    lock: asyncio.Lock
    next_index: int = 0
    users: int = 0
    closed: bool = False


class DisplayTranslator:
    def __init__(self, translator: DeepSeekTranslator):
        if translator is None:
            raise TypeError("Translator is required")
        self.translator = translator
        self.states: dict[str, DisplayState] = {}

    async def translate(self, event: dict) -> str:
        message, index, final, delta = (
            event["message_id"],
            event["index"],
            event["final"],
            event["delta"],
        )
        if (
            not isinstance(message, str)
            or not message
            or type(index) is not int
            or index < 0
            or type(final) is not bool
            or not isinstance(delta, str)
        ):
            raise ValueError("Invalid display event")
        state = self.states.setdefault(message, DisplayState(FenceState(), asyncio.Lock()))
        state.users += 1
        try:
            async with state.lock:
                if state.closed or index != state.next_index:
                    raise ValueError("Display events must arrive in sequence")
                spans = state.fence.split(delta)
                state.next_index += 1
                state.closed = final
                output = []
                for literal, span in spans:
                    output.append(span if literal else await self.translator.translate(span, "zh"))
                return "".join(output)
        finally:
            state.users -= 1
            if state.users == 0 and (state.closed or state.next_index == 0):
                self.states.pop(message, None)
