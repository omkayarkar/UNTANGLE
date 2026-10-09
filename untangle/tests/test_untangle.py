from __future__ import annotations

import shutil

import networkx as nx
import pandas as pd
import pymupdf as fitz
import pytest

from untangle.diagram import render_exports
from untangle.graph_model import (
    _find_line_intersections,
    build_process_graph,
    connection_validation_rows,
)
from untangle.ingest import IngestionError, excel_sheet_names, inspect_pdf, read_io_values
from untangle.synthetic import generate_synthetic_files
from untangle.untangle_core import (
    LineSegment,
    TagOccurrence,
    build_tag_report,
    is_valid_tag,
    normalize_tag,
)


@pytest.fixture(scope="module")
def synthetic():
    return generate_synthetic_files()


def test_tag_normalization_and_validation():
    assert normalize_tag(" pt - 101 ") == "PT101"
    assert normalize_tag("Lv_22a") == "LV22A"
    assert is_valid_tag("LT-201")
    assert not is_valid_tag("not-a-tag")
    assert not is_valid_tag("")


def test_duplicate_missing_ambiguous_and_invalid_tag_rows_are_kept():
    tags = [
        TagOccurrence("LT-201", "LT201", 1, (10, 20, 30, 30), "selectable text"),
        TagOccurrence("LT-201", "LT201", 1, (80, 20, 100, 30), "selectable text"),
        TagOccurrence("PT-101", "PT101", 1, (10, 40, 30, 50), "selectable text"),
    ]
    report = build_tag_report(
        ["pt 101", "LT-201", "XX-999", "PT-101", "", "PT-101"], tags
    )
    assert len(report) == 6
    assert report[0]["detection_status"] == "Duplicate in I/O list; Found"
    assert report[1]["detection_status"] == "Ambiguous PDF detections"
    assert report[2]["detection_status"] == "Missing from P&ID"
    assert report[4]["detection_status"] == "Invalid input tag"
    assert report[5]["detection_status"].startswith("Duplicate in I/O list")


def test_vector_pdf_excel_ingestion_and_equipment_evidence(synthetic):
    vector_pdf, _, workbook = synthetic
    document = inspect_pdf(vector_pdf, "fixture.pdf", use_ocr=False)
    assert document.page_count == 1
    assert any(tag.normalized == "PT101" for tag in document.tags)
    assert any(segment.length > 10 for segment in document.segments)
    assert any(asset.kind == "Pump" and asset.confidence == "label-confirmed" for asset in document.equipment)
    assert excel_sheet_names(workbook) == ["IO List"]
    assert read_io_values(workbook, "IO List", "Instrument Tag").count("FT-201") == 2


def test_graph_records_crossings_but_does_not_join_them(synthetic):
    vector_pdf, _, workbook = synthetic
    document = inspect_pdf(vector_pdf, "fixture.pdf", use_ocr=False)
    values = read_io_values(workbook, "IO List", "Instrument Tag")
    graph = build_process_graph(values, document)
    assert graph.crossovers
    assert all(row["connection_status"] != "Supported vector continuity" for row in connection_validation_rows(graph) if row["connection_status"] == "Unresolved crossover")
    assert any(row["connection_status"] == "Unresolved crossover" for row in connection_validation_rows(graph))


def test_continuous_vectors_produce_graph_edges_from_synthetic_topology(synthetic):
    vector_pdf, _, _ = synthetic
    document = inspect_pdf(vector_pdf, "fixture.pdf", use_ocr=False)
    graph = build_process_graph([], document)
    assert isinstance(graph.graph, nx.Graph)
    assert graph.graph.number_of_nodes() >= 5
    assert graph.connections
    assert any("P101A" in {normalize_tag(row["from"]), normalize_tag(row["to"])} for row in graph.connections)


def test_geometric_crossing_is_not_a_connection():
    horizontal = LineSegment(1, 0, 10, 100, 10, 1)
    vertical = LineSegment(1, 50, 0, 50, 20, 2)
    crossovers, junctions, complete = _find_line_intersections([horizontal, vertical])
    assert complete
    assert len(crossovers) == 1
    assert not junctions


def test_endpoint_to_line_is_a_review_junction_not_a_crossover():
    main = LineSegment(1, 0, 10, 100, 10, 1)
    branch = LineSegment(1, 50, 10, 50, 40, 2)
    crossovers, junctions, _ = _find_line_intersections([main, branch])
    assert not crossovers
    assert len(junctions) == 1


def test_explicit_duty_standby_pair_and_parallel_diagram(synthetic):
    vector_pdf, _, workbook = synthetic
    document = inspect_pdf(vector_pdf, "fixture.pdf", use_ocr=False)
    values = read_io_values(workbook, "IO List", "Instrument Tag")
    graph = build_process_graph(values, document)
    pairs = [pair for pair in graph.pump_pairs if pair["verified"]]
    assert pairs
    assert set(pairs[0]["roles"].values()) == {"duty", "standby"}
    report = build_tag_report(values, document.tags)
    exports = render_exports(document, report, graph, "Test")
    assert exports["pdf"].startswith(b"%PDF")
    assert exports["png"].startswith(b"\x89PNG")
    assert b"<svg" in exports["svg"][:1000]


def test_invalid_and_empty_inputs_are_rejected():
    with pytest.raises(IngestionError, match="empty"):
        inspect_pdf(b"", use_ocr=False)
    with pytest.raises(IngestionError, match="valid PDF"):
        inspect_pdf(b"not a PDF", use_ocr=False)
    with pytest.raises(IngestionError, match="empty"):
        excel_sheet_names(b"")
    with pytest.raises(IngestionError, match="Could not read"):
        excel_sheet_names(b"not an Excel workbook")


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract executable is not installed")
def test_raster_fixture_can_supply_ocr_candidates(synthetic):
    _, raster_pdf, _ = synthetic
    document = inspect_pdf(raster_pdf, "raster.pdf", use_ocr=True)
    assert document.page_count == 1
    assert any(tag.source == "OCR" for tag in document.tags)
