from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from sxpb_llm.block_parse import CodeBlock, CodeBlockError, parse_code_blocks


@dataclass
class PaludoroSession:
    artifacts: Dict[str, str] = field(default_factory=dict)
    default_artifacts: Dict[str, str] = field(default_factory=dict)
    # artipath -> List[(content, turn)]
    artifact_versions: Dict[str, List[Tuple[str, int]]] = field(default_factory=dict)
    dirty_artifacts: set = field(default_factory=set)
    # agent_name -> started_at (epoch seconds)
    running_agents: Dict[str, float] = field(default_factory=dict)
    # Set of agent names that have been requested to cancel
    agent_cancel_signals: set = field(default_factory=set)
    # Per-agent httpx clients so cancel can kill in-flight requests from outside
    agent_http_clients: dict = field(default_factory=dict)
    pipeline_triggers: int = 0
    pipeline_finishes: int = 0
    history_version: int = 0
    # List of functions (artipath, content) -> content
    on_save_hooks: List = field(default_factory=list)

    def save_artifact(
        self, artipath: str, content: str, turn: int = -1, create_version: bool = True
    ):
        for hook in self.on_save_hooks:
            content = hook(artipath, content)

        clean_content = content.strip()
        if self.artifacts.get(artipath) != clean_content:
            self.artifacts[artipath] = clean_content
            self.dirty_artifacts.add(artipath)

            if not create_version:
                return

            if artipath not in self.artifact_versions:
                self.artifact_versions[artipath] = []

            # If turn is -1, just use a sequential number based on version length
            display_turn = turn if turn != -1 else len(self.artifact_versions[artipath])

            self.artifact_versions[artipath].append((clean_content, display_turn))

    def reset(self):
        self.artifacts.clear()
        self.artifacts.update(self.default_artifacts)
        self.artifact_versions.clear()
        for k, v in self.default_artifacts.items():
            self.artifact_versions[k] = [(v, 0)]
        self.dirty_artifacts.update(self.artifacts.keys())

    def request_cancel(self, agent_name: str):
        self.agent_cancel_signals.add(agent_name)

    def is_cancelled(self, agent_name: str) -> bool:
        return agent_name in self.agent_cancel_signals

    def clear_cancel(self, agent_name: str):
        self.agent_cancel_signals.discard(agent_name)

    def to_dict(self) -> dict:
        return {
            "artifacts": self.artifacts,
            "artifact_versions": self.artifact_versions,
            "pipeline_triggers": self.pipeline_triggers,
            "pipeline_finishes": self.pipeline_finishes,
            "history_version": self.history_version,
        }

    def from_dict(self, data: dict):
        self.artifacts = data.get("artifacts", {})
        self.artifact_versions = data.get("artifact_versions", {})
        self.pipeline_triggers = data.get("pipeline_triggers", 0)
        self.pipeline_finishes = data.get("pipeline_finishes", 0)
        self.history_version = data.get("history_version", 0)
        self.dirty_artifacts.update(self.artifacts.keys())


def parse_assistant_response(content: str) -> Tuple[str, Dict[str, str], List[str]]:
    """Extracts virtual artifact saves from the assistant response.

    Supports:
    1. ```lang >artipath ... ``` (fenced code blocks)
    2. >artipath (bare lines, content until end of prose or next >/<)

    Fenced > blocks win over bare > patterns for the same filepath.

    Returns (clean_response, new_artifacts, malformed_errors).
    """
    new_artifacts: Dict[str, str] = {}
    malformed_errors: List[str] = []
    response_parts: List[str] = []

    for block in parse_code_blocks(content):
        if block.error is CodeBlockError.NOT_FENCED:
            # Plain prose — pass through
            response_parts.append(block.content)

        elif block.operation == ">":
            # Artifact — capture, omit from clean_response
            if block.filepath:
                new_artifacts[block.filepath] = block.content

        elif block.operation == "<":
            # Malformed — wrong prefix, but pass through to response
            name = block.filepath or "?"
            malformed_errors.append(
                f"Wrong prefix on artifact block: used '<' for '{name}' — "
                "use '>' to write artifacts, '<' means read-only."
            )
            response_parts.append(_reconstruct_fence(block))

        else:
            # Non-artifact code block — pass through
            response_parts.append(_reconstruct_fence(block))

    clean_response = "".join(response_parts).strip()
    return clean_response, new_artifacts, malformed_errors


def _reconstruct_fence(block: CodeBlock) -> str:
    """Reconstruct a fenced code block from a :class:`CodeBlock`."""
    fence_info = block.language
    if block.operation:
        fence_info += f" {block.operation}"
    if block.filepath:
        fence_info += f" {block.filepath}"
    if block.content:
        return f"```{fence_info}\n{block.content}\n```"
    return f"```{fence_info}\n```"
