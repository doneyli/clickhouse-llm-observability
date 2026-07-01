"""Headless capture of the Langfuse Claude-vs-GPT Compare view (cost + quality).
Logs in, opens the compare deep link, screenshots the table and the Charts tab.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import config  # loads real-estate .env
from playwright.sync_api import sync_playwright

HOST = config.LANGFUSE_HOST
EMAIL = "demo@example.com"
PW = "demodemo1!"
PROJ = "cmr22wcoe0003mv06l3ua15cl"
DS = "cmr23ro4f0009mv06a3coxj0t"
CLAUDE = "bd1eebb5-3263-4bba-ac0b-c6ed27e1b512"
GPT = "d19059cd-40dc-4e4c-b5d0-760a8623eccc"
COMPARE = f"{HOST}/project/{PROJ}/datasets/{DS}/compare?runs={CLAUDE}&runs={GPT}"
OUT = Path(__file__).resolve().parent.parent

def fill_first(page, selectors, value):
    for s in selectors:
        el = page.locator(s)
        if el.count():
            el.first.fill(value); return True
    return False

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1680, "height": 1050})
    pg.goto(f"{HOST}/auth/sign-in", wait_until="networkidle")
    pg.wait_for_timeout(1200)
    em = pg.locator('input[name="email"], input[type="email"]').first
    pwd = pg.locator('input[name="password"], input[type="password"]').first
    em.click(); em.type(EMAIL, delay=20)
    pwd.click(); pwd.type(PW, delay=20)
    pg.wait_for_selector('button[type="submit"]:not([disabled])', timeout=8000)
    pg.locator('button[type="submit"]').first.click()
    try:
        pg.wait_for_url(lambda u: "sign-in" not in u, timeout=15000)
    except Exception:
        pass
    pg.wait_for_timeout(2000)
    print("after login url:", pg.url)

    pg.goto(COMPARE, wait_until="networkidle")
    pg.wait_for_timeout(6000)   # let SPA + run aggregates render
    print("compare url:", pg.url, "| title:", pg.title())
    body = pg.inner_text("body")
    for kw in ("Cost", "cost", "gpt-4o", "claude-sonnet-4-6", "$"):
        print(f"  contains {kw!r}: {kw in body}")
    pg.screenshot(path=str(OUT / "compare-table.png"), full_page=True)
    print("saved compare-table.png")

    # Try to open a "Charts" tab/toggle
    clicked = False
    for s in ['button:has-text("Charts")', 'a:has-text("Charts")', '[role="tab"]:has-text("Charts")',
              'text=Charts']:
        loc = pg.locator(s)
        if loc.count():
            try:
                loc.first.click(); clicked = True; break
            except Exception as e:
                print("charts click err", e)
    print("charts tab clicked:", clicked)
    if clicked:
        pg.wait_for_timeout(5000)
        pg.screenshot(path=str(OUT / "compare-charts.png"), full_page=True)
        print("saved compare-charts.png")
    b.close()
