import sqlite3
import pandas as pd
import numpy as np
import os
import re
import glob
from datetime import datetime, timedelta

# ==========================================
# CONFIGURAZIONI GLOBALI
# ==========================================
MAIN_DIR = r"C:\Users\Utente\Desktop\Lavoro"
OUTPUT_DIR = os.path.join(MAIN_DIR, "Lavoro_giupponi4")
DB_PATH = os.path.join(OUTPUT_DIR, "database_riconciliazioni.db")

# Variabili globali per i file sorgente (verranno valorizzate a runtime)
FILE_FORTECH = None
FILE_CONTANTI = None
FILE_CARTE = None
FILE_PETROLIFERE = None
FILE_BUONI = None
FILE_SATISPAY = None

def identifica_file(input_dir):
    global FILE_FORTECH, FILE_CONTANTI, FILE_CARTE, FILE_PETROLIFERE, FILE_BUONI, FILE_SATISPAY
    
    # Reset all variables to prevent leakage between runs
    FILE_FORTECH = FILE_CONTANTI = FILE_CARTE = FILE_PETROLIFERE = FILE_BUONI = FILE_SATISPAY = None

    file_excel = glob.glob(os.path.join(input_dir, "*.xlsx")) + glob.glob(os.path.join(input_dir, "*.xls")) + glob.glob(os.path.join(input_dir, "*.csv"))
    
    for file_path in file_excel:
        nome_file = os.path.basename(file_path).lower()
        # Normalizziamo sia gli underscore che gli spazi in un formato unico per il match
        nome_norm = nome_file.replace("_", " ")
        
        # Logica di identificazione
        if "fortech" in nome_norm or nome_file.startswith("a_"):
            FILE_FORTECH = file_path
        elif "contanti" in nome_norm or nome_file.startswith("1_"):
            FILE_CONTANTI = file_path
        elif ("carte bancarie" in nome_norm or "numia" in nome_norm or nome_file.startswith("2_")) and "petrolifere" not in nome_norm:
            FILE_CARTE = file_path
        elif "petrolifere" in nome_norm or "azzurro" in nome_norm or nome_file.startswith("3_"):
            FILE_PETROLIFERE = file_path
        elif "buoni" in nome_norm or "rosso" in nome_norm or nome_file.startswith("4_"):
            FILE_BUONI = file_path
        elif "satispay" in nome_norm or "grigio" in nome_norm or nome_file.startswith("5_"):
            FILE_SATISPAY = file_path

    print("\n--- FILE IDENTIFICATI ---")
    print(f"FORTECH:           {FILE_FORTECH}")
    print(f"CONTANTI:          {FILE_CONTANTI}")
    print(f"CARTE BANCARIE:    {FILE_CARTE}")
    print(f"CARTE PETROLIFERE: {FILE_PETROLIFERE}")
    print(f"BUONI IP:          {FILE_BUONI}")
    print(f"SATISPAY:          {FILE_SATISPAY}")

def init_db():
    """Inizializza la cartella di output e si connette al database SQLite."""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    
    conn = sqlite3.connect(DB_PATH)
    return conn

def salva_dataframe_su_db(df, table_name, conn):
    """Salva o sostituisce i datiframe nella rispettiva tabella del Database."""
    if df.empty:
        print(f"[{table_name}] Nessun dato da salvare.")
        return
        
    try:
        # Pulisco eventuali vecchie registrazioni per lo stesso PV
        # Salviamo l'intero DF nella tabella designata (Se esiste la rimpiazza, per riavvi puliti)
        df.to_sql(table_name, conn, if_exists='replace', index=False)
        print(f"[{table_name}] Salvate {len(df)} righe con successo nel Database.")
    except Exception as e:
        print(f"[{table_name}] ERRORE salvataggio su DB: {e}")

# ==========================================
# MOTORI DI RICONCILIAZIONE
# ==========================================

