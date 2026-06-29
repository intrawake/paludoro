from collections.abc import MutableMapping, MutableSequence
import logging
import os
from typing import Any, Dict, List, Optional, cast

import sxpb
from paludoro.state import PaludoroSession, parse_assistant_response
from paludoro.api import call_api, call_image_api
from paludoro.prompt import build_prompt, build_instruction_section
from paludoro.config import get_resource_path

logger = logging.getLogger(__name__)


class AgentPipeline:
    def __init__(self, config: MutableMapping, session: PaludoroSession):
        self.config = config
        self.session = session
        self.agent_dict = config.get("agent_dict", {})

        # Build dependency graph
        # For each agent, what artifacts trigger it?
        self.triggers: Dict[str, List[str]] = {}  # artipath -> list of agent names
        for agent_name, agent_config in self.agent_dict.items():
            remotes = agent_config.get("remotes", [])
            if isinstance(remotes, MutableSequence):
                for r in remotes:
                    self.triggers.setdefault(r, []).append(agent_name)

            prompt_as = agent_config.get("prompt_as", {})
            if isinstance(prompt_as, MutableMapping) and "remote" in prompt_as:
                r = prompt_as["remote"]
                self.triggers.setdefault(r, []).append(agent_name)

            system_prompt_as = agent_config.get("system_prompt_as", {})
            if (
                isinstance(system_prompt_as, MutableMapping)
                and "remote" in system_prompt_as
            ):
                r = system_prompt_as["remote"]
                self.triggers.setdefault(r, []).append(agent_name)

            generate_as = agent_config.get("generate_as", {})
            if "transcript" in generate_as:
                rba = generate_as["transcript"].get("remote_by_artipath", [])
                if isinstance(rba, MutableMapping):
                    for r in rba.keys():
                        self.triggers.setdefault(r, []).append(agent_name)
                elif isinstance(rba, MutableSequence):
                    for item in rba:
                        if isinstance(item, MutableMapping):
                            for r in item.keys():
                                self.triggers.setdefault(r, []).append(agent_name)

    async def run_agent(self, agent_name: str, triggered_by: Optional[str] = None):
        agent_config = self.agent_dict.get(agent_name, {})
        generate_as = agent_config.get("generate_as", {})
        prompt_as = agent_config.get("prompt_as", {})
        system_prompt_as = agent_config.get("system_prompt_as", {})

        logger.info(f"[{agent_name}] Running agent... (triggered by: {triggered_by})")
        self.session.running_agents.add(agent_name)

        try:
            # 1. Build prompt
            instruction = ""
            if isinstance(prompt_as, MutableMapping):
                if "filepath" in prompt_as:
                    # Resolve relative to preset dir (with user override)
                    path = get_resource_path(prompt_as["filepath"])
                    if path.exists():
                        instruction = path.read_text()
                elif "remote" in prompt_as:
                    r = prompt_as["remote"]
                    instruction = self.session.artifacts.get(r, "")

            system_instruction = ""
            if isinstance(system_prompt_as, MutableMapping):
                if "filepath" in system_prompt_as:
                    path = get_resource_path(system_prompt_as["filepath"])
                    if path.exists():
                        system_instruction = path.read_text()
                elif "remote" in system_prompt_as:
                    r = system_prompt_as["remote"]
                    system_instruction = self.session.artifacts.get(r, "")

            input_artipaths = agent_config.get("remotes", [])
            if not isinstance(input_artipaths, MutableSequence):
                input_artipaths = []

            output_artipaths = list(agent_config.get("exposes", []))
            if not isinstance(output_artipaths, MutableSequence):
                output_artipaths = []

            eba = agent_config.get("expose_by_artipath", [])
            required_artipaths = []
            exclude_artipaths = []
            eba_keys = set()
            if isinstance(eba, MutableSequence):
                for item in eba:
                    if isinstance(item, MutableMapping):
                        output_artipaths.extend(item.keys())
                        eba_keys.update(item.keys())
                        for artipath, details in item.items():
                            if isinstance(details, MutableMapping):
                                if details.get("required") is True:
                                    required_artipaths.append(artipath)
                                if details.get("visible") is False:
                                    exclude_artipaths.append(artipath)

            # Artifacts in exposes without a default in expose_by_artipath are required
            original_exposes = list(agent_config.get("exposes", []))
            if isinstance(original_exposes, MutableSequence):
                for artipath in original_exposes:
                    if artipath not in eba_keys and artipath not in required_artipaths:
                        required_artipaths.append(artipath)

            # Deduplicate while preserving order
            output_artipaths = list(dict.fromkeys(output_artipaths))

            if "image" in generate_as:
                # For image agents, we want the raw instruction text as the prompt
                prompt_text = instruction
            else:
                prompt_text = build_prompt(
                    self.session,
                    instruction=instruction,
                    input_artipaths=input_artipaths,
                    output_artipaths=output_artipaths,
                    exclude_artipaths=exclude_artipaths,
                    required_artipaths=required_artipaths,
                )

            # 2. Call model
            if "transcript" in generate_as:
                from sxpb.types import SxpbMany

                transcript_config = generate_as["transcript"]
                rba = transcript_config.get("remote_by_artipath", [])
                expose_artipath = transcript_config.get("expose")

                if not expose_artipath:
                    logger.error(f"[{agent_name}] transcript missing expose")
                    return []

                history_str = self.session.artifacts.get(expose_artipath, "")
                try:
                    history_data = (
                        sxpb.loads(history_str, precise=True)
                        if history_str
                        else {"history": SxpbMany([])}
                    )
                except Exception:
                    history_data = {"history": SxpbMany([])}

                history_list = (
                    cast(Any, history_data).get("history", [])
                    if isinstance(history_data, MutableMapping)
                    else []
                )
                if (
                    not hasattr(history_list, "__class__")
                    or history_list.__class__.__name__ != "SxpbMany"
                ):
                    if isinstance(history_list, list):
                        history_list = SxpbMany(history_list)
                    else:
                        history_list = SxpbMany([])

                changed = False

                def _process_details(remote_name, details):
                    nonlocal changed
                    if triggered_by and remote_name != triggered_by:
                        return
                    content = self.session.artifacts.get(remote_name, "").strip()
                    if content:
                        role = details.get("name", "Unknown")
                        history_list.append({role: content})
                        # Only clear /dev/stdin to avoid "empty" versions/UI flickers for responses
                        # Use create_version=False to avoid cluttering version history with clears
                        if remote_name == "/dev/stdin":
                            self.session.save_artifact(
                                remote_name, "", create_version=False
                            )
                        changed = True

                if isinstance(rba, MutableMapping):
                    for remote_name, details in rba.items():
                        if isinstance(details, MutableMapping):
                            _process_details(remote_name, details)
                elif isinstance(rba, MutableSequence):
                    for item in rba:
                        if isinstance(item, MutableMapping):
                            for remote_name, details in item.items():
                                _process_details(remote_name, details)

                if changed:
                    # Forgetfulness: rolling chat window via .old artifact split
                    forget_config = transcript_config.get("forgetfulness")
                    old_artipath = None
                    if isinstance(forget_config, MutableMapping):
                        threshold = forget_config.get("threshold_turn_count", 0)
                        preserve = forget_config.get("preserve_turn_count", 0)
                        old_artipath = forget_config.get("expose")
                        if threshold > 0 and preserve > 0 and old_artipath:
                            total_msgs = len(history_list)
                            old_content = self.session.artifacts.get(old_artipath, "")
                            old_exists = bool(old_content.strip())
                            logger.info(
                                f"[{agent_name}] forgetfulness check: total={total_msgs} threshold={threshold} preserve={preserve} old_exists={old_exists} old_content_len={len(old_content)}"
                            )
                            if total_msgs >= threshold and not old_exists:
                                logger.info(
                                    f"[{agent_name}] forgetfulness: FIRST CROSSING — overflow msgs to {old_artipath}"
                                )
                                # FIRST CROSSING: overflow to .old, keep preserve-count newest
                                if total_msgs > preserve:
                                    overflow = history_list[:-preserve]
                                    history_list = history_list[-preserve:]
                                    old_data = {"history": overflow}
                                    old_str = (
                                        "; Oldest chat history that will be forgotten after this turn.\n"
                                        + sxpb.dumps(old_data)
                                    )
                                    self.session.save_artifact(old_artipath, old_str)
                                    saved = self.session.artifacts.get(old_artipath, "")
                                    logger.info(
                                        f"[{agent_name}] forgetfulness: wrote {len(overflow)} msgs to {old_artipath}, saved_len={len(saved)}"
                                    )
                                else:
                                    logger.info(
                                        f"[{agent_name}] forgetfulness: threshold reached but total_msgs({total_msgs}) <= preserve({preserve}), no overflow to split"
                                    )
                            elif old_exists:
                                logger.info(
                                    f"[{agent_name}] forgetfulness: CLEANUP — trimming & clearing {old_artipath}"
                                )
                                # CLEANUP: trim history to preserve-count, clear .old
                                if total_msgs > preserve:
                                    history_list = history_list[-preserve:]
                                self.session.save_artifact(
                                    old_artipath, "", create_version=False
                                )
                                logger.info(
                                    f"[{agent_name}] forgetfulness: trimmed to {len(history_list)} msgs, cleared {old_artipath}"
                                )
                            else:
                                logger.info(
                                    f"[{agent_name}] forgetfulness: no action (total={total_msgs} < threshold={threshold} or old_exists={old_exists})"
                                )

                    new_history_data = {"history": history_list}
                    new_history_str = sxpb.dumps(new_history_data)
                    self.session.save_artifact(expose_artipath, new_history_str)
                    logger.info(f"[{agent_name}] transcript updated {expose_artipath}")
                    changed_artifacts = [expose_artipath]
                    if old_artipath:
                        changed_artifacts.append(old_artipath)
                    return changed_artifacts
                return []

            if "text" in generate_as:
                if not instruction and not system_instruction:
                    logger.error(
                        f"[{agent_name}] At least one of prompt_as or system_prompt_as must provide an instruction."
                    )
                    return []

                # Build default_sxpb_types from expose_by_artipath defaults
                default_sxpb_types = {}
                if isinstance(eba, MutableSequence):
                    for item in eba:
                        if isinstance(item, MutableMapping):
                            for artipath, details in item.items():
                                if isinstance(details, MutableMapping):
                                    default_file = details.get("default")
                                    if default_file and artipath.endswith(".sxpb"):
                                        path = get_resource_path(default_file)
                                        if path.exists():
                                            try:
                                                parsed = sxpb.loads(
                                                    path.read_text(), precise=True
                                                )
                                                default_sxpb_types[artipath] = type(
                                                    parsed
                                                ).__name__
                                            except Exception:
                                                pass

                model_info = generate_as["text"].get("model", {})
                model_name = model_info.get("name", "openrouter/openrouter/free")
                api_url = model_info.get("api_url") or os.getenv("OPENAI_API_BASE")
                if not api_url:
                    raise ValueError(
                        f"[{agent_name}] Required API URL is missing. Set it in config or via OPENAI_API_BASE environment variable."
                    )
                api_key = model_info.get("api_key")
                assistant_role = model_info.get("assistant_role", "assistant")
                record_content = self.config.get("record_request_content", False)

                # Initialize messages list with system prompt (if any) then user prompt
                messages = []
                if system_instruction:
                    messages.append({"role": "system", "content": system_instruction})
                messages.append({"role": "user", "content": prompt_text})

                accepted_so_far = set()
                clean_resp = ""

                MAX_ATTEMPTS = 3
                for attempt in range(MAX_ATTEMPTS):
                    response_raw = await call_api(
                        model_name,
                        messages,
                        api_url,
                        api_key=api_key,
                        record_content=record_content,
                        agent_name=agent_name,
                    )
                    if not response_raw:
                        logger.error(f"[{agent_name}] Failed to get response.")
                        return sorted(accepted_so_far) if accepted_so_far else []

                    clean_resp, new_artifacts, malformed_errors = (
                        parse_assistant_response(response_raw)
                    )

                    valid_artifacts = {}
                    accepted_names = []
                    retry_names = []
                    invalid_names = []
                    artifact_errors = {}

                    # Surface malformed artifact blocks as retry-worthy errors
                    if malformed_errors:
                        retry_names.append("response_format")
                        artifact_errors["response_format"] = malformed_errors[0]

                    for a, content in new_artifacts.items():
                        if output_artipaths and a not in output_artipaths:
                            invalid_names.append(a)
                            continue

                        if a.endswith(".sxpb"):
                            try:
                                parsed = sxpb.loads(content, precise=True)
                            except Exception as e:
                                retry_names.append(a)
                                artifact_errors[a] = str(e)
                                continue

                            # Type-check against default artifact type
                            if a in default_sxpb_types:
                                expected_type = default_sxpb_types[a]
                                actual_type = type(parsed).__name__
                                if actual_type != expected_type:
                                    retry_names.append(a)
                                    if expected_type == "SxpbNest":
                                        hint = 'A nest must begin with ("") as its first entry.'
                                    else:
                                        hint = f"Expected {expected_type} but got {actual_type}."
                                    artifact_errors[a] = (
                                        f"Wrong top-level SxPB type: expected {expected_type} but got {actual_type}. {hint}"
                                    )
                                    continue

                        valid_artifacts[a] = content
                        accepted_names.append(a)

                    # Check missing required artifacts
                    for a in required_artipaths:
                        if a not in new_artifacts and a not in accepted_so_far:
                            if a not in retry_names:
                                retry_names.append(a)
                                artifact_errors[a] = f"Missing required artifact: {a}"

                    if retry_names or invalid_names:
                        messages.append(
                            {"role": assistant_role, "content": response_raw}
                        )

                        # Partial acceptance: save valid artifacts immediately
                        for a, content in valid_artifacts.items():
                            if self.session.artifacts.get(a) != content:
                                self.session.save_artifact(a, content)
                                accepted_so_far.add(a)

                        # Build structured error report
                        failed_names = list(retry_names) + list(invalid_names)
                        lines = ["Failed output artifacts:"]
                        for name in failed_names:
                            lines.append(f"- {name}")
                        lines.append("")

                        # Add errors for invalid names (not in output_artipaths)
                        for name in invalid_names:
                            if name not in artifact_errors:
                                artifact_errors[name] = (
                                    "Not a valid output artifact — remove this artifact."
                                )

                        instruction_section = build_instruction_section(
                            output_artipaths,
                            required_artipaths=required_artipaths,
                            accepted_artifacts=sorted(accepted_so_far)
                            if accepted_so_far
                            else None,
                        )

                        for name, err in artifact_errors.items():
                            lines.append(f"### Error for: {name}")
                            lines.append(err)
                            lines.append("")

                        if instruction_section:
                            lines.append(instruction_section.rstrip("\n"))
                        lines.append("")

                        lines.append(
                            "Please retry, providing valid content only for the failed artifacts."
                        )
                        error_msg = "\n".join(lines)
                        messages.append({"role": "user", "content": error_msg})
                        logger.warning(
                            f"[{agent_name}] Attempt {attempt + 1}: Generated invalid output."
                        )
                        continue

                    logger.info(
                        f"[{agent_name}] Generated artifacts: {list(new_artifacts.keys())}"
                    )

                    # Save new artifacts (don't overwrite already-accepted ones)
                    for artipath, content in new_artifacts.items():
                        if artipath not in accepted_so_far:
                            if self.session.artifacts.get(artipath) != content:
                                self.session.save_artifact(artipath, content)
                                accepted_so_far.add(artipath)

                    return sorted(accepted_so_far)

                logger.error(f"[{agent_name}] Failed after {MAX_ATTEMPTS} attempts.")
                return sorted(accepted_so_far) if accepted_so_far else []

            elif "image" in generate_as:
                if not instruction:
                    logger.error(
                        f"[{agent_name}] prompt_as must provide an instruction for image generation."
                    )
                    return []

                model_info = generate_as["image"].get("model", {})
                model_name = model_info.get("name", "sd.cpp/flux2-klein")
                api_url = model_info.get("api_url") or os.getenv("OPENAI_API_BASE")
                if not api_url:
                    raise ValueError(
                        f"[{agent_name}] Required API URL is missing. Set it in config or via OPENAI_API_BASE environment variable."
                    )
                api_key = model_info.get("api_key") or os.getenv("OPENAI_API_KEY")
                record_content = self.config.get("record_request_content", False)

                expose_artifact = generate_as["image"].get("expose", "image.png")

                logger.info(f"[{agent_name}] Image generation triggered.")

                image_data = await call_image_api(
                    model_name,
                    prompt_text,
                    api_url,
                    api_key=api_key,
                    record_content=record_content,
                    agent_name=agent_name,
                )
                if image_data:
                    self.session.save_artifact(expose_artifact, image_data)
                    return [expose_artifact]
                else:
                    logger.error(f"[{agent_name}] Failed to generate image.")

            return []
        finally:
            if agent_name in self.session.running_agents:
                self.session.running_agents.remove(agent_name)

    async def on_artifacts_changed(self, changed_artipaths: List[str]):
        # BFS through triggers
        queue = list(changed_artipaths)
        agent_run_counts: Dict[str, int] = {}

        while queue:
            artipath = queue.pop(0)
            agents_to_run = set()
            for a in self.triggers.get(artipath, []):
                agent_config = self.agent_dict.get(a, {})
                generate_as = agent_config.get("generate_as", {})
                limit = 2 if "transcript" in generate_as else 1

                if agent_run_counts.get(a, 0) < limit:
                    agents_to_run.add(a)

            # Sort or just iterate. Set iteration is non-deterministic, but keeping as is.
            for agent_name in agents_to_run:
                new_changed = await self.run_agent(agent_name, triggered_by=artipath)
                agent_run_counts[agent_name] = agent_run_counts.get(agent_name, 0) + 1

                if new_changed:
                    queue.extend(new_changed)
