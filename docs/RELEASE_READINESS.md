# Release readiness — merged verification (2026-11-28)

LudoFabric remains a local developer preview, not a production-certified game studio.

## Implemented and regression-tested in this batch

- Asset keys must be safe lowercase identifiers, with Windows device names rejected.
- Asset destinations reject traversal, absolute paths, alternate data streams, ambiguous Windows names,
  and existing symlinks/junctions. URL query strings are not treated as filenames.
- Downloads use temporary files and replace their destination only after successful bounded streaming.
  Limits per acquisition: 256 MiB per file, 512 MiB total download, 2,000 files, and a 300-second
  cooperative deadline. Existing HTTP read timeouts still apply while waiting for network data.
- ZIP extraction rejects traversal, link/special/encrypted entries, case-colliding paths, and excessive
  expansion (100:1 maximum ratio; 512 MiB total extracted). Actual copied bytes are checked too.
- Download attempts use fresh staging directories. Failed acquisition does not delete existing user
  libraries. A new successful acquisition gets a distinct directory when the old destination exists.
- Asset registry writes are atomic, and cached texture companion files are checked before reuse.
- Unity reports with zero passed tests or inconclusive cases fail validation.
- Player smoke checks require a readable log and a player that remains running through the observation
  interval. Early exits, absent logs and runtime exceptions fail; the process is reaped after observation.

## Actual validation

The downloaded upgrade was merged with the workspace's source-copy revision support using a three-way comparison against the repository baseline. Local credentials, virtual environments, runtime logs, generated games and unrelated developer scripts were not imported or replaced. A pre-merge workspace backup is retained locally under `local_test/`.

Verified on Windows with CPython 3.13.14:

- **127 regression tests passed**, including the downloaded tests, local revision tests, bounded log capture, recent-project persistence and historical-notice dismissal.
- A fresh virtual environment installed all 55 declared/transitive packages successfully; `uv pip check` reported no dependency conflicts.
- `requirements-lock.txt` records the verified dependency set. Install with `python -m pip install -r requirements.txt -c requirements-lock.txt`.
- Compile checks passed for Python. `tools/check_source.py` found no duplicate Python definitions or dashboard IDs and passed JavaScript syntax checks.
- A real local dashboard process served its homepage, settings, project list and job status with HTTP 200. Missing tokens, cross-site requests and invalid Host headers returned 403; an invalid job mode returned 422.
- Unity 6000.5.0f1 compiled the updated bridge with zero compiler errors in an isolated test project.
- Real Unity scene-builder execution, two screenshot captures, the deterministic visual gate, a Windows player build and a 10-second player smoke run all passed. This was a diagnostic scene, not a benchmark of an AI-generated game.

The merge also fixes source-path forwarding into both engines, preserves selected revision build targets and application identity, retains the LangGraph thread ID and retry history across restarts, reconstructs paused exceptions after recovery-node interruptions, captures resolved worker configuration, and adds OS-owned project mutation locks to public build/change/rollback entry points.

HTTP asset streams and model calls in the regression suite are mocked. No paid provider call, live asset download, non-Windows build, phone test or browser visual inspection was performed for this merge. Byte limits and deadlines are safety defaults, not measured production performance targets. Existing gameplay saves, store compliance and subjective visual quality still require representative testing.

### Subsequent existing-project integration test

A local ten-level Unity 6 URP game was copied into an isolated test project, including its instructions, development history and custom test harnesses. Original authored files were checked against the snapshot. A separate test company/product name isolated progress from the original application.

Using gpt-6.1-sol through the signed-in Codex CLI, LudoFabric made one change-planning request and one implementation request to create a read-only Editor scene audit. The new command compiled and ran through the external-project change workflow. It confirmed ten enabled levels, eleven generated scene assets, and no missing or invalid scene references. The generated change modified no existing files.

The existing ten-level native regression then passed, followed by all 479 Guardian benchmark assertions and 147 crown-framing assertions. Original source files and all authored scene-file hashes in the copy remained unchanged. This verifies the listed software checks and one small AI-authored change; it does not establish full-game completion or visual/device approval.

Initial attempts exposed Windows account permissions and missing project search-index settings. The latter produced a Unity Search startup exception; a headless attempt also produced URP render-pass errors. Retaining the original search settings and running with Direct3D 11 resolved the native-suite startup in this copy. Copy support now preserves the two search settings. Repeated Unity errors also motivated bounded log capture: truncated evidence fails verification while the full log stays on disk. Initial Unity import updated one global-settings asset and five materials in the copy before the AI change; the source was preserved.

Recent project paths and last-opened times persist locally across restarts without duplicate records for the same path. Old failure notices can be dismissed independently of project checkpoints. The dashboard was restarted and these paths were checked through its live API.

## Still blocking unrestricted production release

1. Transactional rollback covering repairs, Editor side effects, scenes, settings and Unity `.meta` files.
2. Source-hash-bound validation evidence; final visual/gameplay gates after all corrections.
3. Platform adaptation tasks, especially touch input and mobile UI, before adding mobile build targets.
4. Isolated execution of untrusted generated/imported Editor code; static checks are not a sandbox.
5. Full browser interaction, representative games, provider compatibility benchmarks, dependency vulnerability review, device testing and validation on other supported Python/OS combinations. Local smoke checks do not replace these release gates.

Path checks and staging do not eliminate concurrent filesystem race attacks by another local process.
Attribution preservation/distribution and imported glTF content validation need further work. Downloaded
libraries remain outside generated-code rollback and must not be advertised as transactionally restored.
