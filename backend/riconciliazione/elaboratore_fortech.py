import pandas as pd
import datetime
from .db_manager import get_impianto_id

def elabora_dati_fortech(file_fortech, conn):
    """
    Estrae i dati Fortech, salva in master se necessario e restituisce 
    un DataFrame raggruppato e la lista dei pv.
    """
    if not file_fortech: return None, []
    print("\n--- AVVIO ESTRAZIONE FORTECH ---")
    
    try:
        df_orig = pd.read_excel(file_fortech, sheet_name='Incassi')
        
        df_orig['PV'] = pd.to_numeric(df_orig['CodicePV'], errors='coerce')
        df_orig['DATA'] = pd.to_datetime(df_orig['DataContabile'], errors='coerce', dayfirst=True).dt.normalize()
        
        cols_to_fill = ['BANCOMAT GESTORE', 'CARTA CREDITO GESTORE', 'AMEX', 'CARTA CREDITO GENERICA', 
                        'PAGOBANCOMAT', 'TBS', 'DKV', 'UTA', 'CARTAMAXIMA', 'CARTAPETROLIFERA', 
                        'BUONI', 'CONTANTI', 'PAGAMENTIINNOVATIVI']
                        
        existing_cols = [col for col in cols_to_fill if col in df_orig.columns]
        df_orig[existing_cols] = df_orig[existing_cols].fillna(0)
        
        # Calcoli aggregati (stessa logica di riconciliazione_totale.py)
        cc_cols = ['BANCOMAT GESTORE', 'CARTA CREDITO GESTORE', 'AMEX', 'CARTA CREDITO GENERICA', 'PAGOBANCOMAT', 'TBS']
        df_orig['CARTE DI CREDITO'] = sum(df_orig.get(col, 0) for col in cc_cols)
        
        cp_cols = ['DKV', 'UTA', 'CARTAMAXIMA']
        df_orig['CARTA PETROLIFERA'] = sum(df_orig.get(col, 0) for col in cp_cols)
        df_orig['BUONI_CALCOLATI'] = sum(df_orig.get(col, 0) for col in ['CARTAPETROLIFERA', 'BUONI'])
        df_orig['SATISPAY_CALC'] = df_orig.get('PAGAMENTIINNOVATIVI', 0)
        df_orig['CONTANTI_CALC'] = df_orig.get('CONTANTI', 0)
        
        final_cols = ['PV', 'DATA', 'CONTANTI_CALC', 'CARTE DI CREDITO', 'CARTA PETROLIFERA', 'BUONI_CALCOLATI', 'SATISPAY_CALC', 
                      'BUONI', 'CARTAPETROLIFERA', 'DKV', 'UTA', 'CARTAMAXIMA', 'BANCOMAT GESTORE', 'CARTA CREDITO GESTORE', 
                      'AMEX', 'CARTA CREDITO GENERICA', 'PAGOBANCOMAT', 'TBS', 'CorrispettivoTotale']
        
        missing = [c for c in final_cols if c not in df_orig.columns]
        for c in missing: df_orig[c] = 0
        
        df_final = df_orig[final_cols].copy()
        
        # Salvataggio nel database relazionale master (import_fortech_master)
        # Svuotiamo i record vecchi per semplicit in questa demo
        cur = conn.cursor()
        cur.execute("DELETE FROM import_fortech_master")
        
        for idx, row in df_orig.iterrows():
            pv = row['PV']
            impianto_id = get_impianto_id(conn, pv)
            if not impianto_id: continue
            
            data_str = row['DATA'].strftime("%Y-%m-%d")
            
            # Mettiamo solo i totali principali per mantenere la compatibilita con LavoroGiupponi
            cur.execute("""
                INSERT INTO import_fortech_master 
                (impianto_id, codice_pv, data_contabile, corrispettivo_totale, 
                 incasso_carte_bancarie_teorico, incasso_carte_petrolifere_teorico, 
                 incasso_buoni_teorico, incasso_satispay_teorico, incasso_contanti_teorico)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                impianto_id, str(pv), data_str, float(row.get('CorrispettivoTotale', 0)),
                float(row['CARTE DI CREDITO']), float(row['CARTA PETROLIFERA']),
                float(row['BUONI_CALCOLATI']), float(row['SATISPAY_CALC']), float(row['CONTANTI_CALC'])
            ))
        conn.commit()

        # Raggruppamento in corso...
        df_grouped = df_final.groupby(['PV', 'DATA']).sum().reset_index()
        df_grouped.rename(columns={'BUONI_CALCOLATI': 'BUONI_TOT', 'SATISPAY_CALC': 'SATISPAY', 'CONTANTI_CALC': 'CONTANTI'}, inplace=True)
        
        lista_pv = df_grouped['PV'].dropna().unique()
        print(f"Fortech: estratti {len(lista_pv)} Punti Vendita: {lista_pv}")
        
        return df_grouped, lista_pv
        
    except Exception as e:
        print(f"ERRORE irreversibile Fortech: {e}")
        return None, []
