"""The correction page in a real browser (Chromium via Playwright): what it shows and does —
search, exact search, cards (badges, notes, buttons), save, undo, errors, recent suggestions —
in light and dark mode. The look is compared through the computed style of every element
(screenshots were not repeatable to the pixel)."""
import json

from s12_editor_api import CACHE, PATCH, free_port, make_map

FAKE = ["--fake", "--no-update"]
PROPS = ["display", "color", "background-color", "font-family", "font-size", "font-weight",
         "line-height", "margin", "padding", "border", "border-radius", "width", "max-width",
         "min-height", "white-space", "cursor", "opacity", "flex-wrap", "gap", "vertical-align"]
STYLES = """() => [...document.body.querySelectorAll("*")].map(e => {
  const cs = getComputedStyle(e);
  return [e.tagName, e.className, ...%s.map(p => cs.getPropertyValue(p))];
})""" % json.dumps(PROPS)


def state(page, shots, name, racy=False):
    """What the page shows. racy: the card was built from a text read while the addon was still
    saving it (also with the real addon), so its textarea and the picture are left out."""
    page.wait_for_timeout(400)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    if not racy:
        shots[name] = page.evaluate(STYLES)
    return {
        "text": page.inner_text("body"),
        "query": page.input_value("#q"),
        "exact": page.is_checked("#exact"),
        "textareas": None if racy else [t.input_value() for t in page.query_selector_all("textarea")],
        "buttons": [[b.inner_text(), b.is_disabled(), b.get_attribute("title"), b.get_attribute("class")]
                    for b in page.query_selector_all("button")],
        "badges": [[s.get_attribute("class"), s.get_attribute("title"), s.inner_text()]
                   for s in page.query_selector_all(".badge")],
        "status": [[s.get_attribute("class"), s.inner_text()] for s in page.query_selector_all(".status")],
    }


def run(ctx):
    from playwright.sync_api import sync_playwright
    out = {}
    for scheme in ("light", "dark"):
        port = free_port()
        a = ctx.addon(args=[*FAKE, "--editor-port", str(port)], cache_rows=CACHE,
                      files={"IT/patch_it.json": PATCH,
                             "IT/contributor.json": json.dumps({"author": "Ann"})})
        make_map(a)
        a.start()
        a.wait_handshake()
        a.wait_log("editor page")
        a.settle(1.0)
        steps, shots, console = {}, {}, []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 900, "height": 700}, color_scheme=scheme)
            page.on("console", lambda m: console.append([m.type, m.text]))
            page.on("pageerror", lambda e: console.append(["pageerror", str(e)]))
            page.goto(f"http://127.0.0.1:{port}/")
            steps["start"] = state(page, shots, "start")
            page.fill("#q", "Ciao")
            page.press("#q", "Enter")
            page.wait_for_selector(".card")
            steps["search"] = state(page, shots, "search")
            page.fill("textarea", "Salve")
            page.click("button:text-is(\"Salva\")")
            page.wait_for_selector("text=Salvata, inviata al gioco")
            a.settle(0.8)
            steps["saved"] = state(page, shots, "saved")
            page.click("button:text-is(\"Annulla\")")
            page.wait_for_selector("text=Annullata, ripristinato il testo precedente")
            a.settle(0.8)
            steps["undone"] = state(page, shots, "undone", racy=True)
            page.fill("#q", "Ciao")
            page.press("#q", "Enter")
            page.wait_for_timeout(600)
            steps["undone_settled"] = state(page, shots, "undone_settled")
            page.fill("textarea", "   ")
            page.click("button:text-is(\"Salva\")")
            page.wait_for_selector(".status.err")
            steps["error"] = state(page, shots, "error")
            page.fill("#q", "Patch")
            page.press("#q", "Enter")
            page.wait_for_selector(".card")
            page.check("#exact")
            page.wait_for_timeout(500)
            steps["exact_none"] = state(page, shots, "exact_none")
            page.uncheck("#exact")
            page.fill("#q", "3")
            page.press("#q", "Enter")
            page.wait_for_selector(".card")
            page.fill("textarea", "Tre")
            page.click("button:text-is(\"Salva\")")
            page.wait_for_selector("text=Salvata, inviata al gioco")
            a.send(3, 1234, "Patch text, changed English")  # the suggestion no longer valid
            a.settle(1.0)
            page.fill("#q", "3")
            page.press("#q", "Enter")
            page.wait_for_timeout(600)
            steps["superseded"] = state(page, shots, "superseded")
            page.click(".recent .card >> nth=0")
            page.wait_for_timeout(600)
            steps["recent_click"] = state(page, shots, "recent_click")
            page.fill("#q", "zzz nothing")
            page.press("#q", "Enter")
            page.wait_for_timeout(600)
            steps["no_results"] = state(page, shots, "no_results")
            browser.close()
        a.close()
        out[scheme] = {"steps": steps, "styles": shots, "console": console}
    return json.loads(json.dumps(out).replace(str(port), "<port>"))
