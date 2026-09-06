/**
 * UI 工具模块 - O2: 从 app.js 拆分
 * 负责 Toast 通知、进度条、Markdown 渲染、文件图标等 UI 辅助函数
 */

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function normalizeMarkdownTables(text) {
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    let inFence = false;
    const tableLike = (line) => {
        const trimmed = line.replace(/｜/g, '|').trim();
        return trimmed.includes('|') && (trimmed.match(/\|/g) || []).length >= 2;
    };
    const separatorLike = (line) => {
        const trimmed = line.replace(/｜/g, '|').trim();
        return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(trimmed);
    };

    for (let i = 0; i < lines.length; i += 1) {
        const trimmed = lines[i].trim();
        if (/^```/.test(trimmed) || /^~~~/.test(trimmed)) {
            inFence = !inFence;
            continue;
        }
        if (inFence) continue;

        if (separatorLike(lines[i])) {
            const headerIndex = i - 1;
            if (headerIndex >= 0 && tableLike(lines[headerIndex])) {
                lines[headerIndex] = lines[headerIndex].replace(/｜/g, '|').trim();
                lines[i] = lines[i].replace(/｜/g, '|').trim();

                if (headerIndex > 0 && lines[headerIndex - 1].trim() !== '') {
                    lines.splice(headerIndex, 0, '');
                    i += 1;
                }

                let rowIndex = i + 1;
                while (rowIndex < lines.length && tableLike(lines[rowIndex])) {
                    lines[rowIndex] = lines[rowIndex].replace(/｜/g, '|').trim();
                    rowIndex += 1;
                }
            }
        }
    }

    return lines.join('\n');
}

function renderMarkdown(text) {
    if (!text) return '';
    const normalizedText = normalizeMarkdownTables(text);
    if (typeof marked !== 'undefined' && marked.parse) {
        try {
            marked.setOptions({ breaks: true, gfm: true });
            return marked.parse(normalizedText);
        } catch (e) {
            return escapeHtml(normalizedText).replace(/\n/g, '<br>');
        }
    }
    let html = escapeHtml(normalizedText);
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<code style="background:var(--bg-hover);padding:1px 4px;border-radius:3px;font-family:var(--font-mono);font-size:14px">$1</code>');
    html = html.replace(/\n/g, '<br>');
    return html;
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function getFileTypeLabel(name) {
    const ext = name.split('.').pop().toLowerCase();
    const labels = {
        'pdf': 'PDF 文档', 'docx': 'Word 文档', 'doc': 'Word 文档',
        'xlsx': 'Excel 表格', 'xls': 'Excel 表格', 'pptx': 'PPT 演示',
        'ppt': 'PPT 演示', 'md': 'Markdown', 'markdown': 'Markdown',
        'txt': '文本文件', 'csv': 'CSV 表格', 'json': 'JSON 文件',
        'html': 'HTML 文件', 'htm': 'HTML 文件',
    };
    return labels[ext] || ext.toUpperCase() + ' 文件';
}

function getFileIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    if (ext === 'md' || ext === 'markdown') return 'file-md';
    if (ext === 'txt') return 'file-txt';
    if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'tiff', 'tif'].includes(ext)) return 'file-img';
    if (ext === 'pdf') return 'file-pdf';
    if (['docx', 'doc'].includes(ext)) return 'file-docx';
    if (['xlsx', 'xls'].includes(ext)) return 'file-xlsx';
    if (['pptx', 'ppt'].includes(ext)) return 'file-pptx';
    if (ext === 'csv') return 'file-csv';
    if (ext === 'json') return 'file-json';
    if (['html', 'htm'].includes(ext)) return 'file-html';
    return 'file-default';
}

function isImageFile(name) {
    const ext = name.split('.').pop().toLowerCase();
    return ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'tiff', 'tif'].includes(ext);
}

function readFileAsDataURL(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error('读取图片失败'));
        reader.readAsDataURL(file);
    });
}

function getFileIconSvg(type) {
    if (type === 'folder') return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
    if (type === 'file-md') return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
    if (type === 'file-img') return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>';
    if (type === 'file-pdf') return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/></svg>';
    return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
}

function showProgress(id, message, percent) {
    let el = document.getElementById(id);
    if (!el) {
        el = document.createElement('div');
        el.id = id;
        el.className = 'floating-progress';
        document.body.appendChild(el);
    }
    el.innerHTML = `
        <div class="floating-progress-msg">${message}</div>
        <div class="floating-progress-track">
            <div class="floating-progress-fill" style="width:${percent}%"></div>
        </div>
        <div class="floating-progress-percent">${percent}%</div>
    `;
}

function hideProgress(id) {
    const el = document.getElementById(id);
    if (el) {
        el.classList.add('floating-progress-out');
        setTimeout(() => el.remove(), 300);
    }
}

function updateConvertProgress(percent, status) {
    const bar = document.getElementById('convert-progress-bar');
    const text = document.getElementById('convert-progress-text');
    if (bar) bar.style.width = percent + '%';
    if (text) text.textContent = status || `${percent}%`;
}

// 导出到全局
window.showToast = showToast;
window.escapeHtml = escapeHtml;
window.renderMarkdown = renderMarkdown;
window.formatFileSize = formatFileSize;
window.getFileTypeLabel = getFileTypeLabel;
window.getFileIcon = getFileIcon;
window.isImageFile = isImageFile;
window.readFileAsDataURL = readFileAsDataURL;
window.getFileIconSvg = getFileIconSvg;
window.showProgress = showProgress;
window.hideProgress = hideProgress;
window.updateConvertProgress = updateConvertProgress;
