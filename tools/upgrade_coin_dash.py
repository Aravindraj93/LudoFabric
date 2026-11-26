"""Install the five-stage campaign into an existing Coin Dash project, with a restorable backup.

Usage: python tools/upgrade_coin_dash.py --project C:/UnityProjects/CreateCoinDashA
Only installs source. Run CoinDash.EditorTools.CampaignUpgradeBuilder.Build through Unity afterward.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameagent.config import Config
from gameagent.state import atomic_json
from gameagent.unity.project import UnityProject


def upgrade(project: Path) -> Path:
    project = project.resolve()
    if not (project / "Assets/Game/Scripts/Core/GameConfig.cs").is_file():
        raise ValueError("This upgrade requires the existing generated Coin Dash game.")
    state_dir = project.parent / f"{project.name}.agent"
    state_dir.mkdir(exist_ok=True)
    backup = state_dir / "backups" / f"before-campaign-{time.time_ns()}.zip"
    backup.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder in ("Assets", "Packages", "ProjectSettings"):
            for file in (project / folder).rglob("*"):
                if file.is_file(): archive.write(file, file.relative_to(project).as_posix())
        for name in ("state.json", "GDD.json", "PLAN.json"):
            if (state_dir / name).exists(): archive.write(state_dir / name, ".agent/" + name)

    assets = ROOT / "examples/coin-dash-campaign/Assets"
    for source in assets.rglob("*"):
        if source.is_file():
            destination = project / "Assets" / source.relative_to(assets)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    modifications = {
        "Assets/Game/Scripts/Core/GameConfig.cs": ("public void ConfigureLevel(", "        public float RunDurationSeconds", """        public void ConfigureLevel(float seconds, int coinCount)
        {
            if (float.IsNaN(seconds) || float.IsInfinity(seconds) || seconds <= 0f || coinCount <= 0)
                throw new System.ArgumentOutOfRangeException(nameof(seconds));
            runDurationSeconds = seconds;
            totalCoins = coinCount;
        }

"""),
        "Assets/Game/Scripts/GameFlow/GameBootstrapper.cs": ("public void OverrideConfig(", "        public void Configure(", """        public void OverrideConfig(GameConfig value)
        {
            if (initialized) throw new System.InvalidOperationException("Override config before bootstrap initialization.");
            config = value;
        }

"""),
        "Assets/Game/Scripts/Run/BestTimeStore.cs": ("public void ConfigureKey(", "        private bool hasBestTime;", """        private string storageKey = PlayerPrefsKey;
        public void ConfigureKey(string key)
        {
            storageKey = string.IsNullOrWhiteSpace(key) ? PlayerPrefsKey : key;
            Load();
        }

"""),
    }
    for rel, (marker, anchor, addition) in modifications.items():
        path = project / rel
        text = path.read_text(encoding="utf-8")
        if marker not in text:
            if anchor not in text: raise ValueError(f"Cannot safely patch {rel}; restore from {backup} if needed.")
            text = text.replace(anchor, addition + anchor, 1)
            if "BestTimeStore" in rel:
                for function in ("HasKey", "GetFloat", "SetFloat", "DeleteKey"):
                    text = text.replace(f"PlayerPrefs.{function}(PlayerPrefsKey", f"PlayerPrefs.{function}(storageKey")
            path.write_text(text, encoding="utf-8", newline="\n")

    for rel in ("Assets/Game/Scripts/CoinDash.Runtime.asmdef", "Assets/Tests/EditMode/CoinDash.EditMode.asmdef",
                "Assets/Tests/PlayMode/CoinDash.PlayMode.asmdef"):
        path = project / rel
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if "LudoFabric.Runtime" not in data.setdefault("references", []): data["references"].append("LudoFabric.Runtime")
            atomic_json(path, data)
    UnityProject(Config(), project, state_dir).install_bridge()
    print(f"Campaign source installed. Original project backup: {backup}")
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    upgrade(parser.parse_args().project)
