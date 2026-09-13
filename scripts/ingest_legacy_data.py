from pathlib import Path
import pandas as pd
import urllib
import os
import pyodbc
from sqlalchemy import create_engine
from dotenv import load_dotenv

# __file__ is 'experiment/phase-0/ingest_legacy_data.py'
script_dir = Path(__file__).resolve().parent # points to experiment/phase-0
project_root = script_dir.parents[0] # climbs up 1 levels to project root

load_dotenv(project_root / ".env")

data_path = project_root / "data" / "raw" / "dynamic_supply_chain_logistics_dataset.csv"

db_host = os.getenv("SQL_SERVER_HOST", "localhost")
db_port = os.getenv("SQL_SERVER_PORT", "1433")
db_user = os.getenv("SQL_ADMIN_USER")
db_password = os.getenv("SQL_ADMIN_PASSWORD")

# 1. Load the raw dataset
print(f"Loading CSV from {data_path}...")
df = pd.read_csv(data_path)

# 2. Map clean columns to a messy 2000s legacy enterprise schema
legacy_mapping = {
    'timestamp': 'TS_UTC',
    'vehicle_gps_latitude': 'V_LAT',
    'vehicle_gps_longitude': 'V_LON',
    'iot_temperature': 'IOT_TEMP_VAL_C',
    'cargo_condition_status': 'CGO_COND_CD',
    'risk_classification': 'RISK_CLS_TXT',
    'delay_probability': 'DELAY_PROB_DEC',
    'port_congestion_level': 'PRT_CNG_LVL',
    'route_risk_level': 'RT_RSK_IDX'
}

# Keep only the columns we mapped for this demo and rename them
df_legacy = df[list(legacy_mapping.keys())].rename(columns=legacy_mapping)

# Add a fake ingestion flag to make it look like an automated legacy system
df_legacy['SYS_INGEST_FLAG'] = 'Y'

# 3. Connect to Docker MSSQL Server
print("Connecting to legacy MSSQL Database...")

# Check for modern ODBC Drivers first, fallback to pymssql if ODBC Driver 17/18 is not installed
odbc_drivers = pyodbc.drivers() if 'pyodbc' in globals() else []
modern_odbc = [d for d in odbc_drivers if "ODBC Driver 18" in d or "ODBC Driver 17" in d]

if modern_odbc:
    selected_driver = modern_odbc[0]
    print(f"Using ODBC Driver: {selected_driver}")
    connection_string = (
        f"DRIVER={{{selected_driver}}};"
        f"SERVER={db_host},{db_port};"
        f"DATABASE=master;"
        f"UID={db_user};"
        f"PWD={db_password};"
        f"Encrypt=no;"
        f"TrustServerCertificate=yes;"
    )
    params = urllib.parse.quote_plus(connection_string)
    engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}")
else:
    print("ODBC Driver 17/18 not detected. Falling back to pymssql connector...")
    try:
        import pymssql
        engine = create_engine(f"mssql+pymssql://{db_user}:{db_password}@{db_host}:{db_port}/master")
    except ImportError:
        raise RuntimeError(
            "Neither 'ODBC Driver 17/18 for SQL Server' nor 'pymssql' was found.\n"
            "Please run: pip install pymssql OR install Microsoft ODBC Driver 18 for SQL Server."
        )

# 4. Ingest data into the messy table name
table_name = 'TBL_SC_FLEET_HIST_RAW'
print(f"Ingesting into {table_name}. This may take a minute...")
df_legacy.to_sql(table_name, engine, if_exists='replace', index=False, schema='dbo')

print("[SUCCESS] Legacy data ingestion complete!")