"""Automated unit and integration test synthesis engine."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from devx.analysis.ast_scanner import SymbolInfo, scan_module
from devx.config import Settings
from devx.llm import create_fallback_llm, create_llm, invoke_with_retry


@dataclass
class TestCaseSpec:
    target_symbol: str
    target_file: str
    code: str
    test_file_path: Path


class TestGenerator:
    """Synthesizes comprehensive unit test suites using AST reflection and LLM code generation."""

    def __init__(self, settings: Settings, workspace: Path | None = None):
        self.settings = settings
        self.workspace = workspace or settings.workspace

    def generate_tests_for_symbol(
        self,
        file_path: Path | str,
        symbol_name: str,
    ) -> TestCaseSpec:
        """Generate comprehensive pytest unit tests for a specific function or class."""
        abs_path = (self.workspace / file_path).resolve() if not Path(file_path).is_absolute() else Path(file_path)
        mod_info = scan_module(abs_path, workspace=self.workspace)

        target_sym: SymbolInfo | None = None
        for sym in mod_info.symbols:
            if sym.name == symbol_name or sym.name.endswith(f".{symbol_name}"):
                target_sym = sym
                break

        if target_sym is None:
            raise ValueError(f"Symbol '{symbol_name}' not found in {file_path}")

        file_content = abs_path.read_text(encoding="utf-8", errors="replace")

        # Determine target test file path
        test_dir = self.workspace / "tests"
        test_dir.mkdir(parents=True, exist_ok=True)
        stem = abs_path.stem
        test_file = test_dir / f"test_{stem}_generated.py"

        prompt = (
            f"You are an expert Python test engineer writing comprehensive pytest unit tests for `{target_sym.name}`.\n\n"
            f"File: {abs_path.name}\n"
            f"Symbol details:\n"
            f"- Name: {target_sym.name}\n"
            f"- Kind: {target_sym.kind}\n"
            f"- Parameters: {target_sym.params}\n"
            f"- Return Type: {target_sym.return_type}\n"
            f"- Exceptions Raised: {target_sym.exceptions_raised}\n"
            f"- Docstring: {target_sym.docstring}\n\n"
            f"Source file content:\n```python\n{file_content[:6000]}\n```\n\n"
            "Requirements:\n"
            "1. Write clean, idiomatic pytest tests.\n"
            "2. Cover the happy path.\n"
            "3. Cover edge cases (e.g., None, empty containers, boundary values).\n"
            "4. Cover negative/error cases (use `pytest.raises(...)` for exceptions).\n"
            "5. Use `unittest.mock` (`patch`, `MagicMock`) where appropriate for external I/O.\n"
            "6. Output ONLY executable Python code inside a ```python ``` block.\n"
        )

        llm = create_llm(self.settings)
        fb_llm = create_fallback_llm(self.settings)
        response = invoke_with_retry(
            llm,
            prompt,
            provider=self.settings.provider,
            fallback_model=fb_llm,
            fallback_provider=getattr(self.settings, "fallback_provider", ""),
        )
        raw_text = str(getattr(response, "content", response))

        # Extract python code
        code_match = re.search(r"```python\s*(.*?)\s*```", raw_text, re.DOTALL)
        code = code_match.group(1) if code_match else raw_text

        return TestCaseSpec(
            target_symbol=target_sym.name,
            target_file=str(file_path),
            code=code,
            test_file_path=test_file,
        )

    def write_tests(self, spec: TestCaseSpec, overwrite: bool = False) -> Path:
        """Write generated test case to the test file."""
        if spec.test_file_path.exists() and not overwrite:
            # Append tests if file already exists
            existing = spec.test_file_path.read_text(encoding="utf-8")
            content = existing + "\n\n" + spec.code
        else:
            content = spec.code

        spec.test_file_path.write_text(content, encoding="utf-8")
        return spec.test_file_path
