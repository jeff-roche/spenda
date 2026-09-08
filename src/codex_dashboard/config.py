from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    codex_home: Path
    database: Path
    keep_preview: bool = True
    preview_chars: int = 240
    running_window_seconds: int = 120

    def validate(self) -> "Settings":
        """Reject configurations that could write into Codex-owned state."""
        codex_home = self.codex_home.resolve()
        database = self.database.resolve()
        try:
            database.relative_to(codex_home)
        except ValueError:
            return self
        raise ValueError(f"dashboard database must be outside CODEX_HOME: {database}")

    @classmethod
    def load(
        cls,
        codex_home: str | Path | None = None,
        database: str | Path | None = None,
        keep_preview: bool = True,
    ) -> "Settings":
        home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        db = Path(
            database
            or os.environ.get("CODEX_DASHBOARD_DB")
            or data_home / "codex-usage-dashboard" / "dashboard.sqlite"
        ).expanduser()
        return cls(home.resolve(), db.resolve(), keep_preview).validate()
