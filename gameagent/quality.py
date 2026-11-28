"""Quality intelligence: deterministic screenshot checks and evidence-based maturity levels.

Deterministic checks run before any vision-model call. They catch defects that need no model
(black/blank frames, washed-out or unreadable output, identical frames) and convert them into the
existing Review/ReviewIssue format so the normal corrective-task flow handles them.
"""
from __future__ import annotations

from pathlib import Path

from .schemas import Review, ReviewIssue

MATURITY_LEVELS = ["none", "technical_prototype", "playable_vertical_slice", "alpha", "beta", "release_candidate"]


def image_stats(path: Path) -> dict:
    from PIL import Image, ImageStat
    with Image.open(path) as img:
        gray = img.convert("L").resize((160, 90))
        stat = ImageStat.Stat(gray)
        hist = gray.histogram()
        distinct = sum(1 for h in hist if h > 0)
        edges = sum(abs(gray.getpixel((x, y)) - gray.getpixel((x + 1, y)))
                    for y in range(0, 90, 3) for x in range(0, 159, 2)) / (30 * 80)
        thumb = gray.resize((8, 8))
        signature = tuple(thumb.get_flattened_data() if hasattr(thumb, 'get_flattened_data') else thumb.getdata())
        return {"mean": round(stat.mean[0], 2), "stddev": round(stat.stddev[0], 2),
                "distinct_levels": distinct, "edge_energy": round(edges, 2),
                "signature": signature}


def check_screenshots(shots: list[Path]) -> tuple[list[dict], list[ReviewIssue]]:
    """Returns per-shot statistics and blocking issues. Thresholds are deliberately conservative:
    they flag frames no player could use, not artistic choices."""
    stats, issues = [], []
    for shot in shots:
        s = image_stats(shot)
        s["file"] = shot.name
        stats.append(s)
        if s["stddev"] < 4 and s["distinct_levels"] < 12:
            tone = "black" if s["mean"] < 20 else "white" if s["mean"] > 235 else "a single flat colour"
            issues.append(ReviewIssue(severity="critical", area="graphics",
                problem=f"{shot.name} is {tone} (no visible scene content: mean {s['mean']}, stddev {s['stddev']}).",
                fix="Ensure the scene has an enabled Camera tagged MainCamera pointed at the play area, visible "
                    "lights, and renderers with assigned URP materials; verify the builder saves the scene."))
        elif s["mean"] < 18:
            issues.append(ReviewIssue(severity="major", area="graphics",
                problem=f"{shot.name} is nearly black (mean luminance {s['mean']}).",
                fix="Add or strengthen the key directional light / ambient lighting and check post-exposure."))
        elif s["mean"] > 240:
            issues.append(ReviewIssue(severity="major", area="graphics",
                problem=f"{shot.name} is overexposed (mean luminance {s['mean']}).",
                fix="Reduce light intensity / exposure and avoid unlit white materials filling the view."))
    if len(stats) > 1 and len({s["signature"] for s in stats}) == 1 and not issues:
        issues.append(ReviewIssue(severity="major", area="level",
            problem="All captured views are identical; scenes or cameras do not differ.",
            fix="Make sure each scene in the build list is distinct and the capture camera is placed per scene."))
    for s in stats:
        s.pop("signature", None)
    return stats, issues


def deterministic_review(shots: list[Path]) -> tuple[list[dict], Review | None]:
    """A Review when deterministic checks found blocking defects (vision call can be skipped), else None."""
    stats, issues = check_screenshots(shots)
    if not issues:
        return stats, None
    return stats, Review(score=2 if any(i.severity == "critical" for i in issues) else 4,
                         summary="Deterministic visual gate failed before model review.", issues=issues)


def assess_maturity(phase: str, evidence: dict) -> dict:
    """Assign the highest level whose criteria are *all* backed by recorded evidence.

    evidence keys (all optional, recorded by the pipeline):
      compiled, builders_ok, visual_gate_ok, review_score, tests_EditMode, tests_PlayMode,
      build_ok, smoke_ok, stages_designed
    """
    e = evidence
    criteria = {
        "technical_prototype": [("compiles cleanly", e.get("compiled")), ("scene builders succeeded", e.get("builders_ok"))],
        "playable_vertical_slice": [("deterministic visual gate passed", e.get("visual_gate_ok")),
                                    ("Edit Mode tests passed", e.get("tests_EditMode")),
                                    ("Play Mode tests passed", e.get("tests_PlayMode")),
                                    ("player build produced", e.get("build_ok")),
                                    ("smoke run without runtime errors",
                                     e.get("smoke_ok") if e.get("smoke_applicable", True) else True)],
        "alpha": [("visual review score >= 7", (e.get("review_score") or 0) >= 7),
                  ("multiple designed stages", (e.get("stages_designed") or 0) >= 2)],
        "beta": [("visual review score >= 8", (e.get("review_score") or 0) >= 8),
                 ("human playtest recorded", e.get("human_playtest"))],
        "release_candidate": [("visual review score >= 9", (e.get("review_score") or 0) >= 9),
                              ("target-device performance measured", e.get("performance_ok"))],
    }
    level, missing = "none", []
    for name in MATURITY_LEVELS[1:]:
        unmet = [label for label, ok in criteria[name] if not ok]
        if unmet:
            missing = unmet
            break
        level = name
    return {"level": level, "next_level_missing": missing, "phase": phase}
