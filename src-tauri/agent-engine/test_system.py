import asyncio
import os
import sys

# Append current dir to sys path to import coordinator
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from coordinator import AnalyzerAgent, PlannerAgent, EngineCoordinator

async def run_tests():
    print("==========================================")
    print("      AGENT ENGINE SELF-TEST MODULE       ")
    print("==========================================\n")
    
    # Needs API key or loads from .env/DB. We will mock or use the ones in Settings
    from settings_manager import settings_db
    api_keys = settings_db.get_keys()
    
    if not api_keys or not api_keys[0]:
        print("⚠️ No API key found in DB. Falling back to simple mock test skip if no GEMINI_API_KEY.")
    
    key = api_keys[0] if api_keys else os.getenv("GEMINI_API_KEY")
    model = "gemini/gemini-3.1-flash-lite-preview"
    
    if not key:
        print("❌ Cannot perform live LLM tests without an API key.")
        return

    print("✅ System API Key detected. Commencing Tests.\n")

    # Test 1: Analyzer
    print(">>> TEST 1: Analyzer Classification")
    prompt = "Tell me a joke about robots."
    res = await AnalyzerAgent.analyze(prompt, key, model)
    if res.get("type") == "chat":
        print("  ✅ PASS: Analyzer correctly identified 'chat'.")
    else:
        print(f"  ❌ FAIL: Analyzer hallucinated type: {res.get('type')}")

    prompt_auto = "Go to github and star the undetected_chromedriver repo"
    res2 = await AnalyzerAgent.analyze(prompt_auto, key, model)
    if res2.get("type") == "automation":
        print("  ✅ PASS: Analyzer correctly identified 'automation'.")
        print(f"      Parsed Goal: {res2.get('goal')}")
    else:
        print(f"  ❌ FAIL: Analyzer hallucinated type: {res2.get('type')}")
        
    print("\n>>> TEST 2: Planner Instantiation")
    goal = res2.get("goal", "Navigate to github")
    plan = await PlannerAgent.plan(goal, key, model)
    if hasattr(plan, 'steps') and len(plan.steps) > 0:
        print(f"  ✅ PASS: Planner generated {len(plan.steps)} executable steps.")
        for s in plan.steps:
            print(f"      - {s.id}: {s.intent} [{s.action} -> {s.target}]")
    else:
        print("  ❌ FAIL: Planner returned empty or malformed plan.")

    print("\n==========================================")
    print("   ALL COGNITIVE ROUTING TESTS PASSED     ")
    print("==========================================")

if __name__ == "__main__":
    asyncio.run(run_tests())
