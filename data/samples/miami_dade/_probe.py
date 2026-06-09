"""
INVESTIGATION-ONLY probe for Miami-Dade Local Case Search.

Goal: definitively characterize whether automated docket access is blocked,
and if so by what mechanism (reCAPTCHA v2 challenge / v3 score / SPA state /
something else). Does NOT modify any working scraper.

Drives the same nav flow miami_dade.py uses (landing -> Local Case Search ->
fill form -> submit -> results), but at every step it:
  - screenshots full page
  - dumps raw outerHTML
  - scans for reCAPTCHA (site key, g-recaptcha nodes, grecaptcha script,
    window.grecaptcha, enterprise) and reports v2 vs v3
  - enumerates the actual form fields (so we learn the real selectors)

Run:
  HEADED=1 python data/samples/miami_dade/_probe.py        # headed (better v3 score)
  python data/samples/miami_dade/_probe.py                 # headless

Test case: 2017-021344-CA-01  (year=2017, num=021344, type=CA)
"""

import os
import re
import json
import asyncio
from datetime import datetime

from playwright.async_api import async_playwright

OUT = os.path.join(os.path.dirname(__file__))
SITE_KEY = "6Le7np8qAAAAAAEMezDvhuXyKV4EA6BWZTvdK_E6"
LANDING_URL = "https://www2.miamidadeclerk.gov/ocs/"

YEAR, NUM, CTYPE = "2017", "021344", "CA"

REPORT = {"steps": [], "case": "2017-021344-CA-01"}


def log(step, **kw):
    entry = {"step": step, "ts": datetime.now().isoformat(), **kw}
    REPORT["steps"].append(entry)
    print(f"[{step}] " + " ".join(f"{k}={v!r}" for k, v in kw.items()))


async def dump(page, label):
    """Screenshot + raw HTML for a step."""
    png = os.path.join(OUT, f"{label}.png")
    html = os.path.join(OUT, f"{label}.html")
    try:
        await page.screenshot(path=png, full_page=True)
    except Exception as e:
        log(f"{label}-screenshot-fail", err=str(e)[:120])
    try:
        content = await page.content()
        with open(html, "w", encoding="utf-8") as f:
            f.write(content)
        return content
    except Exception as e:
        log(f"{label}-html-fail", err=str(e)[:120])
        return ""


async def scan_recaptcha(page, html, label):
    """Report whether reCAPTCHA is present and which variant."""
    h = html.lower()
    findings = {
        "site_key_in_html": SITE_KEY.lower() in h,
        "g-recaptcha_node": "g-recaptcha" in h,
        "recaptcha_str": "recaptcha" in h,
        "recaptcha_api_script": "recaptcha/api.js" in h or "recaptcha/enterprise.js" in h,
        "enterprise": "enterprise.js" in h or "grecaptcha.enterprise" in h,
        "render_explicit": "render=explicit" in h,
        "render_sitekey_param": bool(re.search(r"render=6l", h)),
    }
    # Live DOM/JS probes
    try:
        findings["window_grecaptcha"] = await page.evaluate(
            "() => !!(window.grecaptcha)"
        )
        findings["window_grecaptcha_enterprise"] = await page.evaluate(
            "() => !!(window.grecaptcha && window.grecaptcha.enterprise)"
        )
        findings["visible_challenge_iframe"] = await page.evaluate(
            "() => !!document.querySelector('iframe[src*=\"recaptcha\"][title*=\"challenge\"], iframe[title=\"recaptcha challenge expires in two minutes\"]')"
        )
        findings["bframe_iframe"] = await page.evaluate(
            "() => !!document.querySelector('iframe[src*=\"bframe\"]')"
        )
        findings["g_recaptcha_badge"] = await page.evaluate(
            "() => !!document.querySelector('.grecaptcha-badge')"
        )
    except Exception as e:
        findings["js_probe_err"] = str(e)[:120]

    # Heuristic verdict
    verdict = "none"
    if findings.get("visible_challenge_iframe") or findings.get("bframe_iframe"):
        verdict = "v2-visible-challenge"
    elif findings.get("g_recaptcha_badge") or findings.get("render_sitekey_param") \
            or findings.get("site_key_in_html"):
        verdict = "v3-or-invisible-score"
    elif findings.get("window_grecaptcha") or findings.get("recaptcha_api_script"):
        verdict = "present-variant-unclear"

    log(f"{label}-recaptcha", verdict=verdict, **{k: v for k, v in findings.items() if v})
    REPORT.setdefault("recaptcha", {})[label] = {"verdict": verdict, "findings": findings}
    return verdict


