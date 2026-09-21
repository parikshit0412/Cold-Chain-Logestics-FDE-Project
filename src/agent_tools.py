import os
import sys
import re
import urllib.parse
import requests
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from langchain_core.tools import tool
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_pinecone import PineconeVectorStore

# Fix Windows console emoji/encoding printing errors
if sys.stdout and hasattr(sys.stdout, 'reconfigure') and sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# =====================
# 1. Load .env
# =====================
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent  # go up one level

load_dotenv(project_root / ".env")

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
if not PINECONE_API_KEY:
    raise ValueError("CRITICAL: Ensure PINECONE_API_KEY is set in .env to connect to Pinecone!")

EMBEDDINGS_MODEL_SETTING = os.getenv("EMBEDDINGS_MODEL", os.getenv("Embeddings_model", "GEMINI")).strip().upper()

if EMBEDDINGS_MODEL_SETTING in ["GEMINI", "GOOGLE"]:
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        raise ValueError("CRITICAL: GOOGLE_API_KEY not found in .env")
    print("[INFO] Connecting to Gemini Cloud Embeddings (768 Dim)...")
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001",
        google_api_key=google_api_key,
        output_dimensionality=768
    )
    INDEX_NAME = "fde-sop-index-gemini"
else:
    local_model_target = os.getenv("Local_Embedding_Model", "BAAI/bge-m3").strip()
    print(f"[INFO] Connecting to local Fallback Embeddings: {local_model_target}")
    from langchain_huggingface import HuggingFaceEmbeddings
    embeddings = HuggingFaceEmbeddings(
        model_name=local_model_target,
        model_kwargs={'device': 'cpu'}
    )
    INDEX_NAME = "fde-sop-index-local"

vector_store = PineconeVectorStore(index_name=INDEX_NAME, embedding=embeddings)
retriever = vector_store.as_retriever(search_kwargs={"k": 2})

# ==========================================
# Database Connection Setup & Fallback
# ==========================================
db_host = os.getenv("SQL_SERVER_HOST", "localhost")
db_port = os.getenv("SQL_SERVER_PORT", "1433")
db_user = os.getenv("SQL_AGENT_USER", "USR_FDE_RO")
db_password = os.getenv("SQL_AGENT_PASSWORD", "AgentPassword2026!")

IS_SQLITE_MODE = False

