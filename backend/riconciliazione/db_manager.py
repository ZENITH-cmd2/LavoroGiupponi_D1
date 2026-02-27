import sqlite3
import pandas as pd
import datetime

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def get_impianto_id(conn, pv_code):
    """
    Trova l'id dell'impianto corrispondente al pv_code nella tabella 'impianti'.
    Ritorna None se non trovato.
    """
    cur = conn.cursor()
    cur.execute("SELECT id FROM impianti WHERE codice_pv_fortech = ?", (str(pv_code),))
    row = cur.fetchone()
    return row['id'] if row else None

def salva_report_riconciliazione(conn, record):
    """
    Inserisce una singola riga nella tabella report_riconciliazioni.
    Record dict deve contenere:
        impianto_id, data_riferimento, categoria, valore_fortech,
        valore_reale, differenza, stato, note (opzionale)
    """
    cur = conn.cursor()
    
    # Controlla se esiste gia per evitare duplicati
    cur.execute('''
        SELECT id FROM report_riconciliazioni
        WHERE impianto_id = ? AND data_riferimento = ? AND categoria = ?
    ''', (record['impianto_id'], record['data_riferimento'], record['categoria']))
    esistente = cur.fetchone()

    if esistente:
        cur.execute('''
            UPDATE report_riconciliazioni
            SET valore_fortech = ?, valore_reale = ?, differenza = ?, stato = ?, note = ?, data_elaborazione = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (
            record['valore_fortech'], record['valore_reale'], record['differenza'], 
            record['stato'], record.get('note', ''),
            esistente['id']
        ))
    else:
        cur.execute('''
            INSERT INTO report_riconciliazioni
            (impianto_id, data_riferimento, categoria, valore_fortech, valore_reale, differenza, stato, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            record['impianto_id'], record['data_riferimento'], record['categoria'],
            record['valore_fortech'], record['valore_reale'], record['differenza'],
            record['stato'], record.get('note', '')
        ))
    conn.commit()

def pulisci_report_impianto(conn, impianto_id):
    """Rimuove vecchi report prima di una nuova esecuzione per un certo impianto."""
    cur = conn.cursor()
    cur.execute("DELETE FROM report_riconciliazioni WHERE impianto_id = ?", (impianto_id,))
    conn.commit()
