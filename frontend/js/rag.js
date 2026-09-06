let ragInitialized = false;
let ragSearchMode = 'hybrid';
let ragCurrentChunks = [];
let ragSelectedKB = '';
let ragSelectedKBs = new Set(); // 多知识库选择
let ragWebSearchEnabled = false;
let ragKBList = [];
let ragSessionId = '';
let ragTopK = 5;
let ragConversation = [];
let ragAgentMode = false;
// Resources dragged from the file tree into the RAG composer are kept out of
// the natural-language query and sent as explicit paths. This avoids relying
// on fragile filename parsing (commas, escaped underscores and long Chinese
// filenames) to locate the second/third article.
let ragComposerResourcePaths = new Set();

function removeRAGTreeSelection(path) {
    try {
        if (typeof _fileTreeSelectedPaths !== 'undefined' && _fileTreeSelectedPaths) {
            for (const selected of Array.from(_fileTreeSelectedPaths)) {
                if (selected === path || selected.startsWith(`${path}/`)) _fileTreeSelectedPaths.delete(selected);
            }
            if (typeof syncFileTreeSelectionClasses === 'function') syncFileTreeSelectionClasses();
        }
    } catch (_) {}
}

window.removeRAGComposerResourcePath = function (path) {
    const target = String(path || '');
    if (!target) return;
    for (const selected of Array.from(ragComposerResourcePaths)) {
        if (selected === target || selected.startsWith(`${target}/`)) ragComposerResourcePaths.delete(selected);
    }
    removeRAGTreeSelection(target);
    renderRAGComposerResources();
};

function ragResourceEntry(path) {
    return typeof findFileEntry === 'function' ? findFileEntry(fileStore?.files || [], path) : null;
}

function renderRAGComposerResources() {
    const host = document.getElementById('rag-resource-selection');
    if (!host) return;
    const paths = Array.from(ragComposerResourcePaths);
    host.innerHTML = paths.length
        ? `<span class="rag-resource-selection-label">已定位 ${paths.length} 个资料：</span>${paths.map(path => {
            const entry = ragResourceEntry(path);
            const label = entry?.name || path.split('/').pop() || path;
            return `<button type="button" class="rag-resource-chip" data-resource-path="${escapeHtml(path)}" title="移除此资料">${escapeHtml(label)} <span aria-hidden="true">×</span></button>`;
        }).join('')}`
        : '';
    host.style.display = paths.length ? 'flex' : 'none';
}

function syncRAGComposerResourcesFromText() {
    const input = document.getElementById('rag-chat-input');
    if (!input || !ragComposerResourcePaths.size) return;
    const marker = input.value.match(/\[已选择\s*\d+\s*个资料：([\s\S]*?)\]\s*$/m);
        if (!marker) {
        // If the user removed the marker line entirely, the explicit
        // selection was intentionally cleared.
        if (!input.value.includes('[已选择')) {
            for (const path of Array.from(ragComposerResourcePaths)) removeRAGTreeSelection(path);
            ragComposerResourcePaths.clear();
            renderRAGComposerResources();
        }
        return;
    }
    const labels = new Set(marker[1].split('、').map(item => item.trim()).filter(Boolean));
    for (const path of Array.from(ragComposerResourcePaths)) {
        const entry = ragResourceEntry(path);
        const label = entry?.name || path.split('/').pop() || path;
        if (!labels.has(label)) {
            ragComposerResourcePaths.delete(path);
            removeRAGTreeSelection(path);
        }
    }
    renderRAGComposerResources();
}

function installRAGResourceDrop() {
    const input = document.getElementById('rag-chat-input');
    if (!input || input.dataset.resourceDropReady === 'true') return;
    input.dataset.resourceDropReady = 'true';
    input.addEventListener('dragover', event => {
        if (typeof getKGDraggedFilePaths !== 'function') return;
        const paths = getKGDraggedFilePaths(event);
        if (!paths.length) return;
        event.preventDefault();
        event.stopPropagation();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        input.classList.add('rag-resource-drag-over');
    });
    input.addEventListener('dragleave', () => input.classList.remove('rag-resource-drag-over'));
    input.addEventListener('drop', event => {
        if (typeof getKGDraggedFilePaths !== 'function') return;
        const paths = getKGDraggedFilePaths(event);
        if (!paths.length) return;
        event.preventDefault();
        event.stopPropagation();
        input.classList.remove('rag-resource-drag-over');
        const valid = paths.filter(path => typeof findFileEntry !== 'function' || findFileEntry(fileStore?.files || [], path));
        valid.forEach(path => ragComposerResourcePaths.add(path));
        if (!valid.length) return;
        const labels = valid.map(path => {
            const entry = typeof findFileEntry === 'function' ? findFileEntry(fileStore?.files || [], path) : null;
            return entry?.name || path.split('/').pop() || path;
        });
        const allPaths = Array.from(ragComposerResourcePaths);
        const allLabels = allPaths.map(path => {
            const entry = typeof findFileEntry === 'function' ? findFileEntry(fileStore?.files || [], path) : null;
            return entry?.name || path.split('/').pop() || path;
        });
        const marker = `[已选择 ${allPaths.length} 个资料：${allLabels.join('、')}]`;
        // Replace the previous status marker instead of appending one marker
        // per drop. The query remains readable and clearly shows the total
        // number of resources that will be sent.
        const current = input.value.split(/\r?\n/).filter(line => !line.trim().startsWith('[已选择 ')).join('\n').trim();
        input.value = current ? `${current}\n\n${marker}` : marker;
        renderRAGComposerResources();
        if (typeof showToast === 'function') showToast(`已定位 ${allPaths.length} 个资料，发送时会完整传入 Agent`, 'success');
    });
    input.addEventListener('input', () => {
        if (!input.value.trim()) {
            ragComposerResourcePaths.clear();
            renderRAGComposerResources();
            return;
        }
        syncRAGComposerResourcesFromText();
    });
    const host = document.getElementById('rag-resource-selection');
    if (host && host.dataset.handlersReady !== 'true') {
        host.dataset.handlersReady = 'true';
        host.addEventListener('click', event => {
            const chip = event.target.closest('.rag-resource-chip');
            if (!chip) return;
            const path = chip.dataset.resourcePath || '';
            ragComposerResourcePaths.delete(path);
            removeRAGTreeSelection(path);
            const marker = input.value.match(/\[已选择\s*\d+\s*个资料：([\s\S]*?)\]\s*$/m);
            if (marker) {
                const labels = marker[1].split('、').map(item => item.trim()).filter(Boolean);
                const entry = ragResourceEntry(path);
                const label = entry?.name || path.split('/').pop() || path;
                const remaining = labels.filter(item => item !== label);
                const replacement = remaining.length ? `[已选择 ${remaining.length} 个资料：${remaining.join('、')}]` : '';
                input.value = input.value.replace(marker[0], replacement).trim();
            }
            renderRAGComposerResources();
        });
    }
    renderRAGComposerResources();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installRAGResourceDrop);
else setTimeout(installRAGResourceDrop, 0);

async function downloadAgentArtifact(taskId, filename = 'draft.md') {
    try {
        const response = await fetch(`${API_BASE}/agent/tasks/${encodeURIComponent(taskId)}/artifact/${filename}`, {
            headers: typeof getAccessHeaders === 'function' ? getAccessHeaders() : {},
        });
        if (!response.ok) throw new Error(`下载失败（${response.status}）`);
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url; link.download = filename; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
        if (typeof showToast === 'function') showToast(err.message, 'error');
    }
}

async function prepareAgentPublish(taskId, channel) {
    try {
        if (channel === 'wechat') {
            // 微信同步分为“准备预览”和“创建草稿”两步。没有封面时
            // 允许用户临时填写 media_id，也可以使用服务端默认封面配置。
            let preview = await api('POST', `/agent/tasks/${encodeURIComponent(taskId)}/wechat/prepare`, {});
            let coverMediaId = preview.preview?.cover_media_id || '';
            if (!coverMediaId) {
                coverMediaId = window.prompt('请输入微信公众号封面 media_id（也可先在服务器配置默认封面）：', '') || '';
            }
            preview = await api('POST', `/agent/tasks/${encodeURIComponent(taskId)}/wechat/prepare`, { cover_media_id: coverMediaId });
            if (!preview.preview?.can_sync) {
                throw new Error((preview.preview?.warnings || []).join('；') || '公众号草稿未达到同步条件');
            }
            const confirmed = window.confirm(`确认同步到微信公众号草稿箱？\n标题：${preview.preview.title}\n正文图片：${preview.preview.image_sources?.length || 0} 张`);
            if (!confirmed) return null;
            const created = await api('POST', `/agent/tasks/${encodeURIComponent(taskId)}/wechat/sync-draft`, { cover_media_id: coverMediaId });
            if (typeof showToast === 'function') showToast('公众号草稿已创建，请在微信后台审核', 'success');
            return created;
        }
        const data = await api('POST', `/agent/tasks/${encodeURIComponent(taskId)}/publish/prepare?channel=${channel}`, {});
        const blob = new Blob([JSON.stringify(data.materials || {}, null, 2)], { type: 'application/json;charset=utf-8' });
        const url = URL.createObjectURL(blob); const link = document.createElement('a');
        link.href = url; link.download = `${channel}-publish-materials.json`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        if (typeof showToast === 'function') showToast('已生成发布素材包，请人工审核后发布', 'success');
    } catch (err) { if (typeof showToast === 'function') showToast(err.message, 'error'); }
}

async function publishAgentToWechat(taskId) {
    try {
        const state = await api('GET', `/agent/tasks/${encodeURIComponent(taskId)}/wechat/status`);
        if (!state.wechat?.media_id) throw new Error('请先同步公众号草稿');
        const approved = window.confirm('确认申请发布这篇公众号文章？请先确认标题、摘要、正文、图片和封面。');
        if (!approved) return null;
        const approval = await api('POST', `/agent/tasks/${encodeURIComponent(taskId)}/wechat/approval`, { media_id: state.wechat.media_id });
        const finalConfirm = window.confirm('这是最后一次确认。确认提交到微信公众号发布接口吗？');
        if (!finalConfirm) return null;
        const submitted = await api('POST', `/agent/tasks/${encodeURIComponent(taskId)}/publish/wechat/submit`, {
            confirm: true, media_id: state.wechat.media_id, approval_token: approval.approval_token,
        });
        if (typeof showToast === 'function') showToast('公众号发布已提交，正在查询状态', 'success');
        await new Promise(resolve => setTimeout(resolve, 1200));
        const status = await api('GET', `/mcp/wechat/publish/${encodeURIComponent(submitted.publish_id)}`);
        if (typeof showToast === 'function') showToast(`公众号发布状态：${status.status?.status || status.status?.errmsg || '已提交'}`, 'success');
        return submitted;
    } catch (err) {
        if (typeof showToast === 'function') showToast(err.message || '公众号发布失败', 'error');
        return null;
    }
}

function toggleRAGAgentMode() {
    const previousMode = ragAgentMode ? 'agent' : 'rag';
    ragAgentMode = !ragAgentMode;
    if (typeof recordTelemetry === 'function') recordTelemetry('agent_mode_toggle', {from_mode: previousMode, to_mode: ragAgentMode ? 'agent' : 'rag'});
    const btn = document.getElementById('rag-agent-toggle');
    if (btn) {
        btn.classList.toggle('active', ragAgentMode);
        btn.style.color = ragAgentMode ? 'var(--accent)' : '';
        btn.title = ragAgentMode ? 'Agent 工作台已开启：生成可编辑内容草稿' : '切换 Agent 工作台';
    }
    const indicator = document.getElementById('rag-mode-indicator');
    if (indicator) {
        indicator.textContent = ragAgentMode ? '模式：Agent RAG' : '模式：标准 RAG';
        indicator.classList.toggle('agent', ragAgentMode);
    }
    const options = document.getElementById('rag-agent-options');
    if (options) options.style.display = ragAgentMode ? 'flex' : 'none';
    if (typeof showToast === 'function') {
        showToast(ragAgentMode ? '已切换到 Agent 工作台，适合生成汇总、报告和文章' : '已切换到标准 RAG 问答', 'success');
    }
}

async function runRAGAgentTask(goal, assistantIndex = -1, composerPaths = []) {
    const template = document.getElementById('rag-agent-template')?.value || 'auto';
    const outputFormat = document.getElementById('rag-agent-format')?.value || 'markdown';
    const payload = {
        goal,
        template,
        output_format: outputFormat,
        kb_id: ragSelectedKB || '',
        kb_ids: ragSelectedKBs.size > 0 ? Array.from(ragSelectedKBs) : undefined,
        top_k: Math.max(5, ragTopK),
        web_search_enabled: ragWebSearchEnabled,
        session_id: ragSessionId || '',
    };
    // Preserve Ctrl/Cmd multi-selection from the file tree. Previously the
    // currently opened file always overwrote this list, so a multi-document
    // request reached the backend with only the first article.
    const openedPath = fileStore?.openedFile?.path || '';
    const openedEntry = openedPath && typeof findFileEntry === 'function'
        ? findFileEntry(fileStore?.files || [], openedPath) : null;
    const selectedPath = fileStore?.selectedFile?.path || '';
    const selectedEntry = selectedPath && typeof findFileEntry === 'function'
        ? findFileEntry(fileStore?.files || [], selectedPath) : null;
    const treeSelectedPaths = Array.from(window._fileTreeSelectedPaths || (typeof _fileTreeSelectedPaths !== 'undefined' ? _fileTreeSelectedPaths : []))
        .filter(path => {
            const entry = typeof findFileEntry === 'function' ? findFileEntry(fileStore?.files || [], path) : null;
            // Keep directories as explicit hard scopes. Selecting a date
            // folder is a first-class multi-document review request; the
            // previous file-only filter silently dropped that selection.
            return Boolean(entry);
        });
    const selectedPaths = Array.from(new Set([...treeSelectedPaths, ...composerPaths, ...ragComposerResourcePaths]));
    const activePath = openedPath && (!openedEntry || !openedEntry.isDirectory)
        ? openedPath
        : (selectedPath && (!selectedEntry || !selectedEntry.isDirectory) ? selectedPath : '');
    if (selectedPaths.length > 0) payload.selected_paths = selectedPaths;
    else if (activePath) payload.selected_paths = [activePath];
    // Keep the selection visible in the task payload/trace. This is useful
    // both for users diagnosing a missed article and for online evaluation.
    payload.selected_paths_count = (payload.selected_paths || []).length;
    // The explicit selection is now captured in the payload; clear only the
    // transient composer state so the next message does not inherit it.
    ragComposerResourcePaths.clear();
    renderRAGComposerResources();
    const created = await api('POST', '/agent/tasks', payload);
    if (typeof recordTelemetry === 'function') recordTelemetry('query_submitted', {mode: 'agent', template, resource_count: (payload.selected_paths || []).length, query_length: String(goal || '').length}, {task_id: created.task_id});
    let task = null;
    let lastProgressKey = '';
    for (let i = 0; i < 120; i += 1) {
        await new Promise(resolve => setTimeout(resolve, 1200));
        task = await api('GET', `/agent/tasks/${created.task_id}`);
        const stage = task.message || 'Agent 执行中';
        const assistant = assistantIndex >= 0 ? ragConversation[assistantIndex] : null;
        if (assistant) {
            const progress = Math.round(task.progress || 0);
            const progressKey = `${stage}|${progress}`;
            assistant.metadata = { ...(assistant.metadata || {}), agent_stage: stage, agent_progress: progress };
            assistant.content = `Agent 执行中\n\n${stage}\n\n进度：${progress}%`;
            // Agent progress must not rebuild the entire conversation. Re-rendering
            // every message repeatedly parses Markdown and citations and causes the
            // visible chat to flicker as the session grows.
            if (progressKey !== lastProgressKey) {
                lastProgressKey = progressKey;
                updateRAGAgentProgress(assistantIndex, stage, progress);
            }
        }
        if (task.status === 'completed' || task.status === 'failed') break;
    }
    if (!task || task.status !== 'completed') {
        const failure = new Error(task?.error || 'Agent 任务超时');
        failure.taskId = created.task_id;
        throw failure;
    }
    if (outputFormat === 'pdf') {
        const exported = await api('POST', `/agent/tasks/${created.task_id}/export?format=pdf`, {});
        task.result = { ...(task.result || {}), export_path: exported.path, export_format: 'pdf' };
    }
    const trace = await api('GET', `/agent/tasks/${created.task_id}/trace`);
    return {
        draft: task.result?.draft || 'Agent 未生成草稿',
        evidence: task.result?.evidence || [],
        images: task.result?.images || [],
        quality: task.result?.quality || trace.quality || {},
        toolCalls: task.result?.tool_calls || trace.tool_calls || [],
        taskId: created.task_id,
        format: outputFormat,
        trace,
    };
}

