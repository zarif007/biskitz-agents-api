from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import Dict
from agents.ba_agent import business_analyst
from agents.system_architect import system_architect
from agents.developer import developer
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Get the API key from environment variables
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("API_KEY not set in .env file")

# Define API key header
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)

# Dependency to validate API key
async def verify_api_key(authorization: str = Depends(api_key_header)):
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization

app = FastAPI()

# Define a Pydantic model for the request body
class PromptRequest(BaseModel):
    prompt: str

class DeveloperRequest(BaseModel):
    prompt: str
    current_folder: Dict[str, str]
    tdd_enabled: bool

@app.get("/", dependencies=[Depends(verify_api_key)])
def read_root():
    return {"Hello": "World"}

@app.post("/agents/ba", dependencies=[Depends(verify_api_key)])
async def run_ba_agent(request: PromptRequest):
    """Run the Business Analyst agent."""
    response = await business_analyst(request.prompt)
    return response

@app.post("/agents/system-architect", dependencies=[Depends(verify_api_key)])
async def run_system_architect_agent(request: PromptRequest):
    """Run the System Architect agent."""
    response = await system_architect(request.prompt)
    return response

@app.post("/agents/developer", dependencies=[Depends(verify_api_key)])
async def run_developer_agent(request: DeveloperRequest):
    """Run the Developer agent."""
    response = await developer(request.prompt, request.current_folder, request.tdd_enabled)
    return response