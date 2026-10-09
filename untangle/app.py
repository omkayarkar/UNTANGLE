"""Streamlit interface for evidence-based P&ID and I/O-list analysis."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import streamlit as st

try:
    from .diagram import render_exports
    from .graph_model import build_process_graph, connection_validation_rows
    from .ingest import (
        IngestionError,
        excel_sheet_names,
        inspect_pdf,
        read_excel_preview,
        read_io_values,
        suggest_tag_column,
    )
    from .synthetic import generate_synthetic_files, generate_synthetic_zip
    from .untangle_core import associate_equipment, build_tag_report
except ImportError:
    from diagram import render_exports
    from graph_model import build_process_graph, connection_validation_rows
    from ingest import (
        IngestionError,
        excel_sheet_names,
        inspect_pdf,
        read_excel_preview,
        read_io_values,
        suggest_tag_column,
    )
    from synthetic import generate_synthetic_files, generate_synthetic_zip
    from untangle_core import associate_equipment, build_tag_report


ROOT = Path(__file__).resolve().parents[1]
METHODOLOGY_PATH = ROOT / "methodology.md"

st.set_page_config(
    page_title="UNTANGLE · P&ID analyzer",
    page_icon="U",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root { --ink:#10243a; --teal:#087f8c; --wash:#eef5f5; --line:#d9e3e8; }
    .stApp { background: #f6f9fa; color: var(--ink); }
    [data-testid="stHeader"] { background: rgba(246,249,250,.92); }
    [data-testid="stSidebar"] { background: #10243a; }
    [data-testid="stSidebar"] * { color: #f4f8f9; }
    [data-testid="stSidebar"] [data-baseweb="select"] * { color:#10243a; }
    .ut-kicker { color:#087f8c; letter-spacing:.13em; font-size:.74rem; font-weight:750; }
    .ut-title { color:#10243a; font-weight:760; letter-spacing:-.035em; font-size:2.35rem; line-height:1.05; margin:.25rem 0 .55rem; }
    .ut-sub { color:#536579; max-width:850px; font-size:1rem; line-height:1.5; }
    .ut-note { border-left:3px solid #087f8c; background:#edf4f5; padding:.75rem 1rem; border-radius:0 .5rem .5rem 0; color:#263c4c; }
    div[data-testid="stMetric"] { background:white; border:1px solid #d9e3e8; border-radius:12px; padding:14px 16px; }
    div[data-testid="stMetricLabel"] { color:#536579; }
    div[data-testid="stMetricValue"] { color:#10243a; }
    div.stButton > button[kind="primary"] { background:#087f8c; border-color:#087f8c; }
    [data-testid="stDataFrame"] { border:1px solid #d9e3e8; border-radius:10px; }
    </style>
    """,
    unsafe_allow_html=True,
)


