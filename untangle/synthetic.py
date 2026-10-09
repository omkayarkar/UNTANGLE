"""Generate reproducible synthetic P&ID and I/O-list test inputs."""

from __future__ import annotations

import io
import zipfile

import pymupdf as fitz
import pandas as pd


SYNTHETIC_TAGS = [
    "PT-101",
    "FT-201",
    "LT-201",
    "TT-201",
    "FV-201",
    "LV-201",
    "XX-999",
    "FT-201",  # deliberate repeated I/O-list row
]


def _add_text(page: fitz.Page, x: float, y: float, text: str, size: float = 8.0) -> None:
    page.insert_text((x, y), text, fontsize=size, fontname="helv", color=(0.09, 0.15, 0.21))


def _line(page: fitz.Page, x1: float, y1: float, x2: float, y2: float) -> None:
    page.draw_line((x1, y1), (x2, y2), color=(0.12, 0.35, 0.41), width=1.6)


def _equipment(page: fitz.Page, rect: fitz.Rect, text: str) -> None:
    page.draw_rect(rect, color=(0.07, 0.32, 0.38), width=1.6)
    page.insert_textbox(
        fitz.Rect(rect.x0 + 3, rect.y0 + 5, rect.x1 - 3, rect.y1 - 3),
        text,
        fontsize=7.8,
        fontname="helv",
        color=(0.07, 0.14, 0.21),
        align=fitz.TEXT_ALIGN_CENTER,
    )


def _make_vector_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page(width=900, height=570)
    _add_text(page, 34, 38, "SYNTHETIC TEST DRAWING — NOT A CUSTOMER P&ID", 10)
    _add_text(page, 34, 55, "UNTANGLE test fixture · flow direction intentionally not annotated", 8)

    tank = fitz.Rect(50, 185, 142, 292)
    pump_a = fitz.Rect(250, 207, 335, 263)
    pump_b = fitz.Rect(250, 332, 335, 388)
    exchanger = fitz.Rect(500, 207, 573, 263)
    vessel = fitz.Rect(735, 193, 835, 278)
    valve = fitz.Rect(625, 220, 672, 250)
    _equipment(page, tank, "T-101\nFEED TANK")
    _equipment(page, pump_a, "P-101A\nDUTY PUMP")
    _equipment(page, pump_b, "P-101B\nSTANDBY PUMP")
    _equipment(page, exchanger, "E-201\nHEAT EXCHANGER")
    _equipment(page, vessel, "V-201\nPROCESS VESSEL")
    page.draw_circle((648.5, 235), 14, color=(0.07, 0.32, 0.38), width=1.5)
    _add_text(page, 610, 270, "FV-201 CONTROL VALVE", 7.5)

    # Main flow path and a parallel pump branch. Every shared tee in this
    # fixture is an explicit vector endpoint; the analyzer still validates
    # connectivity from geometry rather than reading a precomputed graph.
    _line(page, tank.x1, 235, 200, 235)
    _line(page, 200, 235, 200, 360)
    _line(page, 200, 235, pump_a.x0, 235)
    _line(page, 200, 360, pump_b.x0, 360)
    _line(page, pump_a.x1, 235, 395, 235)
    _line(page, pump_b.x1, 360, 395, 360)
    _line(page, 395, 360, 395, 235)
    _line(page, 395, 235, exchanger.x0, 235)
    _line(page, exchanger.x1, 235, valve.x0, 235)
    _line(page, valve.x1, 235, vessel.x0, 235)

    # Instrument labels and deliberate duplicate PDF occurrence for LT-201.
    _add_text(page, 148, 182, "PT-101", 8)
    _add_text(page, 418, 195, "FT-201", 8)
    _add_text(page, 468, 193, "TT-201", 8)
    _add_text(page, 680, 330, "LT-201", 8)
    _add_text(page, 795, 325, "LT-201", 8)
    _add_text(page, 755, 180, "LV-201", 8)

    # Two independent pipes cross geometrically. There is no junction dot.
    _line(page, 510, 455, 715, 455)
    _line(page, 612, 420, 612, 500)
    _add_text(page, 515, 480, "SYNTHETIC CROSSING TEST — NO JUNCTION MARKER", 7)
    document.set_metadata(
        {
            "title": "UNTANGLE synthetic P&ID fixture",
            "author": "UNTANGLE synthetic data generator",
            "subject": "Synthetic vector drawing for local test and demo only",
        }
    )
    return document.tobytes(deflate=True)


def _rasterize_pdf(vector_pdf: bytes) -> bytes:
    source = fitz.open(stream=vector_pdf, filetype="pdf")
    output = fitz.open()
    for page in source:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
        new_page = output.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=pixmap.tobytes("png"))
    output.set_metadata(
        {
            "title": "UNTANGLE synthetic raster OCR fixture",
            "author": "UNTANGLE synthetic data generator",
        }
    )
    data = output.tobytes(deflate=True)
    output.close()
    source.close()
    return data


def _make_excel() -> bytes:
    output = io.BytesIO()
    frame = pd.DataFrame(
        {
            "Instrument Tag": SYNTHETIC_TAGS,
            "Signal Type": [
                "Pressure", "Flow", "Level", "Temperature", "Flow control",
                "Level control", "Deliberately missing", "Flow (duplicate input row)",
            ],
            "Description": [
                "Feed tank pressure", "Main line flow", "Vessel level",
                "Outlet temperature", "Flow control valve", "Level control valve",
                "Synthetic missing-tag check", "Repeated tag row for validation",
            ],
        }
    )
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="IO List")
    return output.getvalue()


def generate_synthetic_files() -> tuple[bytes, bytes, bytes]:
    """Return a vector PDF, rasterized PDF, and Excel I/O list."""
    vector_pdf = _make_vector_pdf()
    return vector_pdf, _rasterize_pdf(vector_pdf), _make_excel()


def generate_synthetic_zip() -> bytes:
    vector_pdf, raster_pdf, excel = generate_synthetic_files()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("synthetic_pid_vector.pdf", vector_pdf)
        archive.writestr("synthetic_pid_raster_ocr.pdf", raster_pdf)
        archive.writestr("synthetic_io_list.xlsx", excel)
        archive.writestr(
            "README.txt",
            "All files in this archive are generated synthetic fixtures. "
            "They are not customer engineering data and are not evidence of "
            "extraction quality on arbitrary drawings.\n",
        )
    return output.getvalue()
