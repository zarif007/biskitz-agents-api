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

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define the state for the graph
class CodeGenState(TypedDict):
    messages: List[Dict[str, Any]]
    files: Dict[str, str]
    summary: str | None

# Define Pydantic schemas for tools
class FileSchema(BaseModel):
    path: str = Field(..., description="The file path relative to project root (e.g., 'src/index.ts', 'package.json')")
    content: str = Field(..., description="The complete content of the file")

class CreateOrUpdateFilesInput(BaseModel):
    files: List[FileSchema] = Field(..., description="List of files to create or update")

class ReadFilesInput(BaseModel):
    files: List[str] = Field(..., description="List of file paths to read")

# Define tools with better descriptions
@tool(args_schema=CreateOrUpdateFilesInput)
def create_or_update_files(files: List[FileSchema]) -> str:
    """
    Create or update multiple files in the project. 
    Use this to generate all necessary files for the NPM package including:
    - package.json
    - tsconfig.json
    - README.md
    - All source files in src/ directory
    - Any configuration files
    
    Returns a success message with the list of files created/updated.
    """
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
    """
    Read the content of existing files in the project.
    Returns the content of requested files.
    """
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
    """Node for the Developer agent with multi-turn tool calling support."""
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0.7, max_retries=3)  # Using gpt-4o for better tool use
    
    # Determine system prompt based on tdd_enabled
    tdd_enabled = state.get("tdd_enabled", False)
    system_prompt = DEV_AGENT_PROMPT if tdd_enabled else DEV_AGENT_NO_TDD_PROMPT
    
    # Build message history for multi-turn conversation
    messages = [SystemMessage(content=system_prompt)]
    
    # Convert state messages to proper LangChain message types
    for msg in state["messages"]:
        role = msg.get("role")
        content = msg.get("content", "")
        
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
        elif role == "tool":
            messages.append(ToolMessage(content=content, tool_call_id=msg.get("tool_call_id", "unknown")))
    
    # Add context about existing files if any
    if state.get("files"):
        file_list = "\n".join([f"- {path}" for path in state["files"].keys()])
        context = f"\n\nExisting files in project:\n{file_list}"
        if messages[-1].type == "human":
            messages[-1].content += context
    
    # Bind tools to the LLM
    llm_with_tools = llm.bind_tools(
        [create_or_update_files, read_files],
        tool_choice="auto"
    )
    
    max_iterations = 5  # Prevent infinite loops
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        logger.info(f"🔄 Iteration {iteration}/{max_iterations}")
        
        # Invoke the LLM
        try:
            response = await asyncio.wait_for(
                llm_with_tools.ainvoke(messages), 
                timeout=150.0
            )
            logger.info(f"✅ LLM invocation successful (iteration {iteration})")
            logger.info(f"Response type: {type(response)}, has tool_calls: {hasattr(response, 'tool_calls')}")
            
            if hasattr(response, 'tool_calls'):
                logger.info(f"Tool calls: {len(response.tool_calls) if response.tool_calls else 0}")
            
        except asyncio.TimeoutError:
            logger.error("LLM invocation timed out")
            state["messages"].append({
                "role": "assistant",
                "content": "Timed out generating code. Please try again with a more specific prompt.",
                "usage_metadata": {}
            })
            return state
        except Exception as e:
            logger.error(f"LLM invocation failed: {str(e)}", exc_info=True)
            state["messages"].append({
                "role": "assistant",
                "content": f"Error generating code: {str(e)}. Please try again.",
                "usage_metadata": {}
            })
            return state
        
        # Add the AI response to messages
        messages.append(response)
        
        # Check if there are tool calls
        if not hasattr(response, "tool_calls") or not response.tool_calls:
            # No more tool calls, we're done
            logger.info("✅ No more tool calls, completing")
            
            response_content = response.content or "Generated code files for the request."
            usage_metadata = getattr(response, "usage_metadata", {}) or {}
            
            # Add final message to state
            state["messages"].append({
                "role": "assistant",
                "content": response_content,
                "usage_metadata": usage_metadata
            })
            
            # Check if files were created
            files_count = len(state.get("files", {}))
            if files_count == 0:
                logger.warning("⚠️ No files were generated!")
                state["messages"][-1]["content"] += "\n\n⚠️ Warning: No files were generated. Please try again with more explicit instructions."
            else:
                logger.info(f"✅ Successfully generated {files_count} files")
            
            break
        
        # Process tool calls
        logger.info(f"🔧 Processing {len(response.tool_calls)} tool calls")
        
        for tool_call in response.tool_calls:
            tool_name = tool_call.get("name")
            tool_args = tool_call.get("args", {})
            tool_call_id = tool_call.get("id", f"call_{iteration}")
            
            logger.info(f"Tool: {tool_name}, Args keys: {list(tool_args.keys()) if isinstance(tool_args, dict) else 'not a dict'}")
            
            try:
                if tool_name == "create_or_update_files":
                    # Invoke the tool
                    result_str = create_or_update_files.invoke(tool_args)
                    result = json.loads(result_str)
                    
                    if result.get("success") and result.get("state_files"):
                        # Update state files
                        state["files"].update(result["state_files"])
                        logger.info(f"✅ Created/updated {result['count']} files: {result['files_created']}")
                        
                        # Add tool result message
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
                logger.error(error_msg, exc_info=True)
                
                tool_message = ToolMessage(
                    content=error_msg,
                    tool_call_id=tool_call_id
                )
                messages.append(tool_message)
        
        # Continue to next iteration to let the model respond to tool results
    
    if iteration >= max_iterations:
        logger.warning(f"⚠️ Reached maximum iterations ({max_iterations})")
        state["messages"].append({
            "role": "assistant",
            "content": "Reached maximum iterations. Files may be incomplete.",
            "usage_metadata": {}
        })
    
    return state

