const wikiState = {
    currentSpaceId: 'default',
    currentParentId: '',
    currentPage: null,
    spaces: [],
    tags: [],
    pageTree: [],
    versions: [],
    comments: [],
    searchResults: [],
    annotations: [],
};

async function wikiApi(method, path, body = null) {
    const apiPath = `/wiki${path}`;
    if (isGuestAccess() && isWriteApiRequest(method, apiPath)) {
        const error = new Error('游客模式仅支持阅读和检索');
        error.isHttpError = true;
        error.status = 403;
        throw error;
    }
    const opts = { method, headers: getAccessHeaders() };
    if (body && !(body instanceof FormData)) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    } else if (body instanceof FormData) {
        opts.body = body;
    }
    try {
        const res = await fetch(`${API_BASE}/wiki${path}`, opts);
        const data = await res.json();
        if (!res.ok) {
            const error = new Error(data.detail || '请求失败');
            error.isHttpError = true;
            error.status = res.status;
            throw error;
        }
        return data;
    } catch (err) {
        if (err.isHttpError) throw err;
        const localResult = await handleOfflineWiki(method, `/wiki${path}`, body);
        if (localResult !== undefined) return localResult;
        throw err;
    }
}

function initWikiPage() {
    loadWikiSpaces();
    loadWikiPageTree();
    loadWikiTags();
}

async function loadWikiSpaces() {
    try {
        const data = await wikiApi('GET', '/spaces');
        if (data.success) {
            wikiState.spaces = data.spaces || [];
            renderWikiSpaces();
        }
    } catch (e) {
        console.error('加载空间失败:', e);
    }
}

async function loadWikiTags() {
    try {
        const data = await wikiApi('GET', '/tags');
        if (data.success) {
            wikiState.tags = data.tags || [];
            renderWikiTags();
        }
    } catch (e) {
        console.error('加载标签失败:', e);
    }
}

async function loadWikiPageTree(spaceId) {
    const sid = spaceId || wikiState.currentSpaceId;
    try {
        const data = await wikiApi('GET', `/pages/tree/${sid}`);
        if (data.success) {
            wikiState.pageTree = data.tree || [];
            renderWikiPageTree();
        }
    } catch (e) {
        console.error('加载页面树失败:', e);
    }
}

async function loadWikiPage(pageId) {
    try {
        const data = await wikiApi('GET', `/pages/${pageId}`);
        if (data.success) {
            wikiState.currentPage = data.page;
            renderWikiPageContent();
            loadWikiPageVersions(pageId);
            loadWikiComments(pageId);
            loadAnnotations(pageId);
        }
    } catch (e) {
        showToast('加载页面失败: ' + e.message, 'error');
    }
}

async function createWikiPage() {
    const dialog = document.createElement('div');
    dialog.className = 'new-item-dialog';
    dialog.id = 'wiki-create-dialog';
    dialog.innerHTML = `
        <div class="dialog-box" style="width:500px">
            <div class="dialog-title">新建Wiki页面</div>
            <div style="display:flex;flex-direction:column;gap:12px">
                <input class="dialog-input" id="wiki-new-title" placeholder="页面标题 *" autofocus>
                <select class="dialog-input" id="wiki-new-type" style="padding:8px 12px">
                    <option value="markdown">Markdown</option>
                    <option value="richtext">富文本</option>
                </select>
                <input class="dialog-input" id="wiki-new-tags" placeholder="标签（逗号分隔）">
                <textarea class="dialog-input" id="wiki-new-content" placeholder="初始内容（可选）" rows="5" style="resize:vertical"></textarea>
            </div>
            <div class="dialog-actions" style="margin-top:16px">
                <button class="dialog-btn" onclick="document.getElementById('wiki-create-dialog').remove()">取消</button>
                <button class="dialog-btn primary" onclick="doCreateWikiPage()">创建</button>
            </div>
        </div>`;
    document.body.appendChild(dialog);
    document.getElementById('wiki-new-title').focus();
}

async function doCreateWikiPage() {
    const title = document.getElementById('wiki-new-title').value.trim();
    if (!title) { showToast('请输入页面标题', 'warning'); return; }
    const content = document.getElementById('wiki-new-content').value;
    const pageType = document.getElementById('wiki-new-type').value;
    const tagsStr = document.getElementById('wiki-new-tags').value.trim();
    const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(Boolean) : [];

    try {
        const data = await wikiApi('POST', '/pages/create', {
            title, content, page_type: pageType, tags,
            space_id: wikiState.currentSpaceId,
            parent_id: wikiState.currentParentId,
        });
        if (data.success) {
            showToast('页面创建成功', 'success');
            document.getElementById('wiki-create-dialog')?.remove();
            loadWikiPageTree();
            loadWikiPage(data.page.page_id);
        }
    } catch (e) {
        showToast('创建失败: ' + e.message, 'error');
    }
}

async function saveWikiPage() {
    if (!wikiState.currentPage) return;
    let content;
    if (typeof WikiEditor !== 'undefined' && WikiEditor.getContent) {
        content = WikiEditor.getContent();
    } else {
        const editor = document.getElementById('wiki-page-editor');
        if (!editor) return;
        content = editor.value;
    }

    try {
        const data = await wikiApi('PUT', `/pages/${wikiState.currentPage.page_id}`, {
            content: content,
            change_summary: '编辑保存',
        });
        if (data.success) {
            wikiState.currentPage = data.page;
            showToast('页面已保存', 'success');
        }
    } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
    }
}

async function deleteWikiPage(pageId) {
    if (!confirm('确定要删除此页面吗？')) return;
    try {
        const data = await wikiApi('DELETE', `/pages/${pageId}`);
        if (data.success) {
            showToast('页面已删除', 'success');
            wikiState.currentPage = null;
            loadWikiPageTree();
            renderWikiPageContent();
        }
    } catch (e) {
        showToast('删除失败: ' + e.message, 'error');
    }
}

async function loadWikiPageVersions(pageId) {
    try {
        const data = await wikiApi('GET', `/pages/${pageId}/versions`);
        if (data.success) {
            wikiState.versions = data.versions || [];
            renderWikiVersions();
        }
    } catch (e) {
        console.error('加载版本历史失败:', e);
    }
}

async function rollbackWikiPage(pageId, versionNumber) {
    if (!confirm(`确定要回滚到版本 ${versionNumber} 吗？`)) return;
    try {
        const data = await wikiApi('POST', `/pages/${pageId}/rollback`, {
            version_number: versionNumber,
        });
        if (data.success) {
            showToast('已回滚到版本 ' + versionNumber, 'success');
            loadWikiPage(pageId);
        }
    } catch (e) {
        showToast('回滚失败: ' + e.message, 'error');
    }
}

async function loadWikiComments(pageId) {
    try {
        const data = await wikiApi('GET', `/pages/${pageId}/comments`);
        if (data.success) {
            wikiState.comments = data.comments || [];
            renderWikiComments();
        }
    } catch (e) {
        console.error('加载评论失败:', e);
    }
}

async function addWikiComment() {
    if (!wikiState.currentPage) return;
    const input = document.getElementById('wiki-comment-input');
    if (!input || !input.value.trim()) return;

    try {
        const data = await wikiApi('POST', `/pages/${wikiState.currentPage.page_id}/comments/add`, {
            content: input.value.trim(),
            comment_type: 'comment',
        });
        if (data.success) {
            input.value = '';
            loadWikiComments(wikiState.currentPage.page_id);
            showToast('评论已添加', 'success');
        }
    } catch (e) {
        showToast('评论失败: ' + e.message, 'error');
    }
}

async function createWikiSpace() {
    const dialog = document.createElement('div');
    dialog.className = 'new-item-dialog';
    dialog.id = 'wiki-space-dialog';
    dialog.innerHTML = `
        <div class="dialog-box" style="width:400px">
            <div class="dialog-title">新建知识空间</div>
            <div style="display:flex;flex-direction:column;gap:12px">
                <input class="dialog-input" id="wiki-space-name" placeholder="空间名称 *" autofocus>
                <input class="dialog-input" id="wiki-space-desc" placeholder="描述（可选）">
                <select class="dialog-input" id="wiki-space-type" style="padding:8px 12px">
                    <option value="project">项目空间</option>
                    <option value="department">部门空间</option>
                    <option value="personal">私人空间</option>
                </select>
            </div>
            <div class="dialog-actions" style="margin-top:16px">
                <button class="dialog-btn" onclick="document.getElementById('wiki-space-dialog').remove()">取消</button>
                <button class="dialog-btn primary" onclick="doCreateWikiSpace()">创建</button>
            </div>
        </div>`;
    document.body.appendChild(dialog);
    document.getElementById('wiki-space-name').focus();
}

async function doCreateWikiSpace() {
    const name = document.getElementById('wiki-space-name').value.trim();
    if (!name) { showToast('请输入空间名称', 'warning'); return; }
    const description = document.getElementById('wiki-space-desc').value.trim();
    const spaceType = document.getElementById('wiki-space-type').value;

    try {
        const data = await wikiApi('POST', '/spaces/create', {
            name, description, space_type: spaceType,
        });
        if (data.success) {
            showToast('空间创建成功', 'success');
            document.getElementById('wiki-space-dialog')?.remove();
            loadWikiSpaces();
        }
    } catch (e) {
        showToast('创建失败: ' + e.message, 'error');
    }
}

