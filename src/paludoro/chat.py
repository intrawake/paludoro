from collections.abc import MutableSequence
import asyncio
import logging
import os

from paludoro.agent_pipeline import AgentPipeline
from paludoro.state import PaludoroSession
from paludoro.config import load_config, get_resource_path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _load_default_artifacts(config):
    """Load default artifact contents from expose_filepaths config."""
    default_artifacts = {}
    expose_filepaths = config.get("expose_filepaths", [])
    if isinstance(expose_filepaths, MutableSequence):
        for f in expose_filepaths:
            p = get_resource_path(f)
            if p.exists():
                default_artifacts[f] = p.read_text()
    return default_artifacts


async def chat_loop():
    config = load_config()

    main_agent = config.get("agent_dict", {}).get("main", {})
    if not main_agent:
        raise ValueError("Config must include a 'main' agent in agent_dict.")

    main_prompt_file = main_agent.get("prompt_as", {}).get("filepath")
    main_system_prompt_file = main_agent.get("system_prompt_as", {}).get("filepath")
    if not main_prompt_file and not main_system_prompt_file:
        raise ValueError(
            "Main agent: at least one of prompt_as or system_prompt_as "
            "must provide a filepath."
        )

    model_info = main_agent.get("generate_as", {}).get("text", {}).get("model", {})
    model = model_info.get("name") or os.getenv(
        "PALUDORO_MODEL", "openrouter/openrouter/free"
    )

    session = PaludoroSession(default_artifacts=_load_default_artifacts(config))

    logger.info(f"Paludoro Chat Started (Model: {model})")
    print(
        "Type 'exit' to quit. "
        "Use '```text >file.txt' or '```sxpb >file.sxpb' to save context.\n"
    )

    while True:
        try:
            user_input = await asyncio.to_thread(input, "You: ")
            if user_input.lower() in ["exit", "quit"]:
                break

            print("Assistant: Thinking...", end="\r")
            session.save_artifact("/dev/stdin", user_input)
            pipeline = AgentPipeline(config, session)
            await pipeline.on_artifacts_changed(["/dev/stdin"])

            response = session.artifacts.get("dialogue_line.txt", "")
            print(f"Assistant: {response}\n")

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Error: {e}")


def main():
    asyncio.run(chat_loop())


if __name__ == "__main__":
    main()
