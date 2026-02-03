import os
from datetime import date
from typing import List, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# -----------------------------------------------------------
# Konfiguration / Notion-Setup
# -----------------------------------------------------------

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
DB_TRANSAKT_ID = os.getenv("NOTION_TRANSAKT_DB_ID")
DB_KATEG_ID = os.getenv("NOTION_KATEG_DB_ID")
DB_MONAT_ID = os.getenv("NOTION_MONAT_DB_ID")

if not NOTION_API_KEY or not DB_TRANSAKT_ID:
    print("Warnung: API Key nicht gefunden. Stelle sicher, dass er in Render gesetzt ist.")

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

http_headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

# -----------------------------------------------------------
# Datenmodelle
# -----------------------------------------------------------

class Transaction(BaseModel):
    name: str
    betrag: float
    datum: str
    kategorie: str
    monat: str
    notiz: Optional[str] = None

class OptionsResponse(BaseModel):
    kategorien: List[str]
    monate: List[str]

# -----------------------------------------------------------
# Hilfsfunktionen für Notion
# -----------------------------------------------------------

def query_database(database_id: str, payload: Optional[dict] = None) -> dict:
    url = f"{NOTION_API_BASE}/databases/{database_id}/query"
    try:
        resp = requests.post(url, headers=http_headers, json=payload or {})
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"Fehler beim Lesen von DB {database_id}: {e}")
        return {"results": []}

def get_names_from_db(database_id: str) -> List[str]:
    if not database_id:
        return []
    data = query_database(database_id)
    names: List[str] = []
    for page in data.get("results", []):
        prop = page.get("properties", {}).get("Name", {})
        title = prop.get("title", [])
        if title:
            names.append(title[0]["plain_text"])
    return names

def find_page_id_by_name(database_id: str, name: str) -> Optional[str]:
    if not name or not database_id:
        return None
    payload = {
        "filter": {
            "property": "Name",
            "title": {"equals": name},
        },
        "page_size": 1,
    }
    data = query_database(database_id, payload)
    results = data.get("results", [])
    if not results:
        return None
    return results[0]["id"]

def get_lebensmittel_sum_for_month(monat_name: str) -> float:
    if not monat_name or not DB_MONAT_ID or not DB_TRANSAKT_ID or not DB_KATEG_ID:
        return 0.0

    month_id = find_page_id_by_name(DB_MONAT_ID, monat_name)
    cat_id = find_page_id_by_name(DB_KATEG_ID, "Lebensmittel")
    if not month_id or not cat_id:
        return 0.0

    payload = {
        "filter": {
            "and": [
                { "property": "Monat", "relation": {"contains": month_id} },
                { "property": "Kategorien", "relation": {"contains": cat_id} },
            ]
        }
    }
    data = query_database(DB_TRANSAKT_ID, payload)
    total = 0.0
    for page in data.get("results", []):
        betrag = page.get("properties", {}).get("Betrag", {}).get("number")
        if isinstance(betrag, (int, float)):
            total += betrag
    return total

def load_options() -> OptionsResponse:
    kategorien = get_names_from_db(DB_KATEG_ID) if DB_KATEG_ID else []
    monate = get_names_from_db(DB_MONAT_ID) if DB_MONAT_ID else []
    return OptionsResponse(kategorien=kategorien, monate=monate)

# -----------------------------------------------------------
# FastAPI-App + Routen
# -----------------------------------------------------------

app = FastAPI(title="Einkaufs-App")

@app.get("/")
def read_root():
    return {"message": "Einkaufs-App-API läuft ✅"}

@app.get("/options", response_model=OptionsResponse)
def get_options():
    return load_options()

@app.get("/lebensmittel_sum")
def lebensmittel_sum(monat: str):
    total = get_lebensmittel_sum_for_month(monat)
    return {"monat": monat, "sum": round(total, 2)}

