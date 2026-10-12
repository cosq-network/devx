"""Minimal team/organization layer for devx (prototype).

This module is the seed of the paid "team" tier described in
``docs/team-layer.md``. It coordinates a small amount of shared state through a
JSON manifest (``.devx/team.json``) that can live in a network-shared
directory. There is intentionally **no server component yet**: every operation
is local and can be exercised offline.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TEAM_CONFIG_FILENAME = "team.json"


@dataclass
class TeamManifest:
    """Portable description of a team workspace."""

    team_id: str
    name: str
    shared_session_db: str
    members: list[str] = field(default_factory=list)
    created_at: str = ""

    @classmethod
    def create(
        cls,
        name: str,
        shared_session_db: str | Path | None = None,
        workspace: Path | None = None,
    ) -> "TeamManifest":
        """Create a new manifest; the team ID is generated locally."""
        clean = name.strip()
        if not clean:
            raise ValueError("Team name must not be empty")
        root = Path(workspace or Path.cwd()).expanduser().resolve()
        db = Path(shared_session_db).expanduser() if shared_session_db else root / ".devx" / "sessions.db"
        return cls(
            team_id=str(uuid.uuid4()),
            name=clean,
            shared_session_db=str(db),
            members=[],
            created_at=datetime.now(UTC).isoformat(),
        )

    def add_member(self, member: str) -> bool:
        """Add a member identifier, returning ``False`` if already present."""
        clean = member.strip()
        if not clean:
            raise ValueError("Member identifier must not be empty")
        if clean in self.members:
            return False
        self.members.append(clean)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "name": self.name,
            "shared_session_db": self.shared_session_db,
            "members": list(self.members),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TeamManifest":
        if not isinstance(data, dict):
            raise TypeError("Team manifest must be an object")
        team_id = data.get("team_id")
        name = data.get("name")
        if not team_id or not name:
            raise ValueError("Team manifest requires 'team_id' and 'name'")
        members = data.get("members", [])
        if not isinstance(members, list):
            raise TypeError("Team manifest 'members' must be a list")
        return cls(
            team_id=str(team_id),
            name=str(name),
            shared_session_db=str(data.get("shared_session_db", "")),
            members=[str(item) for item in members],
            created_at=str(data.get("created_at", "")),
        )


def team_config_path(workspace: Path | str | None = None) -> Path:
    """Location of the team manifest for a workspace."""
    root = Path(workspace or Path.cwd()).expanduser()
    return root / ".devx" / TEAM_CONFIG_FILENAME


def save_team(workspace: Path | str | None, manifest: TeamManifest) -> Path:
    path = team_config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    return path


def load_team(workspace: Path | str | None = None) -> TeamManifest | None:
    """Load the team manifest, or ``None`` when the workspace has no team."""
    path = team_config_path(workspace)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid team manifest {path}: {exc}") from exc
    return TeamManifest.from_dict(data)


def create_team(
    workspace: Path | str | None,
    name: str,
    shared_session_db: str | Path | None = None,
) -> TeamManifest:
    """Create and persist a new team manifest."""
    manifest = TeamManifest.create(name, shared_session_db=shared_session_db, workspace=_as_path(workspace))
    save_team(workspace, manifest)
    return manifest


def collect_team_metrics(manifest: TeamManifest) -> dict[str, Any]:
    """Aggregate local usage counters for a team's shared session database.

    Metrics are intentionally cheap and derived from existing SQLite data:
    session count, event count, and dead-letter-queue depth. There is no token
    or billing data yet.
    """
    if not manifest.shared_session_db:
        raise ValueError("Team manifest has no shared_session_db configured")

    from devx.harness.session import SessionStore

    store = SessionStore(Path(manifest.shared_session_db))
    sessions = store.list_sessions()
    event_count = 0
    for item in sessions:
        event_count += len(store.events(item["id"]))
    return {
        "team_id": manifest.team_id,
        "team_name": manifest.name,
        "members": len(manifest.members),
        "sessions": len(sessions),
        "events": event_count,
        "dlq_entries": len(store.get_dlq_entries()),
        "shared_session_db": manifest.shared_session_db,
    }


def _as_path(workspace: Path | str | None) -> Path | None:
    return Path(workspace) if workspace is not None else None
