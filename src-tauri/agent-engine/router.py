import os
import logging

logging.basicConfig(level=logging.INFO)

async def route_task(req) -> dict:
    """
    Cognitive Routing Engine - Returns a dict containing {'response': str, 'logs': list[str]}
    """
    prompt_raw = req.prompt if hasattr(req, 'prompt') else req
    context_str = req.context if hasattr(req, 'context') else "workspace"
    
    logs = [f"Received incoming cognitive request: '{prompt_raw}'"]
    prompt_lower = prompt_raw.lower()
    
    # 1. Retrieve Lean Memory Fact-Base
    try:
        from memory import memory_db
        context = memory_db.retrieve_context("user_1", prompt_raw)
        if context:
            logs.append(f"Memory Check: Injecting past session facts.")
            prompt_injected = (
                f"HISTORY: {context[:400]}\n"
                f"CURRENT GOAL: {prompt_raw}"
            )
        else:
            logs.append(f"Memory Check: Clean session.")
            prompt_injected = prompt_raw
    except Exception as e:
        logs.append(f"Memory fail: {str(e)}")
        prompt_injected = prompt_raw

    # 2. Human-In-The-Loop (HITL) Intercept
    risk_keywords = ["sudo", "rm ", "pay", "buy", "delete", "execute shell"]
    if any(k in prompt_lower for k in risk_keywords):
        warn_msg = "High-risk action detected. Halting for approval."
        logs.append(f"⚠️ {warn_msg}")
        return {
            "logs": logs,
            "response": f"⚠️ **Security Boundary**: Approval required for: `{prompt_raw}`"
        }
    
    # 3. Intention Parsing
    try:
        if context_str == "web_automation" or any(k in prompt_lower for k in ["browser", "web", "site"]):
            from agents.browser_agent import execute_web_automation
            logs.append("Intent: BROWSER_AUTOMATION.")
            res = await execute_web_automation(prompt_injected)
            logs.append("Browser task node execution closed.")
            return {"logs": logs, "response": res}
            
        elif context_str == "local_shell" or any(k in prompt_lower for k in ["file", "script", "local"]):
            from agents.system_agent import execute_local_command
            logs.append("Intent: SYSTEM_CONTROL.")
            res = await execute_local_command(prompt_injected)
            
            # Persist local task outcome
            try:
                from memory import memory_db
                status = "success" if "✅" in res else "failed"
                memory_db.add_memory("user_1", f"Local: {prompt_raw[:60]} | Result: {res[:80]}", status=status)
            except: pass
            
            logs.append("Local execution loop terminated.")
            return {"logs": logs, "response": res}
            
        elif "research" in prompt_lower or "search" in prompt_lower:
            from search import perform_deep_research
            logs.append("Intent: DEEP_RESEARCH.")
            res = await perform_deep_research(prompt_injected)
            logs.append("Search synthesis completed.")
            return {"logs": logs, "response": res}
            
        else:
            logs.append("Intent: GENERAL_PLANNING.")
            return {"logs": logs, "response": f"Task evaluated: '{prompt_raw}'"}
            
    except Exception as e:
        logs.append(f"CRITICAL Router ERROR: {str(e)}")
        return {"logs": logs, "response": f"Task execution failed. See logs."}
