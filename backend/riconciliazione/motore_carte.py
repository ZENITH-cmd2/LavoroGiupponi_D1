import pandas as pd
from .db_manager import salva_report_riconciliazione, get_impianto_id

def log_missing(df_teo, impianto_id, categoria, conn, importo_col, error_msg):
    # Trova la colonna della data (può essere Data_Contabile, Data_Fortech o DATA)
    data_col = 'Data_Contabile'
    if 'Data_Fortech' in df_teo.columns: data_col = 'Data_Fortech'
    elif 'DATA' in df_teo.columns: data_col = 'DATA'
    
    for _, row in df_teo.iterrows():
        teo = row[importo_col]
        stato = "NON_TROVATO" if teo > 0 else "QUADRATO"
        # Non registriamo gli 0 a 0 se manca il file, ma segniamo le anomalie
        record = {
            'impianto_id': impianto_id,
            'data_riferimento': row[data_col].strftime("%Y-%m-%d"),
            'categoria': categoria,
            'valore_fortech': float(teo),
            'valore_reale': 0.0,
            'differenza': float(teo),
            'stato': stato,
            'note': error_msg if teo > 0 else ""
        }
        salva_report_riconciliazione(conn, record)

def riconcilia_carte(df_fortech_agg, pv_code, file_carte, conn):
    print(f"[-] Carte Credito per PV {pv_code}...")
    
    impianto_id = get_impianto_id(conn, pv_code)
    if not impianto_id: return

    df_teo = df_fortech_agg[df_fortech_agg['PV'] == pv_code].copy()
    if df_teo.empty: return
    df_teo.rename(columns={'DATA': 'Data_Contabile', 'CARTE DI CREDITO': 'Incasso_CC_Teorico'}, inplace=True)
    
    if not file_carte: 
        log_missing(df_teo, impianto_id, 'carte_bancarie', conn, 'Incasso_CC_Teorico', 'File Carte Bancarie non caricato')
        return
    
    try:
        df_raw = pd.read_excel(file_carte, header=None)
        header_row = 0
        for i, raw_row in df_raw.iterrows():
            if any(isinstance(v, str) and 'data' in v.lower() for v in raw_row.values) and \
               any(isinstance(v, str) and 'importo' in v.lower() for v in raw_row.values):
                header_row = i
                break
                
        df_rea = pd.read_excel(file_carte, header=header_row)
        df_rea.columns = [str(c).replace('\n', ' ').strip() for c in df_rea.columns]
        
        col_data = next((c for c in df_rea.columns if c.lower() in ['data e ora', 'data transazione', 'data']), 'Data e ora')
        col_importo = next((c for c in df_rea.columns if 'importo' in c.lower()), 'Importo')
        
        if col_data not in df_rea.columns:
            raise ValueError("Colonna data/ora non trovata")
            
        df_rea.dropna(subset=[col_data], inplace=True)
        df_rea['Data_Norm'] = pd.to_datetime(df_rea[col_data], errors='coerce', dayfirst=True).dt.normalize()
        df_rea['Importo_Numia'] = pd.to_numeric(df_rea[col_importo], errors='coerce').fillna(0)
        
        df_rea_agg = df_rea.groupby('Data_Norm').agg({'Importo_Numia': 'sum'}).reset_index()
        
    except Exception as e:
        print(f"Errore carte PV {pv_code}: {e}")
        log_missing(df_teo, impianto_id, 'carte_bancarie', conn, 'Incasso_CC_Teorico', f"File illeggibile/Struttura Errata ({e})")
        return
        
    df_match = pd.merge(df_teo, df_rea_agg, left_on='Data_Contabile', right_on='Data_Norm', how='left')
    df_match['Importo_Numia'] = df_match['Importo_Numia'].fillna(0)
    df_match['Differenza_Euro'] = df_match['Incasso_CC_Teorico'] - df_match['Importo_Numia']
    
    for _, row in df_match.iterrows():
        teo = row['Incasso_CC_Teorico']
        rea = row['Importo_Numia']
        diff_netta = row['Differenza_Euro']
        diff_assoluta = abs(diff_netta)
        
        if teo == 0 and rea == 0: stato = "QUADRATO"
        elif teo > 0 and rea == 0: stato = "NON_TROVATO"
        elif diff_assoluta <= 0.50: stato = "QUADRATO"
        elif diff_assoluta <= 5.00: stato = "ANOMALIA_LIEVE"
        else: stato = "ANOMALIA_GRAVE"

        data_rif = row['Data_Contabile'].strftime("%Y-%m-%d")
        note = "Numia OK" if stato == "QUADRATO" else ("Nessun versamento Excel" if stato == "NON_TROVATO" else "Verificare POS")

        record = {
            'impianto_id': impianto_id,
            'data_riferimento': data_rif,
            'categoria': 'carte_bancarie',
            'valore_fortech': float(teo),
            'valore_reale': float(rea),
            'differenza': float(diff_netta),
            'stato': stato,
            'note': note
        }
        salva_report_riconciliazione(conn, record)
