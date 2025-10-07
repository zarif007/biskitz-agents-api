from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from constants.system_prompts.system_architect import SYS_ARCH_SYSTEM_PROMPT
import time
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AgentState(TypedDict):
    messages: List[Dict[str, Any]]

async def system_architect_node(state: AgentState) -> AgentState:
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, max_retries=3)
    
    conversation = state["messages"][-1]["content"]
    
    messages = [SystemMessage(content=SYS_ARCH_SYSTEM_PROMPT)]
    for msg in conversation:
        if not hasattr(msg, 'type') or not hasattr(msg, 'role') or not hasattr(msg, 'content'):
            logger.error(f"Invalid message format: {msg}")
            continue
        if msg.type != "text":
            logger.warning(f"Skipping non-text message: {msg}")
            continue
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
        elif msg.role == "system":
            messages.append(SystemMessage(content=msg.content))
        else:
            logger.warning(f"Unknown role {msg.role} in message: {msg}")
    
    try:
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=15000.0)
        logger.info("LLM invocation successful")
    except asyncio.TimeoutError:
        logger.error("LLM invocation timed out after 15 seconds")
        state["messages"].append({
            "role": "assistant",
            "content": "Timed out generating response. Please try again with a more specific conversation.",
            "usage_metadata": {}
        })
        return state
    except Exception as e:
        logger.error(f"LLM invocation failed: {str(e)}")
        state["messages"].append({
            "role": "assistant",
            "content": f"Error generating response: {str(e)}. Please try again.",
            "usage_metadata": {}
        })
        return state
    
    response_content = response.content or "Generated response for the request."
    usage_metadata = getattr(response, "usage_metadata", {}) or {}
    
    state["messages"].append({
        "role": "assistant",
        "content": response_content,
        "usage_metadata": usage_metadata
    })
    
    return state

def create_system_architect_graph():
    workflow = StateGraph(AgentState)
    
    workflow.add_node("system_architect_node", system_architect_node)
    
    workflow.set_entry_point("system_architect_node")
    workflow.add_edge("system_architect_node", END)
    
    return workflow.compile()

system_architect_graph = create_system_architect_graph()

async def system_architect(conversation: List[Any]) -> Dict[str, Any]:
    start_time = time.time()
    try:
        initial_state = {
            "messages": [{"role": "user", "content": conversation}]
        }
        
        result = await system_architect_graph.ainvoke(initial_state)
        
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
        
        logger.info(f"System Architect agent response: {response}")
        return response
    except Exception as e:
        logger.error(f"Error in System Architect agent: {str(e)}")
        return {
            "response": f"Error: {str(e)}. No response generated.",
            "time_taken_seconds": round(time.time() - start_time, 3),
            "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }