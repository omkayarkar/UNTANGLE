# Replit setup

This project runs as a Streamlit web app on port 5000.

## Run

```bash
python -m streamlit run untangle/app.py --server.address 0.0.0.0 --server.port 5000 --server.headless true
```

## Test

```bash
python -m pytest -q untangle/tests
```

Dependencies are listed in the root `requirements.txt`. OCR additionally uses
the Tesseract executable; text and vector PDF analysis do not require OCR.
