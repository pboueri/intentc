"""Tests for the validation package."""

from __future__ import annotations

import os
import tempfile

import pytest

from core.types import (
    Intent,
    Target,
    Validation,
    ValidationFile,
    ValidationResult,
    ValidationType,
)
from validation.registry import Registry
from validation.runner import RunOptions, RunReport, Runner
from validation.validators import (
    CommandCheckValidator,
    FileCheckValidator,
    FolderCheckValidator,
    LLMJudgeValidator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_validation(
    name: str,
    vtype: ValidationType,
    **params: object,
) -> Validation:
    return Validation(name=name, type=vtype, parameters=dict(params))


class MockJudgeAgent:
    """Mock agent for LLM judge tests."""

    def __init__(self, passed: bool = True, explanation: str = "looks good") -> None:
        self._passed = passed
        self._explanation = explanation
        self.last_validation: Validation | None = None
        self.last_files: list[str] | None = None

    def validate_with_llm(
        self, validation: Validation, generated_files: list[str]
    ) -> tuple[bool, str]:
        self.last_validation = validation
        self.last_files = generated_files
        return self._passed, self._explanation


# ===========================================================================
# FileCheckValidator
# ===========================================================================


class TestFileCheckValidator:
    def test_file_exists(self, tmp_path: object) -> None:
        d = str(tmp_path)
        open(os.path.join(d, "hello.py"), "w").close()

        v = _make_validation("fc", ValidationType.FILE_CHECK, path="hello.py")
        result = FileCheckValidator().validate(v, d)

        assert result.passed is True
        assert "hello.py" in result.message

    def test_file_missing(self, tmp_path: object) -> None:
        d = str(tmp_path)

        v = _make_validation("fc", ValidationType.FILE_CHECK, path="missing.py")
        result = FileCheckValidator().validate(v, d)

        assert result.passed is False
        assert "file not found: missing.py" in result.message

    def test_file_exists_but_missing_content(self, tmp_path: object) -> None:
        d = str(tmp_path)
        with open(os.path.join(d, "app.py"), "w") as f:
            f.write("import os\nprint('hello')\n")

        v = _make_validation(
            "fc",
            ValidationType.FILE_CHECK,
            path="app.py",
            contains=["import os", "class Widget"],
        )
        result = FileCheckValidator().validate(v, d)

        assert result.passed is False
        assert "class Widget" in result.message

    def test_file_exists_with_all_content(self, tmp_path: object) -> None:
        d = str(tmp_path)
        with open(os.path.join(d, "app.py"), "w") as f:
            f.write("import os\nclass Widget:\n    pass\n")

        v = _make_validation(
            "fc",
            ValidationType.FILE_CHECK,
            path="app.py",
            contains=["import os", "class Widget"],
        )
        result = FileCheckValidator().validate(v, d)

        assert result.passed is True

    def test_missing_path_parameter(self, tmp_path: object) -> None:
        d = str(tmp_path)
        v = _make_validation("fc", ValidationType.FILE_CHECK)
        result = FileCheckValidator().validate(v, d)

        assert result.passed is False
        assert "path" in result.message


# ===========================================================================
# FolderCheckValidator
# ===========================================================================


class TestFolderCheckValidator:
    def test_folder_exists(self, tmp_path: object) -> None:
        d = str(tmp_path)
        os.makedirs(os.path.join(d, "src"))

        v = _make_validation("dc", ValidationType.FOLDER_CHECK, path="src")
        result = FolderCheckValidator().validate(v, d)

        assert result.passed is True
        assert "src" in result.message

    def test_folder_missing(self, tmp_path: object) -> None:
        d = str(tmp_path)

        v = _make_validation("dc", ValidationType.FOLDER_CHECK, path="lib")
        result = FolderCheckValidator().validate(v, d)

        assert result.passed is False
        assert "directory not found: lib" in result.message

    def test_folder_missing_children(self, tmp_path: object) -> None:
        d = str(tmp_path)
        src = os.path.join(d, "src")
        os.makedirs(src)
        open(os.path.join(src, "main.py"), "w").close()

        v = _make_validation(
            "dc",
            ValidationType.FOLDER_CHECK,
            path="src",
            children=["main.py", "utils.py"],
        )
        result = FolderCheckValidator().validate(v, d)

        assert result.passed is False
        assert "utils.py" in result.message

    def test_folder_with_all_children(self, tmp_path: object) -> None:
        d = str(tmp_path)
        src = os.path.join(d, "src")
        os.makedirs(src)
        open(os.path.join(src, "main.py"), "w").close()
        open(os.path.join(src, "utils.py"), "w").close()

        v = _make_validation(
            "dc",
            ValidationType.FOLDER_CHECK,
            path="src",
            children=["main.py", "utils.py"],
        )
        result = FolderCheckValidator().validate(v, d)

        assert result.passed is True

    def test_missing_path_parameter(self, tmp_path: object) -> None:
        d = str(tmp_path)
        v = _make_validation("dc", ValidationType.FOLDER_CHECK)
        result = FolderCheckValidator().validate(v, d)

        assert result.passed is False
        assert "path" in result.message


# ===========================================================================
# CommandCheckValidator
# ===========================================================================


class TestCommandCheckValidator:
    def test_command_passes(self, tmp_path: object) -> None:
        d = str(tmp_path)

        v = _make_validation("cc", ValidationType.COMMAND_CHECK, command="echo hello")
        result = CommandCheckValidator().validate(v, d)

        assert result.passed is True

    def test_command_fails(self, tmp_path: object) -> None:
        d = str(tmp_path)

        v = _make_validation("cc", ValidationType.COMMAND_CHECK, command="false")
        result = CommandCheckValidator().validate(v, d)

        assert result.passed is False
        assert "exit code" in result.message

    def test_command_stdout_check(self, tmp_path: object) -> None:
        d = str(tmp_path)

        v = _make_validation(
            "cc",
            ValidationType.COMMAND_CHECK,
            command="echo hello world",
            stdout_contains=["hello", "world"],
        )
        result = CommandCheckValidator().validate(v, d)

        assert result.passed is True

    def test_command_stdout_missing(self, tmp_path: object) -> None:
        d = str(tmp_path)

        v = _make_validation(
            "cc",
            ValidationType.COMMAND_CHECK,
            command="echo hello",
            stdout_contains=["goodbye"],
        )
        result = CommandCheckValidator().validate(v, d)

        assert result.passed is False
        assert "stdout does not contain" in result.message

    def test_command_stderr_check(self, tmp_path: object) -> None:
        d = str(tmp_path)

        v = _make_validation(
            "cc",
            ValidationType.COMMAND_CHECK,
            command="echo warning >&2",
            stderr_contains=["warning"],
        )
        result = CommandCheckValidator().validate(v, d)

        assert result.passed is True

    def test_command_custom_exit_code(self, tmp_path: object) -> None:
        d = str(tmp_path)

        v = _make_validation(
            "cc",
            ValidationType.COMMAND_CHECK,
            command="exit 42",
            exit_code=42,
        )
        result = CommandCheckValidator().validate(v, d)

        assert result.passed is True

    def test_command_working_dir(self, tmp_path: object) -> None:
        d = str(tmp_path)
        subdir = os.path.join(d, "sub")
        os.makedirs(subdir)
        with open(os.path.join(subdir, "marker.txt"), "w") as f:
            f.write("found")

        v = _make_validation(
            "cc",
            ValidationType.COMMAND_CHECK,
            command="cat marker.txt",
            working_dir=subdir,
            stdout_contains=["found"],
        )
        result = CommandCheckValidator().validate(v, d)

        assert result.passed is True

    def test_missing_command_parameter(self, tmp_path: object) -> None:
        d = str(tmp_path)
        v = _make_validation("cc", ValidationType.COMMAND_CHECK)
        result = CommandCheckValidator().validate(v, d)

        assert result.passed is False
        assert "command" in result.message

    def test_command_timeout(self, tmp_path: object) -> None:
        d = str(tmp_path)

        v = _make_validation(
            "cc",
            ValidationType.COMMAND_CHECK,
            command="sleep 10",
            timeout=0.1,
        )
        result = CommandCheckValidator().validate(v, d)

        assert result.passed is False
        assert "timed out" in result.message


# ===========================================================================
# LLMJudgeValidator
# ===========================================================================


class TestLLMJudgeValidator:
    def test_judge_passes(self, tmp_path: object) -> None:
        d = str(tmp_path)
        with open(os.path.join(d, "code.py"), "w") as f:
            f.write("print('hello')\n")

        agent = MockJudgeAgent(passed=True, explanation="code looks great")
        judge = LLMJudgeValidator(agent)

        v = _make_validation(
            "judge",
            ValidationType.LLM_JUDGE,
            rubric="Code must be clean and readable",
        )
        result = judge.validate(v, d)

        assert result.passed is True
        assert result.message == "code looks great"
        assert agent.last_validation is v
        assert agent.last_files is not None
        assert len(agent.last_files) == 1

    def test_judge_fails(self, tmp_path: object) -> None:
        d = str(tmp_path)
        with open(os.path.join(d, "code.py"), "w") as f:
            f.write("x=1\n")

        agent = MockJudgeAgent(passed=False, explanation="code is messy")
        judge = LLMJudgeValidator(agent)

        v = _make_validation(
            "judge",
            ValidationType.LLM_JUDGE,
            rubric="Code must be clean",
        )
        result = judge.validate(v, d)

        assert result.passed is False
        assert result.message == "code is messy"

    def test_judge_warning_severity(self, tmp_path: object) -> None:
        d = str(tmp_path)
        with open(os.path.join(d, "code.py"), "w") as f:
            f.write("x=1\n")

        agent = MockJudgeAgent(passed=False, explanation="minor style issue")
        judge = LLMJudgeValidator(agent)

        v = _make_validation(
            "judge",
            ValidationType.LLM_JUDGE,
            rubric="Code style check",
            severity="warning",
        )
        result = judge.validate(v, d)

        # warning severity promotes failure to passed with a warning message
        assert result.passed is True
        assert result.severity == "warning"
        assert "minor style issue" in result.message

    def test_judge_context_files_glob(self, tmp_path: object) -> None:
        d = str(tmp_path)
        os.makedirs(os.path.join(d, "src"))
        with open(os.path.join(d, "src", "a.py"), "w") as f:
            f.write("# a")
        with open(os.path.join(d, "src", "b.py"), "w") as f:
            f.write("# b")
        with open(os.path.join(d, "readme.md"), "w") as f:
            f.write("# readme")

        agent = MockJudgeAgent(passed=True, explanation="ok")
        judge = LLMJudgeValidator(agent)

        v = _make_validation(
            "judge",
            ValidationType.LLM_JUDGE,
            rubric="Check python files",
            context_files=["src/*.py"],
        )
        result = judge.validate(v, d)

        assert result.passed is True
        # Only .py files from src/ should have been passed to the agent
        assert agent.last_files is not None
        assert len(agent.last_files) == 2
        assert all(f.endswith(".py") for f in agent.last_files)

    def test_judge_missing_rubric(self, tmp_path: object) -> None:
        d = str(tmp_path)
        agent = MockJudgeAgent()
        judge = LLMJudgeValidator(agent)

        v = _make_validation("judge", ValidationType.LLM_JUDGE)
        result = judge.validate(v, d)

        assert result.passed is False
        assert "rubric" in result.message

    def test_judge_agent_raises(self, tmp_path: object) -> None:
        d = str(tmp_path)

        class FailingAgent:
            def validate_with_llm(self, validation, generated_files):
                raise RuntimeError("API down")

        judge = LLMJudgeValidator(FailingAgent())

        v = _make_validation(
            "judge",
            ValidationType.LLM_JUDGE,
            rubric="check it",
        )
        result = judge.validate(v, d)

        assert result.passed is False
        assert "LLM judge error" in result.message


# ===========================================================================
# Registry
# ===========================================================================


class TestRegistry:
    def test_builtin_validators_registered(self) -> None:
        reg = Registry()
        assert reg.get(ValidationType.FILE_CHECK) is not None
        assert reg.get(ValidationType.FOLDER_CHECK) is not None
        assert reg.get(ValidationType.COMMAND_CHECK) is not None

    def test_llm_judge_not_registered_by_default(self) -> None:
        reg = Registry()
        with pytest.raises(KeyError, match="llm_judge"):
            reg.get(ValidationType.LLM_JUDGE)

    def test_register_llm_judge(self) -> None:
        reg = Registry()
        agent = MockJudgeAgent()
        reg.register_llm_judge(agent)
        validator = reg.get(ValidationType.LLM_JUDGE)
        assert validator is not None

    def test_get_unknown_type_raises(self) -> None:
        reg = Registry()
        with pytest.raises(KeyError):
            reg.get(ValidationType.LLM_JUDGE)

    def test_registered_types(self) -> None:
        reg = Registry()
        types = reg.registered_types
        assert ValidationType.FILE_CHECK in types
        assert ValidationType.FOLDER_CHECK in types
        assert ValidationType.COMMAND_CHECK in types
        assert len(types) == 3

    def test_registered_types_after_llm_judge(self) -> None:
        reg = Registry()
        reg.register_llm_judge(MockJudgeAgent())
        types = reg.registered_types
        assert len(types) == 4
        assert ValidationType.LLM_JUDGE in types


# ===========================================================================
# Runner
# ===========================================================================


class TestRunner:
    @staticmethod
    def _make_target(
        name: str,
        validations: list[Validation],
        judge_profile: str = "",
    ) -> Target:
        vf = ValidationFile(
            target=name,
            judge_profile=judge_profile,
            validations=validations,
        )
        return Target(
            name=name,
            intent=Intent(name=name),
            validations=[vf],
        )

    def test_all_pass(self, tmp_path: object) -> None:
        d = str(tmp_path)
        open(os.path.join(d, "main.py"), "w").close()
        os.makedirs(os.path.join(d, "src"))

        target = self._make_target(
            "myapp",
            [
                _make_validation("f1", ValidationType.FILE_CHECK, path="main.py"),
                _make_validation("d1", ValidationType.FOLDER_CHECK, path="src"),
                _make_validation("c1", ValidationType.COMMAND_CHECK, command="true"),
            ],
        )

        reg = Registry()
        runner = Runner(reg)
        report = runner.run_target_validations(target, d)

        assert report.total == 3
        assert report.passed == 3
        assert report.failed == 0

    def test_mixed_pass_fail(self, tmp_path: object) -> None:
        d = str(tmp_path)
        open(os.path.join(d, "main.py"), "w").close()

        target = self._make_target(
            "myapp",
            [
                _make_validation("f1", ValidationType.FILE_CHECK, path="main.py"),
                _make_validation("f2", ValidationType.FILE_CHECK, path="missing.py"),
            ],
        )

        reg = Registry()
        runner = Runner(reg)
        report = runner.run_target_validations(target, d)

        assert report.total == 2
        assert report.passed == 1
        assert report.failed == 1

    def test_unregistered_type_counted_as_error(self, tmp_path: object) -> None:
        d = str(tmp_path)

        target = self._make_target(
            "myapp",
            [
                _make_validation(
                    "judge1",
                    ValidationType.LLM_JUDGE,
                    rubric="check it",
                ),
            ],
        )

        reg = Registry()  # LLM judge not registered
        runner = Runner(reg)
        report = runner.run_target_validations(target, d)

        assert report.total == 1
        assert report.failed == 1
        assert "judge1" in report.errors

    def test_runner_sorts_deterministic_before_llm(self, tmp_path: object) -> None:
        d = str(tmp_path)
        open(os.path.join(d, "main.py"), "w").close()

        agent = MockJudgeAgent(passed=True, explanation="ok")
        reg = Registry()
        reg.register_llm_judge(agent)

        # Define validations in reverse order: llm first, then file check
        target = self._make_target(
            "myapp",
            [
                _make_validation(
                    "judge1",
                    ValidationType.LLM_JUDGE,
                    rubric="check code",
                ),
                _make_validation("f1", ValidationType.FILE_CHECK, path="main.py"),
            ],
        )

        runner = Runner(reg)
        report = runner.run_target_validations(target, d)

        # Both should pass
        assert report.total == 2
        assert report.passed == 2

        # The file check result should come before the LLM judge result
        result_names = list(report.results.keys())
        assert result_names.index("f1") < result_names.index("judge1")

    def test_runner_with_llm_judge(self, tmp_path: object) -> None:
        d = str(tmp_path)
        with open(os.path.join(d, "code.py"), "w") as f:
            f.write("print('hi')\n")

        agent = MockJudgeAgent(passed=True, explanation="great code")
        reg = Registry()
        reg.register_llm_judge(agent)

        target = self._make_target(
            "myapp",
            [
                _make_validation(
                    "judge1",
                    ValidationType.LLM_JUDGE,
                    rubric="Code must be readable",
                ),
            ],
        )

        runner = Runner(reg)
        report = runner.run_target_validations(target, d)

        assert report.total == 1
        assert report.passed == 1
        assert report.results["judge1"].message == "great code"

    def test_runner_warnings_counted(self, tmp_path: object) -> None:
        d = str(tmp_path)
        with open(os.path.join(d, "code.py"), "w") as f:
            f.write("x=1\n")

        agent = MockJudgeAgent(passed=False, explanation="minor style nit")
        reg = Registry()
        reg.register_llm_judge(agent)

        target = self._make_target(
            "myapp",
            [
                _make_validation(
                    "style",
                    ValidationType.LLM_JUDGE,
                    rubric="Style check",
                    severity="warning",
                ),
            ],
        )

        runner = Runner(reg)
        report = runner.run_target_validations(target, d)

        assert report.total == 1
        assert report.passed == 1
        assert report.warnings == 1
        assert report.failed == 0

    def test_multiple_validation_files(self, tmp_path: object) -> None:
        d = str(tmp_path)
        open(os.path.join(d, "a.py"), "w").close()
        open(os.path.join(d, "b.py"), "w").close()

        vf1 = ValidationFile(
            target="myapp",
            validations=[
                _make_validation("f1", ValidationType.FILE_CHECK, path="a.py"),
            ],
        )
        vf2 = ValidationFile(
            target="myapp",
            validations=[
                _make_validation("f2", ValidationType.FILE_CHECK, path="b.py"),
            ],
        )
        target = Target(
            name="myapp",
            intent=Intent(name="myapp"),
            validations=[vf1, vf2],
        )

        reg = Registry()
        runner = Runner(reg)
        report = runner.run_target_validations(target, d)

        assert report.total == 2
        assert report.passed == 2


# ===========================================================================
# generate_report
# ===========================================================================


class TestGenerateReport:
    def test_passing_report(self) -> None:
        report = RunReport(
            target="auth",
            total=2,
            passed=2,
            failed=0,
            results={
                "file_check": ValidationResult(
                    validation_name="file_check",
                    passed=True,
                    message="file exists: auth.py",
                ),
                "folder_check": ValidationResult(
                    validation_name="folder_check",
                    passed=True,
                    message="directory exists: src",
                ),
            },
        )
        text = Runner.generate_report(report)

        assert "auth" in text
        assert "[PASS]" in text
        assert "PASSED" in text
        assert "[FAIL]" not in text

    def test_failing_report(self) -> None:
        report = RunReport(
            target="auth",
            total=2,
            passed=1,
            failed=1,
            results={
                "file_check": ValidationResult(
                    validation_name="file_check",
                    passed=True,
                    message="file exists: auth.py",
                ),
                "missing_check": ValidationResult(
                    validation_name="missing_check",
                    passed=False,
                    message="file not found: missing.py",
                ),
            },
        )
        text = Runner.generate_report(report)

        assert "[PASS]" in text
        assert "[FAIL]" in text
        assert "FAILED" in text

    def test_warning_report(self) -> None:
        report = RunReport(
            target="auth",
            total=1,
            passed=1,
            failed=0,
            warnings=1,
            results={
                "style": ValidationResult(
                    validation_name="style",
                    passed=True,
                    message="[warning] minor style issue",
                    severity="warning",
                ),
            },
        )
        text = Runner.generate_report(report)

        assert "[WARN]" in text
        assert "PASSED" in text

    def test_error_entries(self) -> None:
        report = RunReport(
            target="auth",
            total=1,
            passed=0,
            failed=1,
            errors={"broken": "no validator registered for type: llm_judge"},
        )
        text = Runner.generate_report(report)

        assert "[ERR]" in text
        assert "broken" in text
        assert "FAILED" in text
