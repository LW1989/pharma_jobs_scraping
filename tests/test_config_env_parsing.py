"""
.env value parsing.

scraper.config is imported by every runner, so a typo in a value must degrade
to the default rather than raise at import time.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
    os.environ.setdefault(_var, "test")

from scraper.config import env_flag, env_int  # noqa: E402


def test_flag_accepts_the_documented_true_values():
    for raw in ("1", "true", "TRUE", "yes", "on", " 1 "):
        assert env_flag(raw, False) is True, raw


def test_flag_accepts_the_documented_false_values():
    for raw in ("0", "false", "no", "off", " 0 "):
        assert env_flag(raw, True) is False, raw


def test_unset_blank_or_unrecognised_falls_back_to_the_default():
    for raw in (None, "", "   ", "maybe", "2"):
        assert env_flag(raw, True) is True, raw
        assert env_flag(raw, False) is False, raw


def test_int_falls_back_instead_of_raising_at_import_time():
    assert env_int("14", 7) == 14
    for raw in (None, "", "seven", "7.5"):
        assert env_int(raw, 7) == 7, raw
