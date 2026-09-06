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

1. **Review this draft** — done; decisions recorded below.
2. **Build `constraints/artifacts`** — `intentc build constraints/artifacts`. Expected touch points in `src/`: `core/models.py` (Artifact type, `artifacts` fields), `core/parser.py` (frontmatter parsing, write-back), `core/project.py` (`artifacts_for`, `source_files`, lint, `write_project`), `build/agents/agent.py` (`BuildContext.artifacts`, `{artifacts}`), the three prompt templates, `build/validations.py` (`{intent_dir}`, context fields), `build/builder/builder.py` (sandbox read paths, hash). All existing tests must keep passing; new tests next to each.
3. **Dogfood artifacts on intentc itself** — after step 2, declare the existing prompt files in `build/agents/agents.ic`, `differencing/differencing.ic` and `workflows/init/init.ic` under `artifacts:` with `kind: prompt`, and add a `cmp` validation like the one in `workflows/refine/validations.icv` so bundled prompts are byte-identical to the intent copies. This is a deliberate outdating of those features; do it when a recompile is planned anyway.
4. **Build `workflows/refine`** — `intentc build workflows/refine`. New package `intentc/refine/`, new storage table, two agent methods, two prompt templates, CLI command plus `status`/`log`/`build`/`clean` integration.
5. **Manual trial** on `examples/shape_canvas` (small, visual, quick to rebuild): refine `blue_square` with a couple of layout asks, bake, and check the rebuilt canvas matches. That trial is the acceptance test for whether the journal → bake step generalises well enough; expect to iterate on `refine_bake.prompt` there rather than in code.
6. **README** — add `refine` to the commands table and an "Iterate conversationally" paragraph under How It Works; add the `artifacts:` frontmatter to the project-structure section. Update `TODO.md` (this covers the "planning mode" and part of the "refactor mode" items).

## Design decisions already taken (push back if you disagree)

- **Journal, not transcript, is the contract.** The agent writes a structured journal file (like the response-file protocol). It is agent-agnostic and forces the agent to state the *rule* at the moment it knows it. No agent-specific transcript, session id or resume mechanism is assumed; resume re-feeds the journal.
- **Bake rewrites only the target's intent.** Changes to files owned by upstream features are expressed as patch instructions inside the target's intent (already permitted by the build prompt). Rewriting upstream intents from a downstream chat is too much blast radius for v1.
- **Rebuild = clean + build --force**, so "from scratch" reuses existing builder semantics, including descendants going `outdated`. Refine does not rebuild descendants; the user runs `intentc build` after.
- **Refined trees never enter the linear history.** They live under `refs/intentc/refinements/<session>` via a side-ref snapshot, so history stays a sequence of intent-derived checkpoints.
- **Failure hands the work back.** After the retry budget the refined code is restored (uncommitted) and the draft intent stays on disk. Nothing the user typed for an hour is lost.
- **Artifacts of the target only feed its staleness hash.** Ancestor/project/implementation artifacts are in the sandbox and the prompt but do not invalidate downstream targets automatically.
- **Both features are patch features** (like `workflows/init`), not edits to core intents, so nothing already built goes outdated until you choose to.

## Decisions on the review questions

Settled at review; the intents reflect them.

1. Bake on exit: `[Y/n]` prompt at the terminal; `--bake` / `--no-bake` skip it.
2. `compare` runs after every bake attempt by default; `--no-compare` opts out.
3. Bake attempts reuse `profile.retries`; no separate flag.
4. Cross-feature edits become patch instructions inside the target's intent.
5. Refined trees live under `refs/intentc/refinements/<session>`.
6. No agent-specific transcript or session mechanism. The journal is the only memory the workflow relies on.
7. Artifacts stay a patch feature on this branch.
8. Ancestor / project / implementation artifacts do not invalidate targets automatically; `--force` does.
9. 16 KB per-file inline limit, no total cap.
10. `{intent_dir}` is available to validation commands.

## Trial findings (todo-app, single-shot `cli` provider session, then `--bake`)

The session phase worked first time: two journal entries with implementation-independent Rules and deterministic Checks, only `cli.py` touched, `status` showed the open session. The bake phase surfaced four spec gaps, now folded into `refine.ic` and `refine_bake.prompt`:

1. `build(target, force=True)` regenerated every ancestor, not just the target. The rebuild is now specified without `force` (`clean` already makes the target pending).
2. The bake agent referenced its check scripts as `{intent_dir}/checks/...` when they lived under `intent/cli/checks/`; the prompt now spells out that `{intent_dir}` is the intent root.
3. On the retry, the agent only received "validation failed: 0/4 passed" and guessed at a `depends_on` problem. `previous_errors` must now carry each failed validation's reason.
4. The rebuild checkpoint (`git add -A`) swept the baked intent into a `build:` commit. The bake now commits the feature directory as its own `refine <target>: attempt n` commit first, and `--bake` can re-bake a `failed` session.

Plus: log prefixes written as `[bake 1/2]` were swallowed as terminal markup; the spec now uses `bake 1/2:`.

## Build

Both features are built with intentc itself, in order: `intentc build constraints/artifacts`, then `intentc build workflows/refine`. No hand-written implementation.