// Retry keeps the original immutable task payload on the server, so a
// transient model/network failure can be retried without re-selecting files.
async function retryRAGAgentTask(taskId, assistantIndex = -1) {
    if (!taskId) return;
    try {
        await api('POST', `/agent/tasks/${encodeURIComponent(taskId)}/retry`, {});
        for (let i = 0; i < 120; i += 1) {
            await new Promise(resolve => setTimeout(resolve, 1200));
            const task = await api('GET', `/agent/tasks/${encodeURIComponent(taskId)}`);
            if (assistantIndex >= 0) updateRAGAgentProgress(assistantIndex, task.message || 'Agent 重试中', task.progress || 0);
            if (task.status === 'completed') {
                const trace = await api('GET', `/agent/tasks/${encodeURIComponent(taskId)}/trace`);
                const content = task.result?.draft || 'Agent 已完成重试';
                if (assistantIndex >= 0 && ragConversation[assistantIndex]) {
                    ragConversation[assistantIndex] = { role: 'assistant', content, chunks: task.result?.evidence || [], metadata: { agent: true, task_id: taskId, trace, quality: task.result?.quality || trace.quality, evidence: task.result?.evidence || [] } };
                    renderRAGConversation();
                }
                return;
            }
            if (task.status === 'failed') throw new Error(task.error || 'Agent 重试失败');
        }
        throw new Error('Agent 重试超时');
    } catch (err) {
        if (typeof showToast === 'function') showToast(err.message || 'Agent 重试失败', 'error');
    }
}

async function submitRAGAgentFeedback(taskId, rating = 'negative') {
    if (!taskId) return;
    const reason = rating === 'negative' ? (window.prompt('请简要说明问题（可选）：', '') || '') : '';
    try {
        await api('POST', `/agent/tasks/${encodeURIComponent(taskId)}/feedback`, { rating, reason });
        if (typeof showToast === 'function') showToast('反馈已记录，将用于后续评测和优化', 'success');
    } catch (err) { if (typeof showToast === 'function') showToast(err.message || '反馈提交失败', 'error'); }
}

async function showRAGAgentTaskHistory() {
    try {
        const data = await api('GET', '/agent/tasks?limit=40');
        const rows = data.tasks || [];
        const old = document.getElementById('rag-agent-task-history-dialog');
        old?.remove();
        const statusLabel = {pending: '排队中', processing: '执行中', completed: '已完成', failed: '失败', cancelled: '已取消'};
        const body = rows.length ? rows.map(row => {
            const result = row.result || {};
            const perception = result.perception || {};
            const retry = ['failed', 'cancelled'].includes(row.status) ? `<button class="dialog-btn" onclick="retryRAGAgentTask('${escapeHtml(row.task_id)}');document.getElementById('rag-agent-task-history-dialog')?.remove()">重试</button>` : '';
            const open = result.draft_path ? `<button class="dialog-btn" onclick="downloadAgentArtifact('${escapeHtml(row.task_id)}','draft.md')">下载</button>` : '';
            return `<div class="rag-task-history-row"><div><b>${escapeHtml(perception.deliverable || perception.intent || 'Agent 任务')}</b><div class="rag-task-history-goal">${escapeHtml((result._payload?.goal || row.message || '').slice(0, 100))}</div></div><span class="rag-task-history-status ${escapeHtml(row.status)}">${statusLabel[row.status] || row.status}</span><div class="rag-task-history-actions">${open}${retry}</div></div>`;
        }).join('') : '<div class="rag-task-history-empty">暂无 Agent 任务记录</div>';
        const overlay = document.createElement('div');
        overlay.className = 'new-item-dialog'; overlay.id = 'rag-agent-task-history-dialog';
        overlay.innerHTML = `<div class="dialog-box rag-task-history-dialog"><div class="dialog-title">Agent 任务记录 <button class="icon-btn" onclick="this.closest('.new-item-dialog').remove()">×</button></div><div class="rag-task-history-list">${body}</div><div class="dialog-actions"><button class="dialog-btn" onclick="this.closest('.new-item-dialog').remove()">关闭</button></div></div>`;
        document.body.appendChild(overlay);
    } catch (err) { if (typeof showToast === 'function') showToast(err.message || '任务记录加载失败', 'error'); }
}

async function showKnowledgeReview() {
    try {
        const data = await api('GET', '/knowledge-review/daily?limit=5');
        const old = document.getElementById('knowledge-review-dialog'); old?.remove();
        const questions = (data.questions || []).map(item => `<div class="knowledge-review-row"><div><b>${escapeHtml(item.title)}</b>${item.handled ? '<span class="knowledge-review-handled">已处理</span>' : ''}<div class="knowledge-review-question">${escapeHtml(item.question)}</div><small>${escapeHtml(item.source_path)}</small></div><div class="knowledge-review-actions"><button class="dialog-btn" onclick="document.getElementById('rag-chat-input').value=${JSON.stringify(item.suggested_goal)};document.getElementById('knowledge-review-dialog')?.remove();document.getElementById('rag-chat-input')?.focus()">生成任务</button>${!item.handled ? `<button class="dialog-btn" onclick="markKnowledgeReviewHandled('${escapeHtml(item.source_path)}')">标记已处理</button>` : ''}</div></div>`).join('');
        const overlay = document.createElement('div'); overlay.className = 'new-item-dialog'; overlay.id = 'knowledge-review-dialog';
        overlay.innerHTML = `<div class="dialog-box knowledge-review-dialog"><div class="dialog-title">今日知识复习 <button class="icon-btn" onclick="this.closest('.new-item-dialog').remove()">×</button></div><div class="knowledge-review-meta">发现 ${Number(data.total_source_files || 0)} 个资料，其中 ${Number(data.unreused_source_files || 0)} 个尚未被 Agent 任务复用。</div><div class="knowledge-review-list">${questions || '<div class="rag-task-history-empty">暂无可复习资料</div>'}</div></div>`;
        document.body.appendChild(overlay);
    } catch (err) { if (typeof showToast === 'function') showToast(err.message || '复习题加载失败', 'error'); }
}

async function markKnowledgeReviewHandled(sourcePath) {
    try {
        await api('POST', '/knowledge-review/handled', { source_path: sourcePath, action: 'reviewed' });
        showToast('已标记，后续复习将优先展示其他资料', 'success');
        showKnowledgeReview();
    } catch (err) { showToast(err.message || '标记失败', 'error'); }
}

function updateRAGAgentProgress(index, stage, progress) {
    const messageEl = document.getElementById(`rag-msg-${index}`);
    const bubble = messageEl?.querySelector('.chat-bubble');
    if (!bubble) return;
    bubble.innerHTML = `<div class="rag-agent-progress"><div class="rag-agent-progress-label">Agent 执行中</div><div class="rag-agent-progress-stage">${escapeHtml(stage)}</div><div class="rag-agent-progress-track"><span style="width:${Math.max(0, Math.min(100, progress))}%"></span></div><div class="rag-agent-progress-percent">${progress}%</div></div>`;
}

function renderAgentExecutionSummary(metadata) {
    const quality = metadata?.quality || metadata?.trace?.quality || {};
    const tools = metadata?.tool_calls || metadata?.trace?.tool_calls || [];
    const evidenceCount = Number(metadata?.evidence_count ?? metadata?.evidence?.length ?? 0);
    if (!metadata?.agent) return '';
    const toolText = tools.length ? tools.map(item => `${item.name || 'tool'}:${item.status || 'done'}`).join(' · ') : '无工具轨迹';
    const perception = metadata?.perception || metadata?.trace?.perception || {};
    const route = perception.intent ? `${perception.deliverable || perception.intent} · ${perception.route === 'generic' ? '通用路径' : '内置路径'}` : '已完成路由';
    const reason = perception.intent_reason ? `（${perception.intent_reason}）` : '';
    const selectedFiles = perception?.constraints?.selected_file_paths || [];
    const selectedText = selectedFiles.length
        ? `已选文件：${selectedFiles.length} 篇${selectedFiles.length > 1 ? '（多篇流程）' : ''}`
        : '已选文件：未指定（按授权范围检索）';
    const selectedList = selectedFiles.length > 1
        ? `<div>本次文章：${selectedFiles.map(item => escapeHtml(item)).join('、')}</div>` : '';
    const coverage = metadata?.coverage || metadata?.trace?.coverage || {};
    const coverageText = Number.isFinite(Number(coverage.requested_count))
        ? `<div>文档覆盖：已读取 ${Number(coverage.read_count || 0)}/${Number(coverage.requested_count)} 篇${(coverage.missing || []).length ? `；未读取：${coverage.missing.map(item => escapeHtml(item)).join('、')}` : ''}</div>`
        : '';
    const feedback = metadata?.task_id ? `<div class="rag-agent-feedback"><button type="button" onclick="submitRAGAgentFeedback('${escapeHtml(metadata.task_id)}','positive')">有帮助</button><button type="button" onclick="submitRAGAgentFeedback('${escapeHtml(metadata.task_id)}','negative')">不满意</button></div>` : '';
    return `<details class="rag-agent-trace"><summary>执行详情：${escapeHtml(route)} · ${evidenceCount} 条证据 · 引用覆盖率 ${Math.round(Number(quality.citation_coverage || 0) * 100)}%</summary><div class="rag-agent-trace-body"><div>意图判断：${escapeHtml(route)}${escapeHtml(reason)}</div><div>资料范围：${escapeHtml((perception?.constraints?.scope || []).join('、') || '默认范围')}</div><div>${selectedText}</div>${selectedList}${coverageText}<div>工具：${escapeHtml(toolText)}</div>${quality.passed === false ? '<div class="rag-answer-warning">引用核验未通过，请补充资料或人工复核。</div>' : ''}${feedback}</div></details>`;
}
let ragSessionList = [];
let ragRightMode = 'sessions';
let ragCitationMessageIndex = -1;
let ragActiveCitationIndex = 0;
let ragTurnIndexScrollTimer = null;
let ragChunkConfig = {
    chunk_size: 900,
    chunk_overlap: 120,
    child_chunk_size: 350,
    child_chunk_overlap: 50,
};

let ragKBImportDialog = null;
let ragKBImportFiles = [];
let ragKBImportSelected = new Set();
let ragKBImportTarget = { kbId: '', kbName: '' };
let ragKBImportFormatFilter = 'all';
let ragKBListRequestId = 0;
const ragPendingMutations = new Set();
const RAG_IMPORTABLE_EXTS = new Set([
    '.md', '.markdown', '.txt', '.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv',
    '.json', '.yaml', '.yml', '.xml', '.html', '.htm', '.rtf', '.odt', '.epub',
    '.pptx', '.ppt', '.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.webp', '.gif', '.svg',
]);

function beginRAGMutation(kbId, actionLabel) {
    const key = `kb:${kbId}`;
    if (ragPendingMutations.has(key)) {
        showToast(`该知识库正在执行其他操作，请等待完成后再${actionLabel}`, 'warning');
        return false;
    }
    ragPendingMutations.add(key);
    return true;
}

function endRAGMutation(kbId) {
    ragPendingMutations.delete(`kb:${kbId}`);
}

function flattenFileResourceTreeForImport(items, parentPath = '') {
    const result = [];
    for (const item of items || []) {
        if (!item) continue;
        const currentPath = item.path || (parentPath ? `${parentPath}/${item.name || ''}` : item.name || '');
        if (item.isDirectory) {
            result.push(...flattenFileResourceTreeForImport(item.children || [], currentPath));
            continue;
        }
        const name = item.name || currentPath.split('/').pop();
        const ext = `.${String(name || '').split('.').pop().toLowerCase()}`;
        if (!RAG_IMPORTABLE_EXTS.has(ext)) continue;
        result.push({
            path: currentPath,
            name,
            size: item.size || 0,
            depth: String(currentPath).split('/').length - 1,
            source: item.source || 'server',
        });
    }
    return result;
}

function renderKBImportSelectionCount() {
    const el = document.getElementById('rag-kb-import-selection-count');
    if (!el) return;
    const selectedBytes = ragKBImportFiles.reduce((total, item) => (
        ragKBImportSelected.has(item.path) ? total + Number(item.size || 0) : total
    ), 0);
    el.innerHTML = `<strong>${ragKBImportSelected.size}</strong> / ${ragKBImportFiles.length} 个文件 · ${formatKBImportFileSize(selectedBytes)}`;
}

