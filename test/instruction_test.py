"""Tests for the instruction section builder — must never have duplicates or empty lists."""

from paludoro.prompt import build_instruction_section


def _assert_no_duplicates(text: str, section_name: str):
    """Assert that no line in the section repeats any artifact name."""
    lines = text.split("\n")
    for line in lines:
        if ":" in line and not line.startswith("###"):
            prefix, rest = line.split(":", 1)
            names = [n.strip() for n in rest.split(",") if n.strip()]
            seen = set()
            for name in names:
                assert name not in seen, (
                    f"Duplicate '{name}' in '{prefix}' ({section_name}): {line}"
                )
                seen.add(name)


def test_instruction_no_duplicates():
    """Artifact names must never appear more than once in any line."""
    # Simulates the real config where exposes + expose_by_artipath overlap
    output_artipaths = [
        "dialogue_line.txt",
        "cosmetic.sxpb",
        "mood.sxpb",
        "pose.sxpb",
        "scene.sxpb",
        "camera.sxpb",
        "style.sxpb",
        "cosmetic.sxpb",
        "mood.sxpb",
        "pose.sxpb",
        "scene.sxpb",
        "camera.sxpb",
        "style.sxpb",
    ]
    result = build_instruction_section(output_artipaths)
    _assert_no_duplicates(result, "no accepted")


def test_instruction_no_duplicates_with_accepted():
    """Even with accepted artifacts, no duplicates in must/may lists."""
    output_artipaths = [
        "dialogue_line.txt",
        "cosmetic.sxpb",
        "mood.sxpb",
        "cosmetic.sxpb",
        "mood.sxpb",
    ]
    result = build_instruction_section(
        output_artipaths,
        required_artipaths=["cosmetic.sxpb"],
        accepted_artifacts=["dialogue_line.txt"],
    )
    _assert_no_duplicates(result, "with accepted")


def test_instruction_empty_returns_empty_string():
    """No output_artipaths should return empty string."""
    result = build_instruction_section([])
    assert result == ""


def test_instruction_no_duplicates_required():
    """Required vs optional split must not duplicate names."""
    output_artipaths = [
        "a.sxpb",
        "b.sxpb",
        "a.sxpb",
        "b.sxpb",
        "c.sxpb",
    ]
    result = build_instruction_section(
        output_artipaths,
        required_artipaths=["a.sxpb"],
        accepted_artifacts=["c.sxpb"],
    )
    _assert_no_duplicates(result, "required/optional")
    # 'a.sxpb' should be in Required, 'b.sxpb' in Optional, 'c.sxpb' accepted
    assert "Required: a.sxpb" in result
    assert "Optional: b.sxpb" in result
    assert "c.sxpb" not in result or "Accepted:" in result


def test_instruction_shows_required_optional():
    """Artifacts without defaults are Required, with defaults are Optional."""
    output_artipaths = [
        "dialogue_line.txt",
        "cosmetic.sxpb",
        "mood.sxpb",
    ]
    result = build_instruction_section(
        output_artipaths,
        required_artipaths=["dialogue_line.txt"],
    )
    assert "Required: dialogue_line.txt" in result, (
        f"Expected Required to list dialogue_line.txt:\n{result}"
    )
    assert "Optional: cosmetic.sxpb, mood.sxpb" in result, (
        f"Expected Optional to list cosmetic.sxpb and mood.sxpb:\n{result}"
    )
    assert "You may write any of" not in result, (
        "Should not use fallback 'You may write any of' when required/optional is set"
    )
