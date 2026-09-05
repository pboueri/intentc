from pathlib import Path

from intentc.core import ParseError, ParseErrors, Severity, ValidationType


def test_validation_type_values():
    assert ValidationType.AGENT_VALIDATION.value == "agent_validation"
    assert ValidationType.COMMAND_VALIDATION.value == "command_validation"
    assert ValidationType.FILE_EXISTS.value == "file_exists"


def test_severity_values():
    assert Severity.ERROR.value == "error"
    assert Severity.WARNING.value == "warning"


def test_parse_error_str_without_field():
    err = ParseError(Path("intent/foo.ic"), None, "missing frontmatter")
    assert str(err) == "intent/foo.ic: missing frontmatter"


def test_parse_error_str_with_field():
    err = ParseError(Path("intent/foo.ic"), "name", "missing or empty required field")
    assert str(err) == "intent/foo.ic [name]: missing or empty required field"


def test_parse_errors_message_format():
    errors = [
        ParseError(Path("a.ic"), "name", "missing"),
        ParseError(Path("b.icv"), None, "bad yaml"),
    ]
    exc = ParseErrors(errors)
    assert exc.errors == errors
    message = str(exc)
    assert message.startswith("2 parse error(s):\n")
    assert "a.ic [name]: missing" in message
    assert "b.icv: bad yaml" in message
