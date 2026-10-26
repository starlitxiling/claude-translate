class AgentContext:
    @staticmethod
    def is_subagent(event: dict) -> bool:
        if event.get("agent_id"):
            return True
        path = event.get("transcript_path", "")
        return isinstance(path, str) and "/subagents/" in path.replace("\\", "/")