@app.post("/add")
def add_transaction(tx: Transaction):
    cat_id = find_page_id_by_name(DB_KATEG_ID, tx.kategorie) if DB_KATEG_ID else None
    month_id = find_page_id_by_name(DB_MONAT_ID, tx.monat) if DB_MONAT_ID else None

    properties: dict = {
        "Name": { "title": [{"text": {"content": tx.name}}] },
        "Betrag": {"number": tx.betrag},
        "Datum": {"date": {"start": tx.datum}},
        "Kategorie_Text": { "rich_text": [{"text": {"content": tx.kategorie}}] },
        "Notiz": { "rich_text": [{"text": {"content": tx.notiz}}] if tx.notiz else [] },
    }
    if cat_id:
        properties["Kategorien"] = { "relation": [{"id": cat_id}] }
    if month_id:
        properties["Monat"] = { "relation": [{"id": month_id}] }

    payload = {
        "parent": {"database_id": DB_TRANSAKT_ID},
        "properties": properties,
    }

    try:
        resp = requests.post(
            f"{NOTION_API_BASE}/pages",
            headers=http_headers,
            json=payload,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Fehler beim Schreiben in Notion: {e}")
        raise HTTPException(status_code=500, detail="Fehler beim Speichern in Notion")

    return {"status": "ok"}

# -----------------------------------------------------------
# HTML-Formular
# -----------------------------------------------------------

@app.get("/formular", response_class=HTMLResponse)
def einkaufs_formular():
    options = load_options()
    today_str = date.today().isoformat()
    
    # 1. Deine Gruppen-Logik definieren
    gruppen_mapping = {
        "Haushalt & Alltag": ["Lebensmittel", "Shopping", "Restaurant"],
        "Fixkosten & Wohnen": ["Wohnen Nebenkosten", "Serafe", "Steuern Bund"],
        "Versicherungen": ["Versicherungen Mobi Haus", "Versicherung GS"],
        "Freizeit & Reisen": ["Reisen", "Freizeit"]
    }

    # 2. HTML für das Dropdown bauen
    kategorie_options_html = ""
    verarbeitete_kats = set()

    # Zuerst die definierten Gruppen abarbeiten
    for gruppe, kats in gruppen_mapping.items():
        # Nur Gruppe anzeigen, wenn mindestens eine Kategorie daraus existiert
        vorhandene_in_gruppe = [k for k in kats if k in options.kategorien]
        if vorhandene_in_gruppe:
            kategorie_options_html += f'<optgroup label="--- {gruppe} ---">'
            for kat in vorhandene_in_gruppe:
                # "Lebensmittel" als Standard vorauswählen, falls gewünscht
                selected = ' selected' if kat == "Lebensmittel" else ""
                kategorie_options_html += f'<option value="{kat}"{selected}>{kat}</option>'
                verarbeitete_kats.add(kat)
            kategorie_options_html += '</optgroup>'

    # Den Rest unter "Diverses" alphabetisch sortiert anhängen
    restliche = sorted([k for k in options.kategorien if k not in verarbeitete_kats])
    if restliche:
        kategorie_options_html += '<optgroup label="--- Diverses ---">'
        for kat in restliche:
            kategorie_options_html += f'<option value="{kat}">{kat}</option>'
        kategorie_options_html += '</optgroup>'

    monat_options_html = "".join(f'<option value="{m}">{m}</option>' for m in options.monate)

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Einkauf erfassen</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes">
  <style>
    :root {{
      --primary-color: #2563eb;
      --bg-color: #f3f4f6;
      --card-bg: #ffffff;
      --text-color: #1f2937;
    }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background-color: var(--bg-color);
      color: var(--text-color);
      margin: 0;
      padding: 20px;
      font-size: 1.1rem; 
      line-height: 1.5;
    }}

    .container {{
      max-width: 600px;
      margin: 0 auto;
      background: var(--card-bg);
      padding: 24px;
      border-radius: 16px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }}

    h1 {{
      text-align: center;
      margin-top: 0;
      font-size: 1.5rem;
      color: var(--primary-color);
    }}

    label {{
      display: block;
      margin-top: 16px;
      margin-bottom: 6px;
      font-weight: 600;
      font-size: 1rem;
    }}

    input, select, textarea {{
      width: 100%;
      padding: 14px;
      border: 1px solid #d1d5db;
      border-radius: 10px;
      background: #fff;
      font-size: 1.1rem; 
      box-sizing: border-box;
      -webkit-appearance: none;
    }}

    optgroup {{
        font-weight: bold;
        font-style: normal;
        color: #666;
        background-color: #eee;
    }}

    .hidden-field {{ display: none; }}

    button {{
      width: 100%;
      margin-top: 24px;
      padding: 16px;
      background-color: var(--primary-color);
      color: white;
      font-size: 1.2rem;
      font-weight: bold;
      border: none;
      border-radius: 12px;
      cursor: pointer;
      box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }}

    button:active {{ transform: scale(0.98); background-color: #1d4ed8; }}

    #msg {{ text-align: center; font-weight: bold; margin-top: 16px; min-height: 1.5em; }}

    .footer-row {{
      margin-top: 20px;
      padding-top: 16px;
      border-top: 1px solid #e5e7eb;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 1rem;
    }}

    .sum {{ font-weight: bold; color: var(--primary-color); font-size: 1.2rem; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Einkauf erfassen</h1>
    <form id="txForm">
      <label for="name">Was?</label>
      <input id="name" name="name" type="text" placeholder="z.B. Migros" required>

      <label for="betrag">Betrag (CHF)</label>
      <input id="betrag" name="betrag" type="number" step="0.01" inputmode="decimal" placeholder="0.00" required>

      <label for="datum">Datum</label>
      <input id="datum" name="datum" type="date" value="{today_str}" required>

      <label for="kategorie">Kategorie</label>
      <select id="kategorie" name="kategorie" required>
        {kategorie_options_html}
      </select>

      <div class="hidden-field">
          <select id="monat" name="monat">
          {monat_options_html}
          </select>
      </div>

      <label for="notiz">Notiz (Optional)</label>
      <textarea id="notiz" name="notiz" rows="2" placeholder="Details..."></textarea>

      <button type="submit">Speichern</button>
      <p id="msg"></p>

      <div class="footer-row">
        <span>Total Lebensmittel <br><small id="monatLabel">...</small>:</span>
        <span class="sum" id="lebTotal">...</span>
      </div>
    </form>
  </div>

  <script>
    const form = document.getElementById("txForm");
    const msg = document.getElementById("msg");
    const monatSelect = document.getElementById("monat");
    const dateInput = document.getElementById("datum");
    const lebTotalSpan = document.getElementById("lebTotal");
    const monatLabel = document.getElementById("monatLabel");

    const monthNames = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];

    function autoSelectMonth() {{
        const dateVal = dateInput.value; 
        if(!dateVal) return;
        const d = new Date(dateVal);
        const targetString = monthNames[d.getMonth()] + " " + String(d.getFullYear()).slice(-2);
        
        for (let i = 0; i < monatSelect.options.length; i++) {{
            if (monatSelect.options[i].text === targetString) {{
                monatSelect.selectedIndex = i;
                updateTotal();
                return;
            }}
        }}
        monatLabel.textContent = targetString + " (?)";
        lebTotalSpan.textContent = "—";
    }}

    async function updateTotal() {{
      const monat = monatSelect.value;
      monatLabel.textContent = "(" + monat + ")";
      try {{
        const res = await fetch("/lebensmittel_sum?monat=" + encodeURIComponent(monat));
        const data = await res.json();
        lebTotalSpan.textContent = (data.sum || 0).toFixed(2);
      }} catch (err) {{ lebTotalSpan.textContent = "—"; }}
    }}

    dateInput.addEventListener("change", autoSelectMonth);
    document.addEventListener("DOMContentLoaded", autoSelectMonth);

    form.addEventListener("submit", async (e) => {{
      e.preventDefault();
      msg.textContent = "Speichere...";
      msg.style.color = "black";
      const data = {{
        name: document.getElementById("name").value,
        betrag: parseFloat(document.getElementById("betrag").value.replace(",", ".")),
        datum: document.getElementById("datum").value,
        kategorie: document.getElementById("kategorie").value,
        monat: monatSelect.value,
        notiz: document.getElementById("notiz").value || null,
      }};
      try {{
        const res = await fetch("/add", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(data),
        }});
        if (!res.ok) throw new Error();
        msg.textContent = "Gespeichert ✅";
        msg.style.color = "green";
        document.getElementById("name").value = "";
        document.getElementById("betrag").value = "";
        document.getElementById("notiz").value = "";
        updateTotal();
      }} catch (err) {{
        msg.textContent = "Fehler beim Speichern ❌";
        msg.style.color = "red";
      }}
    }});
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)
