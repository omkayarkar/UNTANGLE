# UNTANGLE — technical methodology

## Purpose

UNTANGLE compares a customer-supplied P&ID PDF with an instrumentation I/O
workbook, creates a simplified review diagram, and exports traceable tag and
connection findings. It is an engineering review aid, not a substitute for
checking the source drawing or approving a plant change.

## Processing sequence

1. **Validate inputs.** Open the PDF with PyMuPDF and the workbook with
   openpyxl/pandas. Reject empty, encrypted, corrupt, or unusable inputs.
   Workbook sheet and tag-column selection are visible to the operator.
2. **Extract PDF evidence.** Inspect every page. Read selectable text and its
   page coordinates first; collect vector line segments and vector shape bounds.
   When selectable tag evidence is absent or very limited, attempt OCR only if
   the Tesseract executable is available. OCR findings remain marked as
   candidates and include their confidence and page location.
3. **Normalize and match tags.** Match case-insensitively after removing
   separators, while retaining the original spreadsheet tag. Keep every I/O
   row. Distinguish invalid values, duplicate spreadsheet rows, absent labels,
   and multiple PDF detections.
4. **Inventory equipment.** Detect equipment named in nearby text; tag-prefix
   hints without explicit equipment words are listed only as candidates.
   Nearby instrument-to-equipment labels are informational and are never
   treated as proof of a process connection.
5. **Build a conservative graph.** Create nodes for I/O tags, other extracted
   tags, and equipment candidates. A process edge is added only when vector
   segments form continuous endpoint-to-endpoint paths touching two detected
   entity bounds. A geometric crossing is not a connection. An endpoint meeting
   the middle of another line is an unresolved junction, not a confirmed tee.
   Connections have no direction unless explicit, reliable arrow evidence is
   implemented; the current analyzer reports direction as unresolved.
6. **Render and export.** Matplotlib creates a new SVG, PNG, and PDF for the
   current run. Detected items use extracted drawing positions; missing I/O
   tags appear in a separately labelled review lane. Verified duty/standby
   pairs require explicit opposite duty/standby labels, a matching tag stem,
   and at least two shared vector-connected neighboring entities.
7. **Report findings.** The tag report is available as CSV and Excel.
   Connection findings identify supported vector continuity, uncertain
   crossings/junctions, and disconnected graph nodes.

## Evidence and review states

- **Selectable PDF text:** directly extracted text; it still may be a mistaken
  tag interpretation.
- **OCR candidate:** OCR string with confidence and source coordinates; verify
  against the page image.
- **Label-confirmed equipment:** tag text has nearby explicit equipment wording.
- **Equipment candidate:** equipment-like tag prefix only; verify manually.
- **Supported vector connection:** continuous extracted vector paths connect
  two detected entity bounds. This validates drawing geometry only, not actual
  plant topology or process direction.
- **Missing / ambiguous / duplicate:** retained I/O input needing correction
  or engineering review.

## Known limitations

- Raster-only drawing geometry is not vectorized. OCR may recover visible tag
  text, but this build does not assert raster pipe connectivity.
- Vector line intersections cannot reliably distinguish every junction dot,
  bridge, or crossover style used by CAD exporters. Crossing and endpoint-to-
  line cases are therefore flagged rather than joined.
- Equipment symbol recognition is conservative and text-led; it is not a
  general-purpose P&ID symbol classifier. Instrument associations based only
  on proximity are not connectivity evidence.
- Arrowheads are not yet decoded robustly. The diagram does not add flow arrows
  where direction is not supported.
- OCR is optional. It requires both the Python `pytesseract` package and a
  system Tesseract executable; without them the application reports the
  limitation and continues with selectable PDF text.
- The generated arrangement is a review sketch, not a standards-compliant
  issued-for-construction drawing. It simplifies routing and does not preserve
  line classes, specifications, insulation, signal types, or dimensions.
- A PDF with unusually large vector density may exceed the bounded
  intersection scan. In that case the scan limitation is reported and no
  exhaustive crossover claim is made.

## Synthetic data disclosure

The built-in generator creates vector and raster PDF fixtures and an Excel
workbook. They include a tank, explicit duty/standby pump labels, a heat
exchanger, vessel, control valve, multiple instrument types, a deliberate
missing I/O tag, a repeated I/O row, a repeated PDF tag, a branch, and an
unmarked line crossing. These fixtures test code paths only; they are not
customer drawings or proof that arbitrary P&IDs can be parsed perfectly.

## Dependencies

Python, Streamlit, PyMuPDF, pandas, openpyxl, OpenCV, Pillow, NetworkX,
Matplotlib, pytest, and the pytesseract Python wrapper. Tesseract itself is an
optional system dependency for OCR. No database, paid API, LLM, or remote
service is used.
