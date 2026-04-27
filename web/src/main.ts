const messagesDiv = document.getElementById("messages") as HTMLDivElement;
const userInput = document.getElementById("user-input") as HTMLTextAreaElement;
const sendBtn = document.getElementById("send-btn") as HTMLButtonElement;
const clearBtn = document.getElementById("clear-btn") as HTMLButtonElement;
const rerollBtn = document.getElementById("reroll-btn") as HTMLButtonElement;
const deleteBtn = document.getElementById("delete-btn") as HTMLButtonElement;
const fileList = document.getElementById("file-list") as HTMLDivElement;
const fileContent = document.getElementById("file-content") as HTMLDivElement;

let isThinking = false;
let conversationHistory: {
  role: string;
  content: string;
  raw_content?: string;
}[] = [];

function setThinking(val: boolean) {
  isThinking = val;
  sendBtn.disabled = val;
  rerollBtn.disabled = val;
  deleteBtn.disabled = val;
  userInput.disabled = val;
  if (val) {
    sendBtn.textContent = "...";
  } else {
    sendBtn.textContent = "Send";
  }
}

function saveHistory() {
  localStorage.setItem("paludoroHistory", JSON.stringify(conversationHistory));
}

async function rebuildFiles() {
  try {
    const res = await fetch("/api/files", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history: conversationHistory }),
    });
    if (res.ok) {
      const data = await res.json();
      refreshFilesUI(data.files || {});
    }
  } catch (e) {
    console.error("Failed to rebuild files:", e);
  }
}

function refreshFilesUI(files: Record<string, string>) {
  fileList.innerHTML = "";
  for (const [name, content] of Object.entries(files)) {
    const item = document.createElement("div");
    item.className = "file-item";
    item.textContent = name;
    item.onclick = () => {
      fileContent.textContent = content as string;
      fileContent.style.display = "block";
    };
    fileList.appendChild(item);
  }
  if (Object.keys(files).length === 0) {
    fileContent.style.display = "none";
  } else {
    // If the currently viewed file is still in the files, update its content. Otherwise hide.
    const currentName = fileList.querySelector(".active")?.textContent;
    if (currentName && files[currentName]) {
      fileContent.textContent = files[currentName] as string;
    }
  }
}

function addMessageUI(role: string, content: string) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.innerHTML = `<strong>${role === "user" ? "You" : "Assistant"}:</strong> ${content}`;
  messagesDiv.appendChild(div);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function renderHistory() {
  messagesDiv.innerHTML = "";
  conversationHistory.forEach((m) => addMessageUI(m.role, m.content));
  rerollBtn.disabled = conversationHistory.length === 0 || isThinking;
  deleteBtn.disabled = conversationHistory.length === 0 || isThinking;
}

function loadHistory() {
  const saved = localStorage.getItem("paludoroHistory");
  if (saved) {
    try {
      conversationHistory = JSON.parse(saved);
    } catch (e) {
      conversationHistory = [];
    }
  } else {
    conversationHistory = [];
  }
  renderHistory();
  rebuildFiles();
}

async function sendMessage() {
  const text = userInput.value.trim();
  if (!text || isThinking) return;

  userInput.value = "";
  conversationHistory.push({ role: "user", content: text, raw_content: text });
  saveHistory();
  renderHistory();
  setThinking(true);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history: conversationHistory }),
    });

    const data = await res.json();
    if (data.error) {
      alert(data.error);
      // Remove the user message since it failed
      conversationHistory.pop();
      saveHistory();
      renderHistory();
      return;
    }

    conversationHistory.push(data.message);
    saveHistory();
    renderHistory();
    refreshFilesUI(data.files || {});
  } catch (e) {
    alert("Connection error.");
    conversationHistory.pop();
    saveHistory();
    renderHistory();
  } finally {
    setThinking(false);
    rerollBtn.disabled = false;
    deleteBtn.disabled = false;
    userInput.focus();
  }
}

async function rerollLast() {
  if (isThinking) return;
  if (conversationHistory.length === 0) return;

  if (
    conversationHistory[conversationHistory.length - 1].role === "assistant"
  ) {
    conversationHistory.pop();
    saveHistory();
    renderHistory();
  }

  setThinking(true);
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history: conversationHistory }),
    });

    const data = await res.json();
    if (data.error) {
      alert(data.error);
      return;
    }

    conversationHistory.push(data.message);
    saveHistory();
    renderHistory();
    refreshFilesUI(data.files || {});
  } catch (e) {
    alert("Connection error.");
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
    conversationHistory[conversationHistory.length - 1].role === "assistant"
  ) {
    conversationHistory.pop();
  }
  if (
    conversationHistory.length > 0 &&
    conversationHistory[conversationHistory.length - 1].role === "user"
  ) {
    conversationHistory.pop();
  }

  saveHistory();
  renderHistory();
  await rebuildFiles();
  userInput.focus();
}

async function clearChat() {
  if (isThinking) return;
  if (!confirm("Are you sure you want to clear the entire session?")) return;

  conversationHistory = [];
  saveHistory();
  renderHistory();
  refreshFilesUI({});
  fileContent.style.display = "none";
  userInput.focus();
}

sendBtn.onclick = sendMessage;
clearBtn.onclick = clearChat;
rerollBtn.onclick = rerollLast;
deleteBtn.onclick = deleteLastTurn;

userInput.onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
};

// Initial Load
loadHistory();
