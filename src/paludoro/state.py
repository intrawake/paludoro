import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class PaludoroSession:
    artifacts: Dict[str, str] = field(default_factory=dict)
    default_artifacts: Dict[str, str] = field(default_factory=dict)
    # artipath -> List[(content, turn)]
    artifact_versions: Dict[str, List[Tuple[str, int]]] = field(default_factory=dict)
    dirty_artifacts: set = field(default_factory=set)
    running_agents: set = field(default_factory=set)
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
    """
    Extracts virtual artifact saves from the assistant response.
    Supports:
    1. ```sxpb >artipath.sxpb ... ```
    2. >artipath.sxpb (until next empty line or >)

    Returns (clean_response, new_artifacts, malformed_errors).
    malformed_errors lists any code blocks that use < (read prefix) instead of > (write prefix).
    """
    new_artifacts = {}
    malformed_errors: List[str] = []
    clean_response = content

    # 0. Detect malformed < prefix blocks before extraction
    malformed_pattern = r"```[ \t]*\w+[ \t]*<[ \t]*([\w\-\.]+)"
    for m in re.finditer(malformed_pattern, clean_response):
        name = m.group(1)
        malformed_errors.append(
            f"Wrong prefix on artifact block: used '<' for '{name}' — "
            "use '>' to write artifacts, '<' means read-only."
        )

    # 1. Match Markdown Code Blocks first
    # This matches: optional newlines + ```[lang] [whitespace] >artipath [whitespace]\n[content]\n``` [whitespace] + optional newlines
    block_pattern = r"(\n*)[ \t]*```[ \t]*\w+[ \t]*>[ \t]*([\w\-\.]+)[ \t]*\r?\n(.*?)\r?\n[ \t]*```[ \t]*(\n*)"
    matches = list(re.finditer(block_pattern, clean_response, re.DOTALL))

    # We process in reverse to not mess up indices when removing
    for match in reversed(matches):
        pre_newlines = len(match.group(1))
        artipath = match.group(2)
        artifact_content = match.group(3).strip()
        post_newlines = len(match.group(4))

        new_artifacts[artipath] = artifact_content

        # Replacement: the max number of newlines found around the block
        replacement = "\n" * max(pre_newlines, post_newlines)

        clean_response = (
            clean_response[: match.start()]
            + replacement
            + clean_response[match.end() :]
        )

    # 2. Match bare >artipath.sxpb lines
    # This matches the >artipath and then only subsequent lines that start with ( or ;
    # We allow optional trailing spaces after the artipath here too.
    bare_pattern = r"(?:\n|^)\s*>[ \t]*([\w\-\.]+)[ \t]*\n((?:^[ \t]*[\(;].*$\n?)+)"
    matches = list(re.finditer(bare_pattern, clean_response, re.MULTILINE))

    for match in reversed(matches):
        artipath = match.group(1)
        artifact_content = match.group(2).strip()
        # Only add if not already found in a code block
        if artipath not in new_artifacts:
            new_artifacts[artipath] = artifact_content
        # Remove from response
        clean_response = clean_response[: match.start()] + clean_response[match.end() :]

    return clean_response.strip(), new_artifacts, malformed_errors
