"""
Calor Systems — Web Dashboard Server
Server Flask integrato per il progetto Lavoro_giupponi4
Offre endpoint REST per la UI e lancia l'Orchestratore Unificato all'upload dei file.
"""

import sqlite3
import os
import sys
import webbrowser
import threading
import tempfile
import shutil
import time
from flask import Flask, render_template, jsonify, request
from werkzeug.utils import secure_filename

# Add project root to sys path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.append(PROJECT_ROOT)

from backend.riconciliazione.orchestratore import Orchestratore
from backend.riconciliazione.db_manager import get_db_connection
from backend.riconciliazione.ai_report import get_saved_api_key, generate_report

DB_PATH = os.path.join(PROJECT_ROOT, "database_riconciliazioni.db")

app = Flask(__name__, 
            template_folder="frontend/templates",
            static_folder="frontend/static")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16MB max upload

def get_readonly_db():
    conn = get_db_connection(DB_PATH)
    return conn

# ============================================================================
# PAGES
# ============================================================================
@app.route("/")
def index():
    return render_template("index.html")

# ============================================================================
# API ENDPOINTS (READ)
# ============================================================================
@app.route("/api/stats")
def api_stats():
    """Global statistics for the dashboard header."""
    conn = get_readonly_db()
    try:
        cur = conn.cursor()

        # Total impianti
        cur.execute("SELECT COUNT(*) as c FROM impianti WHERE attivo = 1")
        total_impianti = cur.fetchone()['c']

        # Total giornate analizzate
        cur.execute("SELECT COUNT(DISTINCT data_riferimento) as c FROM report_riconciliazioni")
        total_giornate = cur.fetchone()['c']

        # Anomalie
        cur.execute("""
            SELECT COUNT(*) as c FROM report_riconciliazioni 
            WHERE stato IN ('ANOMALIA_LIEVE', 'ANOMALIA_GRAVE') AND risolto = 0
        """)
        anomalie_aperte = cur.fetchone()['c']

        # Quadrate
        cur.execute("SELECT COUNT(*) as c FROM report_riconciliazioni WHERE stato = 'QUADRATO'")
        quadrate = cur.fetchone()['c']

        # Anomalie gravi
        cur.execute("""
            SELECT COUNT(*) as c FROM report_riconciliazioni 
            WHERE stato = 'ANOMALIA_GRAVE' AND risolto = 0
        """)
        anomalie_gravi = cur.fetchone()['c']

        # Total records imported
        cur.execute("SELECT COUNT(*) as c FROM import_fortech_master")
        fortech_records = cur.fetchone()['c']
        
        # Last import date
        cur.execute("SELECT MAX(data_importazione) as md FROM import_fortech_master")
        row = cur.fetchone()
        last_import = row['md'] if row else None

        return jsonify({
            "total_impianti": total_impianti,
            "total_giornate": total_giornate,
            "anomalie_aperte": anomalie_aperte,
            "anomalie_gravi": anomalie_gravi,
            "quadrate": quadrate,
            "fortech_records": fortech_records,
            "last_import": last_import,
        })
    finally:
        conn.close()