function formatKBImportFileSize(bytes) {
    const size = Number(bytes || 0);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function getKBImportExtension(item) {
    const name = String(item?.name || '');
    const dot = name.lastIndexOf('.');
    return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

function getFilteredKBImportFiles(filterText = '') {
    const q = String(filterText || '').trim().toLowerCase();
    return ragKBImportFiles.filter(item => {
        const matchesText = !q || `${item.path} ${item.name}`.toLowerCase().includes(q);
        const matchesFormat = ragKBImportFormatFilter === 'all' || getKBImportExtension(item) === ragKBImportFormatFilter;
        return matchesText && matchesFormat;
    });
}

function setKBImportFormatFilter(value) {
    ragKBImportFormatFilter = value || 'all';
    renderKBImportFileList(document.getElementById('rag-kb-import-search')?.value || '');
}

function buildKBImportFormatOptions() {
    const counts = new Map();
    for (const item of ragKBImportFiles) {
        const ext = getKBImportExtension(item) || '.file';
        counts.set(ext, (counts.get(ext) || 0) + 1);
    }
    return [...counts.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([ext, count]) => `<option value="${escapeHtml(ext)}">${escapeHtml(ext.replace(/^\./, '').toUpperCase())} (${count})</option>`)
        .join('');
}

function renderKBImportFileList(filterText = '') {
    const list = document.getElementById('rag-kb-import-file-list');
    if (!list) return;

    const files = getFilteredKBImportFiles(filterText);

    if (!files.length) {
        list.innerHTML = '<div class="rag-kb-import-empty">没有符合条件的文件</div>';
        renderKBImportSelectionCount();
        return;
    }

    list.innerHTML = files.map(item => {
        const checked = ragKBImportSelected.has(item.path);
        const ext = getKBImportExtension(item);
        const pathArg = escapeHtml(JSON.stringify(item.path));
        return `
            <label class="rag-kb-import-row">
                <input type="checkbox" data-import-path="${escapeHtml(item.path)}" ${checked ? 'checked' : ''} onchange="toggleKBImportFileSelection(${pathArg}, this.checked)">
                <span class="rag-kb-import-ext">${escapeHtml(ext.replace(/^\./, '') || 'file')}</span>
                <span class="rag-kb-import-file">
                    <strong class="rag-kb-import-name">${escapeHtml(item.name)}</strong>
                    <small class="rag-kb-import-path">${escapeHtml(item.path)}</small>
                </span>
                <span class="rag-kb-import-size">${formatKBImportFileSize(item.size)}</span>
            </label>`;
    }).join('');
    renderKBImportSelectionCount();
    // Keep the displayed count and the internal selection set aligned with
    // the actual checkbox DOM after every filtered/re-rendered list update.
    syncKBImportSelectionFromDOM();
}

function toggleKBImportFileSelection(path, checked) {
    if (checked) ragKBImportSelected.add(path);
    else ragKBImportSelected.delete(path);
    renderKBImportSelectionCount();
}

function syncKBImportSelectionFromDOM() {
    const dialog = document.getElementById('rag-kb-import-dialog');
    if (!dialog) return;
    // The checkbox is the source of truth at submit time. This also recovers
    // from browser autofill or an interrupted render that left the Set stale.
    const visibleInputs = [...dialog.querySelectorAll('input[type="checkbox"][data-import-path]')];
    if (!visibleInputs.length) return;
    const visiblePaths = new Set(visibleInputs.map(input => input.dataset.importPath || ''));
    visiblePaths.forEach(path => ragKBImportSelected.delete(path));
    visibleInputs.forEach(input => {
        if (input.checked && input.dataset.importPath) ragKBImportSelected.add(input.dataset.importPath);
    });
    renderKBImportSelectionCount();
}

function toggleAllKBImportFiles(checked, visibleOnly = false) {
    const visibleFiles = getFilteredKBImportFiles(document.getElementById('rag-kb-import-search')?.value || '');
    if (!checked) {
        if (visibleOnly) visibleFiles.forEach(item => ragKBImportSelected.delete(item.path));
        else ragKBImportSelected.clear();
    } else {
        const files = visibleOnly ? visibleFiles : ragKBImportFiles;
        files.forEach(item => ragKBImportSelected.add(item.path));
    }
    renderKBImportFileList(document.getElementById('rag-kb-import-search')?.value || '');
}

function closeKBResourceImportDialog() {
    if (ragKBImportDialog) {
        ragKBImportDialog.remove();
        ragKBImportDialog = null;
    }
    ragKBImportFiles = [];
    ragKBImportSelected = new Set();
    ragKBImportTarget = { kbId: '', kbName: '' };
    ragKBImportFormatFilter = 'all';
}

async function openKBResourceImportDialog(kbId, kbName = '') {
    if (!requireWriteAccess()) return;
    if (!kbId) return;
    if (typeof loadServerFileResources === 'function' && (!fileStore?.files || !fileStore.files.length)) {
        await loadServerFileResources({ silent: true });
    }

    closeKBResourceImportDialog();
    ragKBImportFiles = flattenFileResourceTreeForImport(fileStore?.files || []);
    if (!ragKBImportFiles.length) {
        showToast('文档库中没有可导入的文件', 'warning');
        return;
    }

    ragKBImportTarget = { kbId, kbName: kbName || '核心知识库' };
    ragKBImportSelected = new Set(ragKBImportFiles.map(item => item.path));
    ragKBImportFormatFilter = 'all';
    const overlay = document.createElement('div');
    overlay.className = 'new-item-dialog';
    overlay.id = 'rag-kb-import-dialog';
    overlay.innerHTML = `
        <div class="dialog-box rag-kb-import-dialog-box">
            <div class="rag-kb-import-head">
                <div class="rag-kb-import-head-icon">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
                </div>
                <div>
                    <div class="dialog-title">批量入库</div>
                    <div class="rag-kb-import-target">目标：${escapeHtml(ragKBImportTarget.kbName || '知识库')}</div>
                </div>
            </div>
            <div class="rag-kb-import-pipeline" aria-label="入库处理流程">
                <span>格式解析</span><i>→</i><span>内容清洗</span><i>→</i><span>结构切分</span><i>→</i><span>向量索引</span>
                <em>重复文档自动跳过</em>
            </div>
            <div class="rag-kb-import-toolbar">
                <div class="rag-kb-import-search-wrap">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                    <input id="rag-kb-import-search" class="rag-kb-import-search" placeholder="搜索文件名或路径" oninput="renderKBImportFileList(this.value)">
                </div>
                <select id="rag-kb-import-format" class="rag-kb-import-format" onchange="setKBImportFormatFilter(this.value)">
                    <option value="all">全部格式 (${ragKBImportFiles.length})</option>
                    ${buildKBImportFormatOptions()}
                </select>
                <button class="dialog-btn" onclick="toggleAllKBImportFiles(true, true)">全选当前</button>
                <button class="dialog-btn" onclick="toggleAllKBImportFiles(false)">清空</button>
            </div>
            <div class="rag-kb-import-columns"><span></span><span>类型</span><span>文件</span><span>大小</span></div>
            <div id="rag-kb-import-file-list" class="rag-kb-import-list"></div>
            <div class="rag-kb-import-footer">
                <span id="rag-kb-import-selection-count" class="rag-kb-import-count"></span>
                <div class="dialog-actions">
                    <button class="dialog-btn" onclick="closeKBResourceImportDialog()">取消</button>
                    <button class="dialog-btn primary" id="rag-kb-import-confirm" onclick="confirmKBResourceImport()">开始入库</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    ragKBImportDialog = overlay;
    renderKBImportFileList('');
    setTimeout(() => document.getElementById('rag-kb-import-search')?.focus(), 60);
}

async function confirmKBResourceImport() {
    if (!requireWriteAccess()) return;
    if (!ragKBImportTarget.kbId) return;
    const targetKBId = ragKBImportTarget.kbId;
    syncKBImportSelectionFromDOM();
    const paths = Array.from(ragKBImportSelected);
    if (!paths.length) {
        showToast('请至少选择一个文件', 'warning');
        return;
    }
    if (!beginRAGMutation(targetKBId, '入库')) return;

    const progressId = 'rag-kb-import-' + Date.now();
    const confirmBtn = document.getElementById('rag-kb-import-confirm');
    try {
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.textContent = '正在入库...';
        }
        const batchSize = 4;
        const totals = {
            imported_count: 0,
            duplicate_count: 0,
            skipped_count: 0,
            failed_count: 0,
        };
        showProgress(progressId, `正在批量入库 0/${paths.length} 个文件...`, 5);
        for (let offset = 0; offset < paths.length; offset += batchSize) {
            const batchPaths = paths.slice(offset, offset + batchSize);
            const data = await api('POST', `/kb/${targetKBId}/import/file-resources`, {
                paths: batchPaths,
            }, 240000);
            totals.imported_count += data.imported_count || 0;
            totals.duplicate_count += data.duplicate_count || 0;
            totals.skipped_count += data.skipped_count || 0;
            totals.failed_count += data.failed_count || 0;
            batchPaths.forEach(path => ragKBImportSelected.delete(path));
            const completed = Math.min(offset + batchPaths.length, paths.length);
            const progress = 5 + Math.round((completed / paths.length) * 90);
            showProgress(progressId, `正在批量入库 ${completed}/${paths.length} 个文件...`, progress);
        }
        showProgress(progressId, '批量入库完成', 100);
        setTimeout(() => hideProgress(progressId), 1200);
        const imported = totals.imported_count;
        const duplicated = totals.duplicate_count;
        const skipped = totals.skipped_count;
        const failed = totals.failed_count;
        showToast(
            `批量入库完成：成功 ${imported}，重复 ${duplicated}，跳过 ${skipped}，失败 ${failed}`,
            failed === 0 ? 'success' : 'warning',
        );
        closeKBResourceImportDialog();
        await loadKBList();
    } catch (err) {
        hideProgress(progressId);
        renderKBImportFileList(document.getElementById('rag-kb-import-search')?.value || '');
        const remaining = ragKBImportSelected.size;
        const reason = typeof describeNetworkError === 'function'
            ? describeNetworkError(err)
            : (err.message || '未知错误');
        showToast(`批量入库中断，剩余 ${remaining} 个文件可继续：${reason}`, 'error');
    } finally {
        endRAGMutation(targetKBId);
        if (confirmBtn?.isConnected) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = '开始入库';
        }
    }
}

async function initRAGPage() {
    if (ragInitialized) return;
    ragInitialized = true;
    bindRAGEventHandlers();
    resetRAGConversation();
    showRAGSessionPanel();
    await loadRAGStatus();
    await loadKBList();
    await loadRAGSessions();
}

function bindRAGEventHandlers() {
    if (window.__ragEventHandlersBound) return;
    window.__ragEventHandlersBound = true;

    document.addEventListener('click', (event) => {
        const createKBBtn = event.target.closest('[data-rag-create-kb]');
        if (createKBBtn) {
            event.preventDefault();
            event.stopPropagation();
            showCreateKBDlg();
            return;
        }

        const docDeleteBtn = event.target.closest('[data-rag-delete-doc]');
        if (docDeleteBtn) {
            event.preventDefault();
            event.stopPropagation();
            deleteKBDoc(
                docDeleteBtn.dataset.kbId || '',
                docDeleteBtn.dataset.docId || '',
                docDeleteBtn.dataset.docName || ''
            );
            return;
        }

        const vectorizeDocBtn = event.target.closest('[data-rag-vectorize-doc]');
        if (vectorizeDocBtn) {
            event.preventDefault();
            event.stopPropagation();
            vectorizeKBDocuments(
                vectorizeDocBtn.dataset.kbId || '',
                [vectorizeDocBtn.dataset.docId || ''],
                false
            );
            return;
        }

        const vectorizeKbBtn = event.target.closest('[data-rag-vectorize-kb]');
        if (vectorizeKbBtn) {
            event.preventDefault();
            event.stopPropagation();
            vectorizeKBDocuments(vectorizeKbBtn.dataset.kbId || '', null, false);
            return;
        }

        const chunkBtn = event.target.closest('[data-rag-kb-chunks]');
        if (chunkBtn) {
            event.preventDefault();
            event.stopPropagation();
            showKBChunks(chunkBtn.dataset.kbId || '', chunkBtn.dataset.kbName || '');
            return;
        }

        const docBtn = event.target.closest('[data-rag-doc-chunks]');
        if (docBtn) {
            event.preventDefault();
            event.stopPropagation();
            showKBDocumentChunks(docBtn.dataset.kbId || '', docBtn.dataset.docId || '', docBtn.dataset.docName || '');
            return;
        }

        const sessionDeleteBtn = event.target.closest('[data-rag-delete-session]');
        if (sessionDeleteBtn) {
            event.preventDefault();
            event.stopPropagation();
            deleteRAGSession(sessionDeleteBtn.dataset.sessionId || '');
        }
    });
}

async function loadRAGStatus() {
    try {
        const data = await api('GET', '/rag/status');
        if (data?.chunking) {
            ragChunkConfig = {
                ...ragChunkConfig,
                ...data.chunking,
            };
        }
    } catch (e) {
        // Keep local defaults when the backend status endpoint is unavailable.
    }
}

async function initRAGSession() {
    try {
        const data = await api('POST', '/rag/session/create');
        ragSessionId = data.session_id || '';
    } catch (e) {
        ragSessionId = '';
    }
}

function createRAGWelcomeMessage() {
    return {
        role: 'assistant',
        content: '你好，我是知识库问答助手。请先在知识库中完成文档入库和向量化，然后向我提问。',
        chunks: [],
        metadata: {},
    };
}

function resetRAGConversation() {
    ragConversation = [createRAGWelcomeMessage()];
    renderRAGConversation();
}

function toggleWebSearch() {
    ragWebSearchEnabled = !ragWebSearchEnabled;
    const color = ragWebSearchEnabled ? 'var(--primary)' : 'var(--text-muted)';
    const btn = document.getElementById('rag-web-search-btn');
    const toolbarBtn = document.getElementById('rag-web-search-toolbar');
    if (btn) btn.style.color = color;
    if (toolbarBtn) toolbarBtn.style.color = color;
    showToast(ragWebSearchEnabled ? '联网搜索已开启' : '联网搜索已关闭', ragWebSearchEnabled ? 'success' : 'info');
}

function setRAGKnowledgeBase(kbId) {
    ragSelectedKB = kbId;
    if (kbId) ragSelectedKBs.add(kbId);
}

function toggleMultiKBSelect() {
    const dropdown = document.getElementById('rag-kb-multi-dropdown');
    if (!dropdown) return;
    const visible = dropdown.style.display !== 'none';
    if (visible) {
        dropdown.style.display = 'none';
        return;
    }
    // 构建多选列表
    dropdown.innerHTML = '';
    const allItem = document.createElement('label');
    allItem.className = 'rag-kb-multi-option';
    const allChecked = ragSelectedKBs.size === 0;
    allItem.innerHTML = `<input type="checkbox" ${allChecked ? 'checked' : ''} onchange="toggleAllKBs(this.checked)"> 全部知识库`;
    dropdown.appendChild(allItem);
    ragKBList.forEach(kb => {
        const item = document.createElement('label');
        item.className = 'rag-kb-multi-option';
        const checked = ragSelectedKBs.has(kb.kb_id);
        item.innerHTML = `<input type="checkbox" ${checked ? 'checked' : ''} onchange="toggleKBSelect('${kb.kb_id}', this.checked)"> ${escapeHtml(kb.name)}`;
        dropdown.appendChild(item);
    });
    dropdown.style.display = 'block';
    // 点击外部关闭
    setTimeout(() => {
        const close = (e) => {
            if (!dropdown.contains(e.target) && e.target.id !== 'rag-kb-multi-btn') {
                dropdown.style.display = 'none';
                document.removeEventListener('click', close);
            }
        };
        document.addEventListener('click', close);
    }, 0);
}

function toggleAllKBs(checked) {
    ragSelectedKBs.clear();
    ragSelectedKB = '';
    updateMultiKBLabel();
}

function toggleKBSelect(kbId, checked) {
    if (checked) {
        ragSelectedKBs.add(kbId);
        ragSelectedKB = kbId;
    } else {
        ragSelectedKBs.delete(kbId);
        if (ragSelectedKB === kbId) ragSelectedKB = ragSelectedKBs.values().next().value || '';
    }
    updateMultiKBLabel();
}

function updateMultiKBLabel() {
    const label = document.getElementById('rag-kb-multi-label');
    if (!label) return;
    if (ragSelectedKBs.size === 0) {
        label.textContent = '全部知识库';
    } else if (ragSelectedKBs.size === 1) {
        const kb = ragKBList.find(k => k.kb_id === Array.from(ragSelectedKBs)[0]);
        label.textContent = kb ? kb.name : '1 个知识库';
    } else {
        label.textContent = `${ragSelectedKBs.size} 个知识库`;
    }
}

async function loadKBList() {
    const requestId = ++ragKBListRequestId;
    const container = document.getElementById('kb-list-container');
    if (container) {
        container.innerHTML = '<div class="rag-kb-empty"><p>正在加载知识库...</p></div>';
    }
    try {
        const data = await api('GET', '/kb/list');
        if (requestId !== ragKBListRequestId) return;
        ragKBList = (data.bases || []).sort((a, b) => Number(!!b.is_core) - Number(!!a.is_core));
        await Promise.all(ragKBList.map(kb => attachLocalKBStats(kb)));
        renderKBSelect();
        renderKBList();
        if (typeof LocalDB !== 'undefined') {
            Promise.allSettled(ragKBList.map(kb => LocalDB.saveKnowledgeBase(kb)))
                .then(results => {
                    if (results.some(result => result.status === 'rejected')) {
                        console.warn('部分知识库本地缓存写入失败，服务器数据仍可正常使用');
                    }
                });
        }
    } catch (e) {
        if (requestId !== ragKBListRequestId) return;
        console.error('加载知识库列表失败', e);
        ragKBList = [];
        renderKBSelect();
        if (container) {
            container.innerHTML = `
                <div class="rag-kb-empty rag-kb-load-error">
                    <p>知识库加载失败：${escapeHtml(e.message || '服务器暂时不可用')}</p>
                    <button class="rag-kb-inline-action" type="button" onclick="loadKBList()">重新加载</button>
                </div>`;
        }
    }
}

async function attachLocalKBStats(kb) {
    try {
        const localDocs = await LocalDB.getKBDocuments(kb.kb_id);
        const localChunks = await LocalDB.getKBChunks(kb.kb_id);
        kb.local_doc_count = localDocs.length;
        kb.local_chunk_count = localChunks.length;
        kb.has_local_pending = localDocs.length > 0 || localChunks.length > 0;
        kb.display_doc_count = Math.max(kb.doc_count || 0, localDocs.length);
        kb.display_chunk_count = Math.max(kb.chunk_count || 0, localChunks.length);
    } catch (e) {
        kb.local_doc_count = 0;
        kb.local_chunk_count = 0;
        kb.has_local_pending = false;
        kb.display_doc_count = kb.doc_count || 0;
        kb.display_chunk_count = kb.chunk_count || 0;
    }
    return kb;
}

function renderKBSelect() {
    updateMultiKBLabel();
}

function escapeInlineJsValue(value) {
    return escapeHtml(JSON.stringify(String(value ?? '')));
}

function renderKBList() {
    const container = document.getElementById('kb-list-container');
    if (!container) return;

    if (!ragKBList.length) {
        container.innerHTML = `
            <div class="rag-kb-empty">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="opacity:0.4"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
                <p>暂无知识库，点击上方按钮创建</p>
            </div>`;
        return;
    }

    container.innerHTML = ragKBList.map(kb => {
        const isCore = !!kb.is_core;
        const strategyLabels = {
            auto: '自动检测', layout_aware: '版面感知', general: '常规', semantic: '语义', novel: '小说', law: '法律', qa: 'QA问答', book: '书籍', academic: '学术', code: '代码', table: '表格'
        };
        const strategyLabel = strategyLabels[kb.chunk_strategy] || kb.chunk_strategy;
        const strategyColors = {
            auto: '#2563eb', layout_aware: '#7c3aed', general: '#64748b', semantic: '#7c3aed', novel: '#db2777',
            law: '#dc2626', qa: '#d97706', book: '#2563eb', academic: '#059669',
            code: '#0891b2', table: '#ea580c'
        };
        const strategyColor = strategyColors[kb.chunk_strategy] || '#6b7280';
        const docCount = kb.display_doc_count ?? kb.doc_count ?? 0;
        const chunkCount = kb.display_chunk_count ?? kb.chunk_count ?? 0;
        const chunkSize = kb.chunk_size || ragChunkConfig.chunk_size;
        const chunkOverlap = kb.chunk_overlap || ragChunkConfig.chunk_overlap;
        const kbName = escapeHtml(kb.name);
        const kbNameAttr = escapeHtml(kb.name);
        const kbDescription = kb.description ? escapeHtml(kb.description) : '未添加描述';
        const importButton = isCore
            ? `<button class="icon-btn sm primary write-access-only" onclick="openKBResourceImportDialog('${kb.kb_id}', '${kbNameAttr}')" title="从文档库批量入库"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 8l5-5 5 5"/><path d="M12 3v13"/><path d="M5 21h14"/><path d="M6 13h12"/></svg></button>`
            : `<button class="icon-btn sm write-access-only" onclick="uploadToKB('${kb.kb_id}')" title="上传文件"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg></button>`;
        const deleteButton = isCore
            ? ''
            : `<button class="icon-btn sm danger write-access-only" onclick="deleteKB('${kb.kb_id}')" title="删除知识库"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>`;
        return `
            <article class="rag-kb-card">
                <div class="rag-kb-card-main">
                    <div class="rag-kb-card-top">
                        <div class="rag-kb-title-wrap">
                            <div class="rag-kb-icon" style="--kb-accent:${strategyColor}">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="${strategyColor}" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
                            </div>
                            <div class="rag-kb-title-text">
                                <h4>${kbName}${isCore ? '<span class="rag-kb-fixed-badge">固定</span>' : ''}</h4>
                                <p>${kbDescription}</p>
                            </div>
                        </div>
                        <div class="rag-kb-actions">
                            <button class="icon-btn sm" onclick="showKBDocuments('${kb.kb_id}')" title="查看文件">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                            </button>
                            <button class="icon-btn sm" data-rag-kb-chunks data-kb-id="${escapeHtml(kb.kb_id)}" data-kb-name="${kbNameAttr}" title="查看分块明细">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
                            </button>
                            ${importButton}
                            <button class="icon-btn sm write-access-only" onclick="rebuildKBIndex('${kb.kb_id}')" title="重建索引">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/><path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/></svg>
                            </button>
                            ${deleteButton}
                        </div>
                    </div>
                    <div class="rag-kb-stats">
                        <div>
                            <strong>${docCount}</strong>
                            <span>文档</span>
                        </div>
                        <div>
                            <strong>${chunkCount}</strong>
                            <span>分块</span>
                        </div>
                        <div>
                            <strong>${chunkSize}</strong>
                            <span>父块字数</span>
                        </div>
                        <div>
                            <strong>${chunkOverlap}</strong>
                            <span>重叠</span>
                        </div>
                    </div>
                    <div class="rag-kb-card-foot">
                        <span class="rag-kb-strategy" style="--kb-accent:${strategyColor}">${strategyLabel}</span>
                        ${kb.has_local_pending ? `<span class="rag-kb-local-pending" title="这些数据仍在浏览器本地 IndexedDB 中，尚未进入后端向量库">本地待同步 ${kb.local_doc_count || 0}/${kb.local_chunk_count || 0}</span>` : ''}
                        ${isCore ? `<button class="rag-kb-inline-action write-access-only" onclick="openKBResourceImportDialog('${kb.kb_id}', '${kbNameAttr}')">批量入库</button>` : ''}
                        <button class="rag-kb-inline-action" data-rag-kb-chunks data-kb-id="${escapeHtml(kb.kb_id)}" data-kb-name="${kbNameAttr}">查看分块</button>
                    </div>
                </div>
                <div class="rag-kb-docs" id="kb-docs-${kb.kb_id}" style="display:none"></div>
                <div class="rag-kb-chunks" id="kb-chunks-${kb.kb_id}" style="display:none"></div>
            </article>`;
    }).join('');
}

