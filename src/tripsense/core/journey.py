from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .models import RoutePlan


class JourneyStore:
    """Small persistent journey log for the MVP.

    Planning remains stateless; saving a plan creates a journey that can record
    completed stops and actual dwell time without requiring an account system.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS journeys (
                    id TEXT PRIMARY KEY,
                    city TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    scene TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS journey_stops (
                    journey_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    poi_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    actual_dwell_minutes REAL,
                    note TEXT,
                    PRIMARY KEY (journey_id, position),
                    FOREIGN KEY (journey_id) REFERENCES journeys(id)
                );
                CREATE TABLE IF NOT EXISTS journey_records (
                    id TEXT PRIMARY KEY,
                    journey_id TEXT NOT NULL,
                    day_index INTEGER NOT NULL DEFAULT 1,
                    stop_position INTEGER,
                    place_name TEXT NOT NULL,
                    mood TEXT,
                    note TEXT,
                    photo_refs_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (journey_id) REFERENCES journeys(id) ON DELETE CASCADE,
                    FOREIGN KEY (journey_id, stop_position)
                        REFERENCES journey_stops(journey_id, position)
                );
                CREATE INDEX IF NOT EXISTS idx_journey_records_journey
                    ON journey_records(journey_id, day_index, created_at);
                CREATE TABLE IF NOT EXISTS journey_diaries (
                    id TEXT PRIMARY KEY,
                    journey_id TEXT NOT NULL,
                    source_record_count INTEGER NOT NULL,
                    content_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (journey_id) REFERENCES journeys(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_journey_diaries_journey
                    ON journey_diaries(journey_id, created_at);
                """
            )

    def save(self, plan: RoutePlan) -> str:
        journey_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO journeys
                   (id, city, mode, scene, status, plan_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'planned', ?, ?, ?)""",
                (
                    journey_id,
                    plan.city,
                    plan.mode,
                    plan.scene,
                    json.dumps(plan.to_dict(), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            connection.executemany(
                """INSERT INTO journey_stops
                   (journey_id, position, poi_id, name, completed)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (journey_id, index, stop.poi_id, stop.name, int(stop.completed))
                    for index, stop in enumerate(plan.stops, start=1)
                ],
            )
        return journey_id

    def list(self) -> list[dict]:
        """Return saved journeys in switcher order with lightweight activity counts."""
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT j.*,
                       (SELECT COUNT(*) FROM journey_stops s
                        WHERE s.journey_id = j.id) AS stop_count,
                       (SELECT COUNT(*) FROM journey_records r
                        WHERE r.journey_id = j.id) AS record_count,
                       (SELECT COUNT(*) FROM journey_diaries d
                        WHERE d.journey_id = j.id) AS diary_count
                FROM journeys j
                ORDER BY j.updated_at DESC, j.created_at DESC
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "city": row["city"],
                "mode": row["mode"],
                "scene": row["scene"],
                "status": row["status"],
                "stop_count": row["stop_count"],
                "record_count": row["record_count"],
                "diary_count": row["diary_count"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def add_record(
        self,
        journey_id: str,
        *,
        day_index: int = 1,
        stop_position: int | None = None,
        place_name: str | None = None,
        mood: str | None = None,
        note: str | None = None,
        photo_refs: list[str] | None = None,
    ) -> dict:
        """Save what actually happened, including places outside the planned route."""
        if day_index < 1:
            raise ValueError("day_index must be at least 1")
        photos = [str(item).strip() for item in (photo_refs or []) if str(item).strip()]
        mood = mood.strip() if mood else None
        note = note.strip() if note else None
        place_name = place_name.strip() if place_name else None
        if not any((mood, note, photos)):
            raise ValueError("a journey record needs a mood, note or photo")

        record_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            journey = connection.execute(
                "SELECT id FROM journeys WHERE id = ?", (journey_id,)
            ).fetchone()
            if journey is None:
                raise KeyError(f"journey not found: {journey_id}")
            if stop_position is not None:
                stop = connection.execute(
                    """SELECT name FROM journey_stops
                       WHERE journey_id = ? AND position = ?""",
                    (journey_id, stop_position),
                ).fetchone()
                if stop is None:
                    raise KeyError(f"stop not found: {journey_id}/{stop_position}")
                place_name = place_name or str(stop["name"])
            if not place_name:
                raise ValueError("place_name is required for an unplanned place")

            connection.execute(
                """INSERT INTO journey_records
                   (id, journey_id, day_index, stop_position, place_name, mood,
                    note, photo_refs_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    journey_id,
                    day_index,
                    stop_position,
                    place_name,
                    mood,
                    note,
                    json.dumps(photos, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                "UPDATE journeys SET updated_at = ? WHERE id = ?", (now, journey_id)
            )
        return self.get_record(record_id)

    def get_record(self, record_id: str) -> dict:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM journey_records WHERE id = ?", (record_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"journey record not found: {record_id}")
        return self._record_dict(row)

    def list_records(self, journey_id: str) -> list[dict]:
        self.get(journey_id)
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """SELECT * FROM journey_records WHERE journey_id = ?
                   ORDER BY day_index, created_at""",
                (journey_id,),
            ).fetchall()
        return [self._record_dict(row) for row in rows]

    @staticmethod
    def _record_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "journey_id": row["journey_id"],
            "day_index": row["day_index"],
            "stop_position": row["stop_position"],
            "place_name": row["place_name"],
            "mood": row["mood"],
            "note": row["note"],
            "photo_refs": json.loads(row["photo_refs_json"]),
            "created_at": row["created_at"],
        }

    def generate_diary(
        self,
        journey_id: str,
        *,
        llm_provider=None,
    ) -> dict:
        """Build a diary from a saved route and records; optionally rewrite with LLM."""
        journey = self.get(journey_id)
        records = self.list_records(journey_id)
        if not records:
            raise ValueError("at least one journey record is required to generate a diary")

        city_name = {"shanghai": "上海", "beijing": "北京"}.get(
            journey["city"], journey["city"]
        )
        days: list[dict] = []
        for day_index in sorted({record["day_index"] for record in records}):
            entries = [record for record in records if record["day_index"] == day_index]
            days.append(
                {
                    "day_index": day_index,
                    "title": f"在{entries[0]['place_name']}留下的一天",
                    "narrative": "",
                    "entries": entries,
                }
            )

        photo_count = sum(len(record["photo_refs"]) for record in records)
        moods = list(dict.fromkeys(record["mood"] for record in records if record["mood"]))
        content = {
            "journey_id": journey_id,
            "title": f"{city_name} · {journey['scene']}旅行日记",
            "subtitle": (
                f"{len(days)} 天 · {len(records)} 条记录 · {photo_count} 张照片"
            ),
            "route": [stop["name"] for stop in journey["stops"]],
            "moods": moods,
            "source_record_count": len(records),
            "days": days,
            "generated_by": "local-template-v1",
        }
        if llm_provider is not None:
            content = self._maybe_rewrite_diary(content, llm_provider)

        diary_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        result = {"id": diary_id, "created_at": now, **content}
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO journey_diaries
                   (id, journey_id, source_record_count, content_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    diary_id,
                    journey_id,
                    len(records),
                    json.dumps(content, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                "UPDATE journeys SET updated_at = ? WHERE id = ?", (now, journey_id)
            )
        return result

    @staticmethod
    def _maybe_rewrite_diary(content: dict, llm_provider) -> dict:
        try:
            rewrite = llm_provider.rewrite_diary(content)
        except (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError):
            return content

        rewritten = dict(content)
        rewritten["title"] = rewrite.title or content["title"]
        rewritten["subtitle"] = rewrite.subtitle or content["subtitle"]
        by_day = {item.day_index: item for item in rewrite.days}
        new_days = []
        for day in content["days"]:
            updated = dict(day)
            item = by_day.get(day["day_index"])
            if item is not None:
                if item.title:
                    updated["title"] = item.title
                if item.narrative:
                    updated["narrative"] = item.narrative
            new_days.append(updated)
        rewritten["days"] = new_days
        rewritten["generated_by"] = (
            f"llm:{getattr(llm_provider, 'provider_name', 'unknown')}"
            f"/{getattr(llm_provider, 'model_name', 'unknown')}"
        )
        return rewritten

    def latest_diary(self, journey_id: str) -> dict:
        self.get(journey_id)
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """SELECT * FROM journey_diaries WHERE journey_id = ?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (journey_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"travel diary not found for journey: {journey_id}")
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            **json.loads(row["content_json"]),
        }

    def update_stop(
        self,
        journey_id: str,
        position: int,
        *,
        completed: bool | None = None,
        actual_dwell_minutes: float | None = None,
        note: str | None = None,
    ) -> dict:
        updates: list[str] = []
        values: list[object] = []
        if completed is not None:
            updates.append("completed = ?")
            values.append(int(completed))
        if actual_dwell_minutes is not None:
            if actual_dwell_minutes < 0:
                raise ValueError("actual_dwell_minutes cannot be negative")
            updates.append("actual_dwell_minutes = ?")
            values.append(float(actual_dwell_minutes))
        if note is not None:
            updates.append("note = ?")
            values.append(note)
        if not updates:
            return self.get(journey_id)

        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"UPDATE journey_stops SET {', '.join(updates)} "
                "WHERE journey_id = ? AND position = ?",
                [*values, journey_id, position],
            )
            if cursor.rowcount != 1:
                raise KeyError(f"stop not found: {journey_id}/{position}")
            now = datetime.now(timezone.utc).isoformat()
            remaining = connection.execute(
                "SELECT COUNT(*) FROM journey_stops WHERE journey_id = ? AND completed = 0",
                (journey_id,),
            ).fetchone()[0]
            status = "completed" if remaining == 0 else "in_progress"
            connection.execute(
                "UPDATE journeys SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, journey_id),
            )
        return self.get(journey_id)

    def get(self, journey_id: str) -> dict:
        with closing(self._connect()) as connection, connection:
            journey = connection.execute(
                "SELECT * FROM journeys WHERE id = ?", (journey_id,)
            ).fetchone()
            if journey is None:
                raise KeyError(f"journey not found: {journey_id}")
            stops = connection.execute(
                "SELECT * FROM journey_stops WHERE journey_id = ? ORDER BY position",
                (journey_id,),
            ).fetchall()
        return {
            "id": journey["id"],
            "city": journey["city"],
            "mode": journey["mode"],
            "scene": journey["scene"],
            "status": journey["status"],
            "created_at": journey["created_at"],
            "updated_at": journey["updated_at"],
            "plan": json.loads(journey["plan_json"]),
            "stops": [
                {
                    "position": row["position"],
                    "poi_id": row["poi_id"],
                    "name": row["name"],
                    "completed": bool(row["completed"]),
                    "actual_dwell_minutes": row["actual_dwell_minutes"],
                    "note": row["note"],
                }
                for row in stops
            ],
        }
