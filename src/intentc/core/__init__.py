"""Core intent/validation data models and file I/O. No dependency on build, cli, or differencing."""

from intentc.core.models import (
    Implementation,
    IntentFile,
    ParseError,
    ParseErrors,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
)
from intentc.core.parser import (
    content_hash,
    extract_file_references,
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)

__all__ = [
    "IntentFile",
    "ProjectIntent",
    "Implementation",
    "ValidationFile",
    "Validation",
    "ValidationType",
    "Severity",
    "extract_file_references",
    "ParseError",
    "ParseErrors",
    "parse_intent_file",
    "parse_validation_file",
    "write_intent_file",
    "write_validation_file",
    "content_hash",
]
