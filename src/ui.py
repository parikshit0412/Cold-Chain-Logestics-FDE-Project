# Import the os module to read environment variables (like passwords) from your system
import os
# Import sys to modify Python's path so it knows where to find our other files
import sys
# Import uuid to generate unique random IDs for each chat session
import uuid
# Import json to convert Python dictionaries to text so we can save them in the database
import json
# Import urllib to safely format database passwords that might have special characters
import urllib
# Import Path to easily find where our files are located on the computer
from pathlib import Path

# Import dotenv to read our .env file containing the secret API keys
from dotenv import load_dotenv

# Import Streamlit, which is the web framework we use to build the visual dashboard UI
import streamlit as st
# Import Pandas to handle data tables and dataframes easily
import pandas as pd
# Import SQLAlchemy to create connections to our SQL database and execute raw SQL text
from sqlalchemy import create_engine, text

# Import specific message types from LangChain so we can format the chat history
from langchain_core.messages import HumanMessage, ToolMessage

# ==========================================
# 1. IMMEDIATE PATH & ENVIRONMENT RESOLUTION
# ==========================================
# Get the directory where this UI script lives (the 'src' folder)
script_dir = Path(__file__).resolve().parent  
# Get the main project folder (one folder up from 'src')
project_root = script_dir.parent              

# Add the project folder to Python's system path so we can import 'src.orchestrator'
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Load all the secrets from the .env file located in the project root
load_dotenv(project_root / ".env")

# Import our fully built LangGraph agent and database connection from backend
from src.orchestrator import fde_agent
from src.agent_tools import db_engine, IS_SQLITE_MODE

def write_audit_log(session_id, node_name, tool_name, content):
    """
    Silently writes agent execution traces to the SQL audit table.
    Every time the AI thinks or uses a tool, we save it here for security and compliance.
    """
    try:
        table_name = "AgentAuditLog" if IS_SQLITE_MODE else "FDE_VIEWS.AgentAuditLog"
        with db_engine.connect() as conn:
            conn.execute(text(f"""
                INSERT INTO {table_name} (SessionID, NodeExecuted, ToolName, Content)
                VALUES (:session_id, :node_name, :tool_name, :content)
            """), {
                "session_id": str(session_id),
                "node_name": str(node_name),
                "tool_name": str(tool_name),
                "content": str(content)
            })
            conn.commit()
    except Exception as e:
        # If it fails, print the error in the console but don't crash the web app
        print(f"Audit Log Failed (Silent): {e}")

