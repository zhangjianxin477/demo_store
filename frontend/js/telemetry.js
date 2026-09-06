/* Best-effort product telemetry. Never blocks the UI or exposes query text. */
(function () {
    function record(event_name, properties, ids) {
        try {
            const payload = {
                event_name,
                source: 'frontend',
                properties: properties || {},
                session_id: ids?.session_id || window.ragSessionId || '',
                task_id: ids?.task_id || ''
            };
            const base = window.API_BASE || '/api/v1';
            fetch(`${base}/telemetry/events`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload), keepalive: true
            }).catch(() => {});
        } catch (_) {}
    }
    window.recordTelemetry = record;

    function esc(value) {
        const div = document.createElement('div'); div.textContent = String(value ?? ''); return div.innerHTML;
    }
    function card(label, value) {
        return `<div style="border:1px solid var(--border);border-radius:12px;padding:14px;background:var(--bg-surface,#fff)"><div style="font-size:11px;color:var(--text-muted)">${esc(label)}</div><strong style="display:block;font-size:24px;margin-top:6px">${esc(value)}</strong></div>`;
    }
    function rate(value) {
        if (value === null || value === undefined || value === '') return '—';
        const number = Number(value);
        return Number.isFinite(number) ? `${Math.round(number * 100)}%` : '—';
    }
    window.openTelemetryDashboard = async function () {
        const old = document.getElementById('telemetry-dashboard'); if (old) old.remove();
        const overlay = document.createElement('div'); overlay.id = 'telemetry-dashboard';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:2300;display:flex;align-items:center;justify-content:center;padding:24px;backdrop-filter:blur(6px)';
        overlay.innerHTML = `<div style="width:min(1120px,96vw);max-height:92vh;overflow:auto;background:var(--bg-surface,#fff);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow-xl);padding:22px"><div style="display:flex;justify-content:space-between;align-items:center"><div><h2 style="margin:0">Agent RAG 埋点看板</h2><div style="font-size:12px;color:var(--text-muted);margin-top:5px">真实事件数据 · 不展示模型隐藏推理和敏感凭据</div></div><button class="icon-btn" id="telemetry-close">×</button></div><div id="telemetry-body" style="margin-top:18px">正在加载指标…</div></div>`;
        document.body.appendChild(overlay); overlay.querySelector('#telemetry-close').onclick = () => overlay.remove();
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
        try {
            const base = window.API_BASE || '/api/v1';
            const [summary, recent] = await Promise.all([fetch(`${base}/telemetry/overview`).then(r => r.json()), fetch(`${base}/telemetry/events?limit=20`).then(r => r.json())]);
            const k = summary.kpi || {}, intents = summary.intents || {};
            const intentText = Object.entries(intents).sort((a,b)=>b[1]-a[1]).map(([name,count]) => `<span style="display:inline-block;padding:5px 9px;border-radius:999px;background:var(--bg-elevated,#f1f5f9);margin:3px;font-size:12px">${esc(name)} ${esc(count)}</span>`).join('') || '暂无数据';
            const rows = (recent.events || []).map(row => `<tr><td>${esc(row.event_name)}</td><td>${esc(row.source)}</td><td>${esc(row.task_id || '-')}</td><td>${esc(new Date(row.occurred_at).toLocaleString())}</td></tr>`).join('') || '<tr><td colspan="4">暂无事件</td></tr>';
            const freshness = summary.data_freshness_seconds == null ? '暂无事件' : `数据 ${summary.data_freshness_seconds} 秒前更新`;
            overlay.querySelector('#telemetry-body').innerHTML = `<div style="display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px">${card('Agent 创建', k.agent_tasks_created ?? 0)}${card('Agent 完成', k.agent_tasks_completed ?? 0)}${card('成功率', rate(k.task_success_rate))}${card('引用通过率', rate(k.citation_pass_rate))}${card('有效交付', k.effective_delivery_count ?? 0)}</div><div style="margin-top:8px;font-size:12px;color:var(--text-muted)">${esc(freshness)}；成功率按终态任务去重，引用通过率按每个任务最后一次核验计算。</div><section style="margin-top:18px"><h3 style="font-size:14px">意图分布</h3><div>${intentText}</div></section><section style="margin-top:18px"><h3 style="font-size:14px">最近事件</h3><div style="overflow:auto"><table style="width:100%;font-size:12px;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid var(--border)">事件</th><th style="text-align:left;padding:8px;border-bottom:1px solid var(--border)">来源</th><th style="text-align:left;padding:8px;border-bottom:1px solid var(--border)">任务</th><th style="text-align:left;padding:8px;border-bottom:1px solid var(--border)">时间</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
        } catch (error) { overlay.querySelector('#telemetry-body').innerHTML = `<div style="color:#b91c1c">看板加载失败：${esc(error.message || error)}</div>`; }
    };
})();
