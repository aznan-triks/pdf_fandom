from bs4 import BeautifulSoup
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import tempfile
import base64
import time
import os
import logging
from datetime import datetime

LOG_PATH = Path(__file__).parent / 'fandom_to_pdf.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler(),
    ]
)
for noisy in ('selenium', 'urllib3', 'webdriver_manager', 'asyncio'):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger(__name__)

WORKERS = 10
_driver_lock = threading.Lock()
_chromedriver_path = None


def get_chromedriver_path():
    global _chromedriver_path
    if _chromedriver_path is None:
        _chromedriver_path = ChromeDriverManager().install()
    return _chromedriver_path


def make_driver():
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    opts.add_experimental_option('useAutomationExtension', False)
    opts.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
    service = Service(get_chromedriver_path())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def fetch_and_clean(driver, url):
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'mw-parser-output'))
        )
    except Exception:
        time.sleep(1)

    driver.execute_script("""
        // --- TABBERS (old + new fandom) ---
        document.querySelectorAll(
            '.tabbertab, .wds-tab__content, .fandom-tabber__tab-content, ' +
            '[role="tabpanel"], .tabber-tab-content, .tabber__tab-content'
        ).forEach(el => {
            el.style.setProperty('display', 'block', 'important');
            el.style.setProperty('visibility', 'visible', 'important');
            el.style.setProperty('opacity', '1', 'important');
            el.removeAttribute('hidden');
            el.classList.remove('wds-is-hidden');
        });
        // inject tab title as heading
        document.querySelectorAll('.tabbertab').forEach(el => {
            const title = el.getAttribute('title');
            if (title && !el.querySelector('.injected-tab-title')) {
                const h = document.createElement('h3');
                h.className = 'injected-tab-title';
                h.textContent = title;
                el.insertBefore(h, el.firstChild);
            }
        });

        // --- COLLAPSIBLES (mw + wikia + generic) ---
        document.querySelectorAll(
            '.mw-collapsible, .wikia-collapsible'
        ).forEach(el => el.classList.remove('mw-collapsed'));
        document.querySelectorAll(
            '.mw-collapsible-content, .wikia-collapsible-content, ' +
            '.collapsible-content, .mw-made-collapsible'
        ).forEach(el => {
            el.style.setProperty('display', 'block', 'important');
        });
        document.querySelectorAll('details').forEach(el => el.setAttribute('open', ''));

        // --- FANDOM PORTABLE INFOBOX COLLAPSED GROUPS ---
        document.querySelectorAll(
            '.pi-collapse, .pi-collapse-closed, [data-pi-collapsed]'
        ).forEach(el => {
            el.classList.remove('pi-collapse-closed');
            el.style.setProperty('display', 'block', 'important');
        });

        // --- GENERIC HIDDEN CONTENT inside mw-parser-output ---
        const content = document.querySelector('.mw-parser-output');
        if (content) {
            content.querySelectorAll('*').forEach(el => {
                const cs = window.getComputedStyle(el);
                if (cs.display === 'none' || cs.visibility === 'hidden') {
                    el.style.setProperty('display', 'block', 'important');
                    el.style.setProperty('visibility', 'visible', 'important');
                }
            });
        }
    """)
    time.sleep(0.5)

    soup = BeautifulSoup(driver.page_source, 'html.parser')

    title_el = (soup.find('h1', class_='page-header__title') or
                soup.find('h1', id='firstHeading') or
                soup.find('h1'))
    title = title_el.get_text(strip=True) if title_el else url

    content = soup.find('div', class_='mw-parser-output')
    if not content:
        return None, None

    # skip content-less pages (under 200 chars of real text)
    text_len = len(content.get_text(strip=True))
    if text_len < 200:
        return None, None

    for el in content.select(
        # scripts / styles / media
        'script, style, img, figure, .thumb, noscript, audio, video, .audio, '
        # navigation & TOC
        '.toc, .navbox, .navbox-styles, .navigation-box, '
        # edit links
        '.mw-editsection, '
        # references / footnotes
        '.reference, .references, .reflist, sup, '
        # ads
        '[class*="ad-"], .wikia-ad, '
        # fandom chrome
        '.fandom-sticky-header, .page-header, '
        # galleries
        '.gallery, .wikia-gallery, ul.gallery, div.gallery, .gallerybox, .gallery-caption, '
        # empty mw elements
        '.mw-empty-elt, '
        # discussion tools
        '.ext-discussiontools-init-replylink-buttons, '
        # infobox images
        '.portable-infobox__item--has-images, .pi-image, .pi-image-thumbnail, '
        # stub / maintenance / warning banners
        '.ambox, .tmbox, .cmbox, .ombox, .fmbox, .stub, '
        '.noprint, .mbox-small, [class*="notice"], [class*="banner"], '
        # hatnotes
        '.hatnote, .dablink, .rellink, '
        # spoiler boxes
        '[class*="spoiler"], '
        # message boxes
        '.messagebox, .warningbox, .successbox, '
        # fandom community/social
        '.page-footer, .fandom-community-header'
    ):
        el.decompose()

    # remove "External links" and "See also" sections entirely
    for heading in content.find_all(['h2', 'h3']):
        text = heading.get_text(strip=True).lower()
        if any(x in text for x in ['external link', 'see also', 'further reading', 'references', 'notes']):
            # remove heading + all siblings until next heading
            node = heading.next_sibling
            while node and node.name not in ['h2', 'h3']:
                next_node = node.next_sibling
                if hasattr(node, 'decompose'):
                    node.decompose()
                node = next_node
            heading.decompose()

    # strip links, keep text
    for a in content.find_all('a'):
        a.replace_with(a.get_text())

    # remove empty tags (p, div, span, li) left after cleanup
    for tag in content.find_all(['p', 'div', 'span', 'li', 'ul', 'ol']):
        if not tag.get_text(strip=True):
            tag.decompose()

    cat_el = (soup.find('div', class_='page-footer__category-tags') or
              soup.find('div', id='catlinks') or
              soup.find('div', class_='catlinks'))

    cat_html = ''
    if cat_el:
        cats = [a.get_text(strip=True) for a in cat_el.find_all('a')]
        if cats:
            cat_html = '<div class="categories"><strong>Categories:</strong> ' + ' | '.join(cats) + '</div>'

    return title, str(content) + cat_html


