from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from devx.config import Settings
from devx.review.reviewer import CodeReviewer


def test_review_empty_diff(tmp_path: Path):
    settings = Settings(workspace=tmp_path, api_key="sk-test")
    reviewer = CodeReviewer(settings)

    report = reviewer.review_diff("")
    assert report.verdict == "APPROVED"
    assert "empty" in report.summary.lower()


def test_review_diff_with_mocked_llm(tmp_path: Path):
    settings = Settings(workspace=tmp_path, api_key="sk-test")
    reviewer = CodeReviewer(settings)

    mock_llm_json = {
        "verdict": "CHANGES_REQUESTED",
        "summary": "Detected missing null check and potential SQL injection vulnerability.",
        "comments": [
            {
                "file": "src/user.py",
                "line": 45,
                "severity": "BLOCKER",
                "critic": "Security",
                "message": "Raw SQL query concatenation detected.",
                "suggested_patch": "- query = 'SELECT * FROM users WHERE id = ' + uid\n+ query = 'SELECT * FROM users WHERE id = %s', (uid,)",
            },
            {
                "file": "src/user.py",
                "line": 52,
                "severity": "WARNING",
                "critic": "Performance",
                "message": "Database connection not closed in finally block.",
            },
        ],
    }

    mock_response = MagicMock()
    mock_response.content = json.dumps(mock_llm_json)

    diff_sample = """diff --git a/src/user.py b/src/user.py
@@ -40,5 +40,15 @@ def get_user(uid):
+    query = 'SELECT * FROM users WHERE id = ' + uid
+    return db.execute(query)
"""

    with patch("devx.review.reviewer.invoke_with_retry", return_value=mock_response):
        report = reviewer.review_diff(diff_sample)

    assert report.verdict == "CHANGES_REQUESTED"
    assert len(report.comments) == 2
    assert report.comments[0].severity == "BLOCKER"
    assert report.comments[0].critic == "Security"
    assert report.comments[1].critic == "Performance"

    md = report.to_markdown()
    assert "# Code Review Report — CHANGES_REQUESTED" in md
    assert "[BLOCKER] `src/user.py`:45 (Security)" in md

    # Verify save_report
    saved_path = reviewer.save_report(report, name="test")
    assert saved_path.exists()
    assert saved_path.read_text(encoding="utf-8") == md