@app.route("/api/impianti")
def api_impianti():
    """List all impianti with their latest reconciliation status."""
    conn = get_readonly_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                i.id,
                i.nome_impianto,
                i.codice_pv_fortech,
                i.tipo_gestione,
                i.citta,
                i.attivo,
                (SELECT COUNT(*) FROM report_riconciliazioni r 
                 WHERE r.impianto_id = i.id AND r.stato = 'QUADRATO') as cnt_ok,
                (SELECT COUNT(*) FROM report_riconciliazioni r 
                 WHERE r.impianto_id = i.id AND r.stato = 'ANOMALIA_LIEVE') as cnt_warn,
                (SELECT COUNT(*) FROM report_riconciliazioni r 
                 WHERE r.impianto_id = i.id AND r.stato = 'ANOMALIA_GRAVE' AND r.risolto = 0) as cnt_grave,
                (SELECT MAX(r.data_riferimento) FROM report_riconciliazioni r 
                 WHERE r.impianto_id = i.id) as last_date
            FROM impianti i
            WHERE i.attivo = 1
            ORDER BY i.nome_impianto
        """)
        rows = cur.fetchall()
        result = [dict(r) for r in rows]
        for r in result:
            r['nome'] = r.pop('nome_impianto')
            r['codice_pv'] = r.pop('codice_pv_fortech')
            r['tipo'] = r.pop('tipo_gestione')
        return jsonify(result)
    finally:
        conn.close()

@app.route("/api/impianti/<int:impianto_id>/andamento")
def api_andamento(impianto_id):
    """Andamento riconciliazione per un singolo impianto nel tempo."""
    conn = get_readonly_db()
    try:
        cur = conn.cursor()
        
        # Info impianto
        cur.execute("SELECT nome_impianto, codice_pv_fortech, tipo_gestione FROM impianti WHERE id = ?", (impianto_id,))
        imp = cur.fetchone()
        if not imp:
            return jsonify({"error": "Impianto non trovato"}), 404
        
        # Storico riconciliazioni
        cur.execute("""
            SELECT 
                r.data_riferimento,
                r.categoria,
                r.valore_fortech,
                r.valore_reale,
                r.differenza,
                r.stato,
                r.note
            FROM report_riconciliazioni r
            WHERE r.impianto_id = ?
            ORDER BY r.data_riferimento DESC, r.categoria
        """, (impianto_id,))
        
        rows = cur.fetchall()
        
        giorni = {}
        for r in rows:
            data = r["data_riferimento"]
            if data not in giorni:
                giorni[data] = {
                    "data": data,
                    "categorie": {},
                    "totale_teorico": 0,
                    "totale_reale": 0,
                    "totale_diff": 0,
                    "stato_peggiore": "QUADRATO"
                }
            
            cat = r["categoria"]
            giorni[data]["categorie"][cat] = {
                "teorico": r["valore_fortech"],
                "reale": r["valore_reale"],
                "differenza": r["differenza"],
                "stato": r["stato"],
                "note": r["note"],
            }
            
            giorni[data]["totale_teorico"] += (r["valore_fortech"] or 0)
            giorni[data]["totale_reale"] += (r["valore_reale"] or 0)
            giorni[data]["totale_diff"] += (r["differenza"] or 0)
            
            priority = {"QUADRATO": 0, "QUADRATO_ARROT": 1, "ANOMALIA_LIEVE": 2, 
                        "IN_ATTESA": 3, "NON_TROVATO": 4, "ANOMALIA_GRAVE": 5}
            curr_p = priority.get(r["stato"], 4)
            worst_p = priority.get(giorni[data]["stato_peggiore"], 0)
            if curr_p > worst_p:
                giorni[data]["stato_peggiore"] = r["stato"]
        
        stati_count = {}
        for g in giorni.values():
            for cat_det in g["categorie"].values():
                s = cat_det["stato"]
                stati_count[s] = stati_count.get(s, 0) + 1

        return jsonify({
            "impianto": {
                "id": impianto_id,
                "nome": imp["nome_impianto"],
                "codice_pv": imp["codice_pv_fortech"],
                "tipo": imp["tipo_gestione"],
            },
            "giorni": list(giorni.values()),
            "stats": stati_count,
            "totale_giorni": len(giorni),
        })
    finally:
        conn.close()

@app.route("/api/riconciliazioni")
def api_riconciliazioni():
    conn = get_readonly_db()
    try:
        cur = conn.cursor()
        limit = request.args.get("limit", 200, type=int)
        da = request.args.get("da")
        a = request.args.get("a")

        query = """
            SELECT 
                r.id,
                r.data_riferimento as data,
                i.nome_impianto as impianto,
                r.categoria,
                r.valore_fortech,
                r.valore_reale,
                r.differenza,
                r.stato,
                r.tipo_anomalia,
                r.note
            FROM report_riconciliazioni r
            JOIN impianti i ON r.impianto_id = i.id
            WHERE 1=1
        """
        params = []
        
        if da:
            query += " AND r.data_riferimento >= ?"
            params.append(da)
        if a:
            query += " AND r.data_riferimento <= ?"
            params.append(a)
            
        query += " ORDER BY r.data_riferimento DESC, i.nome_impianto LIMIT ?"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()

@app.route("/api/stato-verifiche")
def api_stato_verifiche():
    conn = get_readonly_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                i.nome_impianto,
                i.codice_pv_fortech,
                i.tipo_gestione,
                r.categoria,
                r.data_riferimento,
                r.valore_fortech,
                r.valore_reale,
                r.differenza,
                r.stato,
                r.note
            FROM report_riconciliazioni r
            JOIN impianti i ON r.impianto_id = i.id
            WHERE r.data_riferimento = (
                SELECT MAX(r2.data_riferimento) 
                FROM report_riconciliazioni r2 
                WHERE r2.impianto_id = r.impianto_id AND r2.categoria = r.categoria
            )
            AND i.attivo = 1
            ORDER BY i.nome_impianto, r.categoria
        """)
        
        rows = cur.fetchall()
        impianti = {}
        for r in rows:
            nome = r["nome_impianto"]
            if nome not in impianti:
                impianti[nome] = {
                    "nome": nome,
                    "codice_pv": r["codice_pv_fortech"],
                    "tipo_gestione": r["tipo_gestione"],
                    "categorie": {}
                }
            impianti[nome]["categorie"][r["categoria"]] = {
                "data": r["data_riferimento"],
                "teorico": r["valore_fortech"],
                "reale": r["valore_reale"],
                "differenza": r["differenza"],
                "stato": r["stato"],
                "note": r["note"],
            }
        
        return jsonify(list(impianti.values()))
    finally:
        conn.close()


