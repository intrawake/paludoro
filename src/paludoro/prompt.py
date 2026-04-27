from paludoro.state import PaludoroSession


def build_prompt(
    session: PaludoroSession, instruction: str = "Continue the conversation."
) -> str:
    lines = []

    # 1. File Context
    if session.files:
        lines.append("### File Context")
        for name, content in session.files.items():
            lines.append(f"```sxpb {name}")
            lines.append(content)
            lines.append("```")
        lines.append("")

    # 2. History
    lines.append("### Conversation History")
    for role, content, _ in session.history:
        lines.append(f"**{role.capitalize()}**: {content}")
    lines.append("")

    # 3. Instruction
    lines.append("### Instruction")
    lines.append(instruction)
    lines.append("")
    lines.append(
        "To save or overwrite a file in your context, use the following syntax:"
    )
    lines.append("```sxpb >filename.sxpb")
    lines.append("(field_name value)")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)