async function showKBDocuments(kbId, forceRefresh = false) {
    const docsContainer = document.getElementById(`kb-docs-${kbId}`);
    if (!docsContainer) return;

    if (!forceRefresh && docsContainer.style.display !== 'none') {
        docsContainer.style.display = 'none';
        return;
    }

    docsContainer.style.display = 'block';
    docsContainer.innerHTML = '<div class="rag-kb-docs-empty">加载中...</div>';

    try {
        const data = await api('GET', `/kb/${kbId}/documents`);
        let docs = data.documents || [];
        let localOnly = false;
        if (!docs.length) {
            const localDocs = await LocalDB.getKBDocuments(kbId);
            if (localDocs.length) {
                docs = localDocs;
                localOnly = true;
            }
        }

        if (!docs.length) {
            docsContainer.innerHTML = '<div class="rag-kb-docs-empty">暂无文档</div>';
            return;
        }

        const pendingCount = docs.filter(doc => (doc.needs_vectorization ?? false)).length;
        docsContainer.innerHTML = `
            <div class="rag-kb-docs-inner">
                <div class="rag-kb-docs-title">
                    <div class="rag-kb-docs-title-main">
                        <strong>文件列表 (${docs.length})</strong>
                        ${localOnly ? '<span class="rag-kb-docs-local">本地待同步</span>' : ''}
                        ${pendingCount > 0 ? `<span class="rag-kb-docs-pending">${pendingCount} 个待向量化</span>` : ''}
                    </div>
                    <div class="rag-kb-docs-title-actions">
                        ${pendingCount > 0 ? `<button class="rag-kb-mini-btn write-access-only" data-rag-vectorize-kb data-kb-id="${escapeHtml(kbId)}">批量补向量</button>` : ''}
                        <button class="rag-kb-mini-btn" onclick="showKBDocuments('${kbId}', true)">刷新</button>
                    </div>
                </div>
                ${docs.map(doc => {
                    const docSize = doc.size || doc.file_size || 0;
                    const sizeStr = docSize < 1024 ? `${docSize}B` :
                        docSize < 1024 * 1024 ? `${(docSize / 1024).toFixed(1)}KB` :
                        `${(docSize / 1024 / 1024).toFixed(1)}MB`;
                    const ext = (doc.filename || '').split('.').pop().toLowerCase();
                    const iconColors = { pdf: '#ef4444', docx: '#3b82f6', xlsx: '#10b981', csv: '#f59e0b', md: '#8b5cf6', txt: '#6b7280', json: '#f59e0b', html: '#ec4899' };
                    const iconColor = iconColors[ext] || '#6b7280';
                    const displayName = doc.original_filename || doc.filename || doc.title || '未命名文档';
                    const docId = doc.doc_id || '';
                    const filenameArg = escapeInlineJsValue(doc.filename || displayName);
                    const docAttrs = docId
                        ? `data-rag-doc-chunks data-kb-id="${escapeHtml(kbId)}" data-doc-id="${escapeHtml(docId)}" data-doc-name="${escapeHtml(displayName)}"`
                        : '';
                    const docClick = docId ? '' : `onclick="previewKBDoc('${kbId}',${filenameArg})"`;
                    const statusKey = doc.embedding_status_key || (doc.needs_vectorization ? 'missing' : 'complete');
                    const statusText = doc.embedding_status || (doc.needs_vectorization ? '未入库' : '已入库');
                    const vectorized = Number(doc.vectorized_chunk_count || 0);
                    const expected = Number(doc.expected_chunk_count || doc.chunk_count || 0);
                    const progress = Number(doc.embedding_progress || 0);
                    return `<div class="rag-kb-doc-row">
                        <div class="rag-kb-doc-main" ${docAttrs} ${docClick}>
                            <span class="rag-kb-doc-ext" style="--doc-accent:${iconColor}">${escapeHtml(ext || 'file')}</span>
                            <div class="rag-kb-doc-copy">
                                <span class="rag-kb-doc-name">${escapeHtml(displayName)}</span>
                                <div class="rag-kb-doc-subline">
                                    <span class="rag-kb-embed-badge ${escapeHtml(statusKey)}">${escapeHtml(statusText)}</span>
                                    <span class="rag-kb-doc-count">${vectorized}/${expected} chunks</span>
                                    ${expected > 0 ? `<span class="rag-kb-doc-progress">${Math.round(progress)}%</span>` : ''}
                                </div>
                            </div>
                        </div>
                        <div class="rag-kb-doc-actions">
                            <span>${sizeStr}</span>
                            ${docId && (doc.needs_vectorization ?? false) ? `<button class="icon-btn sm primary write-access-only" data-rag-vectorize-doc data-kb-id="${escapeHtml(kbId)}" data-doc-id="${escapeHtml(docId)}" data-doc-name="${escapeHtml(displayName)}" title="补向量">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20"/><path d="M2 12h20"/><path d="M12 6l4 4-4 4"/><path d="M6 12l4-4 4 4"/></svg>
                            </button>` : ''}
                            ${docId ? `<button class="icon-btn sm danger write-access-only" data-rag-delete-doc data-kb-id="${escapeHtml(kbId)}" data-doc-id="${escapeHtml(docId)}" data-doc-name="${escapeHtml(displayName)}" title="删除文件">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                            </button>` : ''}
                        </div>
                    </div>`;
                }).join('')}
            </div>`;
    } catch (err) {
        docsContainer.innerHTML = `<div class="rag-kb-docs-empty is-error">加载失败: ${escapeHtml(err.message)}</div>`;
    }
}

async function vectorizeKBDocuments(kbId, docIds = null, force = false) {
    if (!requireWriteAccess()) return;
    if (!kbId) {
        showToast('补向量失败: 缺少知识库ID', 'error');
        return;
    }
    const payload = { force: !!force };
    if (Array.isArray(docIds) && docIds.length > 0) {
        payload.doc_ids = docIds.filter(Boolean);
    }
    const label = payload.doc_ids && payload.doc_ids.length === 1 ? '单文档补向量' : '批量补向量';
    if (!beginRAGMutation(kbId, label)) return;

    try {
        const data = await api('POST', `/kb/${kbId}/documents/vectorize`, payload);
        if (data.success) {
            showToast(data.message || '向量化完成', 'success');
            await loadKBList();
            await showKBDocuments(kbId, true);
        } else {
            showToast(data.message || '向量化失败', 'error');
        }
    } catch (err) {
        showToast(`向量化失败: ${err.message}`, 'error');
    } finally {
        endRAGMutation(kbId);
    }
}

async function deleteKBDoc(kbId, docId, filename) {
    if (!requireWriteAccess()) return;
    if (!kbId || !docId) {
        showToast('删除失败: 缺少文档ID', 'error');
        return;
    }
    if (!confirm(`确定要删除文件 "${filename}" 吗？删除后会同时移除该文件的所有分块数据，此操作不可恢复。`)) return;
    if (!beginRAGMutation(kbId, '删除')) return;

    try {
        const data = await api('DELETE', `/kb/${kbId}/documents/${docId}`);
        if (data.success) {
            if (typeof LocalDB !== 'undefined') {
                await Promise.allSettled([
                    LocalDB.deleteKBChunksByDoc(docId),
                    LocalDB.deleteKBDocument(docId),
                ]);
            }
            showToast(`文件已删除，移除了 ${data.removed_chunks || 0} 个分块`, 'success');
            await loadKBList();
            await showKBDocuments(kbId, true);
        } else {
            showToast(data.message || '删除失败', 'error');
        }
    } catch (err) {
        showToast(`删除失败: ${err.message}`, 'error');
    } finally {
        endRAGMutation(kbId);
    }
}

