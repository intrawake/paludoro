from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from sxpb_llm.block_parse import CodeBlock, CodeBlockError, parse_code_blocks


@dataclass
class ArtifactRevision:
    content: str
    turn_id: Optional[int] = None
    visible: bool = True
    source: str = "manual"

    @property
    def display_turn(self) -> Optional[int]:
        if self.source == "default":
            return 0
        return self.turn_id

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "turn_id": self.turn_id,
            "visible": self.visible,
            "source": self.source,
        }

    @classmethod
    def from_value(cls, value) -> "ArtifactRevision":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict) and "content" in value:
            turn_id = value.get("turn_id")
            return cls(
                content=str(value["content"]),
                turn_id=turn_id if isinstance(turn_id, int) else None,
                visible=bool(value.get("visible", True)),
                source=str(value.get("source", "legacy")),
            )
        if isinstance(value, (list, tuple)) and value:
            # The old pair's second item was a per-artifact display index, not
            # causal provenance. Do not guess a turn ID during migration.
            return cls(content=str(value[0]), source="legacy")
        raise ValueError(f"Invalid artifact revision: {value!r}")

    # Keep old tests and small integrations that read revision[0] working.
    def __getitem__(self, index: int):
        if index == 0:
            return self.content
        if index == 1:
            return self.display_turn
        raise IndexError(index)


@dataclass
class ChatTurn:
    id: int
    user_message: str

    def to_dict(self) -> dict:
        return {"id": self.id, "user_message": self.user_message}

    @classmethod
    def from_value(cls, value) -> "ChatTurn":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(id=int(value["id"]), user_message=str(value["user_message"]))
        raise ValueError(f"Invalid chat turn: {value!r}")


@dataclass
class ChatWork:
    changed_artipaths: List[str]
    turn_id: Optional[int]
    message: Optional[str] = None