async function createWikiTag() {
    const name = prompt('请输入标签名称：');
    if (!name || !name.trim()) return;
    try {
        const data = await wikiApi('POST', '/tags/create', {
            name: name.trim(), space_id: wikiState.currentSpaceId,
        });
        if (data.success) {
            showToast('标签创建成功', 'success');
            loadWikiTags();
        }
    } catch (e) {
        showToast('创建失败: ' + e.message, 'error');
    }
}

async function wikiSearch(query) {
    if (!query || !query.trim()) return;
    try {
        const data = await wikiApi('GET', `/pages/search/${encodeURIComponent(query.trim())}`);
        if (data.success) {
            wikiState.searchResults = data.pages || [];
            renderWikiSearchResults();
        }
    } catch (e) {
        showToast('搜索失败: ' + e.message, 'error');
    }
}

async function semanticSearch(query) {
    if (!query || !query.trim()) return;
    try {
        const data = await wikiApi('POST', '/search/semantic', {
            query: query.trim(), top_k: 10,
        });
        if (data.success) {
            wikiState.searchResults = data.results || [];
            renderWikiSearchResults();
        }
    } catch (e) {
        showToast('语义搜索失败: ' + e.message, 'error');
    }
}

async function fuzzySearch(query) {
    if (!query || !query.trim()) return;
    try {
        const data = await wikiApi('POST', '/search/fuzzy', {
            query: query.trim(), top_k: 10,
        });
        if (data.success) {
            wikiState.searchResults = data.results || [];
            renderWikiSearchResults();
        }
    } catch (e) {
        showToast('模糊搜索失败: ' + e.message, 'error');
    }
}

async function aiSummarizePage() {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    showToast('AI正在生成摘要...', 'info');
    try {
        const data = await wikiApi('POST', `/ai/summarize/${wikiState.currentPage.page_id}`);
        if (data.success) {
            const resultDiv = document.getElementById('wiki-ai-result');
            if (resultDiv) {
                const d = data.data || {};
                resultDiv.innerHTML = `
                    <h4>文档摘要</h4>
                    <p>${escapeHtml(d.summary || '')}</p>
                    ${d.key_points?.length ? `<h4>核心要点</h4><ul>${d.key_points.map(p => `<li>${escapeHtml(p)}</li>`).join('')}</ul>` : ''}
                    ${d.keywords?.length ? `<h4>关键词</h4><p>${d.keywords.map(k => `<span class="wiki-tag">${escapeHtml(k)}</span>`).join(' ')}</p>` : ''}
                `;
                resultDiv.style.display = 'block';
            }
        }
    } catch (e) {
        showToast('AI摘要失败: ' + e.message, 'error');
    }
}

async function aiMindmapPage() {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    showToast('AI正在生成思维导图...', 'info');
    try {
        const data = await wikiApi('POST', `/ai/mindmap/${wikiState.currentPage.page_id}`);
        if (data.success) {
            const resultDiv = document.getElementById('wiki-ai-result');
            if (resultDiv) {
                resultDiv.innerHTML = `<h4>思维导图</h4><pre>${escapeHtml(JSON.stringify(data.data, null, 2))}</pre>`;
                resultDiv.style.display = 'block';
            }
        }
    } catch (e) {
        showToast('AI思维导图失败: ' + e.message, 'error');
    }
}

async function aiTranslatePage(targetLang) {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    showToast('AI正在翻译...', 'info');
    try {
        const data = await wikiApi('POST', `/ai/translate/${wikiState.currentPage.page_id}?target_lang=${targetLang}`);
        if (data.success) {
            const resultDiv = document.getElementById('wiki-ai-result');
            if (resultDiv) {
                resultDiv.innerHTML = `<h4>翻译结果 (${targetLang})</h4><div style="white-space:pre-wrap">${escapeHtml(data.translated_content || '')}</div>`;
                resultDiv.style.display = 'block';
            }
        }
    } catch (e) {
        showToast('AI翻译失败: ' + e.message, 'error');
    }
}

async function aiPolishPage() {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    showToast('AI正在润色...', 'info');
    try {
        const data = await wikiApi('POST', `/ai/polish/${wikiState.currentPage.page_id}`);
        if (data.success) {
            const resultDiv = document.getElementById('wiki-ai-result');
            if (resultDiv) {
                resultDiv.innerHTML = `<h4>润色结果</h4><div style="white-space:pre-wrap">${escapeHtml(data.polished_content || '')}</div>`;
                resultDiv.style.display = 'block';
            }
        }
    } catch (e) {
        showToast('AI润色失败: ' + e.message, 'error');
    }
}

async function aiCopilot(action = 'related') {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    const textarea = document.getElementById('wiki-editor-textarea');
    const selectedText = textarea && textarea.selectionStart !== textarea.selectionEnd
        ? textarea.value.slice(textarea.selectionStart, textarea.selectionEnd) : '';
    showToast(action === 'related' ? '正在查找相关笔记...' : 'Copilot 正在生成预览...', 'info');
    try {
        const data = await wikiApi('POST', '/ai/copilot', {
            page_id: wikiState.currentPage.page_id, action, selected_text: selectedText,
            space_id: wikiState.currentPage.space_id || wikiState.currentSpaceId || '', top_k: 5,
        });
        const resultDiv = document.getElementById('wiki-ai-result');
        if (!resultDiv) return;
        const evidence = (data.evidence || []).map((item, i) => `<li><b>${escapeHtml(item.title || `来源 ${i + 1}`)}</b><span>${escapeHtml(item.snippet || '')}</span></li>`).join('');
        const preview = data.preview || '';
        window._wikiCopilotPreview = preview;
        resultDiv.innerHTML = `<div class="wiki-copilot-result-head"><h4>Copilot · ${escapeHtml(action)}</h4><button class="icon-btn" onclick="this.closest('#wiki-ai-result').style.display='none'">×</button></div>${data.degraded ? '<div class="wiki-copilot-warning">模型当前不可用，以下为安全降级结果，未补充外部事实。</div>' : ''}${preview ? `<pre class="wiki-copilot-preview">${escapeHtml(preview)}</pre><div class="wiki-copilot-actions"><button class="toolbar-btn" onclick="WikiEditor.insertAtCursor(window._wikiCopilotPreview || '')">插入到光标</button><button class="toolbar-btn" onclick="saveCopilotPreview()">保存为新笔记</button></div>` : ''}<h5>相关证据（${(data.evidence || []).length}）</h5><ul class="wiki-copilot-evidence">${evidence || '<li>暂无相关笔记</li>'}</ul>`;
        resultDiv.style.display = 'block';
    } catch (e) { showToast('Copilot 调用失败: ' + e.message, 'error'); }
}

async function saveCopilotPreview() {
    const content = String(window._wikiCopilotPreview || '').trim();
    if (!content) { showToast('当前没有可保存的预览', 'warning'); return; }
    const title = prompt('请输入新笔记标题：', `${wikiState.currentPage?.title || 'Copilot 笔记'}（补充）`);
    if (!title || !title.trim()) return;
    try {
        const data = await wikiApi('POST', '/ai/copilot/save', {
            title: title.trim(), content, space_id: wikiState.currentPage?.space_id || 'default',
            tags: ['copilot'], source_page_id: wikiState.currentPage?.page_id || ''
        });
        if (data.success) { showToast('已保存为新笔记', 'success'); loadWikiPageTree(); }
    } catch (e) { showToast('保存 Copilot 笔记失败: ' + e.message, 'error'); }
}

async function aiCreatePage() {
    const topic = prompt('请输入Wiki页面主题：');
    if (!topic || !topic.trim()) return;
    showToast('AI正在生成Wiki页面...', 'info');
    try {
        const data = await wikiApi('POST', '/ai/create-page', {
            topic: topic.trim(), space_id: wikiState.currentSpaceId,
        });
        if (data.success) {
            showToast('AI页面创建成功', 'success');
            loadWikiPageTree();
            if (data.page_id) loadWikiPage(data.page_id);
        }
    } catch (e) {
        showToast('AI创建失败: ' + e.message, 'error');
    }
}

async function aiGenerateTemplate() {
    const types = ['process', 'standard', 'manual', 'report', 'meeting', 'project'];
    const type = prompt('请选择模板类型：\n1. process\n2. standard\n3. manual\n4. report\n5. meeting\n6. project\n\n输入类型名称：');
    if (!type || !types.includes(type)) return;
    showToast('AI正在生成模板...', 'info');
    try {
        const data = await wikiApi('POST', '/ai/generate-template', {
            template_type: type, space_id: wikiState.currentSpaceId,
        });
        if (data.success) {
            showToast('模板生成成功', 'success');
            loadWikiPageTree();
            if (data.page_id) loadWikiPage(data.page_id);
        }
    } catch (e) {
        showToast('AI模板生成失败: ' + e.message, 'error');
    }
}

async function importWebpage() {
    const url = prompt('请输入网页URL：');
    if (!url || !url.trim()) return;
    showToast('正在抓取网页...', 'info');
    try {
        const data = await wikiApi('POST', '/import/webpage', { url: url.trim() });
        if (data.success) {
            showToast('网页导入成功', 'success');
            loadWikiPageTree();
            if (data.page) loadWikiPage(data.page.page_id);
        }
    } catch (e) {
        showToast('网页导入失败: ' + e.message, 'error');
    }
}

async function enablePageShare() {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    const password = prompt('设置访问密码（留空则公开访问）：') || '';
    try {
        const data = await wikiApi('POST', `/pages/${wikiState.currentPage.page_id}/share/enable`, {
            password,
        });
        if (data.success) {
            const shareUrl = `${window.location.origin}/wiki/share/${data.share_token}`;
            showToast('分享链接已生成: ' + shareUrl, 'success');
        }
    } catch (e) {
        showToast('分享设置失败: ' + e.message, 'error');
    }
}

