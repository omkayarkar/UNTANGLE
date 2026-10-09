"""Conservative process-graph reconstruction from detected labels and vectors."""

from __future__ import annotations

import itertools
import math
import re
from collections import defaultdict
from typing import Iterable

import networkx as nx

try:
    from .untangle_core import (
        DocumentAnalysis,
        Equipment,
        LineSegment,
        ProcessGraph,
        is_valid_tag,
        normalize_tag,
    )
except ImportError:
    from untangle_core import (
        DocumentAnalysis,
        Equipment,
        LineSegment,
        ProcessGraph,
        is_valid_tag,
        normalize_tag,
    )


ENDPOINT_TOLERANCE = 2.0
MAX_INTERSECTION_SCAN_SEGMENTS = 3000


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _intersection(a: LineSegment, b: LineSegment) -> tuple[float, float, float, float] | None:
    rx, ry = a.x2 - a.x1, a.y2 - a.y1
    sx, sy = b.x2 - b.x1, b.y2 - b.y1
    denominator = _cross(rx, ry, sx, sy)
    if abs(denominator) < 1e-8:
        return None
    qpx, qpy = b.x1 - a.x1, b.y1 - a.y1
    t = _cross(qpx, qpy, sx, sy) / denominator
    u = _cross(qpx, qpy, rx, ry) / denominator
    if -1e-8 <= t <= 1 + 1e-8 and -1e-8 <= u <= 1 + 1e-8:
        return a.x1 + t * rx, a.y1 + t * ry, t, u
    return None


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, first: int, second: int) -> None:
        a, b = self.find(first), self.find(second)
        if a != b:
            self.parent[b] = a


def _endpoint_components(segments: list[LineSegment]) -> _UnionFind:
    union_find = _UnionFind(len(segments))
    buckets: dict[tuple[int, int, int], list[tuple[int, float, float]]] = defaultdict(list)
    tol = ENDPOINT_TOLERANCE
    for index, segment in enumerate(segments):
        for x, y in ((segment.x1, segment.y1), (segment.x2, segment.y2)):
            cell_x, cell_y = round(x / tol), round(y / tol)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other, ox, oy in buckets[(segment.page, cell_x + dx, cell_y + dy)]:
                        if math.hypot(x - ox, y - oy) <= tol:
                            union_find.union(index, other)
            buckets[(segment.page, cell_x, cell_y)].append((index, x, y))
    return union_find


def _rect_for_equipment(asset: Equipment) -> tuple[float, float, float, float]:
    return asset.symbol_bbox or asset.bbox


def _expanded(rect: tuple[float, float, float, float], amount: float) -> tuple[float, float, float, float]:
    return rect[0] - amount, rect[1] - amount, rect[2] + amount, rect[3] + amount


def _line_hits_rect(segment: LineSegment, rect: tuple[float, float, float, float]) -> bool:
    """Liang–Barsky segment/rectangle test."""
    x0, y0, x1, y1 = segment.x1, segment.y1, segment.x2, segment.y2
    left, top, right, bottom = rect
    dx, dy = x1 - x0, y1 - y0
    low, high = 0.0, 1.0
    for p, q in (
        (-dx, x0 - left),
        (dx, right - x0),
        (-dy, y0 - top),
        (dy, bottom - y0),
    ):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        ratio = q / p
        if p < 0:
            low = max(low, ratio)
        else:
            high = min(high, ratio)
        if low > high:
            return False
    return True


def _find_line_intersections(
    segments: list[LineSegment],
) -> tuple[list[dict[str, object]], list[dict[str, object]], bool]:
    crossovers: list[dict[str, object]] = []
    junctions: list[dict[str, object]] = []
    if len(segments) > MAX_INTERSECTION_SCAN_SEGMENTS:
        return crossovers, junctions, False
    for first, second in itertools.combinations(range(len(segments)), 2):
        a, b = segments[first], segments[second]
        if a.page != b.page:
            continue
        bounds_a, bounds_b = a.bounds, b.bounds
        if (
            bounds_a[2] < bounds_b[0] - ENDPOINT_TOLERANCE
            or bounds_b[2] < bounds_a[0] - ENDPOINT_TOLERANCE
            or bounds_a[3] < bounds_b[1] - ENDPOINT_TOLERANCE
            or bounds_b[3] < bounds_a[1] - ENDPOINT_TOLERANCE
        ):
            continue
        result = _intersection(a, b)
        if result is None:
            continue
        x, y, t, u = result
        t_end = min(abs(t), abs(1 - t)) <= 1e-5
        u_end = min(abs(u), abs(1 - u)) <= 1e-5
        if not t_end and not u_end:
            crossovers.append(
                {
                    "page": a.page,
                    "location": f"({x:.1f}, {y:.1f})",
                    "status": "Crossing without explicit junction evidence",
                    "action": "Not connected; review the source drawing.",
                }
            )
        elif t_end != u_end:
            junctions.append(
                {
                    "page": a.page,
                    "location": f"({x:.1f}, {y:.1f})",
                    "status": "Endpoint-to-line junction",
                    "action": "Connection is uncertain without a junction marker.",
                }
            )
    return crossovers, junctions, True