def elabora_dati_fortech(conn):
    """Estrae i dati Fortech e raggruppa giornalmente per PV. Genera i Teorici."""
    print("\n--- AVVIO ESTRAZIONE FORTECH ---")
    if not FILE_FORTECH: return None, []
    
    try:
        df_orig = pd.read_excel(FILE_FORTECH, sheet_name='Incassi')
        
        df_orig['PV'] = pd.to_numeric(df_orig['CodicePV'], errors='coerce')
        df_orig['DATA'] = pd.to_datetime(df_orig['DataContabile']).dt.normalize()
        
        cols_to_fill = ['BANCOMAT GESTORE', 'CARTA CREDITO GESTORE', 'AMEX', 'CARTA CREDITO GENERICA', 
                        'PAGOBANCOMAT', 'TBS', 'DKV', 'UTA', 'CARTAMAXIMA', 'CARTAPETROLIFERA', 
                        'BUONI', 'CONTANTI', 'PAGAMENTIINNOVATIVI']
                        
        existing_cols = [col for col in cols_to_fill if col in df_orig.columns]
        df_orig[existing_cols] = df_orig[existing_cols].fillna(0)
        
        # Calcoli aggregati
        cc_cols = ['BANCOMAT GESTORE', 'CARTA CREDITO GESTORE', 'AMEX', 'CARTA CREDITO GENERICA', 'PAGOBANCOMAT', 'TBS']
        df_orig['CARTE DI CREDITO'] = sum(df_orig.get(col, 0) for col in cc_cols)
        
        cp_cols = ['DKV', 'UTA', 'CARTAMAXIMA']
        df_orig['CARTA PETROLIFERA'] = sum(df_orig.get(col, 0) for col in cp_cols)
        df_orig['BUONI_CALCOLATI'] = sum(df_orig.get(col, 0) for col in ['CARTAPETROLIFERA', 'BUONI'])
        df_orig['SATISPAY_CALC'] = df_orig.get('PAGAMENTIINNOVATIVI', 0)
        df_orig['CONTANTI_CALC'] = df_orig.get('CONTANTI', 0)
        
        final_cols = ['PV', 'DATA', 'CONTANTI_CALC', 'CARTE DI CREDITO', 'CARTA PETROLIFERA', 'BUONI_CALCOLATI', 'SATISPAY_CALC', 
                      'BUONI', 'CARTAPETROLIFERA', 'DKV', 'UTA', 'CARTAMAXIMA', 'BANCOMAT GESTORE', 'CARTA CREDITO GESTORE', 'AMEX', 'CARTA CREDITO GENERICA', 'PAGOBANCOMAT', 'TBS']
        
        missing = [c for c in final_cols if c not in df_orig.columns]
        for c in missing: df_orig[c] = 0
        
        df_final = df_orig[final_cols].copy()
        
        print("Raggruppamento in corso...")
        df_grouped = df_final.groupby(['PV', 'DATA']).sum().reset_index()
        df_grouped.rename(columns={'BUONI_CALCOLATI': 'BUONI_TOT', 'SATISPAY_CALC': 'SATISPAY', 'CONTANTI_CALC': 'CONTANTI'}, inplace=True)
        
        # Salva i teorici complessivi per avere un recap
        salva_dataframe_su_db(df_grouped, "Fortech_Teorico_Globale", conn)
        
        lista_pv = df_grouped['PV'].dropna().unique()
        print(f"Ricavati {len(lista_pv)} Punti Vendita: {lista_pv}")
        
        # Generiamo le views specifiche o ritorniamo il dataframe intero e la lista dei pv
        return df_grouped, lista_pv
        
    except Exception as e:
        print(f"ERRORE irreversibile Fortech: {e}")
        return None, []

