"""APAAR registry adapter (FR-40).

The national APAAR registry has no public API, so this prototype ships only a **MOCK**
implementation backed by the synthetic `mock_apaar_registry` table (filled by the data
generator). A real client would implement the same `ApaarClient` interface; nothing else in
the code base talks to the registry directly.

MOCK limits: IDs are 12 digits with a synthetic "99" prefix; the real APAAR structure and
checksum are not modelled.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import MockApaarRegistry

APAAR_ID_RE = re.compile(r"^\d{12}$")


def is_valid_apaar_format(apaar_id: str | None) -> bool:
    return bool(apaar_id) and bool(APAAR_ID_RE.match(apaar_id))


@dataclass(frozen=True)
class ApaarRecord:
    apaar_id: str
    full_name: str
    dob: date
    gender: str
    source: str  # "MOCK" for the mock registry


class ApaarClient(ABC):
    """Look up a person in the APAAR registry."""

    @abstractmethod
    def lookup(self, apaar_id: str) -> ApaarRecord | None: ...

    def lookup_many(self, apaar_ids: Iterable[str]) -> dict[str, ApaarRecord]:
        out = {}
        for apaar_id in apaar_ids:
            rec = self.lookup(apaar_id)
            if rec is not None:
                out[apaar_id] = rec
        return out


class MockApaarClient(ApaarClient):
    """MOCK registry: synthetic records in the local database."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _to_record(row: MockApaarRegistry) -> ApaarRecord:
        return ApaarRecord(
            apaar_id=row.apaar_id,
            full_name=row.full_name,
            dob=row.dob,
            gender=row.gender.value if hasattr(row.gender, "value") else str(row.gender),
            source="MOCK",
        )

    def lookup(self, apaar_id: str) -> ApaarRecord | None:
        if not is_valid_apaar_format(apaar_id):
            return None
        row = self.db.get(MockApaarRegistry, apaar_id)
        return self._to_record(row) if row else None

    def lookup_many(self, apaar_ids: Iterable[str]) -> dict[str, ApaarRecord]:
        ids = sorted({i for i in apaar_ids if is_valid_apaar_format(i)})
        if not ids:
            return {}
        rows = self.db.scalars(
            select(MockApaarRegistry).where(MockApaarRegistry.apaar_id.in_(ids))
        ).all()
        return {r.apaar_id: self._to_record(r) for r in rows}


def get_apaar_client(db: Session) -> ApaarClient:
    if settings.apaar_client == "mock":
        return MockApaarClient(db)
    raise RuntimeError(f"Unknown APAAR client {settings.apaar_client!r}; only 'mock' exists")
