# UNTANGLE — P&ID and I/O-list analyzer

A local Python/Streamlit application for comparing a customer P&ID PDF with
an instrumentation I/O workbook. It extracts text and vector evidence,
validates I/O tags, builds a conservative connection graph, and exports a
new review diagram and reports.

## Run

```bash
python -m pip install -r requirements.txt
streamlit run untangle/app.py --server.address 0.0.0.0 --server.port 5000
```

OCR is optional. To enable it, install the Tesseract executable for the
operating system as well as the `pytesseract` Python dependency.

## Test

```bash
pytest -q untangle/tests
```

Synthetic PDF and Excel inputs can be selected in the app or generated from
`untangle.synthetic`. These files are test fixtures, not customer data.

Read [the methodology and limitations](untangle/methodology.md) before relying
on any extracted tag, equipment classification, or connection.

## Project files

- `untangle/app.py` — Streamlit workflow and download center.
- `untangle/ingest.py` — PDF, Excel, OCR, and vector ingestion.
- `untangle/graph_model.py` — supported connections, crossings, and pump pairs.
- `untangle/diagram.py` — source-derived simplified diagram and exports.
- `untangle/untangle_core.py` — tag normalization, matching, and records.
- `untangle/synthetic.py` — vector/raster PDF and Excel fixture generator.
- `untangle/tests/` — focused automated tests.
