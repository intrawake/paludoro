"""Regression test: every .sxpb file under preset/ must be valid SxPB.

Catches syntax errors (unbalanced parens, bad atoms, etc.) before they
cause confusing runtime failures in agents or the UI.
"""

import sxpb
import pytest

from paludoro.config import get_preset_dir

_SXPB_FILES = sorted(
    p.relative_to(get_preset_dir()) for p in get_preset_dir().rglob("*.sxpb")
)
assert _SXPB_FILES, f"No .sxpb files found in {get_preset_dir()}"


@pytest.mark.parametrize("sxpb_file", _SXPB_FILES, ids=str)
def test_preset_sxpb_parses(sxpb_file):
    """Each .sxpb file in preset/ must be syntactically valid SxPB."""
    full_path = get_preset_dir() / sxpb_file
    content = full_path.read_text(encoding="utf-8")
    # loads() raises on invalid syntax (UnexpectedEOF, etc.)
    result = sxpb.loads(content)
    assert isinstance(result, (dict, list))
