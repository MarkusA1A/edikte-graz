#!/usr/bin/env python3
"""
Ediktsdatei (edikte.justiz.gv.at) - Versteigerungssuche für die Steiermark
=============================================================================

Sucht gerichtliche Versteigerungen (Zwangsversteigerungen von Liegenschaften)
in der Steiermark und erzeugt einen optisch ansprechenden HTML-Report, der
den Raum Graz / Weiz / Kumberg hervorhebt.

Der Report ist eine kleine "App": alle Steiermark-Treffer sind eingebettet,
im Browser gibt es ein Live-Suchfeld und einen Umschalter zwischen
"nur Raum Graz/Weiz/Kumberg" und "alle Steiermark".

Nutzung:
    python edikte_search.py                 # erzeugt versteigerungen_relevant.html
    python edikte_search.py --out public/index.html --no-open   # für CI/Pages
    python edikte_search.py --debug         # zeigt die Formularfelder

Installation:
    pip install requests beautifulsoup4 lxml
"""

import re
import os
import sys
import argparse
import html as html_lib
import webbrowser
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Vienna")
except Exception:  # pragma: no cover - Fallback ohne tzdata
    _TZ = timezone(timedelta(hours=1))
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE = "https://edikte.justiz.gv.at"
SEARCH_URL = f"{BASE}/edikte/ex/exedi3.nsf/suche!OpenForm&subf=eex"

# ---------------------------------------------------------------------------
# Relevanzkriterien - hier anpassen, um das Interessensgebiet zu ändern.
# ---------------------------------------------------------------------------
RELEVANT_KEYWORDS = [
    "graz", "weiz", "kumberg", "gratkorn", "gratwein", "judendorf",
    "semriach", "st. radegund", "übelbach", "stattegg", "andritz",
    "gleisdorf", "hausmannstätten", "kainbach",
]
# PLZ-Präfixe (erste 3 Ziffern) für Raum Graz
RELEVANT_PLZ_PREFIXES = ["800", "801", "802", "803", "804", "805", "806", "807"]
RELEVANT_PLZ_EXACT = ["8160", "8062", "8063"]  # Weiz, Kumberg-Umgebung

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
})


# ---------------------------------------------------------------------------
# Suchformular finden und absenden
# ---------------------------------------------------------------------------
def pick_search_form(soup):
    """Die Suchseite enthält mehrere <form>-Elemente; das erste ist nur ein
    leerer Wrapper. Wir wählen gezielt das echte Suchformular (mit Select)."""
    forms = soup.find_all("form")
    if not forms:
        raise RuntimeError(
            "Kein <form> auf der Suchseite gefunden - Seitenstruktur hat sich "
            "vermutlich geändert."
        )
    for form in forms:
        if form.find("select"):
            return form
    for form in forms:
        if "submitSuche" in (form.get("action") or ""):
            return form
    return max(forms, key=lambda f: len(f.find_all(["input", "select", "textarea"])))


