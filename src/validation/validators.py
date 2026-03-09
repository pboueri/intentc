"""Built-in validators for intentc validation pipeline."""

from __future__ import annotations

import glob
import os
import subprocess
from typing import Protocol

from core.types import Validation, ValidationResult, ValidationType


class Validator(Protocol):
    """Protocol that all validator implementations must satisfy."""

    def validate(self, v: Validation, output_dir: str) -> ValidationResult: ...
    def validator_type(self) -> ValidationType: ...


class JudgeAgent(Protocol):
    """Minimal protocol for an agent that can act as an LLM judge.

    Defined here to avoid circular imports with the agent package.
    """

    def validate_with_llm(
        self, validation: Validation, generated_files: list[str]
    ) -> tuple[bool, str]: ...


class FileCheckValidator:
    """Validates that expected files exist and optionally contain required strings."""

    def validator_type(self) -> ValidationType:
        return ValidationType.FILE_CHECK

    def validate(self, v: Validation, output_dir: str) -> ValidationResult:
        path = v.parameters.get("path", "")
        if not path:
            return ValidationResult(
                validation_name=v.name,
                passed=False,
                message="parameter 'path' is required",
                severity="error",
            )

        resolved = os.path.join(output_dir, path)

        if not os.path.isfile(resolved):
            return ValidationResult(
                validation_name=v.name,
                passed=False,
                message=f"file not found: {path}",
                severity="error",
            )

        contains: list[str] = v.parameters.get("contains", [])
        if contains:
            try:
                with open(resolved, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as exc:
                return ValidationResult(
                    validation_name=v.name,
                    passed=False,
                    message=f"failed to read file {path}: {exc}",
                    severity="error",
                )

            missing: list[str] = []
            for s in contains:
                if s not in content:
                    missing.append(s)

            if missing:
                details = [f"file {path} does not contain: {s}" for s in missing]
                return ValidationResult(
                    validation_name=v.name,
                    passed=False,
                    message=details[0],
                    details=details,
                    severity="error",
                )

        return ValidationResult(
            validation_name=v.name,
            passed=True,
            message=f"file exists: {path}",
            severity="error",
        )


class FolderCheckValidator:
    """Validates that expected directories exist and optionally contain required children."""

    def validator_type(self) -> ValidationType:
        return ValidationType.FOLDER_CHECK

    def validate(self, v: Validation, output_dir: str) -> ValidationResult:
        path = v.parameters.get("path", "")
        if not path:
            return ValidationResult(
                validation_name=v.name,
                passed=False,
                message="parameter 'path' is required",
                severity="error",
            )

        resolved = os.path.join(output_dir, path)

        if not os.path.isdir(resolved):
            return ValidationResult(
                validation_name=v.name,
                passed=False,
                message=f"directory not found: {path}",
                severity="error",
            )

        children: list[str] = v.parameters.get("children", [])
        if children:
            try:
                actual = set(os.listdir(resolved))
            except Exception as exc:
                return ValidationResult(
                    validation_name=v.name,
                    passed=False,
                    message=f"failed to list directory {path}: {exc}",
                    severity="error",
                )

            missing = [c for c in children if c not in actual]
            if missing:
                details = [f"directory {path} missing child: {c}" for c in missing]
                return ValidationResult(
                    validation_name=v.name,
                    passed=False,
                    message=details[0],
                    details=details,
                    severity="error",
                )

        return ValidationResult(
            validation_name=v.name,
            passed=True,
            message=f"directory exists: {path}",
            severity="error",
        )


class CommandCheckValidator:
    """Validates by executing a shell command and checking its output."""

    DEFAULT_TIMEOUT: float = 60.0

    def validator_type(self) -> ValidationType:
        return ValidationType.COMMAND_CHECK

    def validate(self, v: Validation, output_dir: str) -> ValidationResult:
        command = v.parameters.get("command", "")
        if not command:
            return ValidationResult(
                validation_name=v.name,
                passed=False,
                message="parameter 'command' is required",
                severity="error",
            )

        working_dir = v.parameters.get("working_dir", output_dir)
        expected_exit_code = v.parameters.get("exit_code", 0)
        stdout_contains: list[str] | None = v.parameters.get("stdout_contains")
        stderr_contains: list[str] | None = v.parameters.get("stderr_contains")
        timeout = float(v.parameters.get("timeout", self.DEFAULT_TIMEOUT))

        try:
            result = subprocess.run(
                ["sh", "-c", command],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ValidationResult(
                validation_name=v.name,
                passed=False,
                message=f"command timed out after {timeout}s: {command}",
                severity="error",
            )
        except Exception as exc:
            return ValidationResult(
                validation_name=v.name,
                passed=False,
                message=f"command execution failed: {exc}",
                severity="error",
            )

        details: list[str] = []

        if result.returncode != expected_exit_code:
            details.append(
                f"expected exit code {expected_exit_code}, got {result.returncode}"
            )

        if stdout_contains:
            for s in stdout_contains:
                if s not in result.stdout:
                    details.append(f"stdout does not contain: {s}")

        if stderr_contains:
            for s in stderr_contains:
                if s not in result.stderr:
                    details.append(f"stderr does not contain: {s}")

        if details:
            return ValidationResult(
                validation_name=v.name,
                passed=False,
                message=details[0],
                details=details,
                severity="error",
            )

        return ValidationResult(
            validation_name=v.name,
            passed=True,
            message=f"command passed: {command}",
            severity="error",
        )


class LLMJudgeValidator:
    """Validates generated code using an LLM agent as a judge."""

    def __init__(self, agent: JudgeAgent) -> None:
        self._agent = agent

    def validator_type(self) -> ValidationType:
        return ValidationType.LLM_JUDGE

    def validate(self, v: Validation, output_dir: str) -> ValidationResult:
        rubric = v.parameters.get("rubric", "")
        if not rubric:
            return ValidationResult(
                validation_name=v.name,
                passed=False,
                message="parameter 'rubric' is required",
                severity="error",
            )

        severity = v.parameters.get("severity", "error")
        context_files_globs: list[str] = v.parameters.get("context_files", [])

        # Resolve context_files globs relative to output_dir
        generated_files: list[str] = []
        if context_files_globs:
            for pattern in context_files_globs:
                full_pattern = os.path.join(output_dir, pattern)
                matched = glob.glob(full_pattern, recursive=True)
                generated_files.extend(matched)
        else:
            # If no context_files specified, collect all files in output_dir
            for root, _dirs, files in os.walk(output_dir):
                for fname in files:
                    generated_files.append(os.path.join(root, fname))

        generated_files.sort()

        try:
            passed, explanation = self._agent.validate_with_llm(v, generated_files)
        except Exception as exc:
            return ValidationResult(
                validation_name=v.name,
                passed=False,
                message=f"LLM judge error: {exc}",
                severity="error",
            )

        # If severity is "warning" and the judge failed, promote to passed with warning
        if severity == "warning" and not passed:
            return ValidationResult(
                validation_name=v.name,
                passed=True,
                message=f"[warning] {explanation}",
                details=[explanation],
                severity="warning",
            )

        return ValidationResult(
            validation_name=v.name,
            passed=passed,
            message=explanation,
            severity=severity,
        )
