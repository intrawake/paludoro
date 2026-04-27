const messagesDiv = document.getElementById("messages") as HTMLDivElement;
const userInput = document.getElementById("user-input") as HTMLTextAreaElement;
const sendBtn = document.getElementById("send-btn") as HTMLButtonElement;
const clearBtn = document.getElementById("clear-btn") as HTMLButtonElement;
const rerollBtn = document.getElementById("reroll-btn") as HTMLButtonElement;
const deleteBtn = document.getElementById("delete-btn") as HTMLButtonElement;
const fileList = document.getElementById("file-list") as HTMLDivElement;
const fileContent = document.getElementById("file-content") as HTMLDivElement;

let isThinking = false;

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

async function refreshFiles() {
  const res = await fetch("/api/files");
  const files = await res.json();
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
  }
}

function addMessage(role: string, content: string) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.innerHTML = `<strong>${role === "user" ? "You" : "Assistant"}:</strong> ${content}`;
  messagesDiv.appendChild(div);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

async function loadHistory() {
  const res = await fetch("/api/history");
  const history = await res.json();
  messagesDiv.innerHTML = "";
  history.forEach((m: any) => addMessage(m.role, m.content));

  // Enable/disable buttons based on history
  rerollBtn.disabled = history.length === 0 || isThinking;
  deleteBtn.disabled = history.length === 0 || isThinking;
}

async function sendMessage() {
  const text = userInput.value.trim();
  if (!text || isThinking) return;

  userInput.value = "";
  addMessage("user", text);
  setThinking(true);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });

    const data = await res.json();
    if (data.error) {
      alert(data.error);
      return;
    }
    addMessage("assistant", data.response);
    if (data.new_files && data.new_files.length > 0) {
      refreshFiles();
    }
  } finally {
    setThinking(false);
    rerollBtn.disabled = false;
    deleteBtn.disabled = false;
    // MUST FOCUS back on input
    userInput.focus();
  }
}

async function rerollLast() {
  if (isThinking) return;
  setThinking(true);
  try {
    const res = await fetch("/api/reroll", { method: "POST" });
    const data = await res.json();
    if (data.error) {
      alert(data.error);
    } else {
      await loadHistory();
      refreshFiles();
    }
  } finally {
    setThinking(false);
    userInput.focus();
  }
}

async function deleteLastTurn() {
  if (isThinking) return;
  if (!confirm("Delete the last user message and assistant response?")) return;

  await fetch("/api/delete_turn", { method: "POST" });
  await loadHistory();
  refreshFiles();
  userInput.focus();
}

async function clearChat() {
  if (isThinking) return;
  if (!confirm("Are you sure you want to clear the entire session?")) return;

  await fetch("/api/clear", { method: "POST" });
  messagesDiv.innerHTML = "";
  fileList.innerHTML = "";
  fileContent.style.display = "none";
  rerollBtn.disabled = true;
  deleteBtn.disabled = true;
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
refreshFiles();
loadHistory();
