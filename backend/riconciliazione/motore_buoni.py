import pandas as pd
from datetime import timedelta
from .db_manager import salva_report_riconciliazione, get_impianto_id
from .motore_carte import log_missing

def riconcilia_buoni(df_fortech_agg, pv_code, file_buoni, conn):
    print(f"[-] Buoni per PV {pv_code}...")
    
    impianto_id = get_impianto_id(conn, pv_code)
    if not impianto_id: return

    df_teo = df_fortech_agg[df_fortech_agg['PV'] == pv_code].copy()
    if df_teo.empty: return
    df_teo.rename(columns={'DATA': 'Data_Fortech', 'BUONI_TOT': 'Incasso_Buoni_Teorico'}, inplace=True)
    df_teo['Data_Successiva_iPortal'] = df_teo['Data_Fortech'] + timedelta(days=1)
    
    if not file_buoni: 
        log_missing(df_teo, impianto_id, 'buoni_ip', conn, 'Incasso_Buoni_Teorico', 'File Buoni non caricato')
        return
        
    try:
        df_raw = pd.read_excel(file_buoni, header=None)
        header_row = 0
        for i, raw_row in df_raw.iterrows():
            if any(isinstance(v, str) and 'punto vendita' in v.lower() for v in raw_row.values) or \
               any(isinstance(v, str) and 'importo' in v.lower() for v in raw_row.values):
                header_row = i
                break
                
        df_rea = pd.read_excel(file_buoni, header=header_row)
        df_rea.columns = [str(c).replace('\n', ' ').strip() for c in df_rea.columns]
        
        col_pv = next((c for c in df_rea.columns if c.lower() in ['punto vendita', 'pv', 'codice site te']), 'Punto vendita')
        col_data = next((c for c in df_rea.columns if c.lower() in ['data registrazione documento', 'data registrazione', 'data documento']), None)
        
        df_rea['Punto_Clean'] = df_rea[col_pv].astype(str).str.extract(r'(\d+)')[0]
        df_rea['Punto_Clean'] = pd.to_numeric(df_rea['Punto_Clean'], errors='coerce')
        df_rea = df_rea[df_rea['Punto_Clean'] == pv_code].copy()
        
        if df_rea.empty: 
            log_missing(df_teo, impianto_id, 'buoni_ip', conn, 'Incasso_Buoni_Teorico', f"Dati PV {pv_code} assenti nel file Buoni")
            return
            
        df_rea['Importo_Reale'] = pd.to_numeric(df_rea['Importo'], errors='coerce').fillna(0)
        
        # Gestione segno negativo col_segno (dalla logica utente)
        col_segno = next((c for c in df_rea.columns if c.lower() == 'segno'), None)
        if col_segno and col_segno in df_rea.columns:
            df_rea['is_neg'] = df_rea[col_segno].astype(str).str.contains('-')
            df_rea.loc[df_rea['is_neg'], 'Importo_Reale'] = -1 * df_rea.loc[df_rea['is_neg'], 'Importo_Reale'].abs()
            
        df_rea.dropna(subset=[col_data], inplace=True)
        df_rea['Data_Registrazione_iP'] = pd.to_datetime(df_rea[col_data], errors='coerce').dt.normalize()
        
        df_rea['num_transazioni_iportal'] = 1
        df_rea_agg = df_rea.groupby('Data_Registrazione_iP').agg({
            'Importo_Reale': 'sum',
            'num_transazioni_iportal': 'sum'
        }).reset_index()
        
    except Exception as e:
        print(f"Errore Buoni PV {pv_code}: {e}")
        log_missing(df_teo, impianto_id, 'buoni_ip', conn, 'Incasso_Buoni_Teorico', f"File illeggibile/Struttura Errata ({e})")
        return
        
    df_match = pd.merge(df_teo, df_rea_agg, left_on='Data_Successiva_iPortal', right_on='Data_Registrazione_iP', how='left')
    df_match['Importo_Reale'] = df_match['Importo_Reale'].fillna(0.0)
    df_match['num_transazioni_iportal'] = df_match['num_transazioni_iportal'].fillna(0)
    df_match['Differenza_Euro'] = df_match['Incasso_Buoni_Teorico'] - df_match['Importo_Reale']
    
    tolleranza_stretta = 1.00
    tolleranza_larga = 10.00
    
    for _, row in df_match.iterrows():
        teo = row['Incasso_Buoni_Teorico']
        rea = row['Importo_Reale']
        diff_netta = row['Differenza_Euro']
        diff_abs = abs(diff_netta)
        num_trans = int(row['num_transazioni_iportal'])
        
        if pd.isna(row['Importo_Reale']) or (teo > 0 and rea == 0):
            stato = "NON_TROVATO"
        elif diff_abs <= tolleranza_stretta:
            stato = "QUADRATO"
        elif diff_abs <= tolleranza_larga:
            stato = "ANOMALIA_LIEVE"
        else:
            stato = "ANOMALIA_GRAVE"

        data_rif = row['Data_Fortech'].strftime("%Y-%m-%d")

        diff_perc = 0
        if teo > 0:
            diff_perc = round((diff_netta / teo) * 100, 2)
            
        note_text = f"Diff: {diff_perc}%. Su iP Portal (+1g) ({num_trans} tr.)" if stato != "QUADRATO" and stato != "NON_TROVATO" else f"OK ({num_trans} tr. su iP Portal)"
        if stato == "NON_TROVATO": note_text = "Nessun buono su iP Portal (+1g)"

        record = {
            'impianto_id': impianto_id,
            'data_riferimento': data_rif,
            'categoria': 'buoni_ip',
            'valore_fortech': float(teo),
            'valore_reale': float(rea),
            'differenza': float(diff_netta),
            'stato': stato,
            'note': note_text
        }
        salva_report_riconciliazione(conn, record)