async function previewKBDoc(kbId, filename) {
    const overlay = document.createElement('div');
    overlay.className = 'new-item-dialog';
    overlay.id = 'doc-preview-dialog';
    overlay.innerHTML = `
        <div class="dialog-box" style="width:700px;max-height:80vh;display:flex;flex-direction:column">
            <div style="display:flex;align-items:center;justify-content:space-between;padding:16px;border-bottom:1px solid var(--border)">
                <div style="font-size:16px;font-weight:600;color:var(--text-primary)">${escapeHtml(filename)}</div>
                <button class="icon-btn" onclick="document.getElementById('doc-preview-dialog').remove()">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            <div id="doc-preview-content" style="padding:16px;overflow-y:auto;flex:1;min-height:200px">
                <div style="text-align:center;color:var(--text-muted)"><div class="loading-dots"><span></span><span></span><span></span></div></div>
            </div>
        </div>`;
    document.body.appendChild(overlay);

    try {
        const data = await api('GET', `/kb/${kbId}/preview/${encodeURIComponent(filename)}`);
        const contentEl = document.getElementById('doc-preview-content');
        if (!contentEl) return;

        if (!data.success) {
            contentEl.innerHTML = `<div style="color:var(--danger)">预览失败</div>`;
            return;
        }

        let html = '';
        if (data.file_type === 'pdf') {
            const pages = data.content || [];
            html = pages.map(p => `
                <div style="margin-bottom:16px">
                    <div style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:4px">第 ${p.page} 页</div>
                    <div style="font-size:14px;color:var(--text-primary);line-height:1.7;white-space:pre-wrap">${escapeHtml(p.text || '(此页无文字)')}</div>
                </div>`).join('');
        } else if (data.file_type === 'excel') {
            const sheets = data.content || [];
            html = sheets.map(s => `
                <div style="margin-bottom:16px">
                    <div style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:8px">工作表 ${escapeHtml(s.sheet)}</div>
                    <div style="overflow-x:auto">
                        <table style="width:100%;border-collapse:collapse;font-size:13px">
                            ${s.rows.map(row => `<tr>${row.map(cell => `<td style="border:1px solid var(--border);padding:4px 8px;color:var(--text-primary)">${escapeHtml(cell)}</td>`).join('')}</tr>`).join('')}
                        </table>
                    </div>
                </div>`).join('');
        } else if (data.file_type === 'csv') {
            const rows = data.content || [];
            html = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">
                ${rows.map(row => `<tr>${row.map(cell => `<td style="border:1px solid var(--border);padding:4px 8px;color:var(--text-primary)">${escapeHtml(cell)}</td>`).join('')}</tr>`).join('')}
            </table></div>`;
        } else if (data.file_type === 'docx') {
            const paragraphs = data.content || [];
            html = paragraphs.map(p => `<p style="font-size:14px;color:var(--text-primary);line-height:1.7;margin-bottom:8px">${escapeHtml(p)}</p>`).join('');
        } else if (data.file_type === 'image') {
            html = `<img src="data:image/png;base64,${data.content}" style="max-width:100%;max-height:60vh;display:block;margin:0 auto" alt="${escapeHtml(filename)}">`;
        } else if (data.file_type === 'error') {
            html = `<div style="color:var(--danger)">${escapeHtml(data.content)}</div>`;
        } else {
            html = `<pre style="font-size:13px;color:var(--text-primary);line-height:1.6;white-space:pre-wrap;word-break:break-all">${escapeHtml(data.content || '')}</pre>`;
        }

        contentEl.innerHTML = html;
    } catch (err) {
        const contentEl = document.getElementById('doc-preview-content');
        if (contentEl) {
            contentEl.innerHTML = `<div style="color:var(--danger)">预览失败: ${escapeHtml(err.message)}</div>`;
        }
    }
}

function showCreateKBDlg() {
    if (!requireWriteAccess()) return;
    const parentSize = ragChunkConfig.chunk_size || 900;
    const parentOverlap = ragChunkConfig.chunk_overlap || 120;
    const childSize = ragChunkConfig.child_chunk_size || 350;
    const childOverlap = ragChunkConfig.child_chunk_overlap || 50;
    const overlay = document.createElement('div');
    overlay.className = 'new-item-dialog';
    overlay.id = 'create-kb-dialog';
    overlay.innerHTML = `
        <div class="dialog-box" style="width:440px">
            <div class="dialog-title">新建知识库</div>
            <div style="display:flex;flex-direction:column;gap:12px">
                <div>
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px">知识库名称 *</label>
                    <input class="dialog-input" id="kb-name-input" placeholder="输入知识库名称" style="width:100%">
                </div>
                <div>
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px">描述</label>
                    <input class="dialog-input" id="kb-desc-input" placeholder="可选描述" style="width:100%">
                </div>
                <div>
                    <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px">分块策略</label>
                    <select class="dialog-input" id="kb-strategy-input" style="width:100%;padding:8px 12px">
                        <option value="auto">自动检测 - 根据内容自动选择最佳策略</option>
                        <option value="layout_aware">版面感知 - 基于文档版面结构智能分块（推荐）</option>
                        <option value="semantic">语义 - 按语义边界智能切分</option>
                        <option value="general">常规 - 通用文本</option>
                        <option value="novel">小说 - 按段落、章节切分</option>
                        <option value="law">法律 - 按条款切分</option>
                        <option value="qa">QA问答 - 按问答对切分</option>
                        <option value="book">书籍 - 按章节切分</option>
                        <option value="academic">学术 - 按数字标题层级切分</option>
                        <option value="code">代码 - 按函数、类切分</option>
                        <option value="table">表格 - 按表头、数据行切分</option>
                    </select>
                </div>
                <div style="display:flex;gap:12px">
                    <div style="flex:1">
                        <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px">父块大小（字） <span style="font-size:11px;color:var(--text-muted)">700~1200</span></label>
                        <input class="dialog-input" id="kb-chunk-size-input" type="number" value="${parentSize}" min="700" max="1200" style="width:100%">
                    </div>
                    <div style="flex:1">
                        <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px">重叠字数 <span style="font-size:11px;color:var(--text-muted)">父块 10%</span></label>
                        <input class="dialog-input" id="kb-chunk-overlap-input" type="number" value="${parentOverlap}" min="0" max="200" style="width:100%">
                    </div>
                </div>
                <div style="font-size:12px;color:var(--text-muted);background:var(--bg-hover);border:1px solid var(--border);border-radius:6px;padding:8px 10px;line-height:1.6">
                    检索采用父子块：父块用于回答上下文，子块用于精准召回。当前子块约 ${childSize} 字，overlap ${childOverlap} 字。
                </div>
            </div>
            <div class="dialog-actions" style="margin-top:16px">
                <button class="dialog-btn" onclick="closeCreateKBDlg()">取消</button>
                <button class="dialog-btn primary" id="kb-create-confirm" onclick="createKB()">创建</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    setTimeout(() => document.getElementById('kb-name-input')?.focus(), 100);
}

function closeCreateKBDlg() {
    const dlg = document.getElementById('create-kb-dialog');
    if (dlg) dlg.remove();
}

async function createKB() {
    if (!requireWriteAccess()) return;
    const name = document.getElementById('kb-name-input')?.value?.trim();
    const desc = document.getElementById('kb-desc-input')?.value?.trim() || '';
    const strategy = document.getElementById('kb-strategy-input')?.value || 'general';
    let chunkSize = parseInt(document.getElementById('kb-chunk-size-input')?.value) || ragChunkConfig.chunk_size || 900;
    let chunkOverlap = parseInt(document.getElementById('kb-chunk-overlap-input')?.value) || ragChunkConfig.chunk_overlap || 120;

    if (!name) {
        showToast('请输入知识库名称', 'warning');
        return;
    }
    // 校验父块大小范围 700~1200
    if (chunkSize < 700 || chunkSize > 1200) {
        showToast('父块大小需在 700~1200 之间', 'warning');
        return;
    }
    // 校验重叠字数范围 0~200
    if (chunkOverlap < 0 || chunkOverlap > 200) {
        showToast('重叠字数需在 0~200 之间', 'warning');
        return;
    }

    const confirmBtn = document.getElementById('kb-create-confirm');
    try {
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.textContent = '创建中...';
        }
        await api('POST', '/kb/create', {
            name, description: desc, chunk_strategy: strategy,
            chunk_size: chunkSize, chunk_overlap: chunkOverlap,
        });
        showToast('知识库创建成功', 'success');
        closeCreateKBDlg();
        await loadKBList();
    } catch (err) {
        showToast('创建失败: ' + err.message, 'error');
    } finally {
        if (confirmBtn?.isConnected) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = '创建';
        }
    }
}

async function deleteKB(kbId) {
    if (!requireWriteAccess()) return;
    if (!confirm('确定删除此知识库？所有文档和向量数据都会被清除。')) return;
    if (!beginRAGMutation(kbId, '删除')) return;
    try {
        await api('DELETE', `/kb/${kbId}`);
        if (typeof LocalDB !== 'undefined') {
            await Promise.allSettled([
                LocalDB.deleteKBChunksByKB(kbId),
                LocalDB.deleteKBDocumentsByKB(kbId),
                LocalDB.deleteKnowledgeBase(kbId),
            ]);
        }
        showToast('知识库已删除', 'success');
        if (ragSelectedKB === kbId) {
            ragSelectedKB = '';
            ragSelectedKBs.delete(kbId);
            updateMultiKBLabel();
        }
        await loadKBList();
    } catch (err) {
        showToast('删除失败: ' + err.message, 'error');
    } finally {
        endRAGMutation(kbId);
    }
}

async function rebuildKBIndex(kbId) {
    if (!requireWriteAccess()) return;
    if (!confirm('确定重建这个知识库的索引吗？系统会删除旧分块，并按当前策略重新切分、向量化。')) return;
    if (!beginRAGMutation(kbId, '重建')) return;

    const progressId = 'kb-rebuild-' + Date.now();
    try {
        showProgress(progressId, '正在重建索引...', 20);
        const data = await api('POST', `/kb/${kbId}/rebuild-index`, {});
        showProgress(progressId, '索引重建完成', 100);
        setTimeout(() => hideProgress(progressId), 1200);
        showToast(data.message || '索引重建完成', data.success ? 'success' : 'warning');
        await loadKBList();
    } catch (err) {
        hideProgress(progressId);
        showToast('重建失败: ' + err.message, 'error');
    } finally {
        endRAGMutation(kbId);
    }
}

function uploadToKB(kbId) {
    if (!requireWriteAccess()) return;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.md,.txt,.pdf,.docx,.xlsx,.csv,.json,.html,.htm,.pptx,.png,.jpg,.jpeg,.bmp,.tiff,.tif';
    input.multiple = true;
    input.onchange = async () => {
        if (!beginRAGMutation(kbId, '上传')) return;
        for (const file of input.files) {
            const progressId = 'kb-upload-' + Date.now();
            try {
                showProgress(progressId, `${file.name} 正在上传...`, 10);
                const formData = new FormData();
                formData.append('file', file);
                showProgress(progressId, `${file.name} 正在解析分块...`, 40);
                const data = await api('POST', `/kb/${kbId}/upload`, formData);
                showProgress(progressId, `${file.name} 处理完成`, 100);
                setTimeout(() => hideProgress(progressId), 1500);
                if (data.success) {
                    const verify = await api('GET', `/kb/${kbId}/chunks?page=1&page_size=1`);
                    const indexedTotal = verify.total || data.indexed_chunk_count || 0;
                    if (indexedTotal <= 0) {
                        showToast(`文件 ${file.name} 已解析，但后端知识库没有查到分块，请检查索引写入`, 'error');
                    } else {
                        showToast(data.message || `文件 ${file.name} 上传成功`, 'success');
                    }
                } else if (data.duplicate || data.conflict) {
                    showToast(data.message || `文件 ${file.name} 已存在，已跳过上传`, 'warning');
                } else {
                    showToast(`上传 ${file.name} 失败: ${data.message || '未知错误'}`, 'error');
                }
            } catch (err) {
                hideProgress(progressId);
                showToast(`上传 ${file.name} 失败: ${err.message}`, 'error');
            }
        }
        try {
            await loadKBList();
        } finally {
            endRAGMutation(kbId);
        }
    };
    input.click();
}

function renderRAGMessageAt(index) {
    const messages = document.getElementById('rag-chat-messages');
    const messageEl = document.getElementById(`rag-msg-${index}`);
    const message = ragConversation[index];
    if (!messages || !messageEl || !message || message.role !== 'assistant') {
        renderRAGConversation();
        return;
    }

    const bubble = messageEl.querySelector('.chat-bubble');
    if (!bubble) {
        renderRAGConversation();
        return;
    }

    const stickToBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
    if (message.pending) {
        bubble.innerHTML = '<div class="loading-dots"><span></span><span></span><span></span></div>';
    } else if (message.streaming) {
        const embeddingError = message.metadata?.embedding_error || 'Embedding 服务不可用，语义检索已降级为关键词模式，结果可能不够精准。';
        const warningHtml = message.metadata?.embedding_degraded
            ? `<div class="rag-answer-warning">提示：${escapeHtml(embeddingError)}</div>`
            : '';
        const streamText = message.content || message.metadata?.stream_stage || '正在检索相关资料...';
        bubble.innerHTML = `
            ${warningHtml}
            <div class="rag-streaming-answer">${escapeHtml(streamText).replace(/\n/g, '<br>')}<span class="rag-stream-caret"></span></div>
        `;
    } else {
        const embeddingError = message.metadata?.embedding_error || 'Embedding 服务不可用，语义检索已降级为关键词模式，结果可能不够精准。';
        const warningHtml = message.metadata?.embedding_degraded
            ? `<div class="rag-answer-warning">提示：${escapeHtml(embeddingError)}</div>`
            : '';
    const agentMeta = message.metadata?.agent ? `<div class="rag-agent-result-bar"><span>${message.metadata.error ? 'Agent 任务失败' : 'Agent 草稿 · 已完成引用检查'}</span>${message.metadata.task_id ? `<span>${message.metadata.retry_available ? `<button type="button" onclick="retryRAGAgentTask('${escapeHtml(message.metadata.task_id)}',${index})">重新运行</button>` : `<button type="button" onclick="downloadAgentArtifact('${escapeHtml(message.metadata.task_id)}','draft.md')">下载 Markdown</button> <button type="button" onclick="prepareAgentPublish('${escapeHtml(message.metadata.task_id)}','wechat')">同步公众号草稿</button> <button type="button" onclick="publishAgentToWechat('${escapeHtml(message.metadata.task_id)}')">申请发布</button> <button type="button" onclick="prepareAgentPublish('${escapeHtml(message.metadata.task_id)}','xiaohongshu')">小红书素材</button>`}</span>` : ''}</div>${renderAgentExecutionSummary(message.metadata)}` : '';
        bubble.innerHTML = `
            ${warningHtml}
            ${agentMeta}
            ${formatAnswer(message.content || '', index, message.chunks || [])}
            ${renderRAGImages(message.images || [])}
            ${renderRAGSourceSummary(index, message.chunks || [], message.metadata || {})}
        `;
    }
    if (stickToBottom) messages.scrollTop = messages.scrollHeight;
}

let ragStreamRenderPending = false;
const ragStreamStates = new Map();

function scheduleRAGStreamRender(index) {
    if (ragStreamRenderPending) return;
    ragStreamRenderPending = true;
    requestAnimationFrame(() => {
        ragStreamRenderPending = false;
        renderRAGMessageAt(index);
    });
}

function parseRAGStreamEvent(rawEvent) {
    const dataLines = rawEvent
        .split('\n')
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart());
    if (!dataLines.length) return null;
    return JSON.parse(dataLines.join('\n'));
}

function getRAGStreamState(index) {
    if (!ragStreamStates.has(index)) {
        ragStreamStates.set(index, {
            queue: [],
            draining: false,
            doneEvent: null,
        });
    }
    return ragStreamStates.get(index);
}

function drainRAGStreamTokens(index) {
    const state = getRAGStreamState(index);
    if (state.draining) return;
    state.draining = true;

    const tick = () => {
        const assistant = ragConversation[index];
        if (!assistant) {
            ragStreamStates.delete(index);
            return;
        }

        let appended = '';
        let guard = 0;
        while (state.queue.length && guard < 6) {
            appended += state.queue.shift();
            guard += 1;
        }

        if (appended) {
            assistant.pending = false;
            assistant.streaming = true;
            assistant.content = (assistant.content || '') + appended;
            renderRAGMessageAt(index);
        }

        if (state.queue.length) {
            setTimeout(tick, 24);
            return;
        }

        state.draining = false;
        if (state.doneEvent) {
            finishRAGStreamMessage(index, state.doneEvent);
            ragStreamStates.delete(index);
        }
    };

    setTimeout(tick, 0);
}

function finishRAGStreamMessage(index, event) {
    const assistant = ragConversation[index];
    if (!assistant) return;
    assistant.pending = false;
    assistant.streaming = false;
    assistant.content = event.answer || assistant.content || '';
    assistant.chunks = event.chunks || event.sources || assistant.chunks || [];
    assistant.images = event.images || assistant.images || [];
    assistant.metadata = {
        ...(assistant.metadata || {}),
        ...(event.metadata || {}),
        web_results: event.web_results || assistant.metadata?.web_results || [],
    };
    renderRAGMessageAt(index);
}

