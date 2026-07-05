const messagesDiv = document.getElementById("messages") as HTMLDivElement;
const userInput = document.getElementById("user-input") as HTMLTextAreaElement;
const sendBtn = document.getElementById("send-btn") as HTMLButtonElement;
const clearBtn = document.getElementById("clear-btn") as HTMLButtonElement;
const rerollBtn = document.getElementById("reroll-btn") as HTMLButtonElement;
const deleteBtn = document.getElementById("delete-btn") as HTMLButtonElement;
const moreBtn = document.getElementById("more-btn") as HTMLButtonElement;
const extraOptions = document.getElementById("extra-options") as HTMLDivElement;
const refreshBtn = document.getElementById("refresh-btn") as HTMLButtonElement;
const artifactList = document.getElementById("artifact-list") as HTMLDivElement;
const agentList = document.getElementById("agent-list") as HTMLDivElement;
let isThinking = false;
let clientTriggerGen: number | null = null;
let lastHistoryVersion = -1;
let isEditingArtifact = false;
let conversationHistory: {
  role: string;
  content: string;
  raw_content?: string;
}[] = [];
let currentArtifactVersions: Record<string, [string, number][]> = {};
let currentViewedArtifact: string | null = null;
let currentArtifactVersionIndex: number = 0;
let userRoleName = "User";
let runningAgents: Record<string, number> = {};
let availableAgents: string[] = [];
let currentViewedAgent: string | null = null;
let agentElapsedInterval: ReturnType<typeof setInterval> | null = null;

async function fetchUserRole() {
  try {
    const res = await fetch("/api/health");
    if (res.ok) {
      const data = await res.json();
      if (data.user_role) {
        userRoleName = data.user_role;
      }
    }
  } catch (e) {
    console.error("Failed to fetch user role:", e);
  }
}

fetchUserRole();

function setThinking(val: boolean, triggerGen?: number) {
  isThinking = val;
  if (val) clientTriggerGen = triggerGen ?? null;
  sendBtn.disabled = val;
  rerollBtn.disabled = val;
  deleteBtn.disabled = val;
  if (val) {
    sendBtn.textContent = "...";
  } else {
    sendBtn.textContent = "Send";
  }
}

function saveHistory() {
  return fetch("/api/history", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ history: conversationHistory }),
  }).catch((e) => console.error("Failed to save history:", e));
}

const backBtn = document.getElementById(
  "back-to-chat-btn",
) as HTMLButtonElement;

async function rebuildArtifacts() {
  try {
    const res = await fetch("/api/artifacts");
    if (res.ok) {
      const data = await res.json();
      refreshArtifactsUI(data.artifacts || {}, data.version_counts || {});
    }
  } catch (e) {
    console.error("Failed to rebuild artifacts:", e);
  }
}

async function fetchArtifactVersions(
  artipath: string,
): Promise<[string, number][]> {
  try {
    const res = await fetch(
      `/api/artifacts/${encodeURIComponent(artipath)}/versions`,
    );
    if (res.ok) {
      const data = await res.json();
      const versions: [string, number][] = data.versions || [];
      currentArtifactVersions[artipath] = versions;
      return versions;
    }
  } catch (e) {
    console.error(`Failed to fetch versions for ${artipath}:`, e);
  }
  return [];
}

async function rebuildAgents() {
  try {
    const res = await fetch("/api/agents");
    if (res.ok) {
      const data = await res.json();
      refreshAgentsUI(data.agents || []);
    }
  } catch (e) {
    console.error("Failed to rebuild agents:", e);
  }
}

async function runAgent(agentName: string) {
  if (isThinking) return;
  setThinking(true);
  try {
    const res = await fetch(`/api/agents/${agentName}/run`, {
      method: "POST",
    });
    if (!res.ok) {
      const data = await res.json();
      alert(data.error || "Failed to run agent");
    }
  } catch (e) {
    console.error("Failed to run agent:", e);
  } finally {
    setTimeout(() => setThinking(false), 1000);
  }
}

