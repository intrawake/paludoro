import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class PaludoroSession:
    files: Dict[str, str] = field(default_factory=dict)
    history: List[Tuple[str, str]] = field(default_factory=list)

    def save_file(self, name: str, content: str):
        self.files[name] = content.strip()

    def add_message(self, role: str, content: str):
        self.history.append((role, content))


def parse_assistant_response(content: str) -> Tuple[str, Dict[str, str]]:
    """
    Extracts virtual file saves from the assistant response.
    Pattern: [```sxpb] >filename.sxpb\n(content) [```]
    """
    # Matches: optional ```sxpb followed by >name.sxpb followed by content
    # until the next > or the end of a code block or end of string.
    pattern = r"(?:```sxpb\s+)?\s*>([\w\-\.]+)\n(.*?)(?=\s*(?:```|\n\s*>|$))"
    matches = re.finditer(pattern, content, re.DOTALL)

    new_files = {}
    last_end = 0
    clean_content_parts = []

    for match in matches:
        filename = match.group(1)
        file_content = match.group(2).strip()
        new_files[filename] = file_content

        # Add the text before the match to the clean content
        clean_content_parts.append(content[last_end : match.start()])
        last_end = match.end()

    clean_content_parts.append(content[last_end:])
    clean_response = "".join(clean_content_parts).strip()

    return clean_response, new_files
