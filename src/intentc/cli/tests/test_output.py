"""Tests for intentc.cli.output rendering helpers."""

from __future__ import annotations

from rich.console import Console

from intentc.build.agents import DifferencingResponse, DimensionResult, RefineBakeResponse, ValidationResponse
from intentc.build.storage import BuildResult, BuildStep, RefinementSession, TargetStatus
from intentc.build.validations import ValidationSuiteResult
from intentc.cli import output as out
from intentc.core.project import ProjectIssue


def _capturing_console() -> Console:
    return Console(record=True, width=200)


class TestBuildRendering:
    def test_render_build_plan_is_numbered_list_not_table(self) -> None:
        console = _capturing_console()
        results = [
            BuildResult(target="models", status=TargetStatus.PENDING),
            BuildResult(target="api", status=TargetStatus.PENDING),
        ]

        out.render_build_plan(results, console=console)
        text = console.export_text()

        assert "Build plan (dry run) — 2 target(s):" in text
        assert "1. models" in text
        assert "2. api" in text
        assert "Build Results" not in text

    def test_render_build_results_summary_line(self) -> None:
        console = _capturing_console()
        results = [
            BuildResult(target="models", status=TargetStatus.BUILT, total_duration_secs=1.5, attempts=1),
            BuildResult(target="api", status=TargetStatus.FAILED, total_duration_secs=2.5, attempts=2),
        ]

        out.render_build_results(results, console=console)
        text = console.export_text()

        assert "1 built, 1 failed in 4.0s" in text


class TestValidationRendering:
    def test_warning_and_error_labelled_distinctly(self) -> None:
        console = _capturing_console()
        result = ValidationSuiteResult(
            target="api",
            results=[
                ValidationResponse(name="rubric-check", status="fail", severity="warning", reason="a bit thin"),
                ValidationResponse(name="cmd-check", status="fail", severity="error", reason="exit 1"),
            ],
            passed=False,
            summary="0/2 passed, 1 error(s), 1 warning(s)",
            passed_count=0,
            error_count=1,
            warning_count=1,
        )

        out.render_validation_results(result, console=console)
        text = console.export_text()

        assert "warning" in text
        assert "error" in text
        assert "0/2 passed, 1 error(s), 1 warning(s)" in text


class TestCheckRendering:
    def test_summary_line(self) -> None:
        console = _capturing_console()
        issues = [
            ProjectIssue(level="error", path=None, feature="api", message="bad thing"),
            ProjectIssue(level="warning", path=None, feature="api", message="minor thing"),
        ]

        out.render_check_results(issues, total_features=3, console=console)
        text = console.export_text()

        assert "1 error(s), 1 warning(s) across 3 feature(s)" in text
        assert "bad thing" in text
        assert "minor thing" in text


class TestCompareRendering:
    def test_renders_dimensions_and_summary(self) -> None:
        console = _capturing_console()
        response = DifferencingResponse(
            status="divergent",
            dimensions=[DimensionResult(name="public_api", status="fail", rationale="signature changed")],
            summary="API surface diverged",
        )

        out.render_compare_results(response, console=console)
        text = console.export_text()

        assert "public_api" in text
        assert "API surface diverged" in text


class TestBuildLogRendering:
    def test_shows_latest_steps_and_validation_results(self) -> None:
        console = _capturing_console()
        history = [
            BuildResult(
                target="api",
                generation_id="abcdef1234",
                status=TargetStatus.BUILT,
                steps=[BuildStep(phase="build", status="success", duration_secs=1.0, summary="ok")],
                commit_id="deadbeef",
                total_duration_secs=1.0,
                timestamp="2026-01-01T00:00:00",
                attempts=1,
            )
        ]

        out.render_build_log("api", history, [{"name": "check", "status": "pass", "reason": ""}], console=console)
        text = console.export_text()

        assert "Build history: api" in text
        assert "Steps for generation abcdef12" in text
        assert "check" in text


class TestRefineSummaryRendering:
    def test_generalization_with_bracket_markup_is_printed_verbatim(self) -> None:
        console = _capturing_console()
        session = RefinementSession(
            session_id="sess-escape-test",
            target="models",
            output_dir="src",
            status="baked",
            base_commit="deadbeef",
            started_at="2026-01-01T00:00:00",
        )
        response = RefineBakeResponse(
            status="success",
            summary="Checklist state: [x] done, [ ] pending",
            generalizations=["Every item renders a [x] or [ ] checkbox"],
            open_questions=["Should [ ] items be hidden?"],
        )

        out.render_refine_summary(session, response, console=console)
        text = console.export_text()

        assert "[x] done, [ ] pending" in text
        assert "Every item renders a [x] or [ ] checkbox" in text
        assert "Should [ ] items be hidden?" in text

    def test_render_journal_escapes_markup(self) -> None:
        console = _capturing_console()
        out.render_journal("## 1. Ask\n**Rule:** show [x] when done, [ ] otherwise\n", console=console)
        text = console.export_text()

        assert "[x] when done, [ ] otherwise" in text


class TestTimestampedLog:
    def test_bracket_markup_in_message_is_printed_verbatim(self) -> None:
        console = _capturing_console()
        log = out.timestamped_log(console)

        log("  ✗ divergent: runtime_behavior — Checklist [x] done, [ ] pending")
        text = console.export_text()

        assert "divergent: runtime_behavior" in text
        assert "[x] done, [ ] pending" in text

    def test_dim_timestamp_wrapper_still_renders_as_markup(self) -> None:
        console = _capturing_console()
        log = out.timestamped_log(console)

        log("hello")
        text = console.export_text()

        assert "[dim]" not in text
        assert "hello" in text
