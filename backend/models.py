import sqlite3
import datetime
from argon2 import PasswordHasher
from backend.riconciliazione.db_manager import get_db_connection

ph = PasswordHasher()

def init_auth_db(db_path):
    """Crea le tabelle per l'autenticazione se non esistono e inserisce un utente admin di default."""
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    
    # Crea tabella users
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Crea tabella login_audit
    cur.execute('''
        CREATE TABLE IF NOT EXISTS login_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username_attempt TEXT,
            ip_address TEXT,
            success INTEGER,
            attempt_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    
    # Inserisci utente admin di default se non esiste nessun utente
    cur.execute("SELECT COUNT(*) as c FROM users")
    if cur.fetchone()['c'] == 0:
        default_hash = ph.hash("admin")
        cur.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ("admin", default_hash))
        
    conn.commit()
    conn.close()

def authenticate_user(db_path, username, password):
    """Verifica le credenziali dell'utente usando Argon2."""
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    
    cur.execute("SELECT id, username, password_hash, active FROM users WHERE username = ?", (username,))
    user = cur.fetchone()
    
    conn.close()
    
    if not user:
        return None
        
    if not user['active']:
        return None
        
    try:
        if ph.verify(user['password_hash'], password):
            if ph.check_needs_rehash(user['password_hash']):
                # Possibile aggiornamento dell'hash in background
                pass
            return {"id": user["id"], "username": user["username"]}
    except Exception:
        pass
        
    return None

def log_login_attempt(db_path, user_id, username_attempt, ip_address, success):
    """Registra il tentativo di login per audit e rate limiting."""
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO login_audit (user_id, username_attempt, ip_address, success) VALUES (?, ?, ?, ?)",
        (user_id, username_attempt, ip_address, 1 if success else 0)
    )
    conn.commit()
    conn.close()

def get_failed_attempts(db_path, ip_address, minutes=10):
    """Trova il numero di tentativi falliti negli ultimi X minuti da un IP."""
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    
    cutoff = datetime.datetime.now() - datetime.timedelta(minutes=minutes)
    cur.execute("""
        SELECT COUNT(*) as c FROM login_audit 
        WHERE ip_address = ? AND success = 0 AND attempt_time > ?
    """, (ip_address, cutoff.strftime('%Y-%m-%d %H:%M:%S')))
    
    count = cur.fetchone()['c']
    conn.close()
    return count
