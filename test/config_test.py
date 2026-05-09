import os
import tempfile
from pathlib import Path
from paludoro.config import get_config_dir, get_resource_path, load_config


def test_config_dir_env_var():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["PALUDORO_CONFIG_DIR"] = tmpdir
        try:
            assert get_config_dir() == Path(tmpdir)
        finally:
            del os.environ["PALUDORO_CONFIG_DIR"]


def test_resource_override():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["PALUDORO_CONFIG_DIR"] = tmpdir
        try:
            # Create a fake config in the temp dir
            config_file = Path(tmpdir) / "config.sxpb"
            config_file.write_text("(server (port 9999))")

            # Verify load_config sees it
            config = load_config()
            assert config.get("server", {}).get("port") == 9999

            # Verify get_resource_path priorities the override
            assert get_resource_path("config.sxpb") == config_file
        finally:
            del os.environ["PALUDORO_CONFIG_DIR"]


def test_resource_fallback():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["PALUDORO_CONFIG_DIR"] = tmpdir
        try:
            # No config in temp dir, should fall back to preset
            config = load_config()
            # The current preset should have server.port 8000
            assert config.get("server", {}).get("port") == 8000

            # Verify path falls back to preset
            preset_path = get_resource_path("config.sxpb")
            assert "preset" in str(preset_path)
        finally:
            del os.environ["PALUDORO_CONFIG_DIR"]
