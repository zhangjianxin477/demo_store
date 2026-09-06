const $ = (id) => document.getElementById(id);
let currentTab = null;
let currentPage = null;

document.addEventListener("DOMContentLoaded", async () => {
  currentTab = (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
  if (!currentTab?.id) return setStatus("无法读取当前页面", true);
  try {
    const response = await chrome.runtime.sendMessage({ type: "CAPTURE_CURRENT_TAB", tab: currentTab, mode: "preview" });
    currentPage = response?.page;
    if (!currentPage) throw new Error(response?.error || "当前页面不允许采集");
    const meta = [currentPage.author_name && "作者：" + currentPage.author_name, currentPage.published_at && "发布时间：" + currentPage.published_at, "正文：" + (currentPage.content_length || (currentPage.content || "").length) + " 字", `图片：${(currentPage.images || []).length}`, currentPage.content?.includes("| ---") && "含表格", currentPage.capture_mode && "模式：" + currentPage.capture_mode].filter(Boolean).join(" · ");
    const warning = currentPage.warnings?.length ? `\n提示：${currentPage.warnings.join("；")}` : "";
    $("preview").textContent = (currentPage.title || currentPage.url) + "\n" + meta + "\n保存目录：自动抓取/YYYY-MM-DD/\n" + (currentPage.content || "").slice(0, 500) + warning;
  } catch (error) {
    $("preview").textContent = "当前页面暂不支持采集，请尝试普通网页。";
    setStatus(error.message, true);
  }

  $("save-page").addEventListener("click", () => capture("page"));
  $("save-selection").addEventListener("click", () => capture("selection"));
  $("open-options").addEventListener("click", () => chrome.runtime.openOptionsPage());
  $("open-app").addEventListener("click", async (event) => {
    event.preventDefault();
    const settings = await chrome.storage.sync.get({ baseUrl: "http://localhost:8080" });
    chrome.tabs.create({ url: settings.baseUrl });
  });
});

async function capture(mode) {
  setBusy(true);
  setStatus(mode === "page" ? "正在保存当前网页…" : "正在保存选中内容…");
  try {
    const selection = mode === "selection" ? (currentPage?.selection || "") : "";
    const tags = $("tags").value.split(",").map((tag) => tag.trim()).filter(Boolean);
    const result = await chrome.runtime.sendMessage({
      type: "CAPTURE_CURRENT_TAB",
      tab: currentTab,
      mode,
      selection,
      tags
    });
    if (!result?.success) throw new Error(result?.error || "保存失败");
    setStatus(`保存成功：${result.path ? "自动抓取/" + result.path.split("/").slice(-2).join("/") : (result.message || "已保存")}`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  $("save-page").disabled = busy;
  $("save-selection").disabled = busy;
}

function setStatus(message, isError = false) {
  $("status").textContent = message;
  $("status").style.color = isError ? "#dc2626" : "#64748b";
}
