# Import built-in modules for operating system and file paths
import os
import sys
from pathlib import Path

# Fix Windows console emoji printing error (cp1252)
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Import tool for reading environment variables from a .env file
from dotenv import load_dotenv

# Import type hinting tools to make our code more predictable and readable
from typing import Annotated, TypedDict

# Import LangChain core components for handling chat messages
from langchain_core.messages import BaseMessage, SystemMessage

# Import LangGraph components to build our state machine (flowchart)
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

# ==========================================
# 1. SETUP & PATH RESOLUTION
# ==========================================
# Get the directory where this script is located
script_dir = Path(__file__).resolve().parent
# Get the main project folder (one level up from this script)
project_root = script_dir.parent

# Add the project folder to Python's system path so we can import our other files
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import our custom agent tools from src/agent_tools.py
from src.agent_tools import query_telemetry_db, fetch_corridor_conditions, search_compliance_sop

# Load the .env file so we can access our API keys securely
load_dotenv(project_root / ".env")

# Define the "State" (memory/context) that our agent will carry between steps.
# It simply holds a list of messages that gets appended to as the chat progresses.
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

# ==========================================
# 2. FACTORY INITIALIZATION: AGENT REASONER LLM
# ==========================================
# Check our .env file to see which AI brain we want to use (default to GEMINI if missing)
AGENT_LLM_SETTING = os.getenv("Agent_llm", os.getenv("AGENT_LLM", "GEMINI")).strip().upper()

# If the setting says GEMINI, use Google's Gemini model
if AGENT_LLM_SETTING in ["GEMINI", "GOOGLE"]:
    print("✨ Brain Mode: Utilizing Google Gemini Cloud Reasoner (gemini-2.5-flash)...")
    from langchain_google_genai import ChatGoogleGenerativeAI
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0 # Keep it highly logical and deterministic, not creative
    )
# If it says OPENAI, use OpenAI (like your teacher does)
elif AGENT_LLM_SETTING == "OPENAI":
    print("🤖 Brain Mode: Utilizing Cloud OpenAI Reasoner (gpt-4o)...")
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
# Otherwise, fall back to a local model (Ollama)
else:
    print("🤗 Brain Mode: Local Fallback Activated. Binding Local Ollama (qwen2.5:7b)...")
    from langchain_community.chat_models import ChatOllama
    llm = ChatOllama(model="qwen2.5:7b", temperature=0, num_predict=1024)

# Create a list of the tools our AI is allowed to use
fde_tools = [query_telemetry_db, fetch_corridor_conditions, search_compliance_sop]
# Connect (bind) these tools to our AI brain so it knows how to use them
llm_with_tools = llm.bind_tools(fde_tools)

# ==========================================
# 3. GRAPH ARCHITECTURE ASSEMBLY
# ==========================================

# Figure out exactly where our system prompt file is located
prompt_path = project_root / "src" / "prompts" / "system_prompt.txt"
try:
    with open(prompt_path, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT_TEXT = f.read()
except FileNotFoundError:
    SYSTEM_PROMPT_TEXT = "You are a helpful AI assistant." 

# This function is what runs when the graph reaches the "reasoner" node
def reasoning_node(state: AgentState):
    messages = state["messages"]
    
    # Ensure the SystemMessage is always prepended so Gemini has its instructions
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=SYSTEM_PROMPT_TEXT)] + list(messages)
        
    # Ask the AI brain to think about the current messages and give a response
    response = llm_with_tools.invoke(messages)
    # Return the AI's response to be added to the state
    return {"messages": [response]}

print("⚙️ Compiling LangGraph FDE Orchestrator...")
# Create a new graph (flowchart) using our AgentState format
graph_builder = StateGraph(AgentState)

# Add our two main workers to the graph: the AI brain ("reasoner") and the tool executor ("tools")
graph_builder.add_node("reasoner", reasoning_node)
graph_builder.add_node("tools", ToolNode(fde_tools))

# Draw an arrow from the very START of the graph to the "reasoner" node
graph_builder.add_edge(START, "reasoner")
# Draw a conditional arrow from "reasoner": if it wants a tool, go to "tools"; otherwise, it's finished.
graph_builder.add_conditional_edges("reasoner", tools_condition)
# Draw an arrow from "tools" back to "reasoner" so it can see the tool's results
graph_builder.add_edge("tools", "reasoner")

# Compile the whole graph into a runnable application with a memory saver (to remember past chats)
fde_agent = graph_builder.compile(checkpointer=MemorySaver())

# ==========================================
# 4. CHAT LOOP TESTING PANEL
# ==========================================
# This part only runs if you execute this script directly (like 'python orchestrator.py')
if __name__ == "__main__":
    # Print a nice banner to show the system is starting up
    print("\n" + "="*55)
    print("🚀 FDE Supply Chain Orchestrator State Machine Online")
    print(f"   Configured Execution: [LLM: {AGENT_LLM_SETTING}]")
    print("="*55 + "\n")
    
    # Set up a unique "thread" so the AI remembers this specific conversation
    thread_config = {"configurable": {"thread_id": "production_test_1"}}
    
    # Start an endless loop to let the user keep typing questions
    while True:
        # Ask the user for input
        user_input = input("\nDispatcher > ")
        # If the user types exit or quit, break out of the loop to stop the program
        if user_input.lower() in ['exit', 'quit']:
            break
            
        # Send the user's question to the agent and get back a live stream of what it's doing
        events = fde_agent.stream({"messages": [("user", user_input)]}, config=thread_config, stream_mode="updates")
        
        # Loop through each step the agent takes in the background
        for event in events:
            # Check which node (reasoner or tools) just finished working
            for node_name, node_state in event.items():
                if node_name == "tools":
                    # If the tools node just ran, tell the user we got data
                    print("   [System] 🔍 Retrieving external data elements via ToolNode...")
                elif node_name == "reasoner":
                    # If the reasoner node just ran, grab its final text response
                    latest_msg = node_state["messages"][-1]
                    # Print the AI's response so the dispatcher can read it
                    if latest_msg.content:
                        print(f"\n🤖 FDE Agent:\n{latest_msg.content}")