@dataclass
class PaludoroSession:
    artifacts: Dict[str, str] = field(default_factory=dict)
    default_artifacts: Dict[str, str] = field(default_factory=dict)
    artifact_versions: Dict[str, List[ArtifactRevision]] = field(default_factory=dict)
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
    chat_turns: List[ChatTurn] = field(default_factory=list)
    next_turn_id: int = 1
    # Runtime-only set. A last turn cannot be deleted or rerolled while its
    # pipeline can still commit output.
    active_turn_ids: set = field(default_factory=set)
    pending_chat_work: List[ChatWork] = field(default_factory=list)
    chat_worker_running: bool = False
    # List of functions (artipath, content) -> content
    on_save_hooks: List = field(default_factory=list)

    @property
    def chat_busy(self) -> bool:
        return self.chat_worker_running or bool(self.active_turn_ids)

    def revisions_for(self, artipath: str) -> List[ArtifactRevision]:
        revisions = self.artifact_versions.get(artipath, [])
        normalized = [ArtifactRevision.from_value(value) for value in revisions]
        if artipath not in self.artifact_versions or revisions != normalized:
            self.artifact_versions[artipath] = normalized
        return self.artifact_versions[artipath]

    def save_artifact(
        self,
        artipath: str,
        content: str,
        turn: Optional[int] = None,
        create_version: bool = True,
        source: Optional[str] = None,
    ):
        for hook in self.on_save_hooks:
            content = hook(artipath, content)

        clean_content = content.strip()
        if self.artifacts.get(artipath) == clean_content:
            return

        self.artifacts[artipath] = clean_content
        self.dirty_artifacts.add(artipath)

        if turn == -1:
            turn = None
        revision_source = source or ("agent" if turn is not None else "manual")
        revision = ArtifactRevision(
            content=clean_content,
            turn_id=turn,
            visible=create_version,
            source=revision_source,
        )
        self.revisions_for(artipath).append(revision)

    def begin_chat_turn(self, user_message: str) -> ChatTurn:
        turn = ChatTurn(self.next_turn_id, user_message)
        self.next_turn_id += 1
        self.chat_turns.append(turn)
        return turn

    def last_chat_turn(self) -> Optional[ChatTurn]:
        return self.chat_turns[-1] if self.chat_turns else None

    def rollback_turn(self, turn_id: int) -> Tuple[List[str], List[str]]:
        """Remove every artifact mutation caused by one chat turn.

        Returns affected artifact paths and removed revision contents. The
        caller uses the latter to garbage-collect unreferenced image files.
        """
        affected: List[str] = []
        removed_contents: List[str] = []

        for artipath in list(self.artifact_versions):
            revisions = self.revisions_for(artipath)
            retained = []
            removed = []
            for revision in revisions:
                if revision.turn_id == turn_id:
                    removed.append(revision)
                else:
                    retained.append(revision)

            if not removed:
                continue

            affected.append(artipath)
            removed_contents.extend(revision.content for revision in removed)
            if retained:
                self.artifact_versions[artipath] = retained
                self.artifacts[artipath] = retained[-1].content
            else:
                self.artifact_versions.pop(artipath, None)
                self.artifacts.pop(artipath, None)
            self.dirty_artifacts.add(artipath)

        return affected, removed_contents

    def visible_versions(self, artipath: str) -> List[List[object]]:
        return [
            [revision.content, revision.display_turn]
            for revision in self.revisions_for(artipath)
            if revision.visible
        ]

    def delete_visible_version(self, artipath: str, version_index: int) -> List[str]:
        revisions = self.revisions_for(artipath)
        visible_indices = [
            i for i, revision in enumerate(revisions) if revision.visible
        ]
        if version_index < 0 or version_index >= len(visible_indices):
            raise IndexError(version_index)
        if len(visible_indices) <= 1:
            raise ValueError("Cannot delete the last version")

        removed = revisions.pop(visible_indices[version_index])
        if revisions:
            self.artifacts[artipath] = revisions[-1].content
        else:
            self.artifact_versions.pop(artipath, None)
            self.artifacts.pop(artipath, None)
        self.dirty_artifacts.add(artipath)
        return [removed.content]

    def request_cancel(self, agent_name: str):
        self.agent_cancel_signals.add(agent_name)

    def is_cancelled(self, agent_name: str) -> bool:
        return agent_name in self.agent_cancel_signals

    def clear_cancel(self, agent_name: str):
        self.agent_cancel_signals.discard(agent_name)

    def reset(self):
        old_artifacts = set(self.artifacts)
        self.artifacts.clear()
        self.artifact_versions.clear()
        for artipath, content in self.default_artifacts.items():
            clean_content = content.strip()
            self.artifacts[artipath] = clean_content
            self.artifact_versions[artipath] = [
                ArtifactRevision(clean_content, turn_id=0, source="default")
            ]
        self.chat_turns.clear()
        self.next_turn_id = 1
        self.active_turn_ids.clear()
        self.pending_chat_work.clear()
        self.chat_worker_running = False
        self.dirty_artifacts.update(old_artifacts | set(self.artifacts))

    def to_dict(self) -> dict:
        return {
            "schema_version": 2,
            "artifacts": self.artifacts,
            "artifact_versions": {
                artipath: [
                    revision.to_dict() for revision in self.revisions_for(artipath)
                ]
                for artipath in self.artifact_versions
            },
            "pipeline_triggers": self.pipeline_triggers,
            "pipeline_finishes": self.pipeline_finishes,
            "history_version": self.history_version,
            "chat_turns": [turn.to_dict() for turn in self.chat_turns],
            "next_turn_id": self.next_turn_id,
        }

    def from_dict(self, data: dict):
        schema_version = data.get("schema_version", 1)
        self.artifacts = {
            str(artipath): str(content)
            for artipath, content in data.get("artifacts", {}).items()
        }
        self.artifact_versions = {}
        for artipath, values in data.get("artifact_versions", {}).items():
            revisions = [ArtifactRevision.from_value(value) for value in values]
            if schema_version < 2 and revisions:
                default_content = self.default_artifacts.get(artipath)
                if (
                    default_content is not None
                    and revisions[0].content == default_content.strip()
                ):
                    revisions[0].turn_id = 0
                    revisions[0].source = "default"
            self.artifact_versions[str(artipath)] = revisions

        # Older create_version=False writes changed the current-value cache
        # without leaving a revision. Preserve that state as a hidden baseline.
        for artipath, content in self.artifacts.items():
            revisions = self.revisions_for(artipath)
            if not revisions or revisions[-1].content != content:
                revisions.append(
                    ArtifactRevision(content, visible=False, source="legacy-current")
                )

        self.pipeline_triggers = data.get("pipeline_triggers", 0)
        self.pipeline_finishes = data.get("pipeline_finishes", 0)
        self.history_version = data.get("history_version", 0)
        self.chat_turns = []
        for value in data.get("chat_turns", []):
            try:
                self.chat_turns.append(ChatTurn.from_value(value))
            except (KeyError, TypeError, ValueError):
                continue
        stored_next_turn_id = data.get("next_turn_id", 1)
        minimum_next_turn_id = max((turn.id for turn in self.chat_turns), default=0) + 1
        self.next_turn_id = max(int(stored_next_turn_id), minimum_next_turn_id)
        self.active_turn_ids.clear()
        self.pending_chat_work.clear()
        self.chat_worker_running = False
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
