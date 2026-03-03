import requests
import sqlite3
import sys

BASE_URL = "http://localhost:5050"

# 1. Login to get token
res = requests.post(f"{BASE_URL}/api/auth/login", json={"username": "admin", "password": "adminpassword"}) 
# Note: we need the right credentials. Assuming admin/admin or test_admin
if res.status_code != 200:
    res = requests.post(f"{BASE_URL}/api/auth/login", json={"username": "test_admin", "password": "SuperSecretPassword123!"})

if res.status_code != 200:
    print("Login fallito. Impossibile testare l'API.", res.text)
    sys.exit(1)

access_token = res.json().get("access_token")

# 2. Get a record to test
conn = sqlite3.connect('database_riconciliazioni.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("SELECT id, valore_fortech, valore_reale FROM report_riconciliazioni LIMIT 1")
row = cur.fetchone()

if not row:
    print("DB empty.")
    sys.exit(1)

rec_id = row['id']
new_val = float(row['valore_fortech'] or 0) + 15.0

print(f"Tentativo di edit record {rec_id} a valore_reale={new_val}")

# 3. Call Edit API
edit_res = requests.post(
    f"{BASE_URL}/api/riconciliazioni/edit", 
    headers={"Authorization": f"Bearer {access_token}"},
    json={
        "id": rec_id,
        "valore_reale": new_val,
        "note": "Test string from script"
    }
)

print(f"Status Code: {edit_res.status_code}")
try:
    print(f"Response: {edit_res.json()}")
except:
    print(f"Raw Response: {edit_res.text}")

# 4. Verify DB change
cur.execute("SELECT id, valore_reale, differenza, stato, note FROM report_riconciliazioni WHERE id=?", (rec_id,))
new_row = cur.fetchone()
print(f"DB Dopo API: {dict(new_row)}")

conn.close()
