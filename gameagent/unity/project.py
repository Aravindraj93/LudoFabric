"""Unity project lifecycle: create, configure packages, install bridge, compile, build scenes, capture, build, smoke-run."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path


from ..config import Config
from .runner import UnityResult, UnityRunner

from ..ui import console
BRIDGE_SRC = Path(__file__).parent / "bridge"
STARTER_SRC = Path(__file__).parent / "starter"
BRIDGE_DST = "Assets/GameAgentBridge/Editor"
CODE_EXT = {".cs", ".shader", ".hlsl", ".cginc", ".asmdef", ".asmref", ".json", ".uss", ".uxml"}

FALLBACK_PACKAGES = {
    "com.unity.render-pipelines.universal": "17.0.4",
    "com.unity.inputsystem": "1.11.2",
    "com.unity.cinemachine": "3.1.7",
    "com.unity.timeline": "1.8.7",
    "com.unity.ai.navigation": "2.0.5",
    "com.unity.animation.rigging": "1.3.0",
    "com.unity.ugui": "2.0.0",
    "com.unity.test-framework": "1.4.5",
}


class UnityProject:
    def __init__(self, cfg: Config, path: Path, state_dir: Path):
        self.cfg = cfg
        self.path = path.resolve()
        self.state_dir = state_dir
        self.runner = UnityRunner(cfg.unity_editor, self.path, state_dir / "unity_logs", cfg.unity_timeout)
        self.editor_data = Path(cfg.unity_editor).parent / "Data"

    # ------------------------------------------------------------------ setup
    def ensure_created(self) -> None:
        if (self.path / "Assets").exists() and (self.path / "ProjectSettings").exists():
            console.print(f"[green]Using existing Unity project {self.path}[/]")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            args = ["-createProject", str(self.path)]
            template = self._find_urp_template()
            if template:
                console.print(f"[cyan]Creating URP project from template {template.name}[/]")
                args += ["-cloneFromTemplate", str(template)]
            else:
                console.print("[cyan]Creating empty project (URP will be configured by the bridge)[/]")
            res = self.runner.run(args, "create_project")
            if not (self.path / "Assets").exists():
                raise RuntimeError("Unity failed to create the project:\n" + res.log[-3000:])
        self._configure_packages()
        self._enable_input_system()
        self.install_bridge()
        res = self.runner.run(["-projectPath", str(self.path)], "setup", method="GameAgent.Bridge.AgentBridge.Setup")
        if not res.ok:
            raise RuntimeError("Project setup failed:\n" + res.error_text())

    def _find_urp_template(self) -> Path | None:
        tdir = self.editor_data / "Resources" / "PackageManager" / "ProjectTemplates"
        if not tdir.exists():
            return None
        cands = [p for p in tdir.glob("*.tgz") if re.search(r"universal|urp", p.name, re.I)]
        cands.sort(key=lambda p: (("3d" not in p.name.lower()), "blank" in p.name.lower(), p.name))
        return cands[0] if cands else None

    def _builtin_version(self, package: str) -> str | None:
        pj = self.editor_data / "Resources" / "PackageManager" / "BuiltInPackages" / package / "package.json"
        if pj.exists():
            try:
                return json.loads(pj.read_text(encoding="utf-8"))["version"]
            except Exception:
                return None
        return None

    def _configure_packages(self) -> None:
        manifest_path = self.path / "Packages" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        deps: dict = manifest.setdefault("dependencies", {})
        wanted = dict(FALLBACK_PACKAGES)
        wanted["com.unity.cloud.gltfast"] = self.cfg.gltfast_version
        # Editor manifests describe the supported package floor for this exact Unity release.
        catalog_path = self.editor_data / "Resources/PackageManager/Editor/manifest.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8")).get("packages", {}) if catalog_path.exists() else {}
        def version(value: str) -> tuple[int, ...] | None:
            return tuple(map(int, value.split("."))) if re.fullmatch(r"\d+\.\d+\.\d+", value) else None
        for pkg, ver in wanted.items():
            info = catalog.get(pkg, {})
            minimum = info.get("minimumVersion", "")
            # The Editor catalog also lists Cinemachine 2.x; its floor doesn't protect 3.x.
            # Unity's 6000.5 package guide releases 3.1.7. Keep existing 2.x projects on 2.x.
            if pkg == "com.unity.cinemachine" and deps.get(pkg, ver).startswith("3."):
                if not version(minimum) or version(minimum) < (3, 1, 7):
                    minimum = "3.1.7"
            selected = self._builtin_version(pkg) or ver
            if version(minimum) and version(selected) and version(selected) < version(minimum):
                recommended = info.get("version", "")
                selected = recommended if version(recommended) and version(recommended) >= version(minimum) else minimum
            current = deps.get(pkg)
            if current is None:
                deps[pkg] = selected
            elif version(current) and version(minimum) and version(current) < version(minimum):
                console.print(f"[cyan]Updating {pkg}: {current} -> {selected} for this Unity Editor[/]")
                deps[pkg] = selected
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def _enable_input_system(self) -> None:
        ps = self.path / "ProjectSettings" / "ProjectSettings.asset"
        if ps.exists():
            text = ps.read_text(encoding="utf-8")
            text2 = re.sub(r"activeInputHandler: \d", "activeInputHandler: 2", text)  # 2 = Both
            if text2 != text:
                ps.write_text(text2, encoding="utf-8")

    def repair_package_compatibility(self) -> bool:
        manifest = self.path / "Packages/manifest.json"
        before = manifest.read_bytes()
        self._configure_packages()
        return manifest.read_bytes() != before

    def install_bridge(self) -> None:
        dst = self.path / BRIDGE_DST
        dst.mkdir(parents=True, exist_ok=True)
        for f in BRIDGE_SRC.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)
        self.install_starter()

    def install_starter(self) -> None:
        """Reusable, independently compiled campaign data/progress foundation."""
        dst = self.path / "Assets/LudoFabricStarter/Runtime"
        dst.mkdir(parents=True, exist_ok=True)
        for source in STARTER_SRC.iterdir():
            if source.is_file():
                target = dst / source.name
                if not target.exists() or target.read_bytes() != source.read_bytes():
                    shutil.copy2(source, target)

    # ------------------------------------------------------------------ file ops
    def safe_path(self, rel: str) -> Path:
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel.startswith("Assets/") or ".." in rel.split("/") or rel.startswith(("Assets/GameAgentBridge", "Assets/LudoFabricStarter")):
            raise ValueError(f"refusing to write outside Assets/ or into the bridge: {rel}")
        target = (self.path / rel).resolve()
        if not target.is_relative_to(self.path) or target.relative_to(self.path).as_posix().startswith(("Assets/GameAgentBridge", "Assets/LudoFabricStarter")):
            raise ValueError(f"refusing to write through an unsafe/protected asset path: {rel}")
        return target

    def write_file(self, rel: str, content: str) -> None:
        p = self.safe_path(rel)
        if p.exists() and p.read_text(encoding="utf-8") == content:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")

    def delete_file(self, rel: str) -> None:
        p = self.safe_path(rel)
        if p.exists():
            p.unlink()
            meta = p.with_name(p.name + ".meta")
            if meta.exists():
                meta.unlink()

    def read_file(self, rel: str) -> str | None:
        rel = rel.replace("\\", "/")
        if not rel.startswith("Assets/") or ".." in rel.split("/"):
            return None
        p = (self.path / rel).resolve()
        if not p.is_relative_to(self.path):
            return None
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None

    def code_files(self) -> list[str]:
        assets = self.path / "Assets"
        out = []
        for p in assets.rglob("*"):
            if p.is_file() and p.suffix.lower() in CODE_EXT:
                rel = p.relative_to(self.path).as_posix()
                if rel.startswith(("Assets/ThirdParty/", "Assets/GameAgentBridge/", "Assets/TextMesh Pro/",
                                   "Assets/Samples/", "Assets/TutorialInfo/")):
                    continue
                out.append(rel)
        return sorted(out)

    # ------------------------------------------------------------------ unity ops
    def _pp(self) -> list[str]:
        return ["-projectPath", str(self.path)]

    def compile(self) -> UnityResult:
        return self.runner.run(self._pp(), "compile", method="GameAgent.Bridge.AgentBridge.Ping")

    def run_builders(self, methods: list[str]) -> UnityResult:
        self._install_tmp_resources()
        return self.runner.run(self._pp(), "builders", nographics=False,
                               method="GameAgent.Bridge.AgentBridge.RunMethods",
                               extra={"-agentMethods": ";".join(methods)})

    def _install_tmp_resources(self) -> None:
        """Install the Editor's trusted TMP archive before Unity starts (ImportPackage is asynchronous)."""
        roots = [self.editor_data / "Resources/PackageManager/BuiltInPackages"]
        roots.append(self.path / "Library/PackageCache")
        archives = []
        for root in roots:
            if root.exists():
                archives.extend(root.glob("com.unity.ugui*/Package Resources/TMP Essential Resources.unitypackage"))
                archives.extend(root.glob("com.unity.textmeshpro*/Package Resources/TMP Essential Resources.unitypackage"))
        if not archives:
            raise RuntimeError("TextMesh Pro Essential Resources archive was not found in the installed Unity packages.")
        count = 0
        with tarfile.open(archives[0], "r:gz") as archive:
            members = {m.name: m for m in archive.getmembers()}
            for name, member in members.items():
                if not name.endswith("/pathname") or not member.isfile():
                    continue
                rel = archive.extractfile(member).read().decode("utf-8").strip().replace("\\", "/")
                if not (rel == "Assets/TextMesh Pro" or rel.startswith("Assets/TextMesh Pro/")):
                    raise ValueError(f"Unexpected TMP archive path: {rel}")
                target = (self.path / rel).resolve()
                if ".." in rel.split("/") or not target.is_relative_to(self.path):
                    raise ValueError(f"Unsafe TMP archive path: {rel}")
                prefix = name.rsplit("/", 1)[0]
                asset = members.get(prefix + "/asset")
                if asset and asset.isfile():
                    if target.exists():
                        continue  # Preserve user-created settings/fonts and their matching GUIDs.
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(asset).read())
                    count += 1
                else:
                    target.mkdir(parents=True, exist_ok=True)
                meta = members.get(prefix + "/asset.meta")
                meta_path = target.with_name(target.name + ".meta")
                if meta and meta.isfile() and not meta_path.exists():
                    meta_path.write_bytes(archive.extractfile(meta).read())
        if count:
            console.print(f"[cyan]Installed {count} TextMesh Pro resource files before launching Unity[/]")

    def screenshots(self, out_dir: Path) -> tuple[UnityResult, list[Path]]:
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)
        res = self.runner.run(self._pp(), "screenshots", nographics=False,
                              method="GameAgent.Bridge.AgentBridge.CaptureScreenshots",
                              extra={"-agentOut": str(out_dir)})
        return res, sorted(out_dir.glob("*.png"))

    def build(self, exe: Path) -> UnityResult:
        exe.parent.mkdir(parents=True, exist_ok=True)
        return self.runner.run(self._pp() + ["-buildTarget", "Win64"], "build", nographics=False,
                               method="GameAgent.Bridge.AgentBridge.Build",
                               extra={"-agentBuildPath": str(exe)})

    def run_tests(self, platform: str, test_filter: str | None = None) -> tuple[bool, str]:
        results = self.state_dir / "unity_logs" / f"tests_{platform}.xml"
        if results.exists():
            results.unlink()
        args = self._pp() + ["-runTests", "-testPlatform", platform, "-testResults", str(results)]
        if test_filter:
            args += ["-testFilter", test_filter]
        run = self.runner.run(args, f"tests_{platform}", quit=False, nographics=(platform == "EditMode"))
        if not results.exists():
            return False, "Unity produced no test results. " + run.error_text()
        try:
            xml = ET.parse(results).getroot()
        except ET.ParseError as exc:
            return False, f"Invalid Unity test report: {exc}"
        cases = xml.findall(".//test-case")
        failed = [c for c in cases if c.get("result") == "Failed"]
        if failed:
            return False, "\n".join(f"{c.get('fullname', c.get('name'))}: {c.findtext('failure/message', '')[:800]}" for c in failed)
        if not run.ok or xml.get("result") == "Failed" or not cases:
            return False, "Unity tests did not complete successfully. " + run.error_text()
        return True, f"{len(cases)} tests reported; {sum(c.get('result') == 'Passed' for c in cases)} passed"

    def smoke_run(self, exe: Path, seconds: int = 25) -> tuple[bool, str]:
        """Launches the built player windowed for a few seconds and scans its log for runtime exceptions."""
        log = self.state_dir / "unity_logs" / "player_smoke.log"
        if log.exists():
            log.unlink()
        proc = subprocess.Popen([str(exe), "-screen-fullscreen", "0", "-screen-width", "1280",
                                 "-screen-height", "720", "-logFile", str(log)])
        time.sleep(seconds)
        proc.kill()
        time.sleep(1)
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        blocks, lines = [], text.splitlines()
        for i, line in enumerate(lines):
            if re.search(r"\w+Exception\b|Error:|MissingReference|is missing", line) and "Fallback handler" not in line:
                block = "\n".join(lines[i:i + 6])
                if block not in blocks:
                    blocks.append(block)
        return (not blocks), "\n---\n".join(blocks[:15])

