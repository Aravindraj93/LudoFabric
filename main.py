"""LudoFabric CLI.

Examples:
  python main.py run "a cozy low-poly island exploration game with fishing" --project C:/UnityProjects/CozyIsland
  python main.py resume --project C:/UnityProjects/CozyIsland
  python main.py design "souls-like boss fight in a ruined cathedral"      # intent + plan only, no Unity needed
  python main.py find-asset "wooden barrel" --kind model                  # test asset search
"""
from __future__ import annotations

import argparse
from pathlib import Path


from gameagent.config import Config

from gameagent.ui import console


def main() -> None:
    ap = argparse.ArgumentParser(description="LudoFabric ? AI game studio for Unity")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="build a game from a request")
    r.add_argument("request")
    r.add_argument("--project", required=True, type=Path, help="Unity project folder (created if missing)")

    rs = sub.add_parser("resume", help="continue an interrupted run")
    rs.add_argument("--project", required=True, type=Path)

    d = sub.add_parser("design", help="dry run: intent -> design -> plan (no Unity)")
    d.add_argument("request")
    d.add_argument("--out", type=Path, default=Path("design_out"))

    f = sub.add_parser("find-asset", help="search free asset libraries")
    f.add_argument("query")
    f.add_argument("--kind", choices=["model", "hdri", "texture"], default="model")

    w = sub.add_parser("web", help="open the local web dashboard (LudoFabric Studio)")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--no-browser", action="store_true")

    args = ap.parse_args()
    cfg = Config()

    if args.cmd == "web":
        import threading
        import webbrowser

        import uvicorn
        url = f"http://127.0.0.1:{args.port}"
        console.print(f"LudoFabric Studio running at {url}  (local only - press Ctrl+C to stop)")
        if not args.no_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        uvicorn.run("webapp.server:app", host="127.0.0.1", port=args.port, log_level="warning")
        return

    if args.cmd in ("run", "resume"):
        cfg.validate(need_unity=True)
        from gameagent.orchestrator import Orchestrator
        Orchestrator(cfg, args.project).run(getattr(args, "request", None))

    elif args.cmd == "design":
        cfg.validate(need_unity=False)
        from gameagent.agents import Studio
        from gameagent.llm import LLM
        args.out.mkdir(parents=True, exist_ok=True)
        llm = LLM(cfg)
        llm.set_log_dir(args.out / "llm_logs")
        studio = Studio(cfg, llm)
        design = studio.design(args.request)
        (args.out / "GDD.json").write_text(design.model_dump_json(indent=2), encoding="utf-8")
        plan = studio.plan(design)
        (args.out / "PLAN.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"[green]Design and plan written to {args.out.resolve()}[/]")

    elif args.cmd == "find-asset":
        import httpx
        from gameagent.assets.polyhaven import PolyHaven
        from gameagent.assets.sketchfab import Sketchfab
        with httpx.Client(timeout=60) as client:
            for asset_id, info, score in PolyHaven(client).search(args.kind, args.query):
                console.print(f"polyhaven  {asset_id:30} {info.get('name', '')}  (score {score:.1f})")
            sf = Sketchfab(client, cfg.sketchfab_token, cfg.max_faces)
            if sf.enabled and args.kind == "model":
                for m in sf.search(args.query):
                    console.print(f"sketchfab  {m['uid']:30} {m.get('name', '')}")


if __name__ == "__main__":
    main()

