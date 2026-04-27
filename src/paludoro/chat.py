import os
import json
import urllib.request
import time
from paludoro.state import PaludoroSession, parse_assistant_response
from paludoro.prompt import build_prompt


def call_api(model, prompt, api_url):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = json.dumps(payload).encode("utf-8")

    target_url = api_url
    if not target_url.endswith("/chat/completions"):
        if not target_url.endswith("/"):
            target_url += "/"
        target_url += "chat/completions"

    req = urllib.request.Request(
        target_url, data=data, headers={"Content-Type": "application/json"}
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                msg = res_data["choices"][0]["message"]
                content = msg.get("content")
                if content is None:
                    return ""
                return content.strip()
        except Exception as e:
            if attempt == 2:
                print(f"Failed API call after 3 attempts: {e}")
                return ""
            time.sleep(2)


def main():
    import sxpb
    from pathlib import Path

    config: dict = {}
    try:
        loaded = sxpb.load(
            str(Path(__file__).parent.parent.parent / "preset" / "config.sxpb")
        )
        if isinstance(loaded, dict):
            config = loaded
    except Exception:
        pass

    api_url = os.getenv(
        "PALUDORO_API_URL",
        config.get("chat_model", {}).get("api_url", "http://atomman-0-host:11435/v1"),
    )
    model = os.getenv(
        "PALUDORO_MODEL",
        config.get("chat_model", {}).get("name", "openrouter/openrouter/free"),
    )

    session = PaludoroSession()
    print(f"Paludoro Chat Started (Model: {model})")
    print("Type 'exit' to quit. Use 'sxpb >file.sxpb' to save context.\n")

    while True:
        try:
            user_input = input("You: ")
            if user_input.lower() in ["exit", "quit"]:
                break

            session.add_message("user", user_input)
            prompt = build_prompt(session)

            # Show a thinking indicator
            print("Assistant: Thinking...", end="\r")

            response_raw = call_api(model, prompt, api_url)
            clean_resp, new_files = parse_assistant_response(response_raw)

            # Update state
            session.add_message("assistant", clean_resp)
            for name, content in new_files.items():
                session.save_file(name, content)
                print(f"💾 Saved virtual file: {name}")

            print(f"Assistant: {clean_resp}\n")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    main()
