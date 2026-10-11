"""Streamlit interface for evidence-based P&ID and I/O-list analysis."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pymupdf as fitz
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


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
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
:root {
    --ut-bg: #F4F8F7;
    --ut-surface: #FFFFFF;
    --ut-text: #173B40;
    --ut-muted: #526A6D;
    --ut-accent: #176D68;
    --ut-border: #DCE7E4;
    --ut-note: #EAF3F1;
    --ut-note-text: #294D4D;
}


.stApp, [data-testid="stAppViewContainer"] {
    background: var(--ut-bg);
    color: var(--ut-text);
}

[data-testid="stHeader"] {
    background: var(--ut-bg);
}

[data-testid="stSidebar"] {
    background: var(--ut-surface);
    border-right: 1px solid var(--ut-border);
}

[data-testid="stSidebar"] * {
    color: var(--ut-text);
}

[data-testid="stSidebar"] [data-baseweb="select"] * {
    color: var(--ut-text);
}

.ut-kicker {
    color: var(--ut-accent);
    letter-spacing: .14em;
    font-size: .68rem;
    font-weight: 750;
}

.ut-title {
    color: var(--ut-text);
    font-weight: 760;
    letter-spacing: -.025em;
    font-size: 1.8rem;
    line-height: 1.1;
    margin: .18rem 0 .28rem;
}

.ut-sub {
    color: var(--ut-muted);
    max-width: 900px;
    font-size: .92rem;
    line-height: 1.5;
}

.ut-note {
    border-left: 3px solid var(--ut-accent);
    background: var(--ut-note);
    padding: .8rem 1rem;
    border-radius: 0 .5rem .5rem 0;
    color: var(--ut-note-text);
}

.ut-status {
    font-size: .76rem;
    font-weight: 700;
    color: var(--ut-accent);
    background: var(--ut-note);
    border: 1px solid var(--ut-border);
    border-radius: 999px;
    padding: .36rem .7rem;
    display: inline-block;
}

div[data-testid="stMetric"] {
    background: var(--ut-surface);
    color: var(--ut-text);
    border: 1px solid var(--ut-border);
    border-radius: 12px;
    padding: 13px 15px;
}

div[data-testid="stMetricLabel"],
div[data-testid="stMetricValue"] {
    color: var(--ut-text);
}

[data-testid="stExpander"] {
    background: var(--ut-surface);
    border: 1px solid var(--ut-border);
    border-radius: 10px;
}

[data-testid="stFileUploader"] {
    background: var(--ut-surface);
    border: 1px solid var(--ut-border);
    border-radius: 10px;
    padding: 8px;
}

[data-testid="stFileUploader"] section {
    background: transparent;
}

[data-testid="stDataFrame"],
[data-testid="stTable"] {
    border: 1px solid var(--ut-border);
    border-radius: 10px;
}

div.stButton > button[kind="primary"] {
    background: var(--ut-accent);
    border: 1px solid var(--ut-accent);
    color: #FFFFFF;
    border-radius: 9px;
    font-weight: 650;
}


div.stButton > button:focus-visible {
    outline: 2px solid var(--ut-accent);
    outline-offset: 2px;
}

.block-container {
    padding-top: 1.8rem;
    padding-bottom: 2.5rem;
}
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
        ROOT / "pyproject.toml",
        PROJECT_ROOT / "requirements.txt",
        PROJECT_ROOT / "README.md",
    ]
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in include:
            if path.exists():
                archive.write(path, path.relative_to(PROJECT_ROOT))
    return output.getvalue()


def _pdf_preview(pdf_bytes: bytes) -> bytes | None:
    """Render the first source page for review without altering its evidence."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            if not document.page_count:
                return None
            pixmap = document[0].get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
            return pixmap.tobytes("png")
    except Exception:
        return None


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
    st.caption("P&ID / I/O review workspace")
    page = st.radio(
        "Workspace",
        [
            "Project workspace",
            "Process diagram",
            "Validation center",
            "Source evidence",
            "Reports & exports",
            "Methodology",
        ],
        label_visibility="collapsed",
    )
    st.divider()
    mode = st.radio(
        "Input source",
        ["Uploaded files", "Synthetic demonstration"],
        help="Synthetic inputs are generated locally and are not customer data.",
    )
    stored = st.session_state.analysis_result
    if stored and stored.get("mode") == mode:
        st.markdown('<span class="ut-status">ANALYSIS SAVED</span>', unsafe_allow_html=True)
    else:
        st.caption("No current analysis for this input source")

