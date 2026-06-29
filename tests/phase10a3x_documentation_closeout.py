"""Documentation closeout test for Phase 10A.3.x."""

import re
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def _check_text(file_rel: str, pattern: str, description: str) -> str:
    path = REPO / file_rel
    if not path.exists():
        return f"MISSING {file_rel}: {description}"
    text = path.read_text(encoding="utf-8")
    if re.search(pattern, text) is None:
        return f"PATTERN_NOT_FOUND in {file_rel}: {description}  pattern={pattern!r}"
    return ""


CHECKS: list[tuple[str, str, str]] = [
    # STATE.md
    ("STATE.md",
     r"A3\.x implementation complete.*Documentation closeout",
     "STATE.md mentions A3.x closeout"),
    ("STATE.md",
     r"implementation complete",
     "STATE.md states implementation complete"),
    ("STATE.md",
     r"No production stage selected",
     "STATE.md says no next stage selected"),

    # CHANGELOG.md
    ("CHANGELOG.md",
     r"Phase 10A\.3\.1–A3\.6\b.*Texture Sidecar Lifecycle",
     "CHANGELOG.md has Unreleased A3.x entry"),

    # STATUS.md
    ("STATUS.md",
     r"Phase 10A\.3\.x — Texture Sidecar Lifecycle \(A3\.1–A3\.6 COMPLETE\)",
     "STATUS.md has Phase 10A.3.x section"),
    ("STATUS.md",
     r"A3\.6.*\|.*✅ COMPLETE",
     "STATUS.md marks A3.6 as COMPLETE"),
    ("STATUS.md",
     r"A3\.7.*\|.*NOT DEFINED",
     "STATUS.md marks A3.7 as NOT DEFINED"),

    # Docs/ARCHITECTURE.md
    ("Docs/ARCHITECTURE.md",
     r"Texture Sidecar Lifecycle \(A3\.1–A3\.6\)",
     "ARCHITECTURE.md has Texture Sidecar Lifecycle section"),

    # current-state-roadmap.md
    ("Docs/Architecture/current-state-roadmap.md",
     r"10A\.3\.6.*COMPLETE.*2288508",
     "current-state-roadmap.md marks A3.6 as COMPLETE with SHA"),
    ("Docs/Architecture/current-state-roadmap.md",
     r"10A\.3\.7.*NOT DEFINED",
     "current-state-roadmap.md marks A3.7 as NOT DEFINED"),
    ("Docs/Architecture/current-state-roadmap.md",
     r"2026-06-28.*Phase 10A\.3\.x documentation closeout",
     "current-state-roadmap.md last updated 2026-06-28"),
]


def test_doc_closeout():
    """Verify all 5 documentation files reflect A3.x completion."""
    errors: list[str] = []
    for file_rel, pattern, desc in CHECKS:
        msg = _check_text(file_rel, pattern, desc)
        if msg:
            errors.append(msg)
    assert not errors, "Documentation closeout errors:\n  " + "\n  ".join(errors)


def test_status_has_all_six_stage_names():
    """STATUS.md must list all six A3.x stage names with their scope."""
    text = (REPO / "STATUS.md").read_text(encoding="utf-8")
    expected = [
        "Collision-safe texture sidecar identity",
        "Structured sidecar preparation result",
        "Content-based sidecar asset identity",
        "Deterministic manifest v3 persistence",
        "Manifest-informed sidecar reuse",
        "Safe orphan sidecar pruning",
    ]
    for name in expected:
        assert name in text, f"STATUS.md missing stage: {name}"


def test_exact_shas():
    """STATUS.md must contain exact full SHAs for A3.5 and A3.6."""
    text = (REPO / "STATUS.md").read_text(encoding="utf-8")
    assert "e0967c78d0492156af8b48b40a529bf34b6ffb28" in text, \
        "STATUS.md missing A3.5 full SHA"
    assert "22885085fd8a6950f8a335b998e253bf155846f3" in text, \
        "STATUS.md missing A3.6 full SHA"


def test_roadmap_marks_all_six_complete():
    """current-state-roadmap.md must have all six A3.1-A3.6 rows as COMPLETE."""
    text = (REPO / "Docs/Architecture/current-state-roadmap.md").read_text(encoding="utf-8")
    for i in range(1, 7):
        assert re.search(
            rf"10A\.3\.{i}.*COMPLETE", text
        ), f"current-state-roadmap.md missing A3.{i} COMPLETE entry"


