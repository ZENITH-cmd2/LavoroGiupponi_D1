import pandas as pd
from datetime import timedelta
from .db_manager import salva_report_riconciliazione, get_impianto_id
from .motore_carte import log_missing

def riconcilia_petrolifere(df_fortech_agg, pv_code, file_petrolifere, conn):
    print(f"[-] Carte Petrolifere per PV {pv_code}...")
    
    impianto_id = get_impianto_id(conn, pv_code)
    if not impianto_id: return

    df_teo = df_fortech_agg[df_fortech_agg['PV'] == pv_code].copy()
    if df_teo.empty: return
    df_teo.rename(columns={'DATA': 'Data_Contabile', 'CARTA PETROLIFERA': 'Incasso_Petrolifera_Teorico'}, inplace=True)
    
    if not file_petrolifere: 
        log_missing(df_teo, impianto_id, 'carte_petrolifere', conn, 'Incasso_Petrolifera_Teorico', 'File iP Portal non caricato')
        return
        
    try:
        df_raw = pd.read_excel(file_petrolifere, header=None)
        header_row = 0
        for i, raw_row in df_raw.iterrows():
            if any(isinstance(v, str) and 'punto vendita' in v.lower() for v in raw_row.values) or \
               any(isinstance(v, str) and 'circuito' in v.lower() for v in raw_row.values):
                header_row = i
                break
                
        df_rea = pd.read_excel(file_petrolifere, header=header_row)
        df_rea.columns = [str(c).replace('\n', ' ').strip() for c in df_rea.columns]
        
        col_pv = next((c for c in df_rea.columns if c.lower() in ['punto vendita', 'pv', 'codice site']), 'Punto vendita')
        df_rea['Punto_Clean'] = df_rea[col_pv].astype(str).str.extract(r'(\d+)')[0]
        df_rea['Punto_Clean'] = pd.to_numeric(df_rea['Punto_Clean'], errors='coerce')
        
        df_rea = df_rea[df_rea['Punto_Clean'] == pv_code].copy()
        if df_rea.empty: 
            log_missing(df_teo, impianto_id, 'carte_petrolifere', conn, 'Incasso_Petrolifera_Teorico', f"Dati per PV {pv_code} non trovati nel file")
            return

        df_rea['Data_Norm'] = pd.to_datetime(df_rea['Data operazione'], errors='coerce', dayfirst=True).dt.normalize()
        df_rea['Importo_Portal'] = pd.to_numeric(df_rea['Importo'], errors='coerce').fillna(0)
        
        if 'Segno' in df_rea.columns:
            df_rea['is_neg'] = df_rea['Segno'].astype(str).str.contains('-')
            df_rea.loc[df_rea['is_neg'], 'Importo_Portal'] = -1 * df_rea.loc[df_rea['is_neg'], 'Importo_Portal'].abs()
            
        df_rea_agg = df_rea.groupby('Data_Norm')['Importo_Portal'].sum().reset_index()
    except Exception as e:
        print(f"Errore petrolifere pv {pv_code}: {e}")
        log_missing(df_teo, impianto_id, 'carte_petrolifere', conn, 'Incasso_Petrolifera_Teorico', f"File illeggibile/Struttura Errata ({e})")
        return
        
    df_match = pd.merge(df_teo, df_rea_agg, left_on='Data_Contabile', right_on='Data_Norm', how='left')
    df_match['Importo_Portal'] = df_match['Importo_Portal'].fillna(0.0)
    df_match['Differenza_Euro'] = df_match['Incasso_Petrolifera_Teorico'] - df_match['Importo_Portal']
    
    tolleranza_stretta = 1.00 # Margine di arrotondimento
    tolleranza_larga = 10.00 # Anomalia Lieve
    
    for _, row in df_match.iterrows():
        teo = row['Incasso_Petrolifera_Teorico']
        rea = row['Importo_Portal']
        diff_netta = row['Differenza_Euro']
        diff_abs = abs(diff_netta)
        
        # Algoritmo importato da Numia per iP Portal
        if pd.isna(row['Importo_Portal']) or (teo > 0 and rea == 0):
            stato = "NON_TROVATO"
        elif diff_abs <= tolleranza_stretta:
            stato = "QUADRATO"
        elif diff_abs <= tolleranza_larga:
            stato = "ANOMALIA_LIEVE"
        else:
            stato = "ANOMALIA_GRAVE"

        data_rif = row['Data_Contabile'].strftime("%Y-%m-%d")
        
        # Dettaglio perc
        diff_perc = 0
        if teo > 0:
            diff_perc = round((diff_netta / teo) * 100, 2)
            
        note_text = f"Diff: {diff_perc}%" if stato != "QUADRATO" and stato != "NON_TROVATO" else ""
        if stato == "NON_TROVATO": note_text = "Nessuna transazione iP Portal"
        
        record = {
            'impianto_id': impianto_id,
            'data_riferimento': data_rif,
            'categoria': 'carte_petrolifere',
            'valore_fortech': float(teo),
            'valore_reale': float(rea),
            'differenza': float(diff_netta),
            'stato': stato,
            'note': note_text
        }
        salva_report_riconciliazione(conn, record)