def input_fingerprint(
    mode: str, pdf_bytes: bytes, excel_bytes: bytes, sheet: str, column: str, raster: bool = False
) -> str:
    digest = hashlib.sha256()
    for value in (
        mode.encode(),
        pdf_bytes,
        excel_bytes,
        sheet.encode(),
        column.encode(),
        str(raster).encode(),
    ):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def run_analysis(
    pdf_bytes: bytes,
    pdf_name: str,
    excel_bytes: bytes,
    sheet_name: str,
    tag_column: str,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def update(message: str) -> None:
        if progress:
            progress(message)

    update("Inspecting every PDF page for text and vector line evidence…")
    document = inspect_pdf(pdf_bytes, pdf_name, use_ocr=True)
    update("Validating the selected Excel worksheet and reading I/O tags…")
    values = read_io_values(excel_bytes, sheet_name, tag_column)
    update("Matching input rows to PDF detections and nearby equipment labels…")
    report_rows = build_tag_report(values, document.tags)
    associate_equipment(report_rows, document.tags, document.equipment)
    update("Building a conservative process graph from continuous vector paths…")
    process = build_process_graph(values, document)
    return {
        "document": document,
        "values": values,
        "report_rows": report_rows,
        "process": process,
        "warnings": [*document.warnings, *process.warnings],
        "pdf_name": pdf_name,
        "io_name": "synthetic_io_list.xlsx" if pdf_name.startswith("synthetic_") else "Uploaded I/O list",
    }


def _source_bundle() -> bytes:
    output = io.BytesIO()
    include = [
        ROOT / "app.py",
        ROOT / "diagram.py",
        ROOT / "graph_model.py",
        ROOT / "ingest.py",
        ROOT / "synthetic.py",
        ROOT / "untangle_core.py",
        ROOT / "__init__.py",
        ROOT / "methodology.md",
        ROOT / "tests" / "test_untangle.py",
        ROOT.parent / "requirements.txt",
        ROOT.parent / "README.md",
        ROOT.parent / "pyproject.toml",
    ]
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in include:
            if path.exists():
                archive.write(path, path.relative_to(ROOT.parent))
    return output.getvalue()


def _report_excel(rows: list[dict[str, object]]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Tag check")
    return output.getvalue()


def _connection_csv(rows: list[dict[str, object]]) -> bytes:
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig")


if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None
if "synthetic_inputs" not in st.session_state:
    st.session_state.synthetic_inputs = generate_synthetic_files()

with st.sidebar:
    st.markdown("## UNTANGLE")
    st.caption("Evidence-led P&ID analysis · VIT Code_Apex 3.0")
    mode = st.radio(
        "Input source",
        ["Uploaded files", "Synthetic demonstration"],
        help="Synthetic inputs are clearly marked and are not customer data.",
    )
    st.divider()

    pdf_bytes: bytes | None = None
    excel_bytes: bytes | None = None
    pdf_name = "drawing.pdf"
    selected_sheet = ""
    selected_column = ""
    preview: pd.DataFrame | None = None
    raster_demo = False
    current_fingerprint: str | None = None

    if mode == "Uploaded files":
        pdf_file = st.file_uploader("Customer P&ID · PDF", type=["pdf"], key="pid_pdf")
        excel_file = st.file_uploader(
            "Instrumentation I/O list · Excel",
            type=["xlsx", "xlsm"],
            key="io_excel",
        )
        if pdf_file is not None:
            pdf_bytes = pdf_file.getvalue()
            pdf_name = pdf_file.name
        if excel_file is not None:
            excel_bytes = excel_file.getvalue()
            try:
                sheets = excel_sheet_names(excel_bytes)
                selected_sheet = st.selectbox("Worksheet", sheets)
                preview = read_excel_preview(excel_bytes, selected_sheet)
                if len(preview.columns):
                    suggested = suggest_tag_column(list(preview.columns))
                    choices = ["— choose column —", *[str(col) for col in preview.columns]]
                    default_index = choices.index(suggested) if suggested in choices else 0
                    selected_column = st.selectbox(
                        "Column containing instrument tags",
                        choices,
                        index=default_index,
                    )
                    if selected_column == "— choose column —":
                        selected_column = ""
                    st.caption("I/O-list preview")
                    st.dataframe(preview.head(12), use_container_width=True, hide_index=True)
                else:
                    st.error("The selected worksheet has no columns.")
            except IngestionError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Could not preview the Excel file: {exc}")
        if pdf_bytes and excel_bytes and selected_sheet and selected_column:
            current_fingerprint = input_fingerprint(
                mode, pdf_bytes, excel_bytes, selected_sheet, selected_column
            )
    else:
        vector_pdf, raster_pdf, demo_excel = st.session_state.synthetic_inputs
        raster_demo = st.radio(
            "Synthetic drawing type",
            ["Vector PDF", "Raster PDF · OCR test"],
            help="The raster version is an OCR fixture; results depend on local Tesseract availability.",
        ) == "Raster PDF · OCR test"
        pdf_bytes = raster_pdf if raster_demo else vector_pdf
        excel_bytes = demo_excel
        pdf_name = "synthetic_pid_raster_ocr.pdf" if raster_demo else "synthetic_pid_vector.pdf"
        selected_sheet = "IO List"
        selected_column = "Instrument Tag"
        current_fingerprint = input_fingerprint(
            mode, pdf_bytes, excel_bytes, selected_sheet, selected_column, raster_demo
        )
        st.info("Generated local fixtures include a deliberate missing tag, a duplicate input row, an ambiguous PDF tag, pump roles and an unmarked line crossing.")
        st.download_button(
            "Download synthetic PDF + Excel files",
            data=generate_synthetic_zip(),
            file_name="untangle_synthetic_test_files.zip",
            mime="application/zip",
            use_container_width=True,
        )

    run_clicked = st.button(
        "Run analysis" if mode == "Uploaded files" else "Run synthetic demonstration",
        type="primary",
        use_container_width=True,
        disabled=not (pdf_bytes and excel_bytes and selected_sheet and selected_column),
    )

    if run_clicked:
        try:
            with st.status("Analysis in progress", expanded=True) as status:
                result = run_analysis(
                    pdf_bytes or b"",
                    pdf_name,
                    excel_bytes or b"",
                    selected_sheet,
                    selected_column,
                    progress=lambda message: status.update(label=message, state="running"),
                )
                result["fingerprint"] = current_fingerprint
                result["mode"] = mode
                st.session_state.analysis_result = result
                status.update(label="Analysis complete", state="complete", expanded=False)
        except IngestionError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Analysis stopped: {type(exc).__name__}: {exc}")

st.markdown('<div class="ut-kicker">ENGINEERING DOCUMENT INTELLIGENCE</div>', unsafe_allow_html=True)
st.markdown('<div class="ut-title">UNTANGLE</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="ut-sub">Turn P&amp;ID source evidence and an instrumentation I/O list into a traceable, reviewable diagram. '
    'Uncertain detections and connections stay marked for engineering review.</div>',
    unsafe_allow_html=True,
)
st.write("")

stored = st.session_state.analysis_result
result = stored if stored and stored.get("fingerprint") == current_fingerprint else None
if stored and not result and current_fingerprint:
    st.warning("The selected files or column mapping changed after the last run. Run analysis again before using reports or downloads.")

if not result:
    st.markdown(
        '<div class="ut-note"><b>Start with two inputs.</b> Add a P&amp;ID PDF and an Excel I/O list, '
        'or select Synthetic demonstration in the sidebar. Matching is evidence-based; a tag match does not confirm connectivity.</div>',
        unsafe_allow_html=True,
    )
    tabs = st.tabs(["Overview", "Methodology & limits"])
    with tabs[0]:
        st.subheader("Project overview")
        st.write(
            "UNTANGLE extracts selectable PDF text and vector line geometry first. "
            "OCR is a fallback when Tesseract is installed. Every input tag is retained "
            "in the tag check, including missing or ambiguous rows."
        )
        st.write("The synthetic demonstration generates new PDF and Excel data in memory; it is explicitly labelled and is not a prebuilt analysis result.")
    with tabs[1]:
        methodology = METHODOLOGY_PATH.read_text(encoding="utf-8") if METHODOLOGY_PATH.exists() else "Methodology file not found."
        st.markdown(methodology)
else:
    document = result["document"]
    report_rows = result["report_rows"]
    process = result["process"]
    valid_rows = [
        row for row in report_rows if row["detection_status"] != "Invalid input tag"
    ]
    found_rows = sum("Found" in str(row["detection_status"]) for row in valid_rows)
    unresolved_rows = sum(
        any(
            token in str(row["detection_status"])
            for token in ("Missing", "Ambiguous", "Duplicate", "Invalid")
        )
        for row in report_rows
    )
    supported_edges = len(process.connections)
    exports = render_exports(document, report_rows, process, "UNTANGLE · Simplified P&ID")
    connection_rows = connection_validation_rows(process)
    tab_overview, tab_diagram, tab_equipment, tab_connections, tab_tags, tab_downloads, tab_method = st.tabs(
        [
            "Project overview",
            "Diagram & extraction",
            "Equipment inventory",
            "Connection validation",
            "Tag-check report",
            "Download center",
            "Methodology & limits",
        ]
    )

    with tab_overview:
        metric1, metric2, metric3, metric4 = st.columns(4)
        metric1.metric("PDF pages", document.page_count)
        metric2.metric("I/O rows detected", f"{found_rows} / {len(report_rows)}")
        metric3.metric("Detected equipment", len(document.equipment))
        metric4.metric("Supported connections", supported_edges)
        st.markdown(
            '<div class="ut-note"><b>Interpretation:</b> A tag detection means a label-like string was found. '
            'A supported connection means continuous vector endpoints linked detected entity bounds. '
            'Neither confirms the physical plant configuration or process direction.</div>',
            unsafe_allow_html=True,
        )
        if unresolved_rows:
            st.warning(f"{unresolved_rows} I/O row(s) are missing, ambiguous, duplicate, or invalid and need review.")
        if result["warnings"]:
            with st.expander("Review findings", expanded=True):
                for warning in result["warnings"]:
                    st.write(f"- {warning}")
        st.caption(
            f"Inputs: {result['pdf_name']} · {result['io_name']} · "
            f"{len(document.tags)} distinct PDF tag detection(s) · "
            f"{len(document.segments)} vector segment(s)"
        )

    with tab_diagram:
        st.subheader("Generated simplified diagram")
        st.image(exports["png"], use_container_width=True)
        st.caption("Layout positions come from extracted source locations. Missing input tags remain in the review lane. No flow arrows are invented.")
        st.subheader("Extracted tag evidence")
        tag_detection_rows = [
            {
                "tag": tag.original,
                "normalized": tag.normalized,
                "page": tag.page,
                "location": f"({tag.bbox[0]:.1f}, {tag.bbox[1]:.1f}, {tag.bbox[2]:.1f}, {tag.bbox[3]:.1f})",
                "source": tag.source,
                "confidence": "—" if tag.confidence is None else f"{tag.confidence:.0f}%",
                "nearby text": tag.context,
            }
            for tag in document.tags
        ]
        st.dataframe(tag_detection_rows, use_container_width=True, hide_index=True)

    with tab_equipment:
        st.subheader("Equipment inventory and evidence")
        equipment_rows = [
            {
                "tag": asset.tag,
                "type": asset.kind,
                "page": asset.page,
                "evidence status": (
                    "Label-confirmed" if asset.confidence == "label-confirmed" else "Candidate · verify"
                ),
                "evidence": asset.evidence,
                "nearby text": asset.context,
            }
            for asset in document.equipment
        ]
        if equipment_rows:
            st.dataframe(equipment_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No equipment labels met the evidence threshold. This does not prove that the drawing contains no equipment.")
        if process.pump_pairs:
            st.subheader("Duty / standby pump review")
            st.dataframe(
                [
                    {
                        "pump tags": " / ".join(pair["tags"]),
                        "classification": pair["classification"],
                        "roles": "; ".join(f"{tag}: {role}" for tag, role in pair["roles"].items()),
                        "evidence": pair["evidence"],
                    }
                    for pair in process.pump_pairs
                ],
                use_container_width=True,
                hide_index=True,
            )
            st.caption("Only pairs with explicit opposite roles, a matching tag stem, and shared vector-connected topology are placed as parallel branches.")

    with tab_connections:
        st.subheader("Process-connection validation")
        if connection_rows:
            st.dataframe(connection_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No process connection was verified from the available vector evidence.")
        if process.warnings:
            for warning in process.warnings:
                st.warning(warning)
        st.caption("Line crossings and endpoint-to-line tees are not assumed to be connected. Direction remains unresolved unless independently evidenced.")

    with tab_tags:
        st.subheader("I/O-list tag check")
        st.dataframe(report_rows, use_container_width=True, hide_index=True)
        st.caption("Repeated I/O rows are retained. Duplicate input rows and repeated PDF detections are reported separately.")

    with tab_downloads:
        st.subheader("Current analysis exports")
        st.write("Every export below is regenerated from the currently selected input files and mapping.")
        left, middle, right = st.columns(3)
        name = Path(result["pdf_name"]).stem.replace(" ", "_")
        left.download_button("Diagram · PDF", exports["pdf"], f"{name}_simplified.pdf", "application/pdf", use_container_width=True)
        middle.download_button("Diagram · PNG", exports["png"], f"{name}_simplified.png", "image/png", use_container_width=True)
        right.download_button("Diagram · SVG", exports["svg"], f"{name}_simplified.svg", "image/svg+xml", use_container_width=True)
        left, middle, right = st.columns(3)
        left.download_button(
            "Tag report · CSV",
            pd.DataFrame(report_rows).to_csv(index=False).encode("utf-8-sig"),
            f"{name}_tag_check.csv",
            "text/csv",
            use_container_width=True,
        )
        middle.download_button(
            "Tag report · Excel",
            _report_excel(report_rows),
            f"{name}_tag_check.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        right.download_button(
            "Connection report · CSV",
            _connection_csv(connection_rows),
            f"{name}_connection_validation.csv",
            "text/csv",
            use_container_width=True,
        )
        methodology_bytes = METHODOLOGY_PATH.read_bytes() if METHODOLOGY_PATH.exists() else b"Methodology document is not available."
        left.download_button(
            "Technical methodology · Markdown",
            methodology_bytes,
            "UNTANGLE_methodology.md",
            "text/markdown",
            use_container_width=True,
        )
        right.download_button(
            "Complete source code + tests · ZIP",
            _source_bundle(),
            "UNTANGLE_source_and_tests.zip",
            "application/zip",
            use_container_width=True,
        )

    with tab_method:
        methodology = METHODOLOGY_PATH.read_text(encoding="utf-8") if METHODOLOGY_PATH.exists() else "Methodology file not found."
        st.markdown(methodology)
        st.markdown(
            "**Dependency disclosures:** Python · Streamlit · PyMuPDF · pandas · openpyxl · "
            "OpenCV · Pillow · NetworkX · Matplotlib · pytest. OCR uses pytesseract only "
            "when the Tesseract executable is available. No paid service, database, or LLM is used."
        )
