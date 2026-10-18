from __future__ import annotations

from devx.analysis.diff_analyzer import DiffAnalyzer, parse_unified_diff

SAMPLE_DIFF = """diff --git a/src/auth.py b/src/auth.py
index 1234567..89abcdef 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,6 +10,8 @@ def authenticate(user, password):
     if not user:
         return False
+    # API key check
+    secret_key = "abc12345678"
     return True
diff --git a/tests/test_auth.py b/tests/test_auth.py
deleted file mode 100644
index 1111111..0000000
--- a/tests/test_auth.py
+++ /dev/null
@@ -1,5 +0,0 @@
-def test_auth():
-    assert True
"""


def test_parse_unified_diff():
    files = parse_unified_diff(SAMPLE_DIFF)
    assert len(files) == 2

    auth_file = files[0]
    assert auth_file.old_path == "src/auth.py"
    assert auth_file.new_path == "src/auth.py"
    assert not auth_file.is_deleted
    assert len(auth_file.hunks) == 1
    assert auth_file.additions_count == 2

    test_file = files[1]
    assert test_file.old_path == "tests/test_auth.py"
    assert test_file.is_deleted
    assert test_file.deletions_count == 2


def test_diff_analyzer_risk_factors():
    analyzer = DiffAnalyzer()
    report = analyzer.analyze(SAMPLE_DIFF)

    assert report.total_files == 2
    assert report.total_additions == 2
    assert report.total_deletions == 2

    # Verify risk factors detected
    risk_texts = " ".join(report.risk_factors)
    assert "Deleted test file" in risk_texts
    assert "Modified security-sensitive file" in risk_texts
    assert "Possible hardcoded credential" in risk_texts


def test_diff_chunking():
    analyzer = DiffAnalyzer()
    chunks = analyzer.chunk_diff(SAMPLE_DIFF, max_chunk_chars=150)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert "diff --git" in chunk
