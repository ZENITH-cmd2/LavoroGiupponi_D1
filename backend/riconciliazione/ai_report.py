import os
import json
import requests
from dotenv import load_dotenv

def get_saved_api_key(provider: str) -> str:
    """Tenta di recuperare la chiave API dalle variabili d'ambiente o dal file .env.local."""
    load_dotenv(".env.local")
    load_dotenv()
    
    if provider == "OpenRouter":
        return os.environ.get("OPENROUTER_API_KEY", "")
    elif provider == "Gemini":
        return os.environ.get("GEMINI_API_KEY", "")
    return ""

def save_api_key(provider: str, api_key: str):
    """Salva la chiave nel file .env.local per usi futuri."""
    key_name = "OPENROUTER_API_KEY" if provider == "OpenRouter" else "GEMINI_API_KEY"
    
    env_lines = []
    if os.path.exists(".env.local"):
        with open(".env.local", "r", encoding="utf-8") as f:
            env_lines = f.readlines()
            
    # Rimuovi vecchia chiave se presente
    env_lines = [line for line in env_lines if not line.startswith(f"{key_name}=")]
    env_lines.append(f"{key_name}={api_key}\n")
    
    with open(".env.local", "w", encoding="utf-8") as f:
        f.writelines(env_lines)
        
    os.environ[key_name] = api_key

def generate_report(results_list: list, provider: str, api_key: str) -> str:
    """Invoca OpenRouter o Gemini per analizzare i risultati della riconciliazione."""
    if not results_list:
        return "Nessun dato da analizzare."
        
    simplified_data = []
    for r in results_list:
        data_str = r.get("data", "Sconosciuta")
        anomalie = []
        
        # Gestisce la possibile struttura ad albero o lineare dei dict.
        # In Lavoro_giupponi4 i risultati sono già appiattiti nel db
        if isinstance(r.get("risultati"), dict):
            for cat, det in r.get("risultati", {}).items():
                if det.get("stato") != "QUADRATO":
                    anomalie.append(f"- {cat}: Stato {det.get('stato', '')}, Differenza €{det.get('differenza', 0)}, Note: {det.get('note', '')}")
        else:
            # Struct Lavoro_giupponi4 flat API
            if r.get("stato") not in ("QUADRATO", "QUADRATO_ARROT"):
                cat = r.get("categoria", "")
                diff = r.get("differenza", 0)
                nota = r.get("note", "")
                stato = r.get("stato", "")
                impianto = r.get("impianto", "")
                anomalie.append(f"- {impianto} | {cat}: Stato {stato}, Differenza €{diff}, Note: {nota}")
        
        if anomalie:
            simplified_data.append(f"Data: {data_str}\n" + "\n".join(anomalie))
            
    if not simplified_data:
        return "L'analisi ha dato esito positivo per tutte le giornate: Nessuna anomalia rilevata! Tutte le quadrature sono perfette."
        
    prompt_text = (
        "Sei un assistente esperto in contabilità bancaria per stazioni di servizio. "
        "Di seguito ti fornisco i dati delle anomalie riscontrate durante la riconciliazione automatica degli incassi "
        "(i risultati contengono solo i dati NON quadrati o mancanti).\n\n"
        "DATI:\n" + "\n\n".join(simplified_data) + "\n\n"
        "Crea un breve report professionale in cui: \n"
        "1. Riassumi cosa non va (differenze, ammanchi, dati non trovati).\n"
        "2. Identifichi cosa manca chiaramente.\n"
        "3. Suggerisci eventuali azioni correttive rapide.\n"
        "Usa una formattazione markdown chiara."
    )
    
    if provider == "OpenRouter":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://calorsystems.local",
            "X-Title": "Calor Reconciler",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt_text}]
        }
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            return f"Errore API OpenRouter ({resp.status_code}): {resp.text}"
        except Exception as e:
            return f"Eccezione API OpenRouter: {str(e)}"
        
    elif provider == "Gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {"temperature": 0.2}
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 200:
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            return f"Errore API Gemini ({resp.status_code}): {resp.text}"
        except Exception as e:
            return f"Eccezione API Gemini: {str(e)}"
        
    return "Provider non supportato."
