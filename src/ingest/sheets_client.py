import json as _json
import gspread
from google.oauth2.service_account import Credentials

def open_sheet(sheet_id: str, sa_json_or_path: str):
    """
    Abre un Google Spreadsheet por key.
    sa_json_or_path puede ser:
      - Ruta a un archivo JSON (entorno local)
      - Contenido JSON como string (Railway / CI — variable de entorno)
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    # Si la variable empieza con '{' es el contenido JSON, no una ruta
    if sa_json_or_path and sa_json_or_path.strip().startswith("{"):
        info  = _json.loads(sa_json_or_path)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(sa_json_or_path, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id)

def read_worksheet_as_records(sheet, worksheet_name: str):
    ws = sheet.worksheet(worksheet_name)
    return ws.get_all_records()




def write_worksheet_from_df(sheet, worksheet_name: str, df):
    """
    Overwrite completo de una pestaña con el contenido de df (incluye headers).
    Robusto y simple para MVP.
    """
    ws = sheet.worksheet(worksheet_name)
    ws.clear()

    if df is None or df.empty:
        ws.update([["(sin datos)"]])
        return

    values = [list(df.columns)] + df.astype(str).fillna("").values.tolist()
    ws.update(values)