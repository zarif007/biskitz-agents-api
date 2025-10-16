from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import Dict, List
from agents.ba_agent import business_analyst
from agents.system_architect import system_architect
from agents.developer import developer
from dotenv import load_dotenv
import os

load_dotenv()

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("API_KEY not set in .env file")

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)

async def verify_api_key(authorization: str = Depends(api_key_header)):
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization

app = FastAPI()

class Message(BaseModel):
    type: str
    role: str
    content: str

class PromptRequest(BaseModel):
    prompt: str

class CovRequest(BaseModel):
    conversation: List[Message]
    model: str

class DeveloperRequest(BaseModel):
    conversation: List[Message]
    current_folder: Dict[str, str]
    tdd_enabled: bool
    model: str

@app.get("/", dependencies=[Depends(verify_api_key)])
def read_root():
    return {"Hello": "World Version 1.0.1"}

@app.post("/agents/ba", dependencies=[Depends(verify_api_key)])
async def run_ba_agent(request: CovRequest):
    response = await business_analyst(request.conversation, request.model)
    return response

@app.post("/agents/system-architect", dependencies=[Depends(verify_api_key)])
async def run_system_architect_agent(request: CovRequest):
    response = await system_architect(request.conversation, request.model)
    return response

@app.post("/agents/developer", dependencies=[Depends(verify_api_key)])
async def run_developer_agent(request: DeveloperRequest):
    response = await developer(request.conversation, request.current_folder, request.tdd_enabled, request.model)
    return response