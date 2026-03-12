"""Tests for core types."""

import json
from datetime import datetime, timedelta

import yaml

from core.types import (
    AgentProfile,
    BuildPhase,
    BuildResult,
    BuildStep,
    Intent,
    PromptTemplates,
    SchemaViolation,
    StepStatus,
    Target,
    TargetStatus,
    ToolConfig,
    Validation,
    ValidationFile,
    ValidationResult,
    ValidationType,
    _parse_duration,
    _serialize_duration,
)


def test_intent_creation():
    intent = Intent(name="auth", version=1, depends_on=["core"], tags=["security"], content="# Auth\nDesc")
    assert intent.name == "auth"
    assert intent.version == 1
    assert intent.depends_on == ["core"]
    assert intent.tags == ["security"]
    assert intent.content == "# Auth\nDesc"


def test_intent_defaults():
    intent = Intent()
    assert intent.name == ""
    assert intent.version == 1
    assert intent.depends_on == []
    assert intent.tags == []
    assert intent.profile == ""
    assert intent.content == ""
    assert intent.file_path == ""


def test_validation_type_enum():
    assert ValidationType.FILE_CHECK == "file_check"
    assert ValidationType.FOLDER_CHECK == "folder_check"
    assert ValidationType.COMMAND_CHECK == "command_check"
    assert ValidationType.LLM_JUDGE == "llm_judge"


def test_target_status_enum():
    assert TargetStatus.PENDING == "pending"
    assert TargetStatus.BUILDING == "building"
    assert TargetStatus.BUILT == "built"
    assert TargetStatus.FAILED == "failed"
    assert TargetStatus.OUTDATED == "outdated"


def test_validation_creation():
    v = Validation(name="check1", type=ValidationType.FILE_CHECK, parameters={"path": "src/auth.py"})
    assert v.name == "check1"
    assert v.type == ValidationType.FILE_CHECK
    assert v.hidden is False
    assert v.parameters == {"path": "src/auth.py"}


def test_validation_file():
    vf = ValidationFile(
        target="auth",
        version=1,
        judge_profile="review",
        validations=[Validation(name="check1", type=ValidationType.FILE_CHECK)],
    )
    assert vf.target == "auth"
    assert vf.judge_profile == "review"
    assert len(vf.validations) == 1


def test_target():
    intent = Intent(name="auth")
    target = Target(name="auth", intent=intent, status=TargetStatus.PENDING)
    assert target.name == "auth"
    assert target.status == TargetStatus.PENDING
    assert target.dependencies == []
    assert target.validations == []


def test_build_result_serializable():
    br = BuildResult(
        target="auth",
        generation_id="gen-123",
        success=True,
        generated_at=datetime(2024, 1, 1),
        files=["src/auth.py"],
        output_dir="/tmp/out",
    )
    data = json.loads(br.model_dump_json())
    assert data["target"] == "auth"
    assert data["generation_id"] == "gen-123"
    assert data["success"] is True
    assert data["error"] == ""


def test_validation_result():
    vr = ValidationResult(
        validation_name="check1",
        passed=True,
        message="OK",
        details=["detail1"],
        severity="error",
    )
    assert vr.validation_name == "check1"
    assert vr.passed is True


def test_schema_violation():
    sv = SchemaViolation(
        file_path="intent/auth/auth.ic",
        field="name",
        message="missing required field",
        severity="error",
    )
    data = json.loads(sv.model_dump_json())
    assert data["file_path"] == "intent/auth/auth.ic"


def test_tool_config():
    tc = ToolConfig(name="bash", enabled=True, config={"timeout": 30})
    assert tc.name == "bash"
    assert tc.enabled is True


def test_prompt_templates():
    pt = PromptTemplates(build="Build {intent}", validate="Validate {rubric}", system="You are a coder")
    assert pt.build == "Build {intent}"


def test_agent_profile_defaults():
    ap = AgentProfile()
    assert ap.name == "default"
    assert ap.provider == "claude"
    assert ap.timeout == timedelta(minutes=5)
    assert ap.retries == 3
    assert ap.rate_limit == timedelta(seconds=1)


def test_agent_profile_from_yaml_dict():
    data = {
        "provider": "claude",
        "timeout": "5m",
        "retries": 3,
        "rate_limit": "1s",
        "model_id": "claude-sonnet-4-6",
        "tools": [{"name": "bash", "enabled": True}],
        "skills": ["code-generation"],
    }
    ap = AgentProfile.from_yaml_dict("default", data)
    assert ap.name == "default"
    assert ap.provider == "claude"
    assert ap.timeout == timedelta(minutes=5)
    assert ap.model_id == "claude-sonnet-4-6"
    assert len(ap.tools) == 1
    assert ap.tools[0].name == "bash"


def test_agent_profile_to_yaml_dict():
    ap = AgentProfile(
        name="default",
        provider="claude",
        model_id="claude-sonnet-4-6",
        tools=[ToolConfig(name="bash", enabled=True)],
    )
    d = ap.to_yaml_dict()
    assert d["provider"] == "claude"
    assert d["model_id"] == "claude-sonnet-4-6"
    assert "timeout" in d


