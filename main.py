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


@app.get("/formular", response_class=HTMLResponse)
def einkaufs_formular():
    # HTML-Formular mit Dropdowns für Kategorien und Monate
    return """
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8" />
  <title>Einkauf erfassen</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
</head>
<body>
  <h1>Einkauf erfassen</h1>

  <form id="txForm">
    <label>
      Name<br />
      <input type="text" id="name" required placeholder="z.B. Migros" />
    </label>
    <br /><br />

    <label>
      Betrag<br />
      <input type="number" id="betrag" required step="0.05" />
    </label>
    <br /><br />

    <label>
      Datum<br />
      <input type="date" id="datum" />
    </label>
    <br /><br />

    <label>
      Kategorie<br />
      <select id="kategorie">
        <option value="">-- bitte wählen --</option>
      </select>
    </label>
    <br /><br />

    <label>
      Monat<br />
      <select id="monat">
        <option value="">-- bitte wählen --</option>
      </select>
    </label>
    <br /><br />

    <label>
      Notiz<br />
      <input type="text" id="notiz" placeholder="optional" />
    </label>
    <br /><br />

    <button type="submit">Speichern</button>
    <p id="msg"></p>
  </form>

  <script>
    const form = document.getElementById("txForm");
    const msg = document.getElementById("msg");
    const datumFeld = document.getElementById("datum");
    const katSelect = document.getElementById("kategorie");
    const monatSelect = document.getElementById("monat");

    // heutiges Datum setzen
    datumFeld.value = new Date().toISOString().substring(0, 10);

    async function loadOptions() {
      try {
        const res = await fetch("/options");
        const data = await res.json();

        // Kategorien einfüllen
        data.kategorien.forEach((name) => {
          const opt = document.createElement("option");
          opt.value = name;
          opt.textContent = name;
          katSelect.appendChild(opt);
        });

        // Monate einfüllen
        data.monate.forEach((name) => {
          const opt = document.createElement("option");
          opt.value = name;
          opt.textContent = name;
          monatSelect.appendChild(opt);
        });
      } catch (err) {
        console.error("Fehler beim Laden der Optionen:", err);
        msg.textContent = "Fehler beim Laden der Auswahlwerte ❌";
      }
    }

    loadOptions();

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      msg.textContent = "Sende...";

      const data = {
        name: document.getElementById("name").value,
        betrag: parseFloat(document.getElementById("betrag").value),
        datum: datumFeld.value,
        kategorie: katSelect.value || null,
        monat: monatSelect.value || null,
        notiz: document.getElementById("notiz").value || null,
      };

      try {
        const res = await fetch("/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });

        if (!res.ok) throw new Error("Fehler beim Speichern");

        msg.textContent = "Gespeichert ✅";
        form.reset();
        datumFeld.value = new Date().toISOString().substring(0, 10);
        katSelect.selectedIndex = 0;
        monatSelect.selectedIndex = 0;
      } catch (err) {
        console.error(err);
        msg.textContent = "Fehler ❌";
      }
    });
  </script>
</body>
</html>
"""