def fetch_url(url):
    driver = make_driver()
    try:
        log.info(f"  Fetching: {url}")
        title, html = fetch_and_clean(driver, url)
        if title is None:
            log.warning(f"  SKIPPED (no content): {url}")
        return url, title, html, None
    except Exception as e:
        log.error(f"  EXCEPTION on {url}: {e}", exc_info=True)
        return url, None, None, str(e)
    finally:
        driver.quit()


def build_html(pages):
    css = """
    body { font-family: Georgia, serif; font-size: 11pt; line-height: 1.6; color: #111; margin: 0; padding: 0; }
    h1.page-title { font-size: 22pt; border-bottom: 2px solid #000; margin-bottom: 4pt; padding-bottom: 4pt; }
    h2 { font-size: 16pt; border-bottom: 1px solid #bbb; margin-top: 18pt; }
    h3 { font-size: 13pt; margin-top: 14pt; }
    h4 { font-size: 11pt; }
    p { margin: 6pt 0; }
    table { border-collapse: collapse; width: 100%; margin: 10pt 0; font-size: 10pt; }
    td, th { border: 1px solid #aaa; padding: 4pt 6pt; vertical-align: top; }
    th { background: #eee; font-weight: bold; }
    ul, ol { margin: 6pt 0 6pt 20pt; }
    .page-source { color: #555; font-size: 9pt; margin-bottom: 20pt; }
    .categories { margin-top: 24pt; padding: 8pt; background: #f5f5f5; border: 1px solid #ccc; font-size: 10pt; }
    .page-block { page-break-before: always; }
    .page-block:first-child { page-break-before: avoid; }
    """

    parts = []
    for i, (title, url, content_html) in enumerate(pages):
        cls = 'page-block' if i > 0 else ''
        parts.append(f"""
        <div class="{cls}">
            <h1 class="page-title">{title}</h1>
            <p class="page-source">Source: {url}</p>
            {content_html}
        </div>
        """)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{css}</style></head>
<body>{''.join(parts)}</body>
</html>"""


def process_txt(txt_path):
    txt_path = Path(txt_path)
    urls = [l.strip() for l in txt_path.read_text(encoding='utf-8').splitlines()
            if l.strip() and not l.startswith('#')]

    if not urls:
        log.warning(f"  No URLs in {txt_path.name}")
        return

    log.info(f"\n[{txt_path.name}] {len(urls)} page(s) — fetching {WORKERS} at a time...")

    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(fetch_url, url): url for url in urls}
        for future in as_completed(futures):
            url, title, html, err = future.result()
            if err:
                log.error(f"  FAILED: {url} — {err}")
            else:
                results[url] = (title, html)

    pages = [(results[u][0], u, results[u][1]) for u in urls if u in results]

    if not pages:
        log.warning("  Nothing fetched.")
        return

    out = txt_path.with_suffix('.pdf')
    log.info(f"  Building PDF...")

    full_html = build_html(pages)
    with tempfile.NamedTemporaryFile('w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(full_html)
        tmp_path = f.name

    pdf_driver = make_driver()
    try:
        pdf_driver.get(f'file:///{tmp_path.replace(os.sep, "/")}')
        time.sleep(1)
        result = pdf_driver.execute_cdp_cmd('Page.printToPDF', {
            'printBackground': True,
            'paperWidth': 8.27,
            'paperHeight': 11.69,
            'marginTop': 0.5,
            'marginBottom': 0.5,
            'marginLeft': 0.5,
            'marginRight': 0.5,
        })
    finally:
        pdf_driver.quit()

    os.unlink(tmp_path)
    out.write_bytes(base64.b64decode(result['data']))
    log.info(f"  Saved: {out.name}")


def main():
    script_dir = Path(__file__).parent
    txt_files = [f for f in sorted(script_dir.glob('*.txt')) if f.name.lower() != 'requirements.txt']

    if not txt_files:
        log.warning("No .txt files found.")
        input("\nPress Enter to exit...")
        return

    log.info(f"Starting ({WORKERS} parallel fetchers)...")
    get_chromedriver_path()

    for f in txt_files:
        process_txt(f)

    log.info("All done!")
    input("\nPress Enter to exit...")


if __name__ == '__main__':
    main()