def _pair_pumps(
    assets: list[Equipment], graph: nx.Graph
) -> list[dict[str, object]]:
    grouped: dict[str, list[Equipment]] = defaultdict(list)
    for asset in assets:
        if asset.kind != "Pump":
            continue
        normalized = asset.normalized
        base = re.sub(r"[A-Z]$", "", normalized)
        if base != normalized:
            grouped[base].append(asset)

    pairs = []
    for base, group in grouped.items():
        for first, second in itertools.combinations(group, 2):
            if first.normalized == second.normalized:
                continue
            first_role = (
                "duty"
                if re.search(r"\bDUTY\b", first.context, re.IGNORECASE)
                else "standby"
                if re.search(r"\bSTAND(?:[\s-]*BY)?\b", first.context, re.IGNORECASE)
                else "unknown"
            )
            second_role = (
                "duty"
                if re.search(r"\bDUTY\b", second.context, re.IGNORECASE)
                else "standby"
                if re.search(r"\bSTAND(?:[\s-]*BY)?\b", second.context, re.IGNORECASE)
                else "unknown"
            )
            first_nodes = [
                node for node, data in graph.nodes(data=True)
                if data.get("normalized") == first.normalized
            ]
            second_nodes = [
                node for node, data in graph.nodes(data=True)
                if data.get("normalized") == second.normalized
            ]
            first_neighbors = set().union(
                *(set(graph.neighbors(node)) for node in first_nodes)
            ) if first_nodes else set()
            second_neighbors = set().union(
                *(set(graph.neighbors(node)) for node in second_nodes)
            ) if second_nodes else set()
            common = first_neighbors & second_neighbors - set(first_nodes) - set(second_nodes)
            role_confirmed = {first_role, second_role} == {"duty", "standby"}
            topology_confirmed = len(common) >= 2
            verified = role_confirmed and topology_confirmed
            pairs.append(
                {
                    "tags": [first.tag, second.tag],
                    "base_tag": base,
                    "classification": (
                        "Verified duty/standby pair — shared connection topology observed"
                        if verified
                        else "Possible pump pair — role or shared topology needs review"
                    ),
                    "roles": {first.tag: first_role, second.tag: second_role},
                    "verified": verified,
                    "topology_verified": topology_confirmed,
                    "shared_topology_entities": [
                        graph.nodes[node].get("tag", node) for node in sorted(common)
                    ],
                    "page": first.page if first.page == second.page else None,
                    "evidence": (
                        "Explicit opposite role labels, matching equipment tag stem, "
                        "and at least two shared vector-connected entities."
                        if verified
                        else (
                            "Matching equipment tag stem; "
                            + (
                                "explicit role labels found, but shared vector topology is not verified."
                                if role_confirmed
                                else "explicit opposite duty/standby role labels were not both found."
                            )
                        )
                    ),
                }
            )
    return pairs


