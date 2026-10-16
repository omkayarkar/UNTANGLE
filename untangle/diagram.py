"""Matplotlib diagram rendering and genuine SVG, PNG, and PDF export."""

from __future__ import annotations

import io
import math
import re
from collections import defaultdict
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch

try:
    from .untangle_core import DocumentAnalysis, normalize_tag
    from .graph_model import ProcessGraph
except ImportError:
    from untangle_core import DocumentAnalysis, normalize_tag
    from graph_model import ProcessGraph


NAVY = "#10243A"
TEAL = "#087F8C"
MUTED = "#536579"
LINE = "#8CA0AF"
PALE = "#EDF4F5"
AMBER = "#B85C18"
RED = "#A43C43"


def _node_location_map(document: DocumentAnalysis) -> dict[str, tuple[int, float, float, float]]:
    locations: dict[str, tuple[int, float, float, float]] = {}
    for item in document.tags:
        size = document.page_sizes.get(item.page, (1.0, 1.0))
        x, y = (item.bbox[0] + item.bbox[2]) / 2, (item.bbox[1] + item.bbox[3]) / 2
        key = item.normalized
        candidate = (item.page, x, y, size[1])
        current = locations.get(key)
        if current is None or (item.page, x, y) < current[:3]:
            locations[key] = candidate
    for asset in document.equipment:
        key = asset.normalized
        if key not in locations:
            bounds = asset.symbol_bbox or asset.bbox
            x, y = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
            size = document.page_sizes.get(asset.page, (1.0, 1.0))
            locations[key] = (asset.page, x, y, size[1])
    return locations


