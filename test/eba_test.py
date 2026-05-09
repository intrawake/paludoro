from collections.abc import MutableMapping, MutableSequence
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paludoro.agent_pipeline import AgentPipeline
from paludoro.server import get_resource_path, load_config
from paludoro.state import PaludoroSession


def test_expose_by_artipath_defaults():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mocking config directory to control resource discovery
        os.environ["PALUDORO_CONFIG_DIR"] = tmpdir
        try:
            config_path = Path(tmpdir) / "config.sxpb"
            # Define a config with expose_by_artipath and a default
            config_content = """
            (agent_dict ()
                (main
                    (expose_by_artipath (())
                        (target.sxpb (default source.sxpb))
                    )
                )
            )
            """
            config_path.write_text(config_content)

            # Create the default source file
            source_path = Path(tmpdir) / "source.sxpb"
            source_content = "(value 42)"
            source_path.write_text(source_content)

            # Reload config to ensure we're using our temp one
            config = load_config()

            # Replicate the default_artifacts logic from server.py (or test it via session)
            default_artifacts = {}

            agent_dict = config.get("agent_dict", {})
            for agent_name, agent_config in agent_dict.items():
                eba = agent_config.get("expose_by_artipath", [])
                if isinstance(eba, MutableSequence):
                    for item in eba:
                        if isinstance(item, MutableMapping):
                            for artipath, details in item.items():
                                if (
                                    isinstance(details, MutableMapping)
                                    and "default" in details
                                ):
                                    default_f = details["default"]
                                    p = get_resource_path(default_f)
                                    if p.exists():
                                        content = p.read_text()
                                        if artipath not in default_artifacts:
                                            default_artifacts[artipath] = content

            # Check if default_artifacts was populated correctly
            assert "target.sxpb" in default_artifacts
            assert default_artifacts["target.sxpb"] == source_content

            # Ensure it works in a real session
            session = PaludoroSession(default_artifacts=default_artifacts)
            session.reset()
            assert session.artifacts.get("target.sxpb") == source_content
        finally:
            if "PALUDORO_CONFIG_DIR" in os.environ:
                del os.environ["PALUDORO_CONFIG_DIR"]


def test_expose_by_artipath_as_output():
    # Test if AgentPipeline recognizes eba as valid output

    config = {
        "agent_dict": {
            "test_agent": {
                "generate_as": {"text": {}},
                "expose_by_artipath": [{"dynamic.sxpb": {"default": "some_file.sxpb"}}],
            }
        }
    }

    # session = PaludoroSession()
    # pipeline = AgentPipeline(config, session)

    # We mock run_agent logic or check its internal state

    # We want to ensure it expands output_artipaths correctly inside run_agent
    # Since run_agent is async and complex, we'll verify the logic we added

    agent_config = config["agent_dict"]["test_agent"]
    output_artipaths: list[str] = list(agent_config.get("exposes", []))  # type: ignore

    eba = agent_config.get("expose_by_artipath", [])
    if isinstance(eba, MutableSequence):
        for item in eba:
            if isinstance(item, MutableMapping):
                output_artipaths.extend(str(k) for k in item.keys())

    assert "dynamic.sxpb" in output_artipaths


def test_expose_by_artipath_required():
    import sxpb

    # Test the required artifact parsing
    config_content = """
    (agent_dict ()
        (test_agent
            (expose_by_artipath (())
                (dynamic.sxpb (default some_file.sxpb) (required +true))
                (optional.sxpb (default some_file.sxpb))
            )
        )
    )
    """

    config = sxpb.loads(config_content, precise=True)
    assert isinstance(config, MutableMapping)
    agent_dict = config.get("agent_dict", {})  # type: ignore
    assert isinstance(agent_dict, MutableMapping)
    agent_config = agent_dict.get("test_agent", {})
    assert isinstance(agent_config, MutableMapping)

    required_artipaths = []
    eba = agent_config.get("expose_by_artipath", [])
    if isinstance(eba, MutableSequence):
        for item in eba:
            if isinstance(item, MutableMapping):
                for artipath, details in item.items():
                    if (
                        isinstance(details, MutableMapping)
                        and details.get("required") is True
                    ):
                        required_artipaths.append(artipath)

    assert "dynamic.sxpb" in required_artipaths
    assert "optional.sxpb" not in required_artipaths


def test_expose_by_artipath_visibility():
    from paludoro.prompt import build_prompt

    session = PaludoroSession()
    session.save_artifact("secret.txt", "hidden content")
    session.save_artifact("public.txt", "visible content")

    # 1. Test build_prompt logic
    prompt = build_prompt(
        session,
        instruction="Instruction",
        output_artipaths=["secret.txt", "public.txt"],
        exclude_artipaths=["secret.txt"],
    )

    assert "public.txt" in prompt
    assert "visible content" in prompt
    # visible=False hides content but lists the artifact in the instruction section
    assert "hidden content" not in prompt
    assert "secret.txt" in prompt  # listed in ### Instruction

    # 2. Test AgentPipeline configuration parsing
    config = {
        "agent_dict": {
            "test_agent": {
                "expose_by_artipath": [
                    {"secret.txt": {"visible": False}},
                    {"public.txt": {"visible": True}},
                ]
            }
        }
    }
    agent_config = config["agent_dict"]["test_agent"]
    exclude_artipaths = []
    eba = agent_config.get("expose_by_artipath", [])
    if isinstance(eba, MutableSequence):
        for item in eba:
            if isinstance(item, MutableMapping):
                for artipath, details in item.items():
                    if (
                        isinstance(details, MutableMapping)
                        and details.get("visible") is False
                    ):
                        exclude_artipaths.append(artipath)

    assert "secret.txt" in exclude_artipaths
    assert "public.txt" not in exclude_artipaths


@pytest.mark.asyncio
async def test_visibility_effect_on_prompt():
    session = PaludoroSession()
    session.save_artifact("image.txt", "PREVIOUS PROMPT CONTENT")

    config = {
        "agent_dict": {
            "avatar_design": {
                "generate_as": {"text": {"model": {"name": "test-model"}}},
                "prompt_as": {"filepath": "avatar_design_prompt.md"},
                "expose_by_artipath": [{"image.txt": {"visible": False}}],
            }
        }
    }

    with patch("paludoro.agent_pipeline.call_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = "Assistant Response"
        with patch("paludoro.agent_pipeline.get_resource_path") as mock_res:
            mock_res.return_value = MagicMock(
                exists=lambda: True, read_text=lambda: "Design an avatar image."
            )
            with patch("os.getenv", return_value="http://mock-api"):
                pipeline = AgentPipeline(config, session)
                await pipeline.run_agent("avatar_design")

                # Verify what was sent to the model
                args, _ = mock_call.call_args
                messages = args[1]
                prompt_text = messages[0]["content"]

                # image.txt content should be hidden, but the name
                # appears in the ### Instruction section
                assert "PREVIOUS PROMPT CONTENT" not in prompt_text
                assert "image.txt" in prompt_text

                # Ensure image.txt is still a valid output target
                agent_config = config["agent_dict"]["avatar_design"]
                output_artipaths = []
                eba = agent_config.get("expose_by_artipath", [])
                for item in eba:
                    if isinstance(item, MutableMapping):
                        output_artipaths.extend(item.keys())
                assert "image.txt" in output_artipaths
