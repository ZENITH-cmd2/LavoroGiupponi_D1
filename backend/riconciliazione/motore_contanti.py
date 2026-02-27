import pandas as pd
from datetime import timedelta
from .db_manager import salva_report_riconciliazione, get_impianto_id

def riconcilia_contanti(df_fortech_agg, pv_code, file_contanti, conn):
    """Calcola differenze e salva nel nuovo schema DB report_riconciliazioni"""
    print(f"\n[-] Contanti per PV {pv_code}...")
    
    impianto_id = get_impianto_id(conn, pv_code)
    if not impianto_id: return

    df_teo = df_fortech_agg[df_fortech_agg['PV'] == pv_code].copy()
    if df_teo.empty: return
    if not file_contanti: return
    
    df_teo = df_teo[['DATA', 'CONTANTI']].rename(
        columns={'DATA': 'Data_Teorica', 'CONTANTI': 'Importo_Teorico'}
    ).sort_values('Data_Teorica').reset_index(drop=True)
    df_teo = df_teo[df_teo['Importo_Teorico'] > 0]
    
    try:
        df_rea = pd.read_excel(file_contanti)
        df_rea = df_rea[['Registrazione//Data', 'Importo']].rename(
            columns={'Registrazione//Data': 'Data_Reale', 'Importo': 'Importo_Reale'}
        )
        df_rea.dropna(subset=['Data_Reale'], inplace=True)
        df_rea['Data_Reale'] = pd.to_datetime(df_rea['Data_Reale'], errors='coerce').dt.normalize()
        df_rea['Importo_Reale'] = pd.to_numeric(df_rea['Importo_Reale'], errors='coerce').fillna(0)
        df_rea = df_rea[df_rea['Importo_Reale'] > 0].sort_values('Data_Reale').reset_index(drop=True)
        df_rea['Matchato'] = False
    except Exception as e:
        print(f"Errore caricamento contanti {pv_code}: {e}")
        return

    giorni_inf, giorni_sup = 3, 7
    
    for idx_teo, row_teo in df_teo.iterrows():
        data_t, imp_t = row_teo['Data_Teorica'], row_teo['Importo_Teorico']
        lim_inf = data_t - timedelta(days=giorni_inf)
        lim_sup = data_t + timedelta(days=giorni_sup)
        
        candidati = df_rea[(df_rea['Data_Reale'] >= lim_inf) & 
                           (df_rea['Data_Reale'] <= lim_sup) & (~df_rea['Matchato'])]
        
        match_row = None
        min_diff = float('inf')
        for idx_rea, row_rea in candidati.iterrows():
            diff = abs(row_rea['Importo_Reale'] - imp_t)
            if diff < min_diff:
                min_diff = diff
                match_row = row_rea
                match_row['index_originale'] = idx_rea
        
        data_rif = data_t.strftime("%Y-%m-%d")
        record = {
            'impianto_id': impianto_id,
            'data_riferimento': data_rif,
            'categoria': 'contanti',
            'valore_fortech': float(imp_t),
            'valore_reale': 0.0,
            'differenza': float(imp_t),
            'stato': "ANOMALIA_GRAVE",
            'note': "NO_MATCH - Nessun versamento AS400 trovato in range (+7/-3)."
        }

        if match_row is not None:
            imp_r = float(match_row['Importo_Reale'])
            diff_netta = imp_t - imp_r
            diff_assoluta = abs(diff_netta)
            
            if diff_assoluta <= 5.00: 
                stato = "QUADRATO"
            elif diff_assoluta <= 20.00: 
                stato = "ANOMALIA_LIEVE"
            else: 
                stato = "ANOMALIA_GRAVE"

            if stato != "ANOMALIA_GRAVE":
                df_rea.at[match_row['index_originale'], 'Matchato'] = True
                record['note'] = "Vedi database AS400 per dettagli arrotondamenti."

            record['valore_reale'] = imp_r
            record['differenza'] = diff_netta
            record['stato'] = stato

        # Salva nel DB schema corretto
        salva_report_riconciliazione(conn, record)
