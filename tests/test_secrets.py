"""Secret-guard tests: scanner patterns, read/search modes, and the CLI."""

import json
from pathlib import Path

from typer.testing import CliRunner

from devx.cli.app import app
from devx.harness.permissions import PermissionPolicy
from devx.harness.tools import WorkspaceTools
from devx.secrets import redact_secrets, scan_secrets, scan_tree

runner = CliRunner()

PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDX...\n"
    "-----END PRIVATE KEY-----\n"
)

GITHUB_TOKEN = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"
OPENAI_KEY = "sk-proj-" + "0123456789abcdefghijklmnopqrstuvwxyz0123456789abcdef0123"
# Test fixtures are assembled at runtime so the committed source never contains
# a contiguous literal in real credential format (keeps GitHub push protection
# from mistaking intentional test data for a leaked key).
STRIPE_LIVE_KEY = "sk_" + "live_" + "0123456789abcdefghijklmnop"


def _tools(tmp_path: Path, mode: str = "warn") -> WorkspaceTools:
    return WorkspaceTools(PermissionPolicy(tmp_path), secret_scan_mode=mode)


# ── scanner ─────────────────────────────────────────────────────────────────


def test_scan_secrets_detects_private_key():
    hits = scan_secrets(PEM)
    assert any(hit.kind == "private-key" for hit in hits)


def test_scan_secrets_detects_common_tokens():
    text = (
        "aws AKIAIOSFODNN7EXAMPLE  "
        f"github {GITHUB_TOKEN}  openai {OPENAI_KEY}  "
        f"stripe {STRIPE_LIVE_KEY}"
    )
    kinds = {hit.kind for hit in scan_secrets(text)}
    assert "aws-access-key-id" in kinds
    assert "github-token" in kinds
    assert "openai-key" in kinds
    assert "stripe-live-key" in kinds


def test_scan_secrets_ignores_clean_code():
    clean = (
        "def helper(api_key='configured'):\n"
        "    value = config.get('password', 'none')\n"
        "    return value\n"
    )
    assert scan_secrets(clean) == []


def test_scan_secrets_reports_line_numbers():
    text = "line one\nsecret = 'AKIAIOSFODNN7EXAMPLE'\nline three\n"
    hits = scan_secrets(text)
    assert any(hit.line == 2 for hit in hits)


def test_redact_secrets_masks_content_and_preserves_surroundings():
    text = f"key: {GITHUB_TOKEN} rest"
    out = redact_secrets(text)
    assert GITHUB_TOKEN not in out
    assert "[REDACTED]" in out
    assert out.startswith("key: ") and out.endswith(" rest")


def test_scan_tree_skips_node_modules(tmp_path: Path):
    (tmp_path / "conf.env").write_text(f"OPENAI_API_KEY={OPENAI_KEY}\n")
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text(f"sk-{OPENAI_KEY}")
    hits = {(rel, hit.kind) for rel, hit in scan_tree(tmp_path)}
    assert ("conf.env", "openai-key") in hits
    assert not any(rel.startswith("node_modules") for rel, _ in hits)


# ── WorkspaceTools modes ────────────────────────────────────────────────────


def test_read_warn_keeps_content_and_warns(tmp_path: Path):
    (tmp_path / "a.py").write_text(f"x = 1\n# token: {GITHUB_TOKEN}\n")
    result = _tools(tmp_path, "warn").read("a.py")
    assert result.ok
    assert GITHUB_TOKEN in result.output
    assert result.warning and "Potential secret" in result.warning


def test_read_redact_masks_secret(tmp_path: Path):
    (tmp_path / "a.py").write_text(f"x = {GITHUB_TOKEN}\n")
    result = _tools(tmp_path, "redact").read("a.py")
    assert result.ok
    assert GITHUB_TOKEN not in result.output
    assert "[REDACTED]" in result.output
    assert result.warning is not None


def test_read_block_refuses(tmp_path: Path):
    (tmp_path / "a.py").write_text(f"x = {GITHUB_TOKEN}\n")
    result = _tools(tmp_path, "block").read("a.py")
    assert not result.ok
    assert "Blocked by secret scan" in result.output


def test_read_off_returns_plain(tmp_path: Path):
    (tmp_path / "a.py").write_text(f"x = {GITHUB_TOKEN}\n")
    result = _tools(tmp_path, "off").read("a.py")
    assert result.ok
    assert result.warning is None
    assert GITHUB_TOKEN in result.output


def test_search_redacts_matched_lines(tmp_path: Path):
    (tmp_path / "a.py").write_text(f"needle {GITHUB_TOKEN} more\n")
    result = _tools(tmp_path, "redact").search("needle", ".")
    assert result.ok
    assert "needle" in result.output
    assert GITHUB_TOKEN not in result.output
    assert result.warning is not None


def test_search_block_skips_matched_line(tmp_path: Path):
    (tmp_path / "a.py").write_text(f"needle {GITHUB_TOKEN} more\n")
    result = _tools(tmp_path, "block").search("needle", ".")
    assert "needle" not in result.output
    assert result.warning is not None


def test_search_ignores_clean_matches(tmp_path: Path):
    (tmp_path / "a.py").write_text("needle in haystack\n")
    result = _tools(tmp_path, "warn").search("needle", ".")
    assert result.ok
    assert "needle" in result.output
    assert result.warning is None


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_scan_secrets_finds_and_exits_1(tmp_path: Path):
    (tmp_path / "conf.env").write_text("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n")
    result = runner.invoke(app, ["scan-secrets", str(tmp_path)])
    assert result.exit_code == 1
    assert "conf.env" in result.output
    assert "aws-access-key-id" in result.output


def test_cli_scan_secrets_clean_exits_0(tmp_path: Path):
    (tmp_path / "ok.py").write_text("print('hi')\n")
    result = runner.invoke(app, ["scan-secrets", str(tmp_path)])
    assert result.exit_code == 0
    assert "No secrets detected" in result.output


def test_cli_scan_secrets_single_file(tmp_path: Path):
    target = tmp_path / "secret.txt"
    target.write_text(f"token={OPENAI_KEY}\n")
    result = runner.invoke(app, ["scan-secrets", str(target)])
    assert result.exit_code == 1
    assert "secret.txt" in result.output


def test_cli_scan_secrets_json(tmp_path: Path):
    (tmp_path / "conf.env").write_text("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n")
    result = runner.invoke(app, ["scan-secrets", str(tmp_path), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload and payload[0]["file"] == "conf.env"
    assert payload[0]["kind"] == "aws-access-key-id"


def test_cli_scan_secrets_missing_path():
    result = runner.invoke(app, ["scan-secrets", "no-such-path-xyz"])
    assert result.exit_code == 2