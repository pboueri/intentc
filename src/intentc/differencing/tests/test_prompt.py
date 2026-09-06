"""Tests that the bundled difference.prompt loads and renders correctly."""

from __future__ import annotations

from intentc.build.agents import (
    DifferencingContext,
    load_default_prompts,
    render_differencing_prompt,
)
from intentc.core import ProjectIntent


def test_difference_prompt_is_bundled_and_loaded_via_installed_package():
    templates = load_default_prompts()

    assert templates.difference != ""
    assert "{output_dir_a}" in templates.difference
    assert "{output_dir_b}" in templates.difference
    assert "{response_file}" in templates.difference


def test_render_differencing_prompt_substitutes_real_values():
    ctx = DifferencingContext(
        output_dir_a="/tmp/build-a",
        output_dir_b="/tmp/build-b",
        project_intent=ProjectIntent(name="project", body="Build a widget."),
        response_file_path="/tmp/response.json",
    )
    templates = load_default_prompts()

    rendered = render_differencing_prompt(templates.difference, ctx)

    assert "/tmp/build-a" in rendered
    assert "/tmp/build-b" in rendered
    assert "/tmp/response.json" in rendered
    assert "Build a widget." in rendered
    assert "{output_dir_a}" not in rendered
    assert "{output_dir_b}" not in rendered
    assert "{response_file}" not in rendered
    assert "{project}" not in rendered