async function cancelAgent(agentName: string) {
  try {
    const res = await fetch(`/api/agents/${agentName}/stop`, {
      method: "POST",
    });
    if (!res.ok) {
      const data = await res.json();
      alert(data.error || "Failed to stop agent");
    }
  } catch (e) {
    console.error("Failed to stop agent:", e);
  }
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function openAgent(name: string) {
  if (window.innerWidth <= 768) {
    toggleSidebar();
  }

  chatContainer.style.display = "none";
  mainArtifactView.style.display = "none";
  mainAgentView.style.display = "flex";
  backBtn.style.display = "inline-block";

  currentViewedAgent = name;
  renderAgentView(name);

  // Live-update elapsed time if running
  if (agentElapsedInterval) clearInterval(agentElapsedInterval);
  agentElapsedInterval = setInterval(() => {
    if (currentViewedAgent) renderAgentView(currentViewedAgent);
  }, 1000);
}

function renderAgentView(name: string) {
  const isRunning = name in runningAgents;
  const startedAt = isRunning ? runningAgents[name] : null;

  let statusHtml = "";
  if (isRunning && startedAt) {
    const elapsed = Date.now() / 1000 - startedAt;
    statusHtml = `<span style="color: var(--accent);">⚙️ Running — ${formatElapsed(elapsed)}</span>`;
  } else {
    statusHtml = '<span style="color: #888;">Idle</span>';
  }

  agentContentArea.innerHTML = `
    <div style="font-size: 1.5rem; font-weight: bold; color: var(--accent);">${name}</div>
    <div style="font-size: 1.1rem;">${statusHtml}</div>
  `;

  agentStatus.textContent = isRunning
    ? `Running — ${formatElapsed(Date.now() / 1000 - startedAt!)}`
    : "Idle";

  agentRunBtn.style.display = isRunning ? "none" : "inline-block";
  agentCancelBtn.style.display = isRunning ? "inline-block" : "none";

  agentRunBtn.onclick = () => runAgent(name);
  agentCancelBtn.onclick = () => cancelAgent(name);
}

async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    if (res.ok) {
      const data = await res.json();
      conversationHistory = data.history || [];
    } else {
      conversationHistory = [];
    }
  } catch (e) {
    console.error("Failed to load history:", e);
    conversationHistory = [];
  }

  if (conversationHistory.length === 0) {
    const saved = localStorage.getItem("paludoroHistory");
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          conversationHistory = parsed;
          saveHistory();
          console.log("Migrated history from localStorage to backend.");
        }
      } catch (e) {}
    }
  }

  renderHistory();
  rebuildArtifacts();
  rebuildAgents();
}
const chatContainer = document.getElementById(
  "chat-container",
) as HTMLDivElement;
const mainArtifactView = document.getElementById(
  "main-artifact-view",
) as HTMLDivElement;
const artifactContentArea = document.getElementById(
  "artifact-content-area",
) as HTMLDivElement;
const artifactMenuBtn = document.getElementById(
  "artifact-menu-btn",
) as HTMLButtonElement;
const artifactTimestamp = document.getElementById(
  "artifact-timestamp",
) as HTMLSpanElement;
const artifactPrevBtn = document.getElementById(
  "artifact-prev-btn",
) as HTMLButtonElement;
const artifactNextBtn = document.getElementById(
  "artifact-next-btn",
) as HTMLButtonElement;
const artifactVersionDisplay = document.getElementById(
  "artifact-version-display",
) as HTMLSpanElement;

// Agent detail view elements
const mainAgentView = document.getElementById(
  "main-agent-view",
) as HTMLDivElement;
const agentContentArea = document.getElementById(
  "agent-content-area",
) as HTMLDivElement;
const agentMenuBtn = document.getElementById(
  "agent-menu-btn",
) as HTMLButtonElement;
const agentStatus = document.getElementById("agent-status") as HTMLSpanElement;
const agentRunBtn = document.getElementById(
  "agent-run-btn",
) as HTMLButtonElement;
const agentCancelBtn = document.getElementById(
  "agent-cancel-btn",
) as HTMLButtonElement;

agentMenuBtn.onclick = () => {
  toggleSidebar();
};

artifactMenuBtn.onclick = () => {
  toggleSidebar();
};