def get_search_form():
    resp = session.get(SEARCH_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    return soup, pick_search_form(soup)


def describe_form(form):
    """Debug-Hilfe: zeigt alle Feldnamen und deren mögliche Werte an."""
    print("=== Formularfelder auf der Suchseite ===")
    print(f"Action: {form.get('action', '(keine)')}")
    print(f"Method: {form.get('method', 'GET')}")
    for inp in form.find_all(["input", "select", "textarea"]):
        name = inp.get("name")
        tag = inp.name
        if tag == "select":
            print(f"  SELECT name={name!r}")
            for o in inp.find_all("option"):
                print(f"      value={o.get('value')!r:20} text={o.text.strip()!r}")
        else:
            print(f"  {tag.upper():10} name={name!r:28} "
                  f"value={inp.get('value')!r:18} type={inp.get('type')!r}")


def find_select_value(form, option_text_hint):
    """Liefert (feldname, value) des Selects, dessen Option den Text enthält."""
    for sel in form.find_all("select"):
        for opt in sel.find_all("option"):
            if option_text_hint.lower() in opt.text.strip().lower():
                return sel.get("name"), opt.get("value", opt.text.strip())
    return None, None


def submit_search(form, bundesland="Steiermark"):
    action_url = urljoin(SEARCH_URL, form.get("action") or SEARCH_URL)
    method = (form.get("method") or "GET").upper()

    data = {}
    # 1) Eingabefelder mit Standardwerten (Buttons vorerst überspringen).
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("checkbox", "radio"):
            if inp.has_attr("checked"):
                data[name] = inp.get("value", "on")
        elif itype in ("submit", "button", "image", "reset"):
            continue
        else:
            data[name] = inp.get("value", "")

    # 2) Dropdowns mit Standardauswahl.
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        chosen = sel.find("option", selected=True) or sel.find("option")
        if chosen is not None:
            data[name] = chosen.get("value", chosen.get_text(strip=True))

    # 3) Bundesland gezielt setzen (Fallback: BL=5 = Steiermark).
    field_name, field_value = find_select_value(form, bundesland)
    if field_name:
        data[field_name] = field_value
        print(f"Bundesland-Feld erkannt: {field_name} = {field_value}")
    else:
        data["BL"] = "5"
        print("Bundesland nicht erkannt - Fallback BL=5 (Steiermark).")

    # 4) "Suchen"-Button mitsenden, damit der Domino-Agent die Suche ausführt.
    for btn in form.find_all("input", attrs={"type": "submit"}):
        if btn.get("name") in ("sebut", "sebuthide"):
            data[btn["name"]] = btn.get("value", "Suchen")

    if method == "POST":
        resp = session.post(action_url, data=data, timeout=30)
    else:
        resp = session.get(action_url, params=data, timeout=30)
    resp.raise_for_status()
    return resp.text, resp.url


def parse_results(html, base_url):
    """Liest die Ergebnistabelle aus. Jede Zeile enthält bereits alles Nötige.

    Spalten: [0] Laufnr | [1] Edikt+Datum | [2] Adresse+Kategorie | [3] Objekt.
    Detaillinks: 'alldoc/<UNID>!OpenDocument' (relativ -> mit base_url auflösen)."""
    soup = BeautifulSoup(html, "lxml")
    results, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "!OpenDocument" not in href or not re.search(r"[0-9A-Fa-f]{20,}", href):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        row = a.find_parent("tr")
        cols = []
        if row:
            for td in row.find_all("td"):
                cols.append(re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip())
        results.append({
            "edikt":   cols[1] if len(cols) > 1 else a.get_text(strip=True),
            "adresse": cols[2] if len(cols) > 2 else "",
            "objekt":  cols[3] if len(cols) > 3 else "",
            "url":     url,
        })
    return results


def is_relevant(text):
    low = text.lower()
    for kw in RELEVANT_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", low):
            return True
    for plz in RELEVANT_PLZ_EXACT:
        if plz in low:
            return True
    for prefix in RELEVANT_PLZ_PREFIXES:
        if re.search(r"\b" + prefix + r"\d\b", low):
            return True
    return False


# ---------------------------------------------------------------------------
# Aufbereitung / HTML-Report
# ---------------------------------------------------------------------------
KATEGORIEN = sorted([
    "Einfamilienhaus", "Zweifamilienhaus", "Mehrfamilienhaus", "Mietwohnhaus",
    "Mietshaus", "gemischt genutztes Haus", "Reihenhaus", "Hausanteil",
    "Wohnungseigentumsobjekt", "Eigentumswohnung", "Maisonette",
    "Dachterrassenwohnung", "Dachgeschoßwohnung", "Garconniere",
    "Gartenwohnung", "unbebaute Liegenschaft", "bebaubare Liegenschaft",
    "land- und forstwirtschaftlich genutzte Liegenschaft",
    "gewerbliche Liegenschaft", "Superädifikat", "Baurecht", "Sonstiges",
], key=len, reverse=True)


def split_adresse(adresse):
    """Zerlegt 'PLZ Ort Straße Kategorie' in (plz, ort_strasse, kategorie)."""
    rest = adresse.strip()
    kategorie = ""
    for kat in KATEGORIEN:
        if rest.endswith(kat):
            kategorie = kat
            rest = rest[: -len(kat)].strip()
            break
    plz = ""
    m = re.match(r"^(\d{4})\s+(.*)$", rest)
    if m:
        plz, rest = m.group(1), m.group(2)
    return plz, rest, kategorie


def _row_html(r, esc):
    plz, ort_strasse, kategorie = split_adresse(r["adresse"])
    dates = re.findall(r"\d{2}\.\d{2}\.\d{4}", r["edikt"])
    datum = dates[-1] if dates else ""
    typ = r["edikt"].split("(")[0].strip() or "Versteigerung"
    kat_badge = f'<span class="badge kat">{esc(kategorie)}</span>' if kategorie else ""
    search_blob = esc(" ".join([r["adresse"], r["objekt"], r["edikt"]]).lower())
    rel = "1" if r.get("relevant") else "0"
    return f"""
    <tr data-relevant="{rel}" data-search="{search_blob}">
      <td class="plz" data-label="PLZ">{esc(plz) or "&ndash;"}</td>
      <td class="ort" data-label="Ort &amp; Adresse">
        <div class="ort-name">{esc(ort_strasse) or esc(r["adresse"])}</div>
        {kat_badge}
      </td>
      <td class="objekt" data-label="Objekt">{esc(r["objekt"]) or "&ndash;"}</td>
      <td class="termin" data-label="Termin">
        <span class="typ">{esc(typ)}</span>
        <span class="datum">{esc(datum)}</span>
      </td>
      <td class="aktion">
        <a class="btn" href="{esc(r["url"])}" target="_blank" rel="noopener">Edikt&nbsp;&rsaquo;</a>
      </td>
    </tr>"""


def write_html_report(results, out_file, bundesland="Steiermark"):
    """Erzeugt eine in sich geschlossene HTML-Seite mit Live-Filter."""
    esc = html_lib.escape
    # Zeitstempel in österreichischer Zeit (MEZ/MESZ automatisch).
    stand = datetime.now(timezone.utc).astimezone(_TZ)
    stand_str = stand.strftime("%d.%m.%Y, %H:%M Uhr")

    relevant = [r for r in results if r.get("relevant")]
    rows = "".join(_row_html(r, esc)
                   for r in sorted(results, key=lambda x: x["adresse"]))

    doc = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Versteigerungen {esc(bundesland)} &ndash; Raum Graz/Weiz/Kumberg</title>
<style>
  :root {{
    --bg:#f4f6f8; --card:#fff; --ink:#1c2733; --muted:#64748b; --line:#e2e8f0;
    --accent:#2f6f4f; --accent-soft:#e7f2ec;
    --shadow:0 1px 3px rgba(16,24,40,.06),0 8px 24px rgba(16,24,40,.06);
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);line-height:1.45;padding:32px 16px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
  .wrap{{max-width:1080px;margin:0 auto}}
  header.top{{background:linear-gradient(135deg,#2f6f4f,#3f8b64);color:#fff;
    border-radius:16px;padding:28px 28px 24px;box-shadow:var(--shadow)}}
  header.top h1{{margin:0 0 6px;font-size:1.55rem;letter-spacing:-.01em}}
  header.top p{{margin:0;opacity:.9;font-size:.95rem}}
  .stats{{display:flex;flex-wrap:wrap;gap:12px;margin-top:20px}}
  .stat{{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);
    border-radius:12px;padding:10px 16px}}
  .stat .num{{font-size:1.5rem;font-weight:700;display:block}}
  .stat .lbl{{font-size:.78rem;opacity:.9;text-transform:uppercase;letter-spacing:.04em}}
  .controls{{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:22px 2px 0}}
  .controls input[type=search]{{flex:1;min-width:220px;padding:11px 14px;border:1px solid var(--line);
    border-radius:12px;font-size:.95rem;background:#fff;color:var(--ink)}}
  .toggle{{display:inline-flex;background:#fff;border:1px solid var(--line);border-radius:12px;
    overflow:hidden}}
  .toggle button{{border:0;background:#fff;color:var(--muted);padding:10px 14px;font-size:.85rem;
    font-weight:600;cursor:pointer}}
  .toggle button.active{{background:var(--accent);color:#fff}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:16px;
    box-shadow:var(--shadow);margin-top:16px;overflow:hidden}}
  .table-scroll{{overflow-x:auto}}
  table{{width:100%;border-collapse:collapse;font-size:.93rem}}
  thead th{{background:var(--accent-soft);color:#234;text-align:left;font-weight:600;
    font-size:.76rem;text-transform:uppercase;letter-spacing:.05em;padding:12px 16px;
    position:sticky;top:0;border-bottom:1px solid var(--line);white-space:nowrap}}
  tbody td{{padding:14px 16px;border-bottom:1px solid var(--line);vertical-align:top}}
  tbody tr:last-child td{{border-bottom:none}}
  tbody tr:hover{{background:#f8fafb}}
  .plz{{font-variant-numeric:tabular-nums;font-weight:700;color:var(--accent);white-space:nowrap}}
  .ort-name{{font-weight:600}}
  .objekt{{color:var(--muted)}}
  .badge{{display:inline-block;margin-top:6px;padding:2px 9px;border-radius:999px;font-size:.72rem;
    font-weight:600;background:var(--accent-soft);color:var(--accent);border:1px solid #cfe6da}}
  .termin .typ{{display:block;font-weight:600}}
  .termin .datum{{color:var(--muted);font-variant-numeric:tabular-nums}}
  .btn{{display:inline-block;text-decoration:none;white-space:nowrap;background:var(--accent);
    color:#fff;padding:8px 14px;border-radius:10px;font-weight:700;font-size:.85rem}}
  .btn:hover{{background:#255a40}}
  .empty{{padding:28px;text-align:center;color:var(--muted)}}
  footer{{color:var(--muted);font-size:.82rem;margin:22px 4px 0;text-align:center}}
  footer a{{color:var(--accent)}}
  @media (max-width:600px){{
    body{{padding:20px 12px}}
    header.top{{padding:22px 18px}}
    header.top h1{{font-size:1.3rem}}
    .controls{{gap:10px}}
    .toggle{{width:100%}}
    .toggle button{{flex:1}}
    /* Kopfzeile für Screenreader verfügbar lassen, aber visuell ausblenden */
    thead{{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}}
    .table-scroll{{overflow:visible}}
    table,tbody,tr,td{{display:block;width:100%}}
    tbody tr{{border:1px solid var(--line);border-radius:14px;margin:14px 10px;background:#fff}}
    tbody tr:hover{{background:#fff}}
    tbody td{{border:none;border-bottom:1px solid var(--line);padding:10px 16px}}
    tbody td:last-child{{border-bottom:none}}
    tbody td::before{{content:attr(data-label);display:block;font-weight:700;color:var(--muted);
      font-size:.66rem;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px}}
    tbody td.aktion::before{{display:none}}
    tbody td.aktion{{padding-top:12px}}
    tbody td.plz{{font-size:1.05rem}}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header class="top">
      <h1>Gerichtliche Versteigerungen &ndash; {esc(bundesland)}</h1>
      <p>Automatisch aktualisiert &middot; Fokus Raum Graz / Weiz / Kumberg</p>
      <div class="stats">
        <div class="stat"><span class="num" id="stat-shown">{len(relevant)}</span>
          <span class="lbl">Angezeigt</span></div>
        <div class="stat"><span class="num">{len(relevant)}</span>
          <span class="lbl">Raum Graz/Weiz</span></div>
        <div class="stat"><span class="num">{len(results)}</span>
          <span class="lbl">Gesamt {esc(bundesland)}</span></div>
        <div class="stat"><span class="num">{stand.strftime("%d.%m.")}</span>
          <span class="lbl">Stand</span></div>
      </div>
    </header>

    <div class="controls">
      <input type="search" id="q" placeholder="Suchen (Ort, Straße, Objekt) …" autocomplete="off">
      <div class="toggle">
        <button id="btn-rel" class="active" type="button">Raum Graz/Weiz/Kumberg</button>
        <button id="btn-all" type="button">Alle Steiermark</button>
      </div>
    </div>

    <div class="card">
      <div class="table-scroll">
      <table>
        <thead>
          <tr><th>PLZ</th><th>Ort &amp; Adresse</th><th>Objekt</th><th>Termin</th><th></th></tr>
        </thead>
        <tbody id="tbody">{rows}
        </tbody>
      </table>
      </div>
      <p class="empty" id="empty" style="display:none">Keine Treffer für die aktuelle Auswahl.</p>
    </div>

    <footer>
      Erstellt am {esc(stand_str)} &middot;
      Quelle: <a href="{esc(SEARCH_URL)}" target="_blank" rel="noopener">edikte.justiz.gv.at</a>
      &middot; Angaben ohne Gewähr
    </footer>
  </div>

<script>
  var rows = Array.prototype.slice.call(document.querySelectorAll('#tbody tr'));
  var q = document.getElementById('q');
  var btnRel = document.getElementById('btn-rel');
  var btnAll = document.getElementById('btn-all');
  var statShown = document.getElementById('stat-shown');
  var emptyMsg = document.getElementById('empty');
  var onlyRelevant = true;

  function apply() {{
    var term = q.value.trim().toLowerCase();
    var shown = 0;
    rows.forEach(function(tr) {{
      var okRel = !onlyRelevant || tr.getAttribute('data-relevant') === '1';
      var okTerm = !term || tr.getAttribute('data-search').indexOf(term) !== -1;
      var vis = okRel && okTerm;
      tr.style.display = vis ? '' : 'none';
      if (vis) shown++;
    }});
    statShown.textContent = shown;
    emptyMsg.style.display = shown ? 'none' : 'block';
  }}

  q.addEventListener('input', apply);
  btnRel.addEventListener('click', function() {{
    onlyRelevant = true; btnRel.classList.add('active'); btnAll.classList.remove('active'); apply();
  }});
  btnAll.addEventListener('click', function() {{
    onlyRelevant = false; btnAll.classList.add('active'); btnRel.classList.remove('active'); apply();
  }});
  apply();
</script>
</body>
</html>"""

    out_dir = os.path.dirname(out_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(doc)


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Ediktsdatei-Versteigerungssuche Steiermark")
    parser.add_argument("--debug", action="store_true",
                        help="zeigt die Formularfelder und beendet sich")
    parser.add_argument("--out", default="versteigerungen_relevant.html",
                        help="Zieldatei für den HTML-Report")
    parser.add_argument("--no-open", action="store_true",
                        help="Report nicht automatisch im Browser öffnen (für CI)")
    args = parser.parse_args()

    print("Lade Suchformular ...")
    soup, form = get_search_form()

    if args.debug:
        describe_form(form)
        return

    print("Sende Suche (Bundesland = Steiermark) ...")
    html, result_url = submit_search(form, bundesland="Steiermark")
    results = parse_results(html, result_url)

    if not results:
        print("Keine Versteigerungen gefunden - erzeuge leeren Report.")

    for r in results:
        r["relevant"] = is_relevant(" ".join([r["adresse"], r["objekt"], r["edikt"]]))
    relevant = [r for r in results if r["relevant"]]
    print(f"{len(results)} Steiermark-Treffer, davon {len(relevant)} im Raum "
          "Graz/Weiz/Kumberg.")

    write_html_report(results, args.out)
    print(f"HTML-Report gespeichert unter: {args.out}")

    if relevant and not args.no_open:
        try:
            webbrowser.open("file://" + os.path.abspath(args.out))
        except Exception:
            pass


if __name__ == "__main__":
    main()
