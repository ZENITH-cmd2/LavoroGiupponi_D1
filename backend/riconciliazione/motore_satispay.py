import pandas as pd
from .db_manager import salva_report_riconciliazione, get_impianto_id

def riconcilia_satispay(df_fortech_agg, pv_code, str_pv_code, file_satispay, conn):
    print(f"[-] Satispay per PV {pv_code}...")
    
    impianto_id = get_impianto_id(conn, pv_code)
    if not impianto_id: return

    df_teo = df_fortech_agg[df_fortech_agg['PV'] == pv_code].copy()
    if df_teo.empty: return
    if not file_satispay: return
    
    df_teo.rename(columns={'DATA': 'Data_Contabile', 'SATISPAY': 'Incasso_Satispay_Teorico'}, inplace=True)
    
    try:
        df_rea = pd.read_excel(file_satispay)
        df_rea.columns = [str(c).replace('\n', ' ').strip().lower() for c in df_rea.columns]
        
        col_data = next((c for c in ['data transazione', 'data'] if c in df_rea.columns), None)
        col_pv = 'codice negozio' if 'codice negozio' in df_rea.columns else next((c for c in ['negozio', 'punto vendita'] if c in df_rea.columns), None)
        
        if col_pv:
            df_rea[col_pv] = df_rea[col_pv].astype(str)
            if df_rea[col_pv].str.contains(str_pv_code, na=False).any():
                df_rea = df_rea[df_rea[col_pv].str.contains(str_pv_code, na=False)].copy()
                
        df_rea['Importo_Satispay'] = pd.to_numeric(df_rea['importo totale'], errors='coerce').fillna(0)
        df_rea.dropna(subset=[col_data], inplace=True)
        df_rea['Data_Norm'] = pd.to_datetime(df_rea[col_data], errors='coerce').dt.normalize()
        
        df_rea_agg = df_rea.groupby('Data_Norm')['Importo_Satispay'].sum().reset_index()
    except Exception as e:
        print(f"Errore Satispay pv {pv_code}: {e}")
        return
        
    df_match = pd.merge(df_teo, df_rea_agg, left_on='Data_Contabile', right_on='Data_Norm', how='left')
    df_match['Importo_Satispay'] = df_match['Importo_Satispay'].fillna(0)
    df_match['Differenza_Euro'] = df_match['Incasso_Satispay_Teorico'] - df_match['Importo_Satispay']
    
    for _, row in df_match.iterrows():
        teo = row['Incasso_Satispay_Teorico']
        rea = row['Importo_Satispay']
        diff_netta = row['Differenza_Euro']
        diff_assoluta = abs(diff_netta)
        
        if teo == 0 and rea == 0: stato = "QUADRATO"
        elif teo > 0 and rea == 0: stato = "NON_TROVATO"
        elif diff_assoluta <= 0.50: stato = "QUADRATO"
        elif diff_assoluta <= 5.00: stato = "ANOMALIA_LIEVE"
        else: stato = "ANOMALIA_GRAVE"

        data_rif = row['Data_Contabile'].strftime("%Y-%m-%d")

        record = {
            'impianto_id': impianto_id,
            'data_riferimento': data_rif,
            'categoria': 'satispay',
            'valore_fortech': float(teo),
            'valore_reale': float(rea),
            'differenza': float(diff_netta),
            'stato': stato,
            'note': ""
        }
        salva_report_riconciliazione(conn, record)
