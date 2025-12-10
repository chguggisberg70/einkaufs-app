from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
import os

import requests
from dotenv import load_dotenv
from notion_client import Client, APIResponseError

# .env einlesen
load_dotenv(override=True)


NOTION_API_KEY = os.getenv("NOTION_API_KEY")
DB_TRANSAKT_ID = os.getenv("NOTION_TRANSAKT_DB_ID")
DB_KATEG_ID = os.getenv("NOTION_KATEG_DB_ID")
DB_MONAT_ID = os.getenv("NOTION_MONAT_DB_ID")

if not NOTION_API_KEY or not DB_TRANSAKT_ID:
    raise RuntimeError("Bitte NOTION_API_KEY und NOTION_TRANSAKT_DB_ID in der .env setzen.")

# Notion-Client zum Seiten erstellen
notion = Client(auth=NOTION_API_KEY)

# Direkte HTTP-Aufrufe für Queries
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

http_headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


app = FastAPI()


class Transaction(BaseModel):
    name: str
    betrag: float
    datum: date
    kategorie: Optional[str] = None  # Name aus Dropdown
    monat: Optional[str] = None      # Name aus Dropdown
    notiz: Optional[str] = None


class OptionsResponse(BaseModel):
    kategorien: List[str]
    monate: List[str]


def get_names_from_db(db_id: str) -> List[str]:
    """Hole alle Titel 'Name' aus einer Notion-DB."""
    if not db_id:
        return []

    url = f"{NOTION_API_BASE}/databases/{db_id}/query"
    try:
        resp = requests.post(url, headers=http_headers, json={})
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Fehler beim Lesen von DB {db_id}: {e}")
        return []

    data = resp.json()
    names: List[str] = []
    for page in data.get("results", []):
        prop = page["properties"].get("Name", {})
        title = prop.get("title", [])
        if title:
            names.append(title[0].get("plain_text", ""))
    return names


def find_page_by_name(db_id: str, name: str) -> Optional[str]:
    """Suche eine Seite mit Titel 'Name' == name und gib deren ID zurück."""
    if not db_id or not name:
        return None

    url = f"{NOTION_API_BASE}/databases/{db_id}/query"
    payload = {
        "filter": {
            "property": "Name",
            "title": {"equals": name},
        }
    }

    try:
        resp = requests.post(url, headers=http_headers, json=payload)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Fehler bei Suche in DB {db_id}: {e}")
        return None

    data = resp.json()
    results = data.get("results", [])
    if not results:
        return None
    return results[0]["id"]
def get_lebensmittel_sum_for_month(monat_name: str) -> float:
    """Summe aller Beträge mit Kategorie_Text 'Lebensmittel' für einen Monat."""
    if not monat_name or not DB_TRANSAKT_ID or not DB_MONAT_ID:
        return 0.0

    month_id = find_page_by_name(DB_MONAT_ID, monat_name)
    if not month_id:
        return 0.0

    url = f"{NOTION_API_BASE}/databases/{DB_TRANSAKT_ID}/query"
    payload = {
        "filter": {
            "and": [
                {
                    "property": "Monat",
                    "relation": {"contains": month_id},
                },
                {
                    "property": "Kategorie_Text",
                    "rich_text": {"equals": "Lebensmittel"},
                },
            ]
        }
    }

    try:
        resp = requests.post(url, headers=http_headers, json=payload)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Fehler beim Lesen von Transaktionen: {e}")
        return 0.0

    data = resp.json()
    total = 0.0
    for page in data.get("results", []):
        betrag = page["properties"].get("Betrag", {}).get("number")
        if isinstance(betrag, (int, float)):
            total += betrag

    return total


@app.get("/")
def read_root():
    return {"message": "Einkaufs-App-API läuft ✅"}


@app.get("/options", response_model=OptionsResponse)
def get_options():
    """Liefert Listen mit Kategorien- und Monatsnamen für die Dropdowns."""
    kategorien = get_names_from_db(DB_KATEG_ID)
    monate = get_names_from_db(DB_MONAT_ID)
    return OptionsResponse(kategorien=kategorien, monate=monate)


@app.post("/add")
def add_transaction(tx: Transaction):
    properties = {
        "Name": {
            "title": [{"text": {"content": tx.name}}]
        },
        "Betrag": {
            "number": tx.betrag
        },
        "Datum": {
            "date": {"start": tx.datum.isoformat()}
        },
    }

    # Textfelder
    if tx.kategorie:
        properties["Kategorie_Text"] = {
            "rich_text": [{"text": {"content": tx.kategorie}}]
        }

    if tx.notiz:
        properties["Notiz"] = {
            "rich_text": [{"text": {"content": tx.notiz}}]
        }

    # Relation Kategorien
    if tx.kategorie and DB_KATEG_ID:
        cat_id = find_page_by_name(DB_KATEG_ID, tx.kategorie)
        if cat_id:
            properties["Kategorien"] = {"relation": [{"id": cat_id}]}

    # Relation Monat
    if tx.monat and DB_MONAT_ID:
        month_id = find_page_by_name(DB_MONAT_ID, tx.monat)
        if month_id:
            properties["Monat"] = {"relation": [{"id": month_id}]}

    try:
        notion.pages.create(
            parent={"database_id": DB_TRANSAKT_ID},
            properties=properties,
        )
    except APIResponseError as e:
        raise HTTPException(status_code=500, detail=f"Notion-Fehler: {e}")

    return {"status": "ok"}



