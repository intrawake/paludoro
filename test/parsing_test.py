from paludoro.state import parse_assistant_response


def test_failing_llm_message():
    # User's message exactly as provided (with suspected trailing spaces)
    msg = (
        "Yo, Alex! My name is **ChatBot**.  \n"
        "\n"
        "Here are the name artifacts as requested:  \n"
        "```sxpb >assistant_name.sxpb  \n"
        "(name ChatBot)  \n"
        "```  \n"
        "\n"
        "```sxpb >user_name.sxpb  \n"
        "(name Alex)  \n"
        "```  \n"
        "\n"
        "Let me know how else I can help! 👍"
    )

    clean_msg, artifacts, malformed = parse_assistant_response(msg)
    assert malformed == []

    print(f"DEBUG: Artifacts parsed: {list(artifacts.keys())}")

    # These should be found
    assert "assistant_name.sxpb" in artifacts, "Failed to parse assistant_name.sxpb"
    assert "user_name.sxpb" in artifacts, "Failed to parse user_name.sxpb"

    # Check contents
    assert "(name ChatBot)" in artifacts["assistant_name.sxpb"]
    assert "(name Alex)" in artifacts["user_name.sxpb"]

    # Verify newlines (Surgical Parsing)
    # The message has a code block between text.
    # "as requested:  \n" (1 newline)
    # ```...```
    # "\n\n" (2 newlines)
    # The max is 2.
    assert "as requested:  \n\n```sxpb" not in clean_msg


def test_newline_normalization():
    msg = "Text before.\n\n\n```sxpb >file.sxpb\n(content)\n```\nText after."
    clean, artifacts, malformed = parse_assistant_response(msg)
    assert malformed == []
    # 3 newlines before, 1 after. Max is 3.
    assert clean == "Text before.\n\n\nText after."

    msg2 = "Top.\n```sxpb >f.sxpb\n(c)\n```\n\nBottom."
    clean2, artifacts2, malformed2 = parse_assistant_response(msg2)
    assert malformed2 == []
    # 1 before, 2 after. Max is 2.
    assert clean2 == "Top.\n\nBottom."
    print("✅ test_newline_normalization passed!")


def test_text_artifact_parsing():
    msg = (
        "Here is your design:\n\n"
        "```text > image.txt\n"
        "A cute sprite detective with auburn hair.\n"
        "```\n"
        "\n"
        "Hope you like it!"
    )
    clean, artifacts, malformed = parse_assistant_response(msg)
    assert malformed == []
    assert "image.txt" in artifacts
    assert artifacts["image.txt"] == "A cute sprite detective with auburn hair."
    assert "A cute sprite detective" not in clean
    print("✅ test_text_artifact_parsing passed!")


if __name__ == "__main__":
    test_failing_llm_message()
    test_newline_normalization()
    test_text_artifact_parsing()
    print("✅ All parsing tests passed!")
