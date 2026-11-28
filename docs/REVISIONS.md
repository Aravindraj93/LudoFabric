# Improve an existing Unity game

Restart the LudoFabric server after updating it, then refresh the browser.

1. Open a shipped project and click **Improve this game**. This fills in the source path and a new destination name.
2. Describe the changes you want. For example: improve coin pickup particles, lighting and victory animation while preserving gameplay, all levels, controls and saved progress.
3. Choose **Design & plan** to review the proposal, or **Build the full game** to implement it immediately.
4. After reviewing the plan, choose **Build in Unity** on the revision project. Use **Resume** after an interruption.

You can also enter **Source project (optional revision)** directly. Close the source project in Unity before the initial snapshot. Keep the source and destination in separate folders. Paths mentioned in the original form `Improve the existing game at C:\UnityProjects\Coins. ...` are recognized for compatibility; use the source field for other wording.

The host inspects actual project files, saves a source snapshot, and copies `Assets`, `Packages` and `ProjectSettings` with their metadata. Existing `Docs`, `Tests`, root `AGENTS.md`, `README.md` and the two project search-index settings (`UserSettings/Search.index` and `Search.settings`) are retained when present. It excludes Unity caches and previous player builds. Change requests include bounded project instructions and recent development history as well as code context. The source project is never the build destination. The revision preserves its own checkpoints and model-response cache across restarts.

Artifacts in `<revision>.agent/`:

- `source-snapshot.zip`: original authored content.
- `revision.json`: source/destination paths and baseline file hashes.
- `REVISION_CONTEXT.md`: bounded context supplied to the agents.
- `GDD.json` and `PLAN.json`: proposed design and implementation tasks.
- `revision_changes.json`: added, deleted and modified files after successful delivery.

The previously blocked revision attempt can be migrated if it has no completed tasks. Its old state and destination contents are archived rather than deleted. A destination with completed work is not overwritten; choose a new revision name.

Current limits: Unity 6 projects supported by LudoFabric's existing bridge; registry/Git/embedded packages only (embed `file:` dependencies first); linked project folders are rejected. Inspection is bounded and does not replace visual review in Unity. Automated delivery is a Windows player with the existing compile/test/build/smoke checks. Android builds, Android device profiling, and subjective visual approval are not automated by this workflow. Saved progress compatibility depends on keeping the application's identity and save keys; test with an isolated profile before replacing an installed build.

CLI equivalent:

```powershell
python main.py run "Improve pickup VFX and UI transitions; preserve gameplay and saves" --source "C:\UnityProjects\Coins" --project "C:\UnityProjects\Coins_Revision"
python main.py resume --project "C:\UnityProjects\Coins_Revision"
```