async function requestRAGStream(payload, assistantIndex) {
    if (typeof fetch === 'undefined' || typeof TextDecoder === 'undefined') return false;

    const headers = {
        ...getAccessHeaders(),
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
    };
    const response = await fetch(`${API_BASE}/rag/query/stream`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
        cache: 'no-store',
    });

    if (!response.ok || !response.body) {
        if (typeof markBackendOnline === 'function') markBackendOnline(response.ok);
        return false;
    }

    if (typeof markBackendOnline === 'function') markBackendOnline(true);

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let receivedAnyEvent = false;
    let receivedToken = false;

    const applyEvent = (event) => {
        if (!event || !ragConversation[assistantIndex]) return;
        receivedAnyEvent = true;
        const assistant = ragConversation[assistantIndex];

        if (event.type === 'meta') {
            assistant.pending = false;
            assistant.streaming = true;
            assistant.content = assistant.content || '';
            assistant.chunks = event.chunks || event.sources || [];
            assistant.images = event.images || [];
            assistant.metadata = {
                ...(assistant.metadata || {}),
                ...(event.metadata || {}),
                web_results: event.web_results || [],
                stream_stage: '正在生成回答...',
            };
            scheduleRAGStreamRender(assistantIndex);
            return;
        }

        if (event.type === 'token') {
            receivedToken = true;
            const state = getRAGStreamState(assistantIndex);
            state.queue.push(event.content || '');
            drainRAGStreamTokens(assistantIndex);
            return;
        }

        if (event.type === 'replace') {
            const state = getRAGStreamState(assistantIndex);
            state.queue = [];
            assistant.pending = false;
            assistant.streaming = true;
            assistant.content = event.content || '';
            scheduleRAGStreamRender(assistantIndex);
            return;
        }

        if (event.type === 'done') {
            const state = getRAGStreamState(assistantIndex);
            if (state.queue.length || state.draining) {
                state.doneEvent = event;
            } else {
                finishRAGStreamMessage(assistantIndex, event);
                ragStreamStates.delete(assistantIndex);
            }
            return;
        }

        if (event.type === 'error') {
            throw new Error(event.message || '流式问答失败');
        }
    };

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split(/\n\n/);
        buffer = events.pop() || '';
        events.forEach(rawEvent => {
            const event = parseRAGStreamEvent(rawEvent);
            if (event) applyEvent(event);
        });
    }

    const tail = buffer.trim();
    if (tail) {
        const event = parseRAGStreamEvent(tail);
        if (event) applyEvent(event);
    }

    if (receivedAnyEvent && !receivedToken && ragConversation[assistantIndex]?.pending) {
        ragConversation[assistantIndex].pending = false;
        scheduleRAGStreamRender(assistantIndex);
    }
    return receivedAnyEvent;
}

async function sendRAGQuery() {
    const input = document.getElementById('rag-chat-input');
    if (!input || !input.value.trim()) return;

    const query = input.value.trim();
    // Capture explicit drag-and-drop resources before clearing the textarea.
    // The input event handler intentionally clears transient resource state
    // when users erase the field, so capturing after input.value='' loses the
    // second/third article even though its marker was visible in the UI.
    const composerPaths = Array.from(ragComposerResourcePaths);
    input.value = '';
    if (!ragSessionId) {
        await initRAGSession();
    }

    ragConversation.push({ role: 'user', content: query, chunks: [], metadata: {} });
    ragConversation.push({ role: 'assistant', content: '', chunks: [], metadata: {}, pending: true });
    renderRAGConversation();

    const assistantIndex = ragConversation.length - 1;

    if (ragAgentMode) {
        try {
            ragConversation[assistantIndex].metadata = { agent: true };
            ragConversation[assistantIndex].content = '正在执行 Agent 任务：检索 → 整理 → 生成草稿…';
            ragConversation[assistantIndex].pending = false;
            renderRAGConversation();
            const agentResult = await runRAGAgentTask(query, assistantIndex, composerPaths);
            ragConversation[assistantIndex].content = agentResult.draft;
            ragConversation[assistantIndex].chunks = agentResult.evidence || [];
            ragConversation[assistantIndex].images = agentResult.images || [];
            ragConversation[assistantIndex].metadata = { agent: true, output_format: agentResult.format, task_id: agentResult.taskId, trace: agentResult.trace, quality: agentResult.quality, tool_calls: agentResult.toolCalls, evidence: agentResult.evidence, evidence_count: (agentResult.evidence || []).length, perception: agentResult.trace?.perception || {}, coverage: agentResult.trace?.coverage || {} };
            renderRAGConversation();
            if (typeof recordTelemetry === 'function') recordTelemetry('draft_generated', {mode: 'agent', format: agentResult.format, chars: String(agentResult.draft || '').length, source_count: (agentResult.evidence || []).length}, {task_id: agentResult.taskId});
        } catch (err) {
            const failedTaskId = err?.taskId || '';
            ragConversation[assistantIndex] = { role: 'assistant', content: `Agent 任务失败：${err.message}${failedTaskId ? '\n\n可点击下方按钮重试。' : ''}`, chunks: [], metadata: { error: true, agent: true, task_id: failedTaskId, retry_available: Boolean(failedTaskId) } };
            renderRAGConversation();
        }
        return;
    }

    const payload = {
        query,
        mode: ragSearchMode,
        top_k: ragTopK,
        kb_id: ragSelectedKB || '',
        kb_ids: ragSelectedKBs.size > 0 ? Array.from(ragSelectedKBs) : undefined,
        enable_web_search: ragWebSearchEnabled,
        session_id: ragSessionId || '',
    };
    const selectedPaths = Array.from(new Set([
        ...Array.from(window._fileTreeSelectedPaths || (typeof _fileTreeSelectedPaths !== 'undefined' ? _fileTreeSelectedPaths : [])),
        ...composerPaths,
        ...ragComposerResourcePaths,
    ]));
    if (selectedPaths.length) payload.selected_paths = selectedPaths;
    ragComposerResourcePaths.clear();
    renderRAGComposerResources();

    try {
        const streamed = await requestRAGStream(payload, assistantIndex);
        if (streamed) {
            await loadRAGSessions();
            renderRAGConversation();
            return;
        }

        const data = await api('POST', '/rag/query', payload);

        const chunks = data.chunks || data.sources || [];
        ragConversation[assistantIndex] = {
            role: 'assistant',
            content: data.answer || data.response || '未找到相关内容',
            chunks,
            images: data.images || [],
            metadata: {
                ...(data.metadata || {}),
                grouped_sources: data.grouped_sources || {},
                web_results: data.web_results || [],
            },
        };
        await loadRAGSessions();
        renderRAGConversation();
    } catch (err) {
        ragConversation[assistantIndex] = {
            role: 'assistant',
            content: `查询失败：${err.message}`,
            chunks: [],
            metadata: { error: true },
        };
        renderRAGConversation();
    }
}

function ragChatKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendRAGQuery();
    }
}

function renderRAGConversation() {
    const messages = document.getElementById('rag-chat-messages');
    if (!messages) return;

    messages.innerHTML = ragConversation.map((message, index) => {
        if (message.role === 'user') {
            return `
                <div class="chat-msg user" id="rag-msg-${index}" data-rag-msg-index="${index}">
                    <div class="chat-avatar">你</div>
                    <div class="chat-bubble">${escapeHtml(message.content || '')}</div>
                </div>`;
        }

        if (message.pending) {
            return `
                <div class="chat-msg assistant" id="rag-msg-${index}" data-rag-msg-index="${index}">
                    <div class="chat-avatar">AI</div>
                    <div class="chat-bubble"><div class="loading-dots"><span></span><span></span><span></span></div></div>
                </div>`;
        }

        const embeddingError = message.metadata?.embedding_error || 'Embedding 服务不可用，语义检索已降级为关键词模式，结果可能不够精准。';
        const warningHtml = message.metadata?.embedding_degraded
            ? `<div class="rag-answer-warning">提示：${escapeHtml(embeddingError)}</div>`
            : '';
        const answerHtml = formatAnswer(message.content || '', index, message.chunks || []);
        const imageHtml = renderRAGImages(message.images || []);
        const sourceHtml = renderRAGSourceSummary(index, message.chunks || [], message.metadata || {});
        const agentMeta = message.metadata?.agent ? `<div class="rag-agent-result-bar"><span>${message.metadata.error ? 'Agent 任务失败' : 'Agent 草稿 · 已完成引用检查'}</span>${message.metadata.task_id ? `<span>${message.metadata.retry_available ? `<button type="button" onclick="retryRAGAgentTask('${escapeHtml(message.metadata.task_id)}',${index})">重新运行</button>` : `<button type="button" onclick="downloadAgentArtifact('${escapeHtml(message.metadata.task_id)}','draft.md')">下载 Markdown</button> <button type="button" onclick="prepareAgentPublish('${escapeHtml(message.metadata.task_id)}','wechat')">同步公众号草稿</button> <button type="button" onclick="publishAgentToWechat('${escapeHtml(message.metadata.task_id)}')">申请发布</button> <button type="button" onclick="prepareAgentPublish('${escapeHtml(message.metadata.task_id)}','xiaohongshu')">小红书素材</button>`}</span>` : ''}</div>${renderAgentExecutionSummary(message.metadata)}` : '';

        return `
            <div class="chat-msg assistant" id="rag-msg-${index}" data-rag-msg-index="${index}">
                <div class="chat-avatar">AI</div>
                <div class="chat-bubble">
                    ${warningHtml}
                    ${agentMeta}
                    ${answerHtml}
                    ${imageHtml}
                    ${sourceHtml}
                </div>
            </div>`;
    }).join('');

    renderRAGTurnIndex();
    messages.scrollTop = messages.scrollHeight;
}

function getSafeRAGImageUrl(value) {
    const url = String(value || '').trim();
    const apiPrefix = String(API_BASE || '/api/v1').replace(/\/$/, '');
    if (url.startsWith(`${apiPrefix}/file-resources/raw/`)) return url;
    if (url.startsWith('/api/v1/file-resources/raw/')) return url;
    return '';
}

function renderRAGImages(images) {
    const safeImages = (images || []).map(image => ({
        url: getSafeRAGImageUrl(image?.url || image?.thumbnail_url),
        caption: image?.caption || image?.source_path || '相关图片',
    })).filter(image => image.url).slice(0, 12);
    if (!safeImages.length) return '';
    return `<div class="rag-related-images"><div class="rag-related-images-title">相关图片</div><div class="rag-related-images-grid">${safeImages.map(image => `<a class="rag-related-image" href="${escapeHtml(image.url)}" target="_blank" rel="noopener noreferrer"><img src="${escapeHtml(image.url)}" alt="${escapeHtml(image.caption)}" loading="lazy"><span>${escapeHtml(image.caption)}</span></a>`).join('')}</div></div>`;
}

function getRAGAssistantTurns() {
    return ragConversation
        .map((message, index) => ({ message, index }))
        .filter(item => item.message.role === 'assistant');
}

function renderRAGTurnIndex() {
    const indexEl = document.getElementById('rag-turn-index');
    if (!indexEl) return;
    indexEl.innerHTML = '';
    indexEl.hidden = true;
    indexEl.style.display = 'none';
}

function updateRAGTurnIndexActive(messageIndex) {
    return;
}

function scrollRAGMessage(messageIndex) {
    const target = document.getElementById(`rag-msg-${messageIndex}`);
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    updateRAGTurnIndexActive(messageIndex);
    target.classList.add('rag-index-focus');
    setTimeout(() => target.classList.remove('rag-index-focus'), 1200);
}

function updateRAGTurnIndexFromScroll() {
    return;
}

function bindRAGTurnIndexScroll() {
    return;
}

async function newRAGConversation() {
    ragCurrentChunks = [];
    ragCitationMessageIndex = -1;
    ragActiveCitationIndex = 0;
    await initRAGSession();
    resetRAGConversation();
    showRAGSessionPanel();
    await loadRAGSessions();
    showToast('已开始新会话', 'success');
}

function setRAGSearchMode(mode) {
    ragSearchMode = mode;
    const select = document.getElementById('rag-search-mode');
    if (select) select.value = mode;
}

function setRAGTopK(value) {
    const parsed = parseInt(value, 10);
    ragTopK = Number.isFinite(parsed) ? Math.max(3, Math.min(parsed, 8)) : 5;
    const topKSelect = document.getElementById('rag-top-k');
    if (topKSelect) topKSelect.value = String(ragTopK);
}

function getChunkDisplayText(chunk) {
    return chunk.display_text || chunk.parent_text || chunk.text || chunk.content || '';
}

function getChunkChildText(chunk) {
    return chunk.child_text || chunk.metadata?.child_text || '';
}

function getChunkParentText(chunk) {
    return chunk.parent_text || chunk.metadata?.parent_content || '';
}

function getChunkDisplayScore(chunk) {
    const value = chunk.display_score ?? chunk.score ?? chunk.relevance_score ?? chunk.similarity ?? 0;
    return Number.isFinite(Number(value)) ? Number(value) : 0;
}

function getChunkRawScore(chunk) {
    const value = chunk.raw_score ?? chunk.similarity;
    return Number.isFinite(Number(value)) ? Number(value) : null;
}