def riconcilia_contanti(df_fortech_agg, pv_code, conn):
    print(f"\n[-] Contanti per PV {pv_code}...")
    table_name = f"Contanti_{int(pv_code)}"
    
    df_teo = df_fortech_agg[df_fortech_agg['PV'] == pv_code].copy()
    if df_teo.empty: return
    if not FILE_CONTANTI: return
    
    df_teo = df_teo[['DATA', 'CONTANTI']].rename(columns={'DATA': 'Data_Teorica', 'CONTANTI': 'Importo_Teorico'}).sort_values('Data_Teorica').reset_index(drop=True)
    df_teo = df_teo[df_teo['Importo_Teorico'] > 0]
    
    try:
        df_rea = pd.read_excel(FILE_CONTANTI)
        df_rea = df_rea[['Registrazione//Data', 'Importo']].rename(columns={'Registrazione//Data': 'Data_Reale', 'Importo': 'Importo_Reale'})
        df_rea.dropna(subset=['Data_Reale'], inplace=True)
        df_rea['Data_Reale'] = pd.to_datetime(df_rea['Data_Reale'], errors='coerce').dt.normalize()
        df_rea['Importo_Reale'] = pd.to_numeric(df_rea['Importo_Reale'], errors='coerce').fillna(0)
        df_rea = df_rea[df_rea['Importo_Reale'] > 0].sort_values('Data_Reale').reset_index(drop=True)
        df_rea['Matchato'] = False
    except Exception as e:
        return

    giorni_inf, giorni_sup = 3, 7
    risultati = []
    
    for idx_teo, row_teo in df_teo.iterrows():
        data_t, imp_t = row_teo['Data_Teorica'], row_teo['Importo_Teorico']
        lim_inf, lim_sup = data_t - timedelta(days=giorni_inf), data_t + timedelta(days=giorni_sup)
        
        candidati = df_rea[(df_rea['Data_Reale'] >= lim_inf) & (df_rea['Data_Reale'] <= lim_sup) & (~df_rea['Matchato'])]
        
        match_row = None
        min_diff = float('inf')
        for idx_rea, row_rea in candidati.iterrows():
            diff = abs(row_rea['Importo_Reale'] - imp_t)
            if diff < min_diff:
                min_diff = diff
                match_row = row_rea
                match_row['index_originale'] = idx_rea
        
        if match_row is not None:
            diff_netta = imp_t - match_row['Importo_Reale']
            diff_assoluta = abs(diff_netta)
            
            if diff_assoluta <= 5.00: stato = "✅ MATCH_PERFETTO"
            elif diff_assoluta <= 20.00: stato = "🟡 MATCH_LIEVE"
            else: stato = "🔴 NO_MATCH (fuori tolleranza)"
            
            if stato != "🔴 NO_MATCH (fuori tolleranza)":
                df_rea.at[match_row['index_originale'], 'Matchato'] = True
                
            risultati.append({'Data_Teorica': data_t, 'Importo_Teorico': imp_t, 'Data_Reale': match_row['Data_Reale'], 'Importo_Reale': match_row['Importo_Reale'], 'Differenza_Euro': diff_netta, 'Stato': stato, 'PV': pv_code})
        else:
            risultati.append({'Data_Teorica': data_t, 'Importo_Teorico': imp_t, 'Data_Reale': pd.NaT, 'Importo_Reale': 0.0, 'Differenza_Euro': imp_t, 'Stato': "🔴 NO_MATCH", 'PV': pv_code})
            
    df_risultati = pd.DataFrame(risultati)
    salva_dataframe_su_db(df_risultati, table_name, conn)


