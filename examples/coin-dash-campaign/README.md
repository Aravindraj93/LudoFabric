# Coin Dash campaign reference upgrade

This is a reproducible upgrade for the existing generated Coin Dash example, **not a standalone game**.
Its purpose is to exercise LudoFabric's shared data contracts, optional reusable foundation, progression
validation, and runtime tests. Other genres are not expected to use this example's coin mechanics.

The original generation was intentionally simple: the request asked for one compact arena, ten coins,
three stationary obstacles, and a short test game. Its design explicitly excluded progression. The old
director prompt reinforced a vertical-slice scope, and the planner emphasized presentation and glue
tasks without a structured progression contract. A single scene was not itself the problem: the
upgrade still reuses one scene and supplies five authored level configurations.

| Stage | Main challenge |
|---|---|
| First Steps | Basic movement and route learning |
| Switchback Alley | Alternating routes around chicanes |
| Inner Orbit | Outer circuit and a central detour |
| Cross Traffic | Timing movement around sliding gates |
| Grand Circuit | Combining cornering, routing and moving obstacles |

The upgrade includes level selection, unlocks, per-stage best times, one-to-three medals, isolated save
profiles for tests, and static-clearance reachability validation. Each stage reuses the ten-coin pool.
Medal thresholds are provisional and require human/device balancing. Android release builds remain
outside the current tool's supported build path.

## Apply to an existing Coin Dash project

Close the project in Unity, then:

```powershell
python tools/upgrade_coin_dash.py --project C:\UnityProjects\CreateCoinDashA
```

The installer saves the original Assets, Packages, ProjectSettings and selected agent checkpoints to
`<Project>.agent/backups/before-campaign-*.zip`. Keep that backup. To roll back safely, restore into a
separate project folder; checkpoint copies under the archive's `.agent` directory belong in the new
project's adjacent `.agent` folder.

The installer copies source and updates existing runtime API adapters and assembly references. Invoke
`CoinDash.EditorTools.CampaignUpgradeBuilder.Build` through Unity to update the scene. Rebuilding the
base arena removes the campaign object, so keep the campaign builder last in the registered builder list.

Shared runtime data and progress code are in `gameagent/unity/starter`. Example-specific adapters and
test fixtures are under this directory's `Assets` folder. The complete generated project, Unity caches,
downloaded assets, original builds, and private logs are not bundled in the repository.

## Checks performed

- Four progression EditMode tests.
- Three PlayMode tests: all stage layouts/load transitions, level-menu pause/resume, moving-gate behavior.
- Geometry/coin placement and static-clearance checks.
- Rendered stage/menu snapshots, a Windows player build and runtime smoke test.

The reference tests use temporary save profiles. These checks do not establish fun, target-device
performance, perfect playability of every dynamic timing pattern, or Google Play readiness.
