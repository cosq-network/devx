from __future__ import annotations

import pytest

from devx.token_optim.distillation import distill_test_output


def test_distills_pytest_failures():
    raw_output = """
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-8.3.4
rootdir: C:\\Projects\\devx
collected 50 items

tests/test_one.py .................................... [ 72%]
tests/test_two.py .............F                      [100%]

================================== FAILURES ===================================
_________________________________ test_widget _________________________________

    def test_widget():
>       assert 200 == 404
E       assert 200 == 404

tests/test_two.py:12: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_two.py::test_widget - assert 200 == 404
========================= 1 failed, 49 passed in 0.52s =========================
"""
    distilled = distill_test_output(raw_output)
    # Passed tests, rootdir, platform should be stripped
    assert "test session starts" not in distilled
    assert "collected 50 items" not in distilled
    assert "tests/test_one.py" not in distilled

    # The actual failure and assertion must be present
    assert "test_widget" in distilled
    assert "assert 200 == 404" in distilled
    assert "AssertionError" in distilled


def test_distills_python_traceback():
    raw_output = """
INFO:devx.app:Starting execution...
DEBUG:devx.db:Connecting to database...
Traceback (most recent call last):
  File "src/devx/engine.py", line 45, in execute
    result = compute(value)
  File "src/devx/engine.py", line 22, in compute
    raise KeyError("missing_key")
KeyError: 'missing_key'
"""
    distilled = distill_test_output(raw_output)
    assert "Starting execution" not in distilled
    assert "Connecting to database" not in distilled
    assert "Traceback (most recent call last):" in distilled
    assert "KeyError: 'missing_key'" in distilled
    assert "compute(value)" in distilled


def test_distills_linter_errors():
    raw_output = """
Scanning 45 files...
All checks completed in 0.04s.
src/devx/app.py:10:5: F401 `os` imported but unused
src/devx/util.py:25:1: E302 expected 2 blank lines, found 1
Found 2 errors.
"""
    distilled = distill_test_output(raw_output)
    assert "Scanning 45 files" not in distilled
    assert "src/devx/app.py:10:5: F401 `os` imported but unused" in distilled
    assert "src/devx/util.py:25:1: E302 expected 2 blank lines, found 1" in distilled


def test_distills_jest_failures():
    raw_output = """
PASS tests/auth.test.js
FAIL tests/user.test.js
  ● User Profile › updates name
    expect(received).toBe(expected) // Object.is equality

    Expected: "Alice"
    Received: "Bob"

      14 |   const user = updateName("Alice");
    > 15 |   expect(user.name).toBe("Alice");
Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 4 passed, 5 total
"""
    distilled = distill_test_output(raw_output)
    assert "PASS tests/auth.test.js" not in distilled
    assert "FAIL tests/user.test.js" in distilled
    assert "Expected: \"Alice\"" in distilled
    assert "Received: \"Bob\"" in distilled


def test_bounded_character_limit():
    huge_failure = "E   assert False\n" * 500
    distilled = distill_test_output(huge_failure, max_chars=300)
    assert len(distilled) <= 350
    assert "... [truncated diagnostic] ..." in distilled


def test_empty_or_whitespace_output():
    assert distill_test_output("") == ""
    assert distill_test_output("   \n\t  ") == ""