def riconcilia_carte(df_fortech_agg, pv_code, conn):
    print(f"[-] Carte Credito per PV {pv_code}...")
    table_name = f"CarteCredito_{int(pv_code)}"
    
    df_teo = df_fortech_agg[df_fortech_agg['PV'] == pv_code].copy()
    if df_teo.empty: return
    if not FILE_CARTE: return
    
    df_teo.rename(columns={'DATA': 'Data_Contabile', 'CARTE DI CREDITO': 'Incasso_CC_Teorico'}, inplace=True)
    
    try:
        df_rea = pd.read_excel(FILE_CARTE, header=2)
        df_rea.columns = [str(c).replace('\n', ' ').strip() for c in df_rea.columns]
        
        col_data = next((c for c in ['data e ora', 'data transazione'] if c.lower() in [x.lower() for x in df_rea.columns]), 'Data e ora')
        col_importo = next((c for c in ['importo totale', 'importo'] if c.lower() in [x.lower() for x in df_rea.columns]), 'Importo')
        
        df_rea.dropna(subset=[col_data], inplace=True)
        df_rea['Data_Norm'] = pd.to_datetime(df_rea[col_data], errors='coerce').dt.normalize()
        df_rea['Importo_Numia'] = pd.to_numeric(df_rea[col_importo], errors='coerce').fillna(0)
        
        df_rea_agg = df_rea.groupby('Data_Norm').agg({'Importo_Numia': 'sum'}).reset_index()
        
    except Exception as e:
        return
        
    df_match = pd.merge(df_teo, df_rea_agg, left_on='Data_Contabile', right_on='Data_Norm', how='left')
    df_match['Importo_Numia'] = df_match['Importo_Numia'].fillna(0)
    df_match['Differenza_Euro'] = df_match['Incasso_CC_Teorico'] - df_match['Importo_Numia']
    
    def calcola_stato(row):
        teo = row['Incasso_CC_Teorico']
        rea = row['Importo_Numia']
        diff = abs(row['Differenza_Euro'])
        
        if teo == 0 and rea == 0: return "✅ QUADRATO"
        if teo > 0 and rea == 0: return "❓ NON_TROVATO"
        if diff <= 0.50: return "✅ QUADRATO"
        if diff <= 5.00: return "🟡 ANOMALIA_LIEVE"
        return "🔴 ANOMALIA_GRAVE"
        
    df_match['Stato'] = df_match.apply(calcola_stato, axis=1)
    
    col_export = ['Data_Contabile', 'Incasso_CC_Teorico', 'Importo_Numia', 'Differenza_Euro', 'Stato', 'PV']
    salva_dataframe_su_db(df_match[col_export], table_name, conn)

def riconcilia_petrolifere(df_fortech_agg, pv_code, conn):
    print(f"[-] Carte Petrolifere per PV {pv_code}...")
    table_name = f"Petrolifere_{int(pv_code)}"
    
    df_teo = df_fortech_agg[df_fortech_agg['PV'] == pv_code].copy()
    if df_teo.empty: return
    if not FILE_PETROLIFERE: return
    
    df_teo.rename(columns={'DATA': 'Data_Contabile', 'CARTA PETROLIFERA': 'Incasso_Petrolifera_Teorico'}, inplace=True)
    
    try:
        df_rea = pd.read_excel(FILE_PETROLIFERE, header=1)
        df_rea.columns = [str(c).replace('\n', ' ').strip() for c in df_rea.columns]
        
        df_rea['Punto vendita'] = pd.to_numeric(df_rea['Punto vendita'], errors='coerce')
        df_rea = df_rea[df_rea['Punto vendita'] == pv_code].copy()
        if df_rea.empty: return

        df_rea['Circuito_Mappato'] = df_rea['Circuito'].str.upper().replace({'CM IPPLUS': 'CARTAMAXIMA'})
        df_rea['Data_Norm'] = pd.to_datetime(df_rea['Data operazione'], errors='coerce').dt.normalize()
        df_rea['Importo_Portal'] = pd.to_numeric(df_rea['Importo'], errors='coerce').fillna(0)
        
        if 'Segno' in df_rea.columns:
            df_rea['is_neg'] = df_rea['Segno'].astype(str).str.contains('-')
            df_rea.loc[df_rea['is_neg'], 'Importo_Portal'] = -1 * df_rea.loc[df_rea['is_neg'], 'Importo_Portal'].abs()
            
        df_rea_agg = df_rea.groupby('Data_Norm')['Importo_Portal'].sum().reset_index()
    except Exception as e:
        return
        
    df_match = pd.merge(df_teo, df_rea_agg, left_on='Data_Contabile', right_on='Data_Norm', how='left')
    df_match['Importo_Portal'] = df_match['Importo_Portal'].fillna(0)
    df_match['Differenza_Euro'] = df_match['Incasso_Petrolifera_Teorico'] - df_match['Importo_Portal']
    
    def calcola_stato(row):
        teo = row['Incasso_Petrolifera_Teorico']
        rea = row['Importo_Portal']
        diff = abs(row['Differenza_Euro'])
        
        if teo == 0 and rea == 0: return "✅ QUADRATO"
        if teo > 0 and rea == 0: return "❓ NON_TROVATO"
        if diff <= 1.00: return "✅ QUADRATO"
        if diff <= 10.00: return "🟡 ANOMALIA_LIEVE"
        return "🔴 ANOMALIA_GRAVE"

    df_match['Stato'] = df_match.apply(calcola_stato, axis=1)
    col_export = ['Data_Contabile', 'Incasso_Petrolifera_Teorico', 'Importo_Portal', 'Differenza_Euro', 'Stato', 'PV']
    salva_dataframe_su_db(df_match[col_export], table_name, conn)

