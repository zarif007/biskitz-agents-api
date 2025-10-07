from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from constants.system_prompts.dev import DEV_AGENT_PROMPT
from constants.system_prompts.dev_without_tdd import DEV_AGENT_NO_TDD_PROMPT
import time
import json
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CodeGenState(TypedDict):
    messages: List[Dict[str, Any]]
    files: Dict[str, str]
    summary: str | None
    total_tokens: Dict[str, int]

class FileSchema(BaseModel):
    path: str = Field(..., description="The file path relative to project root (e.g., 'src/index.ts', 'package.json')")
    content: str = Field(..., description="The complete content of the file")

class CreateOrUpdateFilesInput(BaseModel):
    files: List[FileSchema] = Field(..., description="List of files to create or update")

class ReadFilesInput(BaseModel):
    files: List[str] = Field(..., description="List of file paths to read")

class Message(BaseModel):
    type: str
    role: str
    content: str

@tool(args_schema=CreateOrUpdateFilesInput)
def create_or_update_files(files: List[FileSchema]) -> str:
    """Create or update multiple files in the project."""
    state_files = {}
    try:
        for file in files:
            if isinstance(file, FileSchema):
                state_files[file.path] = file.content
            elif isinstance(file, dict):
                file_obj = FileSchema.model_validate(file)
                state_files[file_obj.path] = file_obj.content
            else:
                logger.warning(f"Unexpected file type: {type(file)}")
                continue
                
        logger.info(f"Successfully created/updated {len(state_files)} files: {list(state_files.keys())}")
        return json.dumps({
            "success": True,
            "files_created": list(state_files.keys()),
            "count": len(state_files),
            "state_files": state_files
        })
    except Exception as e:
        logger.error(f"Error in create_or_update_files: {str(e)}")
        raise ValueError(f"Invalid file format: {str(e)}")

@tool(args_schema=ReadFilesInput)
def read_files(files: List[str], state_files: Dict[str, str] = None) -> str:
    """Read the content of existing files in the project."""
    if state_files is None:
        state_files = {}
    
    logger.info(f"Reading files: {files}")
    result = []
    for file in files:
        result.append({
            "path": file, 
            "content": state_files.get(file, None),
            "exists": file in state_files
        })
    return json.dumps(result)