async function encryptPage() {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    const password = prompt('请设置页面密码：');
    if (!password) return;
    try {
        const data = await wikiApi('POST', `/pages/${wikiState.currentPage.page_id}/encrypt`, {
            password,
        });
        if (data.success) {
            showToast('页面已加密', 'success');
        }
    } catch (e) {
        showToast('加密失败: ' + e.message, 'error');
    }
}

async function setPermission() {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    const userId = prompt('请输入用户ID：');
    if (!userId) return;
    const role = prompt('请选择角色 (admin/editor/reader/guest)：');
    if (!['admin', 'editor', 'reader', 'guest'].includes(role)) {
        showToast('无效的角色', 'warning'); return;
    }
    try {
        const data = await wikiApi('POST', '/permissions/set', {
            space_id: wikiState.currentSpaceId,
            page_id: wikiState.currentPage.page_id,
            user_id: userId,
            role: role,
        });
        if (data.success) {
            showToast('权限设置成功', 'success');
        }
    } catch (e) {
        showToast('权限设置失败: ' + e.message, 'error');
    }
}

function renderWikiSpaces() {
    const container = document.getElementById('wiki-spaces-list');
    if (!container) return;
    container.innerHTML = wikiState.spaces.map(s => `
        <div class="wiki-space-item ${s.space_id === wikiState.currentSpaceId ? 'active' : ''}"
             onclick="switchWikiSpace('${s.space_id}')">
            <span class="wiki-space-icon">${s.icon || '📁'}</span>
            <span class="wiki-space-name">${escapeHtml(s.name)}</span>
            <span class="wiki-space-type">${escapeHtml(s.space_type || '')}</span>
        </div>
    `).join('') || '<p style="color:var(--text-muted);font-size:12px">暂无空间</p>';
}

function switchWikiSpace(spaceId) {
    wikiState.currentSpaceId = spaceId;
    wikiState.currentPage = null;
    loadWikiPageTree(spaceId);
    renderWikiSpaces();
    renderWikiPageContent();
}

function renderWikiTags() {
    const container = document.getElementById('wiki-tags-list');
    if (!container) return;
    container.innerHTML = wikiState.tags.map(t => {
        const color = t.color || '#6366f1';
        return `<span class="wiki-tag" style="background:${color}20;color:${color};border:1px solid ${color}40;cursor:pointer"
              onclick="filterByTag('${escapeHtml(t.name)}')">
            ${escapeHtml(t.name)}
        </span>`;
    }).join('') || '<span style="color:var(--text-muted);font-size:12px">暂无标签</span>';
}

function renderWikiPageTree() {
    const container = document.getElementById('wiki-page-tree');
    if (!container) return;
    container.innerHTML = renderWikiTreeItems(wikiState.pageTree) || '<p style="color:var(--text-muted);font-size:12px">暂无页面</p>';
}

function renderWikiTreeItems(items) {
    if (!items || !items.length) return '';
    return items.map(item => {
        const depth = item.depth || 0;
        const hasChildren = item.children && item.children.length > 0;
        return `
            <div class="wiki-tree-item" style="padding-left:${depth * 16 + 4}px">
                ${hasChildren ? '<span class="wiki-tree-toggle" onclick="toggleWikiTreeNode(this)">▼</span>' : '<span style="width:14px;display:inline-block"></span>'}
                <span class="wiki-tree-title" onclick="loadWikiPage('${item.page_id}')" style="cursor:pointer">${escapeHtml(item.title)}</span>
                ${hasChildren ? `<div class="wiki-tree-children">${renderWikiTreeItems(item.children)}</div>` : ''}
            </div>`;
    }).join('');
}

function toggleWikiTreeNode(el) {
    const children = el.parentElement.querySelector('.wiki-tree-children');
    if (children) {
        const visible = children.style.display !== 'none';
        children.style.display = visible ? 'none' : 'block';
        el.textContent = visible ? '▶' : '▼';
    }
}

function renderWikiPageContent() {
    const container = document.getElementById('wiki-content-area');
    if (!container) return;

    if (!wikiState.currentPage) {
        container.innerHTML = `
            <div class="wiki-empty" style="text-align:center;padding:60px 20px;color:var(--text-muted)">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
                <p style="margin-top:12px">选择或创建一个Wiki页面开始编辑</p>
            </div>`;
        return;
    }

    const page = wikiState.currentPage;
    container.innerHTML = `
        <div class="wiki-page-header" style="padding:16px;border-bottom:1px solid var(--border)">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
                <h2 style="margin:0;font-size:20px;font-weight:600">${escapeHtml(page.title)}</h2>
                <div style="display:flex;gap:6px;flex-wrap:wrap">
                    <button class="toolbar-btn" onclick="aiSummarizePage()" title="AI摘要">📝 摘要</button>
                    <button class="toolbar-btn" onclick="aiMindmapPage()" title="AI思维导图">🗺️ 导图</button>
                    <button class="toolbar-btn" onclick="aiTranslatePage('en')" title="翻译">🌐 翻译</button>
                    <button class="toolbar-btn" onclick="aiPolishPage()" title="润色">✨ 润色</button>
                    <button class="toolbar-btn" onclick="aiCopilot('related')" title="查找相关笔记">⌕ 相关</button>
                    <button class="toolbar-btn" onclick="aiCopilot('outline')" title="生成大纲">☷ 大纲</button>
                    <button class="toolbar-btn" onclick="aiCopilot('expand')" title="补充内容">＋ 补充</button>
                    <button class="toolbar-btn" onclick="aiCopilot('rewrite')" title="改写选中内容">↻ 改写</button>
                    <button class="toolbar-btn" onclick="aiCopilot('gaps')" title="检查证据缺口">⚑ 缺口</button>
                    <button class="toolbar-btn" onclick="enablePageShare()" title="分享">🔗 分享</button>
                    <button class="toolbar-btn" onclick="encryptPage()" title="加密">🔒 加密</button>
                    <button class="toolbar-btn" onclick="setPermission()" title="权限">👥 权限</button>
                    <button class="toolbar-btn" onclick="addAnnotation()" title="批注">📌 批注</button>
                    <button class="toolbar-btn" onclick="showVersionDiff()" title="版本对比">📊 对比</button>
                    <button class="toolbar-btn" onclick="showHistoryDiff()" title="修改留痕">📝 留痕</button>
                    <span style="width:1px;height:20px;background:var(--border);margin:0 2px"></span>
                    <button class="toolbar-btn" onclick="uploadAttachment()" title="上传附件">📎 附件</button>
                    <button class="toolbar-btn" onclick="showExportDialog()" title="导出页面">📤 导出</button>
                    <button class="toolbar-btn" onclick="resolvePageEmbeds()" title="解析嵌入引用">🔗 嵌入</button>
                    <button class="toolbar-btn" onclick="softDeletePage('${page.page_id}')" title="移入回收站" style="color:var(--warning)">♻️ 回收</button>
                    <button class="toolbar-btn" onclick="deleteWikiPage('${page.page_id}')" title="删除" style="color:var(--danger)">🗑️</button>
                </div>
            </div>
            <div style="margin-top:8px;font-size:12px;color:var(--text-secondary);display:flex;gap:12px;flex-wrap:wrap">
                <span>作者: ${escapeHtml(page.author)}</span>
                <span>版本: v${page.version}</span>
                <span>更新: ${page.updated_at || '-'}</span>
                ${page.tags?.map(t => `<span class="wiki-tag">${escapeHtml(t)}</span>`).join('') || ''}
            </div>
        </div>
        <div style="padding:16px">
            <div id="wiki-editor-container" style="min-height:300px;border:1px solid var(--border);border-radius:6px;overflow:hidden"></div>
            <div style="margin-top:8px;display:flex;gap:8px">
                <button class="btn-primary" onclick="saveWikiPage()" style="padding:8px 16px;background:var(--primary);color:#fff;border:none;border-radius:4px;cursor:pointer">保存</button>
            </div>
        </div>
        <div id="wiki-ai-result" style="display:none;padding:16px;border-top:1px solid var(--border)"></div>
        <div style="padding:16px;border-top:1px solid var(--border)">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
                <h3 style="font-size:15px;font-weight:600;margin:0">附件</h3>
                <button class="toolbar-btn sm" onclick="uploadAttachment()">📎 上传</button>
            </div>
            <div id="wiki-attachments-list"></div>
        </div>
        <div style="padding:16px;border-top:1px solid var(--border)">
            <h3 style="font-size:15px;font-weight:600;margin-bottom:8px">批注</h3>
            <div id="wiki-annotations-list"></div>
        </div>
        <div style="padding:16px;border-top:1px solid var(--border)">
            <h3 style="font-size:15px;font-weight:600;margin-bottom:8px">评论</h3>
            <div id="wiki-comments-list"></div>
            <div style="display:flex;gap:8px;margin-top:8px">
                <input id="wiki-comment-input" placeholder="添加评论..." style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:4px">
                <button onclick="addWikiComment()" style="padding:8px 16px;background:var(--primary);color:#fff;border:none;border-radius:4px;cursor:pointer">发送</button>
            </div>
        </div>
    `;
    renderWikiComments();
    if (wikiState.currentPage) {
        loadAttachments(wikiState.currentPage.page_id);
    }
    if (typeof WikiEditor !== 'undefined') {
        setTimeout(() => {
            WikiEditor.init('wiki-editor-container', page.content || '');
        }, 50);
    }
}

