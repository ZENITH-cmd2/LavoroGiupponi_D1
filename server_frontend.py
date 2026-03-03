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

from flask_jwt_extended import JWTManager, jwt_required
from backend.auth import auth_bp
from backend.models import init_auth_db
import datetime

DB_PATH = os.path.join(PROJECT_ROOT, "database_riconciliazioni.db")

app = Flask(__name__, 
            template_folder="frontend/templates",
            static_folder="frontend/static")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16MB max upload

# Configurazione Sicurezza JWT (OWASP)
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'calor-systems-super-secret-key-production-ready-2025')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(minutes=15)
app.config['JWT_REFRESH_TOKEN_EXPIRES'] = datetime.timedelta(days=7)
jwt = JWTManager(app)

app.register_blueprint(auth_bp)
init_auth_db(DB_PATH)


def get_readonly_db():
    conn = get_db_connection(DB_PATH)
    return conn

# ============================================================================
# PAGES
# ============================================================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login")
def login_page():
    return render_template("login.html")

# ============================================================================
# API ENDPOINTS (READ)
# ============================================================================
@app.route("/api/chart-data")
@jwt_required()
def api_chart_data():
    """Aggregated Fortech vs Reale totals per category for dashboard pie charts."""
    conn = get_readonly_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                categoria,
                ROUND(SUM(COALESCE(valore_fortech, 0)), 2) AS tot_fortech,
                ROUND(SUM(COALESCE(valore_reale,   0)), 2) AS tot_reale,
                COUNT(*) AS num_record
            FROM report_riconciliazioni
            GROUP BY categoria
            ORDER BY tot_fortech DESC
        """)
        rows = cur.fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/stats")
@jwt_required()
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
@jwt_required()
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
@jwt_required()
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
@jwt_required()
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
@jwt_required()
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
@jwt_required()
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
@jwt_required()
def api_sicurezza():
    return jsonify([]) # Non implementata ancora

@app.route("/api/ai-report", methods=["POST"])
@jwt_required()
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


@app.route("/api/contanti-conferma", methods=["POST"])
@jwt_required()
def api_contanti_conferma():
    """Conferma o rifiuta un matching Contanti dalla Vista Simona."""
    conn = get_readonly_db()
    try:
        data = request.get_json()
        rec_id = data.get("id")
        azione = data.get("azione")  # "conferma" o "rifiuta"
        nota = data.get("nota", "")

        if not rec_id or azione not in ("conferma", "rifiuta"):
            return jsonify({"error": "Parametri mancanti o non validi"}), 400

        cur = conn.cursor()

        # Verifica esistenza record
        cur.execute("SELECT id FROM report_riconciliazioni WHERE id = ?", (rec_id,))
        if not cur.fetchone():
            return jsonify({"error": "Record non trovato"}), 404

        if azione == "conferma":
            cur.execute(
                "UPDATE report_riconciliazioni SET risolto = 1, note = ? WHERE id = ?",
                (nota if nota else "Confermato manualmente", rec_id)
            )
            nuovo_stato = "confermato"
        else:  # rifiuta
            cur.execute(
                "UPDATE report_riconciliazioni SET risolto = 0, stato = 'ANOMALIA_GRAVE', note = ? WHERE id = ?",
                (nota if nota else "Segnalato come anomalia", rec_id)
            )
            nuovo_stato = "segnalato"

        conn.commit()
        return jsonify({"message": f"Record {nuovo_stato}", "stato": nuovo_stato}), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/riconciliazioni/edit", methods=["POST"])
@jwt_required()
def api_riconciliazioni_edit():
    conn = get_readonly_db()
    try:
        data = request.get_json()
        rec_id = data.get("id")
        nuovo_reale = data.get("valore_reale")
        nuove_note = data.get("note", "")
        
        if not rec_id or nuovo_reale is None:
            return jsonify({"error": "Dati mancanti (id, valore_reale)"}), 400
            
        try:
            nuovo_reale = float(nuovo_reale)
        except ValueError:
            return jsonify({"error": "Formato valore_reale non valido"}), 400

        cur = conn.cursor()
        cur.execute("SELECT valore_fortech, categoria FROM report_riconciliazioni WHERE id = ?", (rec_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Record non trovato"}), 404
            
        teorico = float(row["valore_fortech"] or 0)
        categoria = row["categoria"]
        
        diff_netta = teorico - nuovo_reale
        diff_assoluta = abs(diff_netta)
        
        # Leggi configurazioni
        config_path = os.path.join(PROJECT_ROOT, "backend", "config.json")
        import json
        cfg = {}
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
        except Exception:
            pass
            
        toll_stretta = 0.50
        toll_larga = 5.00
        
        if categoria == "contanti":
            toll_stretta = float(cfg.get("tolleranza_contanti_arrotondamento", 5.00))
            toll_larga = 20.00
        elif categoria == "carte_bancarie":
            toll_stretta = float(cfg.get("tolleranza_carte_fisiologica", 0.50))
        elif categoria == "satispay":
            toll_stretta = float(cfg.get("tolleranza_satispay", 0.01))
            
        if teorico == 0 and nuovo_reale == 0:
            nuovo_stato = "QUADRATO"
        elif teorico > 0 and nuovo_reale == 0:
            nuovo_stato = "NON_TROVATO"
        elif diff_assoluta <= toll_stretta:
            nuovo_stato = "QUADRATO_ARROT" if categoria == "contanti" else "QUADRATO"
        elif diff_assoluta <= toll_larga:
            nuovo_stato = "ANOMALIA_LIEVE"
        else:
            nuovo_stato = "ANOMALIA_GRAVE"
            
        cur.execute("""
            UPDATE report_riconciliazioni 
            SET valore_reale = ?, differenza = ?, stato = ?, note = ? 
            WHERE id = ?
        """, (nuovo_reale, diff_netta, nuovo_stato, nuove_note, rec_id))
        conn.commit()
        
        return jsonify({"message": "Aggiornato con successo", "nuovo_stato": nuovo_stato, "differenza": diff_netta}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

import pandas as pd
from io import BytesIO
from flask import send_file

@app.route("/api/riconciliazioni/export/excel", methods=["GET"])
@jwt_required()
def api_export_excel():
    conn = get_readonly_db()
    try:
        da = request.args.get("da")
        a = request.args.get("a")

        query = """
            SELECT 
                r.data_riferimento as Data,
                i.nome_impianto as Impianto,
                r.categoria as Categoria,
                r.valore_fortech as Teorico_EUR,
                r.valore_reale as Reale_EUR,
                r.differenza as Differenza_EUR,
                r.stato as Stato,
                r.note as Note
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
            
        query += " ORDER BY r.data_riferimento DESC, i.nome_impianto, r.categoria"

        df = pd.read_sql_query(query, conn, params=params)
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Riconciliazioni')
            workbook = writer.book
            worksheet = writer.sheets['Riconciliazioni']
            
            # Formattazione
            money_fmt = workbook.add_format({'num_format': '€ #,##0.00'})
            date_fmt = workbook.add_format({'num_format': 'yyyy-mm-dd'})
            
            for i, col in enumerate(df.columns):
                col_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(i, i, col_len)
                if 'EUR' in col:
                    worksheet.set_column(i, i, 15, money_fmt)
                if col == 'Data':
                    worksheet.set_column(i, i, 12, date_fmt)

        output.seek(0)
        filename = f"Riconciliazioni_{da or 'all'}_{a or 'all'}.xlsx"
        return send_file(output, download_name=filename, as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ============================================================================
# API ENDPOINTS (WRITE / ACTION)
# ============================================================================

from flask_jwt_extended import get_jwt_identity
from backend.models import update_user_password
import json

@app.route("/api/settings/password", methods=["POST"])
@jwt_required()
def api_update_password():
    current_user = get_jwt_identity()
    data = request.get_json()
    
    old_pw = data.get('old_password')
    new_pw = data.get('new_password')
    
    if not old_pw or not new_pw or len(new_pw) < 8:
        return jsonify({"msg": "Dati mancanti o password troppo corta"}), 400
        
    success, msg = update_user_password(DB_PATH, current_user, old_pw, new_pw)
    if success:
        return jsonify({"msg": msg}), 200
    else:
        return jsonify({"msg": msg}), 400

@app.route("/api/settings/config", methods=["GET", "POST"])
@jwt_required()
def api_config():
    config_path = os.path.join(PROJECT_ROOT, "backend", "config.json")
    
    if request.method == "GET":
        try:
            with open(config_path, "r") as f:
                return jsonify(json.load(f))
        except FileNotFoundError:
            return jsonify({}), 404
            
    elif request.method == "POST":
        data = request.get_json()
        with open(config_path, "w") as f:
            json.dump(data, f, indent=4)
        return jsonify({"msg": "Configurazione aggiornata"}), 200

@app.route("/api/upload", methods=["POST"])
@jwt_required()
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
# API SETTINGS — API KEY MANAGEMENT
# ============================================================================

@app.route("/api/settings/apikey", methods=["GET"])
@jwt_required()
def api_get_apikey():
    """Return the current OpenRouter API key (masked)."""
    from backend.riconciliazione.ai_report import get_saved_api_key
    key = get_saved_api_key("OpenRouter")
    masked = (key[:8] + "…" + key[-4:]) if len(key) > 12 else ("●" * len(key) if key else "")
    return jsonify({"has_key": bool(key), "masked": masked})


@app.route("/api/settings/apikey", methods=["POST"])
@jwt_required()
def api_save_apikey():
    """Save the OpenRouter API key to .env.local."""
    from backend.riconciliazione.ai_report import save_api_key
    data = request.get_json()
    key = (data or {}).get("api_key", "").strip()
    if not key:
        return jsonify({"error": "Chiave vuota"}), 400
    if not key.startswith("sk-or-"):
        return jsonify({"error": "Il formato della chiave non è valido (deve iniziare con sk-or-)"}), 400
    save_api_key("OpenRouter", key)
    return jsonify({"message": "Chiave salvata con successo"})


@app.route("/api/settings/apikey/test", methods=["POST"])
@jwt_required()
def api_test_apikey():
    """Send a minimal test request to OpenRouter to verify the key."""
    from backend.riconciliazione.ai_report import get_saved_api_key
    import requests as ext_req
    data = request.get_json() or {}
    key = data.get("api_key", "").strip() or get_saved_api_key("OpenRouter")
    if not key:
        return jsonify({"error": "Nessuna chiave configurata"}), 400
    try:
        resp = ext_req.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
            timeout=10
        )
        if resp.status_code == 200:
            return jsonify({"message": "✅ Connessione riuscita! Chiave valida."})
        elif resp.status_code == 401:
            return jsonify({"error": "❌ Chiave non autorizzata — controlla che sia corretta."}), 401
        else:
            return jsonify({"error": f"⚠️ Risposta inattesa: {resp.status_code} — {resp.text[:200]}"}), 400
    except Exception as e:
        return jsonify({"error": f"Errore di connessione: {str(e)}"}), 500


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

