import os
import glob
from .db_manager import get_db_connection, pulisci_report_impianto, get_impianto_id
from .elaboratore_fortech import elabora_dati_fortech
from .motore_contanti import riconcilia_contanti
from .motore_carte import riconcilia_carte
from .motore_petrolifere import riconcilia_petrolifere
from .motore_buoni import riconcilia_buoni
from .motore_satispay import riconcilia_satispay

DB_PATH = "database_riconciliazioni.db"

class Orchestratore:
    def __init__(self):
        self.file_fortech = None
        self.file_contanti = None
        self.file_carte = None
        self.file_petrolifere = None
        self.file_buoni = None
        self.file_satispay = None
        
    def _identifica_file(self, input_dir):
        file_excel = glob.glob(os.path.join(input_dir, "*.xlsx")) + \
                     glob.glob(os.path.join(input_dir, "*.xls")) + \
                     glob.glob(os.path.join(input_dir, "*.csv"))
        for file_path in file_excel:
            nome_file = os.path.basename(file_path).lower()
            nome_norm = nome_file.replace("_", " ")

            if "fortech" in nome_norm or nome_file.startswith("a_"):
                self.file_fortech = file_path
            elif "contanti" in nome_norm or nome_file.startswith("1_"):
                self.file_contanti = file_path
            elif ("carte bancarie" in nome_norm or "numia" in nome_norm or nome_file.startswith("2_")) and "petrolifere" not in nome_norm:
                self.file_carte = file_path
            elif "petrolifere" in nome_norm or "azzurro" in nome_norm or nome_file.startswith("3_"):
                self.file_petrolifere = file_path
            elif "buoni" in nome_norm or "rosso" in nome_norm or nome_file.startswith("4_"):
                self.file_buoni = file_path
            elif "satispay" in nome_norm or "grigio" in nome_norm or nome_file.startswith("5_"):
                self.file_satispay = file_path

    def esegui(self, input_dir):
        """Metodo principale richiamato dal server API upload"""
        self._identifica_file(input_dir)
        
        if not self.file_fortech:
            raise ValueError("File Fortech Assente. Impossibile procedere.")
            
        conn = get_db_connection(DB_PATH)
        try:
            # 1. Fortech master
            df_fortech_agg, lista_pv = elabora_dati_fortech(self.file_fortech, conn)
            
            if df_fortech_agg is not None and len(lista_pv) > 0:
                for pv in lista_pv:
                    impianto_id = get_impianto_id(conn, pv)
                    if not impianto_id:
                        print(f"PV {pv} ignorato xke non in anagrafica")
                        continue
                        
                    pulisci_report_impianto(conn, impianto_id)
                    str_pv_code = str(int(pv))
                    riconcilia_contanti(df_fortech_agg, pv, self.file_contanti, conn)
                    riconcilia_carte(df_fortech_agg, pv, self.file_carte, conn)
                    riconcilia_petrolifere(df_fortech_agg, pv, self.file_petrolifere, conn)
                    riconcilia_buoni(df_fortech_agg, pv, self.file_buoni, conn)
                    riconcilia_satispay(df_fortech_agg, pv, str_pv_code, self.file_satispay, conn)
                    
            return len(lista_pv) # Ritorna Punti vendita analizzati
        finally:
            conn.close()