pdf_bytes: bytes | None = None
excel_bytes: bytes | None = None
pdf_name = "drawing.pdf"
selected_sheet = ""
selected_column = ""
preview: pd.DataFrame | None = None
pdf_preview: bytes | None = None
raster_demo = False
current_fingerprint: str | None = None
input_error = ""

st.markdown('<div class="ut-kicker">ENGINEERING DOCUMENT WORKSPACE</div>', unsafe_allow_html=True)
st.markdown('<div class="ut-title">UNTANGLE</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="ut-sub">Evidence-led review of P&amp;ID drawings and instrumentation I/O lists. '
    'Extracted labels and uncertain connections remain visible for engineering review.</div>',
    unsafe_allow_html=True,
)
st.write("")

with st.expander("Project inputs", expanded=page == "Project workspace"):
    if mode == "Uploaded files":
        pdf_column, excel_column = st.columns(2, gap="large")
        with pdf_column:
            st.markdown("#### P&ID drawing")
            pdf_file = st.file_uploader(
                "Select drawing (PDF)",
                type=["pdf"],
                key="pid_pdf",
                help="Upload the source drawing. The first page preview is shown below.",
            )
            if pdf_file is not None:
                pdf_bytes = pdf_file.getvalue()
                pdf_name = pdf_file.name
                pdf_preview = _pdf_preview(pdf_bytes)
                if pdf_preview:
                    st.image(pdf_preview, caption=f"{pdf_name} · page 1 preview", use_container_width=True)
                    try:
                        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                            st.caption(f"Valid PDF · {doc.page_count} page(s)")
                    except Exception:
                        pass
                else:
                    input_error = "The selected drawing could not be opened as a valid PDF."
                    st.error(input_error)
            else:
                st.caption("No P&ID PDF selected.")
        with excel_column:
            st.markdown("#### Instrumentation I/O list")
            excel_file = st.file_uploader(
                "Select I/O workbook (Excel)",
                type=["xlsx", "xlsm"],
                key="io_excel",
            )
            if excel_file is not None:
                excel_bytes = excel_file.getvalue()
                try:
                    sheets = excel_sheet_names(excel_bytes)
                    workbook_id = hashlib.sha256(excel_bytes).hexdigest()[:12]
                    selected_sheet = st.selectbox(
                        "Worksheet", sheets, key=f"io_sheet_{workbook_id}"
                    )
                    preview = read_excel_preview(excel_bytes, selected_sheet)
                    if len(preview.columns):
                        column_key = f"io_tag_column_{workbook_id}_{hashlib.sha256(selected_sheet.encode()).hexdigest()[:8]}"
                        suggested = suggest_tag_column(list(preview.columns))
                        st.selectbox(
                            "Column containing instrument tags",
                            ["— choose column —", *[str(col) for col in preview.columns]],
                            index=(
                                [str(col) for col in preview.columns].index(str(suggested)) + 1
                                if suggested is not None and str(suggested) in [str(col) for col in preview.columns]
                                else 0
                            ),
                            key=column_key,
                        )
                        selected_column = st.session_state[column_key]
                        if selected_column == "— choose column —":
                            selected_column = ""
                        st.dataframe(
                            preview.head(8),
                            use_container_width=True,
                            hide_index=True,
                            height=235,
                        )
                    else:
                        input_error = "The selected worksheet has no columns."
                        st.error(input_error)
                except IngestionError as exc:
                    input_error = str(exc)
                    st.error(input_error)
                except Exception as exc:
                    input_error = f"Could not preview the Excel file: {exc}"
                    st.error(input_error)
            else:
                st.caption("No I/O workbook selected.")
    else:
        st.info(
            "Synthetic demonstration · generated local fixtures, not customer documents. "
            "Includes missing and duplicate tags, ambiguous evidence, pump roles, and an unmarked crossing."
        )
        raster_demo = st.radio(
            "Drawing fixture",
            ["Vector PDF", "Raster PDF · OCR test"],
            horizontal=True,
            help="OCR fixture results depend on whether the Tesseract executable is installed.",
        ) == "Raster PDF · OCR test"
        vector_pdf, raster_pdf, demo_excel = st.session_state.synthetic_inputs
        pdf_bytes = raster_pdf if raster_demo else vector_pdf
        excel_bytes = demo_excel
        pdf_name = "synthetic_pid_raster_ocr.pdf" if raster_demo else "synthetic_pid_vector.pdf"
        pdf_preview = _pdf_preview(pdf_bytes)
        selected_sheet = "IO List"
        selected_column = "Instrument Tag"
        preview = read_excel_preview(excel_bytes, selected_sheet)
        if pdf_preview:
            st.image(pdf_preview, caption=f"{pdf_name} · page 1 preview", use_container_width=True)
        st.download_button(
            "Download synthetic PDF + Excel files",
            data=generate_synthetic_zip(),
            file_name="untangle_synthetic_test_files.zip",
            mime="application/zip",
        )

    if mode == "Uploaded files":
        if pdf_bytes and excel_bytes and selected_sheet and selected_column and not input_error:
            current_fingerprint = input_fingerprint(
                mode, pdf_bytes, excel_bytes, selected_sheet, selected_column
            )
        can_analyze = bool(pdf_bytes and pdf_preview and excel_bytes and selected_sheet and selected_column and not input_error)
    else:
        current_fingerprint = input_fingerprint(
            mode, pdf_bytes or b"", excel_bytes or b"", selected_sheet, selected_column, raster_demo
        )
        can_analyze = bool(pdf_bytes and pdf_preview and excel_bytes)

    run_clicked = st.button(
        "Analyze documents" if mode == "Uploaded files" else "Run synthetic demonstration",
        type="primary",
        disabled=not can_analyze,
        use_container_width=True,
    )
    if mode == "Uploaded files" and not can_analyze and not input_error:
        st.caption("Choose a valid PDF, an Excel workbook, a worksheet, and a tag column to enable analysis.")
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
                result["exports"] = render_exports(
                    result["document"],
                    result["report_rows"],
                    result["process"],
                    "UNTANGLE · Simplified P&ID",
                )
                st.session_state.analysis_result = result
                status.update(label="Analysis complete", state="complete", expanded=False)
        except IngestionError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Analysis stopped: {type(exc).__name__}: {exc}")