from datetime import date
from fastapi.responses import HTMLResponse

@app.get("/formular", response_class=HTMLResponse)
def einkaufs_formular():
    # Optionen holen
    options = get_options()

    today_str = date.today().isoformat()
    default_monat = options.monate[0] if options.monate else ""
    default_kat = "Lebensmittel" if "Lebensmittel" in options.kategorien else (
        options.kategorien[0] if options.kategorien else ""
    )

    # Total Lebensmittel für Default-Monat
    total_leb = get_lebensmittel_sum_for_month(default_monat) if default_monat else 0.0
    total_leb_text = f"{total_leb:0.2f} CHF"

    # Dropdown-HTML bauen
    def build_kat_options():
        html = ""
        for k in options.kategorien:
            selected = " selected" if k == default_kat else ""
            html += f'<option value="{k}"{selected}>{k}</option>'
        return html

    def build_monat_options():
        html = ""
        for m in options.monate:
            selected = " selected" if m == default_monat else ""
            html += f'<option value="{m}"{selected}>{m}</option>'
        return html

    kategorie_options_html = build_kat_options()
    monat_options_html = build_monat_options()

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Einkauf erfassen</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      font-family: -apple-system, system-ui, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      padding: 16px;
      background: #f3f4f6;
    }}
    .container {{
      max-width: 600px;
      margin: 12px auto;
      background: #ffffff;
      padding: 20px 16px 16px 16px;
      border-radius: 18px;
      box-shadow: 0 4px 16px rgba(0,0,0,0.10);
    }}
    h1 {{
      font-size: 24px;
      margin-top: 0;
      margin-bottom: 16px;
      text-align: center;
    }}
    label {{
      display: block;
      margin-top: 14px;
      font-size: 16px;
    }}
    input, select, textarea {{
      width: 100%;
      font-size: 20px;
      padding: 14px 12px;
      margin-top: 6px;
      box-sizing: border-box;
      border-radius: 12px;
      border: 1px solid #d1d5db;
    }}
    textarea {{
      resize: vertical;
      min-height: 70px;
    }}
    button {{
      margin-top: 22px;
      width: 100%;
      font-size: 20px;
      padding: 14px;
      border-radius: 9999px;
      border: none;
      background: #2563eb;
      color: white;
      font-weight: 600;
    }}
    button:active {{
      transform: scale(0.98);
    }}
    #msg {{
      margin-top: 10px;
      font-size: 14px;
      text-align: center;
    }}
    .footer-row {{
      margin-top: 14px;
      font-size: 14px;
      display: flex;
      justify-content: flex-end;
      color: #374151;
    }}
    .footer-row span.sum {{
      font-weight: 600;
      margin-left: 6px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Einkauf erfassen</h1>
    <form id="txForm">
      <label for="name">Name</label>
      <input id="name" name="name" type="text" required>

      <label for="betrag">Betrag</label>
      <input id="betrag" name="betrag" type="number" step="0.01" inputmode="decimal" required>

      <label for="datum">Datum</label>
      <input id="datum" name="datum" type="date" value="{today_str}" required>

      <label for="kategorie">Kategorie</label>
      <select id="kategorie" name="kategorie" required>
        {kategorie_options_html}
      </select>

      <label for="monat">Monat</label>
      <select id="monat" name="monat" required>
        {monat_options_html}
      </select>

      <label for="notiz">Notiz</label>
      <textarea id="notiz" name="notiz" rows="2"></textarea>

      <button type="submit">Speichern</button>
      <p id="msg"></p>

      <div class="footer-row">
        <span>Lebensmittel total {default_monat}:</span>
        <span class="sum">{total_leb_text}</span>
      </div>
    </form>
  </div>

  <script>
    const form = document.getElementById("txForm");
    const msg = document.getElementById("msg");

    form.addEventListener("submit", async (e) => {{
      e.preventDefault();
      msg.textContent = "Speichere...";

      const betragRaw = document.getElementById("betrag").value.trim();
      const betragNorm = betragRaw.replace(",", ".");

      const data = {{
        name: document.getElementById("name").value,
        betrag: parseFloat(betragNorm),
        datum: document.getElementById("datum").value,
        kategorie: document.getElementById("kategorie").value,
        monat: document.getElementById("monat").value,
        notiz: document.getElementById("notiz").value || null,
      }};

      try {{
        const res = await fetch("/add", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(data),
        }});

        if (!res.ok) throw new Error("Fehler beim Speichern");

        msg.textContent = "Gespeichert ✅";

        document.getElementById("name").value = "";
        document.getElementById("betrag").value = "";
        document.getElementById("notiz").value = "";
        document.getElementById("name").focus();
      }} catch (err) {{
        console.error(err);
        msg.textContent = "Fehler beim Speichern ❌";
      }}
    }});
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)