def init_embedded_sqlite():
    """
    Initializes a local embedded SQLite database and auto-populates
    VW_ACTIVE_FLEET and AgentAuditLog if an external MSSQL server is unavailable.
    """
    import pandas as pd
    sqlite_db_path = project_root / "data" / "cold_chain_telemetry.db"
    engine = create_engine(f"sqlite:///{sqlite_db_path.as_posix()}")
    
    with engine.connect() as conn:
        res = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='VW_ACTIVE_FLEET'")).fetchall()
        if not res:
            raw_csv = project_root / "data" / "raw" / "dynamic_supply_chain_logistics_dataset.csv"
            if raw_csv.exists():
                print(f"[INFO] Populating Embedded Telemetry DB from {raw_csv.name}...")
                df = pd.read_csv(raw_csv)
                clean_mapping = {
                    'timestamp': 'Timestamp',
                    'vehicle_gps_latitude': 'Latitude',
                    'vehicle_gps_longitude': 'Longitude',
                    'iot_temperature': 'Current_Temperature_C',
                    'cargo_condition_status': 'Cargo_Condition_Code',
                    'risk_classification': 'Risk_Classification',
                    'delay_probability': 'Delay_Probability',
                    'port_congestion_level': 'Port_Congestion_Level',
                    'route_risk_level': 'Route_Risk_Index'
                }
                cols = [c for c in clean_mapping.keys() if c in df.columns]
                df_clean = df[cols].rename(columns=clean_mapping)
                df_clean.to_sql("VW_ACTIVE_FLEET", engine, if_exists="replace", index=False)
                df_clean.to_sql("TBL_SC_FLEET_HIST_RAW", engine, if_exists="replace", index=False)
            
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS AgentAuditLog (
                    LogID INTEGER PRIMARY KEY AUTOINCREMENT,
                    Timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    SessionID TEXT,
                    NodeExecuted TEXT,
                    ToolName TEXT,
                    Content TEXT
                )
            """))
            conn.commit()
            print("[INFO] Embedded Telemetry Database initialized successfully!")
    return engine

def create_db_engine():
    global IS_SQLITE_MODE
    # If explicitly configured or remote host is localhost with no server running, attempt quick test
    try:
        if os.getenv("USE_EMBEDDED_DB", "").strip().lower() in ["true", "1", "yes"]:
            raise RuntimeError("USE_EMBEDDED_DB is enabled.")
            
        import pyodbc
        available_drivers = pyodbc.drivers()
        modern_odbc = [d for d in available_drivers if "ODBC Driver 18" in d or "ODBC Driver 17" in d]
        if modern_odbc:
            driver = modern_odbc[0]
            conn_str = (
                f"DRIVER={{{driver}}};SERVER={db_host},{db_port};"
                f"DATABASE=master;UID={db_user};PWD={db_password};"
                f"Encrypt=no;TrustServerCertificate=yes;Connection Timeout=2;"
            )
            params = urllib.parse.quote_plus(conn_str)
            engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}")
        else:
            engine = create_engine(
                f"mssql+pymssql://{db_user}:{db_password}@{db_host}:{db_port}/master",
                connect_args={"timeout": 2, "login_timeout": 2}
            )
        
        # Test connection with fast timeout
        with engine.connect() as test_conn:
            test_conn.execute(text("SELECT 1"))
        print("[INFO] Connected to Microsoft SQL Server Telemetry DB.")
        IS_SQLITE_MODE = False
        return engine
    except Exception as e:
        print(f"[INFO] Remote MSSQL not available ({e}). Activating Embedded Standalone DB mode...")
        IS_SQLITE_MODE = True
        return init_embedded_sqlite()

db_engine = create_db_engine()

# ==========================================
# 2. CORE FDE AGENT TOOLS
# ==========================================

@tool
def query_telemetry_db(sql_query: str) -> str:
    """
    Executes a SQL SELECT query against the FDE_VIEWS.VW_ACTIVE_FLEET view.
    Columns available:
    Timestamp, Latitude, Longitude, Current_Temperature_C, Cargo_Condition_Code,
    Risk_Classification, Delay_Probability, Port_Congestion_Level, Route_Risk_Index.
    Always write standard T-SQL queries.
    """
    try:
        import re
        if not sql_query.strip().upper().startswith("SELECT"):
            return "SECURITY BLOCK: Only SELECT operations are authorized on this view."
            
        executed_query = sql_query
        if IS_SQLITE_MODE:
            # Strip schema prefixes like FDE_VIEWS. or dbo. for SQLite compatibility
            executed_query = re.sub(r'\b(FDE_VIEWS|dbo)\.', '', executed_query, flags=re.IGNORECASE)
            # Adapt 'SELECT TOP N ...' to 'SELECT ... LIMIT N'
            top_match = re.search(r'SELECT\s+TOP\s+(\d+)\s+(.*)', executed_query, flags=re.IGNORECASE | re.DOTALL)
            if top_match:
                limit_num = top_match.group(1)
                rest_of_query = top_match.group(2)
                executed_query = f"SELECT {rest_of_query} LIMIT {limit_num}"
            
        with db_engine.connect() as conn:
            cursor = conn.execute(text(executed_query))
            columns = list(cursor.keys())
            rows = cursor.fetchmany(10)
            
            if not rows:
                return "No records matched the query criteria."
                
            formatted_output = f"COLUMNS: {', '.join(columns)}\n"
            for row in rows:
                formatted_output += str(tuple(row)) + "\n"
                
            return formatted_output
    except Exception as e:
        return f"Database Error: {str(e)}"

@tool
def fetch_corridor_conditions(latitude: float, longitude: float) -> str:
    """
    Fetches real-time weather and corridor conditions from a live REST API for given GPS coordinates.
    Provides temperature, wind speed, and computed corridor congestion index.
    """
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current_weather=true"
        response = requests.get(url, timeout=6)
        response.raise_for_status()
        
        payload = response.json().get("current_weather", {})
        temp = payload.get("temperature", "N/A")
        wind = payload.get("windspeed", 0.0)
        
        congestion_index = 8.5 if wind > 10.0 else 2.5
        status_note = "High Transit Disruption" if wind > 10.0 else "Corridor Normal"
        
        return (
            f"--- LIVE CORRIDOR TELEMETRY ---\n"
            f"Target GPS: {latitude}, {longitude}\n"
            f"External Temp: {temp}°C | Wind Speed: {wind} km/h\n"
            f"Corridor Risk: {status_note} (Congestion Index: {congestion_index}/10)\n"
            f"-------------------------------"
        )
    except Exception as e:
        return f"Corridor API Communication Failure: {str(e)}"

@tool
def search_compliance_sop(query: str) -> str:
    """
    Searches enterprise Standard Operating Procedures (SOPs) indexed in the Pinecone Vector DB.
    Use this to retrieve regulatory thresholds, cold-chain breach mitigations, and rerouting rules.
    """
    try:
        matched_docs = retriever.invoke(query)
        if not matched_docs:
            return "No matching compliance clauses found."
            
        formatted_context = "\n\n".join(
            [f"[Source: {doc.metadata.get('source_file', 'SOP')} | Format: {doc.metadata.get('file_format', 'RAW')}]\n{doc.page_content}" for doc in matched_docs]
        )
        return f"--- COMPLIANCE SOP CONTEXT ---\n{formatted_context}\n------------------------------"
    except Exception as e:
        return f"Vector Store Retrieval Error: {str(e)}"

# ==========================================
# 3. LOCAL VERIFICATION
# ==========================================
if __name__ == "__main__":
    print("\n--- Testing Tool 1: SQL Telemetry View ---")
    print(query_telemetry_db.invoke("SELECT TOP 2 Latitude, Longitude, Current_Temperature_C FROM FDE_VIEWS.VW_ACTIVE_FLEET"))
    
    print("\n--- Testing Tool 2: Live Corridor API ---")
    print(fetch_corridor_conditions.invoke({"latitude": 33.77, "longitude": -118.19}))
    
    print("\n--- Testing Tool 3: Pinecone Vector Retrieval ---")
    print(search_compliance_sop.invoke("What are the temperature rules for fresh perishables?"))