stored = st.session_state.analysis_result
result = stored if stored and stored.get("fingerprint") == current_fingerprint else None
if stored and not result:
    st.warning("The selected files or worksheet/tag-column mapping differs from the saved analysis. Run analysis again before reviewing results or downloads.")

if not result:
    if page == "Methodology":
        st.markdown(METHODOLOGY_PATH.read_text(encoding="utf-8"))
    elif page == "Project workspace":
        st.markdown(
            '<div class="ut-note"><b>Start with the source documents.</b> Select a P&amp;ID PDF and an Excel I/O list above, '
            'or switch to Synthetic demonstration. A matched instrument tag does not confirm process connectivity.</div>',
            unsafe_allow_html=True,
        )
        st.subheader("Review workspace")
        st.write(
            "UNTANGLE extracts selectable PDF text and vector geometry first. OCR is a fallback when Tesseract is available. "
            "Every I/O row is retained in the tag check, including missing, duplicate, ambiguous, and invalid entries."
        )
        st.caption("Run analysis to populate the process diagram, validation findings, source evidence, and exports.")
    else:
        st.info("No current analysis is available for this view. Select Project workspace, add both source documents, and run analysis.")
else:
    document = result["document"]
    report_rows = result["report_rows"]
    process = result["process"]
    exports = result["exports"]
    valid_rows = [row for row in report_rows if row["detection_status"] != "Invalid input tag"]
    found_rows = sum("Found" in str(row["detection_status"]) for row in valid_rows)
    unresolved_rows = sum(
        any(token in str(row["detection_status"]) for token in ("Missing", "Ambiguous", "Duplicate", "Invalid"))
        for row in report_rows
    )
    connection_rows = [
        {**row, "page": str(row["page"])}
        for row in connection_validation_rows(process)
    ]

    if page == "Project workspace":
        st.subheader("Analysis overview")
        metric1, metric2, metric3, metric4 = st.columns(4)
        metric1.metric("PDF pages", document.page_count)
        metric2.metric("Tags found", f"{found_rows} / {len(report_rows)}")
        metric3.metric("Equipment labels", len(document.equipment))
        metric4.metric("Supported connections", len(process.connections))
        st.markdown(
            '<div class="ut-note"><b>Evidence boundary:</b> A tag detection means a label-like string was found. '
            'A supported connection requires continuous vector evidence. Neither verifies physical plant configuration or flow direction.</div>',
            unsafe_allow_html=True,
        )
        if unresolved_rows:
            st.warning(f"{unresolved_rows} I/O row(s) need review: missing, ambiguous, duplicate, or invalid.")
        if result["warnings"]:
            with st.expander("Extraction and connection warnings", expanded=True):
                for warning in result["warnings"]:
                    st.write(f"- {warning}")
        st.caption(
            f"{result['pdf_name']} · {result['io_name']} · "
            f"{len(document.tags)} distinct PDF tag detections · {len(document.segments)} vector segments"
        )
    elif page == "Process diagram":
        st.subheader("Process diagram")
        st.image(exports["png"], use_container_width=True)
        st.caption("Positions derive from source locations. Unresolved connections remain unjoined; no flow arrows are invented.")
        st.subheader("Extracted tag evidence")
        st.dataframe(
            [
                {
                    "tag": tag.original,
                    "normalized": tag.normalized,
                    "page": tag.page,
                    "source": tag.source,
                    "confidence": "—" if tag.confidence is None else f"{tag.confidence:.0f}%",
                    "nearby text": tag.context,
                }
                for tag in document.tags
            ],
            use_container_width=True,
            hide_index=True,
        )
    elif page == "Validation center":
        st.subheader("Validation center")
        statuses = sorted({str(row["detection_status"]) for row in report_rows})
        selected_status = st.multiselect("Filter I/O findings by status", statuses, default=statuses)
        filtered_rows = [row for row in report_rows if row["detection_status"] in selected_status]
        metric1, metric2, metric3 = st.columns(3)
        metric1.metric("I/O rows", len(report_rows))
        metric2.metric("Found", found_rows)
        metric3.metric("Needs review", unresolved_rows)
        st.markdown("**I/O tag verification**")
        st.dataframe(filtered_rows, use_container_width=True, hide_index=True)
        st.markdown("**Process connections**")
        if connection_rows:
            st.dataframe(connection_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No process connection was verified from the available vector evidence.")
        for warning in process.warnings:
            st.warning(warning)
        st.caption("Line crossings and endpoint-to-line tees are not assumed to connect. Direction remains unresolved unless independently evidenced.")
        equipment_rows = [
            {
                "tag": asset.tag,
                "type": asset.kind,
                "page": asset.page,
                "status": "Label-confirmed" if asset.confidence == "label-confirmed" else "Candidate · verify",
                "evidence": asset.evidence,
                "nearby text": asset.context,
            }
            for asset in document.equipment
        ]
        if equipment_rows:
            st.markdown("**Equipment evidence**")
            st.dataframe(equipment_rows, use_container_width=True, hide_index=True)
        if process.pump_pairs:
            st.markdown("**Duty / standby pump review**")
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
    elif page == "Source evidence":
        st.subheader("Source evidence")
        st.caption(f"Original drawing: {result['pdf_name']}")
        if pdf_preview:
            st.image(pdf_preview, caption="Page 1 of source P&ID", use_container_width=True)
        elif pdf_bytes:
            st.warning("The source PDF preview could not be rendered.")
        st.markdown(f"**I/O workbook:** {result['io_name']} · **Worksheet:** {selected_sheet}")
        if preview is not None:
            st.dataframe(
                preview.head(100).style.apply(
                    lambda row: [
                        "background-color: #d8eeee" if str(column) == selected_column else ""
                        for column in preview.columns
                    ],
                    axis=1,
                ),
                use_container_width=True,
                hide_index=True,
            )
        st.markdown("**Detected drawing labels**")
        st.dataframe(
            [
                {
                    "tag": tag.original,
                    "page": tag.page,
                    "source": tag.source,
                    "context": tag.context,
                    "bounding box": tuple(round(value, 1) for value in tag.bbox),
                }
                for tag in document.tags
            ],
            use_container_width=True,
            hide_index=True,
        )
        if result["warnings"]:
            st.markdown("**Extraction warnings**")
            for warning in result["warnings"]:
                st.warning(warning)
    elif page == "Reports & exports":
        st.subheader("Reports & exports")
        st.caption("Files below are generated from the current source documents and mapping.")
        name = Path(result["pdf_name"]).stem.replace(" ", "_")
        left, middle, right = st.columns(3)
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
        left.download_button(
            "Methodology · Markdown",
            METHODOLOGY_PATH.read_bytes(),
            "UNTANGLE_methodology.md",
            "text/markdown",
            use_container_width=True,
        )
        right.download_button(
            "Source code + tests · ZIP",
            _source_bundle(),
            "UNTANGLE_source_and_tests.zip",
            "application/zip",
            use_container_width=True,
        )
    elif page == "Methodology":
        st.markdown(METHODOLOGY_PATH.read_text(encoding="utf-8"))
        st.markdown(
            "**Dependencies:** Python · Streamlit · PyMuPDF · pandas · openpyxl · OpenCV · Pillow · "
            "NetworkX · Matplotlib · pytest. OCR uses pytesseract only when the Tesseract executable is available."
        )
