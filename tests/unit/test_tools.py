from __future__ import annotations

import re

from fleet_rlm.utils.tools import regex_extract


def test_regex_extract_basic_matches():
    text = "cat dog cat bird"
    assert regex_extract(text, r"cat") == ["cat", "cat"]


def test_regex_extract_groups():
    text = "x=12 y=99"
    assert regex_extract(text, r"([xy])=(\d+)") == [("x", "12"), ("y", "99")]


def test_regex_extract_flags():
    text = "Error\nerror\nERROR"
    assert len(regex_extract(text, r"error", flags=re.IGNORECASE)) == 3