def _build_positions(
    process: ProcessGraph, report_rows: list[dict[str, Any]], document: DocumentAnalysis
) -> dict[str, tuple[float, float]]:
    graph = process.graph
    locations = _node_location_map(document)
    normalized_order = []
    for row in report_rows:
        key = str(row["normalized_tag"])
        if key and key not in normalized_order:
            normalized_order.append(key)

    records = []
    for node, data in graph.nodes(data=True):
        normalized = str(data.get("normalized", ""))
        location = locations.get(normalized)
        if location:
            page, x, y, page_height = location
            page_width = document.page_sizes.get(page, (page_height, page_height))[0]
            source_x = (page - 1) + x / max(1.0, page_width)
            source_y = y / max(1.0, page_height)
            records.append((node, source_x, source_y, page, True))
        else:
            fallback_index = normalized_order.index(normalized) if normalized in normalized_order else len(normalized_order)
            records.append((node, None, fallback_index, 10**6, False))

    records.sort(
        key=lambda item: (
            0 if item[4] else 1,
            item[1] if item[1] is not None else item[2],
            item[2],
            str(item[0]),
        )
    )
    max_source_x = max((item[1] for item in records if item[1] is not None), default=0.0)
    missing_index = 0
    positioned: dict[str, tuple[float, float]] = {}
    for node, source_x, source_y, page, has_source in records:
        if has_source:
            x = float(source_x) * 5.0
            y = -float(source_y) * 4.2
        else:
            # Place undetected input tags in a generated review lane after
            # source-derived positions; no drawing coordinates are fabricated.
            x = (max_source_x + 1.0 + (missing_index % 3) * 0.22) * 5.0
            y = -5.4 - (missing_index // 3) * 1.0
            missing_index += 1
        positioned[node] = (x, y)

    # A verified pump pair shares a dynamic column and parallel y offsets.
    for pair in process.pump_pairs:
        if not pair["verified"]:
            continue
        nodes = [
            f"EQ:{normalize_tag(tag)}"
            for tag in pair["tags"]
            if f"EQ:{normalize_tag(tag)}" in positioned
        ]
        if len(nodes) == 2:
            x = sum(positioned[node][0] for node in nodes) / 2
            ordered = sorted(nodes, key=lambda node: positioned[node][1], reverse=True)
            positioned[ordered[0]] = (x, -0.8)
            positioned[ordered[1]] = (x, -2.1)
    return positioned


def render_exports(
    document: DocumentAnalysis,
    report_rows: list[dict[str, Any]],
    process: ProcessGraph,
    project_name: str,
) -> dict[str, bytes]:
    """Render one latest-analysis drawing and return actual file bytes."""
    graph = process.graph
    positions = _build_positions(process, report_rows, document)
    row_status = {str(row["normalized_tag"]): str(row["detection_status"]) for row in report_rows}
    node_count = max(1, graph.number_of_nodes())
    max_x = max((value[0] for value in positions.values()), default=6.0)
    min_x = min((value[0] for value in positions.values()), default=0.0)
    max_y = max((value[1] for value in positions.values()), default=0.0)
    min_y = min((value[1] for value in positions.values()), default=-2.0)
    width = min(20.0, max(9.2, (max_x - min_x + 4.5) * 0.72))
    height = min(13.5, max(5.5, (max_y - min_y + 3.8) * 0.82))
    fig, ax = plt.subplots(figsize=(width, height))
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#FFFFFF")

# Draw supported connections as orthogonal pipe routes.
# Keep connections behind equipment and instrument nodes.
    for first, second, data in graph.edges(data=True):
        x1, y1 = positions[first]
        x2, y2 = positions[second]

    # Route horizontally first, then vertically.
        mid_x = (x1 + x2) / 2

        ax.plot(
            [x1, mid_x, mid_x, x2],
            [y1, y1, y2, y2],
            color=LINE,
            linewidth=1.8,
            solid_capstyle="round",
            zorder=1,
        )
    # Small terminal markers make connection endpoints easier to see.
        ax.add_patch(
            Circle(
                (x1, y1),
                0.045,
                facecolor=LINE,
                edgecolor=LINE,
                linewidth=0.5,
                zorder=2,
            )
        )
        ax.add_patch(
            Circle(
                (x2, y2),
                0.045,
                facecolor=LINE,
                edgecolor=LINE,
                linewidth=0.5,
                zorder=2,
            )
        )

    for node, data in graph.nodes(data=True):
        x, y = positions[node]
        tag = str(data.get("tag", node))
        normalized = str(data.get("normalized", ""))
        kind = str(data.get("kind", "instrument"))
        status = row_status.get(normalized, "")
        needs_review = any(
            token in status for token in ("Missing", "Ambiguous", "Duplicate", "Invalid")
        )
        unresolved = data.get("status") == "unresolved" or needs_review
        candidate = data.get("status") == "candidate"
        color = RED if unresolved else AMBER if candidate else TEAL if kind == "instrument" else NAVY
        linestyle = "--" if unresolved or candidate else "-"

        if kind == "equipment":
            patch = FancyBboxPatch(
                (x - 0.78, y - 0.34),
                1.56,
                0.68,
                boxstyle="round,pad=0.06,rounding_size=0.12",
                facecolor="#FFFFFF" if not unresolved else "#FFF8F6",
                edgecolor=color,
                linewidth=1.7,
                linestyle=linestyle,
                zorder=3,
            )
            ax.add_patch(patch)
            equipment_type = str(data.get("equipment_type", "Equipment"))
            ax.text(
                x,
                y,
                f"{tag}\n{equipment_type}",
                ha="center",
                va="center",
                color=NAVY,
                fontsize=8.2,
                zorder=4,
            )
        else:
            ax.add_patch(
                Circle(
                    (x, y),
                    0.30,
                    facecolor=PALE if not unresolved else "#FFF8F6",
                    edgecolor=color,
                    linewidth=1.7,
                    linestyle=linestyle,
                    zorder=3,
                )
            )
            ax.text(x, y, tag, ha="center", va="center", color=NAVY, fontsize=8.0, zorder=4)

    for pair in process.pump_pairs:
        if pair["verified"]:
            tags = " / ".join(str(tag) for tag in pair["tags"])
            matching = [
                positions[f"EQ:{normalize_tag(tag)}"]
                for tag in pair["tags"]
                if f"EQ:{normalize_tag(tag)}" in positions
            ]
            if matching:
                ax.text(
                    sum(point[0] for point in matching) / len(matching),
                    min(point[1] for point in matching) - 0.56,
                    f"DUTY / STANDBY · {tags}",
                    color=TEAL,
                    fontsize=7.2,
                    ha="center",
                    va="top",
                    fontweight="bold",
                )

    ax.set_title(project_name or "UNTANGLE · Simplified P&ID", loc="left", color=NAVY, fontsize=16, fontweight="bold", pad=20)
    ax.text(
        0,
        1.015,
        "Source positions retained where detected · Supported vector connections only · Flow direction is not inferred",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=8.4,
        va="bottom",
    )
    ax.text(
        0,
        -0.075,
        f"Source: {document.file_name} · {document.page_count} page(s) · "
        f"{len(report_rows)} I/O row(s) · {len(process.connections)} supported connection(s)",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=7.5,
        va="top",
    )
    ax.set_xlim(min_x - 1.6, max_x + 1.6)
    ax.set_ylim(min_y - 1.3, max_y + 1.6)
    ax.set_aspect("equal", adjustable="datalim")
    ax.axis("off")
    fig.tight_layout(pad=1.3)

    exports: dict[str, bytes] = {}
    for extension, format_name, dpi in (
        ("png", "png", 210),
        ("svg", "svg", None),
        ("pdf", "pdf", None),
    ):
        target = io.BytesIO()
        save_options: dict[str, Any] = {"format": format_name, "bbox_inches": "tight"}
        if dpi:
            save_options["dpi"] = dpi
        fig.savefig(target, **save_options)
        exports[extension] = target.getvalue()
    plt.close(fig)
    return exports
