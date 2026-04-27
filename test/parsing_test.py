from paludoro.state import parse_assistant_response


def test_failing_llm_message():
    # User's message exactly as provided (with suspected trailing spaces)
    msg = (
        "Yo, Alex! My name is **ChatBot**.  \n"
        "\n"
        "Here are the name files as requested:  \n"
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

    clean_msg, files = parse_assistant_response(msg)

    print(f"DEBUG: Files parsed: {list(files.keys())}")

    # These should be found
    assert "assistant_name.sxpb" in files, "Failed to parse assistant_name.sxpb"
    assert "user_name.sxpb" in files, "Failed to parse user_name.sxpb"

    # Check contents
    assert "(name ChatBot)" in files["assistant_name.sxpb"]
    assert "(name Alex)" in files["user_name.sxpb"]

    # Verify newlines (Surgical Parsing)
    # The message has a code block between text.
    # "as requested:  \n" (1 newline)
    # ```...```
    # "\n\n" (2 newlines)
    # The max is 2.
    assert "as requested:  \n\n```sxpb" not in clean_msg


def test_newline_normalization():
    msg = "Text before.\n\n\n```sxpb >file.sxpb\n(content)\n```\nText after."
    clean, files = parse_assistant_response(msg)
    # 3 newlines before, 1 after. Max is 3.
    assert clean == "Text before.\n\n\nText after."

    msg2 = "Top.\n```sxpb >f.sxpb\n(c)\n```\n\nBottom."
    clean2, files2 = parse_assistant_response(msg2)
    # 1 before, 2 after. Max is 2.
    assert clean2 == "Top.\n\nBottom."
    print("✅ test_newline_normalization passed!")


if __name__ == "__main__":
    test_failing_llm_message()
    test_newline_normalization()
    print("✅ All parsing tests passed!")