backBtn.onclick = () => {
  isEditingArtifact = false;
  chatContainer.style.display = "flex";
  mainArtifactView.style.display = "none";
  mainAgentView.style.display = "none";
  backBtn.style.display = "none";
  currentViewedAgent = null;
  if (agentElapsedInterval) {
    clearInterval(agentElapsedInterval);
    agentElapsedInterval = null;
  }
};

const toggleSidebarBtn = document.getElementById(
  "toggle-sidebar",
) as HTMLButtonElement;
const sidebar = document.getElementById("sidebar") as HTMLDivElement;
const sidebarOverlay = document.getElementById(
  "sidebar-overlay",
) as HTMLDivElement;
const closeSidebarBtn = document.getElementById(
  "close-sidebar",
) as HTMLButtonElement;

function toggleSidebar() {
  sidebar.classList.toggle("open");
  sidebarOverlay.classList.toggle("open");
}

toggleSidebarBtn.onclick = toggleSidebar;
sidebarOverlay.onclick = toggleSidebar;
closeSidebarBtn.onclick = toggleSidebar;

const resizer = document.getElementById("sidebar-resizer") as HTMLDivElement;
const artifactsSection = document.getElementById(
  "artifacts-section",
) as HTMLDivElement;
const agentsSection = document.getElementById(
  "agents-section",
) as HTMLDivElement;

if (resizer && artifactsSection && agentsSection) {
  let isResizing = false;

  resizer.onmousedown = (e) => {
    isResizing = true;
    document.body.style.cursor = "ns-resize";
    e.preventDefault();
  };

  window.addEventListener("mousemove", (e) => {
    if (!isResizing) return;

    const sidebarRect = sidebar.getBoundingClientRect();
    const relativeY = e.clientY - sidebarRect.top;
    const totalHeight = sidebarRect.height;

    // Convert to percentage
    const percentage = (relativeY / totalHeight) * 100;

    // Constraints (10% to 90%)
    if (percentage > 10 && percentage < 90) {
      artifactsSection.style.flex = `0 0 ${percentage}%`;
      agentsSection.style.flex = `1 1 auto`;
    }
  });

  window.addEventListener("mouseup", () => {
    if (isResizing) {
      isResizing = false;
      document.body.style.cursor = "";
    }
  });
}

// Show close button only when sidebar is "open" as a mobile overlay
function updateSidebarUI() {
  if (window.innerWidth <= 768) {
    closeSidebarBtn.style.display = "block";
  } else {
    closeSidebarBtn.style.display = "none";
    sidebar.classList.remove("open");
    sidebarOverlay.classList.remove("open");
  }
}
window.addEventListener("resize", updateSidebarUI);
updateSidebarUI();

const inputArea = document.getElementById("input-area") as HTMLDivElement;

// Handle mobile keyboard "squash" (safely)
if (window.visualViewport) {
  const resizeHandler = () => {
    const vv = window.visualViewport!;
    // Calculate how much of the layout viewport is hidden by keyboard/bars
    // We scale the height to get the "logical" visible height
    const visualHeight = vv.height * vv.scale;
    const layoutHeight = window.innerHeight;
    const offset = Math.max(0, layoutHeight - visualHeight);

    // Instead of shrinking the body (which breaks zoom focal points),
    // we push the input area up using padding.
    // This allows the flex container to squash the message area.
    inputArea.style.paddingBottom = `${offset}px`;

    // Reset body height to let CSS (100dvh) handle the main container
    document.body.style.height = "";

    // Keep chat scrolled to bottom
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
  };

  window.visualViewport.addEventListener("resize", resizeHandler);
  window.visualViewport.addEventListener("scroll", resizeHandler);
  // Initial call
  resizeHandler();
}

const editArtifactBtn = document.getElementById(
  "edit-artifact-btn",
) as HTMLButtonElement;
const saveArtifactBtn = document.getElementById(
  "save-artifact-btn",
) as HTMLButtonElement;
const cancelEditBtn = document.getElementById(
  "cancel-edit-btn",
) as HTMLButtonElement;
const deleteVersionBtn = document.getElementById(
  "delete-version-btn",
) as HTMLButtonElement;

function isImageContent(content: string): boolean {
  return content.startsWith("data:image") || content.startsWith("/images/");
}

