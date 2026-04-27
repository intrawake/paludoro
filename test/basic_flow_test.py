from paludoro.state import parse_assistant_response, PaludoroSession
from paludoro.prompt import build_prompt


def test_save_and_inject():
    session = PaludoroSession()

    # Simulate first turn
    user_msg = "Set my mood to sleepy."
    session.add_message("user", user_msg)

    # Built prompt should have history but no files
    prompt1 = build_prompt(session)
    assert "### File Context" not in prompt1
    assert "Set my mood to sleepy" in prompt1

    # Simulated response with a file save
    response = "I'll do that for you.\n>mood.sxpb\n(mood sleepy)"

    clean_resp, new_files = parse_assistant_response(response)
    assert clean_resp == "I'll do that for you."
    assert new_files == {"mood.sxpb": "(mood sleepy)"}

    # Update session
    session.add_message("assistant", clean_resp, raw_content=response)
    for name, content in new_files.items():
        session.save_file(name, content)

    # Simulate second turn
    session.add_message("user", "What is my mood?")

    prompt2 = build_prompt(session)

    # Now it should have the file context!
    assert "### File Context" in prompt2
    assert "```sxpb mood.sxpb" in prompt2
    assert "(mood sleepy)" in prompt2

    print("✅ test_save_and_inject passed!")


if __name__ == "__main__":
    test_save_and_inject()
