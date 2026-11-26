# LudoFabric efficiency and reliability

The first implementation batch improves the generation pipeline across genres. It does not
automatically turn a prototype request into a larger game, or require every game to have levels.

## Implemented

| Change | Behavior |
|---|---|
| Scope-aware designs | Explicit prototype, vertical-slice, and campaign scope; structured stage objectives, challenges, unlock rules, and replay incentives |
| Shared contracts | Plans can define owning files/roles and exact public APIs before specialists implement them |
| Plan validation | Duplicate task IDs, unknown dependencies, cycles, and conflicting contract ownership are rejected |
| Focused context | Task briefs receive relevant dependency APIs and a role-specific design summary rather than the whole design repeatedly |
| Template briefs | Small contracted tasks can skip the separate AI brief call; template mode can skip all brief-generation calls |
| Exact patches | Small changes can use validated, unambiguous old/new replacements instead of complete file rewrites |
| Safe recovery | New task journals fingerprint task/design/contracts and track dependency/input/output hashes; changed inputs invalidate stale results |
| Concurrent human edits | If involved files change while a specialist is generating, the output is not applied over those changes |
| No-op writes | Identical files are not rewritten, avoiding unnecessary Unity reimports |
| Environment repair | Unity package errors use deterministic compatibility repair, then stop instead of asking a model to rewrite package code |
| Repeated-error detection | Unchanged compiler errors stop the repair loop rather than consuming the remaining attempts |
| Budgets | AI request count and cooperative elapsed-time budgets pause with progress saved |
| Measurements | Local JSONL metrics distinguish model calls, response-cache hits, model time, input characters, Unity runs and Unity time |
| Test integrity | Missing, malformed, empty, or failed Unity test reports cannot be reported as successful |
| Reusable foundations | An optional, separately compiled arcade campaign/save library is installed under `Assets/LudoFabricStarter` |

The optional campaign foundation fits time/score-based stages. Other genres should define their own
appropriate objectives and contracts; coins, timers and medals are not universal requirements.
The protected foundation and bridge are maintained by the tool, not overwritten by specialist outputs.

Legacy task journals remain readable for checkpoint compatibility. New journals use dependency-aware
validation. API token usage is recorded when returned by the provider. CLI token usage is unavailable;
input character counts are not token counts.
Telemetry begins when enabled and cannot reconstruct historical model usage from older runs.

## Configuration

Use **Setup → Efficiency controls** in the dashboard, or set:

```dotenv
BRIEF_MODE=auto
MAX_LLM_CALLS=0
MAX_JOB_SECONDS=0
```

- `auto`: use a template for small tasks that have shared contracts; otherwise generate an AI brief.
- `template`: always construct the brief locally.
- `ai`: always request an AI-generated brief.
- A zero budget means unlimited. Nonzero budgets are checked before the next model request, including
  retries. They do not cancel an active model request or Unity operation. Resume starts a new run budget.
- Completed response-cache hits remain usable after an AI request budget is exhausted.

Per-project measurements live at `<Project>.agent/metrics.jsonl` and appear in the dashboard project
detail. These measurements establish a baseline; no percentage improvement has been benchmarked yet.

```powershell
python tools/efficiency_report.py --project C:\UnityProjects\MyGame
```

`benchmarks/scenarios.json` supplies repeatable prompts and human acceptance checks for three genres.
These are benchmark inputs, not claims of completed or validated reference games. The report command
only reads local measurements; it never starts a paid model call or Unity operation.

## Next batches

1. Benchmark the same small reference prompts and compare first-pass correctness, provider calls,
   elapsed time, repair frequency, and human playtest results. Avoid trading quality for token savings.
2. Add a persistent Unity worker with explicit request/response IDs, domain-reload recovery, compile
   diagnostics, cancellation, and a per-project lock. This is not implemented in this batch.
3. Add isolated generation for independent tasks and serialize application/Unity validation. Dynamic
   output ownership and checkpoint safety must be established before enabling parallel execution.
4. Evaluate model routing and broader patch adoption using measured success rates. The existing
   per-provider design/coding/vision model controls remain available; no hidden paid-provider fallback occurs.

## Validation

```powershell
python -m unittest discover -s tests -v
```

The suite covers stale checkpoints, interrupted patch replay, changed human files, budgets, cache
measurements, contract brief selection, graph cycles, package error handling, and empty Unity reports.
It does not claim gameplay quality or provider-wide benchmark results.
