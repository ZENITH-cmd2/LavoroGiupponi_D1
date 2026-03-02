import requests
import time
import sys
import json

BASE_URL = "http://localhost:5050"

def run_tests():
    print("🚀 Inizio collaudo Impostazioni Dinamiche...")
    session = requests.Session()
    
    # 1. Login corretto
    print("-> Test: POST /login (con credenziali standard)")
    res = session.post(f"{BASE_URL}/api/auth/login", json={"username": "test_admin", "password": "SuperSecretPassword123!"})
    if res.status_code != 200:
        print(f"❌ Impossibile fare login iniziale: {res.status_code}")
        # tenta con admin se test_admin non c'è
        res = session.post(f"{BASE_URL}/api/auth/login", json={"username": "admin", "password": "admin"})
        if res.status_code != 200:
            print("Esco")
            sys.exit(1)
            
    # Usa credenziali valide trovate (admin)
    tokens = res.json()
    access_token = tokens.get("access_token")
    print("✅ Access_token Admin acquisito")
    
    # 2. Update config json
    print("-> Test: POST /api/settings/config")
    payload = {
        "tolleranza_contanti_arrotondamento": 3.00,
        "tolleranza_carte_fisiologica": 10.50,
        "tolleranza_satispay": 0.05,
        "scarto_giorni_buoni": 2,
        "scarto_giorni_contanti_inf": 3,
        "scarto_giorni_contanti_sup": 7
    }
    res = requests.post(
        f"{BASE_URL}/api/settings/config", 
        json=payload,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if res.status_code == 200:
        print("✅ Configurazioni (tolleranze) salvate e lette con successo in json")
    else:
        print(f"❌ Errore salvataggio config: {res.text}")

    # Lecito: Verifico lettura su config
    res = requests.get(f"{BASE_URL}/api/settings/config", headers={"Authorization": f"Bearer {access_token}"})
    if res.json().get('scarto_giorni_buoni') == 2:
        print("✅ GET Configuration -> MATCH corretto coi dati precedentemente salvati")

    # 3. Change password test (se admin pass -> admin_new)
    print("-> Test: POST /api/settings/password")
    pw_payload = {
        "old_password": "admin",
        "new_password": "nuovapassword!"
    }
    
    res = requests.post(f"{BASE_URL}/api/settings/password", json=pw_payload, headers={"Authorization": f"Bearer {access_token}"})
    
    if res.status_code == 200:
        print("✅ Password aggiornata nel database")
        
        # 4. Prova login con nuova (admin / nuovapassword!)
        print("-> Test: Re-Login post update_password")
        res2 = session.post(f"{BASE_URL}/api/auth/login", json={"username": "admin", "password": "nuovapassword!"})
        if res2.status_code == 200:
            print("✅ SUCCESSO! Login eseguito con la nuova password generata tramite /api/settings")
        else:
            print("❌ Fallimento! La nuova password non permette l'accesso")
            
        # Revert per non scassare le robe dell'utente
        res_rev = requests.post(f"{BASE_URL}/api/settings/password", json={"old_password": "nuovapassword!", "new_password": "admin"}, headers={"Authorization": f"Bearer {res2.json()['access_token']}"})
        if res_rev.status_code == 200: print("✅ Revert password eseguito (admin:admin)")
            
    else:
        print(f"❌ Impossibile aggiornare la password o utente non admin")


if __name__ == "__main__":
    run_tests()