@app.route("/api/contanti-banca")
def api_contanti_banca():
    conn = get_readonly_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                r.id,
                r.data_riferimento as data,
                i.nome_impianto as impianto,
                i.codice_pv_fortech as codice_pv,
                r.valore_fortech as contanti_teorico,
                r.valore_reale as contanti_versato,
                r.differenza,
                r.stato,
                r.tipo_anomalia as tipo_match,
                r.note,
                r.risolto,
                r.verificato_da,
                r.data_verifica
            FROM report_riconciliazioni r
            JOIN impianti i ON r.impianto_id = i.id
            WHERE r.categoria = 'contanti'
            ORDER BY r.data_riferimento DESC, i.nome_impianto
            LIMIT 100
        """)
        rows = cur.fetchall()
        res = [dict(r) for r in rows]
        for r in res:
            r['risolto'] = bool(r['risolto'])
        return jsonify(res)
    finally:
        conn.close()

@app.route("/api/sicurezza")
def api_sicurezza():
    return jsonify([]) # Non implementata ancora

@app.route("/api/ai-report", methods=["POST"])
def api_ai_report():
    conn = get_readonly_db()
    try:
        # Recupera tutte le anomalie non risolte
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                r.data_riferimento as data,
                i.nome_impianto as impianto,
                r.categoria,
                r.differenza,
                r.stato,
                r.note
            FROM report_riconciliazioni r
            JOIN impianti i ON r.impianto_id = i.id
            WHERE r.stato IN ('ANOMALIA_LIEVE', 'ANOMALIA_GRAVE', 'NON_TROVATO') AND r.risolto = 0
            ORDER BY r.data_riferimento DESC, i.nome_impianto
            LIMIT 100
        """)
        rows = cur.fetchall()
        results_list = [dict(r) for r in rows]
        
        provider = "OpenRouter"
        api_key = get_saved_api_key(provider)
        
        if not api_key:
            return jsonify({"error": f"Chiave API {provider} non configurata. Salva OPENROUTER_API_KEY in .env.local"}), 400
            
        report_text = generate_report(results_list, provider, api_key)
        return jsonify({"report": report_text})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ============================================================================
# API ENDPOINTS (WRITE / ACTION)
# ============================================================================

@app.route("/api/upload", methods=["POST"])
def api_upload():
    if 'files[]' not in request.files:
        return jsonify({"error": "Nessun file caricato"}), 400
    
    files = request.files.getlist('files[]')
    if not files or all(f.filename == '' for f in files):
        return jsonify({"error": "Nessun file selezionato"}), 400

    temp_dir = tempfile.mkdtemp(prefix="calor_upload_")
    saved_paths = []

    try:
        # 1. Save files to isolated folder
        for f in files:
            if f.filename:
                safe_name = secure_filename(f.filename)
                path = os.path.join(temp_dir, safe_name)
                f.save(path)
                saved_paths.append(path)
        
        if not saved_paths:
             return jsonify({"error": "Nessun file valido"}), 400

        logs = ["File ricevuti. Avvio orchestratore..."]

        # 2. Run Orchestratore from backend.riconciliazione
        orchestratore = Orchestratore()
        
        # We pass the temporary directory to the orchestratore so it can find the files matching its patterns
        pv_count = orchestratore.esegui(temp_dir)

        logs.append(f"Elaborazione terminata per {pv_count} Punti Vendita.")

        return jsonify({
            "message": "Elaborazione completata",
            "files_imported": len(saved_paths),
            "days_analyzed": pv_count,
            "logs": logs
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        # Pulizia della cartella temporanea
        shutil.rmtree(temp_dir, ignore_errors=True)

# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"⚠  Database non trovato: {DB_PATH}")
        print("💡 Suggerimento: Esegui prima `python init_db.py`")

    port = int(os.environ.get("PORT", 5050))
    print(f"\n🌐  Calor Systems Web Dashboard")
    print(f"   http://localhost:{port}")
    print(f"   Database: {DB_PATH}\n")

    # In Render non avviamo il browser locale e usiamo host 0.0.0.0
    is_render = os.environ.get("RENDER") == "true" or os.environ.get("ENV") == "production"
    
    if not is_render:
        def open_browser():
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=open_browser, daemon=True).start()
        
    app.run(host="0.0.0.0", port=port, debug=False)

