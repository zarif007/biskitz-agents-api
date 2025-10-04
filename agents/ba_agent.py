from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from constants.system_prompts.business_analyst import BA_SYSTEM_PROMPT
import time

class AgentState(TypedDict):
    messages: List[Dict[str, Any]]

def ba_node(state: AgentState) -> AgentState:
    """Node for the Business Analyst agent."""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
    
    user_message = state["messages"][-1]["content"]
    
    messages = [
        SystemMessage(content=BA_SYSTEM_PROMPT),
        HumanMessage(content=user_message)
    ]
    
    response = llm.invoke(messages)
    
    state["messages"].append({"role": "assistant", "content": response.content, "usage_metadata": response.usage_metadata})
    return state

def create_ba_graph():
    workflow = StateGraph(AgentState)
    
    workflow.add_node("ba_node", ba_node)
    
    workflow.set_entry_point("ba_node")
    workflow.add_edge("ba_node", END)
    
    return workflow.compile()

ba_graph = create_ba_graph()

async def business_analyst(prompt: str) -> Dict[str, Any]:
    """Run the Business Analyst agent with the given prompt."""
    try:
        initial_state = {
            "messages": [{"role": "user", "content": prompt.strip()}]
        }
        
        start_time = time.time()
        
        result = await ba_graph.ainvoke(initial_state)
        
        time_taken = time.time() - start_time
        
        assistant_message = result["messages"][-1]
        response_content = assistant_message["content"]
        usage_metadata = assistant_message.get("usage_metadata", {})
        
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