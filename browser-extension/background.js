const DEFAULTS = {
  baseUrl: "http://localhost:8080",
  apiKey: "",
  accessToken: "",
  spaceId: "default",
  defaultTags: []
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.get(DEFAULTS).then((settings) => {
    chrome.storage.sync.set(settings);
  });

  chrome.contextMenus.removeAll().then(() => chrome.contextMenus.create({
    id: "corenote-clip-selection",
    title: "保存选中内容到 CoreNote",
    contexts: ["selection"]
  }));
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "corenote-clip-selection" || !tab?.id) return;

  try {
    const page = await collectPage(tab.id);
    const settings = await chrome.storage.sync.get(DEFAULTS);
    const result = await saveToCoreNote({
      ...page,
      selection: info.selectionText || page.selection || "",
      ...settings
    });
    await rememberCapture(result, page, "selection");
    notifyTab(tab.id, result.success ? "已保存到 CoreNote" : `保存失败：${result.error || "未知错误"}`);
  } catch (error) {
    notifyTab(tab.id, `采集失败：${error.message}`);
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "CAPTURE_CURRENT_TAB") return undefined;

  (async () => {
    const tab = message.tab || (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
    if (!tab?.id) throw new Error("无法读取当前页面");
    const page = await collectPage(tab.id);
    if (message.mode === "preview") {
      sendResponse({ success: true, page });
      return;
    }
    const settings = await chrome.storage.sync.get(DEFAULTS);
    const result = await saveToCoreNote({
      ...page,
      ...settings,
      title: message.title || page.title || tab.title || "",
      selection: message.mode === "selection" ? (message.selection || page.selection || "") : "",
      tags: message.tags || settings.defaultTags || [],
      spaceId: message.spaceId || settings.spaceId || "default"
    });
    await rememberCapture(result, page, message.mode || "page");
    sendResponse({ ...result, page });
  })().catch((error) => sendResponse({ success: false, error: error.message }));

  return true;
});

