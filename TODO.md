# TODO

Personal running notes — not for Claude to act on independently.

## Developer Experience & Delight

- [ ] Live DAG visualization — show the dependency graph in the terminal with nodes lighting up as they build, color-coded by status
- [ ] Build narration — stream a condensed play-by-play of what the agent is doing in real-time instead of silence during long builds
- [ ] Build replay — `intentc replay <feature>` to watch a past build unfold step by step from the log, useful for debugging and demos
- [ ] Better logging and visualization of the build process — maybe make it an editor or web app where you can track it in addition to the CLI
- [ ] Nice branding — logo, colors, polished terminal output throughout
- [ ] Suggest next steps after build — "core/project built. You can now build: build/state, build/agents"
- [ ] Detect stale builds automatically — editing an intent file should immediately mark downstream features as outdated in `intentc status`
- [ ] `intentc seed` -- seed from an existing brownfield project in a guided fashion
- [ ] Progress tracking and time estimates

## Build Quality & Intelligence

- [ ] Add git work trees for builds so that they dont interrupt the current git branch
- [ ] Token/cost tracking per build — log API token usage and cost per feature so you can budget and optimize intent verbosity
- [ ] Determinism scoring — run the same build N times and measure how much output varies to validate whether intents are specific enough
- [ ] Add more detail to differencing and think really hard about how to make it as good as possible
- [ ] Validate intents before building — catch broken deps, missing fields, vague specs in milliseconds instead of waiting for an agent round-trip to fail
- [ ] Incremental builds by default — hash intent files + prompt templates and skip anything unchanged
- [ ] Remember what the agent got wrong last time — on retry, include the previous failure reason in the prompt

## New Capabilities

- [ ] Multi-language/multi-target builds — support multiple implementation.ic files (e.g. implementation.python.ic, implementation.typescript.ic) and select which one at build time
- [ ] Add a planning mode to `init` to help seed the DAG in an interactive manner
- [ ] Add a `refactor` mode that takes instructions to refactor all intent files, e.g. removing all language references or adding critical features
- [ ] Add a tool that makes responses from the agent easier than writing a file to disk (if it makes sense to)
- [ ] Use another coding agent with the claude api key to see how it does

## Infrastructure & Robustness

- [ ] Track state in a database (sqlite to start) instead of files
- [ ] Self-recompilation that isolates git history — maybe a submodule that runs self-contained with no side-effects to the parent module
- [ ] Go through the sandboxing and make sure it's as strict as can be

## Showcase & Docs

- [ ] Generate a couple more interesting examples. Maybe a website that compiles from a GitHub Action w/ Claude as a gallery — and add docs for the project as a static site hosted on GitHub
