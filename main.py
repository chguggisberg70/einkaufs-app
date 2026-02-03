import os
from datetime import date
from typing import List, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# -----------------------------------------------------------
# Konfiguration
# -----------------------------------------------------------
load_dotenv()
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
DB_TRANSAKT_ID = os.getenv("NOTION_TRANSAKT_DB_ID")
DB_KATEG_ID = os.getenv("NOTION_KATEG_DB_ID")
DB_MONAT_ID = os.getenv("NOTION_MONAT_DB_ID")

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

http_headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

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
# Hilfsfunktionen
# -----------------------------------------------------------
def query_database(database_id: str, payload: Optional[dict] = None) -> dict:
    url = f"{NOTION_API_BASE}/databases/{database_id}/query"
    try:
        resp = requests.post(url, headers=http_headers, json=payload or {})
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"!!! FEHLER bei DB {database_id}: {e}")
        return {"results": []}

def get_names_from_db(database_id: str) -> List[str]:
    if not database_id: return []
    data = query_database(database_id)
    names = []
    for page in data.get("results", []):
        prop = page.get("properties", {}).get("Name", {})
        title = prop.get("title", [])
        if title: names.append(title[0]["plain_text"])
    return names

def find_page_id_by_name(database_id: str, name: str) -> Optional[str]:
    if not name or not database_id: return None
    payload = {"filter": {"property": "Name", "title": {"equals": name}}, "page_size": 1}
    data = query_database(database_id, payload)
    results = data.get("results", [])
    return results[0]["id"] if results else None

def get_lebensmittel_sum_for_month(monat_name: str) -> float:
    if not all([monat_name, DB_MONAT_ID, DB_TRANSAKT_ID, DB_KATEG_ID]): return 0.0
    month_id = find_page_id_by_name(DB_MONAT_ID, monat_name)
    cat_id = find_page_id_by_name(DB_KATEG_ID, "Lebensmittel")
    if not month_id or not cat_id: return 0.0
    payload = {"filter": {"and": [{"property": "Monat", "relation": {"contains": month_id}}, {"property": "Kategorien", "relation": {"contains": cat_id}}]}}
    data = query_database(DB_TRANSAKT_ID, payload)
    return sum(page.get("properties", {}).get("Betrag", {}).get("number", 0) for page in data.get("results", []))

# -----------------------------------------------------------
# App & Routen
# -----------------------------------------------------------
app = FastAPI()

@app.get("/")
def read_root(): return {"status": "v3-online"}

@app.get("/lebensmittel_sum")
def lebensmittel_sum(monat: str):
    return {"sum": round(get_lebensmittel_sum_for_month(monat), 2)}

@app.post("/add")
def add_transaction(tx: Transaction):
    cat_id = find_page_id_by_name(DB_KATEG_ID, tx.kategorie)
    month_id = find_page_id_by_name(DB_MONAT_ID, tx.monat)
    props = {
        "Name": {"title": [{"text": {"content": tx.name}}]},
        "Betrag": {"number": tx.betrag},
        "Datum": {"date": {"start": tx.datum}},
        "Kategorie_Text": {"rich_text": [{"text": {"content": tx.kategorie}}]},
        "Notiz": {"rich_text": [{"text": {"content": tx.notiz}}] if tx.notiz else []},
    }
    if cat_id: props["Kategorien"] = {"relation": [{"id": cat_id}]}
    if month_id: props["Monat"] = {"relation": [{"id": month_id}]}
    requests.post(f"{NOTION_API_BASE}/pages", headers=http_headers, json={"parent": {"database_id": DB_TRANSAKT_ID}, "properties": props}).raise_for_status()
    return {"status": "ok"}

