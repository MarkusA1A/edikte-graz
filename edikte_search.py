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
import time
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

# Edikt-Typen, die NICHT angezeigt werden sollen (z.B. Meistbotsverteilung und
# 'Zuschlag ohne Überbot'). 'Zuschlag mit Überbot' bleibt sichtbar.
EXCLUDE_EDIKT_RE = re.compile(
    r"(meistbot\w*verteilung|zuschlag\s+ohne\s+überbot)", re.I
)


def is_excluded_edikt(r):
    return bool(EXCLUDE_EDIKT_RE.search(r.get("edikt", "")))


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
# Detailseiten: Schätzwert und geringstes Gebot (Ausrufpreis) auslesen
# ---------------------------------------------------------------------------
def format_euro(raw):
    """'21.000,00 EUR' / 'EUR 900,00' -> '€ 21.000' (bzw. '€ 21.000,50' bei Cent).
    Nur Beträge mit Währungsangabe (EUR/€) werden akzeptiert."""
    if not raw or not re.search(r"EUR|€", raw, re.I):
        return ""
    m = re.search(r"(\d[\d.]*)(?:,(\d{1,2}))?", raw)
    if not m:
        return ""
    try:
        ganz = int(m.group(1).replace(".", ""))
    except ValueError:
        return ""
    s = f"{ganz:,}".replace(",", ".")
    if m.group(2) and m.group(2).ljust(2, "0") != "00":
        s += f",{m.group(2)}"
    return "€ " + s


def _value_after(lines, label_re):
    """Wert nach einem Label finden: entweder inline hinter dem Label oder
    auf der nächsten Zeile (so ist die Ediktsdatei aufgebaut)."""
    for i, ln in enumerate(lines):
        m = re.match(label_re, ln, re.I)
        if m:
            after = ln[m.end():].strip(" :\t")
            if after:
                return after
            if i + 1 < len(lines):
                return lines[i + 1]
    return ""


def _besicht_date(segment):
    """Aus einem Textausschnitt Datum (+ Uhrzeit) ziehen: '25.09.2026, 08:00 Uhr'."""
    dm = re.search(r"(\d{2}\.\d{2}\.\d{4})(?:[^\d]{0,12}?(\d{1,2})[:.](\d{2}))?", segment)
    if not dm:
        return ""
    out = dm.group(1)
    if dm.group(2):
        out += f", {int(dm.group(2)):02d}:{dm.group(3)} Uhr"
    return out


def _extract_besichtigung(text):
    """Besichtigungstermin auslesen (nicht immer vorhanden). Er steht meist am
    Ende des Edikts als '"Ort und Zeit der Besichtigung" hinzugefügt: <Datum>,
    <Uhrzeit>'. Rückgabe: 'TT.MM.JJJJ, HH:MM Uhr' oder ''."""
    # 1) Gezielt das amtliche Muster 'Ort und Zeit der Besichtigung'
    m = re.search(r"Ort und Zeit der Besichtigung", text, re.I)
    if m:
        val = _besicht_date(text[m.end(): m.end() + 80])
        if val:
            return val
    # 2) Allgemeiner Fallback: 'Besichtigung' gefolgt von einem Datum in der Nähe
    for m in re.finditer(r"[Bb]esichtigung", text):
        val = _besicht_date(text[m.end(): m.end() + 60])
        if val:
            return val
    return ""


def extract_detail_values(detail_html):
    """Liest Schätzwert, geringstes Gebot und (falls vorhanden) den
    Besichtigungstermin aus einer Edikt-Detailseite.
    Rückgabe: (schätzwert, gebot, besichtigung)."""
    soup = BeautifulSoup(detail_html, "lxml")
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    schaetz = format_euro(_value_after(lines, r"^schätzwert\s*:?"))
    gebot = format_euro(_value_after(lines, r"^geringstes\s+gebot\s*:?"))
    besichtigung = _extract_besichtigung(text)
    return schaetz, gebot, besichtigung


def enrich_with_prices(results, delay=0.2):
    """Holt für jeden Treffer die Detailseite und ergänzt Schätzwert, Gebot
    und Besichtigungstermin. Fehler pro Treffer werden ignoriert."""
    total = len(results)
    for i, r in enumerate(results, 1):
        try:
            resp = session.get(r["url"], timeout=30)
            resp.raise_for_status()
            r["schaetzwert"], r["gebot"], r["besichtigung"] = \
                extract_detail_values(resp.text)
        except Exception as e:
            r["schaetzwert"], r["gebot"], r["besichtigung"] = "", "", ""
            print(f"  [{i}/{total}] Detail-Fehler: {e}")
        if delay:
            time.sleep(delay)


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


WIEN_BEZIRKE = {
    1: "Innere Stadt", 2: "Leopoldstadt", 3: "Landstraße", 4: "Wieden",
    5: "Margareten", 6: "Mariahilf", 7: "Neubau", 8: "Josefstadt",
    9: "Alsergrund", 10: "Favoriten", 11: "Simmering", 12: "Meidling",
    13: "Hietzing", 14: "Penzing", 15: "Rudolfsheim-Fünfhaus", 16: "Ottakring",
    17: "Hernals", 18: "Währing", 19: "Döbling", 20: "Brigittenau",
    21: "Floridsdorf", 22: "Donaustadt", 23: "Liesing",
}


def wien_bezirk(plz):
    """Wiener PLZ '1BBX' -> (Bezirksnummer, Bezirksname). Sonst (99, ...)."""
    if plz and re.fullmatch(r"1\d{3}", plz):
        n = int(plz[1:3])
        if 1 <= n <= 23:
            return n, WIEN_BEZIRKE.get(n, "")
    return 99, "Sonstige / ohne Bezirk"


def _row_html(r, esc, block=None):
    plz, ort_strasse, kategorie = split_adresse(r["adresse"])
    dates = re.findall(r"\d{2}\.\d{2}\.\d{4}", r["edikt"])
    datum = dates[-1] if dates else ""
    typ = r["edikt"].split("(")[0].strip() or "Versteigerung"
    kat_badge = f'<span class="badge kat">{esc(kategorie)}</span>' if kategorie else ""
    search_blob = esc(" ".join([r["adresse"], r["objekt"], r["edikt"]]).lower())
    rel = "1" if r.get("relevant") else "0"
    block_attr = f' data-block="{block}"' if block is not None else ""
    return f"""
    <tr data-relevant="{rel}" data-search="{search_blob}"{block_attr}>
      <td class="plz" data-label="PLZ">{esc(plz) or "&ndash;"}</td>
      <td class="ort" data-label="Ort &amp; Adresse">
        <div class="ort-name">{esc(ort_strasse) or esc(r["adresse"])}</div>
        {kat_badge}
      </td>
      <td class="objekt" data-label="Objekt">{esc(r["objekt"]) or "&ndash;"}</td>
      <td class="termin" data-label="Termin">
        <span class="typ">{esc(typ)}</span>
        <span class="datum">{esc(datum)}</span>
        {f'<span class="besicht">Besichtigung: {esc(r["besichtigung"])}</span>' if r.get("besichtigung") else ""}
      </td>
      <td class="preise" data-label="Schätzwert / Ausrufpreis">
        <span class="preis-schaetz"><span class="pl">Schätzwert</span>{esc(r.get("schaetzwert") or "–")}</span>
        <span class="preis-gebot"><span class="pl">Ausrufpreis</span>{esc(r.get("gebot") or "–")}</span>
      </td>
      <td class="aktion">
        <a class="btn" href="{esc(r["url"])}" target="_blank" rel="noopener">Edikt&nbsp;&rsaquo;</a>
      </td>
    </tr>"""


_THEAD = ("<tr><th>PLZ</th><th>Ort &amp; Adresse</th><th>Objekt</th>"
          "<th>Termin</th><th>Schätzwert / Ausrufpreis</th><th></th></tr>")


def _wien_rows(results, esc):
    """Rendert Wien-Zeilen nach Bezirk gruppiert (mit Zwischenüberschriften
    und abwechselndem Blockhintergrund)."""
    from collections import Counter

    def gk(r):
        plz, _, _ = split_adresse(r["adresse"])
        return wien_bezirk(plz)

    counts = Counter(gk(r)[0] for r in results)
    ordered = sorted(
        results,
        key=lambda r: (gk(r)[0], split_adresse(r["adresse"])[0], r["adresse"]),
    )
    parts, current, block = [], None, 0
    for r in ordered:
        g = gk(r)
        if g != current:
            current = g
            block ^= 1
            num, name = g
            label = f"{num}. Bezirk – {name}" if num <= 23 else name
            parts.append(
                f'<tr class="grouprow" data-block="{block}">'
                f'<td colspan="6">{esc(label)}'
                f'<span class="gr-c">{counts[num]}</span></td></tr>'
            )
        parts.append(_row_html(r, esc, block=block))
    return "".join(parts)