def riconcilia_buoni(df_fortech_agg, pv_code, conn):
    print(f"[-] Buoni per PV {pv_code}...")
    table_name = f"Buoni_{int(pv_code)}"
    
    df_teo = df_fortech_agg[df_fortech_agg['PV'] == pv_code].copy()
    if df_teo.empty: return
    if not FILE_BUONI: return
    
    df_teo.rename(columns={'DATA': 'Data_Fortech', 'BUONI_TOT': 'Incasso_Buoni_Teorico'}, inplace=True)
    df_teo['Data_Successiva_iPortal'] = df_teo['Data_Fortech'] + timedelta(days=1)
    
    try:
        df_rea = pd.read_excel(FILE_BUONI, header=1)
        df_rea.columns = [str(c).replace('\n', ' ').strip() for c in df_rea.columns]
        
        col_pv = next((c for c in ['Punto vendita', 'PV', 'Codice site TE'] if c.lower() in [x.lower() for x in df_rea.columns]), 'Punto vendita')
        col_data = next((c for c in ['Data registrazione documento', 'Data registrazione', 'Data documento'] if c.lower() in [x.lower() for x in df_rea.columns]), None)
        
        df_rea[col_pv] = pd.to_numeric(df_rea[col_pv], errors='coerce')
        df_rea = df_rea[df_rea[col_pv] == pv_code].copy()
        
        df_rea['Importo_Reale'] = pd.to_numeric(df_rea['Importo'], errors='coerce').fillna(0)
        df_rea.dropna(subset=[col_data], inplace=True)
        df_rea['Data_Registrazione_iP'] = pd.to_datetime(df_rea[col_data], errors='coerce').dt.normalize()
        
        df_rea_agg = df_rea.groupby('Data_Registrazione_iP')['Importo_Reale'].sum().reset_index()
    except Exception as e:
        return
        
    df_match = pd.merge(df_teo, df_rea_agg, left_on='Data_Successiva_iPortal', right_on='Data_Registrazione_iP', how='left')
    df_match['Importo_Reale'] = df_match['Importo_Reale'].fillna(0)
    df_match['Differenza_Euro'] = df_match['Incasso_Buoni_Teorico'] - df_match['Importo_Reale']
    
    def calcola_stato(row):
        teo = row['Incasso_Buoni_Teorico']
        rea = row['Importo_Reale']
        diff = abs(row['Differenza_Euro'])
        
        if teo == 0 and rea == 0: return "✅ QUADRATO"
        if teo > 0 and rea == 0: return "❓ NON_TROVATO"
        if diff <= 1.00: return "✅ QUADRATO"
        if diff <= 10.00: return "🟡 ANOMALIA_LIEVE"
        return "🔴 ANOMALIA_GRAVE"

    df_match['Stato'] = df_match.apply(calcola_stato, axis=1)
    df_match.rename(columns={'Data_Fortech': 'Data_Contabile'}, inplace=True)
    
    col_export = ['Data_Contabile', 'Incasso_Buoni_Teorico', 'Importo_Reale', 'Differenza_Euro', 'Stato', 'PV']
    salva_dataframe_su_db(df_match[col_export], table_name, conn)

