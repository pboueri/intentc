# Plan: interactive refinement + constraining artifacts

Two new intent features, drafted for review. Nothing has been built yet.

| feature | what it adds | patches |
|---|---|---|
| `constraints/artifacts` | typed, declared supporting files (schemas, designs, fixtures, prompts) that are hashed, sandboxed, shown to the agent and linted | core/specifications, core/project, build/agents, build/validations, build/builder |
| `workflows/refine` | `intentc refine <target>`: an interactive session on built code with a journal, then a bake that rewrites the intent, rebuilds from scratch and checks equivalence | interfaces/cli, build/agents, build/state, build/storage |

`workflows/refine` depends on `constraints/artifacts` (the bake can save pasted schemas as artifacts, and the refine prompts are themselves declared artifacts), so artifacts builds first.

## The refine loop in one picture

```
intentc refine api "make 404s return JSON"
        │
        ▼
 ┌─ session (interactive, user present) ───────────────────────┐
 │  agent edits src/ freely, runs tests, appends journal.md    │
 │  entry per ask: Asked / Decided / Rule / Changed / Check    │
 └──────────────────────────────────────────────────────────────┘
        │  exit → "Bake now? [Y/n]"
        ▼
 snapshot refined tree  →  refs/intentc/refinements/<id>   (HEAD untouched)
        │
        ▼
 ┌─ bake attempt n/retries (non-interactive) ──────────────────┐
 │  1. agent rewrites api.ic + validations.icv from journal+diff│
 │  2. intentc check                                            │
 │  3. builder.clean(api) → builder.build(api, force)           │
 │  4. compare(snapshot, src/)  — functional equivalence        │
 │  any failure → previous_errors → next attempt                │
 └──────────────────────────────────────────────────────────────┘
        │
   ┌────┴─────────────────────┐
   ▼                          ▼
 baked                      failed after retries
 src/ = rebuilt from intent  src/ = refined snapshot restored (uncommitted)
 intent edited, uncommitted  draft intent left on disk, target outdated
 descendants outdated        hint: edit + build -f, or --bake again
```

## Sequencing

1. **Review this draft** (you). Settle the open questions below; I will fold the answers into the `.ic` files.
2. **Build `constraints/artifacts`** — `intentc build constraints/artifacts`. Expected touch points in `src/`: `core/models.py` (Artifact type, `artifacts` fields), `core/parser.py` (frontmatter parsing, write-back), `core/project.py` (`artifacts_for`, `source_files`, lint, `write_project`), `build/agents/agent.py` (`BuildContext.artifacts`, `{artifacts}`), the three prompt templates, `build/validations.py` (`{intent_dir}`, context fields), `build/builder/builder.py` (sandbox read paths, hash). All existing tests must keep passing; new tests next to each.
3. **Dogfood artifacts on intentc itself** — after step 2, declare the existing prompt files in `build/agents/agents.ic`, `differencing/differencing.ic` and `workflows/init/init.ic` under `artifacts:` with `kind: prompt`, and add a `cmp` validation like the one in `workflows/refine/validations.icv` so bundled prompts are byte-identical to the intent copies. This is a deliberate outdating of those features; do it when a recompile is planned anyway.
4. **Build `workflows/refine`** — `intentc build workflows/refine`. New package `intentc/refine/`, new storage table, two agent methods, two prompt templates, CLI command plus `status`/`log`/`build`/`clean` integration.
5. **Manual trial** on `examples/shape_canvas` (small, visual, quick to rebuild): refine `blue_square` with a couple of layout asks, bake, and check the rebuilt canvas matches. That trial is the acceptance test for whether the journal → bake step generalises well enough; expect to iterate on `refine_bake.prompt` there rather than in code.
6. **README** — add `refine` to the commands table and an "Iterate conversationally" paragraph under How It Works; add the `artifacts:` frontmatter to the project-structure section. Update `TODO.md` (this covers the "planning mode" and part of the "refactor mode" items).

## Design decisions already taken (push back if you disagree)

- **Journal, not transcript, is the contract.** The agent writes a structured journal file (like the response-file protocol). It is agent-agnostic and forces the agent to state the *rule* at the moment it knows it. With Claude Code the raw conversation is additionally kept by passing `--session-id`, which also gives free resume with memory.
- **Bake rewrites only the target's intent.** Changes to files owned by upstream features are expressed as patch instructions inside the target's intent (already permitted by the build prompt). Rewriting upstream intents from a downstream chat is too much blast radius for v1.
- **Rebuild = clean + build --force**, so "from scratch" reuses existing builder semantics, including descendants going `outdated`. Refine does not rebuild descendants; the user runs `intentc build` after.
- **Refined trees never enter the linear history.** They live under `refs/intentc/refinements/<session>` via a side-ref snapshot, so history stays a sequence of intent-derived checkpoints.
- **Failure hands the work back.** After the retry budget the refined code is restored (uncommitted) and the draft intent stays on disk. Nothing the user typed for an hour is lost.
- **Artifacts of the target only feed its staleness hash.** Ancestor/project/implementation artifacts are in the sandbox and the prompt but do not invalidate downstream targets automatically.
- **Both features are patch features** (like `workflows/init`), not edits to core intents, so nothing already built goes outdated until you choose to.

## Open questions

1. **Bake on exit.** Default is a `[Y/n]` prompt when the session ends. Would you rather bake always (`--no-bake` to opt out) or never (explicit `--bake`)?
2. **Equivalence check cost.** `compare` after every bake attempt is an agent round-trip on top of the rebuild. Keep it default-on with `--no-compare`, or default-off with `--compare`?
3. **Retry budget.** Bake attempts reuse `profile.retries` (default 3), each including a full rebuild. Separate `--max-bakes` (default 2)?
4. **Cross-feature edits.** Patch instructions in the target's intent (chosen) vs. letting the bake edit ancestor intents too (with the whole subtree outdated afterwards). Or forbid the session from touching other features' files at all?
5. **Where the refined snapshot lives.** Side ref under `refs/intentc/` (chosen) vs. a real branch `intentc/refine/<target>` you can check out, vs. a plain tarball under `.intentc/state/`.
6. **Raw transcript.** Beyond `--session-id`, should the bake also be handed Claude's raw JSONL transcript? It would help with things the agent forgot to journal, at the cost of being Claude-specific and noisy.
7. **Artifacts as a patch feature vs. folding into core.** The typed `artifacts:` field arguably belongs in `core/specifications` proper. Folding it in outdates the whole DAG; the patch feature keeps it incremental. Which do you want for this branch?
8. **Ancestor artifact staleness.** Should editing a project-level design system outdate every built target automatically? I chose no (explicit `--force`), but "the design changed, everything is stale" is also a defensible default.
9. **Inline size limit** for artifact content in prompts: 16 KB per file. Also cap the total?
10. **`{intent_dir}` in validations** lets deterministic checks reach schemas. Is exposing the intent directory to validation commands acceptable given the isolation philosophy (they already run with `cwd: "."`)?