function renderWikiComments() {
    const container = document.getElementById('wiki-comments-list');
    if (!container) return;
    container.innerHTML = wikiState.comments.map(c => `
        <div style="padding:8px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-secondary)">
                <span>${escapeHtml(c.author)}</span>
                <span>${c.created_at || ''}</span>
            </div>
            <div style="margin-top:4px">${escapeHtml(c.content)}</div>
            ${c.selection_text ? `<div style="margin-top:4px;padding:4px 8px;background:var(--bg-secondary);border-radius:4px;font-size:12px;color:var(--text-muted)">"${escapeHtml(c.selection_text)}"</div>` : ''}
        </div>
    `).join('') || '<p style="color:var(--text-muted);font-size:13px">暂无评论</p>';
}

function renderWikiVersions() {
    const container = document.getElementById('wiki-versions-list');
    if (!container) return;
    container.innerHTML = wikiState.versions.slice().reverse().map(v => `
        <div style="padding:8px 0;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;font-size:12px">
            <span style="font-weight:600">v${v.version_number}</span>
            <span style="flex:1">${escapeHtml(v.change_summary || '')}</span>
            <span style="color:var(--text-secondary)">${escapeHtml(v.author)}</span>
            <span style="color:var(--text-muted)">${v.created_at || ''}</span>
            <button class="toolbar-btn sm" onclick="rollbackWikiPage('${v.page_id}', ${v.version_number})" style="font-size:11px">回滚</button>
            <button class="toolbar-btn sm" onclick="showVersionDiff()" style="font-size:11px;color:var(--primary)">对比</button>
        </div>
    `).join('') || '<p style="color:var(--text-muted);font-size:13px">暂无版本历史</p>';
}

function renderWikiSearchResults() {
    const container = document.getElementById('wiki-search-results');
    if (!container) return;
    container.innerHTML = wikiState.searchResults.map(r => `
        <div style="padding:12px;border:1px solid var(--border);border-radius:6px;cursor:pointer;margin-bottom:8px" onclick="loadWikiPage('${r.page_id || ''}')">
            <div style="font-weight:600;margin-bottom:4px">${escapeHtml(r.title)}</div>
            <div style="font-size:12px;color:var(--text-muted)">${escapeHtml((r.snippet || r.content || '').slice(0, 150))}</div>
        </div>
    `).join('') || '<p style="color:var(--text-muted)">未找到相关结果</p>';
    container.style.display = 'block';
}

function filterByTag(tagName) {
    wikiSearch(tagName);
}

async function showVersionDiff(pageId) {
    if (!pageId) pageId = wikiState.currentPage?.page_id;
    if (!pageId) { showToast('请先选择页面', 'warning'); return; }
    const v1 = prompt('输入旧版本号：');
    const v2 = prompt('输入新版本号：');
    if (!v1 || !v2) return;
    try {
        const data = await wikiApi('POST', '/versions/diff', {
            page_id: pageId, version1: parseInt(v1), version2: parseInt(v2),
        });
        if (data.success) {
            const resultDiv = document.getElementById('wiki-ai-result');
            if (resultDiv) {
                const stats = data.stats || {};
                resultDiv.innerHTML = `
                    <h4>版本差异 v${v1} → v${v2}</h4>
                    <div style="display:flex;gap:16px;margin:8px 0;font-size:13px">
                        <span style="color:#22c55e">+${stats.lines_added || 0} 行</span>
                        <span style="color:#ef4444">-${stats.lines_removed || 0} 行</span>
                        <span>相似度: ${stats.similarity || 0}%</span>
                    </div>
                    <details><summary>变更详情</summary>
                        <pre style="font-size:12px;overflow-x:auto;max-height:400px;overflow-y:auto;background:var(--bg-secondary);padding:8px;border-radius:4px">${escapeHtml(data.unified_diff || '')}</pre>
                    </details>
                `;
                resultDiv.style.display = 'block';
            }
        }
    } catch (e) {
        showToast('版本对比失败: ' + e.message, 'error');
    }
}

async function showHistoryDiff(pageId) {
    if (!pageId) pageId = wikiState.currentPage?.page_id;
    if (!pageId) { showToast('请先选择页面', 'warning'); return; }
    try {
        const data = await wikiApi('GET', `/pages/${pageId}/history-diff`);
        if (data.success) {
            const resultDiv = document.getElementById('wiki-ai-result');
            if (resultDiv) {
                const diffs = data.diffs || [];
                resultDiv.innerHTML = `
                    <h4>修改历史 (${diffs.length} 次变更)</h4>
                    ${diffs.map(d => {
                        const s = d.stats || {};
                        return `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px">
                            <span style="font-weight:600">v${d.from_version}→v${d.to_version}</span>
                            <span style="color:var(--text-secondary);margin-left:8px">${escapeHtml(d.change_summary || '')}</span>
                            <span style="color:#22c55e;margin-left:8px">+${s.lines_added || 0}</span>
                            <span style="color:#ef4444">-${s.lines_removed || 0}</span>
                            <span style="color:var(--text-muted);margin-left:8px">${d.author || ''}</span>
                        </div>`;
                    }).join('')}
                `;
                resultDiv.style.display = 'block';
            }
        }
    } catch (e) {
        showToast('历史对比失败: ' + e.message, 'error');
    }
}

async function quickBookmark() {
    const url = prompt('输入要保存的网页URL：');
    if (!url || !url.trim()) return;
    const title = prompt('页面标题（可选）：') || '';
    const selection = prompt('摘录内容（可选）：') || '';
    showToast('正在保存...', 'info');
    try {
        const data = await wikiApi('POST', '/bookmarks/quick-save', {
            url: url.trim(), title, selection,
            space_id: wikiState.currentSpaceId,
        });
        if (data.success) {
            showToast('书签保存成功', 'success');
            loadWikiPageTree();
            if (data.page_id) loadWikiPage(data.page_id);
        }
    } catch (e) {
        showToast('书签保存失败: ' + e.message, 'error');
    }
}

async function addAnnotation() {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    const textarea = document.getElementById('wiki-editor-textarea');
    if (!textarea) { showToast('请先进入编辑模式', 'warning'); return; }
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selectedText = textarea.value.substring(start, end);
    if (!selectedText) { showToast('请先在编辑器中选中要批注的文本', 'warning'); return; }
    const content = prompt(`对"${selectedText.slice(0, 30)}${selectedText.length > 30 ? '...' : ''}"的批注：`);
    if (!content) return;
    try {
        const data = await wikiApi('POST', '/annotations/create', {
            page_id: wikiState.currentPage.page_id,
            content,
            selection_start: start,
            selection_end: end,
            selection_text: selectedText,
            annotation_type: 'highlight',
        });
        if (data.success) {
            showToast('批注已添加', 'success');
        }
    } catch (e) {
        showToast('批注添加失败: ' + e.message, 'error');
    }
}

async function loadAnnotations(pageId) {
    if (!pageId) return;
    try {
        const data = await wikiApi('GET', `/annotations/${pageId}`);
        if (data.success) {
            wikiState.annotations = data.annotations || [];
            renderAnnotations();
        }
    } catch (e) {
        console.error('加载批注失败:', e);
    }
}

function renderAnnotations() {
    const container = document.getElementById('wiki-annotations-list');
    if (!container) return;
    container.innerHTML = (wikiState.annotations || []).map(a => `
        <div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px">
            <div style="background:#fef08a30;padding:2px 6px;border-radius:3px;margin-bottom:4px">"${escapeHtml((a.selection_text || '').slice(0, 50))}"</div>
            <div>${escapeHtml(a.content)}</div>
            <div style="color:var(--text-muted);margin-top:2px">${a.author} · ${a.created_at || ''}</div>
        </div>
    `).join('') || '<p style="color:var(--text-muted);font-size:12px">暂无批注</p>';
}

async function importNotionAPI() {
    const databaseId = prompt('请输入Notion数据库ID：');
    if (!databaseId) return;
    const token = prompt('请输入Notion集成Token：');
    if (!token) return;
    showToast('正在从Notion导入...', 'info');
    try {
        const data = await wikiApi('POST', '/import/notion-api', {
            database_id: databaseId, notion_token: token,
            space_id: wikiState.currentSpaceId,
        });
        if (data.success) {
            const count = (data.success_items || []).length;
            showToast(`Notion导入完成: ${count} 个页面`, 'success');
            loadWikiPageTree();
        }
    } catch (e) {
        showToast('Notion导入失败: ' + e.message, 'error');
    }
}

async function importObsidian() {
    const vaultDir = prompt('请输入Obsidian库目录路径：');
    if (!vaultDir) return;
    showToast('正在导入Obsidian库...', 'info');
    try {
        const data = await wikiApi('POST', '/import/obsidian', {
            vault_dir: vaultDir, space_id: wikiState.currentSpaceId,
        });
        if (data.success) {
            showToast(`Obsidian导入完成: ${data.imported_count} 个页面`, 'success');
            loadWikiPageTree();
        }
    } catch (e) {
        showToast('Obsidian导入失败: ' + e.message, 'error');
    }
}