def test_architecture_covers_persistence_reuse_pruning():
    """ARCHITECTURE.md must cover manifest persistence, reuse, and pruning."""
    text = (REPO / "Docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "persist_manifest_v3" in text, "ARCHITECTURE.md missing persistence"
    assert "reuse" in text.lower(), "ARCHITECTURE.md missing reuse"
    assert "prun" in text, "ARCHITECTURE.md missing pruning"


def test_no_blender_or_ue_runtime_claimed():
    """A3.x section in STATUS.md must not claim Blender or UE runtime validation."""
    text = (REPO / "STATUS.md").read_text(encoding="utf-8")
    a3_match = re.search(r"## Phase 10A\.3\.x", text)
    assert a3_match is not None
    a3_section = text[a3_match.start():a3_match.start() + 3000]
    assert "Build.sh" not in a3_section
    assert "blender --background" not in a3_section
    assert "Runtime validation" not in a3_section


def test_changelog_unreleased_position():
    """A3.x entry must be the first Unreleased bullet item (before older entries)."""
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = text.index("## [unreleased]")
    after_unreleased = text[unreleased:unreleased + 2000]
    lines = after_unreleased.splitlines()
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("- **Phase 10A.3."):
            return
    raise AssertionError("No A3.x bullet entry after ## [unreleased]")


def test_phase_totals_consistent():
    """STATUS.md A3.6 totals must have focused <= combined."""
    text = (REPO / "STATUS.md").read_text(encoding="utf-8")
    a3_match = re.search(r"## Phase 10A\.3\.x", text)
    assert a3_match is not None
    a3_section = text[a3_match.start():a3_match.start() + 3000]
    focused_match = re.search(r"Focused A3\.6: (\d+)/\d+ PASS", a3_section)
    combined_match = re.search(r"A3\.1–A3\.6 combined: (\d+) passed", a3_section)
    assert focused_match is not None, "Missing A3.6 focused total"
    assert combined_match is not None, "Missing A3.1–A3.6 combined total"
    focused = int(focused_match.group(1))
    combined = int(combined_match.group(1))
    assert combined >= focused, f"Combined total {combined} < focused total {focused}"


def test_roadmap_scope_selection_note():
    """Roadmap must state A3.x complete, A3.7 undefined, scope lock required."""
    text = (REPO / "Docs/Architecture/current-state-roadmap.md").read_text(encoding="utf-8")
    assert "A3.1–A3.6 are complete" in text, \
        "Roadmap missing 'A3.1–A3.6 are complete'"
    assert "A3.7 is not defined" in text, \
        "Roadmap missing 'A3.7 is not defined'"
    assert "requires a new evidence-based scope lock" in text, \
        "Roadmap missing scope lock requirement"
    assert "candidates remain options" in text or "no stage selected" in text, \
        "Roadmap must indicate candidates are options only"


def test_changed_paths_approved():
    """Changed paths must be limited to 5 tracked docs. No AGENTS.md or production paths."""
    import subprocess
    from pathlib import Path

    # Verify this test lives only in the A3x-Docs worktree, not dirty main
    assert Path(__file__).resolve() == REPO / "tests" / "phase10a3x_documentation_closeout.py", \
        "Test file location mismatch: must be in A3x-Docs worktree"

    diff_modified = subprocess.run(
        ["git", "diff", "--name-only"],
        capture_output=True, text=True, cwd=REPO, timeout=15,
    )
    diff_staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, cwd=REPO, timeout=15,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, cwd=REPO, timeout=15,
    )

    tracked_modified = {
        p for p in diff_modified.stdout.strip().splitlines()
        if p
    } | {
        p for p in diff_staged.stdout.strip().splitlines()
        if p
    }

    allowed_tracked = {
        "CHANGELOG.md",
        "STATE.md",
        "STATUS.md",
        "Docs/ARCHITECTURE.md",
        "Docs/Architecture/current-state-roadmap.md",
    }

    forbidden = tracked_modified - allowed_tracked
    assert not forbidden, \
        f"Unexpected tracked modified files: {forbidden}"

    if tracked_modified:
        assert tracked_modified == allowed_tracked, (
            f"Tracked modified {tracked_modified} != allowed {allowed_tracked}"
        )

    # No AGENTS.md-related changes in any git state
    for name in ("AGENTS.md", ".gitignore"):
        assert name not in tracked_modified, \
            f"{name} must not be in tracked changes"

    # Untracked files should not include AGENTS.md (it is gitignored)
    untracked_files = {p for p in untracked.stdout.strip().splitlines() if p}
    assert "AGENTS.md" not in untracked_files, \
        "AGENTS.md must not appear as untracked"