# Build the LangGraph workflow
def create_developer_graph():
    workflow = StateGraph(CodeGenState)
    
    # Add the Developer node
    workflow.add_node("developer_node", developer_node)
    
    # Set entry and exit points
    workflow.set_entry_point("developer_node")
    workflow.add_edge("developer_node", END)
    
    return workflow.compile()

# Initialize the graph
developer_graph = create_developer_graph()

async def developer(prompt: str, current_folder: Dict[str, str], tdd_enabled: bool) -> Dict[str, Any]:
    """Run the Developer agent with the given prompt, current folder, and TDD setting."""
    start_time = time.time()
    try:
        # Prepare the user message
        content = f"**System Architect Requirements:**\n{prompt.strip()}"
        
        # Add emphasis on using the tool
        content += "\n\n**IMPORTANT: You MUST use the `create_or_update_files` tool to create all project files. Do not just describe them - actually create them!**"
        
        if tdd_enabled:
            content += f"\n\n**Current Test Files:**\n```json\n{json.dumps(current_folder, indent=2)}\n```"
        
        # Initialize state
        initial_state = {
            "messages": [{"role": "user", "content": content}],
            "files": current_folder.copy() if current_folder else {},
            "summary": None,
            "tdd_enabled": tdd_enabled
        }
        
        # Run the graph
        result = await developer_graph.ainvoke(initial_state)
        
        # Calculate time taken
        time_taken = time.time() - start_time
        
        # Extract the assistant's response and metadata
        assistant_messages = [msg for msg in result["messages"] if msg.get("role") == "assistant"]
        
        if assistant_messages:
            assistant_message = assistant_messages[-1]
            response_content = assistant_message["content"]
            usage_metadata = assistant_message.get("usage_metadata", {})
        else:
            response_content = "No response generated."
            usage_metadata = {}
        
        # Log files generated
        files_count = len(result["files"])
        logger.info(f"📦 Developer agent completed. Generated {files_count} files: {list(result['files'].keys())}")
        
        # Prepare the response dictionary
        response = {
            "response": response_content,
            "state": {
                "files": result["files"],
                "summary": result["summary"]
            },
            "time_taken_seconds": round(time_taken, 3),
            "tokens": {
                "prompt_tokens": usage_metadata.get("input_tokens", 0),
                "completion_tokens": usage_metadata.get("output_tokens", 0),
                "total_tokens": usage_metadata.get("total_tokens", 0)
            },
            "files_count": files_count
        }
        
        return response
    except Exception as e:
        logger.error(f"Error in developer agent: {str(e)}", exc_info=True)
        return {
            "response": f"Error: {str(e)}. No files generated.",
            "state": {"files": {}, "summary": None},
            "time_taken_seconds": round(time.time() - start_time, 3),
            "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "files_count": 0
        }