async function collectPage(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: async () => {
      await new Promise((resolve) => setTimeout(resolve, 700));
      const cleanText = (value) => (value || "")
        .replace(/\u00a0/g, " ")
        .replace(/[ \t]+\n/g, "\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
      const fetchWithTimeout = async (url, options = {}, timeoutMs = 6000) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        try { return await fetch(url, { ...options, signal: controller.signal }); }
        finally { clearTimeout(timer); }
      };
      const normalizeLine = (value) => cleanText(value)
        .replace(/^\s*#{1,6}\s*/, "")
        .replace(/[：:]\s*$/, "")
        .replace(/[\s|·•]+/g, "")
        .toLowerCase();
      const isEditorChrome = (node) => {
        if (!node || node.nodeType !== 1) return false;
        const role = (node.getAttribute("role") || "").toLowerCase();
        const idClass = `${node.id || ""} ${typeof node.className === "string" ? node.className : ""}`.toLowerCase();
        return role === "toolbar"
          || node.getAttribute("contenteditable") === "true"
          || /(editor-toolbar|toolbar|tool-bar|format-bar|rich-toolbar|wysiwyg|formula-toolbar|math-toolbar|symbol-panel|emoji-panel|insert-menu|comment-panel|comment-editor|article-editor)/i.test(idClass);
      };
      const pageTitleHint = cleanText(
        document.querySelector("meta[property='og:title'], meta[name='twitter:title'], h1")?.content
        || document.querySelector("meta[property='og:title'], meta[name='twitter:title']")?.getAttribute("content")
        || document.querySelector("h1")?.innerText
        || document.title
        || ""
      );
      const trimArticleBoundary = (value) => {
        const source = cleanText(value);
        if (!source) return source;
        const lines = source.split(/\n+/).map((line) => line.trim()).filter(Boolean);
        if (lines.length < 6) return source;
        const titleKey = normalizeLine(pageTitleHint);
        const titleMatches = (line) => {
          const key = normalizeLine(line);
          if (!key || !titleKey) return false;
          return key === titleKey
            || (key.length >= 12 && titleKey.includes(key))
            || (titleKey.length >= 12 && key.includes(titleKey));
        };
        let start = 0;
        // Prefer the semantic heading that matches the page title. This drops
        // site chrome such as author recommendations and account navigation
        // that appears before the actual article on community portals.
        if (titleKey.length >= 8) {
          const headingIndexes = lines.map((line, index) => ({ line, index }))
            .filter(({ line }) => /^#{1,6}\s+/.test(line) && titleMatches(line))
            .map(({ index }) => index);
          const exactIndexes = lines.map((line, index) => ({ line, index }))
            .filter(({ line }) => titleMatches(line))
            .map(({ index }) => index);
          // A semantic heading is normally the article start. Use the first
          // matching heading; only fall back to the last plain-text match
          // because portals often repeat the title once in their chrome.
          if (headingIndexes.length) start = headingIndexes[0];
          else if (exactIndexes.length) start = exactIndexes[exactIndexes.length - 1];
        }
        // Authoritative Markdown/Draft.js content may legitimately begin with
        // a section heading (for example “一、前言”) rather than repeating the
        // page title. Only hunt for a later heading when the first line is
        // plain portal chrome.
        if (start === 0 && !/^#{1,6}\s+\S/.test(lines[0])) {
          const firstArticleHeading = lines.findIndex((line, index) => index > 0 && /^#{1,3}\s+\S/.test(line) && !isNoise(line));
          if (firstArticleHeading > 0) start = firstArticleHeading;
        }
        const strongEnd = /^(原创声明|版权声明|加入讨论|发表评论|相关产品与服务|相关课程|问题归档|专栏文章归档|快讯文章归档|开发者手册归档|开发者手册section归档|关于社区规范|免责声明|联系我们|友情链接|返回腾讯云官网|期待你的精彩评论|\d+条评论)(?:[：:]|$)/i;
        const weakEnd = /^(?:作者相关精选|猜你喜欢|相关推荐|相关阅读|热门推荐|为你精选更多内容|大家还在看|更多推荐)$/i;
        let end = lines.length;
        // Strong footer markers are safe to apply as soon as the article has
        // started; this catches short posts whose noise begins before 30% of
        // the captured lines.
        for (let index = start + 4; index < lines.length; index += 1) {
          const line = normalizeLine(lines[index]);
          if (strongEnd.test(line)) { end = index; break; }
        }
        if (end === lines.length) {
          for (let index = Math.max(start + 4, Math.floor(lines.length * 0.3)); index < lines.length; index += 1) {
            const line = normalizeLine(lines[index]);
            if (weakEnd.test(line)) { end = index; break; }
          }
        }
        const trimmed = lines.slice(start, end);
        // The CoreNote viewer already renders the resource title above
        // “页面信息/页面正文”. Do not repeat the same page title as the first
        // Markdown heading inside the captured body, but keep all subsequent
        // article headings intact.
        if (trimmed.length && /^#{1,6}\s+/.test(trimmed[0]) && titleMatches(trimmed[0])) {
          trimmed.shift();
        }
        return cleanText(trimmed.join("\n\n"));
      };
      const isNoise = (value) => {
        const text = cleanText(value);
        if (!text || text.length < 2) return true;
        const compact = text.replace(/[\s·•|]+/g, "").toLowerCase();
        const editorTerms = ["h1", "h2", "h3", "h4", "h5", "h6", "有序列表", "无序列表", "行内代码", "代码块", "行内公式", "公式", "导入md", "导入markdown", "常用符号", "二元计算符", "二元关系符", "箭头符号", "根式", "上下标", "极限", "对数指数", "三角函数", "双曲函数", "binaryoperations", "binaryrelations", "trigonometricfunction", "hyperbolicfunction"];
        const editorTermHits = editorTerms.reduce((count, term) => count + (compact.includes(term) ? 1 : 0), 0);
        return /为你精选更多内容|猜你喜欢|相关推荐|相关阅读|热门推荐|广告|赞助商|下载客户端|打开App|登录后查看|扫码关注|微信扫一扫|使用完整服务|对关注你的人展示公众号身份|选择留言身份|暂无留言|已无更多数据|写留言|确认提交投诉|补充投诉原因|在公众号小说中沉浸阅读|在小说阅读器读本章|copyright|privacy policy|terms of use/i.test(text)
          || /^[;；、，,。.\s]+$/.test(text)
          || /^\|(?:\s*\|)+\s*$/.test(text)
          || /^\|?\s*:?-{2,}:?(?:\s*\|\s*:?-{2,}:?)+\s*\|?$/.test(text)
          || /^(?:H[1-6]\s*){2,}$/i.test(text)
          || /^(上一页|下一页|返回顶部|分享|收藏|点赞|评论|首页|学习|活动|专区|圈层|工具|文档|建议反馈|控制台|个人中心|职业认证|通知设置|消息中心|退出登录|关注作者|原创|作者相关精选|作者相关|社区首页|专栏|举报|发表|热度|最新|图片|相关产品与服务|加入讨论|返回腾讯云官网|期待你的精彩评论|用户\d+|腾讯云TVP|LV\.\d+|CTO|获赞|排名|交个朋友|字数统计|导入md|导入markdown|有序列表|无序列表|行内代码|代码块|行内公式|公式|常用符号|代数|矩阵|几何|三角|颜色|字号|文本颜色|背景颜色|二元计算符(?:\s+符)?|二元关系符|箭头符号|其他符号|根式|上下标|极限|对数指数|三角函数|双曲函数|Binary operations|Binary relations|Sub(?:&|and)Super|Trigonometric function|Hyperbolic function|H[1-6])$/i.test(text)
          || (/^(?:Binary operations|Binary relations|Arrows|Others|Radicals|Sub(?:&|and)Super|Limits|Trigonometric function|Hyperbolic function|公式|常用符号|代数|矩阵|几何|三角|颜色|字号|文本颜色|背景颜色)(?:\s+(?:Binary operations|Binary relations|Arrows|Others|Radicals|Sub(?:&|and)Super|Limits|Trigonometric function|Hyperbolic function|公式|常用符号|代数|矩阵|几何|三角|颜色|字号|文本颜色|背景颜色))*$/i.test(text))
          || (editorTermHits >= 3 && text.length <= 260)
          || /^发布于\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}/i.test(text)
          || /^\d{1,6}$/.test(text);
      };
      const stripFormattingArtifacts = (value) => {
        const source = cleanText(value);
        if (!source) return source;
        const lines = source.split(/\n+/).map((line) => line.trim()).filter(Boolean);
        const output = [];
        for (let index = 0; index < lines.length;) {
          // Empty tables are emitted by rich-text editors as a run of rows
          // containing only pipes and separator dashes. Skip the whole run,
          // while preserving tables that contain at least one real cell.
          if (/^\|/.test(lines[index]) && /\|\s*$/.test(lines[index])) {
            const table = [];
            while (index < lines.length && /^\|/.test(lines[index]) && /\|\s*$/.test(lines[index])) {
              table.push(lines[index]);
              index += 1;
            }
            const cells = table.flatMap((row) => row.replace(/^\|\s*|\s*\|$/g, "").split("|").map((cell) => cell.trim()));
            const hasContent = cells.some((cell) => cell && !/^:?-{2,}:?$/.test(cell));
            if (hasContent) output.push(...table);
            continue;
          }
          output.push(lines[index]);
          index += 1;
        }
        // Repeated labels are common in portal chrome (e.g. author name and
        // role rendered twice). Collapse only adjacent, short duplicates so
        // genuine repeated prose is not affected.
        const deduped = [];
        output.forEach((line) => {
          const previous = deduped[deduped.length - 1];
          if (previous && line.length <= 80 && previous.length <= 80 && normalizeLine(previous) === normalizeLine(line)) return;
          deduped.push(line);
        });
        return cleanText(deduped.join("\n\n"));
      };
      const wechatRoot = document.querySelector("#js_content, .rich_media_content, .wx_rich_media_content");
      // Some documentation platforms expose a canonical Markdown endpoint.
      // Restrict this optimization to same-origin URLs so page credentials
      // are never sent to an unrelated host.
      let embeddedMarkdown = "";
      const embeddedImageSources = [];
      // Next.js sites (including Tencent Cloud Developer Community) often
      // render an empty article container and keep the real Markdown in
      // `script#__NEXT_DATA__`. Extract the best article-like `content` value
      // before falling back to the visible DOM.
      try {
        const nextData = document.querySelector("script#__NEXT_DATA__");
        if (nextData?.textContent) {
          const parsed = JSON.parse(nextData.textContent);
          const candidates = [];
          const tiptapToMarkdown = (documentNode) => {
            const renderInline = (nodes) => (Array.isArray(nodes) ? nodes : []).map((node) => {
              if (!node) return "";
              if (node.type === "hardBreak") return "\n";
              if (node.type === "text") {
                let textValue = String(node.text || "");
                (node.marks || []).forEach((mark) => {
                  const type = String(mark?.type || "").toLowerCase();
                  if (type === "bold" || type === "strong") textValue = `**${textValue}**`;
                  else if (type === "italic" || type === "em") textValue = `*${textValue}*`;
                  else if (type === "strike" || type === "strikethrough") textValue = `~~${textValue}~~`;
                  else if (type === "code") textValue = `\`${textValue}\``;
                  else if (type === "link" && mark.attrs?.href) textValue = `[${textValue}](${mark.attrs.href})`;
                });
                return textValue;
              }
              return renderInline(node.content);
            }).join("");
            const renderNode = (node, context = {}) => {
              if (!node) return "";
              const type = String(node.type || "").toLowerCase();
              if (type === "text") return renderInline([node]);
              if (type === "image") {
                const src = String(node.attrs?.src || "");
                if (/^https?:\/\//i.test(src)) {
                  embeddedImageSources.push({ url: src, alt: String(node.attrs?.alt || "图片") });
                  return `![${node.attrs?.alt || "图片"}](${src})`;
                }
                return "";
              }
              if (type === "paragraph") return renderInline(node.content).trim();
              if (type === "heading") {
                const level = Math.min(6, Math.max(1, Number(node.attrs?.level || 1)));
                return `${"#".repeat(level)} ${renderInline(node.content).trim()}`.trim();
              }
              if (type === "blockquote") return (node.content || []).map((child) => renderNode(child)).filter(Boolean).join("\n\n").split("\n").map((line) => `> ${line}`).join("\n");
              if (type === "codeblock" || type === "code-block") {
                const language = node.attrs?.language ? String(node.attrs.language) : "";
                const code = renderInline(node.content).replace(/\n{3,}/g, "\n\n");
                return `\`\`\`${language}\n${code}\n\`\`\``;
              }
              if (type === "bulletlist" || type === "orderedlist") {
                const ordered = type === "orderedlist";
                const start = Number(node.attrs?.start || 1);
                return (node.content || []).map((child, index) => {
                  const rendered = renderNode(child, { listItem: true });
                  const lines = rendered.split("\n");
                  const marker = ordered ? `${start + index}. ` : "- ";
                  return marker + lines.shift() + (lines.length ? `\n${lines.map((line) => `  ${line}`).join("\n")}` : "");
                }).join("\n");
              }
              if (type === "listitem") return (node.content || []).map((child) => renderNode(child)).filter(Boolean).join("\n\n");
              if (type === "table") {
                const rows = (node.content || []).filter((child) => String(child?.type || "").toLowerCase() === "tablerow").map((row) => (row.content || []).map((cell) => renderInline(cell.content?.flatMap((part) => part.content || [part]) || []).replace(/\|/g, "\\|").replace(/\n/g, " ")));
                if (!rows.length) return "";
                const width = Math.max(...rows.map((row) => row.length));
                const normalized = rows.map((row) => Array.from({ length: width }, (_, index) => row[index] || ""));
                return [
                  `| ${normalized[0].join(" | ")} |`,
                  `| ${normalized[0].map(() => "---").join(" | ")} |`,
                  ...normalized.slice(1).map((row) => `| ${row.join(" | ")} |`)
                ].join("\n");
              }
              if (type === "tablerow") return (node.content || []).map((child) => renderNode(child)).join(" | ");
              if (type === "tablecell" || type === "tableheader") return renderInline((node.content || []).flatMap((part) => part.content || [part])).trim();
              if (type === "horizontalrule") return "---";
              return (node.content || []).map((child) => renderNode(child)).filter(Boolean).join("\n\n");
            };
            return cleanText(renderNode(documentNode));
          };
          const parseStructuredDocument = (value) => {
            if (!value || typeof value !== "string") return null;
            const source = value.trim();
            if (!source.startsWith("{") && !source.startsWith("[")) return null;
            try {
              const parsedValue = JSON.parse(source);
              return parsedValue && parsedValue.type === "doc" && Array.isArray(parsedValue.content) ? parsedValue : null;
            } catch (_) { return null; }
          };
          const draftToMarkdown = (rawContent) => {
            const blocks = Array.isArray(rawContent?.blocks) ? rawContent.blocks : [];
            const entityMap = rawContent?.entityMap || {};
            const decode = (value) => {
              const raw = String(value || "").replace(/\u200b/g, "").trim();
              if (!raw) return "";
              try { return decodeURIComponent(raw); } catch (_) { return raw; }
            };
            const lines = [];
            blocks.forEach((block) => {
              const type = String(block?.type || "unstyled").toLowerCase();
              if (type === "atomic") {
                (block?.entityRanges || []).forEach((range) => {
                  const entity = entityMap[String(range.key)] || entityMap[range.key];
                  const imageUrl = entity?.data?.imageUrl || entity?.data?.src || "";
                  if (/^https?:\/\//i.test(imageUrl)) {
                    embeddedImageSources.push({ url: imageUrl, alt: "图片" });
                    lines.push(`![图片](${imageUrl})`);
                  }
                });
                return;
              }
              const textValue = decode(block?.data?.text || block?.text || "");
              if (!textValue) return;
              const indent = "  ".repeat(Math.max(0, Number(block?.depth || 0)));
              if (type === "header-one") lines.push(`${indent}# ${textValue}`);
              else if (type === "header-two") lines.push(`${indent}## ${textValue}`);
              else if (type === "header-three") lines.push(`${indent}### ${textValue}`);
              else if (type === "unordered-list-item") lines.push(`${indent}- ${textValue}`);
              else if (type === "ordered-list-item") lines.push(`${indent}1. ${textValue}`);
              else if (type === "blockquote") lines.push(`${indent}> ${textValue}`);
              else if (type === "code-block") lines.push(`${indent}\`\`\`\n${textValue}\n\`\`\``);
              else lines.push(`${indent}${textValue}`);
            });
            return cleanText(lines.join("\n\n"));
          };
          const visit = (value, key = "", path = "") => {
            if (value && typeof value === "object" && value.type === "doc" && Array.isArray(value.content)) {
              const tiptapMarkdown = tiptapToMarkdown(value);
              if (tiptapMarkdown.length >= 240) candidates.push({ value: tiptapMarkdown, score: 10000 + Math.min(tiptapMarkdown.length, 30000) / 20 });
              return;
            }
            if (value && typeof value === "object" && Array.isArray(value.blocks) && value.entityMap) {
              const draftMarkdown = draftToMarkdown(value);
              if (draftMarkdown.length >= 240) {
                candidates.push({ value: draftMarkdown, score: 9000 + Math.min(draftMarkdown.length, 30000) / 20 });
              }
              return;
            }
            if (typeof value === "string") {
              const structuredDocument = parseStructuredDocument(value);
              if (structuredDocument) {
                const tiptapMarkdown = tiptapToMarkdown(structuredDocument);
                if (tiptapMarkdown.length >= 240) candidates.push({ value: tiptapMarkdown, score: 10000 + Math.min(tiptapMarkdown.length, 30000) / 20 });
                return;
              }
              const normalized = value.replace(/\\n/g, "\n").trim();
              if (normalized.length >= 240 && (key === "content" || /(^|\n)#{1,6}\s+\S/.test(normalized))) {
                const titleScore = normalizeLine(pageTitleHint).length >= 8 && normalizeLine(normalized).includes(normalizeLine(pageTitleHint)) ? 3000 : 0;
                const articlePathScore = /article(?:info|data)|articlecontent/i.test(path) ? 5000 : 0;
                const markdownScore = ((normalized.match(/(^|\n)#{1,6}\s+/g) || []).length * 180)
                  + ((normalized.match(/!\[[^\]]*\]\(/g) || []).length * 30);
                candidates.push({ value: normalized, score: articlePathScore + titleScore + markdownScore + Math.min(normalized.length, 30000) / 20 });
              }
              return;
            }
            if (!value || typeof value !== "object") return;
            Object.entries(value).forEach(([childKey, childValue]) => visit(childValue, childKey, path ? `${path}.${childKey}` : childKey));
          };
          visit(parsed);
          candidates.sort((a, b) => b.score - a.score);
          embeddedMarkdown = candidates[0]?.value || "";
        }
      } catch (_) {}
      let alternateMarkdown = "";
      try {
        const markdownLink = document.querySelector("link[rel='alternate'][type='text/markdown']");
        if (markdownLink?.href && new URL(markdownLink.href, location.href).origin === location.origin) {
          const markdownResponse = await fetchWithTimeout(markdownLink.href, {}, 6000);
          if (markdownResponse.ok) {
            const candidate = await markdownResponse.text();
            if (candidate && candidate.replace(/\s+/g, " ").trim().length >= 240) {
              // Resolve relative image links before the server localizes the
              // assets; ordinary hyperlinks are intentionally left untouched.
              alternateMarkdown = candidate.trim().replace(/(!\[[^\]]*\]\()([^\s)]+)(\))/g, (all, prefix, src, suffix) => {
                try { return prefix + new URL(src, location.href).href + suffix; } catch (_) { return all; }
              });
            }
          }
        }
      } catch (_) {}
      const candidates = Array.from(document.querySelectorAll(
        "article, main, [role='main'], [itemprop='articleBody'], #article, #article-content, .prose, .markdown-body, .markdown, .doc-content, .docs-content, .article-detail, .article__content, .article-body, .article-content, .post-content, .post, .content, .mod-article-content, .mod-content__markdown, .cdc-article-editor__container, .rno-markdown3"
      ));
      const root = wechatRoot || candidates
        .map((node) => {
          const text = cleanText(node.innerText || node.textContent);
          const paragraphs = Array.from(node.querySelectorAll("p")).map((item) => cleanText(item.innerText || item.textContent)).filter((item) => !isNoise(item));
          const linkText = Array.from(node.querySelectorAll("a")).map((item) => cleanText(item.innerText || item.textContent)).join(" ");
          const linkRatio = text.length ? Math.min(1, linkText.length / text.length) : 1;
          const semantic = /article|main|prose|markdown|doc-content|docs-content|post-content/i.test(`${node.id} ${node.className || ""}`) ? 1200 : 0;
          return { node, text, score: semantic + paragraphs.join("\n").length * 2 + text.length + paragraphs.length * 180 - linkRatio * 650 };
        })
        .filter((item) => item.text.length > 120 && item.score > 300)
        .sort((a, b) => b.score - a.score)[0]?.node || document.body;
      const clone = root.cloneNode(true);
      // Remove toolbar/editor/comment composer nodes before walking leaf text.
      // These controls are frequently nested inside the article container on
      // community sites and otherwise appear as fake headings and paragraphs.
      clone.querySelectorAll("*").forEach((node) => { if (isEditorChrome(node)) node.remove(); });
      clone.querySelectorAll("script, style, noscript, nav, footer, header, form, iframe, svg, aside, button, input, select, textarea, [role='button'], [role='navigation'], [role='contentinfo'], [aria-label*='推荐'], [class*='recommend'], [id*='recommend'], [class*='related'], [id*='related'], [class*='advert'], [id*='advert'], [class*='banner'], [id*='banner'], [class*='sidebar'], [id*='sidebar'], [class*='side-menu'], [class*='toc'], [id*='toc'], [class*='breadcrumb'], [id*='breadcrumb'], [class*='comment-list'], [id*='comment-list'], [class*='comment-panel'], [id*='comment-panel'], [class*='comment-editor'], [id*='comment-editor'], [class*='comment-form'], [id*='comment-form'], [class*='comment-area'], [id*='comment-area'], [class*='comment-box'], [id*='comment-box'], [class~='comments'], #comments, [class*='article-operate'], [class*='article-action'], [class*='author-card'], [class*='author-info'], .rich_media_tool, #js_pc_qr_code, .qr_code_pc, .reward_area, .weapp_display_element").forEach((node) => node.remove());
      const blockNodes = Array.from(clone.querySelectorAll("h1, h2, h3, h4, h5, h6, p, li, blockquote, pre"));
      const blocks = blockNodes.map((node) => {
        const text = cleanText(node.innerText || node.textContent);
        if (isNoise(text)) return "";
        if (/^H[1-6]$/.test(node.tagName)) return `${"#".repeat(Number(node.tagName.slice(1)))} ${text}`;
        if (node.tagName === "LI") return `- ${text}`;
        if (node.tagName === "BLOCKQUOTE") return `> ${text}`;
        return text;
      }).filter(Boolean);
      const inlineText = (node) => cleanText(node && (node.innerText || node.textContent) || "");
      const serializeTable = (table) => {
        const grid = [];
        Array.from(table.querySelectorAll("tr")).forEach((row, rowIndex) => {
          grid[rowIndex] ||= [];
          let column = 0;
          Array.from(row.children).forEach((cell) => {
            while (grid[rowIndex][column] !== undefined) column += 1;
            const cellText = inlineText(cell).replace(/\|/g, "\\|").replace(/\n/g, " ");
            const colspan = Math.max(1, Number(cell.getAttribute("colspan") || 1));
            const rowspan = Math.max(1, Number(cell.getAttribute("rowspan") || 1));
            for (let y = 0; y < rowspan; y++) {
              grid[rowIndex + y] ||= [];
              for (let x = 0; x < colspan; x++) grid[rowIndex + y][column + x] = cellText;
            }
            column += colspan;
          });
        });
        // Rich editors sometimes render their toolbar as an empty Markdown
        // table. Do not leak a block of `| | |` separators into the article.
        if (!grid.length || !grid.some((row) => row.some((cell) => cleanText(cell)))) return "";
        const width = Math.max(...grid.map((row) => row.length));
        const normalized = grid.map((row) => Array.from({ length: width }, (_, i) => row[i] || ""));
        return ["| " + normalized[0].join(" | ") + " |", "| " + normalized[0].map(() => "---").join(" | ") + " |"]
          .concat(normalized.slice(1).map((row) => "| " + row.join(" | ") + " |")).join("\n");
      };
      const serialize = (node) => {
        if (!node || node.nodeType !== 1) return "";
        const tag = node.tagName;
        if (isEditorChrome(node)) return "";
        if (["SCRIPT","STYLE","NOSCRIPT","NAV","FOOTER","HEADER","FORM","IFRAME","SVG","ASIDE"].includes(tag)) return "";
        if (tag === "TABLE") return serializeTable(node);
        if (tag === "IMG") {
          const src = node.currentSrc || node.src || node.getAttribute("data-src") || node.getAttribute("data-original") || "";
          const alt = cleanText(node.alt || node.title || "图片");
          if (!/^https?:\/\//i.test(src) || /logo|avatar|icon|emoji|sprite|banner|ad[sx]?|recommend|qrcode|qr_code/i.test(src) || /二维码|头像|划线引导|关注|小程序/i.test(alt)) return "";
          return "![" + alt + "](" + src + ")";
        }
        if (/^H[1-6]$/.test(tag)) return isNoise(inlineText(node)) ? "" : "#".repeat(Number(tag.slice(1))) + " " + inlineText(node);
        if (tag === "LI") return isNoise(inlineText(node)) ? "" : "- " + inlineText(node);
        if (tag === "BLOCKQUOTE") return isNoise(inlineText(node)) ? "" : "> " + inlineText(node);
        if (["P","PRE","FIGCAPTION","DT","DD"].includes(tag)) {
          if (node.querySelector && node.querySelector("img")) {
            const parts = Array.from(node.childNodes).map((child) => {
              if (child.nodeType === 3) return cleanText(child.textContent || "");
              return serialize(child);
            }).filter(Boolean);
            return parts.join(" ").trim();
          }
          return isNoise(inlineText(node)) ? "" : inlineText(node);
        }
        const children = Array.from(node.children).map(serialize).filter(Boolean);
        return children.join("\n\n") || (node.children.length ? "" : (isNoise(inlineText(node)) ? "" : inlineText(node)));
      };
      const structuredBlocks = Array.from(clone.children).map(serialize).filter(Boolean);
      const domText = cleanText(structuredBlocks.length >= 1 ? structuredBlocks.join("\n\n") : (blocks.length >= 3 ? blocks.join("\n\n") : (clone.innerText || clone.textContent || "")));
      const preferredMarkdown = embeddedMarkdown.length >= 240 ? embeddedMarkdown : alternateMarkdown;
      const text = trimArticleBoundary(stripFormattingArtifacts(preferredMarkdown.length >= 240 ? preferredMarkdown : domText));
      const bodyClone = document.body ? document.body.cloneNode(true) : null;
      bodyClone?.querySelectorAll("script, style, noscript, nav, footer, header, form, iframe, svg, aside, button, input, select, textarea, [role='button'], [role='navigation'], [role='contentinfo'], [aria-label*='推荐'], [class*='recommend'], [id*='recommend'], [class*='related'], [id*='related'], [class*='advert'], [id*='advert'], [class*='banner'], [id*='banner'], [class*='sidebar'], [id*='sidebar'], [class*='side-menu'], [id*='side-menu'], [class*='toc'], [id*='toc'], [class*='breadcrumb'], [id*='breadcrumb'], [class*='comment-list'], [id*='comment-list'], [class*='comment-panel'], [id*='comment-panel'], [class*='comment-editor'], [id*='comment-editor'], [class*='comment-form'], [id*='comment-form'], [class*='comment-area'], [id*='comment-area'], [class*='comment-box'], [id*='comment-box'], [class~='comments'], #comments, [class*='article-operate'], [class*='article-action'], [class*='author-card'], [class*='author-info']").forEach((node) => node.remove());
      bodyClone?.querySelectorAll("*").forEach((node) => { if (isEditorChrome(node)) node.remove(); });
      const bodyBlocks = Array.from(bodyClone?.querySelectorAll("h1, h2, h3, h4, h5, h6, p, li, blockquote, pre") || [])
        .map((node) => cleanText(node.innerText || node.textContent))
        .filter((value) => !isNoise(value) && value.length >= 8);
      const bodyText = trimArticleBoundary(stripFormattingArtifacts(bodyBlocks.join("\n\n")));
      const finalText = text.length >= 120 ? text : (bodyText.length > text.length ? bodyText : text);
      const imageRoot = root || document.body;
      const domImages = Array.from(imageRoot.querySelectorAll("img"))
        .map((img) => ({
          url: img.currentSrc || img.src || img.getAttribute("data-src") || img.getAttribute("data-original") || img.getAttribute("data-lazy-src") || (img.getAttribute("srcset") || "").split(",")[0].trim().split(" ")[0] || "",
          alt: cleanText(img.alt || img.getAttribute("title") || "")
        }))
        .filter((item) => /^https?:\/\//i.test(item.url) && !/logo|avatar|icon|emoji|sprite|banner|ad[sx]?|recommend|qrcode|qr_code/i.test(item.url) && !/二维码|头像|划线引导|关注|小程序/i.test(item.alt))
      const markdownImages = [
        ...embeddedImageSources,
        ...Array.from(preferredMarkdown.matchAll(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)[^)]*\)/g))
          .map((match) => ({ url: match[2], alt: cleanText(match[1] || "图片") }))
      ];
      const images = [...domImages, ...markdownImages]
        .filter((item, index, arr) => arr.findIndex((other) => other.url === item.url) === index)
        .slice(0, 12);
      const meta = (names) => {
        for (const name of names) {
          const node = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
          if (node?.content) return cleanText(node.content);
        }
        return "";
      };
      let jsonLd = {};
      document.querySelectorAll('script[type="application/ld+json"]').forEach((node) => {
        try {
          const parsed = JSON.parse(node.textContent || "{}");
          const items = Array.isArray(parsed) ? parsed : (parsed["@graph"] || [parsed]);
          const article = items.find((item) => /Article|NewsArticle|BlogPosting/i.test(item?.["@type"] || ""));
          if (article) jsonLd = article;
        } catch (_) {}
      });
      const jsonAuthor = typeof jsonLd.author === "object" ? jsonLd.author.name : jsonLd.author;
      const title = meta(["og:title", "twitter:title"]) || jsonLd.headline || document.querySelector("h1")?.innerText?.trim() || document.title || "";
      const author = meta(["author", "article:author"]) || (typeof jsonAuthor === "string" ? jsonAuthor : "");
      const publishedAt = meta(["article:published_time", "date", "datePublished"]) || jsonLd.datePublished || document.querySelector("time[datetime]")?.getAttribute("datetime") || "";
      const description = meta(["description", "og:description", "twitter:description"]) || jsonLd.description || "";
      const selection = window.getSelection ? window.getSelection().toString().trim() : "";
      const warnings = [];
      if (finalText.length < 300) warnings.push("正文较短，可能只采集到首屏或摘要");
      if (!wechatRoot && finalText.length < 800) warnings.push("未识别到明确正文容器");
      if (images.length === 0 && document.querySelectorAll("img").length > 0) warnings.push("页面存在图片但未提取到可下载图片");
      const captureMode = selection.length >= 20 ? "selection" : (warnings.length ? "partial" : "full");
      const qualityScore = Math.max(0, Math.min(1, (finalText.length / 3000) * 0.7 + (images.length ? 0.15 : 0.1) + (wechatRoot ? 0.15 : 0)));
      return {
        title,
        url: location.href,
        author_name: author,
        published_at: publishedAt,
        description,
        selection,
        content: finalText.slice(0, 300000),
        content_length: finalText.length,
        images,
        capture_mode: captureMode,
        quality_score: Number(qualityScore.toFixed(2)),
        warnings,
        capture_version: "edge-clipper-0.6.0"
      };
    }
  });
  if (!results?.[0]?.result) throw new Error("当前页面不允许采集");
  return results[0].result;
}

async function saveToCoreNote(payload) {
  const baseUrl = (payload.baseUrl || DEFAULTS.baseUrl).replace(/\/$/, "");
  const accessToken = await resolveAccessToken(payload);
  const body = {
    url: payload.url,
    title: payload.title || payload.url,
    selection: payload.selection || "",
    // Always persist the full extracted article. The selection is retained
    // separately as an optional excerpt; using it as the primary content
    // silently truncated pages whenever the user had text selected.
    content: payload.content || payload.selection || "",
    description: payload.description || "",
    author_name: payload.author_name || "",
    published_at: payload.published_at || "",
    source_type: "edge",
    captured_at: new Date().toISOString(),
    folder_prefix: "自动抓取",
    images: Array.isArray(payload.images) ? payload.images : [],
    tags: Array.isArray(payload.tags) ? payload.tags : [],
    space_id: payload.spaceId || "default",
    author: "edge-clipper"
  };

  const headers = { "Content-Type": "application/json" };
  if (payload.apiKey) headers["X-API-Key"] = payload.apiKey;
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60000);
  let response;
  try {
    response = await fetch(`${baseUrl}/api/v1/file-resources/capture`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: controller.signal
    });
  } finally {
    clearTimeout(timeout);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "object" ? data.detail.message : data.detail;
    if (response.status === 401 || response.status === 403) {
      throw new Error(`CoreNote 未授权（目标：${baseUrl}），请确认该地址的 CoreNote 页面已登录`);
    }
    throw new Error(detail || data.message || (`HTTP ${response.status}`));
  }
  return data;
}