async def developer_node(state: CodeGenState) -> CodeGenState:
    """Process developer node for code generation."""
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0.7, max_retries=3)
    
    tdd_enabled = state.get("tdd_enabled", False)
    system_prompt = DEV_AGENT_PROMPT if tdd_enabled else DEV_AGENT_NO_TDD_PROMPT
    
    conversation = state["messages"][-1]["content"]
    
    messages = [SystemMessage(content=system_prompt)]
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
        elif msg.role == "tool":
            messages.append(ToolMessage(content=msg.content, tool_call_id=msg.get("tool_call_id", "unknown")))
        else:
            logger.warning(f"Unknown role {msg.role} in message: {msg}")
    
    if state.get("files"):
        file_list = "\n".join([f"- {path}" for path in state["files"].keys()])
        context = f"\n\nExisting files in project:\n{file_list}"
        if messages[-1].type == "human":
            messages[-1].content += context
    
    llm_with_tools = llm.bind_tools(
        [create_or_update_files, read_files],
        tool_choice="auto"
    )
    
    max_iterations = 3
    iteration = 0
    total_tokens = state.get("total_tokens", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    
    while iteration < max_iterations:
        iteration += 1
        logger.info(f"🔄 Iteration {iteration}/{max_iterations}")
        
        try:
            response = await asyncio.wait_for(
                llm_with_tools.ainvoke(messages), 
                timeout=15000.0
            )
            logger.info(f"✅ LLM invocation successful (iteration {iteration})")
            logger.info(f"Response type: {type(response)}, has tool_calls: {hasattr(response, 'tool_calls')}")
            
            if hasattr(response, 'tool_calls'):
                logger.info(f"Tool calls: {len(response.tool_calls) if response.tool_calls else 0}")
            
            usage_metadata = getattr(response, "usage_metadata", {}) or {}
            total_tokens["prompt_tokens"] += usage_metadata.get("input_tokens", 0)
            total_tokens["completion_tokens"] += usage_metadata.get("output_tokens", 0)
            total_tokens["total_tokens"] += usage_metadata.get("total_tokens", 0)
            
        except asyncio.TimeoutError:
            logger.error("LLM invocation timed out")
            state["messages"].append({
                "role": "assistant",
                "content": "Timed out generating code. Please try again with a more specific conversation.",
                "usage_metadata": {}
            })
            state["total_tokens"] = total_tokens
            return state
        except Exception as e:
            logger.error(f"LLM invocation failed: {str(e)}")
            state["messages"].append({
                "role": "assistant",
                "content": f"Error generating code: {str(e)}. Please try again.",
                "usage_metadata": {}
            })
            state["total_tokens"] = total_tokens
            return state
        
        messages.append(response)
        
        if not hasattr(response, "tool_calls") or not response.tool_calls:
            logger.info("✅ No more tool calls, completing")
            
            response_content = response.content or "Generated code files for the request."
            usage_metadata = getattr(response, "usage_metadata", {}) or {}
            
            state["messages"].append({
                "role": "assistant",
                "content": response_content,
                "usage_metadata": usage_metadata
            })
            
            files_count = len(state.get("files", {}))
            if files_count == 0:
                logger.warning("⚠️ No files were generated!")
                state["messages"][-1]["content"] += "\n\n⚠️ Warning: No files were generated. Please try again with more explicit instructions."
            else:
                logger.info(f"✅ Successfully generated {files_count} files")
            
            state["total_tokens"] = total_tokens
            break
        
        logger.info(f"🔧 Processing {len(response.tool_calls)} tool calls")
        
        for tool_call in response.tool_calls:
            tool_name = tool_call.get("name")
            tool_args = tool_call.get("args", {})
            tool_call_id = tool_call.get("id", f"call_{iteration}")
            
            logger.info(f"Tool: {tool_name}, Args keys: {list(tool_args.keys()) if isinstance(tool_args, dict) else 'not a dict'}")
            
            try:
                if tool_name == "create_or_update_files":
                    result_str = create_or_update_files.invoke(tool_args)
                    result = json.loads(result_str)
                    
                    if result.get("success") and result.get("state_files"):
                        state["files"].update(result["state_files"])
                        logger.info(f"✅ Created/updated {result['count']} files: {result['files_created']}")
                        
                        tool_message = ToolMessage(
                            content=f"Successfully created {result['count']} files: {', '.join(result['files_created'])}",
                            tool_call_id=tool_call_id
                        )
                        messages.append(tool_message)
                    else:
                        logger.error(f"❌ Tool returned unsuccessful result: {result}")
                        tool_message = ToolMessage(
                            content=f"Failed to create files: {result_str}",
                            tool_call_id=tool_call_id
                        )
                        messages.append(tool_message)
                
                elif tool_name == "read_files":
                    result_str = read_files.invoke({
                        "files": tool_args.get("files", []),
                        "state_files": state["files"]
                    })
                    logger.info(f"Read files result: {result_str}")
                    
                    tool_message = ToolMessage(
                        content=result_str,
                        tool_call_id=tool_call_id
                    )
                    messages.append(tool_message)
                
            except Exception as e:
                error_msg = f"Error processing tool {tool_name}: {str(e)}"
                logger.error(error_msg)
                
                tool_message = ToolMessage(
                    content=error_msg,
                    tool_call_id=tool_call_id
                )
                messages.append(tool_message)
            
    if iteration >= max_iterations:
        logger.warning(f"⚠️ Reached maximum iterations ({max_iterations})")
        state["messages"].append({
            "role": "assistant",
            "content": "Reached maximum iterations. Files may be incomplete.",
            "usage_metadata": {}
        })
        state["total_tokens"] = total_tokens
    
    return state

def create_developer_graph():
    """Create and configure the LangGraph workflow for the Developer agent."""
    workflow = StateGraph(CodeGenState)
    
    workflow.add_node("developer_node", developer_node)
    
    workflow.set_entry_point("developer_node")
    workflow.add_edge("developer_node", END)
    
    return workflow.compile()

developer_graph = create_developer_graph()

async def developer(conversation: List[Message], current_folder: Dict[str, str], tdd_enabled: bool) -> Dict[str, Any]:
    """Run the Developer agent with the given conversation, current folder, and TDD setting."""
    start_time = time.time()
    try:
        initial_state = {
            "messages": [{"role": "user", "content": conversation}],
            "files": current_folder.copy() if current_folder else {},
            "summary": None,
            "tdd_enabled": tdd_enabled,
            "total_tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }
        
        result = await developer_graph.ainvoke(initial_state)
        
        time_taken = time.time() - start_time
        
        assistant_messages = [msg for msg in result["messages"] if msg.get("role") == "assistant"]
        
        if assistant_messages:
            assistant_message = assistant_messages[-1]
            response_content = assistant_message["content"]
            usage_metadata = assistant_message.get("usage_metadata", {})
        else:
            response_content = "No response generated."
            usage_metadata = {}
        
        files_count = len(result["files"])
        logger.info(f"📦 Developer agent completed. Generated {files_count} files: {list(result['files'].keys())}")
        
        response = {
            "response": response_content,
            "state": {
                "files": result["files"],
                "summary": result["summary"]
            },
            "time_taken_seconds": round(time_taken, 3),
            "tokens": result.get("total_tokens", {
                "prompt_tokens": usage_metadata.get("input_tokens", 0),
                "completion_tokens": usage_metadata.get("output_tokens", 0),
                "total_tokens": usage_metadata.get("total_tokens", 0)
            }),
            "files_count": files_count
        }
        
        return response
    except Exception as e:
        logger.error(f"Error in developer agent: {str(e)}")
        return {
            "response": f"Error: {str(e)}. No files generated.",
            "state": {"files": {}, "summary": None},
            "time_taken_seconds": round(time.time() - start_time, 3),
            "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "files_count": 0
        }