def _render_section(sec, esc):
    """Rendert eine Sektion (Bundesland) mit optionalem Relevanz-Umschalter."""
    results = sec["results"]
    relevant = [r for r in results if r.get("relevant")]
    if sec.get("group") == "wien":
        rows = _wien_rows(results, esc)
    else:
        rows = "".join(_row_html(r, esc)
                       for r in sorted(results, key=lambda x: x["adresse"]))
    toggle = sec.get("toggle")
    onlyrel = "1" if (toggle and sec.get("default_relevant", True)) else "0"
    shown0 = len(relevant) if onlyrel == "1" else len(results)
    toggle_html = ""
    if toggle:
        toggle_html = f"""
      <div class="toggle">
        <button class="active" type="button" data-mode="rel">{esc(sec["relevant_label"])}</button>
        <button type="button" data-mode="all">{esc(sec["all_label"])}</button>
      </div>"""
    subtitle = f'<p class="sec-sub">{esc(sec["subtitle"])}</p>' if sec.get("subtitle") else ""
    empty_txt = esc(sec.get("empty", "Aktuell keine Einträge."))
    return f"""
    <section class="section" data-onlyrel="{onlyrel}">
      <div class="sec-head">
        <div><h2>{esc(sec["title"])}</h2>{subtitle}</div>
        <div class="sec-stats">
          <span class="chip"><b class="shown-count">{shown0}</b>&nbsp;angezeigt</span>
          <span class="chip">{len(results)}&nbsp;gesamt</span>
        </div>
      </div>{toggle_html}
      <div class="card">
        <div class="table-scroll"><table>
          <thead>{_THEAD}</thead>
          <tbody>{rows}
          </tbody>
        </table></div>
        <p class="empty section-empty" style="display:none">{empty_txt}</p>
      </div>
    </section>"""