async function resolveAccessToken(payload) {
  const baseUrl = (payload.baseUrl || DEFAULTS.baseUrl).replace(/\/$/, "");
  let tabs = [];
  try {
    const patterns = [`${baseUrl}/*`, "https://corenote.cloud/*", "http://localhost:8080/*", "http://127.0.0.1:8080/*"];
    tabs = await chrome.tabs.query({ url: [...new Set(patterns)] });
    tabs.sort((a, b) => Number((b.url || "").startsWith(baseUrl)) - Number((a.url || "").startsWith(baseUrl)));
  } catch (_) {}
  for (const tab of tabs) {
    if (!tab?.id) continue;
    try {
      const result = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => ({
          accessToken: sessionStorage.getItem("kh_access_token")
            || localStorage.getItem("kh_access_token") || ""
        })
      });
      const token = result?.[0]?.result?.accessToken || "";
      if (token) {
        await chrome.storage.sync.set({ accessToken: token });
        return token;
      }
    } catch (_) {}
  }
  if (payload.accessToken) return payload.accessToken;
  if (payload.apiKey) return "";
  throw new Error("请先打开并登录 CoreNote 页面，插件会自动同步登录态");
}

async function rememberCapture(result, page, mode) {
  const item = {
    id: result.bookmark_id || crypto.randomUUID(),
    title: page.title,
    url: page.url,
    mode,
    success: Boolean(result.success),
    path: result.path || "",
    createdAt: new Date().toISOString(),
    error: result.error || ""
  };
  const stored = await chrome.storage.local.get({ captures: [] });
  await chrome.storage.local.set({ captures: [item, ...stored.captures].slice(0, 30) });
}

function notifyTab(tabId, message) {
  chrome.scripting.executeScript({
    target: { tabId },
    func: (text) => {
      const existing = document.getElementById("corenote-clipper-toast");
      if (existing) existing.remove();
      const toast = document.createElement("div");
      toast.id = "corenote-clipper-toast";
      toast.textContent = text;
      Object.assign(toast.style, {
        position: "fixed", zIndex: "2147483647", right: "20px", bottom: "20px",
        padding: "10px 14px", borderRadius: "10px", background: "#1e293b",
        color: "#fff", font: "14px/1.4 system-ui, sans-serif",
        boxShadow: "0 8px 24px rgba(0,0,0,.2)"
      });
      document.documentElement.appendChild(toast);
      setTimeout(() => toast.remove(), 2600);
    },
    args: [message]
  }).catch(() => {});
}
