import requests
try:
    # Try calling without auth and see what happens (does it return JSON 401 or HTML?)
    r = requests.post("http://localhost:5050/api/riconciliazioni/edit", json={"id":1, "valore_reale":100})
    print(r.status_code)
    print(r.text[:500])
except Exception as e:
    print(e)