function exitEditMode() {
  isEditingArtifact = false;
  renderCurrentArtifactVersion();
}

function enterEditMode() {
  if (!currentViewedArtifact || !currentArtifactVersions[currentViewedArtifact])
    return;
  const versions = currentArtifactVersions[currentViewedArtifact];
  const [content, turn] = versions[currentArtifactVersionIndex];
  if (isImageContent(content as string)) return;

  isEditingArtifact = true;
  renderCurrentArtifactVersion();
}

function renderCurrentArtifactVersion() {
  if (!currentViewedArtifact || !currentArtifactVersions[currentViewedArtifact])
    return;
  const versions = currentArtifactVersions[currentViewedArtifact];
  const [content, turn] = versions[currentArtifactVersionIndex];
  const isImage = isImageContent(content as string);

  // Force exit edit mode for images or if content is not a string
  if (isEditingArtifact && isImage) {
    isEditingArtifact = false;
  }

  // Render content
  if (isEditingArtifact) {
    artifactContentArea.style.padding = "0";
    let textarea = document.getElementById(
      "artifact-editor",
    ) as HTMLTextAreaElement | null;
    if (!textarea) {
      artifactContentArea.innerHTML = "";
      textarea = document.createElement("textarea");
      textarea.id = "artifact-editor";
      textarea.spellcheck = false;
      textarea.style.cssText =
        "width:100%;height:100%;background:#1a1a1a;color:#e0e0e0;" +
        "border:none;border-left:3px solid var(--accent);font-family:monospace;" +
        "font-size:0.9rem;padding:1rem;resize:none;box-sizing:border-box;" +
        "outline:none;line-height:1.5;";
      artifactContentArea.appendChild(textarea);
    }
    textarea.value = content as string;
    textarea.focus();
  } else {
    artifactContentArea.style.padding = "1rem";
    if (isImage) {
      artifactContentArea.innerHTML = `<img src="${content}" style="max-width: 100%; height: auto; display: block; margin: 0 auto;" />`;
    } else {
      artifactContentArea.textContent = content as string;
    }
  }

  // Button visibility
  editArtifactBtn.style.display =
    !isEditingArtifact && !isImage ? "inline-block" : "none";
  saveArtifactBtn.style.display = isEditingArtifact ? "inline-block" : "none";
  cancelEditBtn.style.display = isEditingArtifact ? "inline-block" : "none";
  deleteVersionBtn.style.display =
    !isEditingArtifact && !isImage && versions.length > 1
      ? "inline-block"
      : "none";

  artifactTimestamp.textContent =
    turn === 0 ? "Default" : `Generated at Turn ${turn}`;
  artifactVersionDisplay.textContent = `v${currentArtifactVersionIndex + 1} / v${versions.length}`;

  artifactPrevBtn.disabled = currentArtifactVersionIndex === 0;
  artifactNextBtn.disabled =
    currentArtifactVersionIndex === versions.length - 1;
}

editArtifactBtn.onclick = () => enterEditMode();
cancelEditBtn.onclick = () => exitEditMode();