# ==========================================
# 3. PAGE CONFIGURATION & ENTERPRISE THEME
# ==========================================
# Configure the main Streamlit browser tab (Title, Icon, Layout width)
st.set_page_config(
    page_title="FDE Supply Chain Dispatch Console",
    page_icon="🧊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Apply custom CSS styling to make the app look like a sleek, dark-mode enterprise dashboard
st.markdown("""
<style>
    .stApp { background-color: #0B0E14; color: #E2E8F0; }
    div[data-testid="stSidebar"] { background-color: #111622; border-right: 1px solid #1E293B; }
    .stMarkdown code { background-color: #1E293B !important; color: #38BDF8 !important; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 4. MULTI-USER STATE & THREAD MANAGEMENT
# ==========================================
# Streamlit refreshes the script top-to-bottom on every click.
# We use 'st.session_state' to remember variables between refreshes.

# Generate a random unique ID for this specific user's chat session
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

# Create an empty list to store the chat history on the screen
if "ui_messages" not in st.session_state:
    st.session_state.ui_messages = []

# Package the thread ID so LangGraph knows which memory context to use
thread_config = {"configurable": {"thread_id": st.session_state.thread_id}}

# ==========================================
# 5. SIDEBAR NAVIGATION & METADATA
# ==========================================
# Everything inside 'with st.sidebar:' appears in the left-hand menu panel
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2830/2830305.png", width=65)
    st.title("FDE Command Center")
    
    # Radio buttons to switch between the main Chat UI and the Security Logs tab
    app_mode = st.radio("System Mode", ["🧊 Dispatch Console", "🛡️ Security & Audit Logs"])
    
    st.markdown("---")
    # Display the current Session Token ID for the user's reference
    st.caption(f"Session Token: `{st.session_state.thread_id[:8]}...`")
    # Display which AI model is currently powering the system
    st.markdown(f"**Reasoning Architecture:** `{os.getenv('Agent_llm', 'GEMINI')}`")
    
    st.markdown("---")
    # A button to completely clear the chat history and start a fresh session
    if st.button("🗑️ Purge Dispatch Workspace Session", use_container_width=True):
        st.session_state.ui_messages = []
        st.session_state.thread_id = str(uuid.uuid4())
        st.rerun() # Force Streamlit to reload the page instantly

# ==========================================
# 6. VIEW ROUTING (DISPATCH VS AUDIT)
# ==========================================

# ------------------------------------------
# TAB 1: CHAT UI & AGENT EXECUTION
# ------------------------------------------
if app_mode == "🧊 Dispatch Console":
    st.title("Cold-Chain Incident Control Dashboard")
    st.caption("Production Data Engineering Pipeline • Real-Time Decision Optimization Platform")

    # Loop through our saved chat history and draw each message on the screen
    for entry in st.session_state.ui_messages:
        with st.chat_message(entry["role"], avatar="👤" if entry["role"] == "user" else "🤖"):
            # If the AI used tools during this message, display them as expandable blocks
            if "traces" in entry:
                for trace in entry["traces"]:
                    if trace["type"] == "tool_input":
                        st.markdown(f"**⚡ Intent Recognized:** `{trace['name']}`")
                        with st.expander(f"📥 View Generated Input ({trace['name']})", expanded=False):
                            st.json(trace["args"])
                    elif trace["type"] == "tool_output":
                        with st.expander(f"📤 View Raw Output ({trace['name']})", expanded=False):
                            st.code(trace["content"], language="text")
            # Print the actual text message
            st.markdown(entry["content"])

    # This creates the chat input box at the bottom of the screen.
    # If the user types something and hits enter, 'user_input' gets populated.
    if user_input := st.chat_input("Query fleet telemetry, corridor updates, or compliance thresholds..."):
        
        # Save the user's message to memory and draw it on the screen immediately
        st.session_state.ui_messages.append({"role": "user", "content": user_input})
        with st.chat_message("user", avatar="👤"):
            st.markdown(user_input)

        # Now, prepare to draw the AI's response
        with st.chat_message("assistant", avatar="🤖"):
            final_response = ""
            current_traces = [] 
            
            # Show a spinning status box while the AI is thinking
            with st.status("🧠 Initializing Core Reasoner Node...", expanded=True) as status:
                
                # Send the user's message to our LangGraph Agent and get a live stream of events
                events = fde_agent.stream(
                    {"messages": [HumanMessage(content=user_input)]}, 
                    config=thread_config,
                    stream_mode="updates"
                )
                
                # Iterate through every step the LangGraph agent takes
                for event in events:
                    for node_name, node_state in event.items():
                        
                        # If it's the "reasoner" (AI Brain) taking a turn...
                        if node_name == "reasoner":
                            latest_msg = node_state["messages"][-1]
                            
                            # A. Did the AI decide to use a tool?
                            if hasattr(latest_msg, "tool_calls") and latest_msg.tool_calls:
                                status.update(label="🧠 Agent generated tool parameters...")
                                # Loop through every tool the AI decided to call
                                for tool_call in latest_msg.tool_calls:
                                    st.markdown(f"**⚡ Intent Recognized:** `{tool_call['name']}`")
                                    with st.expander(f"📥 View Generated Input ({tool_call['name']})", expanded=False):
                                        st.json(tool_call['args'])
                                    
                                    # Save this action in our UI memory so it stays on screen after a refresh
                                    current_traces.append({
                                        "type": "tool_input",
                                        "name": tool_call['name'],
                                        "args": tool_call['args']
                                    })
                                    
                                    # Silently save this action to the SQL Audit Log
                                    write_audit_log(
                                        session_id=st.session_state.thread_id,
                                        node_name="reasoner",
                                        tool_name=tool_call['name'],
                                        content=json.dumps(tool_call['args'])
                                    )
                            
                            # B. Did the AI generate a final text response for the user?
                            if latest_msg.content:
                                # Gemini sometimes returns a list of dictionaries instead of a plain string
                                if isinstance(latest_msg.content, list):
                                    # Extract all text blocks from the list and join them together
                                    text_parts = [c.get("text", "") for c in latest_msg.content if isinstance(c, dict) and "text" in c]
                                    extracted_text = "".join(text_parts).strip()
                                else:
                                    # Otherwise, just use it as a normal string
                                    extracted_text = str(latest_msg.content).strip()
                                
                                if extracted_text:
                                    final_response = extracted_text
                                    status.update(label="📝 Generating Operational Resolution Report...")
                                    
                                    # Log the final text response to the Audit Log database
                                    write_audit_log(
                                        session_id=st.session_state.thread_id,
                                        node_name="reasoner_final",
                                        tool_name="LLM Text Synthesis",
                                        content=final_response
                                    )
                                
                        # If it's the "tools" node (Database/API) taking a turn...
                        elif node_name == "tools":
                            status.update(label="🔧 Executing Enterprise Subsystem Tools...")
                            for msg in node_state.get("messages", []):
                                if isinstance(msg, ToolMessage):
                                    # Show the raw JSON/Text output from the database or API
                                    with st.expander(f"📤 View Raw Output ({msg.name})", expanded=False):
                                        st.code(msg.content, language="text")
                                        
                                    # Save this output in our UI memory
                                    current_traces.append({
                                        "type": "tool_output",
                                        "name": msg.name,
                                        "content": msg.content
                                    })
                                    
                                    # Log the raw data fetched from the tool into the Audit Log
                                    write_audit_log(
                                        session_id=st.session_state.thread_id,
                                        node_name="tools",
                                        tool_name=msg.name,
                                        content=msg.content
                                    )
                
                # Once the loop finishes, update the spinning status to Complete
                status.update(label="Incident Matrix Evaluation Complete", state="complete", expanded=False)
                
            # Print the final text output outside of the status box
            if final_response:
                st.markdown(final_response)
                # Save the final text to the permanent chat history
                st.session_state.ui_messages.append({
                    "role": "assistant",
                    "content": final_response,
                    "traces": current_traces
                })
            else:
                # If something crashed entirely, show a red error block
                error_fallback = "⚠️ Execution Timeout: System engine encountered an unresolved processing edge case."
                st.error(error_fallback)


# ------------------------------------------
# TAB 2: AUDIT LOG VIEWER (REQUIRES ADMIN CREDENTIALS)
# ------------------------------------------
elif app_mode == "🛡️ Security & Audit Logs":
    st.title("🛡️ Enterprise Agent Audit Trail")
    st.caption("Secure database inspection of FDE_VIEWS.AgentAuditLog")
    
    st.markdown("### Database Authorization Gate")
    st.markdown("Enter high-privilege administrative credentials (defined in `.env` as `SQL_ADMIN_USER`) to query audit logs.")
    
    # Create a form so the user can type in their username and password
    with st.form("admin_auth_form"):
        col1, col2 = st.columns(2) # Split the row into two columns
        with col1:
            input_user = st.text_input("Admin Username", value=os.getenv("SQL_ADMIN_USER", ""))
        with col2:
            # Hide the password text by setting type="password"
            input_pass = st.text_input("Admin Password", type="password", value="")
            
        # The form will only process when this button is clicked
        submit_admin = st.form_submit_button("Authenticate & Load Logs", use_container_width=True)

    if submit_admin:
        # Check if what they typed matches the secret .env variables
        expected_admin_user = os.getenv("SQL_ADMIN_USER")
        expected_admin_pass = os.getenv("SQL_ADMIN_PASSWORD")
        
        if input_user == expected_admin_user and input_pass == expected_admin_pass:
            try:
                # Fetch all logs, ordering by the newest first
                table_target = "AgentAuditLog" if IS_SQLITE_MODE else "FDE_VIEWS.AgentAuditLog"
                with db_engine.connect() as conn:
                    query = f"""
                        SELECT LogID, Timestamp, SessionID, NodeExecuted, ToolName, Content 
                        FROM {table_target} 
                        ORDER BY Timestamp DESC
                    """
                    # Convert the SQL results directly into a Pandas DataFrame
                    df = pd.read_sql(text(query), conn)
                
                st.success("✅ Authenticated successfully as Admin.")
                
                # If we got data back, render a beautiful interactive data table
                if not df.empty:
                    st.dataframe(
                        df,
                        column_config={
                            "LogID": st.column_config.NumberColumn("ID", format="%d"),
                            "Timestamp": st.column_config.DatetimeColumn("Execution Time", format="DD/MM/YYYY-h:mm a"),
                            "SessionID": "Session Token",
                            "NodeExecuted": "Graph Node",
                            "ToolName": "Tool Triggered",
                            "Content": "Raw Payload Data"
                        },
                        hide_index=True,
                        use_container_width=True,
                        height=600
                    )
                else:
                    # If the table is literally empty, tell the user
                    st.info("No audit logs found in the database. Run a query in the Dispatch Console first.")
                    
            except Exception as e:
                # If the SQL connection failed (e.g. wrong password), show an error
                st.error(f"Database Query Failed: {e}")
        else:
            st.error("❌ Invalid Administrator Credentials.")