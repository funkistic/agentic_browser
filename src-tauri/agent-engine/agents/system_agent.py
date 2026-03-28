import logging
from interpreter import interpreter
import os

logging.basicConfig(level=logging.INFO)

async def execute_local_command(prompt: str) -> str:
    """
    Executes a system-level command using Open Interpreter.
    """
    logging.info(f"OpenInterpreter evaluating: {prompt}")
    try:
        # In a real app we fetch keys from .env
        if not os.getenv("GROQ_API_KEY"):
            return "⚠️ Please set GROQ_API_KEY in the `.env` file to use Open Interpreter."
            
        # HITL safe default - Interpreter will not execute dangerous commands without `auto_run=False`
        interpreter.auto_run = False 
        interpreter.llm.model = "groq/llama-3.3-70b-versatile" 
        
        result = interpreter.chat(prompt, display=False)
        return f"System Output:\n{result}"
    except Exception as e:
        logging.error(f"OpenInterpreter failed: {e}")
        return f"Error executing local command: {str(e)}"
