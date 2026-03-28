import { useState, useRef, useEffect, useCallback } from "react";
import { Command, Terminal, Globe, BrainCircuit, Activity, Settings, User, Pause, Play, Square, Key, Save, Plus, Trash2 } from "lucide-react";
import "./App.css";

const WS_URL = "ws://127.0.0.1:14143/ws/browser-stream";
const API    = "http://127.0.0.1:14143";

// ── Full Interactive Live Browser Viewer ─────────────────────────────────────
function BrowserViewer() {
  const imgRef = useRef<HTMLImageElement>(null);
  const divRef = useRef<HTMLDivElement>(null);
  const wsRef  = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [hasFrame, setHasFrame] = useState(false);

  // WebSocket connection for screenshot stream
  useEffect(() => {
    let ws: WebSocket;
    let reconnectTimer: number;

    const connect = () => {
      ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen  = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        reconnectTimer = window.setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (evt) => {
        if (imgRef.current && typeof evt.data === "string" && evt.data.length > 50) {
          // Playwright streams JPEGs (/9j/), while UC fallback streams PNGs (iVBORw0KGgo)
          const isJpeg = evt.data.startsWith("/9j/");
          imgRef.current.src = `data:image/${isJpeg ? "jpeg" : "png"};base64,` + evt.data;
          setHasFrame(prev => {
            if (!prev) return true;
            return prev;
          });
        }
      };
    };

    connect();
    return () => { clearTimeout(reconnectTimer); ws?.close(); };
  }, []);

  // Focus div on mount so keyboard events fire immediately
  useEffect(() => { divRef.current?.focus(); }, [connected]);

  // ── Ratio helpers ──────────────────────────────────────────────────────────
  const getRatios = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    return {
      x_ratio: (e.clientX - rect.left) / rect.width,
      y_ratio: (e.clientY - rect.top)  / rect.height,
    };
  };

  // ── Event forwarders ──────────────────────────────────────────────────────
  const post = useCallback((path: string, body: object) => {
    fetch(`${API}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => {});
  }, []);

  const handleClick      = (e: React.MouseEvent<HTMLDivElement>) => post("/api/browser-click",  getRatios(e));
  const handleMouseMove  = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    // Throttle to ~30 fps
    post("/api/browser-move", getRatios(e));
  }, [post]);
  const handleScroll     = (e: React.WheelEvent<HTMLDivElement>) => {
    e.preventDefault();
    post("/api/browser-scroll", { delta_x: e.deltaX, delta_y: e.deltaY });
  };
  const handleKeyDown    = (e: React.KeyboardEvent<HTMLDivElement>) => {
    e.preventDefault();
    post("/api/browser-key", { key: e.key, modifiers: {
      shift: e.shiftKey, ctrl: e.ctrlKey, alt: e.altKey, meta: e.metaKey
    }});
  };

  return (
    <div
      ref={divRef}
      id="browser-mount"
      tabIndex={0}
      style={{
        flex: "0 0 55%",
        position: "relative",
        overflow: "hidden",
        background: "#0a0a0f",
        borderBottom: "1px solid var(--border)",
        cursor: connected ? "default" : "wait",
        outline: "none",
        userSelect: "none",
      }}
      onClick={handleClick}
      onMouseMove={handleMouseMove}
      onWheel={handleScroll}
      onKeyDown={handleKeyDown}
    >
      {/* Live screenshot — fills the area */}
      <img
        ref={imgRef}
        alt="Live browser"
        draggable={false}
        style={{
          width: "100%", height: "100%",
          objectFit: "fill",   // fill = exact 1:1 pixel mapping
          display: connected && hasFrame ? "block" : "none",
          pointerEvents: "none",
        }}
      />

      {/* Connected indicator badge */}
      {connected && hasFrame && (
        <div style={{
          position: "absolute", top: 8, right: 8,
          background: "rgba(16,185,129,0.2)",
          border: "1px solid rgba(16,185,129,0.4)",
          color: "#10b981", fontSize: "0.7rem", fontWeight: 700,
          padding: "2px 10px", borderRadius: 20,
          pointerEvents: "none",
        }}>● LIVE</div>
      )}

      {/* Loading state when connected but waiting for first frame */}
      {connected && !hasFrame && (
        <div style={{
          position: "absolute", inset: 0,
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          color: "#38bdf8", gap: "12px",
        }}>
          <Activity size={40} />
          <span style={{ fontSize: "0.85rem", fontWeight: 500 }}>Connecting to video stream…</span>
        </div>
      )}

      {/* Placeholder when stream is not ready */}
      {!connected && (
        <div style={{
          position: "absolute", inset: 0,
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          color: "#334155", gap: "12px",
        }}>
          <Globe size={40} strokeWidth={1} />
          <span style={{ fontSize: "0.85rem" }}>Browser will appear here when a task starts</span>
          <span style={{ fontSize: "0.72rem", color: "#1e293b" }}>Waiting for stream…</span>
        </div>
      )}
    </div>
  );
}



interface Message {
  role: "user" | "agent";
  text: string;
  logs?: string[];
}

// Mirror of the backend AgentState enum
type AgentStateType = "idle" | "running" | "paused" | "stopped" | "waiting_2fa";

interface AgentStatus {
  state: AgentStateType;
  paused: boolean;
  running: boolean;
  stopped: boolean;
  waiting_2fa: boolean;
  browser_context: { url?: string; title?: string };
  live_plan?: { goal: string, steps: { id: number, intent: string, action: string, target: string, status: string }[] };
  current_step_idx?: number;
  current_model?: string;
  current_key_name?: string;
}

interface ApiKey {
  name: string;
  key: string;
  exhausted_until: string | null;
}

interface StoredCredential {
  id: number;
  domain: string;
  username: string;
}

const DEFAULT_STATUS: AgentStatus = {
  state: "idle", paused: false, running: false, stopped: false, waiting_2fa: false, browser_context: {}
};




function App() {
  const [prompt, setPrompt] = useState("");
  const [activeTab, setActiveTab] = useState("workspace");
  const [liveLogs, setLiveLogs] = useState<string[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatus>(DEFAULT_STATUS);
  const [messages, setMessages] = useState<Message[]>([
    { role: "agent", text: "Agentic Workspace initialized. How can I assist you today?" },
  ]);
  const [sysSettings, setSysSettings] = useState<{api_keys: ApiKey[], primary_model: string}>({
    api_keys: [{name: "API Key #1", key: "", exhausted_until: null}], primary_model: "gemini-3.1-flash-lite-preview"
  });

  const chatHistoryRef = useRef<HTMLDivElement>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const [vault, setVault] = useState<StoredCredential[]>([]);
  const [newCred, setNewCred] = useState({ domain: "", username: "", password: "" });
  const [settingsTab, setSettingsTab] = useState<"engine" | "vault">("engine");
  const [twoFACode, setTwoFACode] = useState("");

  // Auto-scroll chat
  useEffect(() => {
    chatHistoryRef.current?.scrollTo({ top: chatHistoryRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // Auto-scroll telemetry logs
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [liveLogs]);

  // Poll logs + status when on web_automation tab
  useEffect(() => {
    if (activeTab !== "web_automation") return;
    const interval = setInterval(async () => {
      try {
        const [logsRes, statusRes] = await Promise.all([
          fetch(`${API}/api/logs`),
          fetch(`${API}/api/status`),
        ]);
        const logsData   = await logsRes.json();
        const statusData = await statusRes.json() as AgentStatus;
        if (logsData.logs) setLiveLogs(logsData.logs);
        setAgentStatus(statusData);
      } catch (_) {}
    }, 600);
    return () => clearInterval(interval);
  }, [activeTab]);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/settings`);
      const data = await res.json();
      if (data.status === "success") {
        setSysSettings({ 
          api_keys: data.api_keys.length ? data.api_keys : [{name: "API Key #1", key: "", exhausted_until: null}], 
          primary_model: data.primary_model 
        });
      }
    } catch (_) {}
  }, []);

  const fetchVault = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/credentials`);
      const data = await res.json();
      if (data.status === "success") setVault(data.credentials);
    } catch (_) {}
  }, []);

  useEffect(() => {
    if (activeTab === "settings") { fetchSettings(); fetchVault(); }
  }, [activeTab, fetchSettings, fetchVault]);

  const handleAddCred = async () => {
    if (!newCred.domain || !newCred.username) return;
    try {
      await fetch(`${API}/api/credentials`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newCred),
      });
      setNewCred({ domain: "", username: "", password: "" });
      fetchVault();
    } catch (_) {}
  };

  const handleDeleteCred = async (id: number) => {
    try {
      await fetch(`${API}/api/credentials/${id}`, { method: "DELETE" });
      fetchVault();
    } catch (_) {}
  };

  const handleDeleteAllCreds = async () => {
    if (!window.confirm("Are you sure you want to clear the entire credential vault?")) return;
    try {
      for (const c of vault) {
        await fetch(`${API}/api/credentials/${c.id}`, { method: "DELETE" });
      }
      fetchVault();
    } catch (_) {}
  };

  const handleSaveSettings = async () => {
    try {
      await fetch(`${API}/api/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sysSettings),
      });
      alert("✅ Configuration saved! Engine will utilize these settings immediately.");
    } catch (err: any) {
      alert("❌ Failed to save: " + err.message);
    }
  };

  const handleSend = useCallback(async () => {
    if (!prompt.trim()) return;
    // If paused, sending a new command auto-resumes (handled by backend)
    // If running, warn user
    if (agentStatus.running) {
      setMessages(prev => [...prev, {
        role: "agent",
        text: "⚠️ An agent task is already running. Press STOP first or wait for it to finish."
      }]);
      return;
    }

    const currentPrompt = prompt;
    setPrompt("");
    setMessages(prev => [...prev, { role: "user", text: currentPrompt }]);

    try {
      const res = await fetch(`${API}/api/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: currentPrompt, context: activeTab }),
      });
      const data = await res.json();
      setMessages(prev => [...prev, {
        role: "agent",
        text: data.response || "Task executed.",
        logs: data.logs || [],
      }]);
    } catch (err: any) {
      setMessages(prev => [...prev, {
        role: "agent",
        text: `Connection error: ${err.message}`,
        logs: ["🚨 Backend unreachable on port 14143."],
      }]);
    }
  }, [prompt, agentStatus.running, activeTab]);

  // ── HITL control actions ──────────────────────────────────────────────────
  const hitlAction = useCallback(async (action: "pause" | "resume" | "stop") => {
    try {
      const res  = await fetch(`${API}/api/${action}`, { method: "POST" });
      const data = await res.json();
      // Refresh status immediately
      const statusRes  = await fetch(`${API}/api/status`);
      const statusData = await statusRes.json() as AgentStatus;
      setAgentStatus(statusData);

      // Show browser context in chat if paused
      if (action === "pause" && data.browser_context?.url) {
        setMessages(prev => [...prev, {
          role: "agent",
          text: `⏸️ Paused at: **${data.browser_context.title}**\n${data.browser_context.url}\n\nYou can now interact with the browser manually. Send a new prompt to auto-resume with full context, or click Resume.`,
        }]);
      }
    } catch (_) {}
  }, []);

  // ── 2FA submit ─────────────────────────────────────────────────────
  const submit2FA = useCallback(async () => {
    if (!twoFACode.trim()) return;
    try {
      await fetch(`${API}/api/submit-2fa`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: twoFACode.trim() }),
      });
      setMessages(prev => [...prev, { role: "user" as const, text: `🔐 2FA Code submitted` }]);
      setTwoFACode("");
    } catch (_) {}
  }, [twoFACode]);

  // 2FA coordination
  const _prev2fa = useRef(false);
  const otpInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (agentStatus.waiting_2fa && !_prev2fa.current) {
      setMessages(prev => [...prev, {
        role: "agent" as const,
        text: "🔐 **Two-Factor Authentication Required**\n\nThe site is asking for a verification code. Enter the OTP from your phone or email in the box below.",
      }]);
    }
    _prev2fa.current = agentStatus.waiting_2fa;
    
    if (agentStatus.waiting_2fa) {
      setTimeout(() => otpInputRef.current?.focus(), 100);
    }
  }, [agentStatus.waiting_2fa]);

  // ── Render helpers ─────────────────────────────────────────────────────────
  const stateColor = () => {
    switch (agentStatus.state) {
      case "running":     return "#10b981";
      case "paused":      return "#f59e0b";
      case "stopped":     return "#ef4444";
      case "waiting_2fa": return "#a855f7";
      default:            return "#64748b";
    }
  };

  const stateLabel = () => {
    switch (agentStatus.state) {
      case "running":     return "● Running";
      case "paused":      return "⏸ Paused";
      case "stopped":     return "■ Stopped";
      case "waiting_2fa": return "🔐 Waiting 2FA";
      default:            return "○ Idle";
    }
  };

  const renderContent = () => {
    if (activeTab === "settings") {
      return (
        <div className="chat-container">
          <div className="settings-panel">
            <h2 className="settings-title"><Settings size={20} /> Cognitive Engine Configuration</h2>

            {/* Sub-tab switcher */}
            <div style={{ display: "flex", gap: "8px", marginBottom: "24px" }}>
              {(["engine", "vault"] as const).map(t => (
                <button
                  key={t}
                  onClick={() => setSettingsTab(t)}
                  className="add-key-btn"
                  style={{
                    background: settingsTab === t ? "rgba(99,102,241,0.25)" : "rgba(255,255,255,0.05)",
                    border: `1px solid ${settingsTab === t ? "rgba(99,102,241,0.6)" : "rgba(255,255,255,0.1)"}`,
                    color: settingsTab === t ? "#a5b4fc" : "#94a3b8",
                    fontWeight: settingsTab === t ? 700 : 400,
                  }}
                >
                  {t === "engine" ? "⚙️ Engine Config" : "🔑 Credential Vault"}
                </button>
              ))}
            </div>

            {settingsTab === "engine" && <>
              <div className="settings-group">
                <label><BrainCircuit size={16}/> Default AI Model</label>
                <select 
                  value={sysSettings.primary_model}
                  onChange={e => setSysSettings({...sysSettings, primary_model: e.target.value})}
                  className="base-input combo-box"
                >
                  <option value="gemini-3.1-flash-lite-preview">Gemini 3.1 Flash-Lite (Optimized/Fast)</option>
                  <option value="gemini-2.0-flash">Gemini 2.0 Flash (Stable)</option>
                  <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
                  <option value="gemini-1.5-pro">Gemini 1.5 Pro</option>
                </select>
                <p className="setting-desc">The primary Large Language Model used to parse the DOM and plan tasks.</p>
              </div>

              <div className="settings-group">
                <label><Key size={16}/> API Key Rotation (Fallback Tier)</label>
                <p className="setting-desc">Add multiple API keys. If the agent hits a `429 Quota Exhausted` limit on one key, it will instantly rotate to the next key without halting the task.</p>
                
                <div className="api-keys-list">
                  {sysSettings.api_keys.map((keyObj, index) => (
                    <div key={index} className="api-key-row">
                      <input 
                        type="text"
                        className="base-input name-input"
                        placeholder="Demo Name"
                        value={keyObj.name}
                        style={{ width: "130px", flexShrink: 0, fontWeight: "600" }}
                        onChange={(e) => {
                          const newKeys = [...sysSettings.api_keys];
                          newKeys[index].name = e.target.value;
                          setSysSettings({...sysSettings, api_keys: newKeys});
                        }}
                      />
                      <input 
                        type="password"
                        className="base-input key-input"
                        placeholder={`API Key Payload`}
                        value={keyObj.key}
                        onChange={(e) => {
                          const newKeys = [...sysSettings.api_keys];
                          newKeys[index].key = e.target.value;
                          setSysSettings({...sysSettings, api_keys: newKeys});
                        }}
                      />
                      {keyObj.exhausted_until && new Date() < new Date(keyObj.exhausted_until) ? (
                        <span className="agent-status-pill" style={{ background: "rgba(239, 68, 68, 0.2)", color: "#f87171", border: "1px solid rgba(239, 68, 68, 0.4)", whiteSpace: "nowrap" }}>
                           🔒 Locked Until: {new Date(keyObj.exhausted_until).toLocaleTimeString()}
                        </span>
                      ) : (
                        <span className="agent-status-pill" style={{ background: "rgba(16, 185, 129, 0.15)", color: "#34d399", border: "1px solid rgba(16, 185, 129, 0.3)", whiteSpace: "nowrap" }}>
                           🟢 Available
                        </span>
                      )}
                      <button className="icon-btn danger" onClick={() => {
                        if (sysSettings.api_keys.length > 1) {
                          const newKeys = sysSettings.api_keys.filter((_, i) => i !== index);
                          setSysSettings({...sysSettings, api_keys: newKeys});
                        }
                      }}><Trash2 size={16} /></button>
                    </div>
                  ))}
                </div>
                
                <button 
                  className="add-key-btn" 
                  onClick={() => setSysSettings({...sysSettings, api_keys: [...sysSettings.api_keys, {name: `API Key #${sysSettings.api_keys.length + 1}`, key: "", exhausted_until: null}]})}
                >
                  <Plus size={16} /> Add Fallback Key
                </button>
              </div>

              <div className="settings-footer">
                 <button className="send-btn" onClick={handleSaveSettings}>
                   <Save size={18} /> Save Settings
                 </button>
              </div>
            </>}

            {settingsTab === "vault" && <>
              <div className="settings-group">
                <label><Key size={16}/> Secure Credential Vault</label>
                <p className="setting-desc">
                  Credentials stored here are automatically injected into login forms via direct Playwright input — the LLM never sees or processes the raw password.
                </p>

                {/* Stored credentials list */}
                <div className="api-keys-list" style={{ marginBottom: "16px" }}>
                  {vault.length === 0 && (
                    <div style={{ color: "#64748b", fontSize: "0.85rem", padding: "12px 0" }}>
                      No credentials stored yet. Add one below or just tell the agent your login details in the chat.
                    </div>
                  )}
                  {vault.map(cred => (
                    <div key={cred.id} className="api-key-row">
                      <span style={{ background: "rgba(99,102,241,0.15)", color: "#a5b4fc", padding: "4px 10px", borderRadius: "8px", fontSize: "0.8rem", fontWeight: 600, minWidth: "130px" }}>
                        {cred.domain}
                      </span>
                      <span style={{ color: "#e2e8f0", fontSize: "0.85rem", flex: 1 }}>
                        {cred.username}
                      </span>
                      <span style={{ color: "#475569", fontSize: "0.8rem" }}>••••••••</span>
                      <button className="icon-btn danger" onClick={() => handleDeleteCred(cred.id)}>
                        <Trash2 size={16} />
                      </button>
                    </div>
                  ))}
                  {vault.length > 0 && (
                    <button className="icon-btn danger" onClick={handleDeleteAllCreds} style={{ width: "100%", marginTop: "8px", fontSize: "0.8rem" }}>
                       <Trash2 size={14} /> Clear Entire Vault
                    </button>
                  )}
                </div>

                {/* Add new credential form */}
                <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                  <input
                    type="text"
                    className="base-input"
                    placeholder="Domain (e.g. instagram.com)"
                    value={newCred.domain}
                    onChange={e => setNewCred({...newCred, domain: e.target.value})}
                    style={{ flex: "1 1 160px" }}
                  />
                  <input
                    type="text"
                    className="base-input"
                    placeholder="Username / Email"
                    value={newCred.username}
                    onChange={e => setNewCred({...newCred, username: e.target.value})}
                    style={{ flex: "1 1 160px" }}
                  />
                  <input
                    type="password"
                    className="base-input"
                    placeholder="Password"
                    value={newCred.password}
                    onChange={e => setNewCred({...newCred, password: e.target.value})}
                    style={{ flex: "1 1 160px" }}
                  />
                  <button className="send-btn" onClick={handleAddCred} style={{ padding: "10px 18px" }}>
                    <Plus size={16} /> Add
                  </button>
                </div>
              </div>
            </>}

          </div>
        </div>
      );
    }


    if (activeTab === "audit_logs") {
      return (
        <div className="chat-container" style={{ justifyContent: "flex-start", maxWidth: "100%" }}>
          <div className="agent-logs" style={{ height: "100%", overflowY: "auto" }}>
            <div className="logs-header"><Activity size={14} /> Full System Audit Log</div>
            <div className="log-line">System Boot: OK</div>
            <div className="log-line">FastAPI Router: Listening on port 14143</div>
            {messages.map((m, i) => m.logs?.map((l, j) => (
              <div key={`${i}-${j}`} className="log-line">&gt; {l}</div>
            )))}
          </div>
        </div>
      );
    }

    let placeholder = "E.g., Research competitor pricing or execute a local script…";
    if (activeTab === "web_automation") {
      if (agentStatus.running)      placeholder = "⏳ Agent working… (Stop it first to send a new task)";
      else if (agentStatus.paused)  placeholder = "Agent paused — send a prompt to auto-resume with context…";
      else                          placeholder = "E.g., Navigate to github.com and scrape trending repos…";
    }
    if (activeTab === "local_shell") placeholder = "E.g., Write a python script to analyze my downloads folder…";

    const chatUI = (
      <div className={`chat-container ${activeTab === "web_automation" ? "split-left" : ""}`}>
        <div className="chat-history" ref={chatHistoryRef}>
          {messages.map((msg, idx) => (
            <div key={idx} className={`chat-message ${msg.role}`}>
              <div className="message-wrapper">
                {/* Reasoning Trail logs are now hidden from the primary chat UI for aesthetics */}
                <div className="message-bubble">{msg.text}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="input-container">
          {/* 🔐 2FA Overlay — replaces normal input when agent is waiting for OTP */}
          {agentStatus.waiting_2fa ? (
            <div style={{ display: "flex", gap: "8px", width: "100%", alignItems: "center",
              background: "rgba(168,85,247,0.08)", border: "1px solid rgba(168,85,247,0.4)",
              borderRadius: "12px", padding: "10px 14px" }}>
              <span style={{ fontSize: "1.2rem" }}>🔐</span>
              <input
                type="text"
                className="chat-input"
                ref={otpInputRef}
                value={twoFACode}
                onChange={e => setTwoFACode(e.target.value)}
                onKeyDown={e => e.key === "Enter" && submit2FA()}
                placeholder="Enter your 2FA / OTP code here…"
                style={{ flex: 1, letterSpacing: "0.2em", fontWeight: 700,
                  background: "transparent", border: "none", outline: "none" }}
                autoFocus
              />
              <button className="send-btn" onClick={submit2FA}
                style={{ background: "linear-gradient(135deg,#a855f7,#7c3aed)", padding: "8px 18px" }}>
                Submit Code
              </button>
            </div>
          ) : (
            <>
              <input
                type="text"
                className="chat-input"
                value={prompt}
                onChange={e => setPrompt(e.target.value)}
                onKeyDown={e => e.key === "Enter" && handleSend()}
                placeholder={placeholder}
              />

              {/* HITL Controls */}
              {activeTab === "web_automation" && (
                <div className="hitl-controls">
                  {agentStatus.running && (
                    <button className="hitl-btn pause" onClick={() => hitlAction("pause")}>
                      <Pause size={15} /><span>Pause</span>
                    </button>
                  )}
                  {agentStatus.paused && (
                    <button className="hitl-btn resume" onClick={() => hitlAction("resume")}>
                      <Play size={15} /><span>Resume</span>
                    </button>
                  )}
                  {(agentStatus.running || agentStatus.paused) && (
                    <button className="hitl-btn stop" onClick={() => hitlAction("stop")}>
                      <Square size={15} /><span>Stop</span>
                    </button>
                  )}
                </div>
              )}

              <button className="send-btn" onClick={handleSend} disabled={agentStatus.running}>
                <Command size={18} />
                <span>{agentStatus.running ? "Running…" : agentStatus.paused ? "Resume & Run" : "Execute"}</span>
              </button>
            </>
          )}
        </div>
      </div>
    );

    if (activeTab === "web_automation") {
      return (
        <div className="layout-split">
          {/* ── Left column: chat ── */}
          <div className="chat-col">
            {chatUI}
          </div>

          {/* ── Right column: browser mount + telemetry ── */}
          <div className="browser-pane">
            {/* Fake browser chrome header */}
            <div className="browser-header">
              <div className="browser-dots">
                <span className="dot red" />
                <span className="dot yellow" />
                <span className="dot green" />
              </div>
              <div className="browser-url-bar">
                {agentStatus.browser_context?.url
                  ? agentStatus.browser_context.url
                  : "Agent Telemetry Console"}
              </div>
              <div className="agent-status-pill" style={{
                background: `${stateColor()}22`,
                color: stateColor(),
                border: `1px solid ${stateColor()}55`,
              }}>
                {stateLabel()}
              </div>
            </div>

            {/* Interactive browser viewer — live screenshot stream */}
            <BrowserViewer />


            {/* Telemetry / logs — fills bottom portion */}
            <div className="agent-logs" style={{
              flex: 1, overflowY: "auto", borderRadius: 0, padding: "16px"
            }}>
              <div className="logs-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}><Activity size={14} /> Cognitive Execution Stream</span>
                {agentStatus.running && agentStatus.current_key_name && (
                  <span style={{ fontSize: '0.72rem', background: 'rgba(52, 211, 153, 0.1)', color: '#34d399', padding: '4px 10px', borderRadius: '12px', border: '1px solid rgba(52, 211, 153, 0.3)' }}>
                    🧠 {agentStatus.current_model} &nbsp;•&nbsp; 🔑 {agentStatus.current_key_name}
                  </span>
                )}
              </div>

              {agentStatus.live_plan && agentStatus.live_plan.steps.length > 0 && (
                <div className="live-plan-hud" style={{ padding: '12px', background: 'rgba(56, 189, 248, 0.05)', border: '1px solid rgba(56, 189, 248, 0.2)', borderRadius: '12px', marginBottom: '16px' }}>
                  <div style={{ color: '#38bdf8', fontWeight: 600, fontSize: '0.85rem', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                    <Activity size={14} style={{ marginRight: '6px', verticalAlign: 'text-bottom' }} /> Native Reasoning Stream
                  </div>
                  <div style={{ color: '#e2e8f0', fontSize: '0.9rem', marginBottom: '12px', fontStyle: 'italic' }}>
                    Tracking Cognitive Sub-goals...
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {agentStatus.live_plan.steps.map((step, idx) => {
                      const isCurrent = agentStatus.current_step_idx === idx;
                      let stepColor = "#64748b";
                      let icon = "⏳";
                      if (step.status === "success" || idx < (agentStatus.current_step_idx || 0)) { stepColor = "#10b981"; icon = "✅"; }
                      if (step.status === "failed") { stepColor = "#ef4444"; icon = "❌"; }
                      if (isCurrent && agentStatus.running) { stepColor = "#38bdf8"; icon = "🧠"; }
                      return (
                        <div key={idx} style={{
                          display: 'flex', alignItems: 'flex-start', gap: '10px',
                          padding: '10px', background: isCurrent ? 'rgba(56,189,248,0.1)' : 'rgba(0,0,0,0.2)',
                          border: `1px solid ${isCurrent ? 'rgba(56,189,248,0.3)' : 'rgba(255,255,255,0.05)'}`,
                          borderRadius: '8px', transition: 'all 0.3s ease'
                        }}>
                          <span style={{ fontSize: '1rem' }}>{icon}</span>
                          <div style={{ display: 'flex', flexDirection: 'column' }}>
                            <span style={{ color: stepColor, fontWeight: isCurrent ? 600 : 500, fontSize: '0.85rem' }}>
                              Step {step.id}: {step.intent}
                            </span>
                            <span style={{ color: '#94a3b8', fontSize: '0.75rem', marginTop: '4px', fontFamily: 'monospace' }}>
                              [{step.action}] {step.target}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {liveLogs.map((log, i) => {
                const clean = log.replace(/\x1b\[[0-9;]*[mG]/g, "").replace(/\x1b/g, "");
                let color = "#94a3b8";
                if (clean.includes("❌"))  color = "#ef4444";
                if (clean.includes("✅"))  color = "#10b981";
                if (clean.includes("⏸"))  color = "#f59e0b";
                if (clean.includes("▶️") || clean.includes("🚀") || clean.includes("⚡")) color = "#38bdf8";
                if (clean.includes("🛑"))  color = "#f87171";
                return <div key={i} className="log-line" style={{ color }}>{'>'} {clean}</div>;
              })}
              {liveLogs.length === 0 && !agentStatus.live_plan && (
                <div className="log-line" style={{ color: "#475569" }}>{'>'} Waiting for cognitive loop to start…</div>
              )}
              <div ref={logsEndRef} />
            </div>
          </div>
        </div>
      );
    }

    return chatUI;
  };

  return (
    <div className="container">
      <aside className="sidebar">
        <div className="sidebar-header">
          <BrainCircuit size={28} className="brand-icon" />
          <h2 className="brand-title">Nexus</h2>
        </div>
        <nav className="sidebar-nav">
          {[
            { id: "workspace",      icon: <Command size={20} />,  label: "Workspace" },
            { id: "web_automation", icon: <Globe size={20} />,    label: "Web Automation" },
            { id: "local_shell",    icon: <Terminal size={20} />, label: "Local Shell" },
            { id: "audit_logs",     icon: <Activity size={20} />, label: "Audit Logs" },
          ].map(({ id, icon, label }) => (
            <a key={id} href="#" className={`nav-item ${activeTab === id ? "active" : ""}`}
              onClick={() => setActiveTab(id)}>
              {icon}<span>{label}</span>
            </a>
          ))}
        </nav>
        <div className="sidebar-footer">
          <a href="#" className={`nav-item ${activeTab === "settings" ? "active" : ""}`} onClick={() => setActiveTab("settings")}>
            <Settings size={20} /><span>Settings</span>
          </a>
          <div className="profile">
            <User size={32} className="avatar" />
            <div className="profile-info">
              <span className="profile-name">Local System</span>
              <span className="profile-status">Zero-Cost API Active</span>
            </div>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="main-header">
          <h1>
            {activeTab === "workspace"      && "General Workspace"}
            {activeTab === "web_automation" && "Web Automation Engine"}
            {activeTab === "local_shell"    && "Local OS Control Shell"}
            {activeTab === "audit_logs"     && "System Audit Logs"}
            {activeTab === "settings"       && "Configuration"}
          </h1>
          <div className="status-badge">
            <span className="status-dot" />
            Agent Engine: Online
          </div>
        </header>
        {renderContent()}
      </main>
    </div>
  );
}

export default App;