def riconcilia_satispay(df_fortech_agg, pv_code, str_pv_code, conn):
    print(f"[-] Satispay per PV {pv_code}...")
    table_name = f"Satispay_{int(pv_code)}"
    
    df_teo = df_fortech_agg[df_fortech_agg['PV'] == pv_code].copy()
    if df_teo.empty: return
    if not FILE_SATISPAY: return
    
    df_teo.rename(columns={'DATA': 'Data_Contabile', 'SATISPAY': 'Incasso_Satispay_Teorico'}, inplace=True)
    
    try:
        df_rea = pd.read_excel(FILE_SATISPAY)
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
        return
        
    df_match = pd.merge(df_teo, df_rea_agg, left_on='Data_Contabile', right_on='Data_Norm', how='left')
    df_match['Importo_Satispay'] = df_match['Importo_Satispay'].fillna(0)
    df_match['Differenza_Euro'] = df_match['Incasso_Satispay_Teorico'] - df_match['Importo_Satispay']
    
    def calcola_stato(row):
        teo = row['Incasso_Satispay_Teorico']
        rea = row['Importo_Satispay']
        diff = abs(row['Differenza_Euro'])
        
        if teo == 0 and rea == 0: return "✅ QUADRATO"
        if teo > 0 and rea == 0: return "❓ NON_TROVATO"
        if diff <= 0.50: return "✅ QUADRATO"
        if diff <= 5.00: return "🟡 ANOMALIA_LIEVE"
        return "🔴 ANOMALIA_GRAVE"

    df_match['Stato'] = df_match.apply(calcola_stato, axis=1)
    
    col_export = ['Data_Contabile', 'Incasso_Satispay_Teorico', 'Importo_Satispay', 'Differenza_Euro', 'Stato', 'PV']
    salva_dataframe_su_db(df_match[col_export], table_name, conn)


# ==========================================
# HUB PRINCIPALE
# ==========================================
def orchestratore_unificato(input_dir=None):
    print("*" * 50)
    print("   AVVIO RICONCILIAZIONE UNIFICATA A DATABASE   ")
    print("*" * 50)
    
    if not input_dir:
        input_dir = input("\nInserisci il percorso della cartella contenente i file da analizzare (es. C:\\percorso\\cartella): ").strip()
        input_dir = input_dir.strip('"').strip("'")
    
    if not os.path.isdir(input_dir):
        msg = f"ERRORE: La cartella specificata non esiste: {input_dir}"
        print(msg)
        return False, msg
        
    identifica_file(input_dir)
    
    if not FILE_FORTECH:
        msg = "ERRORE CRITICO: File Fortech non trovato, impossibile procedere."
        print(msg)
        return False, msg
        
    conn = init_db()
    
    df_fortech_agg, lista_pv = elabora_dati_fortech(conn)
    
    if df_fortech_agg is not None and len(lista_pv) > 0:
        print("\n--- AVVIO MOTORI DI RICONCILIAZIONE (Per singolo PV) ---")
        for pv in lista_pv:
            str_pv_code = str(int(pv))
            riconcilia_contanti(df_fortech_agg, pv, conn)
            riconcilia_carte(df_fortech_agg, pv, conn)
            riconcilia_petrolifere(df_fortech_agg, pv, conn)
            riconcilia_buoni(df_fortech_agg, pv, conn)
            riconcilia_satispay(df_fortech_agg, pv, str_pv_code, conn)
            
    conn.close()
    print("\n--- ELABORAZIONE GLOBALE TERMINATA. DATA IN DATABASE SQLITE. ---")
    print(f"File Database: {DB_PATH}")
    return True, "Elaborazione completata con successo."

if __name__ == '__main__':
    orchestratore_unificato()
