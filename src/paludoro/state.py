import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class PaludoroSession:
    files: Dict[str, str] = field(default_factory=dict)
    # history stores (role, content, raw_content)
    # raw_content is used for rebuilding the virtual filesystem
    history: List[Tuple[str, str, str]] = field(default_factory=list)

    def save_file(self, name: str, content: str):
        self.files[name] = content.strip()

    def add_message(self, role: str, content: str, raw_content: str = ""):
        self.history.append((role, content, raw_content or content))

    def reset(self):
        self.files.clear()
        self.history.clear()

    def delete_last_turn(self):
        """Removes the last user message and the last assistant message."""
        # Pop assistant message
        if self.history and self.history[-1][0] == "assistant":
            self.history.pop()
        # Pop user message
        if self.history and self.history[-1][0] == "user":
            self.history.pop()
        self.rebuild_files()

    def pop_assistant_message(self):
        """Removes the last assistant message (for reroll)."""
        if self.history and self.history[-1][0] == "assistant":
            self.history.pop()
            self.rebuild_files()

    def rebuild_files(self):
        """Re-parses all history to restore the file state."""
        self.files.clear()
        for role, _, raw in self.history:
            if role == "assistant":
                _, new_files = parse_assistant_response(raw)
                for name, content in new_files.items():
                    self.save_file(name, content)


def parse_assistant_response(content: str) -> Tuple[str, Dict[str, str]]:
    """
    Extracts virtual file saves from the assistant response.
    Supports:
    1. ```sxpb >filename.sxpb ... ```
    2. >filename.sxpb (until next empty line or >)
    """
    new_files = {}
    clean_response = content

    # 1. Match Markdown Code Blocks first
    # This matches: optional newlines + ```sxpb [whitespace] >filename [whitespace]\n[content]\n``` [whitespace] + optional newlines
    block_pattern = r"(\n*)```sxpb\s+>([\w\-\.]+)[ \t]*\n(.*?)\n```[ \t]*(\n*)"
    matches = list(re.finditer(block_pattern, clean_response, re.DOTALL))

    # We process in reverse to not mess up indices when removing
    for match in reversed(matches):
        pre_newlines = len(match.group(1))
        filename = match.group(2)
        file_content = match.group(3).strip()
        post_newlines = len(match.group(4))

        new_files[filename] = file_content

        # Replacement: the max number of newlines found around the block
        replacement = "\n" * max(pre_newlines, post_newlines)

        clean_response = (
            clean_response[: match.start()]
            + replacement
            + clean_response[match.end() :]
        )

    # 2. Match bare >filename.sxpb lines
    # This matches the >filename and then only subsequent lines that start with ( or ;
    # We allow optional trailing spaces after the filename here too.
    bare_pattern = r"(?:\n|^)\s*>([\w\-\.]+)[ \t]*\n((?:^[ \t]*[\(;].*$\n?)+)"
    matches = list(re.finditer(bare_pattern, clean_response, re.MULTILINE))

    for match in reversed(matches):
        filename = match.group(1)
        file_content = match.group(2).strip()
        # Only add if not already found in a code block
        if filename not in new_files:
            new_files[filename] = file_content
        # Remove from response
        clean_response = clean_response[: match.start()] + clean_response[match.end() :]

    return clean_response.strip(), new_files