def build_process_graph(
    io_values: Iterable[object], document: DocumentAnalysis
) -> ProcessGraph:
    """Build only line-continuity connections supported by vector evidence."""
    graph = nx.Graph()
    io_tags: dict[str, str] = {}
    for value in io_values:
        original = "" if value is None else str(value).strip()
        normalized = normalize_tag(original)
        if is_valid_tag(original):
            io_tags.setdefault(normalized, original)

    occurrences: dict[str, list[object]] = defaultdict(list)
    for item in document.tags:
        occurrences[item.normalized].append(item)
    asset_by_tag: dict[str, Equipment] = {}
    for asset in document.equipment:
        prior = asset_by_tag.get(asset.normalized)
        if prior is None or (
            asset.confidence == "label-confirmed"
            and prior.confidence != "label-confirmed"
        ):
            asset_by_tag[asset.normalized] = asset

    anchors: dict[str, list[tuple[int, tuple[float, float, float, float]]]] = {}
    for normalized, original in io_tags.items():
        key = f"IO:{normalized}"
        matches = occurrences.get(normalized, [])
        asset = asset_by_tag.get(normalized)
        is_valve = bool(asset and "valve" in asset.kind.lower())
        is_equipment = bool(
            asset
            and asset.confidence == "label-confirmed"
            and not is_valve
        )
        graph.add_node(
            key,
            tag=original,
            normalized=normalized,
            kind="valve" if is_valve else "equipment" if is_equipment else "instrument",
            equipment_type=asset.kind if asset else None,
            status="detected" if matches else "unresolved",
            evidence="; ".join(sorted({match.source for match in matches}))
            if matches
            else "No P&ID tag detection",
        )
        if asset and (is_valve or is_equipment):
            anchors[key] = [(asset.page, asset.symbol_bbox or asset.bbox)]
        else:
            anchors[key] = [(item.page, item.bbox) for item in matches]

    for normalized, asset in asset_by_tag.items():
        if normalized in io_tags:
            continue
        key = f"EQ:{normalized}"
        graph.add_node(
            key,
            tag=asset.tag,
            normalized=normalized,
            kind="equipment",
            equipment_type=asset.kind,
            status=asset.confidence,
            evidence=asset.evidence,
        )
        anchor = asset.symbol_bbox or asset.bbox
        anchors[key] = [(asset.page, anchor)]

    crossovers: list[dict[str, object]] = []
    junctions: list[dict[str, object]] = []
    warnings: list[str] = []
    connection_evidence: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)

    segments_by_page: dict[int, list[LineSegment]] = defaultdict(list)
    for segment in document.segments:
        segments_by_page[segment.page].append(segment)

    for page_number, segments in segments_by_page.items():
        page_crossovers, page_junctions, intersection_scan_complete = _find_line_intersections(segments)
        crossovers.extend(page_crossovers)
        junctions.extend(page_junctions)
        if not intersection_scan_complete:
            warnings.append(
                f"Page {page_number}: {len(segments)} vector segments exceed the "
                f"{MAX_INTERSECTION_SCAN_SEGMENTS}-segment intersection scan limit; "
                "crossovers and uncertain junctions on this page were not exhaustively checked."
            )

        union_find = _endpoint_components(segments)
        components: dict[int, list[LineSegment]] = defaultdict(list)
        for index, segment in enumerate(segments):
            components[union_find.find(index)].append(segment)

        for component in components.values():
            touched: set[str] = set()
            for node_key, node_anchors in anchors.items():
                for anchor_page, bbox in node_anchors:
                    if anchor_page != page_number:
                        continue
                    if any(
                        _line_hits_rect(segment, _expanded(bbox, 1.5))
                        for segment in component
                    ):
                        touched.add(node_key)
                        break
            if len(touched) < 2:
                continue
            for first, second in itertools.combinations(sorted(touched), 2):
                connection_evidence[(first, second)].append(
                    {
                        "page": page_number,
                        "evidence": "Continuous vector line segments with endpoint contact",
                        "flow_direction": "unresolved — no unambiguous arrow evidence",
                    }
                )

    connections: list[dict[str, object]] = []
    for (first, second), evidence in connection_evidence.items():
        graph.add_edge(
            first,
            second,
            evidence=evidence,
            connection_status="vector-supported; direction unresolved",
        )
        connections.append(
            {
                "from": graph.nodes[first]["tag"],
                "to": graph.nodes[second]["tag"],
                "page": ", ".join(str(item["page"]) for item in evidence),
                "connection_status": "Supported vector continuity",
                "flow_direction": "Unresolved",
                "evidence": "Continuous vector line segments; no arrow direction asserted",
            }
        )

    if graph.number_of_nodes() and not connections:
        warnings.append(
            "No entity-to-entity process connections met the vector-continuity evidence threshold."
        )
    components = list(nx.connected_components(graph)) if graph.number_of_nodes() else []
    isolated = [next(iter(group)) for group in components if len(group) == 1]
    if isolated:
        warnings.append(
            f"{len(isolated)} detected or unresolved entity node(s) have no verified process connection."
        )
    if crossovers:
        warnings.append(
            f"{len(crossovers)} line crossing(s) were not treated as connected junctions."
        )
    if junctions:
        warnings.append(
            f"{len(junctions)} endpoint-to-line junction(s) require review before being treated as connected."
        )

    return ProcessGraph(
        graph=graph,
        connections=connections,
        crossovers=crossovers,
        uncertain_junctions=junctions,
        warnings=warnings,
        pump_pairs=_pair_pumps(list(asset_by_tag.values()), graph),
    )


def connection_validation_rows(process: ProcessGraph) -> list[dict[str, object]]:
    rows = list(process.connections)
    for node, data in process.graph.nodes(data=True):
        if process.graph.degree(node) == 0:
            rows.append(
                {
                    "from": data.get("tag", node),
                    "to": "—",
                    "page": "—",
                    "connection_status": "Disconnected / unresolved entity",
                    "flow_direction": "Not evaluated",
                    "evidence": (
                        f"{data.get('tag', node)} has no supported vector connection."
                    ),
                }
            )
    for crossover in process.crossovers:
        rows.append(
            {
                "from": "—",
                "to": "—",
                "page": crossover["page"],
                "connection_status": "Unresolved crossover",
                "flow_direction": "Not evaluated",
                "evidence": (
                    f"{crossover['location']}: crossing is not connected without "
                    "explicit junction evidence."
                ),
            }
        )
    for junction in process.uncertain_junctions:
        rows.append(
            {
                "from": "—",
                "to": "—",
                "page": junction["page"],
                "connection_status": "Uncertain branch/junction",
                "flow_direction": "Not evaluated",
                "evidence": (
                    f"{junction['location']}: endpoint meets a line; review for "
                    "a junction marker."
                ),
            }
        )
    return rows
