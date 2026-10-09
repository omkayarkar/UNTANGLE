"""Core records and tag matching for UNTANGLE."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

TAG_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{1,8}[\s\-_/]*\d{1,8}(?:[\s\-_/]*[A-Z]{1,3})?)(?![A-Z0-9])",
    re.IGNORECASE,
)
VALID_NORMALIZED_TAG = re.compile(r"^[A-Z]{1,8}\d{1,8}[A-Z]{0,3}$")


def normalize_tag(value: object) -> str:
    """Return a case- and separator-insensitive tag key."""
    if value is None:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(value).strip().upper())


def is_valid_tag(value: object) -> bool:
    return bool(VALID_NORMALIZED_TAG.fullmatch(normalize_tag(value)))


@dataclass(frozen=True)
class TagOccurrence:
    original: str
    normalized: str
    page: int
    bbox: tuple[float, float, float, float]
    source: str
    confidence: float | None = None
    context: str = ""


@dataclass(frozen=True)
class LineSegment:
    page: int
    x1: float
    y1: float
    x2: float
    y2: float
    path_id: int

    @property
    def length(self) -> float:
        return ((self.x2 - self.x1) ** 2 + (self.y2 - self.y1) ** 2) ** 0.5

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (
            min(self.x1, self.x2),
            min(self.y1, self.y2),
            max(self.x1, self.x2),
            max(self.y1, self.y2),
        )


@dataclass(frozen=True)
class Equipment:
    tag: str
    normalized: str
    kind: str
    page: int
    bbox: tuple[float, float, float, float]
    evidence: str
    confidence: str
    context: str = ""
    symbol_bbox: tuple[float, float, float, float] | None = None


@dataclass
class DocumentAnalysis:
    file_name: str
    page_count: int
    tags: list[TagOccurrence] = field(default_factory=list)
    equipment: list[Equipment] = field(default_factory=list)
    segments: list[LineSegment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    page_sizes: dict[int, tuple[float, float]] = field(default_factory=dict)


@dataclass
class ProcessGraph:
    graph: object
    connections: list[dict[str, object]]
    crossovers: list[dict[str, object]]
    uncertain_junctions: list[dict[str, object]]
    warnings: list[str]
    pump_pairs: list[dict[str, object]]


def find_tag_strings(text: str) -> list[str]:
    """Extract plausible instrument/equipment tags without restricting prefixes."""
    return [match.group(1).strip() for match in TAG_RE.finditer(text.upper())]


def build_tag_report(
    io_values: Iterable[object], detections: Iterable[TagOccurrence]
) -> list[dict[str, object]]:
    """Match every spreadsheet row to PDF evidence, keeping duplicate rows visible."""
    occurrences = list(detections)
    counts: dict[str, int] = {}
    for value in io_values:
        key = normalize_tag(value)
        if key:
            counts[key] = counts.get(key, 0) + 1

    rows: list[dict[str, object]] = []
    for row_number, value in enumerate(io_values, start=2):
        original = "" if value is None else str(value).strip()
        normalized = normalize_tag(original)
        matches = [item for item in occurrences if item.normalized == normalized]
        duplicate_input = bool(normalized) and counts.get(normalized, 0) > 1
        valid = is_valid_tag(original)

        if not valid:
            status = "Invalid input tag"
        else:
            labels: list[str] = []
            if duplicate_input:
                labels.append("Duplicate in I/O list")
            if not matches:
                labels.append("Missing from P&ID")
            elif len(matches) > 1:
                labels.append("Ambiguous PDF detections")
            else:
                labels.append("Found")
            status = "; ".join(labels)

        location = "; ".join(
            f"page {item.page} ({item.bbox[0]:.0f}, {item.bbox[1]:.0f})"
            for item in matches
        )
        sources = sorted({item.source for item in matches})
        evidence = (
            "OCR candidate — verify"
            if "OCR" in sources
            else "Selectable PDF text"
            if matches
            else "No PDF evidence"
        )
        rows.append(
            {
                "row": row_number,
                "original_tag": original,
                "normalized_tag": normalized,
                "detection_status": status,
                "source_page_location": location or "—",
                "associated_equipment": "—",
                "evidence_status": evidence,
                "notes": (
                    "Tag appears more than once in the I/O list."
                    if duplicate_input
                    else "OCR text requires manual confirmation."
                    if "OCR" in sources
                    else ""
                ),
            }
        )
    return rows


def associate_equipment(
    report_rows: list[dict[str, object]], detections: list[TagOccurrence],
    equipment: list[Equipment], max_distance: float = 100.0,
) -> None:
    """Add a nearby-label hint; this is not treated as process connectivity."""
    confirmed = [asset for asset in equipment if asset.confidence == "label-confirmed"]
    for row in report_rows:
        normalized = str(row["normalized_tag"])
        matched = [item for item in detections if item.normalized == normalized]
        names: set[str] = set()
        for detection in matched:
            x = (detection.bbox[0] + detection.bbox[2]) / 2
            y = (detection.bbox[1] + detection.bbox[3]) / 2
            nearby = []
            for asset in confirmed:
                if asset.page != detection.page:
                    continue
                ax = (asset.bbox[0] + asset.bbox[2]) / 2
                ay = (asset.bbox[1] + asset.bbox[3]) / 2
                distance = ((x - ax) ** 2 + (y - ay) ** 2) ** 0.5
                if distance <= max_distance and asset.normalized != normalized:
                    nearby.append((distance, asset))
            if nearby:
                nearest = min(distance for distance, _ in nearby)
                names.update(
                    asset.tag for distance, asset in nearby
                    if abs(distance - nearest) < 1.0
                )
        if len(names) == 1:
            row["associated_equipment"] = (
                f"{next(iter(names))} (nearby label; connectivity unverified)"
            )
        elif len(names) > 1:
            row["associated_equipment"] = "Ambiguous nearby equipment labels"
