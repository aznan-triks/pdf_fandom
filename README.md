# pdf_fandom

Scraper that turns [Fandom](https://www.fandom.com/) wiki pages into PDFs, for import into
tools like NotebookLM.

## How it works

- Each `*.txt` file at the root contains a list of Fandom URLs (one per line, `#` for
  comments). One file = one topic = one output PDF with the same name (e.g. `weapons.txt` →
  `weapons.pdf`).
- For each URL: headless fetch via Selenium/Chrome, HTML cleanup (unfolding collapsed
  tabs/sections, removing menus/ads/external links/galleries), then conversion to PDF
  (Chrome DevTools Protocol printing).
- Parallel fetching (10 workers by default).

## Usage

Prerequisites: Python 3.x, Google Chrome installed (the driver is managed automatically by
`webdriver-manager`).

```bash
pip install -r requirements.txt
```

Create/edit one or more `topic-name.txt` files with the desired Fandom URLs, then:

Double-click `run.bat` (or `python fandom_to_pdf.py`).

The generated PDFs appear next to the matching `.txt` files. Detailed log in
`fandom_to_pdf.log`.
