class NativeArguments:
    """Keep native option values separate from management subcommands."""

    required = frozenset(
        """
        --agent --agents --append-system-prompt --append-system-prompt-file --autocompact
        --debug-file --effort --environment --fallback-model --input-format --json-schema
        --max-budget-usd --max-turns --model -n --name --output-format --permission-mode
        --permission-prompts --permission-prompt-tool --plugin-dir --plugin-url
        --remote-control-session-name-prefix --session-id --setting-sources --settings
        --system-prompt --system-prompt-file --system-prompt-snapshot
    """.split()
    )
    optional = frozenset(
        """
        --cloud -d --debug --from-pr --prompt-suggestions --remote-control
        -r --resume --teleport -w --worktree
    """.split()
    )
    variadic = frozenset(
        """
        --add-dir --allowedTools --allowed-tools --betas --disallowedTools
        --disallowed-tools --file --mcp-config --tools
    """.split()
    )

    @classmethod
    def command(cls, arguments: list[str]) -> str | None:
        index = 0
        while index < len(arguments):
            value = arguments[index]
            if value == "--":
                return None
            if not value.startswith("-"):
                return value
            index += 1
            if "=" in value:
                continue
            if not value.startswith("--") and len(value) > 2:
                for offset, character in enumerate(value[1:], start=1):
                    flag = "-" + character
                    if flag in cls.required or flag in cls.optional:
                        if offset == len(value) - 1:
                            if flag in cls.required:
                                index += 1
                            elif index < len(arguments) and not arguments[index].startswith("-"):
                                index += 1
                        break
                continue
            if value in cls.required:
                index += 1
            elif value in cls.optional:
                if index < len(arguments) and not arguments[index].startswith("-"):
                    index += 1
            elif value in cls.variadic:
                while index < len(arguments) and not arguments[index].startswith("-"):
                    index += 1
        return None
