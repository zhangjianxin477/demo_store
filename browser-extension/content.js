// Reserved for future inline selection/highlight UI.
// The MVP uses activeTab + scripting.executeScript to avoid permanent page access.

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "CORENOTE_TOAST") return;
  const existing = document.getElementById("corenote-clipper-toast");
  if (existing) existing.remove();
  const toast = document.createElement("div");
  toast.id = "corenote-clipper-toast";
  toast.textContent = message.message || "CoreNote";
  Object.assign(toast.style, {
    position: "fixed",
    zIndex: "2147483647",
    right: "20px",
    bottom: "20px",
    padding: "10px 14px",
    borderRadius: "10px",
    background: "#1e293b",
    color: "#fff",
    font: "14px/1.4 system-ui, sans-serif",
    boxShadow: "0 8px 24px rgba(0,0,0,.2)"
  });
  document.documentElement.appendChild(toast);
  setTimeout(() => toast.remove(), 2600);
});