function normalizeRAGOrderedLists(text) {
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    const output = [];
    const counters = new Map();
    let inFence = false;
    let listBlockActive = false;
    let activeListIndent = null;

    const getIndentWidth = (value) => {
        const match = String(value || '').match(/^[ \t]*/);
        return (match ? match[0] : '').replace(/\t/g, '    ').length;
    };

    const orderedPattern = /^([ \t]*)(\d+)[.)、]\s+(.*)$/;
    const bulletPattern = /^[ \t]*[-*+]\s+/;

    for (const line of lines) {
        const fenceMatch = line.match(/^[ \t]*(```+|~~~+)/);
        if (fenceMatch) {
            inFence = !inFence;
            listBlockActive = false;
            activeListIndent = null;
            output.push(line);
            continue;
        }

        if (inFence) {
            output.push(line);
            continue;
        }

        const trimmed = line.trim();
        if (!trimmed) {
            output.push(line);
            continue;
        }

        const orderedMatch = line.match(orderedPattern);
        if (orderedMatch) {
            const indent = getIndentWidth(orderedMatch[1]);
            const hasCounter = counters.has(indent);
            const isContinuation = listBlockActive && hasCounter;
            const nextNumber = isContinuation ? counters.get(indent) + 1 : 1;

            counters.set(indent, nextNumber);
            activeListIndent = activeListIndent === null ? indent : Math.min(activeListIndent, indent);
            listBlockActive = true;
            output.push(`${orderedMatch[1]}${nextNumber}. ${orderedMatch[3]}`);
            continue;
        }

        if (bulletPattern.test(line)) {
            listBlockActive = true;
            if (activeListIndent === null) activeListIndent = getIndentWidth(line);
            output.push(line);
            continue;
        }

        const indent = getIndentWidth(line);
        const isIndentedContinuation = listBlockActive
            && activeListIndent !== null
            && indent > activeListIndent;

        if (!isIndentedContinuation) {
            listBlockActive = false;
            activeListIndent = null;
            counters.clear();
        }
        output.push(line);
    }

    // Consecutive ordered items should remain one Markdown list even when the
    // model inserts an empty line between every item.
    const compacted = [];
    for (let index = 0; index < output.length; index += 1) {
        const current = output[index];
        if (current.trim() === '' && compacted.length > 0) {
            const previous = compacted[compacted.length - 1];
            let nextIndex = index + 1;
            while (nextIndex < output.length && output[nextIndex].trim() === '') nextIndex += 1;
            const next = output[nextIndex] || '';
            const previousMatch = previous.match(orderedPattern);
            const nextMatch = next.match(orderedPattern);
            if (previousMatch && nextMatch
                && getIndentWidth(previousMatch[1]) === getIndentWidth(nextMatch[1])) {
                continue;
            }
        }
        compacted.push(current);
    }

    return compacted.join('\n');
}

function formatAnswer(text, messageIndex = -1, chunks = []) {
    const html = renderMarkdown(normalizeRAGOrderedLists(text));
    return decorateRAGAnswerHtml(html, messageIndex, chunks);
}

function decorateRAGAnswerHtml(html, messageIndex, chunks) {
    let output = html || '';
    output = output.replace(/\[模型推断\]/g, '<span class="rag-inference-tag">模型推断</span>');
    output = output.replace(/\[(\d+)\]/g, (_, num) => {
        const refIndex = parseInt(num, 10) - 1;
        const exists = refIndex >= 0 && refIndex < (chunks || []).length;
        if (!exists) return `<span class="rag-inline-citation is-disabled">[${num}]</span>`;
        return `<button class="rag-inline-citation" onclick="openRAGMessageCitation(${messageIndex}, ${refIndex})" title="查看引用 ${num}">[${num}]</button>`;
    });
    return output;
}

function renderRAGSourceSummary(messageIndex, chunks, metadata) {
    let html = '';

    if (chunks && chunks.length > 0) {
        html += `<div class="sources"><div class="rag-source-header"><span>引用来源 (${chunks.length})</span><button class="rag-open-citations-btn" onclick="openRAGMessageCitation(${messageIndex}, 0)">查看引用</button></div>`;
        chunks.forEach((chunk, idx) => {
            const score = getChunkDisplayScore(chunk);
            const sourceName = chunk.title || chunk.source || chunk.doc_name || `来源 ${idx + 1}`;
            html += `<button class="source-item rag-source-chip" onclick="openRAGMessageCitation(${messageIndex}, ${idx})">
                <span class="rag-source-chip-index">${idx + 1}</span>
                <span class="rag-source-chip-name">${escapeHtml(sourceName)}</span>
                <span class="rag-source-chip-score">${(score * 100).toFixed(0)}%</span>
            </button>`;
        });
        html += '</div>';
    }

    if (metadata?.grouped_sources && Object.keys(metadata.grouped_sources).length > 0) {
        html += `<div class="sources" style="margin-top:8px"><div style="margin-bottom:4px">多库检索结果</div>`;
        for (const [kid, sources] of Object.entries(metadata.grouped_sources)) {
            const kbName = ragKBList.find(k => k.kb_id === kid)?.name || kid;
            html += `<div style="font-size:12px;color:var(--text-muted);margin:4px 0;">知识库：${escapeHtml(kbName)}（${sources.length} 条）</div>`;
        }
        html += '</div>';
    }

    if (metadata?.web_results && metadata.web_results.length > 0) {
        html += `<div class="sources" style="margin-top:8px"><div style="margin-bottom:4px">联网搜索结果 (${metadata.web_results.length})</div>`;
        metadata.web_results.forEach((result, idx) => {
            html += `<div class="source-item" onclick="window.open('${result.url || '#'}','_blank')">${idx + 1}. ${escapeHtml(result.title || '搜索结果')} - ${escapeHtml(result.source || '')}</div>`;
        });
        html += '</div>';
    }

    return html;
}

async function loadRAGSessions(activeSessionId = ragSessionId) {
    try {
        const data = await api('GET', '/rag/sessions');
        ragSessionList = (data.sessions || []).sort((a, b) => (b.last_active || 0) - (a.last_active || 0));
        if (activeSessionId) {
            ragSessionId = activeSessionId;
        }
        if (ragRightMode === 'sessions') {
            renderRAGSessionsPanel();
        }
    } catch (e) {
        ragSessionList = [];
        if (ragRightMode === 'sessions') {
            renderRAGSessionsPanel();
        }
    }
}

function updateRAGRightPanelChrome() {
    const titleEl = document.getElementById('rag-right-title');
    const newSessionBtn = document.getElementById('rag-right-new-session');
    const closeBtn = document.getElementById('rag-right-close');
    const sessionsPanel = document.getElementById('rag-sessions-panel');
    const citationsPanel = document.getElementById('rag-citations-panel');

    if (titleEl) titleEl.textContent = ragRightMode === 'citations' ? '引用详情' : '会话记录';
    if (newSessionBtn) newSessionBtn.style.display = ragRightMode === 'sessions' ? '' : 'none';
    if (closeBtn) closeBtn.style.display = ragRightMode === 'citations' ? '' : 'none';
    if (sessionsPanel) sessionsPanel.style.display = ragRightMode === 'sessions' ? '' : 'none';
    if (citationsPanel) citationsPanel.style.display = ragRightMode === 'citations' ? 'flex' : 'none';
}

function expandRAGRightPanelIfCollapsed() {
    const rightPanel = document.getElementById('rag-right-panel');
    const resizer = document.getElementById('rag-resizer');
    if (!rightPanel || !rightPanel.classList.contains('panel-collapsed')) return;

    if (typeof ragRightPanelCollapsed !== 'undefined') {
        ragRightPanelCollapsed = false;
    }
    rightPanel.style.width = rightPanel.dataset.prevWidth || '340px';
    rightPanel.style.minWidth = '';
    rightPanel.style.overflow = '';
    if (resizer) resizer.style.display = '';
    rightPanel.classList.remove('panel-collapsed');
}

function showRAGSessionPanel() {
    expandRAGRightPanelIfCollapsed();
    ragRightMode = 'sessions';
    closeChunkDetail();
    updateRAGRightPanelChrome();
    renderRAGSessionsPanel();
}

function cleanRAGSessionPreview(value) {
    return String(value || '还没有消息')
        .replace(/<[^>]*>/g, '')
        .replace(/&nbsp;/g, ' ')
        .replace(/[鐓鐭鍒鍔鏌鏂鏆瀹瀛鍏鎵閲缁绱鏍鏉椤褰寮璺馃鈥绗鐖鍛绌妫澶姝娌鍥鐩鍘浠鏈灏棣鎼][^，。；、！？\s]*/g, '')
        .replace(/\s+/g, ' ')
        .trim() || '还没有消息';
}

function renderRAGSessionsPanel() {
    const container = document.getElementById('rag-sessions-panel');
    if (!container) return;

    updateRAGRightPanelChrome();

    if (!ragSessionList.length) {
        container.innerHTML = `
            <div class="chunks-empty">
                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                <p>暂无历史会话</p>
            </div>`;
        return;
    }

    container.innerHTML = `<div class="rag-session-list">${ragSessionList.map(session => {
        const isActive = session.session_id === ragSessionId;
        const title = cleanRAGSessionPreview(session.title || '新会话');
        const preview = cleanRAGSessionPreview(session.preview);
        const updatedAt = session.last_active ? new Date(session.last_active * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
        return `<div class="rag-session-item ${isActive ? 'is-active' : ''}" onclick="openRAGSession('${session.session_id}')">
            <div class="rag-session-title-row">
                <div class="rag-session-title">${escapeHtml(title)}</div>
                <div class="rag-session-side">
                    <div class="rag-session-meta">${updatedAt}</div>
                    <button class="rag-session-delete-btn" data-rag-delete-session data-session-id="${escapeHtml(session.session_id)}" title="删除会话">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
            </div>
            <div class="rag-session-preview">${escapeHtml(preview)}</div>
            <div class="rag-session-footer">
                <span>${session.message_count || 0} 条消息</span>
                ${isActive ? '<span class="rag-session-badge">当前</span>' : ''}
            </div>
        </div>`;
    }).join('')}</div>`;
}

async function deleteRAGSession(sessionId) {
    if (!requireWriteAccess()) return;
    if (!sessionId) return;
    if (!confirm('确定删除这个会话吗？')) return;

    try {
        await api('DELETE', `/rag/session/${sessionId}`);
        ragSessionList = ragSessionList.filter(session => session.session_id !== sessionId);
        if (ragSessionId === sessionId) {
            ragSessionId = '';
            ragCurrentChunks = [];
            ragCitationMessageIndex = -1;
            ragActiveCitationIndex = 0;
            resetRAGConversation();
            showRAGSessionPanel();
        } else {
            renderRAGSessionsPanel();
        }
        await loadRAGSessions(ragSessionId);
        showToast('会话已删除', 'success');
    } catch (err) {
        showToast('删除会话失败: ' + err.message, 'error');
    }
}

async function openRAGSession(sessionId) {
    try {
        const data = await api('GET', `/rag/session/${sessionId}/history`);
        // Rolling summaries are internal memory, not assistant messages. Keep
        // them in the server context but do not render them as if the model
        // had just answered the user.
        const history = (data.history || []).filter(message =>
            message.role !== 'system' && message.metadata?.type !== 'rolling_summary'
        );
        ragSessionId = sessionId;
        ragConversation = history.length ? history.map(message => ({
            role: message.role,
            content: message.content || '',
            chunks: Array.isArray(message.metadata?.chunks) ? message.metadata.chunks : [],
            images: Array.isArray(message.metadata?.images) ? message.metadata.images : [],
            metadata: message.metadata || {},
        })) : [createRAGWelcomeMessage()];
        ragCurrentChunks = [];
        ragCitationMessageIndex = -1;
        ragActiveCitationIndex = 0;
        switchRAGView('chat');
        renderRAGConversation();
        await loadRAGSessions(sessionId);
        showRAGSessionPanel();
    } catch (err) {
        showToast('加载会话失败: ' + err.message, 'error');
    }
}

function renderRAGChunks() {
    const container = document.getElementById('rag-chunks-list');
    if (!container) return;

    if (!ragCurrentChunks.length) {
        renderRAGChunksEmpty();
        return;
    }

    container.innerHTML = ragCurrentChunks.map((chunk, idx) => {
        const score = getChunkDisplayScore(chunk);
        const scorePercent = Math.round(score * 100);
        const title = chunk.title || chunk.source || `分块 ${idx + 1}`;
        const text = getChunkDisplayText(chunk);
        const childText = getChunkChildText(chunk);
        const parentText = getChunkParentText(chunk);
        const preview = text.substring(0, 150) + (text.length > 150 ? '...' : '');
        const source = chunk.source || chunk.document || '';
        const page = chunk.page || chunk.metadata?.page || '';
        const childLength = chunk.child_content_length || childText.length || 0;
        const parentLength = chunk.parent_content_length || parentText.length || 0;

        return `
            <button class="rag-citation-card ${idx === ragActiveCitationIndex ? 'is-active' : ''}" id="chunk-${idx}" onclick="expandChunk(${idx})">
                <div class="rag-citation-card-head">
                    <div class="rag-citation-card-title">
                        <span>${idx + 1}</span>
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                        <strong>${escapeHtml(title)}</strong>
                    </div>
                    <em>${scorePercent}%</em>
                </div>
                <div class="rag-citation-card-preview">${escapeHtml(preview || '(空父块)')}</div>
                <div class="rag-citation-card-meta">
                    ${page ? `<span>第 ${escapeHtml(String(page))} 页</span>` : ''}
                    <span>父块 ${parentLength || text.length} 字</span>
                    ${childLength && childLength !== text.length ? `<span>命中 ${childLength} 字</span>` : ''}
                </div>
            </button>`;
    }).join('');
}

function renderRAGChunksEmpty() {
    const container = document.getElementById('rag-chunks-list');
    if (!container) return;
    container.innerHTML = `
        <div class="chunks-empty">
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>
            <p>检索结果将在这里显示</p>
        </div>`;
}

function openRAGMessageCitation(messageIndex, citationIndex = 0) {
    const message = ragConversation[messageIndex];
    if (!message || !Array.isArray(message.chunks) || message.chunks.length === 0) return;

    ragCitationMessageIndex = messageIndex;
    ragActiveCitationIndex = citationIndex;
    ragCurrentChunks = message.chunks;
    ragRightMode = 'citations';
    expandRAGRightPanelIfCollapsed();
    switchRAGView('chat');
    updateRAGRightPanelChrome();
    renderRAGChunks();
    expandChunk(citationIndex);
}

function expandChunk(idx) {
    const chunk = ragCurrentChunks[idx];
    if (!chunk) return;

    ragActiveCitationIndex = idx;

    const text = getChunkDisplayText(chunk);
    const childText = getChunkChildText(chunk);
    const parentText = getChunkParentText(chunk);
    const title = chunk.title || chunk.source || `分块 ${idx + 1}`;
    const hasChildHit = childText && childText !== text;
    const score = getChunkDisplayScore(chunk);
    const rawScore = getChunkRawScore(chunk);

    const detailEl = document.getElementById('rag-chunk-detail');
    if (!detailEl) return;

    detailEl.innerHTML = `
        <div class="rag-citation-detail-head">
            <div class="rag-citation-detail-title-row">
                <div class="rag-citation-detail-title">
                    <span class="rag-citation-detail-index">${idx + 1}</span>
                    <span>${escapeHtml(title)}</span>
                </div>
                <button class="icon-btn sm" onclick="closeChunkDetail()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            <div class="rag-citation-detail-meta">
                ${chunk.source ? `<span><b>来源</b>${escapeHtml(chunk.source)}</span>` : ''}
                ${chunk.page ? `<span><b>页码</b>${escapeHtml(String(chunk.page))}</span>` : ''}
                ${typeof score === 'number' ? `<span><b>相关度</b>${Math.round(score * 100)}%</span>` : ''}
                ${typeof rawScore === 'number' ? `<span><b>原始检索分</b>${rawScore.toFixed(3)}</span>` : ''}
                ${parentText ? `<span><b>父块</b>${parentText.length} 字</span>` : ''}
                ${childText ? `<span><b>命中子块</b>${childText.length} 字</span>` : ''}
            </div>
        </div>
        <div class="rag-citation-detail-body">
            <div class="rag-citation-section-title">父块上下文</div>
            <div class="rag-citation-detail-content">${escapeHtml(text || '(空父块)')}</div>
            ${hasChildHit ? `
                <div class="rag-citation-child-hit">
                    <div class="rag-citation-section-title is-primary">实际命中的子块</div>
                    <div class="rag-citation-child-text">${escapeHtml(childText)}</div>
                </div>` : ''}
        </div>`;

    detailEl.style.display = 'flex';
    renderRAGChunks();
}

function closeChunkDetail() {
    const detailEl = document.getElementById('rag-chunk-detail');
    if (detailEl) detailEl.style.display = 'none';
}

async function ragSearch() {
    const input = document.getElementById('rag-chat-input');
    if (!input || !input.value.trim()) return;
    await sendRAGQuery();
}

async function clearRAGChat() {
    await newRAGConversation();
}

function exportRAGChat() {
    if (!ragConversation.length) {
        showToast('没有可导出的会话', 'warning');
        return;
    }
    let text = '# 知识库问答记录\n\n';
    ragConversation.forEach(msg => {
        const role = msg.role === 'user' ? '用户' : 'AI';
        const content = (msg.content || '').trim();
        if (content) {
            text += `**${role}**: ${content}\n\n`;
        }
    });
    const latestAssistant = [...ragConversation].reverse().find(msg => msg.role === 'assistant' && Array.isArray(msg.chunks) && msg.chunks.length);
    if (latestAssistant?.chunks?.length) {
        text += '---\n\n## 引用分块\n\n';
        latestAssistant.chunks.forEach((chunk, idx) => {
            const title = chunk.title || chunk.source || `分块 ${idx + 1}`;
            const content = getChunkDisplayText(chunk);
            const childContent = getChunkChildText(chunk);
            text += `### ${idx + 1}. ${title}\n\n${content}\n\n`;
            if (childContent && childContent !== content) {
                text += `> 命中子块：${childContent}\n\n`;
            }
        });
    }
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'rag-chat-export.md';
    a.click();
    URL.revokeObjectURL(url);
    showToast('会话已导出', 'success');
}

// ========== 分块明细查看 ==========

let _chunkViewData = { kbId: '', kbName: '', page: 1, pageSize: 20, total: 0, chunks: [] };

async function showKBChunks(kbId, kbName) {
    const inline = document.getElementById(`kb-chunks-${kbId}`);
    if (inline) {
        if (inline.style.display !== 'none') {
            inline.style.display = 'none';
            inline.innerHTML = '';
            return;
        }
        document.querySelectorAll('.rag-kb-chunks').forEach(el => {
            if (el !== inline) {
                el.style.display = 'none';
                el.innerHTML = '';
            }
        });
        inline.style.display = 'block';
        inline.innerHTML = '<div class="rag-parent-chunk-empty">正在加载分块...</div>';
        await _loadInlineKBChunks(kbId, kbName, inline);
        return;
    }

    _chunkViewData = { kbId, kbName, page: 1, pageSize: 30, total: 0, chunks: [] };
    await _loadChunksPage();
}

