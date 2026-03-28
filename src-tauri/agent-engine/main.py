import sys
import json
import logging

logging.basicConfig(level=logging.INFO, filename="agent.log")

def handle_request(command, payload):
    """
    Cognitive Routing Engine - Route requests to Open Interpreter or Browser-Use based on intent.
    For Phase 4, we establish the sidecar IPC bridge.
    """
    logging.info(f"Received command: {command} with payload: {payload}")
    
    if command == "ping":
        return {"status": "success", "message": "Agent Engine Online"}
    
    if command == "execute_agent":
        prompt = payload.get("prompt", "")
        # TODO: Integrate browser-use / open-interpreter here
        return {
            "status": "success", 
            "result": f"Simulated backend analysis for: {prompt}. (Connect to Groq/Gemini APIs in Phase 5)"
        }
        
    return {"status": "error", "message": "Unknown command"}

def main():
    """Main loop reading from Tauri via stdin/stdout"""
    logging.info("Agent engine started.")
    
    # Optional startup signal
    print(json.dumps({"status": "ready"}))
    sys.stdout.flush()
    
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
                
            data = json.loads(line)
            command = data.get("command")
            payload = data.get("payload", {})
            
            response = handle_request(command, payload)
            
            # Respond to Tauri shell plugin
            print(json.dumps(response))
            sys.stdout.flush()
            
        except json.JSONDecodeError:
            print(json.dumps({"status": "error", "message": "Invalid JSON format received"}))
            sys.stdout.flush()
        except Exception as e:
            logging.error(f"Error handling request: {e}", exc_info=True)
            print(json.dumps({"status": "error", "message": str(e)}))
            sys.stdout.flush()

if __name__ == "__main__":
    main()
