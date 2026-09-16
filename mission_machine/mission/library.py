"""Locating the bundled synthetic missions."""

from __future__ import annotations

from pathlib import Path

from mission_machine.mission.spec import MissionSpec

#: Repository-relative data directory (works from a source checkout).
DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
MISSION_DIR = DATA_ROOT / "missions"

DEFAULT_MISSION_ID = "MM-DEMO-001"


def mission_path(mission_id: str = DEFAULT_MISSION_ID) -> Path:
    return MISSION_DIR / f"{mission_id.lower()}.json"


def list_missions() -> list[str]:
    if not MISSION_DIR.exists():
        return []
    return sorted(p.stem.upper() for p in MISSION_DIR.glob("*.json"))


def load_mission(mission_id: str = DEFAULT_MISSION_ID) -> MissionSpec:
    """Load a bundled synthetic mission by id (e.g. ``MM-DEMO-001``)."""

    path = mission_path(mission_id)
    if not path.exists():
        available = ", ".join(list_missions()) or "none"
        raise FileNotFoundError(f"no such mission: {mission_id} (available: {available})")
    return MissionSpec.from_file(path)
