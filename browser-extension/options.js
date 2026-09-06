const DEFAULTS = {
  baseUrl: "http://localhost:8080",
  apiKey: "",
  accessToken: "",
  spaceId: "default",
  defaultTags: []
};

document.addEventListener("DOMContentLoaded", async () => {
  const settings = await chrome.storage.sync.get(DEFAULTS);
  document.getElementById("base-url").value = settings.baseUrl;
  document.getElementById("api-key").value = settings.apiKey;
  document.getElementById("access-token").value = settings.accessToken;
  document.getElementById("space-id").value = settings.spaceId;
  document.getElementById("default-tags").value = settings.defaultTags.join(", ");
  document.getElementById("save").addEventListener("click", save);
});

async function save() {
  const baseUrl = document.getElementById("base-url").value.trim().replace(/\/$/, "");
  if (!/^https?:\/\//i.test(baseUrl)) return show("地址必须以 http:// 或 https:// 开头", true);
  await chrome.storage.sync.set({
    baseUrl,
    apiKey: document.getElementById("api-key").value.trim(),
    accessToken: document.getElementById("access-token").value.trim(),
    spaceId: document.getElementById("space-id").value.trim() || "default",
    defaultTags: document.getElementById("default-tags").value.split(",").map((tag) => tag.trim()).filter(Boolean)
  });
  show("设置已保存");
}

function show(message, error = false) {
  const status = document.getElementById("status");
  status.textContent = message;
  status.style.color = error ? "#dc2626" : "#16a34a";
}