def write_html_report(sections, out_file):
    """Erzeugt eine in sich geschlossene HTML-Seite mit einer oder mehreren
    Sektionen (Bundesländern) und globaler Live-Suche."""
    esc = html_lib.escape
    # Zeitstempel in österreichischer Zeit (MEZ/MESZ automatisch).
    stand = datetime.now(timezone.utc).astimezone(_TZ)
    stand_str = stand.strftime("%d.%m.%Y, %H:%M Uhr")

    sections_html = "".join(_render_section(s, esc) for s in sections)

    doc = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gerichtliche Versteigerungen &ndash; Steiermark &amp; Wien</title>
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
  header.top .stand-line{{margin-top:10px;display:inline-block;background:rgba(255,255,255,.16);
    border:1px solid rgba(255,255,255,.25);border-radius:999px;padding:6px 14px;
    font-size:.85rem;font-weight:600;opacity:1}}
  .stats{{display:flex;flex-wrap:wrap;gap:12px;margin-top:20px}}
  .stat{{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);
    border-radius:12px;padding:10px 16px}}
  .stat .num{{font-size:1.5rem;font-weight:700;display:block}}
  .stat .lbl{{font-size:.78rem;opacity:.9;text-transform:uppercase;letter-spacing:.04em}}
  .controls{{position:sticky;top:0;z-index:5;display:flex;gap:12px;align-items:center;
    margin:22px 0 0;padding:10px 0;background:var(--bg)}}
  .controls input[type=search]{{flex:1;min-width:0;padding:12px 16px;border:1px solid var(--line);
    border-radius:12px;font-size:.95rem;background:#fff;color:var(--ink)}}
  .section{{margin-top:30px}}
  .sec-head{{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;
    flex-wrap:wrap;margin:0 2px 10px}}
  .sec-head h2{{margin:0;font-size:1.25rem;letter-spacing:-.01em}}
  .sec-sub{{margin:2px 0 0;color:var(--muted);font-size:.9rem}}
  .sec-stats{{display:flex;gap:8px;flex-wrap:wrap}}
  .chip{{background:#fff;border:1px solid var(--line);border-radius:999px;padding:5px 12px;
    font-size:.8rem;color:var(--muted)}}
  .chip b{{color:var(--accent)}}
  .toggle{{display:inline-flex;background:#fff;border:1px solid var(--line);border-radius:12px;
    overflow:hidden;margin:0 2px}}
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
  /* Bezirks-Gruppierung (Wien) */
  tbody tr[data-block="1"] td{{background:#f6faf8}}
  tbody tr.grouprow td{{background:var(--accent);color:#fff;font-weight:700;
    font-size:.82rem;letter-spacing:.02em;padding:9px 16px;position:sticky;left:0}}
  tbody tr.grouprow:hover td{{background:var(--accent)}}
  tbody tr.grouprow .gr-c{{display:inline-block;margin-left:8px;background:rgba(255,255,255,.22);
    border-radius:999px;padding:1px 9px;font-size:.72rem;font-weight:700}}
  .plz{{font-variant-numeric:tabular-nums;font-weight:700;color:var(--accent);white-space:nowrap}}
  .ort-name{{font-weight:600}}
  .objekt{{color:var(--muted)}}
  .badge{{display:inline-block;margin-top:6px;padding:2px 9px;border-radius:999px;font-size:.72rem;
    font-weight:600;background:var(--accent-soft);color:var(--accent);border:1px solid #cfe6da}}
  .termin .typ{{display:block;font-weight:600}}
  .termin .datum{{color:var(--muted);font-variant-numeric:tabular-nums}}
  .termin .besicht{{display:block;margin-top:4px;font-size:.8rem;color:#b06a00;font-weight:600}}
  .preise{{white-space:nowrap;font-variant-numeric:tabular-nums}}
  .preise span{{display:block}}
  .preise .preis-schaetz{{font-weight:700}}
  .preise .preis-gebot{{color:var(--accent);font-weight:600}}
  .preise .pl{{display:none}}
  .btn{{display:inline-block;text-decoration:none;white-space:nowrap;background:var(--accent);
    color:#fff;padding:8px 14px;border-radius:10px;font-weight:700;font-size:.85rem}}
  .btn:hover{{background:#255a40}}
  .empty{{padding:28px;text-align:center;color:var(--muted)}}
  footer{{color:var(--muted);font-size:.82rem;margin:22px 4px 0;text-align:center}}
  footer p{{margin:6px 0}}
  footer a{{color:var(--accent)}}
  footer .disclaimer{{max-width:760px;margin:10px auto;font-size:.75rem;line-height:1.5;opacity:.85}}
  footer .provider{{font-weight:600;color:var(--ink)}}
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
    /* Preise am Handy: Gruppen-Label weg, dafür Einzel-Labels je Betrag */
    tbody td.preise::before{{display:none}}
    .preise .pl{{display:inline-block;min-width:104px;color:var(--muted);
      font-weight:600;font-size:.66rem;text-transform:uppercase;letter-spacing:.05em}}
    .preise .preis-schaetz,.preise .preis-gebot{{display:block;margin:1px 0}}
    /* Bezirks-Überschrift als Balken, nicht als Karte */
    tbody tr.grouprow{{border:none;border-radius:8px;margin:16px 10px 4px;background:transparent}}
    tbody tr.grouprow td{{border-radius:8px;padding:9px 14px}}
    tbody tr.grouprow td::before{{display:none}}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header class="top">
      <h1>Gerichtliche Versteigerungen</h1>
      <p>Steiermark (Raum Graz/Weiz/Kumberg) &amp; Wien</p>
      <p class="stand-line">Datenstand: {esc(stand_str)} &middot; automatische Aktualisierung tgl. gegen 06:00 Uhr</p>
    </header>

    <div class="controls">
      <input type="search" id="q" placeholder="Suchen (Ort, Straße, Objekt) … – filtert beide Sektionen" autocomplete="off">
    </div>

    {sections_html}

    <footer>
      <p>Erstellt am {esc(stand_str)} &middot;
      Quelle: <a href="{esc(SEARCH_URL)}" target="_blank" rel="noopener">edikte.justiz.gv.at</a></p>
      <p>Automatische Aktualisierung täglich gegen 06:00 Uhr</p>
      <p class="disclaimer">Rechtlicher Hinweis: Diese Seite gibt öffentlich zugängliche
      Einträge der Ediktsdatei des österreichischen Bundesministeriums für Justiz
      automatisiert und unverbindlich wieder. Alle Angaben ohne Gewähr auf Richtigkeit,
      Vollständigkeit oder Aktualität; rechtlich maßgeblich sind ausschließlich die
      amtlichen Veröffentlichungen unter edikte.justiz.gv.at. Diese Seite stellt keine
      Rechts-, Anlage- oder Immobilienberatung dar und steht in keiner Verbindung zum
      Bundesministerium für Justiz. Der angezeigte „Ausrufpreis" entspricht dem
      gesetzlichen geringsten Gebot; Schätzwert und Ausrufpreis werden automatisiert
      aus den Detailseiten übernommen und sind ohne Gewähr.</p>
      <p class="provider">Bereitgestellt von Markus O. Thalhamer &middot;
      <a href="https://immobilienwerte.at" target="_blank" rel="noopener">immobilienwerte.at</a></p>
    </footer>
  </div>

<script>
  var q = document.getElementById('q');

  function applyAll() {{
    var term = q.value.trim().toLowerCase();
    document.querySelectorAll('.section').forEach(function(sec) {{
      var onlyRel = sec.getAttribute('data-onlyrel') === '1';
      var shown = 0;
      sec.querySelectorAll('tbody tr').forEach(function(tr) {{
        if (tr.classList.contains('grouprow')) return; // Überschriften separat
        var okRel = !onlyRel || tr.getAttribute('data-relevant') === '1';
        var okTerm = !term || tr.getAttribute('data-search').indexOf(term) !== -1;
        var vis = okRel && okTerm;
        tr.style.display = vis ? '' : 'none';
        if (vis) shown++;
      }});
      // Bezirks-Überschrift nur zeigen, wenn im Block sichtbare Zeilen sind
      sec.querySelectorAll('tr.grouprow').forEach(function(gr) {{
        var any = false, n = gr.nextElementSibling;
        while (n && !n.classList.contains('grouprow')) {{
          if (n.style.display !== 'none') {{ any = true; break; }}
          n = n.nextElementSibling;
        }}
        gr.style.display = any ? '' : 'none';
      }});
      var cnt = sec.querySelector('.shown-count');
      if (cnt) cnt.textContent = shown;
      var em = sec.querySelector('.section-empty');
      if (em) em.style.display = shown ? 'none' : 'block';
    }});
  }}

  q.addEventListener('input', applyAll);
  document.querySelectorAll('.toggle button').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var sec = btn.closest('.section');
      sec.setAttribute('data-onlyrel', btn.getAttribute('data-mode') === 'rel' ? '1' : '0');
      sec.querySelectorAll('.toggle button').forEach(function(b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');
      applyAll();
    }});
  }});
  applyAll();
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
    parser.add_argument("--no-prices", action="store_true",
                        help="Detailseiten nicht abrufen (kein Schätzwert/Ausrufpreis)")
    args = parser.parse_args()

    print("Lade Suchformular ...")
    soup, form = get_search_form()

    if args.debug:
        describe_form(form)
        return

    # --- Sektion 1: Steiermark (Fokus Raum Graz/Weiz/Kumberg) ---
    print("Sende Suche (Bundesland = Steiermark) ...")
    html_s, url_s = submit_search(form, bundesland="Steiermark")
    res_stmk = [r for r in parse_results(html_s, url_s) if not is_excluded_edikt(r)]
    for r in res_stmk:
        r["relevant"] = is_relevant(" ".join([r["adresse"], r["objekt"], r["edikt"]]))
    rel_stmk = [r for r in res_stmk if r["relevant"]]
    print(f"Steiermark: {len(res_stmk)} Treffer, davon {len(rel_stmk)} im Raum "
          "Graz/Weiz/Kumberg.")

    # --- Sektion 2: Wien (alle Bezirke) ---
    print("Sende Suche (Bundesland = Wien) ...")
    html_w, url_w = submit_search(form, bundesland="Wien")
    res_wien = [r for r in parse_results(html_w, url_w) if not is_excluded_edikt(r)]
    for r in res_wien:
        r["relevant"] = True  # in Wien werden alle Treffer angezeigt
    print(f"Wien: {len(res_wien)} Treffer.")

    if not args.no_prices:
        print("Hole Schätzwert/Ausrufpreis aus den Detailseiten (Steiermark) ...")
        enrich_with_prices(res_stmk)
        print("Hole Schätzwert/Ausrufpreis aus den Detailseiten (Wien) ...")
        enrich_with_prices(res_wien)

    sections = [
        {
            "id": "stmk", "title": "Steiermark",
            "subtitle": "Raum Graz / Weiz / Kumberg",
            "results": res_stmk, "toggle": True, "default_relevant": True,
            "relevant_label": "Raum Graz/Weiz/Kumberg", "all_label": "Alle Steiermark",
            "empty": "Aktuell keine Treffer im gewählten Gebiet.",
        },
        {
            "id": "wien", "title": "Wien", "subtitle": "Nach Bezirk gruppiert",
            "results": res_wien, "toggle": False, "group": "wien",
            "empty": "Aktuell keine offenen Versteigerungen in Wien.",
        },
    ]

    write_html_report(sections, args.out)
    print(f"HTML-Report gespeichert unter: {args.out}")

    if (rel_stmk or res_wien) and not args.no_open:
        try:
            webbrowser.open("file://" + os.path.abspath(args.out))
        except Exception:
            pass


if __name__ == "__main__":
    main()
