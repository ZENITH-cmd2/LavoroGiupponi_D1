import sqlite3
import os

DB_PATH = "database_riconciliazioni.db"
SCHEMA_PATH = "db/calor_systems_schema.sql"

def init_db():
    print(f"Inizializzando {DB_PATH} col nuovo schema...")
    conn = sqlite3.connect(DB_PATH)
    try:
        with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
            script = f.read()
            conn.executescript(script)
        print("Schema applicato con successo.")
    except Exception as e:
        print(f"Errore durante l'applicazione dello schema: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    init_db()