def test_agent_profile_yaml_roundtrip():
    ap = AgentProfile(
        name="test",
        provider="claude",
        model_id="claude-sonnet-4-6",
        timeout=timedelta(minutes=5),
        retries=3,
    )
    d = ap.to_yaml_dict()
    yaml_str = yaml.dump(d)
    loaded = yaml.safe_load(yaml_str)
    ap2 = AgentProfile.from_yaml_dict("test", loaded)
    assert ap2.provider == ap.provider
    assert ap2.model_id == ap.model_id
    assert ap2.timeout == ap.timeout


def test_parse_duration():
    assert _parse_duration("5m") == timedelta(minutes=5)
    assert _parse_duration("1s") == timedelta(seconds=1)
    assert _parse_duration("2h") == timedelta(hours=2)
    assert _parse_duration("500ms") == timedelta(milliseconds=500)
    assert _parse_duration(30) == timedelta(seconds=30)


def test_serialize_duration():
    assert _serialize_duration(timedelta(minutes=5)) == "5m"
    assert _serialize_duration(timedelta(seconds=1)) == "1s"
    assert _serialize_duration(timedelta(hours=2)) == "2h"


def test_all_types_json_serializable():
    """Verify all types can serialize to JSON."""
    types_to_test = [
        Intent(name="test"),
        Validation(name="v1", type=ValidationType.FILE_CHECK),
        ValidationFile(target="test"),
        BuildResult(target="test", generation_id="gen-1"),
        ValidationResult(validation_name="v1"),
        SchemaViolation(file_path="test.ic", field="name", message="err"),
        AgentProfile(name="default"),
        PromptTemplates(),
        ToolConfig(name="bash"),
        BuildStep(),
    ]
    for obj in types_to_test:
        json_str = obj.model_dump_json()
        assert json_str  # non-empty
        data = json.loads(json_str)
        assert isinstance(data, dict)


class TestBuildPhase:
    """Tests for BuildPhase enum."""

    def test_enum_values(self):
        assert BuildPhase.RESOLVE_DEPS == "resolve_deps"
        assert BuildPhase.READ_PLAN == "read_plan"
        assert BuildPhase.BUILD == "build"
        assert BuildPhase.POST_BUILD == "post_build"
        assert BuildPhase.VALIDATE == "validate"

    def test_enum_count(self):
        assert len(BuildPhase) == 5


class TestStepStatus:
    """Tests for StepStatus enum."""

    def test_enum_values(self):
        assert StepStatus.SUCCESS == "success"
        assert StepStatus.FAILED == "failed"
        assert StepStatus.SKIPPED == "skipped"


class TestBuildStep:
    """Tests for BuildStep model."""

    def test_defaults(self):
        step = BuildStep()
        assert step.phase == BuildPhase.BUILD
        assert step.status == StepStatus.SUCCESS
        assert isinstance(step.started_at, datetime)
        assert isinstance(step.ended_at, datetime)
        assert step.duration_seconds == 0.0
        assert step.summary == ""
        assert step.error == ""
        assert step.files_changed == 0
        assert step.diff_stat == ""
        assert step.diff == ""

    def test_serialization_roundtrip(self):
        step = BuildStep(
            phase=BuildPhase.RESOLVE_DEPS,
            status=StepStatus.FAILED,
            duration_seconds=1.5,
            summary="Resolved deps",
            error="timeout",
            files_changed=3,
            diff_stat="3 files changed",
            diff="--- a/foo\n+++ b/foo",
        )
        json_str = step.model_dump_json()
        data = json.loads(json_str)
        assert data["phase"] == "resolve_deps"
        assert data["status"] == "failed"
        assert data["duration_seconds"] == 1.5
        assert data["summary"] == "Resolved deps"
        assert data["error"] == "timeout"
        assert data["files_changed"] == 3

        # Roundtrip: parse back from JSON
        step2 = BuildStep.model_validate_json(json_str)
        assert step2.phase == BuildPhase.RESOLVE_DEPS
        assert step2.status == StepStatus.FAILED
        assert step2.duration_seconds == 1.5


class TestBuildResultSteps:
    """Tests for BuildResult with steps and total_duration_seconds."""

    def test_default_empty_steps(self):
        br = BuildResult(target="test", generation_id="gen-1")
        assert br.steps == []
        assert br.total_duration_seconds == 0.0

    def test_with_steps_populated(self):
        steps = [
            BuildStep(phase=BuildPhase.RESOLVE_DEPS, duration_seconds=0.5),
            BuildStep(phase=BuildPhase.BUILD, duration_seconds=2.0, files_changed=5),
        ]
        br = BuildResult(
            target="auth",
            generation_id="gen-2",
            success=True,
            steps=steps,
            total_duration_seconds=2.5,
        )
        assert len(br.steps) == 2
        assert br.steps[0].phase == BuildPhase.RESOLVE_DEPS
        assert br.steps[1].files_changed == 5
        assert br.total_duration_seconds == 2.5

    def test_backward_compatible_deserialization(self):
        """Old JSON without steps/total_duration_seconds still parses."""
        old_json = json.dumps({
            "target": "auth",
            "generation_id": "gen-old",
            "success": True,
            "error": "",
            "generated_at": "2024-01-01T00:00:00",
            "files": ["src/auth.py"],
            "output_dir": "/tmp/out",
        })
        br = BuildResult.model_validate_json(old_json)
        assert br.target == "auth"
        assert br.steps == []
        assert br.total_duration_seconds == 0.0