async function importExcelEnhanced() {
    const filePath = prompt('请输入Excel文件路径：');
    if (!filePath) return;
    showToast('正在导入Excel...', 'info');
    try {
        const data = await wikiApi('POST', '/import/excel-enhanced', {
            file_path: filePath, space_id: wikiState.currentSpaceId,
            include_stats: true,
        });
        if (data.success) {
            showToast('Excel导入成功（含统计）', 'success');
            loadWikiPageTree();
            if (data.page) loadWikiPage(data.page.page_id);
        }
    } catch (e) {
        showToast('Excel导入失败: ' + e.message, 'error');
    }
}

async function getBookmarkletScript() {
    try {
        const data = await wikiApi('GET', '/bookmarklet/script');
        if (data.success) {
            const dialog = document.createElement('div');
            dialog.className = 'new-item-dialog';
            dialog.id = 'bookmarklet-dialog';
            dialog.innerHTML = `
                <div class="dialog-box" style="width:500px">
                    <div class="dialog-title">浏览器书签快速保存</div>
                    <div style="padding:12px">
                        <p style="font-size:13px;margin-bottom:8px">将下面的链接拖拽到浏览器书签栏即可使用：</p>
                        <div style="padding:8px;background:var(--bg-secondary);border-radius:4px;word-break:break-all;font-size:12px">
                            <a href="${escapeHtml(data.script)}" style="color:var(--primary)">📌 快速保存到Wiki</a>
                        </div>
                        <p style="font-size:12px;color:var(--text-muted);margin-top:8px">在任意网页点击此书签，即可将页面保存到Wiki知识库</p>
                    </div>
                    <div class="dialog-actions" style="margin-top:16px">
                        <button class="dialog-btn" onclick="document.getElementById('bookmarklet-dialog').remove()">关闭</button>
                    </div>
                </div>`;
            document.body.appendChild(dialog);
        }
    } catch (e) {
        showToast('获取书签脚本失败: ' + e.message, 'error');
    }
}

async function showWikiKGVisualization() {
    try {
        const data = await wikiApi('GET', '/kg/visualization');
        if (data.success) {
            const nodes = data.nodes || [];
            const edges = data.edges || [];
            const dialog = document.createElement('div');
            dialog.className = 'new-item-dialog';
            dialog.id = 'wiki-kg-dialog';
            dialog.innerHTML = `
                <div class="dialog-box" style="width:800px;height:600px;display:flex;flex-direction:column">
                    <div class="dialog-title" style="display:flex;justify-content:space-between;align-items:center">
                        <span>知识图谱可视化</span>
                        <button class="dialog-btn" onclick="document.getElementById('wiki-kg-dialog').remove()" style="padding:4px 8px">关闭</button>
                    </div>
                    <div style="flex:1;position:relative;overflow:hidden">
                        <canvas id="wiki-kg-canvas" style="width:100%;height:100%"></canvas>
                        <div id="wiki-kg-tooltip" style="display:none;position:absolute;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;padding:6px 10px;font-size:12px;pointer-events:none;box-shadow:0 2px 8px rgba(0,0,0,0.15);z-index:10"></div>
                    </div>
                    <div style="padding:8px;font-size:12px;color:var(--text-muted);display:flex;justify-content:space-between">
                        <span>节点: ${nodes.length} | 关系: ${edges.length}</span>
                        <span>拖拽节点 · 滚轮缩放 · 点击查看详情</span>
                    </div>
                </div>`;
            document.body.appendChild(dialog);
            setTimeout(() => renderWikiKGCanvas(nodes, edges), 100);
        }
    } catch (e) {
        showToast('知识图谱加载失败: ' + e.message, 'error');
    }
}

function renderWikiKGCanvas(nodes, edges) {
    const canvas = document.getElementById('wiki-kg-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const container = canvas.parentElement;
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;

    const typeColors = {
        crime: '#ef4444', term: '#3b82f6', organization: '#22c55e',
        person: '#f59e0b', location: '#8b5cf6', default: '#6366f1',
    };

    const simNodes = nodes.map((n, i) => ({
        ...n,
        x: canvas.width / 2 + (Math.random() - 0.5) * 300,
        y: canvas.height / 2 + (Math.random() - 0.5) * 300,
        vx: 0, vy: 0,
        radius: 8 + Math.min(n.label?.length || 1, 6) * 2,
    }));

    const nodeMap = {};
    simNodes.forEach(n => nodeMap[n.id] = n);

    let alpha = 1.0;
    let dragNode = null;
    let hoverNode = null;
    let offsetX = 0, offsetY = 0;
    let scale = 1;

    function simulate() {
        if (alpha < 0.001) return;
        for (let i = 0; i < simNodes.length; i++) {
            for (let j = i + 1; j < simNodes.length; j++) {
                const dx = simNodes[j].x - simNodes[i].x;
                const dy = simNodes[j].y - simNodes[i].y;
                const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                const force = 500 * alpha / (dist * dist);
                const fx = dx / dist * force;
                const fy = dy / dist * force;
                simNodes[i].vx -= fx;
                simNodes[i].vy -= fy;
                simNodes[j].vx += fx;
                simNodes[j].vy += fy;
            }
        }
        edges.forEach(e => {
            const source = nodeMap[e.source];
            const target = nodeMap[e.target];
            if (!source || !target) return;
            const dx = target.x - source.x;
            const dy = target.y - source.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = (dist - 120) * 0.01 * alpha;
            const fx = dx / dist * force;
            const fy = dy / dist * force;
            source.vx += fx;
            source.vy += fy;
            target.vx -= fx;
            target.vy -= fy;
        });
        simNodes.forEach(n => {
            if (n === dragNode) return;
            n.vx += (canvas.width / 2 - n.x) * 0.001 * alpha;
            n.vy += (canvas.height / 2 - n.y) * 0.001 * alpha;
            n.vx *= 0.6;
            n.vy *= 0.6;
            n.x += n.vx;
            n.y += n.vy;
        });
        alpha *= 0.99;
    }

    function draw() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.save();
        ctx.translate(offsetX, offsetY);
        ctx.scale(scale, scale);

        edges.forEach(e => {
            const source = nodeMap[e.source];
            const target = nodeMap[e.target];
            if (!source || !target) return;
            ctx.beginPath();
            ctx.moveTo(source.x, source.y);
            ctx.lineTo(target.x, target.y);
            ctx.strokeStyle = 'rgba(100,100,100,0.2)';
            ctx.lineWidth = 1;
            ctx.stroke();
            const mx = (source.x + target.x) / 2;
            const my = (source.y + target.y) / 2;
            if (e.label) {
                ctx.fillStyle = 'rgba(100,100,100,0.5)';
                ctx.font = '9px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText(e.label, mx, my - 3);
            }
        });

        simNodes.forEach(n => {
            const color = typeColors[n.node_type] || typeColors.default;
            ctx.beginPath();
            ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
            ctx.fillStyle = n === hoverNode ? color : color + 'cc';
            ctx.fill();
            ctx.strokeStyle = n === hoverNode ? '#fff' : color;
            ctx.lineWidth = n === hoverNode ? 2 : 1;
            ctx.stroke();
            ctx.fillStyle = '#fff';
            ctx.font = '10px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(n.label?.slice(0, 6) || '', n.x, n.y + 3);
        });

        ctx.restore();
    }

    function loop() {
        simulate();
        draw();
        requestAnimationFrame(loop);
    }
    loop();

    canvas.addEventListener('mousemove', (e) => {
        const rect = canvas.getBoundingClientRect();
        const mx = (e.clientX - rect.left - offsetX) / scale;
        const my = (e.clientY - rect.top - offsetY) / scale;
        hoverNode = null;
        for (const n of simNodes) {
            const dx = mx - n.x;
            const dy = my - n.y;
            if (dx * dx + dy * dy < n.radius * n.radius) {
                hoverNode = n;
                break;
            }
        }
        if (dragNode) {
            dragNode.x = mx;
            dragNode.y = my;
            dragNode.vx = 0;
            dragNode.vy = 0;
            alpha = Math.max(alpha, 0.3);
        }
        const tooltip = document.getElementById('wiki-kg-tooltip');
        if (tooltip) {
            if (hoverNode) {
                tooltip.style.display = 'block';
                tooltip.style.left = (e.clientX - rect.left + 10) + 'px';
                tooltip.style.top = (e.clientY - rect.top + 10) + 'px';
                tooltip.innerHTML = `<strong>${escapeHtml(hoverNode.label || '')}</strong><br>类型: ${escapeHtml(hoverNode.node_type || 'unknown')}`;
                canvas.style.cursor = 'pointer';
            } else {
                tooltip.style.display = 'none';
                canvas.style.cursor = dragNode ? 'grabbing' : 'default';
            }
        }
    });

    canvas.addEventListener('mousedown', (e) => {
        if (hoverNode) {
            dragNode = hoverNode;
            alpha = Math.max(alpha, 0.5);
        }
    });

    canvas.addEventListener('mouseup', () => { dragNode = null; });
    canvas.addEventListener('mouseleave', () => { dragNode = null; hoverNode = null; });

    canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        scale *= delta;
        scale = Math.max(0.3, Math.min(3, scale));
    });
}