saveArtifactBtn.onclick = async () => {
  const textarea = document.getElementById(
    "artifact-editor",
  ) as HTMLTextAreaElement | null;
  if (!textarea || !currentViewedArtifact) return;

  const newContent = textarea.value;
  isEditingArtifact = false;

  try {
    const res = await fetch(
      `/api/artifacts/${encodeURIComponent(currentViewedArtifact)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: newContent }),
      },
    );
    if (res.ok) {
      await rebuildArtifacts();
      // Jump to the newly saved version (last)
      const vers = currentArtifactVersions[currentViewedArtifact];
      if (vers) {
        currentArtifactVersionIndex = vers.length - 1;
        renderCurrentArtifactVersion();
      }
    } else {
      isEditingArtifact = true;
      alert("Failed to save artifact");
    }
  } catch (e) {
    isEditingArtifact = true;
    console.error("Failed to save:", e);
    alert("Failed to save artifact");
  }
};

deleteVersionBtn.onclick = async () => {
  if (!currentViewedArtifact) return;
  const versions = currentArtifactVersions[currentViewedArtifact];
  if (!versions || versions.length <= 1) return;

  const label = `v${currentArtifactVersionIndex + 1} of ${versions.length}`;
  if (!confirm(`Delete ${label} of ${currentViewedArtifact}?`)) return;

  try {
    const res = await fetch(
      `/api/artifacts/${encodeURIComponent(currentViewedArtifact)}/versions/${currentArtifactVersionIndex}`,
      { method: "DELETE" },
    );
    if (res.ok) {
      const data = await res.json();
      currentArtifactVersions = data.artifact_versions;
      const newVersions = currentArtifactVersions[currentViewedArtifact];
      if (currentArtifactVersionIndex >= newVersions.length) {
        currentArtifactVersionIndex = newVersions.length - 1;
      }
      renderCurrentArtifactVersion();
    } else {
      const data = await res.json();
      alert(data.error || "Failed to delete version");
    }
  } catch (e) {
    console.error("Failed to delete:", e);
    alert("Failed to delete version");
  }
};

artifactPrevBtn.onclick = () => {
  if (currentArtifactVersionIndex > 0) {
    isEditingArtifact = false;
    currentArtifactVersionIndex--;
    renderCurrentArtifactVersion();
  }
};

artifactNextBtn.onclick = () => {
  if (
    currentViewedArtifact &&
    currentArtifactVersionIndex <
      currentArtifactVersions[currentViewedArtifact].length - 1
  ) {
    isEditingArtifact = false;
    currentArtifactVersionIndex++;
    renderCurrentArtifactVersion();
  }
};

function refreshAgentsUI(agents: string[]) {
  availableAgents = agents;
  agentList.innerHTML = "";
  agents.forEach((name) => {
    const isRunning = name in runningAgents;

    const item = document.createElement("div");
    item.className = "artifact-item"; // Reuse same style
    item.style.display = "flex";
    item.style.justifyContent = "space-between";
    item.style.alignItems = "center";
    item.style.cursor = "pointer";
    if (isRunning) {
      item.style.color = "var(--accent)";
    }

    const nameSpan = document.createElement("span");
    nameSpan.textContent = isRunning ? `⚙️ ${name}` : name;
    item.appendChild(nameSpan);

    item.onclick = () => openAgent(name);

    agentList.appendChild(item);
  });
}

let knownArtifactNames: string[] = [];
let currentArtifactContent: Record<string, string> = {};

function refreshArtifactsUI(
  artifacts: Record<string, string>,
  versionCounts: Record<string, number>,
) {
  const artifactNames = Object.keys(artifacts);

  // Cache current content for instant display on click
  currentArtifactContent = { ...currentArtifactContent, ...artifacts };

  // Skip DOM rebuild if artifact names haven't changed
  const namesChanged =
    JSON.stringify(artifactNames) !== JSON.stringify(knownArtifactNames);
  knownArtifactNames = artifactNames;

  if (namesChanged) {
    artifactList.innerHTML = "";
    for (const name of artifactNames) {
      const item = document.createElement("div");
      item.className = "artifact-item";
      item.textContent = name;
      item.onclick = () => openArtifact(name);
      artifactList.appendChild(item);
    }
  }

  if (artifactNames.length === 0) {
    artifactContentArea.style.display = "none";
  } else {
    // Don't clobber the editor while the user is typing
    if (isEditingArtifact) return;

    // If the currently viewed artifact is still present, show updated content
    // immediately and refresh version history in background
    const currentName = currentViewedArtifact;
    if (currentName && artifacts[currentName] !== undefined) {
      // Show latest content right away (single version, no nav)
      currentArtifactVersions[currentName] = [
        [artifacts[currentName], versionCounts[currentName] || 0],
      ];
      currentArtifactVersionIndex = 0;
      renderCurrentArtifactVersion();

      // Fetch full version history in background
      fetchArtifactVersions(currentName).then((vers) => {
        if (vers.length > 0) {
          currentArtifactVersionIndex = vers.length - 1;
          renderCurrentArtifactVersion();
        }
      });
    }
  }
}

async function openArtifact(name: string) {
  // Close sidebar if on mobile
  if (window.innerWidth <= 768) {
    toggleSidebar();
  }

  chatContainer.style.display = "none";
  mainArtifactView.style.display = "flex";
  mainAgentView.style.display = "none";
  backBtn.style.display = "inline-block";

  currentViewedAgent = null;
  if (agentElapsedInterval) {
    clearInterval(agentElapsedInterval);
    agentElapsedInterval = null;
  }

  isEditingArtifact = false;
  currentViewedArtifact = name;

  // Show cached current content immediately
  if (currentArtifactContent[name] !== undefined) {
    currentArtifactVersions[name] = [[currentArtifactContent[name], 0]];
    currentArtifactVersionIndex = 0;
    renderCurrentArtifactVersion();
  }

  // Fetch full version history in background for nav + editing
  const vers = await fetchArtifactVersions(name);
  if (vers.length > 0) {
    currentArtifactVersionIndex = vers.length - 1;
    renderCurrentArtifactVersion();
  }
}

function addMessageUI(role: string, content: string) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  // Auto-link any image paths to actual img tags for inline viewing
  let htmlContent = content.replace(
    /(?:\!\[.*?\]\()?(\/images\/[a-zA-Z0-9_\-\.]+\.png)\)?/g,
    '<img src="$1" style="max-width: 100%; height: auto; display: block; margin: 0.5rem 0;" />',
  );
  div.innerHTML = `<strong>${role}:</strong> ${htmlContent}`;
  messagesDiv.appendChild(div);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function renderHistory() {
  messagesDiv.innerHTML = "";
  conversationHistory.forEach((m) => addMessageUI(m.role, m.content));
  rerollBtn.disabled = conversationHistory.length === 0 || isThinking;
  deleteBtn.disabled = conversationHistory.length === 0 || isThinking;
}

async function sendMessage() {
  const text = userInput.value.trim();
  if (!text || isThinking) return;

  userInput.value = "";
  userInput.focus();
  conversationHistory.push({
    role: userRoleName,
    content: text,
    raw_content: text,
  });
  // Do NOT saveHistory() here. The backend transcript agent will append /dev/stdin
  // to chat_history.sxpb. If we saveHistory() here, it duplicates the User message.
  renderHistory();
  setThinking(true);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history: conversationHistory, message: text }),
    });

    const data = await res.json();
    if (data.error) {
      alert(data.error);
      return;
    }
    // Capture the trigger gen so the poll can detect when this pipeline finishes
    if (typeof data.trigger_gen === "number") {
      clientTriggerGen = data.trigger_gen;
    }
  } catch (e) {
    console.warn("Connection error. Polling will attempt recovery.");
  } finally {
    setThinking(false);
    userInput.focus();
  }
}

async function rerollLast() {
  if (isThinking) return;
  if (conversationHistory.length === 0) return;

  if (
    conversationHistory[conversationHistory.length - 1].role.toLowerCase() !==
    "user"
  ) {
    conversationHistory.pop();
    // saveHistory();
    renderHistory();
  }

  setThinking(true);
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reroll: true }),
    });

    const data = await res.json();
    if (data.error) {
      alert(data.error);
      return;
    }
    // Capture the trigger gen so the poll can detect when this pipeline finishes
    if (typeof data.trigger_gen === "number") {
      clientTriggerGen = data.trigger_gen;
    }
  } catch (e) {
    console.warn("Connection error. Polling will attempt recovery.");
  } finally {
    setThinking(false);
    userInput.focus();
  }
}

async function deleteLastTurn() {
  if (isThinking) return;
  if (conversationHistory.length === 0) return;
  if (!confirm("Delete the last user message and assistant response?")) return;

  if (
    conversationHistory[conversationHistory.length - 1].role.toLowerCase() !==
    "user"
  ) {
    conversationHistory.pop();
  }
  if (
    conversationHistory.length > 0 &&
    (conversationHistory[conversationHistory.length - 1].role ===
      userRoleName ||
      conversationHistory[conversationHistory.length - 1].role === "User" ||
      conversationHistory[conversationHistory.length - 1].role === "user")
  ) {
    conversationHistory.pop();
  }

  saveHistory();
  renderHistory();
  await rebuildArtifacts();
  // userInput.focus();
}

async function clearChat() {
  if (isThinking) return;
  if (!confirm("Are you sure you want to clear the entire session?")) return;

  conversationHistory = [];
  await saveHistory();
  renderHistory();
  rebuildArtifacts();
  // userInput.focus();
}

sendBtn.onclick = sendMessage;
clearBtn.onclick = clearChat;
rerollBtn.onclick = rerollLast;
deleteBtn.onclick = deleteLastTurn;

moreBtn.onclick = () => {
  const isHidden = extraOptions.style.display === "none";
  extraOptions.style.display = isHidden ? "flex" : "none";
  moreBtn.textContent = isHidden ? "«" : "⋯";
};

refreshBtn.onclick = () => {
  window.location.reload();
};

userInput.onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
};

setInterval(async () => {
  try {
    const res = await fetch("/api/poll");
    if (res.ok) {
      const data = await res.json();

      // --- History version check (authoritative for ALL history changes) ---
      if (
        data.history_version !== undefined &&
        data.history_version !== lastHistoryVersion
      ) {
        const histRes = await fetch("/api/history");
        if (histRes.ok) {
          const histData = await histRes.json();
          conversationHistory = histData.history || [];
          renderHistory();
          lastHistoryVersion = data.history_version;
        }
      }

      // --- Pipeline finish detection ---
      if (isThinking) {
        if (
          clientTriggerGen === null &&
          typeof data.pipeline_triggers === "number"
        ) {
          clientTriggerGen = data.pipeline_triggers;
        }
        if (
          clientTriggerGen !== null &&
          typeof data.pipeline_finishes === "number" &&
          data.pipeline_finishes >= clientTriggerGen
        ) {
          setThinking(false);
          rebuildArtifacts();
        }
      }

      // --- Dirty artifacts (intermediate pipeline output) ---
      if (data.artifacts && Object.keys(data.artifacts).length > 0) {
        let lastAsst = [...conversationHistory]
          .reverse()
          .find((m) => m.role.toLowerCase() !== "user");
        if (lastAsst) {
          for (const [artipath, content] of Object.entries(data.artifacts)) {
            if (!lastAsst.raw_content?.includes(`>${artipath}`)) {
              lastAsst.raw_content += `\n\n\`\`\`sxpb > ${artipath}\n${content}\n\`\`\`\n`;
            }
          }
          rebuildArtifacts();
        }
      }

      // --- Running agents ---
      if (data.running_agents) {
        const newRunning: Record<string, number> = data.running_agents;
        if (JSON.stringify(newRunning) !== JSON.stringify(runningAgents)) {
          runningAgents = newRunning;
          refreshAgentsUI(availableAgents);
          // Refresh agent detail view if open
          if (currentViewedAgent) {
            renderAgentView(currentViewedAgent);
          }
        }
      }
    }
  } catch (e) {
    // Ignore polling errors
  }
}, 2000);

