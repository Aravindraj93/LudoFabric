"""Target platforms: what the user can choose, how each is built, and what each requires.

Build support for each platform is a separate Unity Hub module. Missing modules are detected before a build,
so a missing module is reported precisely instead of failing deep inside Unity.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    build_target: str      # Unity -buildTarget
    module: str            # folder under <Editor>/Data/PlaybackEngines
    mobile: bool
    runnable_here: bool    # can be smoke-run on this Windows host
    note: str = ""

    def artifact(self, project: Path, title: str) -> Path:
        name = re.sub(r"[^A-Za-z0-9]+", "", title) or "Game"
        if self.id == "windows":
            return project / "Build" / f"{name}.exe"  # unchanged location for existing projects
        root = project / "Build" / self.label.replace(" ", "")
        if self.id == "android":
            return root / f"{name}.{'aab' if os.getenv('LF_ANDROID_FORMAT', 'apk').lower() == 'aab' else 'apk'}"
        if self.id == "macos":
            return root / f"{name}.app"
        if self.id == "linux":
            return root / f"{name}.x86_64"
        return root / name  # ios: Xcode project folder; webgl: site folder


PLATFORMS: dict[str, Platform] = {p.id: p for p in [
    Platform("windows", "Windows", "Win64", "WindowsStandaloneSupport", False, os.name == "nt"),
    Platform("android", "Android", "Android", "AndroidPlayer", True, False,
             "APK by default (LF_ANDROID_FORMAT=aab for Play Store bundles). Debug-signed unless LF_ANDROID_KEYSTORE is set."),
    Platform("ios", "iOS", "iOS", "iOSSupport", True, False,
             "Unity produces an Xcode project. Compiling, signing and installing it requires macOS, Xcode and an Apple developer account."),
    Platform("macos", "macOS", "OSXUniversal", "MacStandaloneSupport", False, False,
             "Building on Windows uses the Mono backend; notarization requires macOS."),
    Platform("linux", "Linux", "Linux64", "LinuxStandaloneSupport", False, False),
    Platform("webgl", "WebGL", "WebGL", "WebGLSupport", False, False, "Serve the output folder over HTTP to play."),
]}


def normalize(targets) -> list[str]:
    if isinstance(targets, str):
        targets = [t for t in re.split(r"[,\s]+", targets) if t]
    if not targets:
        return ["windows"]
    aliases = {"win": "windows", "pc": "windows", "apk": "android", "aab": "android", "iphone": "ios",
               "mac": "macos", "osx": "macos", "web": "webgl", "all": "all"}
    out = []
    for t in targets:
        t = aliases.get(t.strip().lower(), t.strip().lower())
        if t == "all":
            return list(PLATFORMS)
        if t not in PLATFORMS:
            raise ValueError(f"Unknown platform '{t}'. Choose from: {', '.join(PLATFORMS)}, all")
        if t not in out:
            out.append(t)
    return out


def module_installed(unity_editor: str, platform: str) -> bool | None:
    """True/False when it can be determined from the Editor installation; None when Unity is not configured."""
    if not unity_editor or not Path(unity_editor).exists():
        return None
    engines = Path(unity_editor).parent / "Data" / "PlaybackEngines"
    if not engines.exists():
        return None
    wanted = PLATFORMS[platform].module.lower()
    if platform == "windows" and os.name == "nt":
        return True  # the Windows Editor always includes Windows standalone support
    return any(p.name.lower() == wanted for p in engines.iterdir())


def app_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", (title or "game").lower()) or "game"
    if slug[0].isdigit():
        slug = "g" + slug
    return os.getenv("LF_APP_ID_PREFIX", "com.ludofabric") + "." + slug


def design_brief(targets: list[str]) -> str:
    labels = ", ".join(PLATFORMS[t].label for t in targets)
    lines = [f"\n\nTARGET PLATFORMS (chosen by the user; design and code must work on ALL of them): {labels}."]
    if any(PLATFORMS[t].mobile for t in targets):
        lines.append("- Mobile: every control must be playable with touch (on-screen joystick/buttons or gestures); "
                     "keep keyboard/mouse working for desktop targets and Editor testing. Use the Input System "
                     "(EnhancedTouch / on-screen controls) rather than legacy Input. Canvas Scaler 'Scale With Screen "
                     "Size' with safe-area padding; readable UI at phone size; target 60 FPS on mid-range phones; "
                     "modest draw calls, texture sizes and lights; no Windows-only APIs or file paths.")
    if "webgl" in targets:
        lines.append("- WebGL: no threads, no System.IO outside Application.persistentDataPath, keep memory small.")
    if len(targets) > 1:
        lines.append("- Use Application.platform / Input System device checks only where behaviour must differ.")
    return "\n".join(lines)
