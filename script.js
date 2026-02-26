// --- 1. THEME ENGINE ---
function applyTheme() {
  const theme = localStorage.getItem("theme");
  if (theme === "dark") {
    document.body.classList.add("dark-theme");
  } else {
    document.body.classList.remove("dark-theme");
  }
}
applyTheme();

// --- 2. CHAT LOGIC (Persist during Session, Clear on Logout) ---
const userInput = document.getElementById("userInput");
const chatMessages = document.getElementById("chatMessages");
const sendBtn = document.getElementById("sendBtn");

if (userInput && chatMessages) {
  const API_URL = "http://127.0.0.1:5000/ask";

  window.addEventListener("DOMContentLoaded", () => {
    const sessionChats = JSON.parse(
      sessionStorage.getItem("current_session_chats") || "[]",
    );
    sessionChats.forEach((msg) => renderMessageToUI(msg.text, msg.sender));
    console.log("Session chat restored.");
  });

  async function handleSendMessage(e) {
    if (e) e.preventDefault();
    const text = userInput.value.trim();
    if (!text) return;

    appendAndSaveMessage(text, "user");
    userInput.value = "";

    const loadingDiv = document.createElement("div");
    loadingDiv.className = "message bot";
    loadingDiv.innerText = "Thinking...";
    chatMessages.appendChild(loadingDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    try {
      const userEmail = sessionStorage.getItem("user_id");

      const res = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          email: userEmail,
        }),
      });
      const data = await res.json();

      loadingDiv.remove();

      if (data.answer) {
        appendAndSaveMessage(data.answer, "bot");
      }
    } catch (err) {
      loadingDiv.innerText = "Error connecting to server.";
      console.error(err);
    }
  }

  sendBtn.onclick = handleSendMessage;
  userInput.onkeydown = (e) => {
    if (e.key === "Enter") handleSendMessage(e);
  };

  function renderMessageToUI(text, sender) {
    const msgDiv = document.createElement("div");
    msgDiv.className = `message ${sender}`;
    msgDiv.innerText = text;
    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function appendAndSaveMessage(text, sender) {
    renderMessageToUI(text, sender);
    const chatArray = JSON.parse(
      sessionStorage.getItem("current_session_chats") || "[]",
    );
    chatArray.push({ text, sender });
    sessionStorage.setItem("current_session_chats", JSON.stringify(chatArray));
  }
}

// --- 3. LOGOUT FUNCTIONALITY ---
function logoutUser() {
  sessionStorage.removeItem("current_session_chats");
  sessionStorage.removeItem("user_id");
  window.location.href = "login.html";
}

// --- 4. NAVIGATION ---
const setupBtn = (id, file) => {
  const btn = document.getElementById(id);
  if (btn)
    btn.onclick = (e) => {
      e.preventDefault();
      window.location.href = file;
    };
};

setupBtn("historyBtn", "history.html");
setupBtn("dashboardBtn", "dashboard.html");
setupBtn("aboutBtn", "about.html");
setupBtn("settingBtn", "setting.html");

function toggleSidebar() {
  const sidebar = document.getElementById("sidebar");
  if (window.innerWidth <= 768) {
    sidebar.classList.toggle("show");
  } else {
    document.body.classList.toggle("sidebar-open");
  }
}

// --- 4. Logout Functionality ---
function handleLogout() {
  Swal.fire({
    title: "Logout Account?",
    text: "Are you sure you want to end your session?",
    icon: "question",
    showCancelButton: true,
    confirmButtonColor: "#ff4757",
    cancelButtonColor: "#6a11cb",
    confirmButtonText:
      '<i class="fa-solid fa-right-from-bracket"></i> Yes, Logout',
    cancelButtonText: "Cancel",
    width: window.innerWidth < 480 ? "90%" : "400px",
  }).then((result) => {
    if (result.isConfirmed) {
      sessionStorage.clear();
      window.location.href = "login.html";
    }
  });
}