async function _loadInlineKBChunks(kbId, kbName, container) {
    try {
        const pageSize = 50;
        const data = await api('GET', `/kb/${kbId}/chunks?page=1&page_size=${pageSize}`);
        if (!data.success) {
            container.innerHTML = '<div class="rag-parent-chunk-empty is-error">加载失败</div>';
            return;
        }

        let chunks = data.chunks || [];
        let localOnly = false;
        if (!chunks.length) {
            const localChunks = await LocalDB.getKBChunks(kbId);
            if (localChunks.length) {
                chunks = localChunks.slice(0, pageSize).map(normalizeLocalChunkForView);
                localOnly = true;
            }
        }

        if (!chunks.length) {
            container.innerHTML = '<div class="rag-parent-chunk-empty">暂无分块数据</div>';
            return;
        }

        const groups = groupChunksByParent(chunks);
        container.innerHTML = `
            <div class="rag-kb-inline-chunks-head">
                <div>
                    <strong>分块明细</strong>
                    <span>${escapeHtml(kbName || '')} · 当前预览 ${groups.length} 个父块 / ${chunks.length} 个子块</span>
                </div>
                <button class="dialog-btn" onclick="event.stopPropagation();openKBChunksDialog('${kbId}', ${escapeInlineJsValue(kbName || '')})">打开完整分页</button>
            </div>
            ${localOnly ? '<div class="rag-parent-chunk-notice">当前显示浏览器本地分块，尚未进入后端向量库。</div>' : ''}
            <div class="rag-kb-inline-chunks-list">
                ${renderParentChunkGroups(groups, 1)}
            </div>`;
    } catch (err) {
        container.innerHTML = `<div class="rag-parent-chunk-empty is-error">加载失败: ${escapeHtml(err.message)}</div>`;
    }
}

async function openKBChunksDialog(kbId, kbName) {
    _chunkViewData = { kbId, kbName, page: 1, pageSize: 30, total: 0, chunks: [] };
    await _loadChunksPage();
}

async function showKBDocumentChunks(kbId, docId, docName) {
    if (!docId) return;

    const old = document.getElementById('chunk-detail-dialog');
    if (old) old.remove();

    _chunkViewData = { kbId, kbName: docName || '文档分块', page: 1, pageSize: 9999, total: 0, chunks: [], documentMode: true };
    _openChunkDetailDialog(docName || '文档分块', false);

    try {
        const data = await api('GET', `/kb/${kbId}/documents/${docId}/chunks`);
        if (!data.success) {
            document.getElementById('chunk-list-content').innerHTML = '<div class="rag-parent-chunk-empty">加载失败</div>';
            return;
        }
        _chunkViewData.chunks = data.chunks || [];
        _chunkViewData.total = data.total || _chunkViewData.chunks.length;
        _chunkViewData.localOnly = false;
        _renderChunkList();
    } catch (err) {
        document.getElementById('chunk-list-content').innerHTML = `<div class="rag-parent-chunk-empty is-error">加载失败: ${escapeHtml(err.message)}</div>`;
    }
}

function _openChunkDetailDialog(title, showExport = true) {
    const overlay = document.createElement('div');
    overlay.className = 'new-item-dialog';
    overlay.id = 'chunk-detail-dialog';
    overlay.innerHTML = `
        <div class="dialog-box rag-chunk-dialog">
            <div class="rag-chunk-dialog-head">
                <div class="rag-chunk-dialog-title">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
                    <div>
                        <strong>分块明细</strong>
                        <span>${escapeHtml(title)}</span>
                    </div>
                </div>
                <div class="rag-chunk-dialog-actions">
                    ${showExport ? `<button class="dialog-btn" onclick="_exportChunksJSON()" title="导出 JSON">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        导出 JSON
                    </button>` : ''}
                    <button class="icon-btn" onclick="document.getElementById('chunk-detail-dialog').remove()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                </div>
            </div>
            <div id="chunk-list-content" class="rag-parent-chunk-list">
                <div class="rag-parent-chunk-empty"><div class="loading-dots"><span></span><span></span><span></span></div></div>
            </div>
            <div id="chunk-pagination" class="rag-chunk-pagination"></div>
        </div>`;
    document.body.appendChild(overlay);
}

async function _loadChunksPage() {
    const { kbId, kbName, page, pageSize } = _chunkViewData;

    // 移除旧弹窗
    const old = document.getElementById('chunk-detail-dialog');
    if (old) old.remove();
    _openChunkDetailDialog(kbName);

    try {
        const data = await api('GET', `/kb/${kbId}/chunks?page=${page}&page_size=${pageSize}`);
        if (!data.success) {
            document.getElementById('chunk-list-content').innerHTML = '<div style="color:var(--danger)">加载失败</div>';
            return;
        }
        let chunks = data.chunks || [];
        let total = data.total || 0;
        _chunkViewData.localOnly = false;
        if (!chunks.length) {
            const localChunks = await LocalDB.getKBChunks(kbId);
            if (localChunks.length) {
                total = localChunks.length;
                const start = (page - 1) * pageSize;
                chunks = localChunks.slice(start, start + pageSize).map(normalizeLocalChunkForView);
                _chunkViewData.localOnly = true;
            }
        }
        _chunkViewData.chunks = chunks;
        _chunkViewData.total = total;
        _renderChunkList();
    } catch (err) {
        try {
            const localChunks = await LocalDB.getKBChunks(kbId);
            if (localChunks.length) {
                const start = (page - 1) * pageSize;
                _chunkViewData.chunks = localChunks.slice(start, start + pageSize).map(normalizeLocalChunkForView);
                _chunkViewData.total = localChunks.length;
                _chunkViewData.localOnly = true;
                _renderChunkList();
                return;
            }
        } catch {}
        document.getElementById('chunk-list-content').innerHTML = `<div style="color:var(--danger)">加载失败: ${escapeHtml(err.message)}</div>`;
    }
}

function normalizeLocalChunkForView(chunk) {
    return {
        chunk_id: chunk.chunk_id,
        doc_id: chunk.doc_id,
        content: chunk.content || chunk.text || '',
        metadata: {
            ...(chunk.metadata || {}),
            document_id: chunk.doc_id || '',
            document_title: chunk.title || chunk.filename || '本地分块',
            source_type: 'browser_local',
            chunk_index: chunk.index,
            content_length: chunk.char_count || (chunk.text || chunk.content || '').length,
            embedding_model: chunk.embedding ? 'browser-local' : 'not-generated',
            chunk_strategy: 'local_fallback',
        },
    };
}

function getChunkParentTextForView(chunk) {
    const meta = chunk.metadata || {};
    return chunk.parent_text || meta.parent_content || chunk.display_text || chunk.text || chunk.content || '';
}

function getChunkChildTextForView(chunk) {
    return chunk.child_text || chunk.metadata?.child_text || chunk.content || chunk.text || '';
}

function groupChunksByParent(chunks) {
    const groups = [];
    const groupMap = new Map();

    chunks.forEach((chunk, idx) => {
        const meta = chunk.metadata || {};
        const parentText = getChunkParentTextForView(chunk);
        const childText = getChunkChildTextForView(chunk);
        const parentId = meta.parent_chunk_id || chunk.parent_chunk_id || chunk.display_chunk_id || chunk.chunk_id || `parent-${idx}`;
        const groupKey = `${chunk.doc_id || meta.document_id || ''}::${parentId}::${parentText.slice(0, 80)}`;

        if (!groupMap.has(groupKey)) {
            const group = {
                id: parentId,
                title: meta.heading_path || meta.document_title || chunk.title || chunk.source || `父块 ${groups.length + 1}`,
                source: chunk.doc_id || meta.document_id || chunk.source || '',
                page: meta.page_number || chunk.page || '',
                parentText,
                meta,
                chunks: [],
                firstIndex: idx,
            };
            groupMap.set(groupKey, group);
            groups.push(group);
        }

        groupMap.get(groupKey).chunks.push({
            ...chunk,
            childText,
            childLength: childText.length,
        });
    });

    return groups;
}

function renderParentChunkGroups(parentGroups, startIndex = 1) {
    return parentGroups.map((group, idx) => {
        const globalIdx = startIndex + idx;
        const parentText = group.parentText || '';
        const parentPreview = parentText.substring(0, 260) + (parentText.length > 260 ? '...' : '');
        const childCount = group.chunks.length;
        const childTotalLength = group.chunks.reduce((sum, item) => sum + (item.childLength || 0), 0);
        const meta = group.meta || {};
        const parentOverlapActual = Number(meta.parent_overlap_actual || 0);
        const parentOverlapExpected = Boolean(meta.parent_overlap_expected);
        const parentOverlapMissing = Boolean(meta.parent_overlap_missing);
        const parentOverlapBoundary = meta.parent_overlap_boundary || '';
        const parentOverlapLabel = parentOverlapActual > 0
            ? `父块重叠 ${parentOverlapActual} 字`
            : parentOverlapExpected
                ? '连续父块未检测到重叠'
                : '边界块，无前置重叠';
        return `
            <section class="rag-parent-chunk-card">
                <button class="rag-parent-chunk-summary" onclick="_toggleChunkExpand(this)">
                    <div class="rag-parent-chunk-summary-main">
                        <span class="rag-parent-chunk-index">${globalIdx}</span>
                        <div class="rag-parent-chunk-title-wrap">
                            <div class="rag-parent-chunk-title">${escapeHtml(group.title || '父块')}</div>
                            <div class="rag-parent-chunk-preview">${escapeHtml(parentPreview || '(空父块)')}</div>
                        </div>
                    </div>
                    <div class="rag-parent-chunk-summary-meta">
                        ${group.page ? `<span>第 ${escapeHtml(String(group.page))} 页</span>` : ''}
                        <span>父块 ${parentText.length} 字</span>
                        <span>${childCount} 个子块</span>
                        <span>子块 ${childTotalLength} 字</span>
                        <span>${escapeHtml(parentOverlapLabel)}</span>
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
                    </div>
                </button>
                <div class="chunk-expand-content rag-parent-chunk-detail" style="display:none">
                    <div class="rag-parent-section">
                        <div class="rag-parent-section-head">
                            <strong>父块内容</strong>
                            <span>回答上下文，含 overlap</span>
                        </div>
                        <div class="rag-parent-text">${escapeHtml(parentText || '(空父块)')}</div>
                    </div>
                    <div class="rag-parent-section">
                        <div class="rag-parent-section-head">
                            <strong>对应子块</strong>
                            <span>用于向量召回和命中定位</span>
                        </div>
                        <div class="rag-child-list">
                            ${group.chunks.map((child, childIdx) => {
                                const childMeta = child.metadata || {};
                                return `<div class="rag-child-item">
                                    <div class="rag-child-item-head">
                                        <span>子块 ${childIdx + 1}</span>
                                        <span>${child.childLength || 0} 字</span>
                                    </div>
                                    <div class="rag-child-text">${escapeHtml(child.childText || '')}</div>
                                    <div class="rag-child-meta">
                                        <span>chunk_id: ${escapeHtml(child.chunk_id || '')}</span>
                                        ${childMeta.chunk_index !== undefined ? `<span>序号: ${childMeta.chunk_index}</span>` : ''}
                                        ${childMeta.child_chunk_overlap ? `<span>overlap: ${childMeta.child_chunk_overlap}</span>` : ''}
                                    </div>
                                </div>`;
                            }).join('')}
                        </div>
                    </div>
                    <div class="rag-parent-meta-grid">
                        <span><b>document_id</b>${escapeHtml(meta.document_id || group.source || '')}</span>
                        <span><b>document_title</b>${escapeHtml(meta.document_title || '')}</span>
                        <span><b>parent_chunk_id</b>${escapeHtml(group.id || '')}</span>
                        <span><b>parent_size</b>${meta.parent_chunk_size || ''}</span>
                        <span><b>parent_overlap_config</b>${meta.parent_overlap_config || meta.parent_chunk_overlap || ''}</span>
                        <span><b>parent_overlap_actual</b>${parentOverlapActual}</span>
                        <span><b>parent_overlap_boundary</b>${escapeHtml(parentOverlapBoundary)}</span>
                        ${parentOverlapMissing ? '<span><b>parent_overlap_status</b>missing</span>' : ''}
                        <span><b>child_size</b>${meta.child_chunk_size || ''}</span>
                        <span><b>child_overlap</b>${meta.child_chunk_overlap || ''}</span>
                        <span><b>strategy</b>${escapeHtml(meta.chunk_strategy || '')}</span>
                    </div>
                </div>
            </section>`;
    }).join('');
}

function _renderChunkList() {
    const { chunks, total, page, pageSize, localOnly, documentMode } = _chunkViewData;
    const contentEl = document.getElementById('chunk-list-content');
    const paginationEl = document.getElementById('chunk-pagination');
    if (!contentEl) return;

    if (!chunks.length) {
        contentEl.innerHTML = '<div class="rag-parent-chunk-empty">暂无分块数据</div>';
        if (paginationEl) paginationEl.innerHTML = '';
        return;
    }

    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const parentGroups = groupChunksByParent(chunks);
    const noticeHtml = localOnly ? '<div class="rag-parent-chunk-notice">当前显示的是浏览器本地 IndexedDB 分块，尚未进入后端向量库。建议重新上传到后端或执行重建。</div>' : '';

    contentEl.innerHTML = noticeHtml + renderParentChunkGroups(parentGroups, (page - 1) * pageSize + 1);

    if (paginationEl) {
        paginationEl.style.display = documentMode ? 'none' : '';
        paginationEl.innerHTML = documentMode ? '' : `
            <span>共 ${total} 个子块，当前页聚合为 ${parentGroups.length} 个父块，第 ${page}/${totalPages} 页</span>
            <div class="rag-chunk-pagination-actions">
                <button class="dialog-btn" ${page <= 1 ? 'disabled' : ''} onclick="_chunkViewData.page=1;_loadChunksPage()">首页</button>
                <button class="dialog-btn" ${page <= 1 ? 'disabled' : ''} onclick="_chunkViewData.page--;_loadChunksPage()">上一页</button>
                <input type="number" id="chunk-page-input" value="${page}" min="1" max="${totalPages}" onkeydown="if(event.key==='Enter'){const p=parseInt(this.value);if(p>=1&&p<=${totalPages}){_chunkViewData.page=p;_loadChunksPage();}else{this.value=${page};}}" />
                <button class="dialog-btn" onclick="const p=parseInt(document.getElementById('chunk-page-input').value);if(p>=1&&p<=${totalPages}){_chunkViewData.page=p;_loadChunksPage();}">跳转</button>
                <button class="dialog-btn" ${page >= totalPages ? 'disabled' : ''} onclick="_chunkViewData.page++;_loadChunksPage()">下一页</button>
                <button class="dialog-btn" ${page >= totalPages ? 'disabled' : ''} onclick="_chunkViewData.page=${totalPages};_loadChunksPage()">末页</button>
            </div>`;
    }
}

function _toggleChunkExpand(headerEl) {
    const content = headerEl.nextElementSibling;
    const arrow = headerEl.querySelector('svg:last-child');
    if (content.style.display === 'none') {
        content.style.display = 'block';
        if (arrow) arrow.style.transform = 'rotate(180deg)';
    } else {
        content.style.display = 'none';
        if (arrow) arrow.style.transform = '';
    }
}

async function _exportChunksJSON() {
    const { kbId, kbName, total } = _chunkViewData;
    showToast('正在导出全部分块...', 'info');

    try {
        // 获取全部分块
        const allChunks = [];
        const pageSize = 100;
        const totalPages = Math.ceil(total / pageSize);
        for (let p = 1; p <= totalPages; p++) {
            const data = await api('GET', `/kb/${kbId}/chunks?page=${p}&page_size=${pageSize}`);
            if (data.chunks) {
                allChunks.push(...data.chunks.map(chunk => ({
                    chunk_id: chunk.chunk_id,
                    content: chunk.content,
                    metadata: chunk.metadata || {}
                })));
            }
        }

        const jsonStr = JSON.stringify(allChunks, null, 2);
        const blob = new Blob([jsonStr], { type: 'application/json;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `chunks_${kbName}_${new Date().toISOString().slice(0,10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
        showToast(`已导出 ${allChunks.length} 个分块`, 'success');
    } catch (err) {
        showToast('导出失败: ' + err.message, 'error');
    }
}

Object.assign(window, {
    scrollRAGMessage,
    renderRAGTurnIndex,
    updateRAGTurnIndexActive,
});
