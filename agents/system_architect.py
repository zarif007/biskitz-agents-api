from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from constants.system_prompts.system_architect import SYS_ARCH_SYSTEM_PROMPT
import time

# Define the state for the graph
class AgentState(TypedDict):
    messages: List[Dict[str, Any]]

def system_architect_node(state: AgentState) -> AgentState:
    """Node for the System Architect agent."""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
    
    # Extract the latest user message
    user_message = state["messages"][-1]["content"]
    
    # Create messages for the LLM
    messages = [
        SystemMessage(content=SYS_ARCH_SYSTEM_PROMPT),
        HumanMessage(content=user_message)
    ]
    
    # Invoke the LLM
    response = llm.invoke(messages)
    
    # Append the assistant's response to the state
    state["messages"].append({"role": "assistant", "content": response.content, "usage_metadata": response.usage_metadata})
    return state

# Build the LangGraph workflow
def create_system_architect_graph():
    workflow = StateGraph(AgentState)
    
    # Add the System Architect node
    workflow.add_node("system_architect_node", system_architect_node)
    
    # Set entry and exit points
    workflow.set_entry_point("system_architect_node")
    workflow.add_edge("system_architect_node", END)
    
    return workflow.compile()

# Initialize the graph
system_architect_graph = create_system_architect_graph()

async def system_architect(prompt: str) -> Dict[str, Any]:
    """Run the System Architect agent with the given prompt."""
    try:
        # Initialize state with user prompt
        initial_state = {
            "messages": [{"role": "user", "content": prompt.strip()}]
        }
        
        # Measure start time
        start_time = time.time()
        
        # Run the graph
        result = await system_architect_graph.ainvoke(initial_state)
        
        # Calculate time taken
        time_taken = time.time() - start_time
        
        # Extract the assistant's response and metadata
        assistant_message = result["messages"][-1]
        response_content = assistant_message["content"]
        usage_metadata = assistant_message.get("usage_metadata", {})
        
        # Prepare the response dictionary
        response = {
            "response": response_content,
            "time_taken_seconds": round(time_taken, 3),
            "tokens": {
                "prompt_tokens": usage_metadata.get("input_tokens", 0),
                "completion_tokens": usage_metadata.get("output_tokens", 0),
                "total_tokens": usage_metadata.get("total_tokens", 0)
            }
        }
        
        return response
    except Exception as e:
        return {
            "response": f"Error: {str(e)}",
            "time_taken_seconds": 0.0,
            "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }