(function () {
    const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[ch]));
    const byId = id => document.getElementById(id);
    const value = id => byId(id)?.value || '';
    const key = id => String(id || '').replace(/[^a-zA-Z0-9_-]/g, '_');

    // Inline handlers in dynamically-created modal HTML run in the global
    // scope, not inside this IIFE. Expose the tiny lookup helper so the
    // existing close/cancel handlers remain functional.
    window.byId = window.byId || byId;

    function ensureStyles() {
        if (byId('passive-workbench-styles')) return;
        const style = document.createElement('style');
        style.id = 'passive-workbench-styles';
        style.textContent = `.passive-workbench{display:grid;gap:14px}.passive-overview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.passive-stat{border:1px solid var(--border);border-radius:12px;padding:12px;background:var(--bg-surface,#fff)}.passive-stat small{display:block;color:var(--text-muted);font-size:11px;margin-bottom:5px}.passive-stat strong{font-size:15px}.passive-card{border:1px solid var(--border);border-radius:14px;background:var(--bg-surface,#fff);overflow:hidden}.passive-card-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px}.passive-card-title{display:flex;gap:8px;align-items:center}.passive-dot{width:8px;height:8px;border-radius:50%;background:#94a3b8}.passive-dot.on{background:#10b981;box-shadow:0 0 0 3px #d1fae5}.passive-help{color:var(--text-muted);font-size:11px;line-height:1.5}.passive-actions{display:flex;gap:7px;flex-wrap:wrap}.passive-status{font-size:11px;padding:3px 7px;border-radius:999px;background:var(--bg-elevated,#f1f5f9)}.passive-status.completed{color:#047857;background:#d1fae5}.passive-status.failed{color:#b91c1c;background:#fee2e2}.passive-status.partial{color:#92400e;background:#fef3c7}.passive-status.running{color:#1d4ed8;background:#dbeafe}.passive-run{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 10px;border-bottom:1px solid var(--border-subtle,#eee);font-size:12px}.passive-run:last-child{border-bottom:0}.passive-editor{position:fixed;inset:0;background:var(--bg-overlay,rgba(15,23,42,.45));display:flex;align-items:center;justify-content:center;z-index:2200;backdrop-filter:blur(7px)}.passive-editor-box{width:min(920px,94vw);max-height:90vh;display:flex;flex-direction:column;background:var(--bg-surface,#fff);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow-xl);overflow:hidden}.passive-editor-head{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--border-subtle,#eee)}.passive-editor-body{padding:16px 20px;overflow:auto}.passive-editor-footer{display:flex;justify-content:flex-end;gap:8px;padding:12px 20px;border-top:1px solid var(--border-subtle,#eee)}.passive-config-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.passive-field{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--text-secondary)}.passive-field input,.passive-field select,.passive-field textarea{width:100%;box-sizing:border-box;border:1px solid var(--border);border-radius:8px;padding:9px;background:var(--bg-input,#fff);color:var(--text-primary);font:inherit}.passive-field textarea{min-height:82px;resize:vertical;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px}.passive-field-full{grid-column:1/-1}.passive-time-row{display:flex;gap:8px;align-items:center}.passive-time-row input{flex:1}.passive-trace{margin-top:10px;border:1px solid var(--border-subtle,#eee);border-radius:8px;max-height:260px;overflow:auto}.passive-trace-row{padding:7px 9px;border-bottom:1px solid var(--border-subtle,#eee);font-size:11px}.passive-trace-row:last-child{border-bottom:0}.passive-trace-row b{color:var(--primary)}.passive-live{width:min(560px,92vw)}.passive-live-progress{height:8px;background:var(--bg-elevated,#eef2f7);border-radius:999px;overflow:hidden;margin:14px 0}.passive-live-progress i{display:block;height:100%;width:18%;background:linear-gradient(90deg,#2563eb,#06b6d4);border-radius:inherit;transition:width .35s ease}.passive-live-status{font-size:14px}.passive-live-mail{margin-top:8px;padding:9px 11px;border-radius:8px;background:var(--bg-elevated,#f8fafc);font-size:12px}@media(max-width:680px){.passive-overview,.passive-config-grid{grid-template-columns:1fr}.passive-field-full{grid-column:auto}.passive-card-head{align-items:flex-start;flex-direction:column}}`;
        document.head.appendChild(style);
    }

    window.openPassiveSkillsDialog = function () { const modal = byId('passive-skills-modal'); if (!modal) return; ensureStyles(); modal.style.display = 'flex'; loadPassiveSkills(); };
    window.closePassiveSkillsDialog = function () { const modal = byId('passive-skills-modal'); if (modal) modal.style.display = 'none'; };

    if (!window.__passiveModalHandlersInstalled) {
        window.__passiveModalHandlersInstalled = true;
        document.addEventListener('click', event => {
            if (event.target?.classList?.contains('passive-editor')) event.target.remove();
        });
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape') return;
            ['passive-config-editor', 'passive-skill-editor', 'passive-run-detail', 'passive-live-status']
                .map(byId).filter(Boolean).forEach(node => node.remove());
        });
    }

    window.uploadPassiveSkill = async function () {
        if (typeof requireWriteAccess === 'function' && !requireWriteAccess()) return;
        const input = byId('passive-skill-file'), file = input?.files?.[0];
        if (!file) { const status = byId('passive-upload-status'); if (status) status.textContent = '请先点击“选择文件”，选中 .md 文件后再上传'; return showToast?.('请先选择 SKILL.md 文件', 'warning'); }
        const form = new FormData(); form.append('file', file);
        try { const status = byId('passive-upload-status'); if (status) status.textContent = `正在上传：${file.name}`; const data = await api('POST', '/passive/skills/upload', form, 30000); input.value = ''; if (status) status.textContent = `已上传：${file.name}`; showToast?.(`Skill ${data.skill?.name || ''} 上传成功，请点击“启用”`, 'success'); await loadPassiveSkills(); }
        catch (e) { showToast?.(`上传失败：${e.message}`, 'error'); }
    };

    document.addEventListener('change', event => {
        if (event.target?.id !== 'passive-skill-file') return;
        const file = event.target.files?.[0], status = byId('passive-upload-status');
        if (status) status.textContent = file ? `已选择：${file.name}` : '';
    });

    function renderSkill(skill) {
        const schedule = skill.schedule || {}, output = skill.output || {}, delivery = skill.delivery || {};
        const recipientText = (delivery.recipients || (delivery.email ? [delivery.email] : [])).join(', ') || '默认发件邮箱';
        const protectionNote = skill.status === 'enabled' ? '<div class="passive-help" style="margin-top:6px;color:#047857">已挂载 Skill 受保护，不能直接删除；如需卸载，请先停用。</div>' : '';
        const uninstallAction = skill.status === 'enabled' ? '' : `<button class="btn passive-uninstall-btn" onclick="uninstallPassiveSkill('${esc(skill.skill_id)}')">卸载</button>`;
        return `<section class="passive-card"><div class="passive-card-head"><div><div class="passive-card-title"><span class="passive-dot ${skill.status === 'enabled' ? 'on' : ''}"></span><strong>${esc(skill.name)}</strong><span class="passive-status ${skill.status === 'enabled' ? 'completed' : ''}">${skill.status === 'enabled' ? '已挂载' : '未启用'}</span></div><div class="passive-help" style="margin-top:7px">${esc(skill.description || skill.skill_id)}</div><div class="passive-help" style="margin-top:4px">v${esc(skill.version)} · ${esc(schedule.cron || '未设置计划')} · ${esc(schedule.timezone || 'Asia/Shanghai')} · 每条摘要约300字，总述约500字</div>${protectionNote}</div><div class="passive-actions"><button class="btn" onclick="togglePassiveSkill('${esc(skill.skill_id)}','${esc(skill.status)}')">${skill.status === 'enabled' ? '停用' : '启用'}</button><button class="btn btn-primary" onclick="openPassiveSkillConfig('${esc(skill.skill_id)}')">编辑配置</button><button class="btn" onclick="openPassiveSkillEditor('${esc(skill.skill_id)}')">编辑正文</button>${uninstallAction}</div></div><div style="padding:0 16px 14px"><div class="passive-help">收件人：${esc(recipientText)} · 输出：${esc(output.format || 'markdown_and_html')} · 最多 ${esc(output.max_items || 10)} 条</div><div class="passive-actions" style="margin-top:10px"><button class="btn" onclick="runPassiveSkill('${esc(skill.skill_id)}',true)">立即试跑</button><button class="btn btn-primary" onclick="runPassiveSkill('${esc(skill.skill_id)}',false)">运行并真实发邮件</button></div></div></section>`;
    }
    function renderRuns(rows) {
        if (!rows.length) return '<div class="passive-help" style="padding:12px">暂无运行记录</div>';
        return rows.map(row => `<div class="passive-run"><div><strong>${esc(row.skill_id)}</strong><div class="passive-help">${esc(row.logical_date || '')} · ${esc(row.stage || '')} · ${row.item_count || 0} 条 · ${row.duration_seconds ? esc(row.duration_seconds) + 's' : '进行中'}</div></div><div class="passive-actions"><span class="passive-status ${esc(row.status)}">${esc(row.status)}</span><button class="btn" onclick="viewPassiveRun('${esc(row.run_id)}')">详情</button>${row.artifact_path ? `<a class="btn" href="/api/v1/passive/runs/${encodeURIComponent(row.run_id)}/artifact" target="_blank">日报</a>` : ''}<button class="btn" style="color:#b91c1c" onclick="deletePassiveRun('${esc(row.run_id)}')">删除记录</button></div></div>`).join('');
    }
    window.loadPassiveSkills = async function () {
        const list = byId('passive-skills-list'), runs = byId('passive-runs-list'); if (!list) return;
        ensureStyles(); list.innerHTML = '<div class="passive-help">正在读取 Skill 注册表…</div>';
        try {
            const [skillData, runData, mailData] = await Promise.all([api('GET', '/passive/skills', null, 12000), api('GET', '/passive/runs?limit=12', null, 12000), api('GET', '/wiki/email/config', null, 12000)]);
            // Older running backends may retain archived Skill metadata for
            // audit. They must never be rendered as active/manageable Skills.
            const skills = (skillData.skills || []).filter(skill => skill.status !== 'archived'), rows = runData.runs || [], mail = mailData.config || {};
            const enabled = skills.filter(x => x.status === 'enabled').length;
            list.innerHTML = `<div class="passive-workbench"><div class="passive-overview"><div class="passive-stat"><small>已挂载 Skill</small><strong>${enabled}/${skills.length}</strong></div><div class="passive-stat"><small>邮件通道</small><strong style="color:${mail.enabled && mail.smtp_host ? '#059669' : '#dc2626'}">${mail.enabled && mail.smtp_host ? '已配置' : '未就绪'}</strong></div><div class="passive-stat"><small>执行方式</small><strong>Skill → Agent → 日报</strong></div></div>${skills.length ? skills.map(renderSkill).join('') : '<div class="passive-help">尚未挂载 Skill，请上传 SKILL.md。</div>'}</div>`;
            if (runs) runs.innerHTML = renderRuns(rows);
            const status = byId('passive-email-status'); if (status) status.textContent = mail.enabled && mail.smtp_host ? `邮件已配置：${mail.smtp_host}:${mail.smtp_port}` : '邮件未配置（点击“邮件设置”）';
        } catch (e) { list.innerHTML = `<div style="color:#dc2626">加载失败：${esc(e.message)}</div>`; }
    };

    window.openPassiveSkillConfig = async function (skillId) {
        try {
            const data = await api('GET', `/passive/skills/${encodeURIComponent(skillId)}`, null, 12000), skill = data.skill || {}, schedule = skill.schedule || {}, filters = skill.filters || {}, output = skill.output || {}, delivery = skill.delivery || {};
            const modal = document.createElement('div'); modal.className = 'passive-editor'; modal.id = 'passive-config-editor';
            const id = key(skillId), cronParts = String(schedule.cron || '0 8 * * *').split(/\s+/);
            modal.innerHTML = `<div class="passive-editor-box"><div class="passive-editor-head"><div><strong>编辑 Skill 配置</strong><div class="passive-help">${esc(skill.name || skillId)} · 保存后生成新版本</div></div><button class="icon-btn" onclick="byId('passive-config-editor')?.remove()">×</button></div><div class="passive-editor-body"><div class="passive-config-grid"><label class="passive-field">快捷计划<select id="pc-preset-${id}" onchange="applyPassiveConfigPreset('${id}')"><option value="daily8">每天 08:00</option><option value="daily9">每天 09:00</option><option value="every6">每 6 小时</option><option value="custom">自定义时间</option></select></label><label class="passive-field">时区<select id="pc-timezone-${id}"><option value="Asia/Shanghai" ${schedule.timezone === 'Asia/Shanghai' ? 'selected' : ''}>中国标准时间（Asia/Shanghai）</option><option value="UTC" ${schedule.timezone === 'UTC' ? 'selected' : ''}>UTC</option></select></label><label class="passive-field">自定义每天执行时间<div class="passive-time-row"><input id="pc-hour-${id}" type="number" min="0" max="23" value="${Number(cronParts[1]) >= 0 ? Number(cronParts[1]) : 8}"><span>:</span><input id="pc-minute-${id}" type="number" min="0" max="59" value="${Number(cronParts[0]) >= 0 ? Number(cronParts[0]) : 0}"></div><span class="passive-help">选择“自定义时间”后，保存时自动生成每日 cron</span></label><label class="passive-field">Cron 表达式<input id="pc-cron-${id}" value="${esc(schedule.cron || '0 8 * * *')}" placeholder="分 时 日 月 周"></label><label class="passive-field">回看时间窗口（小时）<input id="pc-hours-${id}" type="number" min="1" max="720" value="${esc(schedule.lookback_hours || 24)}"></label><label class="passive-field">最多热点条数<input id="pc-max-${id}" type="number" min="1" max="50" value="${esc(output.max_items || 10)}"></label><label class="passive-field">输出格式<select id="pc-format-${id}"><option value="markdown_and_html" ${output.format === 'markdown_and_html' ? 'selected' : ''}>Markdown + HTML</option><option value="markdown" ${output.format === 'markdown' ? 'selected' : ''}>仅 Markdown</option><option value="html" ${output.format === 'html' ? 'selected' : ''}>仅 HTML</option></select></label><label class="passive-field">收件人（多个逗号分隔）<input id="pc-mail-${id}" value="${esc((delivery.recipients || (delivery.email ? [delivery.email] : [])).join(', '))}" placeholder="留空则使用邮件设置中的发件邮箱"></label><label class="passive-field">包含关键词<textarea id="pc-include-${id}">${esc((filters.include_keywords || []).join(', '))}</textarea></label><label class="passive-field">排除关键词<textarea id="pc-exclude-${id}">${esc((filters.exclude_keywords || []).join(', '))}</textarea></label><label class="passive-field passive-field-full">来源配置 JSON<textarea id="pc-sources-${id}" style="min-height:180px">${esc(JSON.stringify(skill.sources || [], null, 2))}</textarea></label></div></div><div class="passive-editor-footer"><button class="dialog-btn" onclick="byId('passive-config-editor')?.remove()">取消</button><button class="dialog-btn primary" onclick="savePassiveSkillConfigModal('${esc(skillId)}')">保存配置</button></div></div>`;
            document.body.appendChild(modal);
        } catch (e) { showToast?.(`读取 Skill 配置失败：${e.message}`, 'error'); }
    };
    window.applyPassiveConfigPreset = function (id) { const preset = value(`pc-preset-${id}`), cron = byId(`pc-cron-${id}`), hour = byId(`pc-hour-${id}`), minute = byId(`pc-minute-${id}`); if (preset === 'daily9') { hour.value = 9; minute.value = 0; cron.value = '0 9 * * *'; } else if (preset === 'every6') cron.value = '0 */6 * * *'; else if (preset === 'daily8') { hour.value = 8; minute.value = 0; cron.value = '0 8 * * *'; } else if (preset === 'custom') cron.value = `${Number(minute.value) || 0} ${Number(hour.value) || 0} * * *`; };
    window.savePassiveSkillConfigModal = async function (skillId) {
        if (typeof requireWriteAccess === 'function' && !requireWriteAccess()) return;
        const id = key(skillId), split = name => value(`pc-${name}-${id}`).split(/[,，\n]/).map(x => x.trim()).filter(Boolean); let sources;
        try { sources = JSON.parse(value(`pc-sources-${id}`) || '[]'); if (!Array.isArray(sources)) throw new Error('来源必须是数组'); } catch (e) { return showToast?.(`来源 JSON 无效：${e.message}`, 'error'); }
        const preset = value(`pc-preset-${id}`); let cron = value(`pc-cron-${id}`); if (preset === 'custom') cron = `${Math.max(0, Math.min(59, Number(value(`pc-minute-${id}`) || 0)))} ${Math.max(0, Math.min(23, Number(value(`pc-hour-${id}`) || 0)))} * * *`;
        const payload = {schedule: {timezone: value(`pc-timezone-${id}`) || 'Asia/Shanghai', cron, lookback_hours: Number(value(`pc-hours-${id}`) || 24)}, output: {format: value(`pc-format-${id}`) || 'markdown_and_html', max_items: Number(value(`pc-max-${id}`) || 10)}, delivery: {channel: 'qq_email', recipients: split('mail')}, filters: {include_keywords: split('include'), exclude_keywords: split('exclude')}, sources};
        try { await api('PATCH', `/passive/skills/${encodeURIComponent(skillId)}`, payload, 18000); byId('passive-config-editor')?.remove(); showToast?.('Skill 配置已保存为新版本', 'success'); loadPassiveSkills(); } catch (e) { showToast?.(`保存失败：${e.message}`, 'error'); }
    };
    window.togglePassiveSkill = async function (skillId, status) { if (typeof requireWriteAccess === 'function' && !requireWriteAccess()) return; try { await api('POST', `/passive/skills/${encodeURIComponent(skillId)}/${status === 'enabled' ? 'disable' : 'enable'}`, null, 12000); showToast?.(status === 'enabled' ? 'Skill 已停用' : 'Skill 已启用', 'success'); loadPassiveSkills(); } catch (e) { showToast?.(`操作失败：${e.message}`, 'error'); } };
    window.uninstallPassiveSkill = async function (skillId) { if (typeof requireWriteAccess === 'function' && !requireWriteAccess()) return; if (!confirm('确认卸载这个未启用的 Skill？历史运行记录和版本会保留。')) return; try { await api('DELETE', `/passive/skills/${encodeURIComponent(skillId)}`, null, 12000); showToast?.('Skill 已卸载（历史版本保留）', 'success'); loadPassiveSkills(); } catch (e) { showToast?.(`卸载失败：${e.message}`, 'error'); } };
    const stageLabels = { collecting: '正在采集来源', processing: '正在清洗、筛选和去重', writing: '正在生成中文摘要与总述', saving: '正在保存日报文件', completed: '日报生成完成', failed: '执行失败' };
    function ensureLiveDialog(runId, dryRun) {
        byId('passive-live-status')?.remove();
        const dialog = document.createElement('div'); dialog.className = 'passive-editor'; dialog.id = 'passive-live-status';
        dialog.innerHTML = `<div class="passive-editor-box passive-live"><div class="passive-editor-head"><div><strong>${dryRun ? '日报试跑进度' : '日报发送进度'}</strong><div class="passive-help">任务 ${esc(runId)}</div></div><button class="icon-btn" onclick="byId('passive-live-status')?.remove()">×</button></div><div class="passive-editor-body"><div id="passive-live-stage" class="passive-live-status">任务已启动，正在准备…</div><div class="passive-live-progress"><i id="passive-live-bar"></i></div><div id="passive-live-count" class="passive-help">正在连接后端读取实时状态…</div><div id="passive-live-mail" class="passive-live-mail">邮件状态：等待执行结果</div></div><div class="passive-editor-footer"><button class="dialog-btn primary" onclick="byId('passive-live-status')?.remove()">后台运行</button></div></div>`;
        document.body.appendChild(dialog);
    }
    window.watchPassiveRun = async function (runId, dryRun) {
        ensureLiveDialog(runId, dryRun);
        const started = Date.now(); let finished = false;
        while (!finished && Date.now() - started < 15 * 60 * 1000) {
            try {
                const data = await api('GET', `/passive/runs/${encodeURIComponent(runId)}`, null, 12000), run = data.run || {};
                const stage = run.stage || 'collecting', label = stageLabels[stage] || stage;
                const stageEl = byId('passive-live-stage'), countEl = byId('passive-live-count'), mailEl = byId('passive-live-mail'), bar = byId('passive-live-bar');
                if (stageEl) stageEl.textContent = label;
                if (countEl) countEl.textContent = `候选 ${run.candidate_count || 0} 条 · 入选 ${run.selected_count || 0} 条 · 已生成 ${run.item_count || 0} 条${run.duration_seconds ? ` · 用时 ${run.duration_seconds}s` : ''}`;
                const mail = run.email_status || {}, mailStatus = mail.status || (run.status === 'running' ? 'pending' : 'skipped');
                if (mailEl) mailEl.innerHTML = `邮件状态：<b>${esc(mailStatus)}</b>${mail.error ? ` · ${esc(mail.error)}` : ''}${mail.recipients?.length ? ` · 收件人：${esc(mail.recipients.join(', '))}` : ''}`;
                if (bar) bar.style.width = ({collecting:'20%',processing:'42%',writing:'68%',saving:'86%',completed:'100%',failed:'100%'}[stage] || '12%');
                finished = ['completed', 'partial', 'failed'].includes(run.status);
                if (finished) {
                    if (stageEl) stageEl.textContent = run.status === 'failed' ? '执行失败' : (run.status === 'partial' ? '日报部分完成' : '日报生成完成');
                    showToast?.(run.status === 'failed' ? `日报执行失败：${run.error || '请查看详情'}` : (dryRun ? '试跑完成（未发送邮件）' : `日报完成，邮件状态：${mailStatus}`), run.status === 'failed' ? 'error' : 'success');
                    await loadPassiveSkills();
                    break;
                }
            } catch (e) {
                const stageEl = byId('passive-live-stage'); if (stageEl) stageEl.textContent = `读取进度失败：${e.message}`;
            }
            await new Promise(resolve => setTimeout(resolve, 2000));
        }
        if (!finished) { const stageEl = byId('passive-live-stage'); if (stageEl) stageEl.textContent = '任务仍在后台执行，可关闭窗口后在“最近运行”查看'; }
    };
    window.runPassiveSkill = async function (skillId, dryRun) {
        if (typeof requireWriteAccess === 'function' && !requireWriteAccess()) return;
        try {
            const data = await api('POST', `/passive/skills/${encodeURIComponent(skillId)}/${dryRun ? 'dry-run' : 'run'}`, dryRun ? null : {dry_run:false,force:true}, 12000);
            const run = data.run || {};
            showToast?.(dryRun ? '试跑已启动（不会发邮件）' : '任务已启动，正在真实连接 SMTP 发送', 'success');
            if (run.run_id) window.watchPassiveRun(run.run_id, dryRun);
            else { await loadPassiveSkills(); setTimeout(loadPassiveSkills, 3000); }
        } catch (e) { showToast?.(`启动失败：${e.message}`, 'error'); }
    };
    window.runWeeklyReview = async function (dryRun = false) {
        if (typeof requireWriteAccess === 'function' && !requireWriteAccess()) return;
        try {
            const data = await api('POST', '/passive/weekly-review/run', {dry_run: !!dryRun, force: true}, 12000);
            const run = data.run || {};
            showToast?.(dryRun ? '本周综述试跑已启动' : '本周综述已启动，将保存并发送邮件', 'success');
            if (run.run_id) window.watchPassiveRun(run.run_id, dryRun);
        } catch (e) { showToast?.(`本周综述启动失败：${e.message}`, 'error'); }
    };
    window.configureKnowledgeReview = async function () {
        if (typeof requireWriteAccess === 'function' && !requireWriteAccess()) return;
        try {
            const data = await api('GET', '/knowledge-review/preferences', null, 12000), p = data.preferences || {};
            const topics = window.prompt('提醒主题关键词（逗号分隔，留空表示全部）：', (p.topics || []).join(', '));
            if (topics === null) return;
            const max = window.prompt('每次最多提醒几条（1-10）：', String(p.max_items || 3));
            if (max === null) return;
            await api('PATCH', '/knowledge-review/preferences', {enabled: true, topics: topics.split(/[,，]/).map(x => x.trim()).filter(Boolean), frequency: p.frequency || 'daily', max_items: Math.max(1, Math.min(10, Number(max) || 3))}, 12000);
            showToast?.('提醒偏好已保存', 'success');
        } catch (e) { showToast?.(`提醒偏好保存失败：${e.message}`, 'error'); }
    };
    window.openPassiveSkillEditor = async function (skillId) { try { const data = await api('GET', `/passive/skills/${encodeURIComponent(skillId)}`, null, 12000), dialog = document.createElement('div'); dialog.className = 'passive-editor'; dialog.id = 'passive-skill-editor'; dialog.innerHTML = `<div class="passive-editor-box"><div class="passive-editor-head"><strong>编辑 Skill 正文 · ${esc(skillId)}</strong><button class="icon-btn" onclick="byId('passive-skill-editor')?.remove()">×</button></div><textarea id="passive-skill-content" class="passive-editor-body" style="min-height:560px;border:0;border-radius:0;font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;resize:vertical">${esc(data.content || '')}</textarea><div class="passive-editor-footer"><button class="dialog-btn" onclick="byId('passive-skill-editor')?.remove()">取消</button><button class="dialog-btn primary" onclick="savePassiveSkillContent('${esc(skillId)}')">保存为新版本</button></div></div>`; document.body.appendChild(dialog); } catch (e) { showToast?.(`读取 Skill 失败：${e.message}`, 'error'); } };
    window.savePassiveSkillContent = async function (skillId) { if (typeof requireWriteAccess === 'function' && !requireWriteAccess()) return; try { await api('PUT', `/passive/skills/${encodeURIComponent(skillId)}/content`, {content: value('passive-skill-content')}, 20000); byId('passive-skill-editor')?.remove(); showToast?.('Skill 正文已保存为新版本，请重新启用', 'success'); loadPassiveSkills(); } catch (e) { showToast?.(`保存失败：${e.message}`, 'error'); } };
    window.deletePassiveRun = async function (runId) { if (typeof requireWriteAccess === 'function' && !requireWriteAccess()) return; if (!confirm('删除这条运行记录？已生成的日报文件不会删除。')) return; try { await api('DELETE', `/passive/runs/${encodeURIComponent(runId)}`, null, 12000); showToast?.('运行记录已删除', 'success'); loadPassiveSkills(); } catch (e) { showToast?.(`删除失败：${e.message}`, 'error'); } };
    window.viewPassiveRun = async function (runId) { try { const data = await api('GET', `/passive/runs/${encodeURIComponent(runId)}`, null, 12000), run = data.run || {}, trace = (run.trace || []).map(x => `<div class="passive-trace-row"><b>${esc(x.phase)}</b> · ${esc(x.message)}<div class="passive-help">${esc(x.at || '')}</div>${x.data && Object.keys(x.data).length ? `<div class="passive-help">${esc(JSON.stringify(x.data))}</div>` : ''}</div>`).join(''), dialog = document.createElement('div'); dialog.className = 'passive-editor'; dialog.id = 'passive-run-detail'; dialog.innerHTML = `<div class="passive-editor-box" style="max-width:760px"><div class="passive-editor-head"><strong>Agent 执行详情 · ${esc(run.run_id)}</strong><button class="icon-btn" onclick="byId('passive-run-detail')?.remove()">×</button></div><div class="passive-editor-body" style="font-size:12px"><div>状态：<b>${esc(run.status)}</b> · 阶段：${esc(run.stage || '')}</div><div>候选 ${run.candidate_count || 0} · 入选 ${run.selected_count || 0} · 输出 ${run.item_count || 0}</div><div class="passive-trace">${trace || '<div class="passive-help" style="padding:10px">暂无执行记录</div>'}</div>${run.error ? `<pre style="white-space:pre-wrap;color:#b91c1c">${esc(run.error)}</pre>` : ''}</div><div class="passive-editor-footer"><button class="dialog-btn primary" onclick="byId('passive-run-detail')?.remove()">关闭</button></div></div>`; document.body.appendChild(dialog); } catch (e) { showToast?.(`读取运行详情失败：${e.message}`, 'error'); } };
    window.testPassiveEmail = async function () { const recipient = window.prompt('输入测试收件人邮箱（会真实发送一封测试邮件）', ''); if (!recipient) return; try { const data = await api('POST', '/wiki/email/test-send', {recipient}, 20000); showToast?.(data.success ? '真实测试邮件已发送' : `发送失败：${data.error || ''}`, data.success ? 'success' : 'error'); } catch (e) { showToast?.(`测试失败：${e.message}`, 'error'); } };
})();
