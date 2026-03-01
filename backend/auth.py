import os
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity
from backend.models import authenticate_user, log_login_attempt, get_failed_attempts, init_auth_db, ph
from backend.riconciliazione.db_manager import get_db_connection

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "database_riconciliazioni.db")

@auth_bp.route('/register', methods=['POST'])
def register():
    """Registra un nuovo utente. Protetto in produzione, ma aperto per il setup iniziale."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password or len(password) < 8:
        return jsonify({"msg": "Username e password (>8 char) obbligatori"}), 400
        
    conn = get_db_connection(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = ?", (username,))
    if cur.fetchone():
        conn.close()
        return jsonify({"msg": "Username già in uso"}), 400
        
    try:
        pw_hash = ph.hash(password)
        cur.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, pw_hash))
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"msg": f"Errore interno: {str(e)}"}), 500
        
    conn.close()
    return jsonify({"msg": "Utente registrato con successo"}), 201

@auth_bp.route('/login', methods=['POST'])
def login():
    """Effettua il login verificando credenziali, rate limit e audit."""
    ip = request.remote_addr
    
    # Rate Limiting (OWASP): Max 5 tentativi ogni 10 minuti
    failed_attempts = get_failed_attempts(DB_PATH, ip, minutes=10)
    if failed_attempts >= 5:
        return jsonify({"msg": "Troppi tentativi falliti. Riprova tra 10 minuti.", "error": "Too Many Requests"}), 429
        
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        log_login_attempt(DB_PATH, None, "MISSING_DATA", ip, False)
        return jsonify({"msg": "Credenziali mancanti"}), 400
        
    username = data['username']
    password = data['password']
    
    user_data = authenticate_user(DB_PATH, username, password)
    
    if user_data:
        log_login_attempt(DB_PATH, user_data['id'], username, ip, True)
        access_token = create_access_token(identity=user_data['username'])
        refresh_token = create_refresh_token(identity=user_data['username'])
        return jsonify(access_token=access_token, refresh_token=refresh_token, user=user_data['username']), 200
    else:
        # Prendi l'id utente se esiste solo per tracciarlo (anche se la pw è errata)
        conn = get_db_connection(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = ?", (username,))
        user_row = cur.fetchone()
        conn.close()
        
        uid = user_row['id'] if user_row else None
        log_login_attempt(DB_PATH, uid, username, ip, False)
        
        return jsonify({"msg": "Credenziali invalide"}), 401

@auth_bp.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh_token():
    """Genera un nuovo access token tramite il refresh token valido."""
    current_user = get_jwt_identity()
    new_access_token = create_access_token(identity=current_user)
    return jsonify(access_token=new_access_token), 200

@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def protected():
    current_user = get_jwt_identity()
    return jsonify(logged_in_as=current_user), 200