// ---------------------------------------------------------------------------
// LLM Request Log
// ---------------------------------------------------------------------------
const llmLogBtn = document.getElementById("llm-log-btn") as HTMLButtonElement;
const llmLogOverlay = document.getElementById(
  "llm-log-overlay",
) as HTMLDivElement;
const llmLogCloseBtn = document.getElementById(
  "llm-log-close-btn",
) as HTMLButtonElement;
const llmLogContent = document.getElementById(
  "llm-log-content",
) as HTMLDivElement;

function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function formatTimestamp(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function renderLlmLog(requests: any[]) {
  if (requests.length === 0) {
    llmLogContent.innerHTML =
      '<p style="color: #888; text-align: center">No LLM requests recorded yet.</p>';
    return;
  }

  // Show newest first
  const sorted = [...requests].reverse();

  llmLogContent.innerHTML = sorted
    .map((req, i) => {
      const isError = !!req.error;
      const borderColor = isError ? "var(--danger)" : "#4a4";
      const statusIcon = isError ? "❌" : "✅";
      const kindBadge = req.kind === "image" ? "🖼️" : "💬";

      // Build messages preview — each per-role message is individually collapsible
      let messagesPreview = "";
      if (req.request_messages && req.request_messages.length > 0) {
        messagesPreview = req.request_messages
          .map((m: any) => {
            const role = escapeHtml(m.role || "?");
            const content = escapeHtml(
              typeof m.content === "string"
                ? m.content
                : JSON.stringify(m.content),
            );
            const preview =
              content.length > 120 ? content.slice(0, 120) + "…" : content;
            return `<details style="margin: 0.25rem 0; border: 1px solid #333; border-radius: 4px; overflow: hidden;"><summary style="cursor: pointer; color: var(--accent); font-weight: bold; padding: 0.35rem 0.5rem; background: #1a1a1a;">${role}</summary><div style="padding: 0.35rem 0.5rem; background: #111; color: #ccc; white-space: pre-wrap;">${content}</div></details>`;
          })
          .join("");
      }

      // Response or error
      let responseBlock = "";

      // Reasoning (if available)
      let reasoningBlock = "";
      if (req.reasoning) {
        reasoningBlock = `<details style="margin-top: 0.5rem;"><summary style="cursor: pointer; color: #88a; font-size: 0.85em;">Reasoning</summary><div style="padding: 0.5rem; background: #1a1a2a; border-left: 3px solid #88a; border-radius: 4px; margin-top: 0.25rem;"><pre style="color: #aac; margin: 0; white-space: pre-wrap; word-break: break-word;">${escapeHtml(req.reasoning)}</pre></div></details>`;
      }
      if (isError) {
        responseBlock = `<details style="margin-top: 0.5rem;"><summary style="cursor: pointer; color: var(--danger); font-size: 0.85em;">Error</summary><div style="padding: 0.5rem; background: #2a1111; border-left: 3px solid var(--danger); border-radius: 4px; margin-top: 0.25rem;"><span style="color: #faa;">${escapeHtml(req.error || "")}</span></div></details>`;
      } else if (req.response) {
        responseBlock = `<details style="margin-top: 0.5rem;"><summary style="cursor: pointer; color: #4a4; font-size: 0.85em;">Response</summary><div style="padding: 0.5rem; background: #112a11; border-left: 3px solid #4a4; border-radius: 4px; margin-top: 0.25rem;"><pre style="color: #cfc; margin: 0; white-space: pre-wrap; word-break: break-word;">${escapeHtml(req.response)}</pre></div></details>`;
      } else {
        responseBlock = `<div style="margin-top: 0.5rem; color: #888;">(empty response)</div>`;
      }

      return `
        <div style="margin-bottom: 1rem; border-left: 3px solid ${borderColor}; padding: 0.5rem 0.75rem; background: #1e1e1e; border-radius: 4px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem; flex-wrap: wrap; gap: 0.3rem;">
            <span>${statusIcon} ${kindBadge} <strong style="color: var(--accent);">${escapeHtml(req.agent || "?")}</strong></span>
            <span style="color: #888; font-size: 0.8em;">
              ${formatTimestamp(req.timestamp)} &middot; ${req.duration}s &middot; ${escapeHtml(req.model || "?")}
            </span>
          </div>
          <details>
            <summary style="cursor: pointer; color: #aaa; font-size: 0.85em;">Messages (${req.request_messages?.length || 0})</summary>
            ${messagesPreview}
          </details>
          ${reasoningBlock}
          ${responseBlock}
        </div>
      `;
    })
    .join("");
}

async function openLlmLog() {
  llmLogOverlay.style.display = "flex";
  llmLogContent.innerHTML =
    '<p style="color: #888; text-align: center">Loading...</p>';
  try {
    const res = await fetch("/api/llm-requests");
    if (res.ok) {
      const data = await res.json();
      renderLlmLog(data.requests || []);
    } else {
      llmLogContent.innerHTML =
        '<p style="color: var(--danger);">Failed to load LLM log.</p>';
    }
  } catch (e) {
    llmLogContent.innerHTML =
      '<p style="color: var(--danger);">Connection error.</p>';
  }
}

llmLogBtn.onclick = openLlmLog;

function closeLlmLog() {
  llmLogOverlay.style.display = "none";
}

llmLogCloseBtn.onclick = closeLlmLog;

llmLogOverlay.onclick = (e) => {
  if (e.target === llmLogOverlay) {
    closeLlmLog();
  }
};

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && llmLogOverlay.style.display === "flex") {
    closeLlmLog();
  }
});

// Initial Load
loadHistory();
