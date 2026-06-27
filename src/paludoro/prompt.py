from paludoro.state import PaludoroSession
from typing import List, Optional


def build_instruction_section(
    output_artipaths: List[str],
    required_artipaths: Optional[List[str]] = None,
    accepted_artifacts: Optional[List[str]] = None,
) -> str:
    """Build the ### Instruction section for prompts and retry messages."""
    if required_artipaths is None:
        required_artipaths = []
    if accepted_artifacts is None:
        accepted_artifacts = []

    writable = list(dict.fromkeys(output_artipaths))
    if not writable:
        return ""

    lines = ["### Instruction"]
    lines.append("Write your output artifacts using appropriate syntax.")

    if accepted_artifacts:
        lines.append(f"Accepted: {', '.join(accepted_artifacts)}")

    if required_artipaths:
        must = [
            a
            for a in writable
            if a in required_artipaths and a not in accepted_artifacts
        ]
        may = [
            a
            for a in writable
            if a not in required_artipaths and a not in accepted_artifacts
        ]
        if must:
            lines.append(f"Required: {', '.join(must)}")
        if may:
            lines.append(f"Optional: {', '.join(may)}")
    else:
        pending = [a for a in writable if a not in accepted_artifacts]
        if pending:
            lines.append(f"You may write any of: {', '.join(pending)}")

    lines.append("")
    return "\n".join(lines)


def build_prompt(
    session: PaludoroSession,
    instruction: str = "Continue the conversation.",
    input_artipaths: Optional[List[str]] = None,
    output_artipaths: Optional[List[str]] = None,
    exclude_artipaths: Optional[List[str]] = None,
    required_artipaths: Optional[List[str]] = None,
) -> str:
    if input_artipaths is None:
        input_artipaths = []
    if output_artipaths is None:
        output_artipaths = []
    if exclude_artipaths is None:
        exclude_artipaths = []
    if required_artipaths is None:
        required_artipaths = []

    lines = []

    # 1. Instruction (no custom heading)
    if instruction:
        lines.append(instruction.strip())
        lines.append("")

    # Helper for adding artifacts with stripped whitespace and trailing newline
    def add_artifacts(artifact_list, arrow="<"):
        for artipath in artifact_list:
            if artipath in exclude_artipaths:
                continue
            if artipath in session.artifacts:
                content = session.artifacts[artipath].strip()
                if not content:
                    continue
                lang = "text" if artipath.endswith(".txt") else "sxpb"
                lines.append(f"```{lang} {arrow} {artipath}")
                lines.append(content)
                lines.append("```")
                lines.append("")

    has_artifacts = any(
        name in session.artifacts
        for name in (input_artipaths + output_artipaths)
        if name not in exclude_artipaths
    )
    if not input_artipaths and not output_artipaths and session.artifacts:
        has_artifacts = True

    if has_artifacts:
        lines.append("### Artifacts")

    # 2. Remote / Readonly artifacts
    if input_artipaths:
        add_artifacts(input_artipaths, arrow="<")

    # 3. Exposed artifacts
    if output_artipaths:
        add_artifacts(output_artipaths, arrow=">")

    # 4. Instruction: which artifacts can/must be written
    instruction_section = build_instruction_section(
        output_artipaths,
        required_artipaths=required_artipaths,
    )
    if instruction_section:
        lines.append(instruction_section)

    return "\n".join(lines)
