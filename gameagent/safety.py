"""Static safety policy for AI-generated C#/shader code, applied before anything is written or executed.

Generated Editor code runs on the user's machine with the user's permissions, so code that launches
processes, opens network sockets from the Editor, touches the registry, loads native or dynamic code, or
deletes outside the project is rejected. The rejection message goes back to the model, which regenerates.

This is a guardrail, not a sandbox: a determined obfuscation can evade static checks. Use a dedicated
project folder or a VM for untrusted prompts. CODE_POLICY: strict (default) | warn | off.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

_STRIP = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)  # ignore comments (strings are kept: paths matter)


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern
    reason: str
    editor_only: bool = False  # True: only forbidden inside Editor code


RULES = [
    Rule("process", re.compile(r"\bSystem\.Diagnostics\.Process\b|\bProcess\s*\.\s*Start\s*\(|\bnew\s+ProcessStartInfo\b"),
         "launches external processes"),
    Rule("shell", re.compile(r"\b(cmd\.exe|powershell|/bin/(ba)?sh|bash\s+-c)\b", re.I), "invokes a shell"),
    Rule("registry", re.compile(r"\bMicrosoft\.Win32\b|\bRegistry(Key)?\s*\."), "modifies the Windows registry"),
    Rule("native", re.compile(r"\[\s*DllImport\b|\bextern\s+static\b|\bstatic\s+extern\b|\bMarshal\.GetDelegateForFunctionPointer\b"),
         "calls native code"),
    Rule("dynamic_code", re.compile(r"\bAssembly\s*\.\s*(Load|LoadFrom|LoadFile|UnsafeLoadFrom)\s*\(|\bCSharpCodeProvider\b|\bCompileAssemblyFromSource\b|\bReflection\.Emit\b"),
         "loads or compiles code at run time"),
    Rule("editor_network", re.compile(r"\b(HttpClient|WebClient|TcpClient|UdpClient|Socket|UnityWebRequest|WebRequest)\b"),
         "performs network access from Editor code", editor_only=True),
    Rule("absolute_delete", re.compile(r"\b(File|Directory|FileUtil)\s*\.\s*(Delete\w*|Move\w*)\s*\(\s*@?\"([A-Za-z]:|/|\\\\|~)"),
         "deletes or moves files by absolute path"),
    Rule("parent_escape", re.compile(r"\b(File|Directory|FileUtil)\s*\.\s*\w+\s*\([^;]*\"[^\"\n]*\.\.[\\/]"),
         "accesses files outside the project via '..'"),
    Rule("user_dirs", re.compile(r"Environment\.GetFolderPath\s*\(\s*Environment\.SpecialFolder\.(?!(LocalApplicationData|ApplicationData)\b)"),
         "accesses user folders outside Unity's data paths"),
    Rule("exit_editor", re.compile(r"\bEditorApplication\.Exit\s*\(|\bEnvironment\.Exit\s*\("), "terminates the Editor process"),
]

SCANNED = (".cs", ".shader", ".hlsl", ".cginc", ".asmdef")


def mode() -> str:
    value = os.getenv("CODE_POLICY", "strict").strip().lower()
    return value if value in ("strict", "warn", "off") else "strict"


def is_editor_path(path: str) -> bool:
    return "/Editor/" in "/" + path.replace("\\", "/") + "/"


def scan(path: str, source: str) -> list[str]:
    if not path.lower().endswith(SCANNED):
        return []
    code = _STRIP.sub("", source)
    editor = is_editor_path(path) or "using UnityEditor" in code
    found = []
    for rule in RULES:
        if rule.editor_only and not editor:
            continue
        m = rule.pattern.search(code)
        if m:
            found.append(f"{path}: {rule.reason} ({rule.id}: '{m.group(0)[:60]}')")
    if path.endswith(".asmdef") and re.search(r'"allowUnsafeCode"\s*:\s*true', source):
        found.append(f"{path}: enables unsafe code (asmdef allowUnsafeCode)")
    return found


def check_files(files: dict[str, str]) -> list[str]:
    """Raises ValueError (strict) with every violation; returns warnings otherwise."""
    if mode() == "off":
        return []
    problems = [p for path, src in files.items() for p in scan(path, src)]
    if problems and mode() == "strict":
        raise ValueError("Generated code violates the LudoFabric safety policy and was not written:\n- "
                         + "\n- ".join(problems)
                         + "\nRewrite without these APIs (games may use Application.persistentDataPath and "
                           "UnityWebRequest in runtime code; Editor code must stay inside the project).")
    return problems