async function fulltextSearch(query, spaceId) {
    if (!query) { showToast('请输入搜索关键词', 'warning'); return; }
    try {
        const data = await wikiApi('POST', '/search/fulltext', {
            query, space_id: spaceId || undefined, page: 1, page_size: 20, fuzzy: true, highlight: true,
        });
        if (data.success) {
            const resultDiv = document.getElementById('wiki-ai-result');
            if (resultDiv) {
                resultDiv.style.display = 'block';
                const results = data.results || [];
                resultDiv.innerHTML = `
                    <h4>全文检索: "${escapeHtml(query)}" (${data.total} 结果)</h4>
                    <div style="margin-top:8px">
                    ${results.map(r => `
                        <div style="padding:8px;border:1px solid var(--border);border-radius:4px;margin-bottom:6px;cursor:pointer" onclick="loadWikiPage('${r.page_id}')">
                            <div style="font-weight:600;margin-bottom:4px">${r.title}</div>
                            <div style="font-size:12px;color:var(--text-secondary)">${r.content_preview?.slice(0, 150) || ''}</div>
                            <div style="font-size:11px;color:var(--text-muted);margin-top:4px">
                                ${r.author || ''} · ${r.tags?.join(', ') || ''} · 评分: ${r.score || 0}
                            </div>
                        </div>
                    `).join('')}
                    </div>
                    ${results.length === 0 ? '<p style="color:var(--text-muted)">未找到匹配结果</p>' : ''}
                `;
            }
        }
    } catch (e) {
        showToast('全文检索失败: ' + e.message, 'error');
    }
}

async function showTemplateDialog() {
    try {
        const data = await wikiApi('GET', '/templates');
        if (data.success) {
            const templates = data.templates || [];
            const dialog = document.createElement('div');
            dialog.className = 'new-item-dialog';
            dialog.id = 'template-dialog';
            dialog.innerHTML = `
                <div class="dialog-box" style="width:700px;max-height:80vh;overflow-y:auto">
                    <div class="dialog-title">Wiki模板</div>
                    <div style="padding:12px">
                        <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
                            <button class="toolbar-btn active" onclick="this.parentElement.querySelectorAll('.toolbar-btn').forEach(b=>b.classList.remove('active'));this.classList.add('active');filterTemplates('')" style="padding:6px 12px;font-size:12px">全部</button>
                            ${[...new Set(templates.map(t=>t.category))].map(c =>
                                `<button class="toolbar-btn" onclick="this.parentElement.querySelectorAll('.toolbar-btn').forEach(b=>b.classList.remove('active'));this.classList.add('active');filterTemplates('${c}')" style="padding:6px 12px;font-size:12px">${c}</button>`
                            ).join('')}
                        </div>
                        <div id="template-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
                            ${templates.map(t => `
                                <div class="template-card" data-category="${t.category}" style="padding:12px;border:1px solid var(--border);border-radius:6px;cursor:pointer;transition:all 0.2s" onmouseover="this.style.borderColor='var(--primary)'" onmouseout="this.style.borderColor='var(--border)'" onclick="applyTemplate('${t.template_id}','${escapeHtml(t.name)}')">
                                    <div style="font-weight:600;font-size:14px;margin-bottom:4px">${escapeHtml(t.name)}</div>
                                    <div style="font-size:12px;color:var(--text-secondary)">${escapeHtml(t.description || '')}</div>
                                    <div style="font-size:11px;color:var(--text-muted);margin-top:4px">${t.category} ${t.is_builtin ? '· 内置' : '· 自定义'}</div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    <div class="dialog-actions" style="margin-top:12px">
                        <button class="dialog-btn" onclick="document.getElementById('template-dialog').remove()">关闭</button>
                    </div>
                </div>`;
            document.body.appendChild(dialog);
        }
    } catch (e) {
        showToast('模板加载失败: ' + e.message, 'error');
    }
}

function filterTemplates(category) {
    document.querySelectorAll('.template-card').forEach(card => {
        card.style.display = (!category || card.dataset.category === category) ? '' : 'none';
    });
}

async function applyTemplate(templateId, templateName) {
    const title = prompt(`使用模板"${templateName}"创建页面，请输入标题：`);
    if (!title) return;
    try {
        const data = await wikiApi('POST', '/templates/apply', {
            template_id: templateId, title, space_id: wikiState.currentSpaceId,
        });
        if (data.success) {
            showToast('模板页面创建成功', 'success');
            document.getElementById('template-dialog')?.remove();
            loadWikiPageTree();
            loadWikiPage(data.page_id);
        }
    } catch (e) {
        showToast('模板应用失败: ' + e.message, 'error');
    }
}

async function uploadAttachment() {
    if (!requireWriteAccess()) return;
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    const input = document.createElement('input');
    input.type = 'file';
    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        formData.append('author', 'system');
        formData.append('description', '');
        try {
            const resp = await fetch(`/api/v1/wiki/attachments/upload/${wikiState.currentPage.page_id}`, {
                method: 'POST', headers: getAccessHeaders(), body: formData,
            });
            const data = await resp.json();
            if (data.success) {
                showToast('附件上传成功', 'success');
                loadAttachments(wikiState.currentPage.page_id);
            }
        } catch (e) {
            showToast('附件上传失败: ' + e.message, 'error');
        }
    };
    input.click();
}

async function loadAttachments(pageId) {
    try {
        const data = await wikiApi('GET', `/attachments/${pageId}`);
        if (data.success) {
            const container = document.getElementById('wiki-attachments-list');
            if (container) {
                const atts = data.attachments || [];
                container.innerHTML = atts.map(a => `
                    <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:12px">
                        <span style="flex:1">${escapeHtml(a.original_name)}</span>
                        <span style="color:var(--text-muted)">${(a.file_size / 1024).toFixed(1)}KB</span>
                        <a href="/api/v1/wiki/attachments/download/${a.attach_id}" style="color:var(--primary);text-decoration:none" download>下载</a>
                        <button class="toolbar-btn sm" onclick="deleteAttachment('${a.attach_id}')" style="font-size:11px;color:var(--danger)">删除</button>
                    </div>
                `).join('') || '<p style="color:var(--text-muted);font-size:12px">暂无附件</p>';
            }
        }
    } catch (e) { /* ignore */ }
}

async function deleteAttachment(attachId) {
    if (!requireWriteAccess()) return;
    if (!confirm('确定删除此附件？')) return;
    try {
        await wikiApi('DELETE', `/attachments/delete/${attachId}`);
        showToast('附件已删除', 'success');
        if (wikiState.currentPage) loadAttachments(wikiState.currentPage.page_id);
    } catch (e) {
        showToast('删除失败: ' + e.message, 'error');
    }
}

async function exportPage(format) {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    try {
        const resp = await fetch('/api/v1/wiki/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAccessHeaders() },
            body: JSON.stringify({ page_id: wikiState.currentPage.page_id, format, resolve_embeds: true }),
        });
        if (resp.ok) {
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${wikiState.currentPage.title}.${format === 'word' ? 'docx' : format}`;
            a.click();
            URL.revokeObjectURL(url);
            showToast('导出成功', 'success');
        } else {
            const data = await resp.json();
            showToast('导出失败: ' + (data.detail || ''), 'error');
        }
    } catch (e) {
        showToast('导出失败: ' + e.message, 'error');
    }
}

async function showRecycleBin() {
    try {
        const data = await wikiApi('GET', '/recycle-bin/list');
        if (data.success) {
            const items = data.items || [];
            const dialog = document.createElement('div');
            dialog.className = 'new-item-dialog';
            dialog.id = 'recycle-bin-dialog';
            dialog.innerHTML = `
                <div class="dialog-box" style="width:600px;max-height:70vh;overflow-y:auto">
                    <div class="dialog-title" style="display:flex;justify-content:space-between;align-items:center">
                        <span>回收站 (${items.length})</span>
                        <div>
                            <button class="toolbar-btn" onclick="emptyRecycleBin()" style="font-size:12px;color:var(--danger);padding:4px 8px">清空</button>
                            <button class="dialog-btn" onclick="document.getElementById('recycle-bin-dialog').remove()" style="padding:4px 8px">关闭</button>
                        </div>
                    </div>
                    <div style="padding:12px">
                        ${items.map(item => `
                            <div style="display:flex;align-items:center;gap:8px;padding:8px;border:1px solid var(--border);border-radius:4px;margin-bottom:6px">
                                <div style="flex:1">
                                    <div style="font-weight:600">${escapeHtml(item.title)}</div>
                                    <div style="font-size:11px;color:var(--text-muted)">删除于 ${item.deleted_at} · ${item.deleted_by}</div>
                                </div>
                                <button class="toolbar-btn" onclick="restoreFromBin('${item.page_id}')" style="font-size:11px;padding:4px 8px;color:var(--primary)">恢复</button>
                                <button class="toolbar-btn" onclick="permanentDelete('${item.page_id}')" style="font-size:11px;padding:4px 8px;color:var(--danger)">永久删除</button>
                            </div>
                        `).join('') || '<p style="color:var(--text-muted)">回收站为空</p>'}
                    </div>
                </div>`;
            document.body.appendChild(dialog);
        }
    } catch (e) {
        showToast('回收站加载失败: ' + e.message, 'error');
    }
}

async function restoreFromBin(pageId) {
    try {
        const data = await wikiApi('POST', `/recycle-bin/restore/${pageId}`);
        if (data.success) {
            showToast('页面已恢复', 'success');
            document.getElementById('recycle-bin-dialog')?.remove();
            showRecycleBin();
            loadWikiPageTree();
        }
    } catch (e) { showToast('恢复失败: ' + e.message, 'error'); }
}

async function permanentDelete(pageId) {
    if (!confirm('永久删除不可恢复，确定继续？')) return;
    try {
        await wikiApi('DELETE', `/recycle-bin/permanent/${pageId}`);
        showToast('已永久删除', 'success');
        document.getElementById('recycle-bin-dialog')?.remove();
        showRecycleBin();
    } catch (e) { showToast('删除失败: ' + e.message, 'error'); }
}