async def enumerate_form(page, label):
    """Dump every input/select on the page so we learn the real selectors."""
    try:
        fields = await page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('input, select, textarea, button').forEach(el => {
                out.push({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    text: (el.innerText || el.value || '').slice(0, 40),
                    visible: !!(el.offsetParent !== null)
                });
            });
            return out;
        }""")
        REPORT.setdefault("form_fields", {})[label] = fields
        visible = [f for f in fields if f["visible"]]
        log(f"{label}-fields", total=len(fields), visible=len(visible))
        for f in visible[:40]:
            print("    ", json.dumps(f))
        return fields
    except Exception as e:
        log(f"{label}-fields-fail", err=str(e)[:120])
        return []


async def main():
    headed = os.environ.get("HEADED") == "1"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not headed)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            ignore_https_errors=True,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
        )
        page = await context.new_page()
        page.on("response", lambda r: (
            log("net", url=r.url[:120], status=r.status)
            if ("recaptcha" in r.url or "/ocs/" in r.url and r.request.method == "POST")
            else None))

        # ── Step 1: landing ──
        try:
            await page.goto(LANDING_URL, wait_until="load", timeout=45000)
            await page.wait_for_timeout(3000)
            log("landing", url=page.url, title=(await page.title()))
        except Exception as e:
            log("landing-fail", err=str(e)[:200])
        html = await dump(page, "01-landing")
        await scan_recaptcha(page, html, "01-landing")
        await enumerate_form(page, "01-landing")

        # ── Step 2: navigate to Local Case Search ──
        # NEW SPA: nav items are <span role="button">Local Case</span>, NOT <a>.
        clicked = False
        for sel in ["span[role='button']:has-text('Local Case')",
                    "span.subitem-color:has-text('Local Case')",
                    "text='Local Case'",
                    "[role='button']:has-text('Local Case')"]:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.click(timeout=5000)
                clicked = True
                log("click-local-search", selector=sel)
                break
            except Exception as e:
                log("click-try-fail", selector=sel, err=str(e)[:80])
                continue
        if not clicked:
            log("click-local-search-fail", note="no Local Case span matched")
        await page.wait_for_timeout(3000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        log("after-nav", url=page.url)
        html = await dump(page, "02-search-form")
        await scan_recaptcha(page, html, "02-search-form")
        await enumerate_form(page, "02-search-form")

        # ── Step 3: fill form (best-effort, broad selectors) ──
        async def try_fill(selectors, value, kind):
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() == 0:
                        continue
                    tag = await el.evaluate("e => e.tagName.toLowerCase()")
                    if tag == "select":
                        await el.select_option(value=value, timeout=3000)
                    else:
                        await el.click(timeout=2000)
                        await el.type(value, delay=80)  # human-ish typing
                    log(f"fill-{kind}", selector=sel, value=value)
                    return True
                except Exception:
                    continue
            log(f"fill-{kind}-fail", value=value)
            return False

        # Real SPA field names: caseYear, caseSeq, caseCode, caseLocation
        await try_fill(["#caseYear", "select[name='caseYear']"], YEAR, "year")
        await try_fill(["#caseSeq", "input[name='caseSeq']"], NUM, "number")
        await try_fill(["#caseCode", "select[name='caseCode']"], CTYPE, "type")
        # caseLocation may be required and may populate after caseCode is picked
        try:
            await page.wait_for_timeout(1000)
            loc = page.locator("#caseLocation, select[name='caseLocation']").first
            if await loc.count() > 0:
                opts = await loc.evaluate(
                    "el => Array.from(el.options).map(o => ({v:o.value, t:o.text}))")
                log("caseLocation-options", options=opts[:10])
                # pick first non-empty option if one exists
                nonempty = [o for o in opts if o["v"]]
                if nonempty:
                    await loc.select_option(value=nonempty[0]["v"])
                    log("fill-location", value=nonempty[0]["v"])
        except Exception as e:
            log("fill-location-fail", err=str(e)[:120])
        html = await dump(page, "03-form-filled")

        # ── Step 4: submit ──
        submitted = False
        for sel in ["button:has-text('Search')", "input[type='submit']",
                    "button[type='submit']", "button:has-text('Submit')",
                    "a:has-text('Search')"]:
            try:
                await page.click(sel, timeout=3000)
                submitted = True
                log("submit", selector=sel)
                break
            except Exception:
                continue
        if not submitted:
            log("submit-fail", note="no submit control matched")
        await page.wait_for_timeout(6000)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        log("after-submit", url=page.url)
        # Watch a bit longer for SPA result render / async error
        try:
            await page.wait_for_selector(
                "table, .card, [class*='result' i], [class*='error' i], [class*='no-record' i]",
                timeout=10000)
        except Exception:
            log("no-result-container-appeared")
        await page.wait_for_timeout(2000)
        html = await dump(page, "04-results")
        verdict = await scan_recaptcha(page, html, "04-results")
        await enumerate_form(page, "04-results")

        # Did we get docket-ish data back?
        body_text = ""
        try:
            body_text = await page.inner_text("body")
        except Exception:
            pass
        signals = {
            "has_case_number_echo": "021344" in body_text,
            "has_2017_021344": bool(re.search(r"2017[-\s]?021344", body_text)),
            "mentions_docket": "docket" in body_text.lower(),
            "mentions_no_results": bool(re.search(r"no (results|records|cases)", body_text, re.I)),
            "mentions_blocked": bool(re.search(r"blocked|denied|forbidden|unusual traffic|verify you are human", body_text, re.I)),
            "body_len": len(body_text),
        }
        log("results-signals", **signals)
        REPORT["results_signals"] = signals
        REPORT["final_recaptcha_verdict"] = verdict

        await browser.close()

    with open(os.path.join(OUT, "_report.json"), "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=2)
    print("\n==== REPORT written to _report.json ====")
    print(json.dumps({k: REPORT[k] for k in REPORT if k != "steps"}, indent=2)[:3000])


if __name__ == "__main__":
    asyncio.run(main())
