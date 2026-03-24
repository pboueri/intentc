from __future__ import annotations

from pathlib import Path

from intentc.build.agents.types import (
    BuildContext,
    DifferencingContext,
    PromptTemplates,
)


def load_default_prompts() -> PromptTemplates:
    """Load default prompt templates from the intent directory.

    Paths are resolved relative to cwd/intent/.
    Missing files result in empty strings (no error).
    """
    intent_dir = Path.cwd() / "intent"
    templates: dict[str, str] = {}
    paths = {
        "build": intent_dir / "build" / "agents" / "prompts" / "build.prompt",
        "validate_template": intent_dir / "build" / "agents" / "prompts" / "validate.prompt",
        "plan": intent_dir / "build" / "agents" / "prompts" / "plan.prompt",
        "difference": intent_dir / "differencing" / "prompts" / "difference.prompt",
    }
    for field, path in paths.items():
        if path.is_file():
            templates[field] = path.read_text()
        else:
            templates[field] = ""
    return PromptTemplates(**templates)


def render_prompt(template: str, ctx: BuildContext) -> str:
    """Render a prompt template with BuildContext variables."""
    previous_errors = ""
    if ctx.previous_errors:
        errors_list = "\n".join(f"- {err}" for err in ctx.previous_errors)
        previous_errors = (
            f"\n### Previous Errors\n"
            f"This is a retry. Previous attempts failed with the following errors. "
            f"You MUST fix these issues:\n{errors_list}\n"
        )
    return template.format(
        project=ctx.project_intent.body,
        implementation=ctx.implementation.body if ctx.implementation else "",
        feature=ctx.intent.body,
        validations="\n".join(
            _format_validation_file(vf) for vf in ctx.validations
        ),
        validation="",  # single validation placeholder for validate template
        response_file=ctx.response_file_path,
        previous_errors=previous_errors,
    )


def render_validate_prompt(template: str, ctx: BuildContext, validation_text: str) -> str:
    """Render a validation prompt template."""
    return template.format(
        project=ctx.project_intent.body,
        implementation=ctx.implementation.body if ctx.implementation else "",
        feature=ctx.intent.body,
        validation=validation_text,
        response_file=ctx.response_file_path,
    )


def render_differencing_prompt(template: str, ctx: DifferencingContext) -> str:
    """Render a differencing prompt template."""
    return template.format(
        project=ctx.project_intent.body,
        implementation=ctx.implementation.body if ctx.implementation else "",
        output_dir_a=ctx.output_dir_a,
        output_dir_b=ctx.output_dir_b,
        response_file=ctx.response_file_path,
    )


def _format_validation_file(vf: object) -> str:
    """Format a ValidationFile for inclusion in a prompt."""
    from intentc.core.types import ValidationFile

    if not isinstance(vf, ValidationFile):
        return str(vf)
    parts = [f"target: {vf.target}"]
    for v in vf.validations:
        parts.append(f"- {v.name} ({v.type}, {v.severity.value}): {v.args}")
    return "\n".join(parts)