async function emptyRecycleBin() {
    if (!confirm('清空回收站后所有页面将永久删除，确定继续？')) return;
    try {
        await wikiApi('POST', '/recycle-bin/empty');
        showToast('回收站已清空', 'success');
        document.getElementById('recycle-bin-dialog')?.remove();
    } catch (e) { showToast('清空失败: ' + e.message, 'error'); }
}

async function showAuditLogs() {
    try {
        const data = await wikiApi('POST', '/audit-logs/query', { page: 1, page_size: 50 });
        if (data.success) {
            const logs = data.logs || [];
            const dialog = document.createElement('div');
            dialog.className = 'new-item-dialog';
            dialog.id = 'audit-log-dialog';
            dialog.innerHTML = `
                <div class="dialog-box" style="width:700px;max-height:70vh;overflow-y:auto">
                    <div class="dialog-title" style="display:flex;justify-content:space-between;align-items:center">
                        <span>操作审计日志</span>
                        <button class="dialog-btn" onclick="document.getElementById('audit-log-dialog').remove()" style="padding:4px 8px">关闭</button>
                    </div>
                    <div style="padding:12px">
                        ${logs.reverse().map(l => `
                            <div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px">
                                <span style="color:var(--primary);font-weight:600">${escapeHtml(l.action)}</span>
                                <span style="color:var(--text-secondary);margin-left:8px">${escapeHtml(l.resource_type || '')} ${escapeHtml(l.resource_id || '')}</span>
                                <span style="float:right;color:var(--text-muted)">${l.timestamp || ''}</span>
                            </div>
                        `).join('') || '<p style="color:var(--text-muted)">暂无审计日志</p>'}
                    </div>
                </div>`;
            document.body.appendChild(dialog);
        }
    } catch (e) {
        showToast('审计日志加载失败: ' + e.message, 'error');
    }
}

async function showBackupManager() {
    try {
        const data = await wikiApi('GET', '/backups/list');
        if (data.success) {
            const backups = data.backups || [];
            const dialog = document.createElement('div');
            dialog.className = 'new-item-dialog';
            dialog.id = 'backup-dialog';
            dialog.innerHTML = `
                <div class="dialog-box" style="width:600px;max-height:70vh;overflow-y:auto">
                    <div class="dialog-title" style="display:flex;justify-content:space-between;align-items:center">
                        <span>数据备份与恢复</span>
                        <div>
                            <button class="toolbar-btn" onclick="createBackup()" style="font-size:12px;padding:4px 8px;color:var(--primary)">创建备份</button>
                            <button class="dialog-btn" onclick="document.getElementById('backup-dialog').remove()" style="padding:4px 8px">关闭</button>
                        </div>
                    </div>
                    <div style="padding:12px">
                        ${backups.map(b => `
                            <div style="display:flex;align-items:center;gap:8px;padding:8px;border:1px solid var(--border);border-radius:4px;margin-bottom:6px">
                                <div style="flex:1">
                                    <div style="font-weight:600;font-size:13px">${escapeHtml(b.description || b.backup_id)}</div>
                                    <div style="font-size:11px;color:var(--text-muted)">${b.created_at} · ${b.backup_type} · ${b.file_size_mb}MB</div>
                                </div>
                                <a href="/api/v1/wiki/backups/download/${b.backup_id}" style="font-size:11px;color:var(--primary);text-decoration:none">下载</a>
                                <button class="toolbar-btn" onclick="restoreBackup('${b.backup_id}')" style="font-size:11px;padding:4px 8px;color:var(--primary)">恢复</button>
                                <button class="toolbar-btn" onclick="deleteBackup('${b.backup_id}')" style="font-size:11px;padding:4px 8px;color:var(--danger)">删除</button>
                            </div>
                        `).join('') || '<p style="color:var(--text-muted)">暂无备份</p>'}
                    </div>
                </div>`;
            document.body.appendChild(dialog);
        }
    } catch (e) {
        showToast('备份管理加载失败: ' + e.message, 'error');
    }
}

async function createBackup() {
    const desc = prompt('备份描述（可选）：') || '';
    try {
        const data = await wikiApi('POST', '/backups/create', { description: desc, backup_type: 'full' });
        if (data.success) {
            showToast('备份创建成功', 'success');
            document.getElementById('backup-dialog')?.remove();
            showBackupManager();
        }
    } catch (e) { showToast('备份创建失败: ' + e.message, 'error'); }
}

async function restoreBackup(backupId) {
    if (!confirm('恢复备份将覆盖当前数据，确定继续？')) return;
    try {
        const data = await wikiApi('POST', '/backups/restore', { backup_id: backupId });
        if (data.success) {
            showToast('备份恢复成功，请重启服务', 'success');
        } else {
            showToast('恢复失败: ' + (data.error || ''), 'error');
        }
    } catch (e) { showToast('恢复失败: ' + e.message, 'error'); }
}

async function deleteBackup(backupId) {
    if (!confirm('确定删除此备份？')) return;
    try {
        await wikiApi('DELETE', `/backups/delete/${backupId}`);
        showToast('备份已删除', 'success');
        document.getElementById('backup-dialog')?.remove();
        showBackupManager();
    } catch (e) { showToast('删除失败: ' + e.message, 'error'); }
}

async function showApiKeyManager() {
    try {
        const data = await wikiApi('GET', '/api-keys/list');
        if (data.success) {
            const keys = data.keys || [];
            const dialog = document.createElement('div');
            dialog.className = 'new-item-dialog';
            dialog.id = 'apikey-dialog';
            dialog.innerHTML = `
                <div class="dialog-box" style="width:600px;max-height:70vh;overflow-y:auto">
                    <div class="dialog-title" style="display:flex;justify-content:space-between;align-items:center">
                        <span>API密钥管理</span>
                        <div>
                            <button class="toolbar-btn" onclick="createApiKey()" style="font-size:12px;padding:4px 8px;color:var(--primary)">创建密钥</button>
                            <button class="dialog-btn" onclick="document.getElementById('apikey-dialog').remove()" style="padding:4px 8px">关闭</button>
                        </div>
                    </div>
                    <div style="padding:12px">
                        ${keys.map(k => `
                            <div style="display:flex;align-items:center;gap:8px;padding:8px;border:1px solid var(--border);border-radius:4px;margin-bottom:6px">
                                <div style="flex:1">
                                    <div style="font-weight:600;font-size:13px">${escapeHtml(k.name)}</div>
                                    <div style="font-size:11px;color:var(--text-muted)">${k.key_prefix} · 权限: ${k.permissions?.join(', ')} · ${k.is_active ? '✅ 活跃' : '❌ 已禁用'}</div>
                                </div>
                                <button class="toolbar-btn" onclick="revokeApiKey('${k.key_id}')" style="font-size:11px;padding:4px 8px;color:var(--danger)">${k.is_active ? '禁用' : '已禁用'}</button>
                                <button class="toolbar-btn" onclick="deleteApiKey('${k.key_id}')" style="font-size:11px;padding:4px 8px;color:var(--danger)">删除</button>
                            </div>
                        `).join('') || '<p style="color:var(--text-muted)">暂无API密钥</p>'}
                    </div>
                </div>`;
            document.body.appendChild(dialog);
        }
    } catch (e) {
        showToast('API密钥管理加载失败: ' + e.message, 'error');
    }
}

async function createApiKey() {
    const name = prompt('密钥名称：');
    if (!name) return;
    try {
        const data = await wikiApi('POST', '/api-keys/create', {
            name, permissions: ['read', 'write'], expires_days: 0,
        });
        if (data.success) {
            const dialog2 = document.createElement('div');
            dialog2.className = 'new-item-dialog';
            dialog2.id = 'apikey-show-dialog';
            dialog2.innerHTML = `
                <div class="dialog-box" style="width:500px">
                    <div class="dialog-title">API密钥已创建</div>
                    <div style="padding:16px">
                        <p style="color:var(--danger);font-size:13px;margin-bottom:8px">⚠️ 请立即复制并保存，此密钥仅显示一次！</p>
                        <div style="padding:8px;background:var(--bg-secondary);border-radius:4px;word-break:break-all;font-family:monospace;font-size:13px">${escapeHtml(data.api_key)}</div>
                    </div>
                    <div class="dialog-actions">
                        <button class="dialog-btn" onclick="navigator.clipboard.writeText('${data.api_key}');showToast('已复制','success')">复制密钥</button>
                        <button class="dialog-btn" onclick="document.getElementById('apikey-show-dialog').remove();document.getElementById('apikey-dialog')?.remove();showApiKeyManager()">完成</button>
                    </div>
                </div>`;
            document.body.appendChild(dialog2);
        }
    } catch (e) { showToast('创建失败: ' + e.message, 'error'); }
}

async function revokeApiKey(keyId) {
    try {
        await wikiApi('POST', `/api-keys/revoke/${keyId}`);
        showToast('密钥已禁用', 'success');
        document.getElementById('apikey-dialog')?.remove();
        showApiKeyManager();
    } catch (e) { showToast('操作失败: ' + e.message, 'error'); }
}

async function deleteApiKey(keyId) {
    if (!confirm('确定删除此API密钥？')) return;
    try {
        await wikiApi('DELETE', `/api-keys/delete/${keyId}`);
        showToast('密钥已删除', 'success');
        document.getElementById('apikey-dialog')?.remove();
        showApiKeyManager();
    } catch (e) { showToast('删除失败: ' + e.message, 'error'); }
}