@app.get("/formular", response_class=HTMLResponse)
def einkaufs_formular():
    kats_raw = get_names_from_db(DB_KATEG_ID)
    mons_raw = get_names_from_db(DB_MONAT_ID)
    today = date.today().isoformat()
    
    # DEBUG: Zeigt uns in Render genau, was Notion schickt
    print(f"DEBUG: Gefundene Kategorien in Notion: {kats_raw}")

    # 1. Deine Gruppen-Logik (Case-Insensitive)
    gruppen_def = {
        "Alltag": ["Shopping", "Restaurant", "Lebensmittel"],
        "Wohnen": ["Wohnen Nebenkosten", "Serafe", "Steuern Bund"],
        "Versicherung": ["Versicherungen Mobi Haus", "Versicherung GS"],
        "Freizeit": ["Reisen", "Freizeit"]
    }

    kategorie_html = ""
    verarbeitet = set()

    # 2. Gruppen bauen mit Case-Insensitive Matching
    for g_title, g_list in gruppen_def.items():
        html_inner = ""
        for target in g_list:
            for real_name in kats_raw:
                if target.strip().lower() == real_name.strip().lower():
                    sel = ' selected' if "lebensmittel" in real_name.lower() else ""
                    html_inner += f'<option value="{real_name}"{sel}>{real_name}</option>'
                    verarbeitet.add(real_name)
                    break
        if html_inner:
            kategorie_html += f'<optgroup label="--- {g_title} ---">{html_inner}</optgroup>'

    # 3. Rest alphabetisch
    rest = sorted([k for k in kats_raw if k not in verarbeitet])
    if rest:
        kategorie_html += '<optgroup label="--- Diverses ---">'
        for r in rest:
            kategorie_html += f'<option value="{r}">{r}</option>'
        kategorie_html += '</optgroup>'

    monat_html = "".join(f'<option value="{m}">{m}</option>' for m in mons_raw)

    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Einkauf v3</title>
    <style>
        :root {{ --p: #2563eb; --bg: #f3f4f6; }}
        body {{ font-family: sans-serif; background: var(--bg); padding: 15px; font-size: 1.1rem; }}
        .card {{ max-width: 500px; margin: 0 auto; background: white; padding: 20px; border-radius: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        h1 {{ color: var(--p); text-align: center; margin-bottom: 20px; border-bottom: 2px solid var(--p); padding-bottom: 10px; }}
        label {{ display: block; margin: 15px 0 5px; font-weight: bold; }}
        input, select, textarea {{ width: 100%; padding: 12px; border: 1px solid #ccc; border-radius: 8px; box-sizing: border-box; font-size: 1.1rem; -webkit-appearance: none; }}
        optgroup {{ background: #f0f0f0; font-weight: bold; color: #444; }}
        button {{ width: 100%; margin-top: 25px; padding: 16px; background: var(--p); color: white; border: none; border-radius: 10px; font-weight: bold; font-size: 1.2rem; }}
        .footer {{ margin-top: 20px; border-top: 1px solid #eee; padding-top: 10px; display: flex; justify-content: space-between; align-items: center; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Einkauf V3 ✅</h1>
        <form id="f">
            <label>Was?</label><input id="n" type="text" placeholder="z.B. Migros" required>
            <label>Betrag (CHF)</label><input id="b" type="number" step="0.01" inputmode="decimal" required>
            <label>Datum</label><input id="d" type="date" value="{today}" required>
            <label>Kategorie</label><select id="k">{kategorie_html}</select>
            <div style="display:none"><select id="m">{monat_html}</select></div>
            <label>Notiz</label><textarea id="no" rows="2"></textarea>
            <button type="submit">Speichern</button>
            <p id="msg" style="text-align:center; font-weight:bold;"></p>
            <div class="footer">
                <span>Lebensmittel <br><small id="ml">...</small>:</span>
                <span id="lt" style="font-weight:bold; color:var(--p); font-size: 1.3rem;">...</span>
            </div>
        </form>
    </div>
    <script>
        const months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
        async function up() {{
            const m = document.getElementById("m").value;
            document.getElementById("ml").textContent = "(" + m + ")";
            const r = await fetch("/lebensmittel_sum?monat=" + encodeURIComponent(m));
            const d = await r.json();
            document.getElementById("lt").textContent = (d.sum || 0).toFixed(2);
        }}
        function setM() {{
            const d = new Date(document.getElementById("d").value);
            const t = months[d.getMonth()] + " " + String(d.getFullYear()).slice(-2);
            const s = document.getElementById("m");
            for(let i=0; i<s.options.length; i++) {{
                if(s.options[i].text === t) {{ s.selectedIndex = i; up(); break; }}
            }}
        }}
        document.getElementById("d").onchange = setM;
        window.onload = setM;
        document.getElementById("f").onsubmit = async (e) => {{
            e.preventDefault();
            document.getElementById("msg").textContent = "⏳ Speichere...";
            const res = await fetch("/add", {{ 
                method: "POST", 
                headers: {{"Content-Type":"application/json"}}, 
                body: JSON.stringify({{
                    name: document.getElementById("n").value,
                    betrag: parseFloat(document.getElementById("b").value),
                    datum: document.getElementById("d").value,
                    kategorie: document.getElementById("k").value,
                    monat: document.getElementById("m").value,
                    notiz: document.getElementById("no").value || null
                }}) 
            }});
            if(res.ok) {{
                document.getElementById("msg").textContent = "✅ Gespeichert!";
                document.getElementById("n").value = ""; document.getElementById("b").value = "";
                up();
            }} else {{ document.getElementById("msg").textContent = "❌ Fehler!"; }}
        }};
    </script>
</body>
</html>
""")
