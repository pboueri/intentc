"""Validation runner for intentc -- executes validations against generated code."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core.types import Target, ValidationResult, ValidationType

from validation.registry import Registry


# ---------------------------------------------------------------------------
# Configuration models
# ---------------------------------------------------------------------------


class RunOptions(BaseModel):
    """Options controlling how validations are executed."""

    parallel: bool = False
    timeout: float = 60.0  # seconds

    model_config = {"extra": "ignore"}


class RunReport(BaseModel):
    """Aggregated report for all validations run against a single target."""

    target: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    results: dict[str, ValidationResult] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Ordering helpers
# ---------------------------------------------------------------------------

_DETERMINISTIC_TYPES: set[ValidationType] = {
    ValidationType.FILE_CHECK,
    ValidationType.FOLDER_CHECK,
    ValidationType.COMMAND_CHECK,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class Runner:
    """Executes validations for a Target and collects results into a RunReport."""

    def __init__(
        self,
        registry: Registry,
        default_agent: Any = None,
        config: Any = None,
    ) -> None:
        self.registry = registry
        self.default_agent = default_agent
        self.config = config

    def run_target_validations(
        self,
        target: Target,
        output_dir: str,
        opts: RunOptions | None = None,
    ) -> RunReport:
        """Run every validation attached to *target* and return a RunReport."""
        if opts is None:
            opts = RunOptions()

        report = RunReport(target=target.name)

        # Collect (validation, validation_file) pairs from all ValidationFiles
        from core.types import Validation, ValidationFile

        entries: list[tuple[Validation, ValidationFile]] = []
        for vf in target.validations:
            for v in vf.validations:
                entries.append((v, vf))

        # Sort: deterministic validators first, then llm_judge
        def _sort_key(pair: tuple[Validation, ValidationFile]) -> int:
            return 0 if pair[0].type in _DETERMINISTIC_TYPES else 1

        entries.sort(key=_sort_key)

        for validation, vfile in entries:
            vtype = validation.type
            report.total += 1

            try:
                # For LLM judge validations with a judge_profile, create a
                # dedicated agent.  We import create_from_profile lazily so
                # the validation package does not hard-depend on the agent
                # package at import time.
                if vtype == ValidationType.LLM_JUDGE and vfile.judge_profile:
                    try:
                        from validation.validators import LLMJudgeValidator

                        # Try to resolve the profile and create an agent
                        if self.config is not None and hasattr(self.config, "agent_profiles"):
                            profile = self.config.agent_profiles.get(vfile.judge_profile)
                            if profile is not None and hasattr(self.default_agent, "create_from_profile"):
                                agent = self.default_agent.create_from_profile(profile)
                                judge = LLMJudgeValidator(agent)
                                result = judge.validate(validation, output_dir)
                            else:
                                # Fall back to the default registry lookup
                                validator = self.registry.get(vtype)
                                result = validator.validate(validation, output_dir)
                        else:
                            validator = self.registry.get(vtype)
                            result = validator.validate(validation, output_dir)
                    except Exception:
                        # Fall back to registry
                        validator = self.registry.get(vtype)
                        result = validator.validate(validation, output_dir)
                else:
                    validator = self.registry.get(vtype)
                    result = validator.validate(validation, output_dir)

            except KeyError as exc:
                report.errors[validation.name] = str(exc)
                report.failed += 1
                continue
            except Exception as exc:
                report.errors[validation.name] = f"unexpected error: {exc}"
                report.failed += 1
                continue

            report.results[validation.name] = result
            if result.passed:
                report.passed += 1
                if result.severity == "warning":
                    report.warnings += 1
            else:
                report.failed += 1

        return report

    @staticmethod
    def generate_report(report: RunReport) -> str:
        """Generate a human-readable text report from a RunReport."""
        lines: list[str] = []
        lines.append(f"Validation Report: {report.target}")
        lines.append("=" * 60)
        lines.append(
            f"Total: {report.total}  Passed: {report.passed}  "
            f"Failed: {report.failed}  Warnings: {report.warnings}"
        )
        lines.append("-" * 60)

        for name, result in report.results.items():
            indicator = "[PASS]" if result.passed else "[FAIL]"
            if result.passed and result.severity == "warning":
                indicator = "[WARN]"
            lines.append(f"  {indicator} {name}: {result.message}")
            for detail in result.details:
                lines.append(f"         {detail}")

        for name, error in report.errors.items():
            lines.append(f"  [ERR]  {name}: {error}")

        lines.append("=" * 60)
        status = "PASSED" if report.failed == 0 else "FAILED"
        lines.append(f"Result: {status}")
        return "\n".join(lines)