async function showExportDialog() {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    const dialog = document.createElement('div');
    dialog.className = 'new-item-dialog';
    dialog.id = 'export-dialog';
    dialog.innerHTML = `
        <div class="dialog-box" style="width:400px">
            <div class="dialog-title">导出页面</div>
            <div style="padding:16px;display:grid;grid-template-columns:1fr 1fr;gap:10px">
                <button class="toolbar-btn" onclick="exportPage('markdown');document.getElementById('export-dialog').remove()" style="padding:16px;font-size:14px">📝 Markdown</button>
                <button class="toolbar-btn" onclick="exportPage('html');document.getElementById('export-dialog').remove()" style="padding:16px;font-size:14px">🌐 HTML</button>
                <button class="toolbar-btn" onclick="exportPage('pdf');document.getElementById('export-dialog').remove()" style="padding:16px;font-size:14px">📄 PDF</button>
                <button class="toolbar-btn" onclick="exportPage('word');document.getElementById('export-dialog').remove()" style="padding:16px;font-size:14px">📘 Word</button>
            </div>
            <div class="dialog-actions" style="margin-top:8px">
                <button class="dialog-btn" onclick="document.getElementById('export-dialog').remove()">取消</button>
            </div>
        </div>`;
    document.body.appendChild(dialog);
}

async function resolvePageEmbeds() {
    if (!wikiState.currentPage) { showToast('请先选择页面', 'warning'); return; }
    try {
        const data = await wikiApi('POST', '/embeds/resolve', {
            content: wikiState.currentPage.content,
        });
        if (data.success) {
            const resultDiv = document.getElementById('wiki-ai-result');
            if (resultDiv) {
                resultDiv.style.display = 'block';
                resultDiv.innerHTML = `
                    <h4>嵌入引用解析结果</h4>
                    <div style="margin-top:8px;padding:12px;background:var(--bg-secondary);border-radius:6px;max-height:400px;overflow-y:auto">
                        <pre style="white-space:pre-wrap;font-size:13px">${escapeHtml(data.resolved_content)}</pre>
                    </div>
                    <div style="margin-top:8px;display:flex;gap:8px">
                        <button class="toolbar-btn" onclick="navigator.clipboard.writeText(\`${escapeHtml(data.resolved_content).replace(/`/g, '\\`')}\`);showToast('已复制','success')" style="font-size:12px">📋 复制结果</button>
                    </div>
                `;
            }
        }
    } catch (e) {
        showToast('嵌入解析失败: ' + e.message, 'error');
    }
}

async function softDeletePage(pageId) {
    if (!confirm('确定将此页面移入回收站？')) return;
    try {
        const data = await wikiApi('POST', `/recycle-bin/soft-delete/${pageId}`);
        if (data.success) {
            showToast('页面已移入回收站', 'success');
            wikiState.currentPage = null;
            loadWikiPageTree();
            renderWikiPageContent();
        }
    } catch (e) {
        showToast('操作失败: ' + e.message, 'error');
    }
}

async function showEmailConfig() {
    try {
        const data = await wikiApi('GET', '/email/config');
        if (data.success) {
            const config = data.config || {};
            const dialog = document.createElement('div');
            dialog.className = 'new-item-dialog';
            dialog.id = 'email-config-dialog';
            dialog.innerHTML = `
                <div class="dialog-box" style="width:550px;max-height:80vh;overflow-y:auto">
                    <div class="dialog-title">邮件通知配置</div>
                    <div style="padding:16px;display:flex;flex-direction:column;gap:10px">
                        <div style="display:flex;align-items:center;gap:8px">
                            <label style="width:100px;font-size:13px">启用通知</label>
                            <input type="checkbox" id="email-enabled" ${config.enabled ? 'checked' : ''}>
                        </div>
                        <div style="display:flex;align-items:center;gap:8px">
                            <label style="width:100px;font-size:13px">SMTP服务器</label>
                            <input class="dialog-input" id="email-smtp-host" value="${config.smtp_host || ''}" placeholder="smtp.example.com" style="flex:1">
                        </div>
                        <div style="display:flex;align-items:center;gap:8px">
                            <label style="width:100px;font-size:13px">SMTP端口</label>
                            <input class="dialog-input" id="email-smtp-port" type="number" value="${config.smtp_port || 587}" style="flex:1">
                        </div>
                        <div style="display:flex;align-items:center;gap:8px">
                            <label style="width:100px;font-size:13px">用户名</label>
                            <input class="dialog-input" id="email-smtp-user" value="${config.smtp_user || ''}" style="flex:1">
                        </div>
                        <div style="display:flex;align-items:center;gap:8px">
                            <label style="width:100px;font-size:13px">密码</label>
                            <input class="dialog-input" id="email-smtp-password" type="password" value="${config.smtp_password || ''}" style="flex:1">
                        </div>
                        <div style="display:flex;align-items:center;gap:8px">
                            <label style="width:100px;font-size:13px">发件地址</label>
                            <input class="dialog-input" id="email-from" value="${config.from_address || ''}" style="flex:1">
                        </div>
                        <div style="display:flex;align-items:center;gap:8px">
                            <label style="width:100px;font-size:13px">使用TLS</label>
                            <input type="checkbox" id="email-use-tls" ${config.use_tls !== false ? 'checked' : ''}>
                        </div>
                        <div style="display:flex;align-items:center;gap:8px">
                            <label style="width:100px;font-size:13px">SSL模式</label>
                            <input type="checkbox" id="email-use-ssl" ${config.use_ssl ? 'checked' : ''}>
                            <span style="font-size:11px;color:var(--text-muted)">QQ邮箱465端口勾选；587端口使用TLS</span>
                        </div>
                        <div style="border-top:1px solid var(--border);padding-top:10px;margin-top:4px">
                            <span style="font-weight:600;font-size:13px">通知事件</span>
                            <div style="display:flex;flex-direction:column;gap:6px;margin-top:6px">
                                <label style="font-size:12px;display:flex;align-items:center;gap:6px">
                                    <input type="checkbox" id="email-notify-update" ${config.notify_on_page_update !== false ? 'checked' : ''}> 页面更新
                                </label>
                                <label style="font-size:12px;display:flex;align-items:center;gap:6px">
                                    <input type="checkbox" id="email-notify-comment" ${config.notify_on_comment !== false ? 'checked' : ''}> 新评论
                                </label>
                                <label style="font-size:12px;display:flex;align-items:center;gap:6px">
                                    <input type="checkbox" id="email-notify-annotation" ${config.notify_on_annotation !== false ? 'checked' : ''}> 新批注
                                </label>
                            </div>
                        </div>
                    </div>
                    <div class="dialog-actions" style="margin-top:12px">
                        <button class="dialog-btn" onclick="document.getElementById('email-config-dialog').remove()">取消</button>
                        <button class="toolbar-btn" onclick="testEmailConnection()" style="font-size:12px;color:var(--primary)">测试连接</button>
                        <button class="dialog-btn primary" onclick="saveEmailConfig()">保存</button>
                    </div>
                </div>`;
            document.body.appendChild(dialog);
        }
    } catch (e) {
        showToast('邮件配置加载失败: ' + e.message, 'error');
    }
}

function getEmailConfigFormPayload() {
    return {
        enabled: document.getElementById('email-enabled')?.checked || false,
        smtp_host: document.getElementById('email-smtp-host')?.value?.trim() || '',
        smtp_port: parseInt(document.getElementById('email-smtp-port')?.value, 10) || 587,
        smtp_user: document.getElementById('email-smtp-user')?.value?.trim() || '',
        smtp_password: document.getElementById('email-smtp-password')?.value || '',
        from_address: document.getElementById('email-from')?.value?.trim() || '',
        use_tls: document.getElementById('email-use-tls')?.checked || false,
        use_ssl: document.getElementById('email-use-ssl')?.checked || false,
        notify_on_page_update: document.getElementById('email-notify-update')?.checked || false,
        notify_on_comment: document.getElementById('email-notify-comment')?.checked || false,
        notify_on_annotation: document.getElementById('email-notify-annotation')?.checked || false,
    };
}

async function saveEmailConfig(closeDialog = true, rethrow = false) {
    try {
        const data = await wikiApi('POST', '/email/config', getEmailConfigFormPayload());
        if (data.success) {
            showToast('邮件配置已保存', 'success');
            if (closeDialog) document.getElementById('email-config-dialog')?.remove();
        }
    } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
        if (rethrow) throw e;
    }
}

async function testEmailConnection() {
    try {
        // Test the values currently visible in the form. Previously this
        // button tested only the last persisted config, so changing
        // smtp.qq.com in the dialog and clicking Test produced a misleading
        // getaddrinfo error from the old smtp.test.com value.
        await saveEmailConfig(false, true);
        const data = await wikiApi('POST', '/email/test');
        if (data.success) {
            showToast('邮件连接测试成功', 'success');
        } else {
            showToast('连接失败: ' + (data.error || ''), 'error');
        }
    } catch (e) {
        showToast('测试失败: ' + e.message, 'error');
    }
}

async function rebuildSearchIndex() {
    if (!confirm('重建搜索索引可能需要一些时间，确定继续？')) return;
    showToast('正在重建搜索索引...', 'info');
    try {
        const data = await wikiApi('POST', '/search/rebuild-index');
        if (data.success) {
            showToast(`索引重建完成: ${data.indexed_pages || 0} 个页面`, 'success');
        }
    } catch (e) {
        showToast('索引重建失败: ' + e.message, 'error');
    }
}
