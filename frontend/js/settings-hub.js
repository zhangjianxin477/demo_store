/* Unified settings center for operational Agent/RAG capabilities. */
(function () {
    const panels = {
        overview: { title: '运行概览', kicker: 'OBSERVABILITY' },
        models: { title: '模型与 API Key', kicker: 'MODEL PROVIDERS' },
        mcp: { title: 'MCP 连接', kicker: 'TOOL CONNECTIONS' },
        skills: { title: 'Skill 管理', kicker: 'AGENT WORKFLOWS' },
        email: { title: '邮件通道', kicker: 'DELIVERY' },
    };
    const esc = (value) => typeof escapeHtml === 'function' ? escapeHtml(String(value ?? '')) : String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const apiGet = (path) => api('GET', path, null, 20000);
    function card(label, value, hint, tone) { return `<div class="settings-stat"><span>${esc(label)}</span><strong class="${tone || ''}">${esc(value)}</strong><small>${esc(hint || '')}</small></div>`; }
    function rate(value) {
        if (value === null || value === undefined || value === '') return '—';
        const number = Number(value);
        return Number.isFinite(number) ? `${Math.round(number * 100)}%` : '—';
    }
    function setActive(panel) {
        document.querySelectorAll('.settings-hub-nav-item').forEach(item => item.classList.toggle('active', item.dataset.settingsPanel === panel));
        const meta = panels[panel] || panels.overview;
        const title = document.getElementById('settings-hub-title');
        if (title) title.textContent = meta.title;
        const kicker = document.querySelector('.settings-hub-kicker');
        if (kicker) kicker.textContent = meta.kicker;
    }
    function actionCard(icon, title, desc, action, button) { return `<section class="settings-action-card"><div class="settings-action-icon">${icon}</div><div class="settings-action-copy"><h3>${esc(title)}</h3><p>${esc(desc)}</p></div><button class="btn btn-primary" onclick="${action}">${esc(button)}</button></section>`; }

    async function renderOverview(target) {
        target.innerHTML = '<div class="settings-loading">正在读取运行指标…</div>';
        try {
            const [summary, health] = await Promise.all([apiGet('/telemetry/overview'), apiGet('/health')]);
            // The API contract uses the singular `kpi`.  Keep a legacy
            // fallback for older servers, but never treat the whole response
            // object as KPI data (that was the source of the all-zero view).
            const k = summary.kpi || summary.kpis || {};
            const deps = health.dependencies || {};
            const healthLabel = health.status === 'healthy' ? '健康' : '降级';
            target.innerHTML = `<div class="settings-section-intro"><div><h3>系统运行状态</h3><p>集中查看 Agent、模型连接、向量库和交付通道。</p></div><span class="settings-health-pill ${health.status === 'healthy' ? 'ok' : 'warn'}"><i></i>${healthLabel}</span></div><div class="settings-stats-grid">${card('Agent 创建', k.agent_tasks_created ?? 0, '累计任务')}${card('Agent 完成', k.agent_tasks_completed ?? 0, '已完成任务')}${card('任务成功率', rate(k.task_success_rate), k.task_terminal_count ? `已统计 ${k.task_terminal_count} 个终态任务` : '暂无终态任务')}${card('引用通过率', rate(k.citation_pass_rate), k.citation_verified_count ? `已核验 ${k.citation_verified_count} 次` : '暂无核验数据')}${card('有效交付', k.effective_delivery_count ?? 0, '去重后的已交付产物')}</div><div class="settings-section-intro compact"><div><h3>依赖状态</h3><p>异常时系统会自动降级，但这里会明确显示真实状态。</p></div></div><div class="settings-dependency-grid"><div class="settings-dependency"><span>LLM</span><b class="${deps.llm?.ready ? 'ok' : 'bad'}">${deps.llm?.ready ? '已连接' : '未就绪'}</b><small>${esc(deps.llm?.model || '未配置')}</small></div><div class="settings-dependency"><span>Embedding</span><b class="${deps.embedding?.ready ? 'ok' : 'bad'}">${deps.embedding?.ready ? '已连接' : '降级中'}</b><small>${esc(deps.embedding?.provider || deps.embedding?.model || '未配置')}</small></div><div class="settings-dependency"><span>向量数据库</span><b class="${deps.vector_store?.ready ? 'ok' : 'bad'}">${deps.vector_store?.ready ? '已连接' : '未连接'}</b><small>${esc(deps.vector_store?.backend || 'local')}</small></div></div>`;
        } catch (err) { target.innerHTML = `<div class="settings-error">运行指标读取失败：${esc(err.message || err)}</div>`; }
    }
    async function renderModels(target) {
        target.innerHTML = `<div class="settings-section-intro"><div><h3>模型服务配置</h3><p>所有 API Key 仍由原有真实管理页面保存和测试。</p></div></div><div class="settings-managed-grid">${actionCard('⌁', 'LLM / Embedding / Reranker', '统一配置模型地址、模型名、维度和真实 API Key。', "openModelApiKeysDialog()", '打开 API Key 管理')}${actionCard('◌', '向量数据库状态', '检查 Milvus 是否真正接管检索，避免静默回退本地。', "openSettingsHealth()", '查看连接状态')}</div><div id="settings-model-status" class="settings-inline-status">正在读取当前模型状态…</div>`;
        try {
            const data = await apiGet('/rag/status');
            const emb = data.embedding || {};
            const vec = data.vector_store || {};
            const index = emb.index_model_status || {};
            const activeVector = vec.active_query_backend || vec.backend || 'local';
            const milvusActive = activeVector === 'milvus' && vec.milvus_ready === true;
            const modelReady = emb.index_rebuild_required !== true;
            const hasVectors = Number(index.total_count || 0) > 0;
            const consistent = hasVectors && index.consistent === true;
            const milvusDimensionMismatch = !milvusActive && /dimension|维度/i.test(String(vec.milvus_error || ''));
            const consistencyLabel = !hasVectors ? '暂无向量' : (consistent ? '已统一' : '不统一，需清理');
            const consistencyClass = !hasVectors ? 'muted' : (consistent ? 'ok' : 'bad');
            const modelList = (index.models || []).join('、') || '暂无索引';
            const dimensionList = (index.dimensions || []).map(Number).filter(Number.isFinite).join('、') || '暂无索引';
            const vectorHint = vec.milvus_error && !milvusActive ? ` · ${esc(vec.milvus_error).slice(0, 180)}` : '';
            const needsCleanup = (hasVectors && !consistent) || milvusDimensionMismatch;
            const cleanupText = milvusDimensionMismatch ? '云端 Collection 是 2560 维，当前模型是 1024 维。清理后会按 1024 维重建 Collection，原始文档不受影响。' : '检测到不同模型或维度的历史向量，继续入库会失败或导致召回不准。';
            byId('settings-model-status').innerHTML = `<div class="settings-status-row"><span>当前 LLM</span><b>${esc(data.llm?.model || '未配置')}</b><em class="${data.llm?.available ? 'ok' : 'bad'}">${data.llm?.available ? '可用' : '不可用'}</em></div><div class="settings-status-row"><span>当前 Embedding</span><b>${esc(emb.model || '未配置')} · ${esc(emb.dimension || '-')} 维</b><em class="${emb.degraded_to_hash || !modelReady ? 'bad' : 'ok'}">${emb.degraded_to_hash ? '哈希降级' : (!modelReady ? '需重建索引' : '正常')}</em></div><div class="settings-status-row"><span>向量一致性</span><b>${esc(modelList)} · 历史 ${esc(dimensionList)} 维</b><em class="${consistencyClass}">${consistencyLabel}</em></div><div class="settings-status-row"><span>向量后端</span><b>${esc(activeVector)}${vectorHint}</b><em class="${milvusActive ? 'ok' : 'bad'}">${milvusActive ? 'Milvus 正在提供检索' : '当前使用本地检索'}</em></div>${needsCleanup ? `<div class="settings-index-warning"><span>${cleanupText}</span><button class="btn btn-danger" onclick="clearVectorIndexes()">清理并重建向量索引</button></div>` : ''}`;
        } catch (err) { byId('settings-model-status').textContent = `状态读取失败：${err.message || err}`; }
    }
    window.openSettingsHealth = async function () { try { const data = await apiGet('/health'); showToast(`系统状态：${data.status}；向量后端：${data.dependencies?.vector_store?.backend || 'local'}`, data.status === 'healthy' ? 'success' : 'warning'); } catch (e) { showToast(`健康检查失败：${e.message}`, 'error'); } };
    window.clearVectorIndexes = async function () {
        if (!confirm('确定清理全部向量索引吗？原始文档和知识库目录不会删除，但清理后必须重新批量入库。')) return;
        try {
            showToast('正在清理向量索引…', 'info');
            const result = await api('POST', '/rag/vectors/clear', {});
            if (!result?.success) throw new Error(result?.message || '清理失败');
            showToast(`已清理 ${result.local_removed || 0} 个向量分块，请重新批量入库`, 'success');
            await switchSettingsPanel('models');
        } catch (err) { showToast(`向量索引清理失败：${err.message || err}`, 'error'); }
    };
    async function renderMcp(target) {
        target.innerHTML = '<div class="settings-loading">正在读取 MCP 连接…</div>';
        try { const [runtime, wechat] = await Promise.all([apiGet('/agent/runtime'), apiGet('/mcp/wechat/status')]); const servers = runtime.mcp_servers || []; target.innerHTML = `<div class="settings-section-intro"><div><h3>MCP 与外部工具</h3><p>工具调用经过白名单、参数校验和人工审批。</p></div><button class="btn btn-primary" onclick="openWeChatMcpDialog()">配置微信公众号</button></div><div class="settings-managed-grid">${actionCard('文', '微信公众号 MCP', wechat.configured ? '已配置，可测试 access_token、同步草稿和申请发布。' : '尚未配置 AppID / AppSecret。', "openWeChatMcpDialog()", wechat.configured ? '打开配置' : '立即配置')}${actionCard('⟁', 'MCP Server 注册表', `${servers.length} 个 Server 已注册，运行时会按需发现 tools/list。`, "showToast('当前 MCP Server 通过服务端配置 MCP_SERVERS_JSON 管理', 'info')", '查看说明')}</div><div class="settings-server-list">${servers.length ? servers.map(item => `<div class="settings-server-row"><span class="settings-server-dot ${item.enabled ? 'on' : ''}"></span><div><b>${esc(item.name)}</b><small>${esc(item.endpoint || '')}</small></div><em>${item.enabled ? '已启用' : '已停用'}</em><span>${(item.tools || []).length} 个工具</span></div>`).join('') : '<div class="settings-empty">暂无外部 MCP Server；微信公众号使用内置安全适配器。</div>'}</div>`; } catch (err) { target.innerHTML = `<div class="settings-error">MCP 状态读取失败：${esc(err.message || err)}</div>`; }
    }
    async function renderSkills(target) {
        target.innerHTML = '<div class="settings-loading">正在读取 Skill 注册表…</div>';
        try { const data = await apiGet('/passive/skills'); const skills = data.skills || []; const enabled = skills.filter(item => item.status === 'enabled').length; target.innerHTML = `<div class="settings-section-intro"><div><h3>Skill 工作流</h3><p>Skill 决定采集、清洗、摘要、汇总和投递的业务流程。</p></div><div class="settings-toolbar-actions"><button class="btn btn-primary" onclick="openPassiveSkillsDialog()">打开 Skill 管理</button><button class="btn" onclick="openPassiveSkillsDialog();setTimeout(()=>document.getElementById('passive-skill-file')?.click(),80)">上传 Skill</button></div></div><div class="settings-stats-grid settings-stats-compact">${card('已挂载', enabled, `共 ${skills.length} 个 Skill`)}${card('执行方式', 'Skill → Agent', '定时或手动运行')}${card('输出', '日报 / HTML', '可发送邮件')}</div><div class="settings-skill-list">${skills.length ? skills.map(item => `<div class="settings-skill-row"><span class="settings-server-dot ${item.status === 'enabled' ? 'on' : ''}"></span><div><b>${esc(item.name || item.skill_id)}</b><small>${esc(item.description || item.skill_id)}</small></div><em class="${item.status === 'enabled' ? 'ok' : ''}">${item.status === 'enabled' ? '已挂载' : '未启用'}</em><span>v${esc(item.version || '-')}</span></div>`).join('') : '<div class="settings-empty">尚未挂载 Skill。</div>'}</div>`; } catch (err) { target.innerHTML = `<div class="settings-error">Skill 读取失败：${esc(err.message || err)}</div>`; }
    }
    async function renderEmail(target) { target.innerHTML = `<div class="settings-section-intro"><div><h3>邮件推送通道</h3><p>日报生成后通过真实 SMTP 发送，配置和测试沿用现有能力。</p></div><div class="settings-toolbar-actions"><button class="btn btn-primary" onclick="showEmailConfig()">邮件设置</button><button class="btn" onclick="testPassiveEmail()">真实发信测试</button></div></div><div class="settings-managed-grid">${actionCard('✉', '日报投递', '配置 QQ 邮箱授权码、收件人和日报推送策略。', "showEmailConfig()", '打开邮件设置')}${actionCard('✓', '发送测试', '发送一封真实测试邮件，确认 SMTP 配置可用。', "testPassiveEmail()", '发送测试邮件')}</div><div class="settings-inline-status">邮件发送失败会记录在运行详情中；定时任务仍受 Skill 的时区和 Cron 配置控制。</div>`; }
    window.switchSettingsPanel = async function (panel) { const target = document.getElementById('settings-hub-content'); if (!target) return; panel = panels[panel] ? panel : 'overview'; setActive(panel); const renderers = { overview: renderOverview, models: renderModels, mcp: renderMcp, skills: renderSkills, email: renderEmail }; await renderers[panel](target); };
    window.openSettingsHub = function (panel) { const modal = byId('settings-hub'); if (!modal) return; modal.style.display = 'flex'; switchSettingsPanel(panel || 'overview'); };
    window.closeSettingsHub = function () { const modal = byId('settings-hub'); if (modal) modal.style.display = 'none'; };
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && byId('settings-hub')?.style.display === 'flex') closeSettingsHub(); });
})();
