import sxpb
from sxpb.types import SxpbMany
from paludoro.state import parse_assistant_response, PaludoroSession
from paludoro.prompt import build_prompt


def test_save_and_inject():
    session = PaludoroSession()

    # Simulate first turn
    user_msg = "Set my mood to sleepy."
    history = SxpbMany([{"User": user_msg}])
    session.save_artifact("chat_history.sxpb", sxpb.dumps({"history": history}))

    # Built prompt should include the history artifact if requested as a remote
    prompt1 = build_prompt(session, input_artipaths=["chat_history.sxpb"])
    assert "### Artifacts" in prompt1
    assert "Set my mood to sleepy" in prompt1

    # Simulated response with a artifact save
    response = "I'll do that for you.\n>mood.sxpb\n(mood sleepy)"

    clean_resp, new_artifacts, malformed = parse_assistant_response(response)
    assert malformed == []
    assert clean_resp == "I'll do that for you."
    assert new_artifacts == {"mood.sxpb": "(mood sleepy)"}

    # Update session
    history.append({"Assistant": clean_resp})
    session.save_artifact("chat_history.sxpb", sxpb.dumps({"history": history}))
    for artipath, content in new_artifacts.items():
        session.save_artifact(artipath, content)

    # Simulate second turn
    history.append({"User": "What is my mood?"})
    session.save_artifact("chat_history.sxpb", sxpb.dumps({"history": history}))

    prompt2 = build_prompt(session, input_artipaths=["mood.sxpb", "chat_history.sxpb"])

    # Now it should have the artifact context!
    assert "### Artifacts" in prompt2
    assert "```sxpb < mood.sxpb" in prompt2
    assert "(mood sleepy)" in prompt2
    assert "What is my mood?" in prompt2

    print("✅ test_save_and_inject passed!")


if __name__ == "__main__":
    test_save_and_inject()
