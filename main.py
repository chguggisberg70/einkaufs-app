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
    raise RuntimeError(
        "Bitte NOTION_API_KEY und NOTION_TRANSAKT_DB_ID als Environment-Variablen setzen."
    )

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
    datum: str  # ISO-String YYYY-MM-DD
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
    """POST /databases/{id}/query mit einfachem Fehler-Handling."""
    url = f"{NOTION_API_BASE}/databases/{database_id}/query"
    try:
        resp = requests.post(url, headers=http_headers, json=payload or {})
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"Fehler beim Lesen von DB {database_id}: {e}")
        # Für Optionen & Summe geben wir im Fehlerfall einfach leere Werte zurück
        return {"results": []}


def get_names_from_db(database_id: str) -> List[str]:
    """Liest alle Seitennamen (Eigenschaft 'Name') aus einer Datenbank."""
    data = query_database(database_id)
    names: List[str] = []
    for page in data.get("results", []):
        prop = page.get("properties", {}).get("Name", {})
        title = prop.get("title", [])
        if title:
            names.append(title[0]["plain_text"])
    return names


def find_page_id_by_name(database_id: str, name: str) -> Optional[str]:
    """Sucht eine Seite per exaktem Name und gibt die ID zurück."""
    if not name:
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
    """
    Summe aller Beträge mit Kategorie_Text 'Lebensmittel'
    und Relation 'Monat' = ausgewählter Monat.
    """
    if not monat_name or not DB_MONAT_ID or not DB_TRANSAKT_ID:
        return 0.0

    month_id = find_page_id_by_name(DB_MONAT_ID, monat_name)
    if not month_id:
        return 0.0

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

    url = f"{NOTION_API_BASE}/databases/{DB_TRANSAKT_ID}/query"
    try:
        resp = requests.post(url, headers=http_headers, json=payload)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Fehler beim Lesen von Transaktionen: {e}")
        return 0.0

    data = resp.json()
    total = 0.0
    for page in data.get("results", []):
        betrag = (
            page.get("properties", {})
            .get("Betrag", {})
            .get("number")
        )
        if isinstance(betrag, (int, float)):
            total += betrag

    return total


def load_options() -> OptionsResponse:
    """Lädt Kategorien- und Monatsnamen aus Notion."""
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
    """Neue Transaktion in Notion anlegen."""
    # passende Kategorie- und Monats-Seite finden (für Relation)
    cat_id = find_page_id_by_name(DB_KATEG_ID, tx.kategorie) if DB_KATEG_ID else None
    month_id = find_page_id_by_name(DB_MONAT_ID, tx.monat) if DB_MONAT_ID else None

    properties: dict = {
        "Name": {
            "title": [
                {"text": {"content": tx.name}}
            ]
        },
        "Betrag": {"number": tx.betrag},
        "Datum": {"date": {"start": tx.datum}},
        "Kategorie_Text": {
            "rich_text": [{"text": {"content": tx.kategorie}}]
        },
        "Notiz": {
            "rich_text": [{"text": {"content": tx.notiz}}] if tx.notiz else []
        },
    }

    if cat_id:
        properties["Kategorien"] = {
            "relation": [{"id": cat_id}]
        }

    if month_id:
        properties["Monat"] = {
            "relation": [{"id": month_id}]
        }

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
# HTML-Formular (Handy-optimiert)
# -----------------------------------------------------------


@app.get("/formular", response_class=HTMLResponse)
def einkaufs_formular():
    options = load_options()

    today_str = date.today().isoformat()
    default_monat = options.monate[0] if options.monate else ""
    default_kat = (
        "Lebensmittel"
        if "Lebensmittel" in options.kategorien
        else (options.kategorien[0] if options.kategorien else "")
    )

    initial_total = (
        get_lebensmittel_sum_for_month(default_monat) if default_monat else 0.0
    )
    initial_total_text = f"{initial_total:0.2f} CHF"

    # Dropdown-HTML bauen
    kategorie_options_html = "".join(
        f'<option value="{k}"{" selected" if k == default_kat else ""}>{k}</option>'
        for k in options.kategorien
    )

    monat_options_html = "".join(
        f'<option value="{m}"{" selected" if m == default_monat else ""}>{m}</option>'
        for m in options.monate
    )

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Einkauf erfassen</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {{
      font-size: 18px;
    }}
    body {{
      font-family: -apple-system, system-ui, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      padding: 0;
      background: #f3f4f6;
    }}
    .page {{
      min-height: 100vh;
      padding: 8px;
      box-sizing: border-box;
    }}
    .container {{
      max-width: 640px;
      width: 100%;
      margin: 0 auto;
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

    @media (max-width: 768px) {{
      .page {{
        padding: 4px;
      }}
      .container {{
        max-width: 100%;
        border-radius: 0;
        box-shadow: none;
      }}
      input, select, textarea, button {{
        font-size: 22px;
        padding: 16px 14px;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
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
          <span>Lebensmittel total <span id="monatLabel">{default_monat}</span>:</span>
          <span class="sum" id="lebTotal">{initial_total_text}</span>
        </div>
      </form>
    </div>
  </div>

  <script>
    const form = document.getElementById("txForm");
    const msg = document.getElementById("msg");
    const monatSelect = document.getElementById("monat");
    const lebTotalSpan = document.getElementById("lebTotal");
    const monatLabel = document.getElementById("monatLabel");

    async function updateTotal() {{
      const monat = monatSelect.value;
      monatLabel.textContent = monat;
      try {{
        const res = await fetch("/lebensmittel_sum?monat=" + encodeURIComponent(monat));
        if (!res.ok) throw new Error("Fehler beim Laden der Summe");
        const data = await res.json();
        const sum = typeof data.sum === "number" ? data.sum : 0;
        lebTotalSpan.textContent = sum.toFixed(2) + " CHF";
      }} catch (err) {{
        console.error(err);
        lebTotalSpan.textContent = "—";
      }}
    }}

    monatSelect.addEventListener("change", updateTotal);

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
        monat: monatSelect.value,
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

        // Summe aktualisieren
        updateTotal();
      }} catch (err) {{
        console.error(err);
        msg.textContent = "Fehler beim Speichern ❌";
      }}
    }});

    // Beim Laden einmal die Summe aktualisieren (falls Monat geändert wurde)
    updateTotal();
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)
