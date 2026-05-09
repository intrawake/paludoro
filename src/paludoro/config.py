from collections.abc import MutableMapping
import os
from pathlib import Path
import sxpb


def get_config_dir() -> Path:
    paludoro_config_dir = os.getenv("PALUDORO_CONFIG_DIR")
    if paludoro_config_dir:
        return Path(paludoro_config_dir)
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "paludoro"
    return Path.home() / ".config" / "paludoro"


def get_preset_dir() -> Path:
    # src/paludoro/config.py -> src/paludoro -> src -> project_root -> preset
    return Path(__file__).parent.parent.parent / "preset"


def get_resource_path(filename: str) -> Path:
    # Try user config dir first
    user_path = get_config_dir() / filename
    if user_path.exists():
        return user_path
    # Fall back to preset dir
    return get_preset_dir() / filename


def load_config() -> MutableMapping:
    config_path = get_resource_path("config.sxpb")
    if config_path.exists():
        try:
            loaded = sxpb.load(str(config_path), precise=True)
            if isinstance(loaded, MutableMapping):
                return loaded
        except Exception as e:
            print(f"Error loading config from {config_path}: {e}")
    return {}
