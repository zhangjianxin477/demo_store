// API_BASE, _backendOnline 等已在 modules/api.js 中声明，此处不再重复声明
// 仅确保 converters.js 中的变量可用
var API_BASE = window.API_BASE || '/api/v1';
window.API_BASE = API_BASE;
window.KNOWLEDGE_HUB_BOOT_VERSION = 'v35';
var _backendOnline = typeof window._backendOnline === 'boolean' ? window._backendOnline : null;
var _backendCheckTime = window._backendCheckTime || 0;
var BACKEND_CHECK_INTERVAL = window.BACKEND_CHECK_INTERVAL || 30000;
var CLOUD_RECONNECT_INTERVAL = window.CLOUD_RECONNECT_INTERVAL || 20000;
var CLOUD_OFFLINE_FAILURE_THRESHOLD = window.CLOUD_OFFLINE_FAILURE_THRESHOLD || 3;
var _backendFailureCount = window._backendFailureCount || 0;
var _backendLastFailureAt = window._backendLastFailureAt || 0;
var _cloudConnectionMonitorTimer = null;
var _kgGraphStableRefreshTimer = null;
var _pdfjsLoaded = _pdfjsLoaded || false;
var _mammothLoaded = _mammothLoaded || false;
var _xlsxLoaded = _xlsxLoaded || false;

function ensureLocalEmbeddingService() {
    if (window.localEmbeddingService) return window.localEmbeddingService;
    const fallback = {
        get isAvailable() { return false; },
        get isLoading() { return false; },
        get dimension() { return 0; },
        setProgressCallback() {},
        async initialize() { return false; },
        async embed() { return null; },
        async embedText() { return null; },
        async embedTexts(texts) { return (texts || []).map(() => null); },
        cosineSimilarity() { return 0; },
        keywordSearch(query, chunks, topK = 5) {
            const terms = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
            return (chunks || [])
                .map((chunk, index) => {
                    const text = String(chunk.text || chunk.content || '').toLowerCase();
                    const score = terms.reduce((sum, term) => sum + (text.includes(term) ? 1 : 0), 0);
                    return { ...chunk, score, chunk_id: chunk.chunk_id || chunk.id || `local-${index}` };
                })
                .filter(item => item.score > 0)
                .sort((a, b) => b.score - a.score)
                .slice(0, topK);
        },
        vectorSearch() { return []; },
        hybridSearch(query, chunks, topK = 5) {
            return this.keywordSearch(query, chunks, topK);
        },
    };
    window.localEmbeddingService = fallback;
    return fallback;
}

ensureLocalEmbeddingService();

async function loadPdfJs() {
    if (_pdfjsLoaded && window.pdfjsLib) return;
    if (window.pdfjsLib) { _pdfjsLoaded = true; return; }
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.min.js';
        script.onload = () => {
            window.pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js';
            _pdfjsLoaded = true;
            resolve();
        };
        script.onerror = () => reject(new Error('PDF.js 加载失败'));
        document.head.appendChild(script);
    });
}

async function loadMammothJs() {
    if (_mammothLoaded && window.mammoth) return;
    if (window.mammoth) { _mammothLoaded = true; return; }
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/mammoth@1.6.0/mammoth.browser.min.js';
        script.onload = () => { _mammothLoaded = true; resolve(); };
        script.onerror = () => reject(new Error('Mammoth.js 加载失败'));
        document.head.appendChild(script);
    });
}

async function loadXlsxJs() {
    if (_xlsxLoaded && window.XLSX) return;
    if (window.XLSX) { _xlsxLoaded = true; return; }
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js';
        script.onload = () => { _xlsxLoaded = true; resolve(); };
        script.onerror = () => reject(new Error('XLSX.js 加载失败'));
        document.head.appendChild(script);
    });
}

async function convertDocxToMarkdown(file) {
    await loadMammothJs();
    const arrayBuffer = await file.arrayBuffer();
    const result = await window.mammoth.convertToMarkdown({ arrayBuffer });
    return result.value;
}

async function convertXlsxToMarkdown(file) {
    await loadXlsxJs();
    const arrayBuffer = await file.arrayBuffer();
    const workbook = window.XLSX.read(arrayBuffer, { type: 'array' });
    let md = '';
    for (const sheetName of workbook.SheetNames) {
        md += `## ${sheetName}\n\n`;
        const sheet = workbook.Sheets[sheetName];
        const data = window.XLSX.utils.sheet_to_json(sheet, { header: 1 });
        if (data.length === 0) { md += '（空工作表）\n\n'; continue; }
        const maxCols = Math.max(...data.map(r => (r || []).length));
        md += '| ' + Array.from({ length: maxCols }, (_, i) => data[0] && data[0][i] !== undefined ? String(data[0][i]) : ' ').join(' | ') + ' |\n';
        md += '| ' + Array.from({ length: maxCols }, () => '---').join(' | ') + ' |\n';
        for (let i = 1; i < data.length; i++) {
            const row = data[i] || [];
            md += '| ' + Array.from({ length: maxCols }, (_, j) => row[j] !== undefined ? String(row[j]) : ' ').join(' | ') + ' |\n';
        }
        md += '\n';
    }
    return md;
}

async function convertPptxToMarkdown(file) {
    try {
        const JSZip = window.JSZip || (await loadJsZip());
        const arrayBuffer = await file.arrayBuffer();
        const zip = await JSZip.loadAsync(arrayBuffer);
        let md = '';
        const slideFiles = Object.keys(zip.files)
            .filter(name => name.match(/^ppt\/slides\/slide\d+\.xml$/))
            .sort((a, b) => {
                const numA = parseInt(a.match(/slide(\d+)/)[1]);
                const numB = parseInt(b.match(/slide(\d+)/)[1]);
                return numA - numB;
            });
        for (const slidePath of slideFiles) {
            const slideNum = slidePath.match(/slide(\d+)/)[1];
            const xmlContent = await zip.files[slidePath].async('text');
            const parser = new DOMParser();
            const doc = parser.parseFromString(xmlContent, 'application/xml');
            const textElements = doc.getElementsByTagNameNS('http://schemas.openxmlformats.org/drawingml/2006/main', 't');
            const texts = [];
            for (let i = 0; i < textElements.length; i++) {
                const t = textElements[i].textContent.trim();
                if (t) texts.push(t);
            }
            md += `## 幻灯片 ${slideNum}\n\n`;
            if (texts.length > 0) {
                md += texts.join('\n\n') + '\n\n';
            } else {
                md += '（此幻灯片无文字内容）\n\n';
            }
        }
        if (!md.trim()) {
            md = '（PPT文件未提取到文字内容）';
        }
        return md;
    } catch (err) {
        console.warn('PPT本地解析失败，尝试文本提取:', err);
        const text = await file.text();
        if (text && text.trim()) {
            return '```\n' + text + '\n```';
        }
        return '（PPT文件无法解析）';
    }
}

async function loadJsZip() {
    if (window.JSZip) return window.JSZip;
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js';
        script.onload = () => resolve(window.JSZip);
        script.onerror = () => reject(new Error('JSZip 加载失败'));
        document.head.appendChild(script);
    });
}

function convertTextToMarkdown(text, ext) {
    if (ext === 'md' || ext === 'markdown') return text;
    if (ext === 'csv') {
        const lines = text.split('\n').filter(l => l.trim());
        if (lines.length === 0) return '';
        const separator = lines[0].includes('\t') ? '\t' : ',';
        const rows = lines.map(l => {
            const cells = [];
            let current = '';
            let inQuotes = false;
            for (const ch of l) {
                if (ch === '"') { inQuotes = !inQuotes; continue; }
                if (ch === separator && !inQuotes) { cells.push(current.trim()); current = ''; continue; }
                current += ch;
            }
            cells.push(current.trim());
            return cells;
        });
        const maxCols = Math.max(...rows.map(r => r.length));
        let md = '| ' + rows[0].concat(Array(maxCols - rows[0].length).fill(' ')).join(' | ') + ' |\n';
        md += '| ' + Array(maxCols).fill('---').join(' | ') + ' |\n';
        for (let i = 1; i < rows.length; i++) {
            md += '| ' + rows[i].concat(Array(maxCols - rows[i].length).fill(' ')).join(' | ') + ' |\n';
        }
        return md;
    }
    if (ext === 'json') {
        try {
            const obj = JSON.parse(text);
            return '```json\n' + JSON.stringify(obj, null, 2) + '\n```';
        } catch {
            return '```json\n' + text + '\n```';
        }
    }
    if (ext === 'html' || ext === 'htm') {
        const div = document.createElement('div');
        div.innerHTML = text;
        let md = '';
        const headings = div.querySelectorAll('h1,h2,h3,h4,h5,h6');
        const hasStructuredContent = headings.length > 0 || div.querySelector('table,p,ul,ol');
        if (hasStructuredContent) {
            div.querySelectorAll('h1').forEach(el => { md += '# ' + el.textContent.trim() + '\n\n'; });
            div.querySelectorAll('h2').forEach(el => { md += '## ' + el.textContent.trim() + '\n\n'; });
            div.querySelectorAll('h3').forEach(el => { md += '### ' + el.textContent.trim() + '\n\n'; });
            div.querySelectorAll('p').forEach(el => { md += el.textContent.trim() + '\n\n'; });
            div.querySelectorAll('li').forEach(el => { md += '- ' + el.textContent.trim() + '\n'; });
            div.querySelectorAll('table').forEach(table => {
                const rows = table.querySelectorAll('tr');
                rows.forEach((row, idx) => {
                    const cells = Array.from(row.querySelectorAll('th,td')).map(c => c.textContent.trim());
                    md += '| ' + cells.join(' | ') + ' |\n';
                    if (idx === 0) md += '| ' + cells.map(() => '---').join(' | ') + ' |\n';
                });
                md += '\n';
            });
        } else {
            md = div.textContent.trim();
        }
        return md || text;
    }
    return text;
}

async function extractPdfText(file) {
    await loadPdfJs();
    const arrayBuffer = await file.arrayBuffer();
    const pdf = await window.pdfjsLib.getDocument({ data: arrayBuffer }).promise;
    let text = '';
    for (let i = 1; i <= pdf.numPages; i++) {
        const page = await pdf.getPage(i);
        const content = await page.getTextContent();
        const pageText = buildPageText(content.items);
        if (pageText.trim()) {
            text += `## 第 ${i} 页\n\n${pageText}\n\n`;
        } else {
            text += `## 第 ${i} 页\n\n（此页无文字内容）\n\n`;
        }
    }

    // 检测 CID 乱码：如果 (cid:XX) 模式占比过高，降级到后端 OCR 解析
    if (text && _hasCidGarbage(text)) {
        console.warn('[PDF] 检测到 CID 编码乱码，尝试后端 OCR 解析');
        const backendResult = await _extractPdfViaBackend(file, true);
        if (backendResult) return backendResult;
        // OCR 也失败，尝试普通后端解析
        const normalResult = await _extractPdfViaBackend(file, false);
        if (normalResult) return normalResult;
    }

    return text || '（PDF文件未提取到文字内容，可能为扫描件或图片PDF）';
}

function _hasCidGarbage(text) {
    const cidMatches = text.match(/\(cid:\d+\)/g) || [];
    const cidLen = cidMatches.join('').length;
    const ratio = cidLen / Math.max(text.length, 1);
    return ratio > 0.1; // CID 占比超过 10% 视为乱码
}

async function _extractPdfViaBackend(file, forceOcr = false) {
    try {
        const isOnline = await checkBackendOnline();
        if (!isOnline) return null;

        const formData = new FormData();
        formData.append('file', file);
        if (forceOcr) {
            formData.append('force_ocr', 'true');
        }

        const res = await fetch(`${API_BASE}/documents/convert`, {
            method: 'POST',
            body: formData,
        });

        if (!res.ok) return null;
        const data = await res.json();
        if (data.success && data.markdown_content && data.markdown_content.trim().length > 50) {
            return data.markdown_content;
        }
    } catch (err) {
        console.warn('[PDF] 后端解析失败:', err);
    }
    return null;
}

function buildPageText(items) {
    if (!items || items.length === 0) return '';

    const lineData = [];
    let currentLine = [];
    let lastY = null;

    for (const item of items) {
        if (!item.str && item.str !== '') continue;
        const y = item.transform ? item.transform[5] : 0;
        if (lastY !== null && Math.abs(y - lastY) > 2) {
            if (currentLine.length > 0) lineData.push(currentLine);
            currentLine = [];
        }
        currentLine.push(item);
        lastY = y;
    }
    if (currentLine.length > 0) lineData.push(currentLine);

    const lines = lineData.map(line => {
        line.sort((a, b) => {
            const ax = a.transform ? a.transform[4] : 0;
            const bx = b.transform ? b.transform[4] : 0;
            return ax - bx;
        });

        let text = '';
        let minX = Infinity;
        let maxX = -Infinity;
        for (const item of line) {
            if (item.transform) {
                const x0 = item.transform[4];
                const fontSize = Math.abs(item.transform[0]) || Math.abs(item.transform[3]) || 12;
                const x1 = x0 + fontSize * (item.str.length || 1);
                if (x0 < minX) minX = x0;
                if (x1 > maxX) maxX = x1;
            }
        }

        for (let i = 0; i < line.length; i++) {
            const item = line[i];
            const str = item.str;
            if (i === 0) {
                text += str;
                continue;
            }
            const prevItem = line[i - 1];
            const prevEndX = prevItem.transform[4] + Math.abs(prevItem.transform[0]) * (prevItem.str.length || 1);
            const curStartX = item.transform[4];
            const fontSize = Math.abs(item.transform[0]) || Math.abs(item.transform[3]) || 12;
            const gap = curStartX - prevEndX;

            const prevIsCjk = /[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]/.test(prevItem.str.slice(-1));
            const curIsCjk = /[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]/.test(str[0]);

            if (prevIsCjk && curIsCjk) {
                text += gap > fontSize * 2 ? '  ' : '';
                text += str;
            } else if (prevIsCjk || curIsCjk) {
                text += gap > fontSize * 0.5 ? ' ' : '';
                text += str;
            } else {
                text += gap > fontSize * 0.3 ? ' ' : '';
                text += str;
            }
        }
        return { text, minX, maxX };
    });

    if (lines.length === 0) return '';

    const allMinX = Math.min(...lines.map(l => l.minX).filter(x => isFinite(x)));
    const allMaxX = Math.max(...lines.map(l => l.maxX).filter(x => isFinite(x)));
    const contentWidth = allMaxX - allMinX;

    if (contentWidth > 200 && lines.length >= 4) {
        const boundary = detectColumnBoundary(lines, allMinX, allMaxX);
        if (boundary !== null) {
            return reorderColumns(lines, boundary);
        }
    }

    return cleanPdfText(lines.map(l => l.text).join('\n'));
}

function detectColumnBoundary(lines, minX, maxX) {
    const contentWidth = maxX - minX;
    const numBins = 30;
    const binWidth = contentWidth / numBins;
    const bins = new Array(numBins).fill(0);

    for (const line of lines) {
        if (!line.text.trim()) continue;
        const mid = (line.minX + line.maxX) / 2;
        const binIdx = Math.floor((mid - minX) / binWidth);
        if (binIdx >= 0 && binIdx < numBins) {
            bins[binIdx]++;
        }
    }

    const maxBin = Math.max(...bins);
    if (maxBin === 0) return null;

    const threshold = maxBin * 0.15;
    const gapBins = [];
    for (let i = 0; i < numBins; i++) {
        if (bins[i] < threshold) gapBins.push(i);
    }

    if (gapBins.length < 2) return null;

    const groups = [];
    let currentGroup = [gapBins[0]];
    for (let i = 1; i < gapBins.length; i++) {
        if (gapBins[i] - gapBins[i - 1] <= 2) {
            currentGroup.push(gapBins[i]);
        } else {
            groups.push(currentGroup);
            currentGroup = [gapBins[i]];
        }
    }
    groups.push(currentGroup);

    const centerBin = numBins / 2;
    let bestGroup = null;
    let bestDist = numBins;
    for (const group of groups) {
        if (group.length < 2) continue;
        const groupMid = group.reduce((a, b) => a + b, 0) / group.length;
        const dist = Math.abs(groupMid - centerBin);
        if (dist < bestDist) {
            bestDist = dist;
            bestGroup = group;
        }
    }

    if (!bestGroup || bestGroup.length < 2) return null;

    const gapStart = bestGroup[0];
    const gapEnd = bestGroup[bestGroup.length - 1];
    const boundary = minX + (gapStart + gapEnd + 1) / 2 * binWidth;

    const leftLines = lines.filter(l => l.maxX < boundary + 5 && l.text.trim());
    const rightLines = lines.filter(l => l.minX > boundary - 5 && l.text.trim());
    if (leftLines.length < 3 || rightLines.length < 3) return null;

    return boundary;
}

function reorderColumns(lines, boundary) {
    const leftLines = [];
    const rightLines = [];
    const fullLines = [];

    for (const line of lines) {
        if (!line.text.trim()) continue;
        const isLeft = line.maxX < boundary + 5;
        const isRight = line.minX > boundary - 5;
        if (isLeft && !isRight) {
            leftLines.push(line.text);
        } else if (isRight && !isLeft) {
            rightLines.push(line.text);
        } else {
            fullLines.push(line.text);
        }
    }

    const parts = [];
    if (fullLines.length > 0) parts.push(fullLines.join('\n'));
    if (leftLines.length > 0) parts.push(leftLines.join('\n'));
    if (rightLines.length > 0) parts.push(rightLines.join('\n'));

    return cleanPdfText(parts.join('\n\n'));
}

function cleanPdfText(text) {
    let prev = '';
    while (prev !== text) {
        prev = text;
        text = text.replace(/([\u4e00-\u9fff\u3400-\u4dbf])\s+([\u4e00-\u9fff\u3400-\u4dbf])/g, '$1$2');
    }
    text = text.replace(/([\u4e00-\u9fff])\s+([\u3000-\u303f\uff00-\uffef])/g, '$1$2');
    text = text.replace(/([\u3000-\u303f\uff00-\uffef])\s+([\u4e00-\u9fff])/g, '$1$2');
    text = text.replace(/([\u4e00-\u9fff])\s+([，。！？；：、）】」』])/g, '$1$2');
    text = text.replace(/([（【「『])\s+([\u4e00-\u9fff])/g, '$1$2');
    text = text.replace(/([\u4e00-\u9fff])\s+(\d)/g, '$1$2');
    text = text.replace(/(\d)\s+([\u4e00-\u9fff])/g, '$1$2');
    text = text.replace(/  +/g, ' ');
    text = mergeBrokenChineseLines(text);
    text = text.replace(/\n{3,}/g, '\n\n');
    text = text.trim();
    return text;
}

function mergeBrokenChineseLines(text) {
    const lines = text.split('\n');
    const merged = [];
    let i = 0;
    while (i < lines.length) {
        const line = lines[i].trim();
        if (!line) {
            merged.push('');
            i++;
            continue;
        }
        if (line.startsWith('#') || line.startsWith('|') || line.startsWith('```') || line.startsWith('![') || line.startsWith('> ')) {
            merged.push(line);
            i++;
            continue;
        }
        if (/^[\d一二三四五六七八九十]+[、.．]\s/.test(line)) {
            merged.push(line);
            i++;
            continue;
        }
        let current = line;
        while (i + 1 < lines.length) {
            const next = lines[i + 1].trim();
            if (!next) break;
            if (next.startsWith('#') || next.startsWith('|') || next.startsWith('```') || next.startsWith('![') || next.startsWith('> ')) break;
            if (/^[\d一二三四五六七八九十]+[、.．]\s/.test(next)) break;
            if (/^(?:摘要|关键词|关键字|Abstract|Key\s*words)[\s：:]/i.test(next)) break;
            const sentenceEndings = ['。', '！', '？', '；', '：', '.', '!', '?', ';', ':', '…', '—', '】', '）', ')', ']', '》'];
            if (sentenceEndings.some(e => current.endsWith(e))) {
                if (/^[\u4e00-\u9fff]/.test(next) && next.length > 15) break;
                if (/^[A-Z]/.test(next) && next.length > 20) break;
            }
            const lastChar = current.slice(-1);
            const firstChar = next[0];
            const bothCjk = /[\u4e00-\u9fff]/.test(lastChar) && /[\u4e00-\u9fff]/.test(firstChar);
            const cjkToNum = /[\u4e00-\u9fff\d]/.test(lastChar) && /[\u4e00-\u9fff]/.test(firstChar);
            const numToCjk = /[\u4e00-\u9fff]/.test(lastChar) && /\d/.test(firstChar);
            const commaContinue = /[，、；：]$/.test(current) && /[\u4e00-\u9fff\d]/.test(firstChar);
            if (bothCjk || cjkToNum || numToCjk || commaContinue) {
                current = current + next;
                i++;
            } else {
                break;
            }
        }
        merged.push(current);
        i++;
    }
    return merged.join('\n');
}

function chunkText(text, chunkSize = 1000, chunkOverlap = 100) {
    if (!text || !text.trim()) return [];
    const chunks = [];
    const paragraphs = text.split(/\n{2,}/);
    let currentChunk = '';
    let chunkIndex = 0;

    for (const para of paragraphs) {
        const trimmed = para.trim();
        if (!trimmed) continue;

        if (currentChunk.length + trimmed.length + 2 <= chunkSize) {
            currentChunk += (currentChunk ? '\n\n' : '') + trimmed;
        } else {
            if (currentChunk) {
                chunks.push({ index: chunkIndex++, text: currentChunk });
                if (chunkOverlap > 0 && currentChunk.length > chunkOverlap) {
                    currentChunk = currentChunk.slice(-chunkOverlap) + '\n\n' + trimmed;
                } else {
                    currentChunk = trimmed;
                }
            } else {
                const sentences = trimmed.split(/(?<=[。！？；\n.!?;])\s*/);
                let subChunk = '';
                for (const s of sentences) {
                    if (subChunk.length + s.length + 1 > chunkSize && subChunk) {
                        chunks.push({ index: chunkIndex++, text: subChunk });
                        subChunk = chunkOverlap > 0 && subChunk.length > chunkOverlap
                            ? subChunk.slice(-chunkOverlap) + s
                            : s;
                    } else {
                        subChunk += (subChunk ? '' : '') + s;
                    }
                }
                if (subChunk) currentChunk = subChunk;
            }
        }
    }

    if (currentChunk) {
        chunks.push({ index: chunkIndex++, text: currentChunk });
    }

    return chunks;
}

function markBackendOnline(isOnline, options = {}) {
    const wasOnline = _backendOnline === true;
    const nextOnline = isOnline === true;

    if (!nextOnline) {
        const failureAt = Date.now();
        if (!options.forceCount && failureAt - _backendLastFailureAt < 1500) {
            return;
        }
        _backendLastFailureAt = failureAt;
        window._backendLastFailureAt = failureAt;
        _backendFailureCount += 1;
        window._backendFailureCount = _backendFailureCount;
        _backendCheckTime = Date.now();
        window._backendCheckTime = _backendCheckTime;

        if (!options.immediate && wasOnline && _backendFailureCount < CLOUD_OFFLINE_FAILURE_THRESHOLD) {
            return;
        }
    } else {
        _backendFailureCount = 0;
        window._backendFailureCount = 0;
    }

    _backendOnline = isOnline === true;
    window._backendOnline = _backendOnline;
    _backendCheckTime = Date.now();
    window._backendCheckTime = _backendCheckTime;
    updateOnlineStatus();

    if (_backendOnline && (!wasOnline || options.sync)) {
        setTimeout(() => {
            processPendingCloudSync({ silent: true }).catch(() => {});
            if (typeof loadServerFileResources === 'function') {
                loadServerFileResources({ silent: true }).catch(() => {});
            }
        }, 0);
    }
}

async function checkBackendOnline(options = {}) {
    const force = options.force === true;
    const now = Date.now();
    if (!force && _backendOnline !== null && now - _backendCheckTime < BACKEND_CHECK_INTERVAL) {
        return _backendOnline;
    }
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000);
        const res = await fetch(`${API_BASE}/info`, {
            signal: controller.signal,
            cache: 'no-store',
        });
        clearTimeout(timeoutId);
        markBackendOnline(res.ok);
    } catch {
        markBackendOnline(false);
    }
    return _backendOnline;
}

function updateOnlineStatus() {
    const statusEl = document.getElementById('system-status');
    if (!statusEl) return;
    if (_backendOnline === null) {
        statusEl.innerHTML = '<span class="status-dot" style="background:#94a3b8"></span> 正在连接';
        statusEl.title = '正在检测云端 API 连接状态';
        return;
    }
    if (_backendOnline) {
        statusEl.innerHTML = '<span class="status-dot" style="background:#22c55e"></span> 在线';
        statusEl.title = '云端 API 已连接，文件会保存到服务器';
    } else {
        statusEl.innerHTML = '<span class="status-dot" style="background:#f59e0b"></span> 云端未连接 · 无法保存到服务器';
        statusEl.title = '当前浏览器无法访问云端 API。编辑内容只会先保存为本机草稿，换电脑不可见。';
    }
}

function describeNetworkError(err) {
    const message = err && err.message ? String(err.message) : '';
    if (
        message.includes('Failed to fetch')
        || message.includes('NetworkError')
        || message.includes('Load failed')
        || err?.name === 'TypeError'
    ) {
        return '云端 API 不可达，服务器或 Nginx 可能已断开连接';
    }
    if (err?.name === 'AbortError') {
        return '云端 API 响应超时';
    }
    return message || '网络异常';
}

function startCloudConnectionMonitor() {
    if (_cloudConnectionMonitorTimer) return;

    const probe = () => {
        checkBackendOnline({ force: true }).catch(() => markBackendOnline(false));
    };

    _cloudConnectionMonitorTimer = setInterval(probe, CLOUD_RECONNECT_INTERVAL);
    window.addEventListener('online', probe);
    window.addEventListener('focus', probe);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') probe();
    });
    setTimeout(probe, 800);
}

function isCloudOnlyRAGRequest(method, path) {
    return String(method || '').toUpperCase() === 'POST' && path === '/rag/query';
}

function isRetryableCloudRequest(method, path) {
    const verb = String(method || 'GET').toUpperCase();
    const cleanPath = String(path || '').split('?')[0];
    if (verb === 'GET' || verb === 'HEAD' || verb === 'PUT') return true;
    if (verb !== 'POST') return false;
    return cleanPath === '/rag/query'
        || cleanPath === '/query'
        || cleanPath === '/file-resources/query'
        || cleanPath === '/knowledge-graph/query'
        || /^\/kb\/[^/]+\/import\/file-resources$/.test(cleanPath);
}

function waitForCloudRetry(delayMs) {
    return new Promise(resolve => setTimeout(resolve, delayMs));
}

async function fetchCloudApi(method, path, body = null, timeoutMs = 0) {
    const opts = {
        method,
        headers: typeof getAccessHeaders === 'function' ? getAccessHeaders() : {},
        cache: 'no-store',
    };
    let timeoutId = null;

    if (body && !(body instanceof FormData)) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    } else if (body instanceof FormData) {
        opts.body = body;
    }

    if (timeoutMs > 0) {
        const controller = new AbortController();
        opts.signal = controller.signal;
        timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    }

    try {
        const res = await fetch(`${API_BASE}${path}`, opts);
        if (!res.ok) {
            let errMsg = '请求失败';
            try {
                const data = await res.json();
                errMsg = data.detail
                    ? (typeof data.detail === 'string' ? data.detail : (data.detail.message || JSON.stringify(data.detail)))
                    : (data.message || errMsg);
            } catch {}
            const httpErr = new Error(`HTTP ${res.status}: ${errMsg}`);
            httpErr.isHttpError = true;
            httpErr.status = res.status;
            throw httpErr;
        }

        const contentType = res.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            return await res.json();
        }

        const text = await res.text();
        return text ? JSON.parse(text) : { success: true };
    } finally {
        if (timeoutId) clearTimeout(timeoutId);
    }
}

async function retryCloudOnlyRAG(method, path, body, timeoutMs, originalError) {
    try {
        await new Promise(resolve => setTimeout(resolve, 500));
        const data = await fetchCloudApi(method, path, body, Math.max(timeoutMs || 0, 90000));
        markBackendOnline(true, { sync: true });
        return data;
    } catch (retryError) {
        try {
            await checkBackendOnline({ force: true });
        } catch (_) {}
        const message = describeNetworkError(retryError || originalError);
        const err = new Error(`云端知识库连接不稳定，请稍后重试：${message}`);
        err.originalError = retryError || originalError;
        throw err;
    }
}

async function api(method, path, body = null, timeoutMs = 0) {
    if (typeof isGuestAccess === 'function' && isGuestAccess() && isWriteApiRequest(method, path)) {
        const accessErr = new Error('游客模式仅支持阅读和检索');
        accessErr.isHttpError = true;
        accessErr.status = 403;
        throw accessErr;
    }
    try {
        const data = await fetchCloudApi(method, path, body, timeoutMs);
        markBackendOnline(true);
        return data;
    } catch (err) {
        if (!err.isHttpError && isRetryableCloudRequest(method, path)) {
            for (const delayMs of [500, 1400]) {
                await waitForCloudRetry(delayMs);
                try {
                    const data = await fetchCloudApi(method, path, body, timeoutMs);
                    markBackendOnline(true);
                    return data;
                } catch (retryError) {
                    err = retryError;
                    if (err.isHttpError) break;
                }
            }
        }
        if (err.isHttpError) {
            throw err;
        }
        if (isCloudOnlyRAGRequest(method, path)) {
            return await retryCloudOnlyRAG(method, path, body, timeoutMs, err);
        }
        markBackendOnline(false);
        if (method === 'POST' && /^\/kb\/[^/]+\/upload$/.test(path)) {
            throw err;
        }
        const localResult = await handleOfflineRequest(method, path, body);
        if (localResult !== undefined) return localResult;
        throw err;
    }
}

async function handleOfflineRequest(method, path, body) {
    if (path.startsWith('/kb/')) {
        return handleOfflineKB(method, path, body);
    }
    if (path.startsWith('/knowledge-graph/')) {
        return handleOfflineKG(method, path, body);
    }
    if (path.startsWith('/wiki')) {
        return handleOfflineWiki(method, path, body);
    }
    if (path.startsWith('/documents/')) {
        return handleOfflineDocuments(method, path, body);
    }
    if (path.startsWith('/rag/')) {
        return handleOfflineRAG(method, path, body);
    }
    return undefined;
}

async function handleOfflineKB(method, path, body) {
    if (method === 'GET' && path === '/kb/list') {
        const bases = await LocalDB.getAllKnowledgeBases();
        return { bases: bases || [], success: true };
    }
    if (method === 'POST' && path === '/kb/create') {
        const kbId = 'kb_' + Date.now();
        const kb = {
            kb_id: kbId,
            name: body.name,
            description: body.description || '',
            chunk_strategy: body.chunk_strategy || 'general',
            chunk_size: body.chunk_size || 1000,
            chunk_overlap: body.chunk_overlap || 100,
            doc_count: 0,
            chunk_count: 0,
            created_at: new Date().toISOString(),
        };
        await LocalDB.saveKnowledgeBase(kb);
        showToast('知识库已创建（本地模式）', 'success');
        return { success: true, kb_id: kbId, message: '知识库创建成功' };
    }
    if (method === 'DELETE' && path.match(/^\/kb\/[^/]+$/)) {
        const kbId = path.split('/').pop();
        await LocalDB.deleteKBChunksByKB(kbId);
        await LocalDB.deleteKBDocumentsByKB(kbId);
        await LocalDB.deleteKnowledgeBase(kbId);
        showToast('知识库已删除（本地模式）', 'success');
        return { success: true, message: '知识库已删除' };
    }
    if (method === 'GET' && path.match(/^\/kb\/[^/]+\/documents$/)) {
        const kbId = path.match(/^\/kb\/([^/]+)\/documents$/)[1];
        const docs = await LocalDB.getKBDocuments(kbId);
        return { documents: docs || [], success: true };
    }
    if (method === 'DELETE' && path.match(/^\/kb\/[^/]+\/documents\/[^/]+$/)) {
        const match = path.match(/^\/kb\/([^/]+)\/documents\/([^/]+)$/);
        const docId = decodeURIComponent(match[2]);
        await LocalDB.deleteKBChunksByDoc(docId);
        await LocalDB.deleteKBDocument(docId);
        showToast('文件已删除（本地模式）', 'success');
        return { success: true, doc_id: docId, removed_chunks: 0, message: '文件已删除' };
    }
    if (method === 'POST' && path.match(/^\/kb\/[^/]+\/upload$/)) {
        const kbId = path.match(/^\/kb\/([^/]+)\/upload$/)[1];
        if (!(body instanceof FormData) || !body.get('file')) {
            return { success: false, message: '未提供文件' };
        }
        const file = body.get('file');
        const ext = file.name.split('.').pop().toLowerCase();
        let textContent = '';

        if (ext === 'pdf') {
            try {
                textContent = await extractPdfText(file);
            } catch (pdfErr) {
                return { success: false, message: 'PDF解析失败: ' + pdfErr.message };
            }
        } else {
            try {
                textContent = await file.text();
            } catch (txtErr) {
                return { success: false, message: '文件读取失败: ' + txtErr.message };
            }
        }

        if (!textContent.trim()) {
            return { success: false, message: '文件内容为空' };
        }

        const kb = await LocalDB.getKnowledgeBase(kbId);
        const chunkSize = kb ? (kb.chunk_size || 1000) : 1000;
        const chunkOverlap = kb ? (kb.chunk_overlap || 100) : 100;
        const chunks = chunkText(textContent, chunkSize, chunkOverlap);

        const docId = 'doc_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
        await LocalDB.saveKBDocument({
            doc_id: docId,
            kb_id: kbId,
            filename: file.name,
            size: file.size,
            file_type: ext,
            chunk_count: chunks.length,
            created_at: new Date().toISOString(),
        });

        const embeddingAvailable = localEmbeddingService.isAvailable;
        let embeddings = [];

        if (embeddingAvailable) {
            try {
                embeddings = await localEmbeddingService.embedTexts(chunks.map(c => c.text));
            } catch (embErr) {
                console.error('离线嵌入向量生成失败:', embErr);
                embeddings = [];
            }
        }

        for (let i = 0; i < chunks.length; i++) {
            const chunk = chunks[i];
            await LocalDB.saveKBChunk({
                chunk_id: `${docId}_chunk_${chunk.index}`,
                doc_id: docId,
                kb_id: kbId,
                index: chunk.index,
                text: chunk.text,
                char_count: chunk.text.length,
                embedding: embeddings[i] || null,
            });
        }

        if (!embeddingAvailable && !localEmbeddingService.isLoading) {
            localEmbeddingService.initialize().then(loaded => {
                if (loaded) {
                    generateMissingEmbeddings(kbId);
                }
            });
        }

        if (kb) {
            const existingDocs = await LocalDB.getKBDocuments(kbId);
            const existingChunks = await LocalDB.getKBChunks(kbId);
            kb.doc_count = existingDocs.length;
            kb.chunk_count = existingChunks.length;
            await LocalDB.saveKnowledgeBase(kb);
        }

        const embStatus = embeddingAvailable ? '已生成向量嵌入' : (localEmbeddingService.isLoading ? '向量嵌入生成中…' : '向量嵌入将在模型加载后自动生成');
        return {
            success: true,
            message: `文件 ${file.name} 已本地入库（${chunks.length} 分块，${embStatus}）`,
            chunk_count: chunks.length,
            doc_count: 1,
        };
    }
    if (method === 'GET' && path.match(/^\/kb\/[^/]+\/preview\//)) {
        const kbId = path.match(/^\/kb\/([^/]+)\/preview\//)[1];
        const filename = decodeURIComponent(path.split('/preview/').pop());
        const docs = await LocalDB.getKBDocuments(kbId);
        const doc = docs.find(d => d.filename === filename);
        if (!doc) {
            return { success: false, detail: '未找到该文档' };
        }
        const chunks = await LocalDB.getKBChunksByDoc(doc.doc_id);
        const fullText = chunks.sort((a, b) => a.index - b.index).map(c => c.text).join('\n\n');
        return {
            success: true,
            file_type: doc.file_type === 'pdf' ? 'pdf' : 'text',
            content: doc.file_type === 'pdf'
                ? fullText.split(/\n{2,}/).map((text, i) => ({ page: i + 1, text }))
                : fullText,
        };
    }
    return undefined;
}

async function handleOfflineKG(method, path, body) {
    if (method === 'GET' && path.startsWith('/knowledge-graph/graph')) {
        const params = new URLSearchParams(path.split('?')[1] || '');
        const docKey = params.get('doc_key') || '__global__';
        const graphData = await LocalDB.getKGGraph(docKey);
        if (graphData) {
            return {
                nodes: graphData.nodes || [],
                edges: graphData.edges || [],
                success: true,
            };
        }
        return { nodes: [], edges: [], success: true };
    }
    if (method === 'GET' && path === '/knowledge-graph/doc-graphs') {
        const graphs = await LocalDB.getAllKGGraphs();
        return {
            graphs: (graphs || []).map(g => ({
                doc_key: g.doc_key,
                node_count: (g.nodes || []).length,
                edge_count: (g.edges || []).length,
            })),
            success: true,
        };
    }
    return undefined;
}

async function handleOfflineWiki(method, path, body) {
    if (method === 'GET' && path === '/wiki/spaces') {
        return { success: true, spaces: [{ space_id: 'default', name: '默认空间', description: '本地默认空间' }] };
    }
    if (method === 'GET' && path === '/wiki/tags') {
        return { success: true, tags: [] };
    }
    if (method === 'GET' && path === '/wiki/pages') {
        const pages = await LocalDB.getAllWikiPages();
        return { success: true, pages: pages || [] };
    }
    return undefined;
}

async function handleOfflineDocuments(method, path, body) {
    if (method === 'POST' && path === '/documents/convert') {
        if (body instanceof FormData && body.get('file')) {
            const file = body.get('file');
            const ext = file.name ? file.name.split('.').pop().toLowerCase() : '';
            if (ext === 'pdf') {
                try {
                    const mdText = await extractPdfText(file);
                    return { success: true, markdown_content: mdText };
                } catch (pdfErr) {
                    return { success: false, detail: 'PDF解析失败: ' + pdfErr.message };
                }
            }
            if (ext === 'docx' || ext === 'doc') {
                try {
                    const mdText = await convertDocxToMarkdown(file);
                    return { success: true, markdown_content: mdText };
                } catch (docxErr) {
                    return { success: false, detail: 'Word文档解析失败: ' + docxErr.message };
                }
            }
            if (ext === 'xlsx' || ext === 'xls') {
                try {
                    const mdText = await convertXlsxToMarkdown(file);
                    return { success: true, markdown_content: mdText };
                } catch (xlsxErr) {
                    return { success: false, detail: 'Excel表格解析失败: ' + xlsxErr.message };
                }
            }
            const textExts = ['txt', 'md', 'markdown', 'csv', 'json', 'html', 'htm'];
            if (textExts.includes(ext)) {
                try {
                    const text = await file.text();
                    if (!text.trim()) {
                        return { success: false, detail: '文件内容为空' };
                    }
                    const mdText = convertTextToMarkdown(text, ext);
                    return { success: true, markdown_content: mdText };
                } catch (txtErr) {
                    return { success: false, detail: '文本文件读取失败: ' + txtErr.message };
                }
            }
        }
        return { success: false, detail: '后端服务不可用，无法转换该文件格式（离线模式支持：PDF、Word、Excel、TXT、CSV、JSON、HTML、Markdown）' };
    }
    return undefined;
}

async function generateMissingEmbeddings(kbId) {
    try {
        const chunks = await LocalDB.getKBChunksWithoutEmbeddings(kbId || '');
        if (chunks.length === 0) return;

        console.log(`开始为 ${chunks.length} 个分块生成嵌入向量...`);
        const texts = chunks.map(c => c.text);
        const batchSize = 8;

        for (let i = 0; i < texts.length; i += batchSize) {
            const batch = texts.slice(i, i + batchSize);
            const batchChunks = chunks.slice(i, i + batchSize);
            const embeddings = await localEmbeddingService.embedTexts(batch);

            const updates = [];
            for (let j = 0; j < batchChunks.length; j++) {
                if (embeddings[j]) {
                    updates.push({
                        chunkId: batchChunks[j].chunk_id,
                        embedding: embeddings[j],
                    });
                }
            }
            if (updates.length > 0) {
                await LocalDB.batchUpdateKBChunkEmbeddings(updates);
            }
        }
        console.log(`嵌入向量生成完成: ${chunks.length} 个分块`);
    } catch (e) {
        console.error('生成缺失嵌入向量失败:', e);
    }
}

async function handleOfflineRAG(method, path, body) {
    if (method === 'POST' && path === '/rag/query') {
        return await handleOfflineRAGQuery(body);
    }
    if (method === 'POST' && path === '/rag/session/create') {
        return { session_id: 'offline_' + Date.now() };
    }
    if (method === 'GET' && path === '/rag/stats') {
        const allChunks = await LocalDB.getAllKBChunks();
        const withEmb = allChunks.filter(c => c.embedding && c.embedding.length > 0).length;
        return {
            document_count: allChunks.length,
            embedding_available: localEmbeddingService.isAvailable,
            chunks_with_embedding: withEmb,
            chunks_total: allChunks.length,
        };
    }
    return undefined;
}

async function handleOfflineRAGQuery(body) {
    const query = body.query || '';
    const mode = body.mode || 'hybrid';
    const kbId = body.kb_id || '';
    const topK = body.top_k || 5;

    if (!query.trim()) {
        return { answer: '请输入查询内容', sources: [], chunks: [], success: false };
    }

    let chunks = [];
    if (kbId) {
        chunks = await LocalDB.getKBChunks(kbId);
    } else {
        chunks = await LocalDB.getAllKBChunks();
    }

    if (chunks.length === 0) {
        return {
            answer: '本地知识库中暂无数据。请先上传文档到知识库。',
            sources: [],
            chunks: [],
            success: true,
            metadata: { has_results: false, result_count: 0, offline: true },
        };
    }

    let results = [];

    if (mode === 'keyword') {
        results = localEmbeddingService.keywordSearch(query, chunks, topK);
    } else if (mode === 'semantic') {
        if (localEmbeddingService.isAvailable) {
            const queryEmbedding = await localEmbeddingService.embedText(query);
            if (queryEmbedding) {
                results = localEmbeddingService.vectorSearch(queryEmbedding, chunks, topK);
            } else {
                results = localEmbeddingService.keywordSearch(query, chunks, topK);
            }
        } else {
            results = localEmbeddingService.keywordSearch(query, chunks, topK);
        }
    } else {
        if (localEmbeddingService.isAvailable) {
            const queryEmbedding = await localEmbeddingService.embedText(query);
            results = localEmbeddingService.hybridSearch(
                queryEmbedding, query, chunks, topK
            );
        } else {
            results = localEmbeddingService.keywordSearch(query, chunks, topK);
        }
    }

    const sources = results.map(r => ({
        chunk_id: r.chunk_id,
        text: r.text || r.content,
        content: r.text || r.content,
        score: r.score,
        source: r.source || r.doc_id || '',
        title: r.title || r.doc_id || '',
        page: r.metadata?.page || '',
    }));

    let answer = '';
    if (sources.length === 0) {
        answer = '未在本地知识库中找到相关内容。请尝试上传更多文档或使用不同的关键词。';
    } else {
        const topResults = sources.slice(0, 5);
        answer = '基于本地知识库检索结果，以下是相关内容：\n\n';
        topResults.forEach((s, i) => {
            const text = s.text.length > 500 ? s.text.substring(0, 500) + '...' : s.text;
            answer += `**[${i + 1}]** (相关度: ${Math.round(s.score * 100)}%)\n${text}【${i + 1}】\n\n`;
        });
        answer += '\n---\n*本地模式：以上结果基于浏览器本地向量/关键词检索，未调用 GLM、后端向量库或重排模型。*';
    }

    return {
        answer,
        sources: sources.slice(0, topK),
        chunks: sources,
        web_results: [],
        success: true,
        metadata: {
            has_results: sources.length > 0,
            result_count: sources.length,
            offline: true,
            embedding_available: localEmbeddingService.isAvailable,
        },
    };
}

let currentPage = 'kg';
let convertedMarkdown = '';
let kgReaderMode = 'render';
let kgReaderGraphRefreshTimer = null;
let kgReaderOutlineRefreshTimer = null;
let kgReaderEditGraphRefreshTimer = null;
let kgReaderEditorMirrorTimer = null;
let kgReaderEditorMirrorValue = null;
let kgReaderUndoRecordTimer = null;
let kgReaderUndoState = { key: '', stack: [], index: -1, applying: false, lastRecordedAt: 0, pendingValue: null };
let kgReaderLastEditAt = 0;
let kgFileSelectRequestId = 0;

const fileStore = {
    rootDirHandle: null,
    rootDirName: null,
    files: [],
    openedFile: null,
    selectedFile: null,
    lastCreateParentPath: '',
};
const SERVER_FILE_TREE_CACHE_KEY = 'kh:serverFileTree:v1';
let _serverFileTreeLoadPromise = null;
let _serverFileTreeRetryTimer = null;
let _serverFileTreeFailureCount = 0;

function cacheServerFileTree(root, files) {
    try {
        localStorage.setItem(SERVER_FILE_TREE_CACHE_KEY, JSON.stringify({
            root: root || '我的文档库',
            files: files || [],
            savedAt: Date.now(),
        }));
    } catch (_) {}
}

function restoreCachedServerFileTree() {
    if (fileStore.files.length) return false;
    try {
        const cached = JSON.parse(localStorage.getItem(SERVER_FILE_TREE_CACHE_KEY) || 'null');
        if (!cached || !Array.isArray(cached.files) || !cached.files.length) return false;
        fileStore.rootDirName = cached.root || '我的文档库';
        fileStore.rootDirHandle = null;
        fileStore.files = cached.files;
        renderFileTree();
        updateStats();
        return true;
    } catch (_) {
        return false;
    }
}

function mergeServerFileTreeContent(nextFiles, previousFiles) {
    const previousByPath = new Map();
    const collect = items => (items || []).forEach(item => {
        previousByPath.set(item.path, item);
        if (item.isDirectory) collect(item.children || []);
    });
    collect(previousFiles || []);

    const merge = items => (items || []).map(item => {
        const previous = previousByPath.get(item.path);
        const next = { ...item };
        if (item.isDirectory) {
            next.children = merge(item.children || []);
        } else if (
            previous
            && typeof previous.content === 'string'
            && previous.size === item.size
            && previous.updated_at === item.updated_at
        ) {
            next.content = previous.content;
            next._kgContentLoaded = previous._kgContentLoaded;
        }
        return next;
    });
    return merge(nextFiles || []);
}

// Some browsers expose custom DataTransfer MIME types inconsistently during
// dragover/drop. Keep an in-app fallback so tree-to-reader links remain
// reliable without changing the browser's native drag behavior.
let _kgActiveDraggedFilePaths = [];
let _kgLastDocumentLinkDropAt = 0;

async function loadServerFileResources({ silent = true } = {}) {
    if (_serverFileTreeLoadPromise) return _serverFileTreeLoadPromise;
    _serverFileTreeLoadPromise = (async () => {
        try {
            const data = await api('GET', '/file-resources/tree', null, 20000);
            if (data && data.success && Array.isArray(data.files)) {
                fileStore.rootDirName = data.root || '我的文档库';
                fileStore.rootDirHandle = null;
                fileStore.files = mergeServerFileTreeContent(data.files, fileStore.files);
                cacheServerFileTree(fileStore.rootDirName, data.files);
                renderFileTree();
                updateStats();
                scheduleStableKGGraphRefresh(240);
                _serverFileTreeFailureCount = 0;
                if (_serverFileTreeRetryTimer) {
                    clearTimeout(_serverFileTreeRetryTimer);
                    _serverFileTreeRetryTimer = null;
                }
            }
            return data;
        } catch (err) {
            _serverFileTreeFailureCount += 1;
            if (!fileStore.files.length) restoreCachedServerFileTree();
            if (!_serverFileTreeRetryTimer) {
                const retryDelay = Math.min(15000, 1200 * Math.pow(2, Math.min(3, _serverFileTreeFailureCount - 1)));
                _serverFileTreeRetryTimer = setTimeout(() => {
                    _serverFileTreeRetryTimer = null;
                    loadServerFileResources({ silent: true }).catch(() => {});
                }, retryDelay);
            }
            if (!silent) showToast('加载云端文件失败: ' + (err.message || '未知错误'), 'error');
            throw err;
        } finally {
            _serverFileTreeLoadPromise = null;
        }
    })();
    return _serverFileTreeLoadPromise;
}

async function createServerFileResource(path, content = '', isDirectory = false, options = {}) {
    const data = await api('POST', '/file-resources/create', {
        path,
        content,
        is_directory: isDirectory,
    });
    if (data && data.files) {
        fileStore.files = data.files;
        fileStore.rootDirName = '我的文档库';
        fileStore.rootDirHandle = null;
        if (options.render !== false) {
            renderFileTree();
            updateStats();
        }
    }
    return data;
}

async function readServerFileResource(path) {
    const encodedPath = String(path).split('/').map(encodeURIComponent).join('/');
    return await api('GET', `/file-resources/content/${encodedPath}`, null, 20000);
}

function getServerFileResourceRawUrl(path) {
    const encodedPath = String(path).split('/').map(encodeURIComponent).join('/');
    return `${API_BASE}/file-resources/raw/${encodedPath}`;
}

async function readServerFileResourceRawUrl(path) {
    const response = await fetch(getServerFileResourceRawUrl(path), {
        headers: typeof getAccessHeaders === 'function' ? getAccessHeaders() : {},
        cache: 'no-store',
    });
    if (!response.ok) {
        const error = new Error(`HTTP ${response.status}`);
        error.isHttpError = true;
        error.status = response.status;
        throw error;
    }
    markBackendOnline(true);
    return URL.createObjectURL(await response.blob());
}

function getPdfPreviewMarkup(file) {
    const title = escapeHtml(file?.name || 'PDF');
    const url = file?.pdfDataUrl || '';
    if (url) {
        return `
            <div class="pdf-preview-container" data-pdf-preview="ready">
                <iframe class="pdf-preview-frame" src="${escapeHtml(url)}" title="${title}" loading="eager" allowfullscreen></iframe>
            </div>`;
    }
    return `
        <div class="pdf-preview-container pdf-preview-unavailable" data-pdf-preview="unavailable">
            <div class="pdf-preview-message">
                <strong>PDF 预览暂时不可用</strong>
                <span>请检查云端连接后重新加载原文件。</span>
                <button type="button" onclick="retryCurrentPdfPreview()">重新加载</button>
            </div>
        </div>`;
}

function getFastServerPdfPreviewUrl(path) {
    return getServerFileResourceRawUrl(path);
}

async function retryCurrentPdfPreview() {
    const path = fileStore.openedFile?.path;
    if (!path) return;
    await selectFile(path, { refreshGraph: false });
}

async function saveServerFileResource(path, content) {
    const encodedPath = String(path).split('/').map(encodeURIComponent).join('/');
    return await api('PUT', `/file-resources/content/${encodedPath}`, { content }, 300000);
}

async function queryServerFileResources(query, topK = 5) {
    return await api('POST', '/file-resources/query', { query, top_k: topK }, 60000);
}

async function downloadAllFileResources(event) {
    const button = event?.currentTarget;
    if (button?.disabled) return;

    try {
        if (button) button.disabled = true;
        showToast('正在准备下载...', 'info');
        const link = document.createElement('a');
        link.href = `${API_BASE}/file-resources/download-all`;
        link.download = 'CoreNote文件资源.zip';
        document.body.appendChild(link);
        link.click();
        link.remove();
        showToast('已开始下载全部文件', 'success');
    } catch (err) {
        showToast('下载全部文件失败: ' + (err.message || '未知错误'), 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

function encodeResourcePathForUrl(path) {
    return String(path || '').split('/').map(encodeURIComponent).join('/');
}

let _cloudContentSaveTimer = null;
let _cloudDraftSaveTimer = null;
let _pendingCloudDraft = null;
let _cloudContentSaveRequestId = 0;
let _cloudSaveStatusKey = '';
const CLOUD_CONTENT_AUTOSAVE_DELAY = 3000;
const CLOUD_DRAFT_SAVE_DELAY = 650;
const CLOUD_DRAFT_PREFIX = 'kh:cloudDraft:';
const CLOUD_DRAFT_PATHS_KEY = 'kh:cloudDraftPaths';
const CLOUD_PENDING_CREATES_KEY = 'kh:cloudPendingCreates';
const CLOUD_PENDING_MOVES_KEY = 'kh:cloudPendingMoves';
let _pendingCloudSyncRunning = false;

function isEditableCloudFile(file = fileStore.openedFile) {
    if (!file || file.source !== 'server' || !file.path) return false;
    if (file.isImage || file.isPdf) return false;
    const ext = (file.name || file.path || '').split('.').pop().toLowerCase();
    return ['md', 'markdown', 'txt', 'csv', 'json', 'html', 'htm', 'xml', 'yaml', 'yml'].includes(ext);
}

function setCloudSaveStatus(status, message = '', options = {}) {
    const labels = {
        idle: '',
        dirty: '未保存',
        saving: '保存中...',
        saved: '已保存',
        error: '保存失败',
    };
    const text = message || labels[status] || '';
    const key = `${status || 'idle'}:${text}`;
    if (!options.force && _cloudSaveStatusKey === key) return;
    _cloudSaveStatusKey = key;
    document.querySelectorAll('[data-cloud-save-status]').forEach(el => {
        el.textContent = text;
        el.dataset.status = status || 'idle';
        el.style.display = text ? '' : 'none';
    });
}

function getCloudDraftKey(path) {
    return CLOUD_DRAFT_PREFIX + encodeURIComponent(String(path || ''));
}

function getJsonStorage(key, fallback) {
    try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
    } catch {
        return fallback;
    }
}

function setJsonStorage(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
        console.warn('本地同步队列保存失败:', err);
    }
}

function addPendingCloudSave(path) {
    if (!path) return;
    const paths = getJsonStorage(CLOUD_DRAFT_PATHS_KEY, []);
    if (!paths.includes(path)) {
        paths.push(path);
        setJsonStorage(CLOUD_DRAFT_PATHS_KEY, paths);
    }
}

function removePendingCloudSave(path) {
    const paths = getJsonStorage(CLOUD_DRAFT_PATHS_KEY, []).filter(p => p !== path);
    setJsonStorage(CLOUD_DRAFT_PATHS_KEY, paths);
}

function isSameOrChildPath(path, basePath) {
    if (!path || !basePath) return false;
    return path === basePath || path.startsWith(basePath + '/');
}

function replacePathPrefix(path, sourcePath, targetPath) {
    if (path === sourcePath) return targetPath;
    if (path.startsWith(sourcePath + '/')) {
        return targetPath + path.slice(sourcePath.length);
    }
    return path;
}

function enqueuePendingCloudCreate(path, content = '', isDirectory = false) {
    if (!path) return;
    const creates = getJsonStorage(CLOUD_PENDING_CREATES_KEY, [])
        .filter(op => op.path !== path);
    creates.push({
        path,
        content: content || '',
        is_directory: !!isDirectory,
        created_at: Date.now(),
    });
    setJsonStorage(CLOUD_PENDING_CREATES_KEY, creates);
}

function removePendingCloudCreate(path) {
    const creates = getJsonStorage(CLOUD_PENDING_CREATES_KEY, [])
        .filter(op => op.path !== path);
    setJsonStorage(CLOUD_PENDING_CREATES_KEY, creates);
}

function enqueuePendingCloudMove(sourcePath, targetPath) {
    if (!sourcePath || !targetPath || sourcePath === targetPath) return;
    let movedPendingCreate = false;
    const creates = getJsonStorage(CLOUD_PENDING_CREATES_KEY, []).map(op => {
        if (!isSameOrChildPath(op.path, sourcePath)) return op;
        movedPendingCreate = true;
        return { ...op, path: replacePathPrefix(op.path, sourcePath, targetPath) };
    });
    if (movedPendingCreate) {
        setJsonStorage(CLOUD_PENDING_CREATES_KEY, creates);
    }

    const moves = getJsonStorage(CLOUD_PENDING_MOVES_KEY, [])
        .filter(op => op.source_path !== sourcePath && op.target_path !== sourcePath);
    if (!movedPendingCreate) {
        moves.push({ source_path: sourcePath, target_path: targetPath, created_at: Date.now() });
        setJsonStorage(CLOUD_PENDING_MOVES_KEY, moves);
    }

    const draftPaths = getJsonStorage(CLOUD_DRAFT_PATHS_KEY, []);
    for (const draftPath of draftPaths.slice()) {
        if (!isSameOrChildPath(draftPath, sourcePath)) continue;
        const targetDraftPath = replacePathPrefix(draftPath, sourcePath, targetPath);
        const draft = loadCloudDraft(draftPath);
        if (draft) {
            saveCloudDraft(targetDraftPath, draft.content || '');
            clearCloudDraft(draftPath);
        }
    }
}

function removePendingCloudMove(sourcePath, targetPath) {
    const moves = getJsonStorage(CLOUD_PENDING_MOVES_KEY, [])
        .filter(op => !(op.source_path === sourcePath && op.target_path === targetPath));
    setJsonStorage(CLOUD_PENDING_MOVES_KEY, moves);
}

function saveCloudDraft(path, content) {
    if (!path) return;
    try {
        localStorage.setItem(getCloudDraftKey(path), JSON.stringify({
            content: content || '',
            updatedAt: Date.now(),
        }));
        addPendingCloudSave(path);
    } catch (err) {
        console.warn('本地草稿保存失败:', err);
    }
}

function isKGReaderEditingActive(graceMs = 8000) {
    const editor = document.getElementById('kg-reader-editor');
    const activeInEditor = !!editor && document.activeElement === editor;
    const recentlyEdited = Date.now() - (kgReaderLastEditAt || 0) < graceMs;
    return currentPage === 'kg' && kgReaderMode === 'edit' && (activeInEditor || recentlyEdited);
}

function scheduleCloudDraftSave(path, content, delay = CLOUD_DRAFT_SAVE_DELAY) {
    if (!path) return;
    _pendingCloudDraft = {
        path,
        content: String(content ?? ''),
    };
    if (_cloudDraftSaveTimer) clearTimeout(_cloudDraftSaveTimer);
    _cloudDraftSaveTimer = setTimeout(() => {
        flushCloudDraftSave();
    }, delay);
}

function flushCloudDraftSave() {
    if (_cloudDraftSaveTimer) {
        clearTimeout(_cloudDraftSaveTimer);
        _cloudDraftSaveTimer = null;
    }
    if (!_pendingCloudDraft) return;
    const draft = _pendingCloudDraft;
    _pendingCloudDraft = null;
    saveCloudDraft(draft.path, draft.content);
}

function loadCloudDraft(path) {
    if (!path) return null;
    try {
        const raw = localStorage.getItem(getCloudDraftKey(path));
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
}

function clearCloudDraft(path) {
    if (!path) return;
    if (_pendingCloudDraft && _pendingCloudDraft.path === path) {
        _pendingCloudDraft = null;
        if (_cloudDraftSaveTimer) {
            clearTimeout(_cloudDraftSaveTimer);
            _cloudDraftSaveTimer = null;
        }
    }
    try {
        localStorage.removeItem(getCloudDraftKey(path));
        removePendingCloudSave(path);
    } catch (_) {}
}

function updateFileEntryContentCache(path, content) {
    const entry = findFileEntry(fileStore.files, path);
    if (entry && !entry.isDirectory) {
        entry.content = String(content ?? '');
    }
}

async function processPendingCloudSync({ silent = true } = {}) {
    if (typeof hasWriteAccess === 'function' && !hasWriteAccess()) return;
    if (_pendingCloudSyncRunning || _backendOnline !== true) return;
    flushCloudDraftSave();
    _pendingCloudSyncRunning = true;
    let syncedCount = 0;
    try {
        const creates = getJsonStorage(CLOUD_PENDING_CREATES_KEY, []);
        for (const op of creates.slice()) {
            await createServerFileResource(op.path, op.content || '', !!op.is_directory, { render: false });
            removePendingCloudCreate(op.path);
            syncedCount++;
        }

        const moves = getJsonStorage(CLOUD_PENDING_MOVES_KEY, []);
        for (const op of moves.slice()) {
            await moveServerFileResources([{ source_path: op.source_path, target_path: op.target_path }]);
            removePendingCloudMove(op.source_path, op.target_path);
            syncedCount++;
        }

        const draftPaths = getJsonStorage(CLOUD_DRAFT_PATHS_KEY, []);
        for (const path of draftPaths.slice()) {
            const draft = loadCloudDraft(path);
            if (!draft || typeof draft.content !== 'string') {
                clearCloudDraft(path);
                continue;
            }
            await saveServerFileResource(path, draft.content);
            clearCloudDraft(path);
            if (fileStore.openedFile && fileStore.openedFile.path === path) {
                fileStore.openedFile.isDirty = false;
                setCloudSaveStatus('saved');
            }
            syncedCount++;
        }

        if (syncedCount > 0) {
            await loadServerFileResources({ silent: true });
            if (!silent) showToast(`已同步 ${syncedCount} 个云端变更`, 'success');
        }
    } catch (err) {
        markBackendOnline(false);
        if (!silent) showToast('待同步任务暂未完成: ' + describeNetworkError(err), 'warning');
    } finally {
        _pendingCloudSyncRunning = false;
    }
}

async function saveOpenedCloudFile({ silent = false } = {}) {
    if (!requireWriteAccess()) return false;
    if (!isEditableCloudFile()) return false;
    if (_cloudContentSaveTimer) {
        clearTimeout(_cloudContentSaveTimer);
        _cloudContentSaveTimer = null;
    }
    flushCloudDraftSave();

    const file = fileStore.openedFile;
    const path = file.path;
    const content = file.content || '';
    const requestId = ++_cloudContentSaveRequestId;
    setCloudSaveStatus('saving');

    try {
        const saveResult = await saveServerFileResource(path, content);
        const savedEntry = findFileEntry(fileStore.files, path);
        if (savedEntry && saveResult) {
            if (Number.isFinite(saveResult.size)) savedEntry.size = saveResult.size;
            if (Number.isFinite(saveResult.updated_at)) savedEntry.updated_at = saveResult.updated_at;
        }
        const stillSameFile = fileStore.openedFile && fileStore.openedFile.path === path;
        const contentUnchanged = stillSameFile && (fileStore.openedFile.content || '') === content;
        if (contentUnchanged && requestId === _cloudContentSaveRequestId) {
            fileStore.openedFile.isDirty = false;
            updateFileEntryContentCache(path, content);
            setCloudSaveStatus('saved');
            clearCloudDraft(path);
            if (!isKGReaderEditingActive()) {
                scheduleKGReaderDerivedRefresh({ outlineDelay: 120, graphDelay: 700, refreshGraph: currentPage === 'kg' });
            }
        }
        if (!silent) showToast('云端文件已保存', 'success');
        return true;
    } catch (err) {
        if (fileStore.openedFile && fileStore.openedFile.path === path) {
            fileStore.openedFile.isDirty = true;
            saveCloudDraft(path, fileStore.openedFile.content || '');
            setCloudSaveStatus('error', '云端未连接，本地草稿待同步');
        }
        markBackendOnline(false);
        if (!silent) showToast('保存失败，已先保存为本地草稿: ' + describeNetworkError(err), 'error');
        throw err;
    }
}

function scheduleCloudContentSave(delay = CLOUD_CONTENT_AUTOSAVE_DELAY) {
    if (typeof hasWriteAccess === 'function' && !hasWriteAccess()) return;
    if (!isEditableCloudFile()) return;
    fileStore.openedFile.isDirty = true;
    setCloudSaveStatus('dirty');
    if (_cloudContentSaveTimer) clearTimeout(_cloudContentSaveTimer);
    _cloudContentSaveTimer = setTimeout(() => {
        saveOpenedCloudFile({ silent: true }).catch(() => {});
    }, delay);
}

function markOpenedFileContentChanged(value, options = {}) {
    if (!requireWriteAccess()) return;
    if (!fileStore.openedFile) return;
    fileStore.openedFile.content = value;
    updateFileEntryContentCache(fileStore.openedFile.path || fileStore.openedFile.name, value);
    fileStore.openedFile.isDirty = true;
    if (isEditableCloudFile()) {
        if (options.flushDraft) {
            saveCloudDraft(fileStore.openedFile.path, value);
        } else {
            scheduleCloudDraftSave(fileStore.openedFile.path, value);
        }
    }
    scheduleCloudContentSave();
    if (typeof AutoSave !== 'undefined' && !isEditableCloudFile()) {
        AutoSave.debounceSave('editor', () => AutoSave.saveFileState(), 2500);
    }
}

function tryKeepaliveCloudSave() {
    if (typeof hasWriteAccess === 'function' && !hasWriteAccess()) return;
    if (!isEditableCloudFile() || !fileStore.openedFile.isDirty) return;
    flushCloudDraftSave();
    const encodedPath = String(fileStore.openedFile.path).split('/').map(encodeURIComponent).join('/');
    const body = JSON.stringify({ content: fileStore.openedFile.content || '' });
    if (body.length > 60000) return;
    try {
        fetch(`${API_BASE}/file-resources/content/${encodedPath}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                ...(typeof getAccessHeaders === 'function' ? getAccessHeaders() : {}),
            },
            body,
            keepalive: true,
        });
    } catch (_) {}
}

async function deleteServerFileResource(path) {
    const encodedPath = String(path).split('/').map(encodeURIComponent).join('/');
    return await api('DELETE', `/file-resources/${encodedPath}`, null, 300000);
}

async function moveServerFileResources(operations) {
    if (!operations || !operations.length) return null;
    return await api('POST', '/file-resources/move', { operations }, 300000);
}

async function reorderServerFileResources(parentPath, orderedPaths) {
    return await api('POST', '/file-resources/reorder', {
        parent_path: parentPath || '',
        ordered_paths: orderedPaths || [],
    }, 300000);
}

async function syncServerFileResourceMoves(operations) {
    const serverOps = (operations || []).filter(op => op.source_path && op.target_path && op.source_path !== op.target_path);
    if (!serverOps.length) return;
    try {
        const data = await moveServerFileResources(serverOps);
        if (data && data.files) {
            fileStore.rootDirName = '我的文档库';
            fileStore.rootDirHandle = null;
            fileStore.files = data.files;
            renderFileTree();
            updateStats();
        }
    } catch (err) {
        showToast('同步云端文件移动失败: ' + (err.message || '未知错误'), 'error');
        await loadServerFileResources({ silent: true });
    }
}

function createNewFile() {
    if (!requireWriteAccess()) return;
    const parentPath = getCreateParentPath();
    const targetLabel = getCreateTargetLabel(parentPath);
    const dialog = document.createElement('div');
    dialog.className = 'new-item-dialog';
    dialog.id = 'new-file-dialog';
    dialog.innerHTML = `
        <div class="dialog-box">
            <div class="dialog-title">新建文件</div>
            <div class="dialog-target">创建到：${escapeHtml(targetLabel)}</div>
            <input class="dialog-input" id="new-file-name" placeholder="输入文件名（如 note.md）" autofocus>
            <div class="dialog-actions">
                <button class="dialog-btn" onclick="closeNewFileDialog()">取消</button>
                <button class="dialog-btn primary" onclick="confirmNewFile()">创建</button>
            </div>
        </div>`;
    document.body.appendChild(dialog);
    const input = document.getElementById('new-file-name');
    input.focus();
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') confirmNewFile();
        if (e.key === 'Escape') closeNewFileDialog();
    });
}

function closeNewFileDialog() {
    const dialog = document.getElementById('new-file-dialog');
    if (dialog) dialog.remove();
}

function getCreateParentPath() {
    if (fileStore.lastCreateParentPath) {
        const lastParent = findFileEntry(fileStore.files, fileStore.lastCreateParentPath);
        if (lastParent?.isDirectory) return lastParent.path;
    }
    const selectedPath = Array.from(_fileTreeSelectedPaths || []).slice(-1)[0];
    const selectedEntry = selectedPath ? findFileEntry(fileStore.files, selectedPath) : null;
    if (selectedEntry?.isDirectory) return selectedEntry.path;
    if (selectedEntry && selectedEntry.path && selectedEntry.path.includes('/')) {
        return selectedEntry.path.substring(0, selectedEntry.path.lastIndexOf('/'));
    }
    if (fileStore.selectedFile?.isDirectory) return fileStore.selectedFile.path;
    if (fileStore.selectedFile?.path && fileStore.selectedFile.path.includes('/')) {
        return fileStore.selectedFile.path.substring(0, fileStore.selectedFile.path.lastIndexOf('/'));
    }
    return '';
}

function joinResourcePath(parentPath, name) {
    const cleanName = String(name || '').replace(/^\/+|\/+$/g, '');
    const cleanParent = String(parentPath || '').replace(/^\/+|\/+$/g, '');
    return cleanParent ? `${cleanParent}/${cleanName}` : cleanName;
}

function getCreateTargetLabel(parentPath) {
    return parentPath ? parentPath : (fileStore.rootDirName || '我的文档库');
}

function addLocalEntryToCreateParent(entry, parentPath, prepend = false) {
    if (parentPath) {
        const parent = findFileEntry(fileStore.files, parentPath);
        if (parent && parent.isDirectory) {
            parent.children = parent.children || [];
            if (prepend) parent.children.unshift(entry);
            else parent.children.push(entry);
            return;
        }
    }
    if (!fileStore.files.length) {
        fileStore.files = [entry];
    } else if (prepend) {
        fileStore.files.unshift(entry);
    } else {
        fileStore.files.push(entry);
    }
}

function expandTreePath(path) {
    if (!path) return;
    const parts = path.split('/').filter(Boolean);
    const ancestors = [];
    for (let i = 0; i < parts.length; i++) {
        ancestors.push(parts.slice(0, i + 1).join('/'));
    }
    ancestors.forEach(p => _fileTreeExpandedPaths.add(p));
    requestAnimationFrame(() => {
        for (const ancestor of ancestors) {
            const children = Array.from(document.querySelectorAll('.dir-children[data-path]'))
                .find(el => el.dataset.path === ancestor);
            if (children) children.style.display = 'block';
        }
    });
}

async function confirmNewFile() {
    if (!requireWriteAccess()) return;
    const input = document.getElementById('new-file-name');
    let name = input.value.trim();
    if (!name) {
        showToast('请输入文件名', 'warning');
        return;
    }
    if (!name.includes('.')) name += '.md';
    const parentPath = getCreateParentPath();
    const fullPath = joinResourcePath(parentPath, name);

    try {
        await createServerFileResource(fullPath, '', false, { render: false });
        fileStore.openedFile = {
            name,
            path: fullPath,
            content: '',
            isDirty: false,
            source: 'server',
        };
        fileStore.selectedFile = findFileEntry(fileStore.files, fullPath);
        _fileTreeSelectedPaths.clear();
        _fileTreeSelectedPaths.add(fullPath);
        updateEditorContent();
        renderFileTree();
        updateStats();
        expandTreePath(parentPath);
        closeNewFileDialog();
        showToast(`已创建文件: ${fullPath}`, 'success');
        return;
    } catch (err) {
        console.warn('create server file failed, fallback to local state', err);
    }

    const newFile = {
        name,
        path: fullPath,
        isDirectory: false,
        isNew: true,
        pendingSync: true,
        source: 'server',
    };

    addLocalEntryToCreateParent(newFile, parentPath);
    enqueuePendingCloudCreate(fullPath, '', false);

    fileStore.openedFile = {
        name,
        path: fullPath,
        content: '',
        isDirty: true,
        pendingSync: true,
        source: 'server',
    };
    fileStore.selectedFile = newFile;
    _fileTreeSelectedPaths.clear();
    _fileTreeSelectedPaths.add(fullPath);

    renderFileTree();
    expandTreePath(parentPath);
    updateEditorContent();
    closeNewFileDialog();
    showToast(`已本地创建文件，云端恢复后自动同步: ${fullPath}`, 'warning');
}

function createNewFolder() {
    if (!requireWriteAccess()) return;
    const parentPath = getCreateParentPath();
    const targetLabel = getCreateTargetLabel(parentPath);
    const dialog = document.createElement('div');
    dialog.className = 'new-item-dialog';
    dialog.id = 'new-folder-dialog';
    dialog.innerHTML = `
        <div class="dialog-box">
            <div class="dialog-title">新建文件夹</div>
            <div class="dialog-target">创建到：${escapeHtml(targetLabel)}</div>
            <input class="dialog-input" id="new-folder-name" placeholder="输入文件夹名" autofocus>
            <div class="dialog-actions">
                <button class="dialog-btn" onclick="closeNewFolderDialog()">取消</button>
                <button class="dialog-btn primary" onclick="confirmNewFolder()">创建</button>
            </div>
        </div>`;
    document.body.appendChild(dialog);
    const input = document.getElementById('new-folder-name');
    input.focus();
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') confirmNewFolder();
        if (e.key === 'Escape') closeNewFolderDialog();
    });
}

function closeNewFolderDialog() {
    const dialog = document.getElementById('new-folder-dialog');
    if (dialog) dialog.remove();
}

async function confirmNewFolder() {
    if (!requireWriteAccess()) return;
    const input = document.getElementById('new-folder-name');
    const name = input.value.trim();
    if (!name) {
        showToast('请输入文件夹名', 'warning');
        return;
    }
    const parentPath = getCreateParentPath();
    const fullPath = joinResourcePath(parentPath, name);

    try {
        await createServerFileResource(fullPath, '', true, { render: false });
        fileStore.selectedFile = findFileEntry(fileStore.files, fullPath);
        _fileTreeSelectedPaths.clear();
        _fileTreeSelectedPaths.add(fullPath);
        renderFileTree();
        updateStats();
        expandTreePath(fullPath);
        closeNewFolderDialog();
        showToast(`已创建文件夹: ${fullPath}`, 'success');
        return;
    } catch (err) {
        console.warn('create server folder failed, fallback to local state', err);
    }

    const newFolder = {
        name,
        path: fullPath,
        isDirectory: true,
        children: [],
        expanded: false,
        isNew: true,
        pendingSync: true,
        source: 'server',
    };

    addLocalEntryToCreateParent(newFolder, parentPath, true);
    enqueuePendingCloudCreate(fullPath, '', true);
    fileStore.selectedFile = newFolder;
    _fileTreeSelectedPaths.clear();
    _fileTreeSelectedPaths.add(fullPath);

    renderFileTree();
    expandTreePath(fullPath);
    closeNewFolderDialog();
    showToast(`已本地创建文件夹，云端恢复后自动同步: ${fullPath}`, 'warning');
}

function switchKGView(view) {
    updateEditorContent();
    if (view === 'graph' && typeof openKGGraphPage === 'function') {
        openKGGraphPage();
    }
}

function switchRAGView(view) {
    document.querySelectorAll('#page-rag .view-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`#page-rag .view-tab[data-view="${view}"]`)?.classList.add('active');

    const chatView = document.getElementById('rag-chat-view');
    const editorView = document.getElementById('rag-editor-view');
    const kbView = document.getElementById('rag-kb-view');

    if (chatView) chatView.style.display = view === 'chat' ? '' : 'none';
    if (editorView) editorView.style.display = view === 'editor' ? '' : 'none';
    if (kbView) kbView.style.display = view === 'kb' ? '' : 'none';

    if (view === 'chat') {
        // chat view active
    } else if (view === 'editor') {
        updateEditorContent();
    } else if (view === 'kb') {
        if (typeof loadKBList === 'function') loadKBList();
    }
}

function switchWikiView(view) {
    document.querySelectorAll('#page-wiki .center-view-tabs .view-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`#page-wiki .center-view-tabs .view-tab[data-view="${view}"]`)?.classList.add('active');

    const pagesView = document.getElementById('wiki-pages-view');
    const searchView = document.getElementById('wiki-search-view');
    const aiView = document.getElementById('wiki-ai-view');
    const importView = document.getElementById('wiki-import-view');

    if (pagesView) pagesView.style.display = view === 'pages' ? '' : 'none';
    if (searchView) searchView.style.display = view === 'search' ? '' : 'none';
    if (aiView) aiView.style.display = view === 'ai' ? '' : 'none';
    if (importView) importView.style.display = view === 'import' ? '' : 'none';
}

function switchWikiRightView(view) {
    document.querySelectorAll('#wiki-right-panel .view-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`#wiki-right-panel .view-tab[data-view="${view}"]`)?.classList.add('active');

    const treePanel = document.getElementById('wiki-tree-panel');
    const versionsPanel = document.getElementById('wiki-versions-panel');
    const managePanel = document.getElementById('wiki-manage-panel');

    if (treePanel) treePanel.style.display = view === 'tree' ? '' : 'none';
    if (versionsPanel) versionsPanel.style.display = view === 'versions' ? '' : 'none';
    if (managePanel) managePanel.style.display = view === 'manage' ? '' : 'none';
}

async function wikiBatchUpload(files) {
    if (!files || !files.length) return;
    const formData = new FormData();
    for (const file of files) {
        formData.append('files', file);
    }
    try {
        showToast('正在批量上传...', 'info');
        const data = await api('POST', '/wiki/import/batch', formData);
        if (data.success) {
            showToast(`成功导入 ${data.results?.length || 0} 个文件`, 'success');
            if (typeof loadWikiPageTree === 'function') loadWikiPageTree();
        }
    } catch (e) {
        showToast('批量上传失败: ' + e.message, 'error');
    }
}

function updateEditorContent(options = {}) {
    flushKGReaderEditorMirror();
    const filenameEl = document.getElementById('kg-editor-filename');
    const contentEl = document.getElementById('kg-editor-content');
    const ragFilenameEl = currentPage === 'rag' ? document.getElementById('rag-editor-filename') : null;
    const ragContentEl = currentPage === 'rag' ? document.getElementById('rag-editor-content') : null;

    if (fileStore.openedFile) {
        if (filenameEl) filenameEl.textContent = fileStore.openedFile.name;
        if (ragFilenameEl) ragFilenameEl.textContent = fileStore.openedFile.name;

        const isImg = fileStore.openedFile.isImage
            && fileStore.openedFile.content
            && /^(data:|blob:|https?:\/\/|\/)/i.test(String(fileStore.openedFile.content));
        const isPdf = fileStore.openedFile.isPdf;

        if (isPdf) {
            setCloudSaveStatus('idle');
            if (contentEl) { contentEl.style.display = 'none'; contentEl.value = ''; }
            if (ragContentEl) { ragContentEl.style.display = 'none'; ragContentEl.value = ''; }

            const kgPreview = document.getElementById('kg-editor-preview');
            const ragPreview = document.getElementById('rag-editor-preview');

            if (kgPreview) {
                kgPreview.style.display = 'flex';
                kgPreview.innerHTML = getPdfPreviewMarkup(fileStore.openedFile);
            }
            if (ragPreview) {
                ragPreview.style.display = 'flex';
                ragPreview.innerHTML = getPdfPreviewMarkup(fileStore.openedFile);
            }
        } else if (isImg) {
            setCloudSaveStatus('idle');
            if (contentEl) { contentEl.style.display = 'none'; contentEl.value = ''; }
            if (ragContentEl) { ragContentEl.style.display = 'none'; ragContentEl.value = ''; }

            const kgPreview = document.getElementById('kg-editor-preview');
            const ragPreview = document.getElementById('rag-editor-preview');

            if (kgPreview) {
                kgPreview.style.display = 'flex';
                kgPreview.innerHTML = `<div class="image-preview-container"><img src="${escapeHtml(fileStore.openedFile.content)}" alt="${escapeHtml(fileStore.openedFile.name)}" class="image-preview-img" /></div>`;
            }
            if (ragPreview) {
                ragPreview.style.display = 'flex';
                ragPreview.innerHTML = `<div class="image-preview-container"><img src="${escapeHtml(fileStore.openedFile.content)}" alt="${escapeHtml(fileStore.openedFile.name)}" class="image-preview-img" /></div>`;
            }
        } else {
            if (contentEl) { contentEl.style.display = ''; contentEl.value = fileStore.openedFile.content; }
            if (ragContentEl) { ragContentEl.style.display = ''; ragContentEl.value = fileStore.openedFile.content; }
            setCloudSaveStatus(fileStore.openedFile.isDirty ? 'dirty' : (isEditableCloudFile() ? 'saved' : 'idle'));

            const kgPreview = document.getElementById('kg-editor-preview');
            const ragPreview = document.getElementById('rag-editor-preview');
            if (kgPreview && kgPreview.style.display !== 'none') {
                kgPreview.innerHTML = renderMarkdown(fileStore.openedFile.content);
            }
            if (ragPreview && ragPreview.style.display !== 'none') {
                ragPreview.innerHTML = renderMarkdown(fileStore.openedFile.content);
            }
        }
    } else {
        setCloudSaveStatus('idle');
        if (filenameEl) filenameEl.textContent = '未打开文件';
        if (contentEl) { contentEl.style.display = ''; contentEl.value = ''; }
        if (ragFilenameEl) ragFilenameEl.textContent = '未打开文件';
        if (ragContentEl) { ragContentEl.style.display = ''; ragContentEl.value = ''; }
        const kgPreview = document.getElementById('kg-editor-preview');
        const ragPreview = document.getElementById('rag-editor-preview');
        if (kgPreview) { kgPreview.style.display = 'none'; kgPreview.innerHTML = ''; }
        if (ragPreview) { ragPreview.style.display = 'none'; ragPreview.innerHTML = ''; }
    }

    renderKGMarkdownReader();
    const openedBinaryPreview = fileStore.openedFile?.isPdf || fileStore.openedFile?.isImage;
    if (options.refreshGraph !== false
        && !openedBinaryPreview
        && typeof refreshKGGraph === 'function'
        && currentPage === 'kg') {
        clearTimeout(kgReaderGraphRefreshTimer);
        kgReaderGraphRefreshTimer = setTimeout(
            () => refreshKGGraph(undefined, options.reuseGraph === true ? { reuse: true } : {}),
            options.reuseGraph === true ? 80 : 600,
        );
    }
}

function normalizeKGHeadingText(text) {
    return String(text || '')
        .replace(/\s+/g, ' ')
        .replace(/[\u200B-\u200D\uFEFF]/g, '')
        .trim()
        .toLowerCase();
}

function stripKGHeadingInlineMarkdown(text) {
    return String(text || '')
        .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
        .replace(/\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]/g, (_, target, label) => label || target)
        .replace(/<[^>]+>/g, '')
        .replace(/\\([\\`*{}\[\]()#+\-.!_>])/g, '$1')
        .replace(/[`*_~]/g, '')
        .replace(/\s+/g, ' ')
        .trim();
}

function stripKGMarkdownCodeSegments(content) {
    const lines = String(content || '').split(/\r?\n/);
    const kept = [];
    let inFence = false;
    let fenceChar = '';
    let fenceSize = 0;

    for (const line of lines) {
        const fence = line.match(/^\s{0,3}(`{3,}|~{3,})/);
        if (fence) {
            const marker = fence[1];
            const markerChar = marker[0];
            if (!inFence) {
                inFence = true;
                fenceChar = markerChar;
                fenceSize = marker.length;
            } else if (markerChar === fenceChar && marker.length >= fenceSize) {
                inFence = false;
                fenceChar = '';
                fenceSize = 0;
            }
            kept.push('');
            continue;
        }
        if (inFence || /^\s{4,}\S/.test(line)) {
            kept.push('');
            continue;
        }
        kept.push(line.replace(/`[^`]*`/g, ''));
    }

    return kept.join('\n');
}

function extractKGHeadingsFromMarkdown(content) {
    const headings = [];
    const used = new Map();
    const lines = String(content || '').split(/\r?\n/);
    let inFence = false;
    let fenceChar = '';
    let fenceSize = 0;

    lines.forEach((line, lineIndex) => {
        const fence = line.match(/^\s{0,3}(`{3,}|~{3,})/);
        if (fence) {
            const marker = fence[1];
            const markerChar = marker[0];
            if (!inFence) {
                inFence = true;
                fenceChar = markerChar;
                fenceSize = marker.length;
            } else if (markerChar === fenceChar && marker.length >= fenceSize) {
                inFence = false;
                fenceChar = '';
                fenceSize = 0;
            }
            return;
        }
        if (inFence || /^\s{4,}\S/.test(line)) return;

        const match = line.match(/^\s{0,3}(#{1,6})[ \t]+(.+?)\s*$/);
        if (!match) return;
        const level = match[1].length;
        const raw = match[2].replace(/[ \t]+#+[ \t]*$/, '').trim();
        const text = stripKGHeadingInlineMarkdown(raw);
        if (!text) return;
        const base = text.toLowerCase()
            .replace(/[^\w\u4e00-\u9fff]+/g, '-')
            .replace(/^-+|-+$/g, '') || `heading-${headings.length + 1}`;
        const count = used.get(base) || 0;
        used.set(base, count + 1);
        headings.push({
            level,
            text,
            line: lineIndex,
            id: count ? `${base}-${count + 1}` : base,
            index: headings.length,
        });
    });
    return headings;
}

function enhanceKGReaderHeadings() {
    const body = document.querySelector('#kg-md-reader .kg-reader-body');
    if (!body || !fileStore.openedFile) return;
    const sourceHeadings = extractKGHeadingsFromMarkdown(fileStore.openedFile.content || '');
    const renderedHeadings = Array.from(body.querySelectorAll('h1,h2,h3,h4,h5,h6'))
        .filter(heading => !heading.closest('.kg-backlinks'));
    const usedDomIndexes = new Set();

    renderedHeadings.forEach(heading => {
        heading.removeAttribute('id');
        heading.removeAttribute('data-kg-heading-index');
        heading.classList.remove('kg-reader-anchor-heading');
    });

    for (const sourceHeading of sourceHeadings) {
        const sourceText = normalizeKGHeadingText(sourceHeading.text);
        const exactIndex = renderedHeadings.findIndex((heading, index) => {
            if (usedDomIndexes.has(index)) return false;
            const level = Number((heading.tagName || '').replace(/^H/i, ''));
            return level === sourceHeading.level && normalizeKGHeadingText(heading.textContent) === sourceText;
        });
        const fallbackIndex = exactIndex >= 0 ? exactIndex : renderedHeadings.findIndex((heading, index) => {
            if (usedDomIndexes.has(index)) return false;
            const level = Number((heading.tagName || '').replace(/^H/i, ''));
            return level === sourceHeading.level && normalizeKGHeadingText(heading.textContent).includes(sourceText);
        });
        if (fallbackIndex < 0) continue;
        const heading = renderedHeadings[fallbackIndex];
        usedDomIndexes.add(fallbackIndex);
        heading.id = `kg-heading-${sourceHeading.index}`;
        heading.dataset.kgHeadingIndex = String(sourceHeading.index);
        heading.classList.add('kg-reader-anchor-heading');
    }
}

function cleanKGDocumentReference(ref) {
    let value = String(ref || '').trim();
    try {
        value = decodeURIComponent(value);
    } catch (_) {}
    return value
        .replace(/\\/g, '/')
        .replace(/[#?].*$/, '')
        .replace(/^\[\[|\]\]$/g, '')
        .replace(/\|.*$/, '')
        .replace(/^@+/, '')
        .replace(/[@$]+$/, '')
        .replace(/^\.\/+/, '')
        .replace(/^\//, '')
        .replace(/<[^>]+>/g, '')
        .replace(/[`*_~]/g, '')
        .replace(/\s+/g, ' ')
        .trim();
}

function normalizeKGDocumentKey(ref, options = {}) {
    let value = cleanKGDocumentReference(ref)
        .replace(/\.(md|markdown)$/i, '')
        .toLowerCase();
    if (!options.keepPath) {
        value = value.replace(/^.*\//, '');
    }
    return value.trim();
}

function getKGDocumentPath(file) {
    return file ? (file.path || file.name || '') : '';
}

function getKGDocumentDir(path) {
    const normalized = String(path || '').replace(/\\/g, '/');
    const idx = normalized.lastIndexOf('/');
    return idx >= 0 ? normalized.slice(0, idx) : '';
}

function normalizeKGRelativePath(ref, currentPath = '') {
    const raw = cleanKGDocumentReference(ref).replace(/\\/g, '/');
    if (!raw) return '';
    if (!raw.startsWith('../') && !raw.startsWith('./')) return raw;
    const parts = [...getKGDocumentDir(currentPath).split('/').filter(Boolean), ...raw.split('/')];
    const resolved = [];
    for (const part of parts) {
        if (!part || part === '.') continue;
        if (part === '..') {
            resolved.pop();
        } else {
            resolved.push(part);
        }
    }
    return resolved.join('/');
}

function collectKGMarkdownFiles(items = fileStore.files) {
    const result = [];
    function walk(list) {
        for (const item of list || []) {
            if (item.isDirectory) {
                walk(item.children || []);
            } else if (/\.(md|markdown)$/i.test(item.name || item.path || '')) {
                result.push(item);
            }
        }
    }
    walk(items);
    return result;
}

function buildKGDocumentLinkIndex() {
    if (buildKGDocumentLinkIndex._filesRef === fileStore.files && buildKGDocumentLinkIndex._cache) {
        return buildKGDocumentLinkIndex._cache;
    }
    const index = new Map();
    const files = collectKGMarkdownFiles();

    function add(key, file) {
        const normalized = normalizeKGDocumentKey(key, { keepPath: true });
        if (!normalized) return;
        if (!index.has(normalized)) index.set(normalized, []);
        const list = index.get(normalized);
        if (!list.includes(file)) list.push(file);
    }

    for (const file of files) {
        const path = getKGDocumentPath(file);
        const name = file.name || path;
        const title = name.replace(/\.(md|markdown)$/i, '');
        const pathWithoutExt = path.replace(/\.(md|markdown)$/i, '');
        const basename = path.replace(/\\/g, '/').split('/').pop() || name;
        const basenameWithoutExt = basename.replace(/\.(md|markdown)$/i, '');
        [path, pathWithoutExt, name, title, basename, basenameWithoutExt].forEach(key => add(key, file));
        add(normalizeKGDocumentKey(title), file);
        add(normalizeKGDocumentKey(basenameWithoutExt), file);
    }

    const result = { files, index };
    buildKGDocumentLinkIndex._filesRef = fileStore.files;
    buildKGDocumentLinkIndex._cache = result;
    return result;
}

function chooseKGDocumentMatch(matches, currentPath = '') {
    const unique = Array.from(new Set(matches || []));
    if (unique.length <= 1) return unique[0] || null;
    const currentDir = getKGDocumentDir(currentPath);
    const sameDir = unique.filter(file => getKGDocumentDir(getKGDocumentPath(file)) === currentDir);
    return sameDir.length === 1 ? sameDir[0] : null;
}

function resolveKGDocumentReference(ref, options = {}) {
    const { files, index } = buildKGDocumentLinkIndex();
    const currentPath = options.currentPath || getKGDocumentPath(fileStore.openedFile);
    const normalizedRelative = normalizeKGRelativePath(ref, currentPath);
    const candidates = [
        normalizedRelative,
        cleanKGDocumentReference(ref),
        normalizeKGDocumentKey(ref),
        normalizeKGDocumentKey(ref, { keepPath: true }),
    ].filter(Boolean);

    for (const candidate of candidates) {
        const fullKey = normalizeKGDocumentKey(candidate, { keepPath: true });
        const titleKey = normalizeKGDocumentKey(candidate);
        const match = chooseKGDocumentMatch([
            ...(index.get(fullKey) || []),
            ...(index.get(titleKey) || []),
        ], currentPath);
        if (match) return match;
    }

    if (options.allowFuzzy === false) return null;
    const query = normalizeKGDocumentKey(ref);
    if (!query || query.length < 2) return null;
    const fuzzyMatches = files.filter(file => {
        const title = normalizeKGDocumentKey(file.name || file.path || '');
        return title === query || title.includes(query) || query.includes(title);
    });
    return chooseKGDocumentMatch(fuzzyMatches, currentPath);
}

function getKGDocAliasRecords(currentPath = '', options = {}) {
    const includeCurrent = options.includeCurrent === true;
    const current = String(currentPath || '');
    const aliasToFiles = new Map();
    for (const file of collectKGMarkdownFiles()) {
        const path = getKGDocumentPath(file);
        if (!includeCurrent && current && path === current) continue;
        const name = file.name || path;
        const basename = path.replace(/\\/g, '/').split('/').pop() || name;
        const aliases = new Set([
            name.replace(/\.(md|markdown)$/i, ''),
            basename.replace(/\.(md|markdown)$/i, ''),
            path.replace(/\.(md|markdown)$/i, ''),
        ].map(cleanKGDocumentReference).filter(alias => alias && alias.length >= 2));

        for (const alias of aliases) {
            const key = normalizeKGDocumentKey(alias, { keepPath: true });
            if (!key) continue;
            if (!aliasToFiles.has(key)) aliasToFiles.set(key, { alias, files: [] });
            aliasToFiles.get(key).files.push(file);
        }
    }

    const records = [];
    for (const entry of aliasToFiles.values()) {
        const file = chooseKGDocumentMatch(entry.files, currentPath);
        if (!file) continue;
        records.push({
            alias: entry.alias,
            key: normalizeKGDocumentKey(entry.alias, { keepPath: true }),
            file,
            path: getKGDocumentPath(file),
        });
    }
    return records.sort((a, b) => b.alias.length - a.alias.length);
}

function isKGExternalHref(href) {
    return /^(https?:|mailto:|tel:|data:|blob:|javascript:)/i.test(String(href || ''))
        || String(href || '').startsWith('#');
}

function isKGDocMarkerBoundary(ch) {
    return !ch || /[\s,，.。;；:：!?！？()[\]{}<>《》'"“”‘’、]/.test(ch);
}

function startsWithKGAlias(text, index, alias) {
    return text.slice(index, index + alias.length).toLowerCase() === alias.toLowerCase();
}

function findKGDocMarkerAt(text, index, records) {
    if (text.startsWith('[[', index)) {
        const end = text.indexOf(']]', index + 2);
        if (end > index) {
            const raw = text.slice(index + 2, end);
            const target = raw.split('|')[0].split('#')[0].trim();
            const label = (raw.includes('|') ? raw.split('|').slice(1).join('|') : target).trim();
            const file = resolveKGDocumentReference(target, { allowFuzzy: true });
            if (file) {
                return {
                    start: index,
                    end: end + 2,
                    label: label || target,
                    raw: target,
                    path: getKGDocumentPath(file),
                };
            }
        }
    }

    if (text[index] === '@' && isKGDocMarkerBoundary(text[index - 1])) {
        for (const record of records) {
            const start = index + 1;
            const end = start + record.alias.length;
            if (!startsWithKGAlias(text, start, record.alias)) continue;
            if (!isKGDocMarkerBoundary(text[end])) continue;
            return {
                start: index,
                end,
                label: text.slice(start, end),
                raw: record.alias,
                path: record.path,
            };
        }
    }

    for (const record of records) {
        const end = index + record.alias.length;
        const marker = text[end];
        if (marker !== '@' && marker !== '$') continue;
        if (!startsWithKGAlias(text, index, record.alias)) continue;
        if (!isKGDocMarkerBoundary(text[end + 1])) continue;
        return {
            start: index,
            end: end + 1,
            label: text.slice(index, end),
            raw: record.alias,
            path: record.path,
        };
    }

    return null;
}

function collectKGDocumentMarkerMatches(text, records) {
    const matches = [];
    const value = String(text || '');
    const markerRe = /\[\[|@|\$/g;
    let marker;
    while ((marker = markerRe.exec(value))) {
        const markerIndex = marker.index;
        let match = null;
        if (marker[0] === '[[' || marker[0] === '@') {
            match = findKGDocMarkerAt(value, markerIndex, records);
        }
        if (!match && (marker[0] === '@' || marker[0] === '$')) {
            for (const record of records) {
                const start = markerIndex - record.alias.length;
                if (start < 0 || !startsWithKGAlias(value, start, record.alias)) continue;
                if (!isKGDocMarkerBoundary(value[start - 1]) || !isKGDocMarkerBoundary(value[markerIndex + 1])) continue;
                match = {
                    start,
                    end: markerIndex + 1,
                    label: value.slice(start, markerIndex),
                    raw: record.alias,
                    path: record.path,
                };
                break;
            }
        }
        if (!match) continue;
        const previous = matches[matches.length - 1];
        if (!previous || match.start >= previous.end) matches.push(match);
        markerRe.lastIndex = Math.max(markerRe.lastIndex, match.end);
    }
    return matches;
}

function extractKGMarkdownOutgoingReferences(content, currentPath = '') {
    const text = stripKGMarkdownCodeSegments(content || '');
    const refs = [];
    const wikiRe = /\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]/g;
    const mdRe = /(!?)\[[^\]]*\]\(([^)]+)\)/g;
    let match;

    while ((match = wikiRe.exec(text))) refs.push(match[1]);
    while ((match = mdRe.exec(text))) {
        if (match[1]) continue;
        const href = match[2].trim();
        if (!href || isKGExternalHref(href)) continue;
        refs.push(href);
    }

    const records = getKGDocAliasRecords(currentPath, { includeCurrent: true });
    for (const marker of collectKGDocumentMarkerMatches(text, records)) {
        refs.push(marker.raw);
    }

    return Array.from(new Set(refs.map(cleanKGDocumentReference).filter(Boolean)));
}

function createKGDocLinkElement(label, path) {
    const link = document.createElement('a');
    link.href = '#';
    link.className = 'kg-doc-link';
    link.dataset.docPath = path;
    link.textContent = label;
    link.title = `打开 ${label}`;
    return link;
}

function getKGDocumentLinkLabel(path) {
    const entry = findFileEntry(fileStore.files, path);
    const name = entry?.name || String(path || '').replace(/\\/g, '/').split('/').pop() || '未命名文档';
    return name.replace(/\.(md|markdown)$/i, '');
}

function encodeKGMarkdownLinkHref(path) {
    return String(path || '')
        .replace(/\\/g, '/')
        .replace(/ /g, '%20')
        .replace(/\(/g, '%28')
        .replace(/\)/g, '%29');
}

function createKGMarkdownDocumentLink(path) {
    const entry = findFileEntry(fileStore.files, path);
    if (!entry || entry.isDirectory) return '';
    if (!/\.(md|markdown)$/i.test(entry.name || entry.path || '')) return '';
    const label = getKGDocumentLinkLabel(path).replace(/([\[\]])/g, '\\$1');
    return `[${label}](${encodeKGMarkdownLinkHref(path)})`;
}

function getKGDraggedFilePaths(event) {
    const transfer = event?.dataTransfer;
    if (!transfer) return _kgActiveDraggedFilePaths.slice();
    if (event?.type !== 'drop' && _kgActiveDraggedFilePaths.length) {
        return _kgActiveDraggedFilePaths.slice();
    }
    const customTypes = [
        'application/x-knowledge-hub-file-paths',
        'application/x-knowledge-file-paths',
        'application/x-knowledge-file',
    ];
    for (const type of customTypes) {
        let custom = '';
        try { custom = transfer.getData(type); } catch (_) {}
        if (!custom) continue;
        try {
            const parsed = JSON.parse(custom);
            if (Array.isArray(parsed)) {
                const paths = parsed.filter(Boolean).map(normalizeKGDraggedPath).filter(Boolean);
                if (paths.length) return paths;
            }
            if (typeof parsed === 'string' && parsed.trim()) return [normalizeKGDraggedPath(parsed)];
        } catch (_) {
            const paths = custom.split(/\r?\n/).map(normalizeKGDraggedPath).filter(Boolean);
            if (paths.length) return paths;
        }
    }
    let uriList = '';
    try { uriList = transfer.getData('text/uri-list') || ''; } catch (_) {}
    const uriPaths = uriList
        .split(/\r?\n/)
        .map(path => path.trim())
        .filter(path => path && !path.startsWith('#'))
        .map(normalizeKGDraggedPath);
    if (uriPaths.length) return uriPaths;
    let plain = '';
    try { plain = transfer.getData('text/plain') || ''; } catch (_) {}
    const paths = plain.split(/\r?\n/).map(normalizeKGDraggedPath).filter(Boolean);
    const fallbackPaths = _kgActiveDraggedFilePaths.length ? _kgActiveDraggedFilePaths : _dragSourcePaths;
    return Array.from(new Set((paths.length ? paths : fallbackPaths)
        .map(normalizeKGDraggedPath)
        .filter(Boolean)));
}

function normalizeKGDraggedPath(path) {
    const raw = String(path || '').trim();
    if (!raw) return '';
    const candidates = [raw];
    try {
        const decoded = decodeURIComponent(raw);
        if (decoded && decoded !== raw) candidates.push(decoded);
    } catch (_) {}
    for (const candidate of candidates) {
        const normalized = candidate
            .replace(/^file:\/\//i, '')
            .replace(/\\/g, '/')
            .replace(/^\/+/, '');
        const entry = findFileEntry(fileStore.files, normalized) || findFileEntry(fileStore.files, candidate);
        if (entry?.path) return entry.path;
    }
    return raw.replace(/\\/g, '/');
}

function getKGDraggedMarkdownPaths(event) {
    return getKGDraggedFilePaths(event)
        .map(path => ({ path, entry: findFileEntry(fileStore.files, path) }))
        .filter(item => item.entry && !item.entry.isDirectory && /\.(md|markdown)$/i.test(item.entry.name || item.entry.path || ''))
        .map(item => item.path);
}

function formatKGMarkdownLinksForInsert(paths, mode = 'inline') {
    const links = Array.from(new Set(paths || []))
        .map(path => createKGMarkdownDocumentLink(path))
        .filter(Boolean);
    if (!links.length) return '';
    return mode === 'block' ? links.map(link => `- ${link}`).join('\n') : links.join('、');
}

function getCurrentKGDocumentPath() {
    return getKGDocumentPath(fileStore.openedFile);
}

function filterKGLinkTargetPaths(paths, { excludeCurrent = true, notify = true } = {}) {
    const currentPath = getCurrentKGDocumentPath();
    const unique = Array.from(new Set(paths || []))
        .map(normalizeKGDraggedPath)
        .filter(Boolean);
    const linkable = unique.filter(path => {
        const entry = findFileEntry(fileStore.files, path);
        if (!entry || entry.isDirectory) return false;
        if (!/\.(md|markdown)$/i.test(entry.name || entry.path || '')) return false;
        return !(excludeCurrent && currentPath && path === currentPath);
    });
    if (notify && unique.length && !linkable.length) {
        if (unique.includes(currentPath)) {
            showToast('当前文档不能链接到自己，请拖入其他 Markdown 文件', 'warning');
        } else {
            showToast('只能拖入 Markdown 文档建立文档链接', 'warning');
        }
    }
    return linkable;
}

function scheduleKGReaderDerivedRefresh(options = {}) {
    const outlineDelay = options.outlineDelay ?? 260;
    const graphDelay = options.graphDelay ?? 900;
    const refreshGraph = options.refreshGraph !== false;

    if (kgReaderOutlineRefreshTimer) clearTimeout(kgReaderOutlineRefreshTimer);
    kgReaderOutlineRefreshTimer = setTimeout(() => {
        renderKGHeadingOutline();
        kgReaderOutlineRefreshTimer = null;
    }, outlineDelay);

    if (refreshGraph && typeof refreshKGGraph === 'function' && currentPage === 'kg') {
        if (kgReaderEditGraphRefreshTimer) clearTimeout(kgReaderEditGraphRefreshTimer);
        kgReaderEditGraphRefreshTimer = setTimeout(() => {
            refreshKGGraph()
                .then(() => {
                    if (typeof renderKGBacklinks === 'function') renderKGBacklinks();
                })
                .catch(err => console.warn('refresh KG graph after edit failed:', err));
            kgReaderEditGraphRefreshTimer = null;
        }, graphDelay);
    }
}

function replaceKGDocMarkersInTextNode(node, records) {
    const text = node.nodeValue || '';
    const matches = collectKGDocumentMarkerMatches(text, records);
    if (!matches.length) return;

    const fragment = document.createDocumentFragment();
    let cursor = 0;
    for (const match of matches) {
        if (match.start > cursor) {
            fragment.appendChild(document.createTextNode(text.slice(cursor, match.start)));
        }
        fragment.appendChild(createKGDocLinkElement(match.label, match.path));
        cursor = match.end;
    }
    if (cursor < text.length) {
        fragment.appendChild(document.createTextNode(text.slice(cursor)));
    }
    node.parentNode.replaceChild(fragment, node);
}

function shouldSkipKGDocLinkNode(node) {
    const parent = node.parentElement;
    return !parent || !!parent.closest('a,code,pre,script,style,textarea,.kg-backlinks');
}

function enhanceKGReaderDocumentLinks() {
    const body = document.querySelector('#kg-md-reader .kg-reader-body');
    if (!body || !fileStore.openedFile) return;
    const currentPath = getKGDocumentPath(fileStore.openedFile);

    body.querySelectorAll('a[href]').forEach(anchor => {
        const href = anchor.getAttribute('href') || '';
        if (isKGExternalHref(href)) return;
        const target = resolveKGDocumentReference(href, { currentPath, allowFuzzy: true })
            || resolveKGDocumentReference(anchor.textContent || '', { currentPath, allowFuzzy: true });
        if (!target) return;
        anchor.href = '#';
        anchor.classList.add('kg-doc-link');
        anchor.dataset.docPath = getKGDocumentPath(target);
        anchor.title = `打开 ${target.name || target.path}`;
        anchor.removeAttribute('target');
    });

    const records = getKGDocAliasRecords(currentPath);
    if (!records.length) return;
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
            return shouldSkipKGDocLinkNode(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
        },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => replaceKGDocMarkersInTextNode(node, records));
}

function renderKGBacklinks() {
    const body = document.querySelector('#kg-md-reader .kg-reader-body');
    if (!body || !fileStore.openedFile) return;
    body.querySelector('.kg-backlinks')?.remove();

    const currentPath = getKGDocumentPath(fileStore.openedFile);
    if (!/\.(md|markdown)$/i.test(currentPath || fileStore.openedFile.name || '')) return;

    const incoming = new Map();
    for (const file of collectKGMarkdownFiles()) {
        const sourcePath = getKGDocumentPath(file);
        if (!sourcePath || sourcePath === currentPath) continue;
        const content = typeof file.content === 'string' ? file.content : '';
        if (!content.trim()) continue;
        const refs = extractKGMarkdownOutgoingReferences(content, sourcePath);
        const pointsHere = refs.some(ref => {
            const target = resolveKGDocumentReference(ref, { currentPath: sourcePath, allowFuzzy: true });
            return target && getKGDocumentPath(target) === currentPath;
        });
        if (pointsHere) incoming.set(sourcePath, file);
    }

    const outgoing = new Map();
    const outgoingRefs = extractKGMarkdownOutgoingReferences(fileStore.openedFile.content || '', currentPath);
    for (const ref of outgoingRefs) {
        const target = resolveKGDocumentReference(ref, { currentPath, allowFuzzy: true });
        const targetPath = getKGDocumentPath(target);
        if (target && targetPath && targetPath !== currentPath) {
            outgoing.set(targetPath, target);
        }
    }

    const renderTextLink = file => {
        const path = getKGDocumentPath(file);
        const label = (file.name || path).replace(/\.(md|markdown)$/i, '');
        return `
            <a href="#" class="kg-backlinks-text-link kg-doc-link" data-doc-path="${escapeHtml(path)}" title="${escapeHtml(path)}">
                ${escapeHtml(label)}
            </a>
        `;
    };

    const section = document.createElement('section');
    section.className = 'kg-backlinks';
    section.setAttribute('data-kg-dropzone', 'backlinks');
    section.addEventListener('dragenter', handleKGBacklinksDragEnter);
    section.addEventListener('dragover', handleKGBacklinksDragOver);
    section.addEventListener('dragleave', handleKGBacklinksDragLeave);
    section.addEventListener('drop', handleKGBacklinksDrop);
    const linkedFiles = new Map();
    for (const file of incoming.values()) linkedFiles.set(getKGDocumentPath(file), file);
    for (const file of outgoing.values()) linkedFiles.set(getKGDocumentPath(file), file);
    const linkedItems = Array.from(linkedFiles.values()).map(renderTextLink).join('');

    section.innerHTML = `
        <div class="kg-backlinks-head">
            <div class="kg-backlinks-title">LINKS TO THIS PAGE</div>
            <div class="kg-backlinks-count">${linkedFiles.size}</div>
        </div>
        <div class="kg-backlinks-list">
            ${linkedItems || '<div class="kg-backlinks-empty">暂无链接，可从左侧拖入 Markdown 文件建立连接。</div>'}
        </div>
    `;
    body.appendChild(section);
}

async function openKGDocumentLink(path) {
    const entry = findFileEntry(fileStore.files, path);
    if (!entry || entry.isDirectory) {
        showToast('没有找到对应的 Markdown 文档', 'warning');
        return;
    }
    if (currentPage !== 'kg') navigateTo('kg');
    await selectFile(path);
    const shell = document.querySelector('.kg-reader-shell');
    if (shell) shell.scrollTo({ top: 0, behavior: 'smooth' });
}

function handleKGDocumentLinkClick(event) {
    const link = event.target.closest?.('.kg-doc-link[data-doc-path]');
    if (!link) return;
    event.preventDefault();
    openKGDocumentLink(link.dataset.docPath || '').catch(err => {
        showToast('打开文档失败: ' + (err.message || '未知错误'), 'error');
    });
}

function renderKGHeadingOutline() {
    const infoEl = document.getElementById('kg-node-info');
    if (!infoEl) return;
    const file = fileStore.openedFile;
    if (!file || !String(file.content || '').trim()) {
        infoEl.innerHTML = `
            <div class="kg-outline-empty">
                <div class="kg-outline-empty-icon">i</div>
                <p>选择 Markdown 文件后显示本文目录</p>
            </div>`;
        return;
    }

    const headings = extractKGHeadingsFromMarkdown(file.content || '');
    if (!headings.length) {
        infoEl.innerHTML = `
            <div class="kg-outline">
                <div class="kg-outline-title">ON THIS PAGE</div>
                <div class="kg-outline-muted">当前文档还没有 Markdown 标题。</div>
            </div>`;
        return;
    }

    const items = headings.map(h => `
        <button class="kg-outline-item level-${Math.min(h.level, 4)}" data-heading-index="${h.index}" onclick="scrollKGReaderHeading(${h.index})" title="${escapeHtml(h.text)}">
            <span>${escapeHtml(h.text)}</span>
        </button>
    `).join('');

    infoEl.innerHTML = `
        <div class="kg-outline">
            <div class="kg-outline-title">ON THIS PAGE</div>
            <div class="kg-outline-list">${items}</div>
        </div>`;
}

function scrollKGReaderHeading(index) {
    const target = document.getElementById(`kg-heading-${index}`);
    const shell = document.querySelector('.kg-reader-shell');
    if (target && shell) {
        const top = target.getBoundingClientRect().top - shell.getBoundingClientRect().top + shell.scrollTop - 18;
        shell.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
        target.classList.add('kg-heading-highlight');
        setTimeout(() => target.classList.remove('kg-heading-highlight'), 1200);
        document.querySelectorAll('.kg-outline-item').forEach(item => {
            item.classList.toggle('is-active', item.dataset.headingIndex === String(index));
        });
        return;
    }

    const editor = document.getElementById('kg-reader-editor');
    if (!editor || !fileStore.openedFile) return;
    const headings = extractKGHeadingsFromMarkdown(fileStore.openedFile.content || '');
    const heading = headings[index];
    if (!heading) return;
    const lines = String(fileStore.openedFile.content || '').split(/\r?\n/);
    const charPos = lines.slice(0, heading.line).join('\n').length + (heading.line > 0 ? 1 : 0);
    editor.focus();
    editor.setSelectionRange(charPos, charPos);
    editor.scrollTop = Math.max(0, (heading.line - 3) * 24);
}

function getAttachmentFolderForOpenedFile() {
    const file = fileStore.openedFile;
    const path = file?.path || file?.name || 'untitled.md';
    const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
    const base = (file?.name || 'image').replace(/\.[^.]+$/, '').replace(/[^\w\u4e00-\u9fff-]+/g, '-').slice(0, 48) || 'images';
    return `${parent ? parent + '/' : ''}_attachments/${base}`;
}

async function uploadPastedImageToServer(file) {
    const extFromType = (file.type || '').split('/')[1] || 'png';
    const ext = extFromType === 'jpeg' ? 'jpg' : extFromType.replace(/[^a-z0-9]/gi, '') || 'png';
    const name = `image-${Date.now()}.${ext}`;
    const imageFile = new File([file], name, { type: file.type || `image/${ext}` });
    const folder = getAttachmentFolderForOpenedFile();
    const formData = new FormData();
    formData.append('file', imageFile);
    formData.append('folder', folder);
    const data = await api('POST', '/file-resources/upload', formData, 300000);
    if (data?.files) {
        fileStore.files = data.files;
        fileStore.rootDirName = '我的文档库';
        renderFileTree();
        updateStats();
    }
    return data.path;
}

function insertTextIntoKGEditor(text) {
    const editor = document.getElementById('kg-reader-editor');
    if (!editor) return;
    const start = editor.selectionStart ?? editor.value.length;
    const end = editor.selectionEnd ?? start;
    const nextValue = editor.value.slice(0, start) + text + editor.value.slice(end);
    editor.value = nextValue;
    const cursor = start + text.length;
    editor.setSelectionRange(cursor, cursor);
    handleKGReaderEditInput(nextValue);
}

async function handleKGReaderPaste(event) {
    const items = Array.from(event.clipboardData?.items || []);
    const imageItem = items.find(item => item.kind === 'file' && item.type.startsWith('image/'));
    if (!imageItem) return;
    if (!isEditableCloudFile(fileStore.openedFile)) {
        showToast('请先打开云端 Markdown 文件再粘贴图片', 'warning');
        return;
    }

    event.preventDefault();
    try {
        showToast('正在上传粘贴的图片...', 'info');
        const file = imageItem.getAsFile();
        const path = await uploadPastedImageToServer(file);
        const imageUrl = `${API_BASE}/file-resources/raw/${encodeResourcePathForUrl(path)}`;
        insertTextIntoKGEditor(`\n![${fileStore.openedFile.name || 'image'}](${imageUrl})\n`);
        showToast('图片已嵌入当前 Markdown', 'success');
    } catch (err) {
        showToast('粘贴图片失败: ' + (err.message || '未知错误'), 'error');
    }
}

function handleKGReaderFileDragOver(event) {
    const paths = filterKGLinkTargetPaths(getKGDraggedMarkdownPaths(event), { excludeCurrent: false, notify: false });
    if (!paths.length) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'link';
    event.currentTarget.classList.add('is-doc-drag-over');
}

function handleKGReaderFileDragLeave(event) {
    event.currentTarget.classList.remove('is-doc-drag-over');
}

function handleKGReaderFileDrop(event) {
    const paths = filterKGLinkTargetPaths(getKGDraggedMarkdownPaths(event), { excludeCurrent: false });
    if (!paths.length) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.classList.remove('is-doc-drag-over');
    const text = formatKGMarkdownLinksForInsert(paths, 'inline');
    if (!text) {
        showToast('只能拖入 Markdown 文档建立文档链接', 'warning');
        return;
    }
    insertTextIntoKGEditor(text);
    showToast(`已插入 ${paths.length} 个文档链接`, 'success');
}

async function appendKGDocumentLinksToOpenedFile(paths) {
    const openedFile = fileStore.openedFile;
    const isMarkdown = openedFile && /\.(md|markdown)$/i.test(openedFile.name || openedFile.path || '');
    const canPersist = isEditableCloudFile(openedFile) || !!openedFile?.handle;
    if (!isMarkdown || !canPersist) {
        showToast('请先打开可编辑的 Markdown 文件', 'warning');
        return false;
    }
    const linkPaths = filterKGLinkTargetPaths(paths, { excludeCurrent: true });
    if (!linkPaths.length) return false;
    const blockLinks = formatKGMarkdownLinksForInsert(linkPaths, 'block');
    if (!blockLinks) {
        showToast('只能拖入 Markdown 文档建立文档链接', 'warning');
        return false;
    }
    const current = String(fileStore.openedFile.content || '');
    const existingHeading = /(^|\n)##\s+关联文档\s*(\n|$)/.test(current);
    const gap = current
        ? (current.endsWith('\n\n') ? '' : (current.endsWith('\n') ? '\n' : '\n\n'))
        : '';
    const insertion = existingHeading
        ? `${current.endsWith('\n') ? '' : '\n'}${blockLinks}\n`
        : `${gap}## 关联文档\n\n${blockLinks}\n`;
    const nextValue = current + insertion;
    fileStore.openedFile.content = nextValue;
    markOpenedFileContentChanged(nextValue);

    const editor = document.getElementById('kg-reader-editor');
    if (editor) {
        editor.value = nextValue;
        editor.focus();
        editor.setSelectionRange(nextValue.length, nextValue.length);
    } else {
        renderKGMarkdownReader();
    }
    if (currentPage === 'kg') {
        renderKGBacklinks();
    }
    scheduleKGReaderDerivedRefresh({ outlineDelay: 120, graphDelay: 1100 });
    if (isEditableCloudFile(openedFile)) {
        saveOpenedCloudFile({ silent: true }).catch(() => {
            showToast('链接已插入本地草稿，云端恢复后会自动同步', 'warning');
        });
    } else if (openedFile.handle) {
        saveLocalFile().catch(err => showToast('本地保存失败: ' + (err.message || '未知错误'), 'error'));
    }
    return linkPaths.length;
}

function handleKGBacklinksDragEnter(event) {
    const paths = filterKGLinkTargetPaths(getKGDraggedMarkdownPaths(event), { excludeCurrent: true, notify: false });
    if (!paths.length) return;
    event.preventDefault();
    event.stopPropagation();
}

function handleKGBacklinksDragOver(event) {
    const paths = filterKGLinkTargetPaths(getKGDraggedMarkdownPaths(event), { excludeCurrent: true, notify: false });
    if (!paths.length) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'link';
    event.currentTarget.classList.add('is-doc-drag-over');
}

function handleKGBacklinksDragLeave(event) {
    if (event.relatedTarget && event.currentTarget.contains(event.relatedTarget)) return;
    event.currentTarget.classList.remove('is-doc-drag-over');
}

function handleKGBacklinksDrop(event) {
    if (event.__kgDropHandled) return;
    const paths = filterKGLinkTargetPaths(getKGDraggedMarkdownPaths(event), { excludeCurrent: true });
    if (!paths.length) return;
    event.__kgDropHandled = true;
    event.preventDefault();
    event.stopPropagation();
    _kgLastDocumentLinkDropAt = Date.now();
    event.currentTarget.classList.remove('is-doc-drag-over');
    appendKGDocumentLinksToOpenedFile(paths).then(created => {
        if (created) {
            showToast(`已在本文末尾建立 ${created} 个文档链接`, 'success');
        }
    }).catch(err => showToast('建立文档链接失败: ' + (err.message || '未知错误'), 'error'));
}

function getKGDragDropZone(event) {
    const target = event?.target;
    if (!target || typeof target.closest !== 'function') return null;
    return target.closest('.kg-backlinks, #kg-reader-editor, #kg-md-reader .kg-reader-body');
}

function installKGDocumentDropFallback() {
    if (document.documentElement.dataset.kgDropFallback === 'ready') return;
    document.documentElement.dataset.kgDropFallback = 'ready';

    // Capture the event before browser-native textarea handling can consume it.
    document.addEventListener('dragenter', (event) => {
        const zone = getKGDragDropZone(event);
        if (!zone || !getKGDraggedMarkdownPaths(event).length) return;
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = 'link';
        zone.classList.add('is-doc-drag-over');
    }, true);

    document.addEventListener('dragover', (event) => {
        const zone = getKGDragDropZone(event);
        if (!zone || !getKGDraggedMarkdownPaths(event).length) return;
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = 'link';
        zone.classList.add('is-doc-drag-over');
    }, true);

    document.addEventListener('drop', (event) => {
        const zone = getKGDragDropZone(event);
        const paths = zone ? getKGDraggedMarkdownPaths(event) : [];
        if (!zone || !paths.length) return;
        event.preventDefault();
        event.stopPropagation();
        _kgLastDocumentLinkDropAt = Date.now();
        zone.classList.remove('is-doc-drag-over');
        event.__kgDropHandled = true;
        if (zone.id === 'kg-reader-editor') {
            insertTextIntoKGEditor(formatKGMarkdownLinksForInsert(paths, 'inline'));
            showToast(`已插入 ${paths.length} 个文档链接`, 'success');
            return;
        }
        appendKGDocumentLinksToOpenedFile(paths).then(created => {
            if (created) showToast(`已在本文末尾建立 ${created} 个文档链接`, 'success');
        }).catch(err => showToast('建立文档链接失败: ' + (err.message || '未知错误'), 'error'));
    }, true);

    document.addEventListener('dragend', (event) => {
        if (Date.now() - _kgLastDocumentLinkDropAt < 700) return;
        const paths = getKGDraggedMarkdownPaths(event);
        if (!paths.length) return;
        const hit = document.elementFromPoint(event.clientX, event.clientY);
        const zone = hit?.closest?.('.kg-backlinks, #kg-reader-editor, #kg-md-reader .kg-reader-body');
        if (!zone) return;
        zone.classList.remove('is-doc-drag-over');
        if (zone.id === 'kg-reader-editor') {
            const linkPaths = filterKGLinkTargetPaths(paths, { excludeCurrent: false });
            const text = formatKGMarkdownLinksForInsert(linkPaths, 'inline');
            if (text) {
                insertTextIntoKGEditor(text);
                showToast(`已插入 ${linkPaths.length} 个文档链接`, 'success');
            }
            return;
        }
        appendKGDocumentLinksToOpenedFile(paths).then(created => {
            if (created) showToast(`已在本文末尾建立 ${created} 个文档链接`, 'success');
        }).catch(err => showToast('建立文档链接失败: ' + (err.message || '未知错误'), 'error'));
    }, true);
}

function renderKGMarkdownReader() {
    const reader = document.getElementById('kg-md-reader');
    if (!reader) return;

    if (!fileStore.openedFile) {
        reader.classList.remove('kg-reader-pdf-mode');
        reader.innerHTML = `
            <div class="kg-reader-empty">
                <div class="kg-reader-empty-title">选择左侧 Markdown 文件</div>
                <div class="kg-reader-empty-subtitle">中间区域会以阅读模式渲染内容，右上角图谱按文件名相关度连接文档。</div>
            </div>`;
        renderKGHeadingOutline();
        return;
    }

    const file = fileStore.openedFile;
    reader.classList.toggle('kg-reader-pdf-mode', !!file.isPdf);
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    const isMarkdown = ext === 'md' || ext === 'markdown';
    const title = escapeHtml(file.name.replace(/\.(md|markdown|txt)$/i, ''));
    const path = escapeHtml(file.path || file.name);
    const duplicateLeadingTitle = isMarkdown && hasDuplicateLeadingMarkdownTitle(file.content || '', file.name || '');
    const canEdit = (typeof hasWriteAccess !== 'function' || hasWriteAccess())
        && ['md', 'markdown', 'txt', 'csv', 'json', 'html', 'htm'].includes(ext);
    let body = '';
    const readerHeadingRow = (duplicateLeadingTitle && !canEdit) ? '' : `
            <div class="kg-reader-heading-row${duplicateLeadingTitle ? ' without-title' : ''}">
                ${duplicateLeadingTitle ? '' : `<h1>${title}</h1>`}
                ${canEdit ? `
                    <div class="kg-reader-actions">
                        <button class="kg-reader-mode-btn ${kgReaderMode === 'render' ? 'active' : ''}" onclick="setKGReaderMode('render')">渲染</button>
                        <button class="kg-reader-mode-btn ${kgReaderMode === 'edit' ? 'active' : ''}" onclick="setKGReaderMode('edit')">编辑</button>
                        <button class="kg-reader-save-btn" onclick="saveLocalFile()">保存</button>
                        <span class="cloud-save-status" data-cloud-save-status style="display:none"></span>
                    </div>
                ` : ''}
            </div>`;

    if (file.isImage && file.content && /^(data:|blob:|https?:\/\/|\/)/i.test(String(file.content))) {
        body = `<div class="kg-media-preview"><img src="${escapeHtml(file.content)}" alt="${escapeHtml(file.name)}"></div>`;
    } else if (file.isPdf) {
        body = getPdfPreviewMarkup(file);
    } else if (canEdit && kgReaderMode === 'edit') {
        initKGReaderUndo(file.content || '', file.path || file.name || '');
        body = `
            <textarea class="kg-reader-editor" id="kg-reader-editor"
                spellcheck="false"
                onpaste="handleKGReaderPaste(event)"
                ondragover="handleKGReaderFileDragOver(event)"
                ondragleave="handleKGReaderFileDragLeave(event)"
                ondrop="handleKGReaderFileDrop(event)"
                onkeydown="handleKGReaderEditKeyDown(event)"
                oninput="handleKGReaderEditInput(this.value)">${escapeHtml(file.content || '')}</textarea>`;
    } else if (isMarkdown) {
        body = renderMarkdown(file.content || '');
    } else if (['txt', 'csv', 'json', 'html', 'htm'].includes(ext)) {
        body = `<pre class="kg-reader-plain-text">${escapeHtml(file.content || '')}</pre>`;
    } else if (['docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt'].includes(ext)) {
        body = renderMarkdown(file.content || '');
    } else {
        body = `<pre>${escapeHtml(file.content || '暂不支持预览该文件类型')}</pre>`;
    }

    reader.innerHTML = `
        <header class="kg-reader-titlebar">
            <div class="kg-reader-path">${path}</div>
            ${readerHeadingRow}
        </header>
        <div class="kg-reader-body">${body || '<p class="kg-reader-muted">这个文件还是空的。</p>'}</div>`;
    setCloudSaveStatus(file.isDirty ? 'dirty' : (isEditableCloudFile(file) ? 'saved' : 'idle'), '', { force: true });
    if (isMarkdown) {
        enhanceKGReaderHeadings();
        enhanceKGReaderDocumentLinks();
        renderKGBacklinks();
    }
    renderKGHeadingOutline();
}

/**
 * Avoid showing the filename as a second H1 when the Markdown document
 * already starts with the same title. The document's own heading remains the
 * canonical visible title, so editing and exported content are unchanged.
 */
function hasDuplicateLeadingMarkdownTitle(content, filename) {
    const normalize = value => String(value || '')
        .replace(/\.(md|markdown|txt)$/i, '')
        .replace(/[\\/_-]+/g, ' ')
        .replace(/[\u3000\s]+/g, '')
        .replace(/[“”"'`*_~#[\](){}<>:：、，。！？!?；;·•]/g, '')
        .toLocaleLowerCase();
    const fileTitle = normalize(filename);
    if (!fileTitle) return false;
    const lines = String(content || '').replace(/^\uFEFF/, '').split(/\r?\n/);
    let started = false;
    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line) continue;
        if (!started && line === '---') continue;
        started = true;
        const match = line.match(/^#{1}\s+(.+?)\s*#*$/);
        if (!match) return false;
        const heading = normalize(match[1]);
        if (!heading || !fileTitle) return false;
        return heading === fileTitle || (heading.length > 8 && (heading.includes(fileTitle) || fileTitle.includes(heading)));
    }
    return false;
}

function setKGReaderMode(mode) {
    if (kgReaderMode === 'edit') {
        flushCloudDraftSave();
        flushKGReaderEditorMirror();
        flushKGReaderUndoSnapshot();
        kgReaderLastEditAt = 0;
    }
    kgReaderMode = mode === 'edit' ? 'edit' : 'render';
    if (currentPage === 'kg') renderKGMarkdownReader();
    if (kgReaderMode === 'render') {
        scheduleKGReaderDerivedRefresh({ outlineDelay: 80, graphDelay: 500, refreshGraph: true });
    }
}

function getKGReaderUndoKey() {
    return fileStore.openedFile ? (fileStore.openedFile.path || fileStore.openedFile.name || '') : '';
}

function initKGReaderUndo(value, key = getKGReaderUndoKey()) {
    if (kgReaderUndoState.key === key && kgReaderUndoState.stack.length) return;
    kgReaderUndoState = {
        key,
        stack: [String(value || '')],
        index: 0,
        applying: false,
        lastRecordedAt: Date.now(),
        pendingValue: null,
    };
}

function pushKGReaderUndoSnapshot(value) {
    const key = getKGReaderUndoKey();
    if (!key || kgReaderUndoState.applying) return;
    if (kgReaderUndoState.key !== key || !kgReaderUndoState.stack.length) {
        initKGReaderUndo(fileStore.openedFile?.content || '', key);
    }
    const nextValue = String(value || '');
    if (kgReaderUndoState.stack[kgReaderUndoState.index] === nextValue) return;
    kgReaderUndoState.stack = kgReaderUndoState.stack.slice(0, kgReaderUndoState.index + 1);
    kgReaderUndoState.stack.push(nextValue);
    if (kgReaderUndoState.stack.length > 80) {
        kgReaderUndoState.stack.shift();
    }
    kgReaderUndoState.index = kgReaderUndoState.stack.length - 1;
    kgReaderUndoState.lastRecordedAt = Date.now();
    kgReaderUndoState.pendingValue = null;
}

function recordKGReaderUndo(value) {
    const now = Date.now();
    const nextValue = String(value || '');
    const previousValue = kgReaderUndoState.stack[kgReaderUndoState.index] || '';
    const structuralChange = /\n$/.test(nextValue) || Math.abs(nextValue.length - previousValue.length) > 24;
    if (structuralChange || now - (kgReaderUndoState.lastRecordedAt || 0) > 700) {
        if (kgReaderUndoRecordTimer) {
            clearTimeout(kgReaderUndoRecordTimer);
            kgReaderUndoRecordTimer = null;
        }
        pushKGReaderUndoSnapshot(nextValue);
        return;
    }

    kgReaderUndoState.pendingValue = nextValue;
    if (kgReaderUndoRecordTimer) clearTimeout(kgReaderUndoRecordTimer);
    kgReaderUndoRecordTimer = setTimeout(() => {
        pushKGReaderUndoSnapshot(kgReaderUndoState.pendingValue);
        kgReaderUndoRecordTimer = null;
    }, 720);
}

function flushKGReaderUndoSnapshot() {
    if (kgReaderUndoRecordTimer) {
        clearTimeout(kgReaderUndoRecordTimer);
        kgReaderUndoRecordTimer = null;
    }
    if (kgReaderUndoState.pendingValue !== null) {
        pushKGReaderUndoSnapshot(kgReaderUndoState.pendingValue);
    }
}

function applyKGReaderUndo(delta) {
    const editor = document.getElementById('kg-reader-editor');
    if (!editor || !kgReaderUndoState.stack.length) return;
    if (delta < 0) flushKGReaderUndoSnapshot();
    const nextIndex = Math.max(0, Math.min(kgReaderUndoState.stack.length - 1, kgReaderUndoState.index + delta));
    if (nextIndex === kgReaderUndoState.index) return;
    kgReaderUndoState.index = nextIndex;
    const value = kgReaderUndoState.stack[nextIndex] || '';
    kgReaderUndoState.applying = true;
    editor.value = value;
    editor.focus();
    editor.setSelectionRange(value.length, value.length);
    handleKGReaderEditInput(value, { fromUndo: true });
    kgReaderUndoState.applying = false;
}

function handleKGReaderEditKeyDown(event) {
    if (event.key === 'Tab' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        applyKGReaderTabIndent(event.shiftKey);
        return;
    }

    const isMod = event.ctrlKey || event.metaKey;
    const key = (event.key || '').toLowerCase();
    if (!isMod || (key !== 'z' && key !== 'y')) return;
    event.preventDefault();
    const redo = key === 'y' || (key === 'z' && event.shiftKey);
    applyKGReaderUndo(redo ? 1 : -1);
}

function applyKGReaderTabIndent(outdent = false) {
    const editor = document.getElementById('kg-reader-editor');
    if (!editor) return;
    const indent = '\u3000\u3000';
    const value = editor.value;
    const start = editor.selectionStart ?? value.length;
    const end = editor.selectionEnd ?? start;

    if (outdent) {
        const lineStart = value.lastIndexOf('\n', Math.max(0, start - 1)) + 1;
        const linePrefix = value.slice(lineStart, lineStart + 2);
        let removeCount = 0;
        if (linePrefix === indent) {
            removeCount = 2;
        } else if (linePrefix === '  ') {
            removeCount = 2;
        }
        if (!removeCount) return;
        const nextValue = value.slice(0, lineStart) + value.slice(lineStart + removeCount);
        editor.value = nextValue;
        const nextStart = Math.max(lineStart, start - removeCount);
        const nextEnd = Math.max(lineStart, end - removeCount);
        editor.setSelectionRange(nextStart, nextEnd);
        handleKGReaderEditInput(nextValue, { immediateMirror: true });
        return;
    }

    const nextValue = value.slice(0, start) + indent + value.slice(end);
    editor.value = nextValue;
    const cursor = start + indent.length;
    editor.setSelectionRange(cursor, cursor);
    handleKGReaderEditInput(nextValue, { immediateMirror: true });
}

function syncKGReaderEditorMirror(value) {
    const nextValue = String(value ?? '');
    const editorContent = document.getElementById('kg-editor-content');
    const ragEditorContent = document.getElementById('rag-editor-content');
    if (editorContent && editorContent.value !== nextValue) editorContent.value = nextValue;
    if (ragEditorContent && ragEditorContent.value !== nextValue) ragEditorContent.value = nextValue;
}

function scheduleKGReaderEditorMirror(value, delay = 320) {
    kgReaderEditorMirrorValue = String(value ?? '');
    if (kgReaderEditorMirrorTimer) clearTimeout(kgReaderEditorMirrorTimer);
    kgReaderEditorMirrorTimer = setTimeout(() => {
        syncKGReaderEditorMirror(kgReaderEditorMirrorValue);
        kgReaderEditorMirrorValue = null;
        kgReaderEditorMirrorTimer = null;
    }, delay);
}

function flushKGReaderEditorMirror() {
    if (kgReaderEditorMirrorTimer) {
        clearTimeout(kgReaderEditorMirrorTimer);
        kgReaderEditorMirrorTimer = null;
    }
    if (kgReaderEditorMirrorValue !== null) {
        syncKGReaderEditorMirror(kgReaderEditorMirrorValue);
        kgReaderEditorMirrorValue = null;
    }
}

function handleKGReaderEditInput(value, options = {}) {
    if (!fileStore.openedFile) return;
    kgReaderLastEditAt = Date.now();
    markOpenedFileContentChanged(value);
    if (!options.fromUndo) {
        recordKGReaderUndo(value);
    }
    if (options.fromUndo || options.immediateMirror) {
        flushKGReaderEditorMirror();
        syncKGReaderEditorMirror(value);
    } else {
        scheduleKGReaderEditorMirror(value);
    }
    scheduleKGReaderDerivedRefresh({ outlineDelay: 1200, refreshGraph: false });
}

const _editorPreviewState = { kg: false, rag: false };

function toggleEditorPreview(section) {
    const textarea = document.getElementById(`${section}-editor-content`);
    const preview = document.getElementById(`${section}-editor-preview`);
    const toggleBtn = document.getElementById(`${section}-preview-toggle`);
    if (!textarea || !preview) return;

    const isImg = fileStore.openedFile
        && fileStore.openedFile.isImage
        && fileStore.openedFile.content
        && /^(data:|blob:|https?:\/\/|\/)/i.test(String(fileStore.openedFile.content));
    const isPdf = fileStore.openedFile && fileStore.openedFile.isPdf;

    if (isPdf) {
        textarea.style.display = 'none';
        preview.style.display = 'flex';
        preview.innerHTML = getPdfPreviewMarkup(fileStore.openedFile);
        if (toggleBtn) { toggleBtn.style.background = 'var(--primary)'; toggleBtn.style.color = '#fff'; }
        _editorPreviewState[section] = true;
        return;
    }

    if (isImg) {
        const isPreview = _editorPreviewState[section];
        if (isPreview) {
            textarea.style.display = 'none';
            preview.style.display = 'flex';
            preview.innerHTML = `<div class="image-preview-container"><img src="${escapeHtml(fileStore.openedFile.content)}" alt="${escapeHtml(fileStore.openedFile.name)}" class="image-preview-img" /></div>`;
            if (toggleBtn) { toggleBtn.style.background = 'var(--primary)'; toggleBtn.style.color = '#fff'; }
        } else {
            textarea.style.display = '';
            textarea.value = '[图片文件 - 切换到预览模式查看]';
            preview.style.display = 'none';
            if (toggleBtn) { toggleBtn.style.background = ''; toggleBtn.style.color = ''; }
        }
        _editorPreviewState[section] = !_editorPreviewState[section];
        return;
    }

    _editorPreviewState[section] = !_editorPreviewState[section];
    const isPreview = _editorPreviewState[section];

    if (isPreview) {
        textarea.style.display = 'none';
        preview.style.display = '';
        preview.innerHTML = renderMarkdown(fileStore.openedFile ? fileStore.openedFile.content : '');
        if (toggleBtn) toggleBtn.style.background = 'var(--primary)';
        if (toggleBtn) toggleBtn.style.color = '#fff';
    } else {
        textarea.style.display = '';
        preview.style.display = 'none';
        if (toggleBtn) toggleBtn.style.background = '';
        if (toggleBtn) toggleBtn.style.color = '';
    }
}

function openConvertDialog() {
    document.getElementById('convert-modal').style.display = 'flex';
    document.getElementById('convert-upload-section').style.display = '';
    document.getElementById('convert-progress-section').style.display = 'none';
    document.getElementById('convert-result-section').style.display = 'none';
    convertedMarkdown = '';
}

function closeConvertDialog() {
    document.getElementById('convert-modal').style.display = 'none';
}

function resetConvertDialog() {
    document.getElementById('convert-upload-section').style.display = '';
    document.getElementById('convert-progress-section').style.display = 'none';
    document.getElementById('convert-result-section').style.display = 'none';
    document.getElementById('convert-file-input').value = '';
    convertedMarkdown = '';
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

function getFileTypeLabel(name) {
    const ext = name.split('.').pop().toLowerCase();
    const map = {
        pdf: 'PDF 文档', docx: 'Word 文档', doc: 'Word 文档',
        xlsx: 'Excel 表格', xls: 'Excel 表格', csv: 'CSV 数据',
        pptx: 'PPT 演示', ppt: 'PPT 演示',
        html: 'HTML 页面', htm: 'HTML 页面',
        json: 'JSON 数据', yaml: 'YAML 数据', yml: 'YAML 数据', xml: 'XML 数据',
        rtf: 'RTF 文档', odt: 'OpenDocument 文档', epub: 'EPUB 电子书',
        txt: '文本文件', md: 'Markdown', markdown: 'Markdown',
        png: 'PNG 图片', jpg: 'JPG 图片', jpeg: 'JPEG 图片',
        bmp: 'BMP 图片', tiff: 'TIFF 图片', tif: 'TIFF 图片',
        gif: 'GIF 图片', webp: 'WebP 图片', svg: 'SVG 图片',
    };
    return map[ext] || ext.toUpperCase() + ' 文件';
}

function updateConvertProgress(percent, status) {
    const fill = document.getElementById('convert-progress-fill');
    const glow = document.getElementById('convert-progress-glow');
    const statusEl = document.getElementById('convert-progress-status');
    const percentEl = document.getElementById('convert-progress-percent');

    if (fill) fill.style.width = percent + '%';
    if (glow) glow.style.left = percent + '%';
    if (statusEl) statusEl.textContent = status;
    if (percentEl) percentEl.textContent = percent + '%';
}

async function handleConvertFile(files) {
    if (!files || !files.length) return;
    const file = files[0];

    document.getElementById('convert-upload-section').style.display = 'none';
    document.getElementById('convert-progress-section').style.display = '';
    document.getElementById('convert-result-section').style.display = 'none';

    document.getElementById('convert-file-name').textContent = file.name;
    document.getElementById('convert-file-meta').textContent =
        getFileTypeLabel(file.name) + ' · ' + formatFileSize(file.size);

    updateConvertProgress(5, '正在读取文件...');

    try {
        const formData = new FormData();
        formData.append('file', file);

        updateConvertProgress(15, '正在上传文件...');

        await new Promise(r => setTimeout(r, 200));
        updateConvertProgress(25, '正在解析文档...');

        const data = await api('POST', '/documents/convert', formData, 300000);

        if (data.success) {
            updateConvertProgress(90, '正在生成 Markdown...');

            await new Promise(r => setTimeout(r, 150));
            updateConvertProgress(100, '转换完成');

            await new Promise(r => setTimeout(r, 500));

            document.getElementById('convert-progress-section').style.display = 'none';
            document.getElementById('convert-result-section').style.display = '';

            convertedMarkdown = data.markdown_content;
            convertedFileName = data.original_name ? data.original_name.replace(/\.[^.]+$/, '') : 'converted';

            document.getElementById('convert-result-name').textContent = data.original_name;
            const cleaning = data.metadata?.cleaning || {};
            const cleaningSummary = Number(cleaning.removed_chars || 0) > 0
                ? ` · 清洗 ${cleaning.removed_chars} 字符`
                : ' · 结构清洗完成';
            document.getElementById('convert-result-meta').textContent =
                getFileTypeLabel(file.name) + ' · ' + formatFileSize(file.size) + ' → Markdown ' + formatFileSize(data.markdown_content.length) + cleaningSummary;
            document.getElementById('convert-preview').value = data.markdown_content;

            const renderedEl = document.getElementById('convert-preview-rendered');
            if (renderedEl && typeof renderMarkdown === 'function') {
                renderedEl.innerHTML = renderMarkdown(data.markdown_content);
            }

            switchConvertPreview('source');
            showToast('文件转换成功', 'success');
        } else {
            document.getElementById('convert-progress-section').style.display = 'none';
            document.getElementById('convert-upload-section').style.display = '';
            showToast('文件转换失败: ' + (data.detail || data.error || '转换服务不可用，请确保后端服务已启动'), 'error');
        }
    } catch (err) {
        document.getElementById('convert-progress-section').style.display = 'none';
        document.getElementById('convert-upload-section').style.display = '';
        showToast('文件转换失败: ' + (err.message || '未知错误'), 'error');
    }
}

function switchConvertPreview(tab) {
    document.querySelectorAll('.preview-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });
    const sourceEl = document.getElementById('convert-preview');
    const renderedEl = document.getElementById('convert-preview-rendered');
    if (tab === 'source') {
        if (sourceEl) sourceEl.style.display = '';
        if (renderedEl) renderedEl.style.display = 'none';
    } else {
        if (sourceEl) sourceEl.style.display = 'none';
        if (renderedEl) {
            renderedEl.style.display = '';
            if (convertedMarkdown && typeof renderMarkdown === 'function') {
                renderedEl.innerHTML = renderMarkdown(convertedMarkdown);
            }
        }
    }
}

function downloadConverted() {
    if (!convertedMarkdown) return;
    const blob = new Blob([convertedMarkdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = (convertedFileName || 'converted') + '.md';
    a.click();
    URL.revokeObjectURL(url);
    showToast('Markdown 文件已下载', 'success');
}

let convertedFileName = '';

function collectFileLibraryDirectories(items = fileStore.files, result = []) {
    (items || []).forEach(item => {
        if (!item || !item.isDirectory) return;
        result.push({ path: item.path || '', name: item.name || item.path || '未命名文件夹' });
        collectFileLibraryDirectories(item.children || [], result);
    });
    return result;
}

async function sendConvertedToFileLibrary() {
    if (!convertedMarkdown) {
        showToast('请先转化文件', 'warning');
        return;
    }
    try {
        if (!fileStore.files?.length && typeof loadServerFileResources === 'function') {
            await loadServerFileResources({ silent: true });
        }
        const dirs = collectFileLibraryDirectories();
        const options = [{ path: '', name: fileStore.rootDirName || '我的文档库' }, ...dirs]
            .map(dir => `<option value="${escapeHtml(dir.path)}">${escapeHtml(dir.path ? `└ ${dir.path}` : dir.name)}</option>`)
            .join('');
        const overlay = document.createElement('div');
        overlay.className = 'new-item-dialog';
        overlay.id = 'send-file-dialog';
        overlay.innerHTML = `<div class="dialog-box convert-target-dialog">
            <div class="dialog-title">发送到文件目录</div>
            <div class="dialog-target">转换结果将保存为 Markdown，并出现在左侧文件资源树中。</div>
            <label class="convert-target-field">选择目标目录
                <select class="dialog-input" id="send-file-folder">${options}</select>
            </label>
            <label class="convert-target-field">文件名
                <input class="dialog-input" id="send-file-name" value="${escapeHtml((convertedFileName || 'converted') + '.md')}" placeholder="例如：文章摘要.md">
            </label>
            <div class="dialog-actions">
                <button class="dialog-btn" onclick="document.getElementById('send-file-dialog')?.remove()">取消</button>
                <button class="dialog-btn primary" onclick="doSendToFileLibrary()">保存到文件目录</button>
            </div>
        </div>`;
        document.body.appendChild(overlay);
        document.getElementById('send-file-name')?.focus();
    } catch (err) {
        showToast('读取文件目录失败: ' + (err.message || '未知错误'), 'error');
    }
}

async function doSendToFileLibrary() {
    if (!convertedMarkdown) return showToast('没有可保存的转换结果', 'warning');
    const folder = document.getElementById('send-file-folder')?.value || '';
    let name = document.getElementById('send-file-name')?.value.trim() || '';
    if (!name) return showToast('请输入文件名', 'warning');
    name = name.replace(/[\\/:*?"<>|]/g, '-');
    if (!/\.(md|markdown)$/i.test(name)) name += '.md';
    const fullPath = joinResourcePath(folder, name);
    const button = document.querySelector('#send-file-dialog .dialog-btn.primary');
    if (button) { button.disabled = true; button.textContent = '保存中...'; }
    try {
        await createServerFileResource(fullPath, convertedMarkdown, false, { render: false });
        renderFileTree();
        updateStats();
        expandTreePath(folder);
        document.getElementById('send-file-dialog')?.remove();
        showToast(`已保存到文件目录：${fullPath}`, 'success');
    } catch (err) {
        if (button) { button.disabled = false; button.textContent = '保存到文件目录'; }
        showToast('保存到文件目录失败: ' + (err.message || '未知错误'), 'error');
    }
}

async function sendConvertedToKB() {
    if (!convertedMarkdown) {
        showToast('请先转化文件', 'warning');
        return;
    }

    try {
        const data = await api('GET', '/kb/list');
        const bases = data.bases || [];
        if (bases.length === 0) {
            showToast('请先创建知识库', 'warning');
            return;
        }

        const overlay = document.createElement('div');
        overlay.className = 'new-item-dialog';
        overlay.id = 'send-kb-dialog';
        const options = bases.map(b => `<option value="${b.kb_id}">${escapeHtml(b.name)}</option>`).join('');
        overlay.innerHTML = `
            <div class="dialog-box" style="width:400px">
                <div class="dialog-title">发送到知识库</div>
                <div style="display:flex;flex-direction:column;gap:12px">
                    <div>
                        <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px">选择知识库 *</label>
                        <select class="dialog-input" id="send-kb-select" style="width:100%;padding:8px 12px">${options}</select>
                    </div>
                    <div>
                        <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px">分块策略</label>
                        <select class="dialog-input" id="send-kb-strategy" style="width:100%;padding:8px 12px">
                            <option value="">使用知识库默认</option>
                            <option value="general">常规</option>
                            <option value="novel">小说</option>
                            <option value="law">法律</option>
                            <option value="qa">QA问答</option>
                            <option value="book">书籍</option>
                            <option value="academic">学术</option>
                        </select>
                    </div>
                </div>
                <div class="dialog-actions" style="margin-top:16px">
                    <button class="dialog-btn" onclick="document.getElementById('send-kb-dialog').remove()">取消</button>
                    <button class="dialog-btn primary" onclick="doSendToKB()">发送</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
    } catch (err) {
        showToast('获取知识库列表失败: ' + err.message, 'error');
    }
}

async function doSendToKB() {
    const kbId = document.getElementById('send-kb-select')?.value;
    const strategy = document.getElementById('send-kb-strategy')?.value || '';

    if (!kbId) {
        showToast('请选择知识库', 'warning');
        return;
    }

    try {
        showToast('正在发送到知识库...', 'info');
        const blob = new Blob([convertedMarkdown], { type: 'text/markdown' });
        const formData = new FormData();
        formData.append('file', blob, (convertedFileName || 'converted') + '.md');
        formData.append('kb_id', kbId);
        if (strategy) formData.append('chunk_strategy', strategy);

        const data = await api('POST', '/documents/convert-and-send-to-kb', formData, 300000);
        if (data.success) {
            showToast(data.message, 'success');
            document.getElementById('send-kb-dialog')?.remove();
            if (typeof loadKBList === 'function') loadKBList();
        }
    } catch (err) {
        showToast('发送失败: ' + err.message, 'error');
    }
}

async function sendConvertedToKG() {
    if (!convertedMarkdown) {
        showToast('请先转化文件', 'warning');
        return;
    }

    try {
        showToast('正在构建知识图谱...', 'info');
        const blob = new Blob([convertedMarkdown], { type: 'text/markdown' });
        const formData = new FormData();
        formData.append('file', blob, (convertedFileName || 'converted') + '.md');

        const data = await api('POST', '/documents/convert-and-build-kg', formData, 300000);
        if (data.success) {
            showToast(data.message, 'success');
            if (data.doc_key) {
                kgCurrentDocKey = data.doc_key;
            }
            navigateTo('kg');
            if (typeof refreshKGGraph === 'function') {
                await refreshKGGraph(data.doc_key);
            }
        }
    } catch (err) {
        showToast('构建知识图谱失败: ' + err.message, 'error');
    }
}

async function sendConvertedToBoth() {
    if (!convertedMarkdown) {
        showToast('请先转化文件', 'warning');
        return;
    }

    try {
        const data = await api('GET', '/kb/list');
        const bases = data.bases || [];
        if (bases.length === 0) {
            showToast('请先创建知识库', 'warning');
            return;
        }

        const overlay = document.createElement('div');
        overlay.className = 'new-item-dialog';
        overlay.id = 'send-both-dialog';
        const options = bases.map(b => `<option value="${b.kb_id}">${escapeHtml(b.name)}</option>`).join('');
        overlay.innerHTML = `
            <div class="dialog-box" style="width:420px">
                <div class="dialog-title">一键发送到知识库 + 知识图谱</div>
                <div style="display:flex;flex-direction:column;gap:12px">
                    <div>
                        <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px">选择知识库 *</label>
                        <select class="dialog-input" id="send-both-kb-select" style="width:100%;padding:8px 12px">${options}</select>
                    </div>
                    <div>
                        <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:4px">分块策略</label>
                        <select class="dialog-input" id="send-both-strategy" style="width:100%;padding:8px 12px">
                            <option value="">使用知识库默认</option>
                            <option value="general">常规</option>
                            <option value="law">法律</option>
                            <option value="qa">QA问答</option>
                            <option value="book">书籍</option>
                            <option value="academic">学术</option>
                        </select>
                    </div>
                    <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
                        <input type="checkbox" id="send-both-build-kg" checked style="width:16px;height:16px">
                        同时构建知识图谱
                    </label>
                </div>
                <div class="dialog-actions" style="margin-top:16px">
                    <button class="dialog-btn" onclick="document.getElementById('send-both-dialog').remove()">取消</button>
                    <button class="dialog-btn primary" onclick="doSendToBoth()">发送</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
    } catch (err) {
        showToast('获取知识库列表失败: ' + err.message, 'error');
    }
}

async function doSendToBoth() {
    const kbId = document.getElementById('send-both-kb-select')?.value;
    const strategy = document.getElementById('send-both-strategy')?.value || '';
    const buildKg = document.getElementById('send-both-build-kg')?.checked ?? true;

    if (!kbId) {
        showToast('请选择知识库', 'warning');
        return;
    }

    try {
        showToast('正在发送到知识库和图谱...', 'info');
        const blob = new Blob([convertedMarkdown], { type: 'text/markdown' });
        const formData = new FormData();
        formData.append('file', blob, (convertedFileName || 'converted') + '.md');
        formData.append('kb_id', kbId);
        if (strategy) formData.append('chunk_strategy', strategy);
        formData.append('build_kg', buildKg ? 'true' : 'false');

        const data = await api('POST', '/documents/convert-and-send-to-both', formData, 300000);
        if (data.success) {
            showToast(data.message, 'success');
            document.getElementById('send-both-dialog')?.remove();
            if (typeof loadKBList === 'function') loadKBList();
            if (buildKg && data.kg && data.kg.doc_key) {
                kgCurrentDocKey = data.kg.doc_key;
                navigateTo('kg');
                if (typeof refreshKGGraph === 'function') {
                    await refreshKGGraph(data.kg.doc_key);
                }
            }
        }
    } catch (err) {
        showToast('发送失败: ' + err.message, 'error');
    }
}

async function rewriteKGQuery() {
    const input = document.getElementById('kg-chat-input');
    if (!input || !input.value.trim()) {
        showToast('请先输入问题', 'warning');
        return;
    }
    try {
        const data = await api('POST', '/knowledge-graph/rewrite-query', { query: input.value.trim() });
        if (data.rewritten) {
            input.value = data.rewritten;
            showToast('查询已改写', 'success');
        }
    } catch (err) {
        showToast('查询改写失败', 'error');
    }
}

async function expandKGQuery() {
    const input = document.getElementById('kg-chat-input');
    if (!input || !input.value.trim()) {
        showToast('请先输入问题', 'warning');
        return;
    }
    const original = input.value.trim();
    const expanded = `${original} 相关知识 关联概念 详细信息`;
    input.value = expanded;
    showToast('查询已扩展', 'success');
}

async function rewriteRAGQuery() {
    const input = document.getElementById('rag-chat-input');
    if (!input || !input.value.trim()) {
        showToast('请先输入问题', 'warning');
        return;
    }
    try {
        const data = await api('POST', '/rag/rewrite-query', {
            query: input.value.trim(),
            session_id: typeof ragSessionId !== 'undefined' ? ragSessionId : '',
        });
        if (data.rewritten) {
            input.value = data.rewritten;
            showToast('查询已改写', 'success');
        }
    } catch (err) {
        showToast('查询改写失败', 'error');
    }
}

async function expandRAGQuery() {
    const input = document.getElementById('rag-chat-input');
    if (!input || !input.value.trim()) {
        showToast('请先输入问题', 'warning');
        return;
    }
    const original = input.value.trim();
    const expanded = `${original} 相关内容 详细解释 上下文`;
    input.value = expanded;
    showToast('查询已扩展', 'success');
}

function switchKGRightView(view) {
    document.querySelectorAll('#kg-right-panel .view-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`#kg-right-panel .view-tab[data-view="${view}"]`)?.classList.add('active');

    const chatPanel = document.getElementById('kg-chat-panel');
    const infoPanel = document.getElementById('kg-node-info-panel');

    if (view === 'chat') {
        if (chatPanel) chatPanel.style.display = '';
        if (infoPanel) infoPanel.style.display = 'none';
    } else {
        if (chatPanel) chatPanel.style.display = 'none';
        if (infoPanel) infoPanel.style.display = '';
        renderKGHeadingOutline();
    }
}

function navigateTo(page) {
    currentPage = page;
    document.body.classList.toggle('kg-layout-active', page === 'kg');
    document.body.classList.toggle('focused-layout-active', page === 'kg' || page === 'rag');
    document.body.classList.toggle('rag-layout-active', page === 'rag');
    document.body.dataset.currentPage = page;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    const pageEl = document.getElementById(`page-${page}`);
    const tabEl = document.querySelector(`.nav-tab[data-page="${page}"]`);
    if (pageEl) pageEl.classList.add('active');
    if (tabEl) tabEl.classList.add('active');

    if (page === 'kg') {
        if (fileStore.openedFile) {
            kgCurrentDocKey = fileStore.openedFile.path || fileStore.openedFile.name;
        }
        initKGPage();
        requestAnimationFrame(() => {
            if (typeof resizeKGCanvas === 'function') resizeKGCanvas();
            if (typeof refreshKGGraph === 'function') {
                refreshKGGraph().catch(err => console.warn('refresh KG graph after navigation failed:', err));
            }
        });
    }
    if (page === 'rag') initRAGPage();
}

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

const MARKDOWN_BLANK_LINE_TOKEN = 'KH_MARKDOWN_BLANK_LINE_TOKEN';

function repairFlattenedMarkdownSegment(segment) {
    return String(segment || '')
        .replace(/\*\*([^*\n]*?\S)\s+\*\*/g, '**$1**')
        .replace(/\s+---\s+(?=#{1,6}\s+)/g, '\n\n---\n\n')
        .replace(/([^\n])\s+(#{1,6}\s+Q[:：])/g, '$1\n\n$2')
        .replace(/([。；;：:])\s+-\s+(\d+[.、]\s+)/g, '$1\n- $2')
        .replace(/([^\n])\s+-\s+(\d+[.、]\s+\*\*)/g, '$1\n- $2');
}

function repairFlattenedMarkdown(text) {
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    const output = [];
    let inFence = false;
    let fenceChar = '';
    let fenceSize = 0;

    for (const line of lines) {
        const fence = line.match(/^\s{0,3}(`{3,}|~{3,})/);
        if (fence) {
            const marker = fence[1];
            const markerChar = marker[0];
            if (!inFence) {
                inFence = true;
                fenceChar = markerChar;
                fenceSize = marker.length;
            } else if (markerChar === fenceChar && marker.length >= fenceSize) {
                inFence = false;
                fenceChar = '';
                fenceSize = 0;
            }
            output.push(line);
            continue;
        }

        output.push(inFence ? line : repairFlattenedMarkdownSegment(line));
    }

    return output.join('\n');
}

function preserveMarkdownBlankLines(text) {
    const lines = repairFlattenedMarkdown(text).split('\n');
    const output = [];
    let inFence = false;
    let fenceChar = '';
    let fenceSize = 0;

    for (const line of lines) {
        const fence = line.match(/^\s{0,3}(`{3,}|~{3,})/);
        if (fence) {
            const marker = fence[1];
            const markerChar = marker[0];
            if (!inFence) {
                inFence = true;
                fenceChar = markerChar;
                fenceSize = marker.length;
            } else if (markerChar === fenceChar && marker.length >= fenceSize) {
                inFence = false;
                fenceChar = '';
                fenceSize = 0;
            }
            output.push(line);
            continue;
        }

        if (!inFence && line.trim() === '') {
            output.push('');
            output.push(MARKDOWN_BLANK_LINE_TOKEN);
            output.push('');
            continue;
        }
        output.push(line);
    }
    return output.join('\n');
}

function restoreMarkdownBlankLines(html) {
    const tokenPattern = new RegExp(`<p>\\s*${MARKDOWN_BLANK_LINE_TOKEN}\\s*</p>`, 'g');
    return String(html || '')
        .replace(tokenPattern, '<div class="markdown-blank-line" aria-hidden="true"></div>')
        .replace(new RegExp(MARKDOWN_BLANK_LINE_TOKEN, 'g'), '<div class="markdown-blank-line" aria-hidden="true"></div>');
}

function normalizeMarkdownTables(text) {
    return normalizeMarkdownRichBlocks(text).markdown;
}

function stripLatexOuterBraces(value) {
    value = String(value || '').trim();
    if (value.startsWith('{') && value.endsWith('}')) return value.slice(1, -1);
    return value;
}

function readLatexGroup(source, startIndex) {
    const src = String(source || '');
    let index = startIndex;
    while (/\s/.test(src[index] || '')) index += 1;
    if (src[index] !== '{') {
        if (src[index] === '\\') {
            const cmd = src.slice(index).match(/^\\[a-zA-Z]+/);
            if (cmd) return { value: cmd[0], end: index + cmd[0].length };
        }
        return { value: src[index] || '', end: index + 1 };
    }
    let depth = 0;
    let value = '';
    for (let i = index; i < src.length; i += 1) {
        const ch = src[i];
        if (ch === '{') {
            depth += 1;
            if (depth > 1) value += ch;
            continue;
        }
        if (ch === '}') {
            depth -= 1;
            if (depth === 0) return { value, end: i + 1 };
            value += ch;
            continue;
        }
        value += ch;
    }
    return { value: src.slice(index + 1), end: src.length };
}

function renderLatexAtoms(source) {
    const src = String(source || '')
        .replace(/\\left/g, '')
        .replace(/\\right/g, '')
        .replace(/\\,/g, ' ')
        .replace(/\\;/g, ' ')
        .replace(/\\!/g, '')
        .trim();
    let html = '';
    let i = 0;

    const commandMap = {
        times: '×',
        cdot: '·',
        cdots: '⋯',
        ldots: '…',
        top: '⊤',
        infty: '∞',
        leq: '≤',
        geq: '≥',
        neq: '≠',
        approx: '≈',
        alpha: 'α',
        beta: 'β',
        gamma: 'γ',
        delta: 'δ',
        epsilon: 'ε',
        theta: 'θ',
        lambda: 'λ',
        mu: 'μ',
        pi: 'π',
        sigma: 'σ',
        phi: 'φ',
        omega: 'ω',
        softmax: 'softmax',
        exp: 'exp',
        log: 'log',
        sin: 'sin',
        cos: 'cos',
    };

    const appendScript = (tag) => {
        const group = readLatexGroup(src, i + 1);
        html += `<${tag}>${renderLatexAtoms(stripLatexOuterBraces(group.value))}</${tag}>`;
        i = group.end;
    };

    while (i < src.length) {
        const ch = src[i];
        if (ch === '\\') {
            const cmdMatch = src.slice(i + 1).match(/^[a-zA-Z]+/);
            if (cmdMatch) {
                const cmd = cmdMatch[0];
                i += cmd.length + 1;
                if (cmd === 'frac') {
                    const numerator = readLatexGroup(src, i);
                    const denominator = readLatexGroup(src, numerator.end);
                    html += `<span class="math-frac"><span class="math-num">${renderLatexAtoms(numerator.value)}</span><span class="math-den">${renderLatexAtoms(denominator.value)}</span></span>`;
                    i = denominator.end;
                    continue;
                }
                if (cmd === 'sqrt') {
                    const radicand = readLatexGroup(src, i);
                    html += `<span class="math-sqrt"><span class="math-radicand">${renderLatexAtoms(radicand.value)}</span></span>`;
                    i = radicand.end;
                    continue;
                }
                html += `<span class="math-cmd">${escapeHtml(commandMap[cmd] || cmd)}</span>`;
                continue;
            }
            html += escapeHtml(src[i + 1] || '');
            i += 2;
            continue;
        }
        if (ch === '^') {
            appendScript('sup');
            continue;
        }
        if (ch === '_') {
            appendScript('sub');
            continue;
        }
        if (ch === '{' || ch === '}') {
            i += 1;
            continue;
        }
        if (/\s/.test(ch)) {
            html += ' ';
            i += 1;
            continue;
        }
        html += escapeHtml(ch);
        i += 1;
    }
    return html;
}

function renderLatexExpression(source, display = false) {
    const html = renderLatexAtoms(source);
    return display
        ? `<div class="math-display" data-latex="${escapeHtml(source)}">${html}</div>`
        : `<span class="math-inline" data-latex="${escapeHtml(source)}">${html}</span>`;
}

function restoreMarkdownHtmlBlocks(html, blocks) {
    let output = String(html || '');
    (blocks || []).forEach((block, index) => {
        const token = `KH_MARKDOWN_HTML_BLOCK_${index}`;
        const paragraphPattern = new RegExp(`<p>\\s*${token}\\s*</p>`, 'g');
        output = output
            .replace(paragraphPattern, block)
            .replace(new RegExp(token, 'g'), block);
    });
    return output;
}

function normalizeMarkdownRichBlocks(text) {
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    const output = [];
    const htmlBlocks = [];
    let inFence = false;

    const registerHtmlBlock = (html) => {
        const token = `KH_MARKDOWN_HTML_BLOCK_${htmlBlocks.length}`;
        htmlBlocks.push(html);
        return token;
    };
    const pushHtmlBlock = (html) => {
        const token = registerHtmlBlock(html);
        output.push('');
        output.push(token);
        output.push('');
    };
    const convertInlineMath = (line) => String(line || '').replace(/(^|[^\\])\$([^$\n]+?)\$/g, (match, prefix, formula) => {
        if (!formula.trim()) return match;
        return `${prefix}${registerHtmlBlock(renderLatexExpression(formula, false))}`;
    });
    const splitPipeCells = (line) => {
        const normalized = String(line || '').replace(/｜/g, '|').trim();
        if (!normalized.includes('|')) return [];
        const trimmed = normalized.replace(/^\|/, '').replace(/\|$/, '');
        return trimmed.split('|').map(cell => cell.trim());
    };
    const isSeparatorCells = cells => cells.length >= 2 && cells.every(cell => /^:?-{2,}:?$/.test(cell.trim()));
    const getAlignments = cells => cells.map(cell => {
        const value = cell.trim();
        if (value.startsWith(':') && value.endsWith(':')) return 'center';
        if (value.endsWith(':')) return 'right';
        return '';
    });
    const tableLike = line => splitPipeCells(line).length >= 2;
    const inlineCell = value => escapeHtml(String(value || ''))
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/~~([^~]+)~~/g, '<del>$1</del>')
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
    const renderCell = (tag, value, align) => {
        const alignAttr = align ? ` style="text-align:${align}"` : '';
        return `<${tag}${alignAttr}>${inlineCell(value)}</${tag}>`;
    };
    const convertImages = (line) => String(line || '').replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_, alt, src) => {
        const safeSrc = escapeHtml(src);
        const safeAlt = escapeHtml(alt || 'image');
        return `<img src="${safeSrc}" alt="${safeAlt}" loading="lazy">`;
    });

    for (let i = 0; i < lines.length;) {
        const trimmed = lines[i].trim();
        if (/^```/.test(trimmed) || /^~~~/.test(trimmed)) {
            inFence = !inFence;
            output.push(lines[i]);
            i += 1;
            continue;
        }
        if (!inFence && trimmed.startsWith('$$')) {
            let formula = trimmed.replace(/^\$\$/, '');
            let rowIndex = i;
            if (formula.trim().endsWith('$$')) {
                formula = formula.replace(/\$\$$/, '');
            } else {
                rowIndex += 1;
                while (rowIndex < lines.length) {
                    const nextLine = lines[rowIndex];
                    if (nextLine.trim().endsWith('$$')) {
                        formula += `\n${nextLine.replace(/\$\$\s*$/, '')}`;
                        break;
                    }
                    formula += `\n${nextLine}`;
                    rowIndex += 1;
                }
            }
            pushHtmlBlock(renderLatexExpression(formula.trim(), true));
            i = Math.min(rowIndex + 1, lines.length);
            continue;
        }

        if (!inFence && i + 1 < lines.length) {
            const headerCells = splitPipeCells(lines[i]);
            const separatorCells = splitPipeCells(lines[i + 1]);
            if (headerCells.length >= 2 && isSeparatorCells(separatorCells)) {
                const alignments = getAlignments(separatorCells);
                const rows = [];
                let rowIndex = i + 2;
                while (rowIndex < lines.length && tableLike(lines[rowIndex])) {
                    const cells = splitPipeCells(lines[rowIndex]);
                    if (!cells.length) break;
                    rows.push(cells);
                    rowIndex += 1;
                }
                const columnCount = Math.max(headerCells.length, separatorCells.length, ...rows.map(row => row.length));
                const headerHtml = Array.from({ length: columnCount }, (_, col) => renderCell('th', headerCells[col] || '', alignments[col])).join('');
                const bodyHtml = rows.map(row => {
                    const rowHtml = Array.from({ length: columnCount }, (_, col) => renderCell('td', row[col] || '', alignments[col])).join('');
                    return `<tr>${rowHtml}</tr>`;
                }).join('');
                pushHtmlBlock(`<table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`);
                i = rowIndex;
                continue;
            }
        }

        if (!inFence && /!\[[^\]]*\]\([^)]+\)/.test(lines[i])) {
            pushHtmlBlock(convertImages(lines[i]));
            i += 1;
            continue;
        }

        output.push(inFence ? lines[i] : convertInlineMath(lines[i]));
        i += 1;
    }

    return {
        markdown: output.join('\n'),
        htmlBlocks,
    };
}

function renderMarkdown(text) {
    if (!text) return '';
    const richMarkdown = normalizeMarkdownRichBlocks(text);
    const normalizedText = richMarkdown.markdown;
    if (typeof marked !== 'undefined' && marked.parse) {
        try {
            marked.setOptions({
                breaks: true,
                gfm: true,
            });
            const html = restoreMarkdownBlankLines(marked.parse(preserveMarkdownBlankLines(normalizedText)));
            return restoreMarkdownHtmlBlocks(html, richMarkdown.htmlBlocks);
        } catch (e) {
            return restoreMarkdownHtmlBlocks(renderMarkdownFallback(normalizedText), richMarkdown.htmlBlocks);
        }
    }
    return restoreMarkdownHtmlBlocks(renderMarkdownFallback(normalizedText), richMarkdown.htmlBlocks);
}

function renderMarkdownFallback(text) {
    const inline = value => escapeHtml(String(value || ''))
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/~~([^~]+)~~/g, '<del>$1</del>')
        .replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, '<img src="$2" alt="$1" loading="lazy" referrerpolicy="no-referrer">')
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    const output = [];
    let inFence = false;
    let fenceLines = [];
    let listType = '';

    const closeList = () => {
        if (!listType) return;
        output.push(`</${listType}>`);
        listType = '';
    };

    for (const line of lines) {
        const fence = line.match(/^\s*```\s*([^\s]*)/);
        if (fence) {
            closeList();
            if (!inFence) {
                inFence = true;
                fenceLines = [];
            } else {
                output.push(`<pre><code>${escapeHtml(fenceLines.join('\n'))}</code></pre>`);
                inFence = false;
                fenceLines = [];
            }
            continue;
        }
        if (inFence) {
            fenceLines.push(line);
            continue;
        }

        const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
        if (heading) {
            closeList();
            const level = heading[1].length;
            output.push(`<h${level}>${inline(heading[2])}</h${level}>`);
            continue;
        }

        const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
        const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
        if (unordered || ordered) {
            const nextType = unordered ? 'ul' : 'ol';
            if (listType !== nextType) {
                closeList();
                listType = nextType;
                output.push(`<${listType}>`);
            }
            output.push(`<li>${inline((unordered || ordered)[1])}</li>`);
            continue;
        }

        closeList();
        if (!line.trim()) {
            output.push('<div class="markdown-blank-line" aria-hidden="true"></div>');
        } else if (/^\s*>/.test(line)) {
            output.push(`<blockquote>${inline(line.replace(/^\s*>\s?/, ''))}</blockquote>`);
        } else if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
            output.push('<hr>');
        } else {
            output.push(`<p>${inline(line)}</p>`);
        }
    }
    closeList();
    if (inFence) output.push(`<pre><code>${escapeHtml(fenceLines.join('\n'))}</code></pre>`);
    return output.join('');
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function getFileIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    if (ext === 'md' || ext === 'markdown') return 'file-md';
    if (ext === 'txt') return 'file-txt';
    if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'tiff', 'tif'].includes(ext)) return 'file-img';
    if (['pdf'].includes(ext)) return 'file-pdf';
    if (['docx', 'doc'].includes(ext)) return 'file-docx';
    if (['xlsx', 'xls'].includes(ext)) return 'file-xlsx';
    if (['pptx', 'ppt'].includes(ext)) return 'file-pptx';
    if (['csv'].includes(ext)) return 'file-csv';
    if (['json'].includes(ext)) return 'file-json';
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

async function openLocalFolder() {
    if (!requireWriteAccess()) return;
    const isLocalHost = ['localhost', '127.0.0.1', '::1'].includes(location.hostname);
    if (!isLocalHost || !window.showDirectoryPicker) {
        uploadFolderToServer();
        return;
    }
    if (!isLocalHost) {
        showToast('云端版不直接读取本机文件夹，请使用上传文件或新建云端文件夹', 'warning');
        return;
    }
    if (!window.showDirectoryPicker) {
        showToast('浏览器不支持本地文件夹访问，请使用Chrome/Edge', 'error');
        return;
    }
    try {
        const dirHandle = await window.showDirectoryPicker();
        fileStore.rootDirHandle = dirHandle;
        fileStore.rootDirName = dirHandle.name;
        const newFiles = await readDirectory(dirHandle, '');
        const existingItems = fileStore.files.filter(f => {
            if (f.isNew) return true;
            if (!f.handle) return true;
            const inNewFiles = newFiles.some(nf => nf.path === f.path);
            if (!inNewFiles) return true;
            return false;
        });
        fileStore.files = [...newFiles, ...existingItems];
        renderFileTree();
        updateStats();
        showToast(`已打开文件夹: ${dirHandle.name}`, 'success');
    } catch (err) {
        if (err.name !== 'AbortError') {
            showToast('打开文件夹失败: ' + err.message, 'error');
        }
    }
}

async function readDirectory(dirHandle, basePath) {
    const entries = [];
    for await (const [name, handle] of dirHandle.entries()) {
        const fullPath = basePath ? `${basePath}/${name}` : name;
        if (handle.kind === 'directory') {
            const children = await readDirectory(handle, fullPath);
            entries.push({
                name,
                path: fullPath,
                handle,
                isDirectory: true,
                children,
                expanded: false,
            });
        } else {
            entries.push({
                name,
                path: fullPath,
                handle,
                isDirectory: false,
            });
        }
    }
    entries.sort((a, b) => {
        if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
        return a.name.localeCompare(b.name);
    });
    return entries;
}

async function openLocalFile() {
    if (!requireWriteAccess()) return;
    if (!window.showOpenFilePicker) {
        uploadToServer();
        return;
    }
    try {
        const [fileHandle] = await window.showOpenFilePicker({
            multiple: false,
            types: [{
                description: '文档文件',
                accept: {
                    'text/*': ['.md', '.txt', '.csv', '.json', '.html'],
                    'application/pdf': ['.pdf'],
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
                    'image/*': ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp'],
                },
            }],
        });
        const file = await fileHandle.getFile();
        const ext = file.name.split('.').pop().toLowerCase();
        const binaryExts = ['pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt'];
        let content = '';
        let isImage = false;
        let imageDataUrl = null;

        if (isImageFile(file.name)) {
            isImage = true;
            imageDataUrl = await readFileAsDataURL(file);
            content = imageDataUrl;
        } else if (ext === 'pdf') {
            let pdfDataUrl = '';
            let pdfMarkdown = '';
            try {
                pdfMarkdown = await extractPdfText(file);
                pdfDataUrl = await readFileAsDataURL(file);
                content = pdfMarkdown;
            } catch {
                pdfDataUrl = await readFileAsDataURL(file);
                content = pdfDataUrl;
            }
        } else if (binaryExts.includes(ext)) {
            try {
                const formData = new FormData();
                formData.append('file', file);
                const data = await api('POST', '/documents/convert', formData, 300000);
                if (data.success && data.markdown_content) {
                    content = data.markdown_content;
                } else {
                    content = `[无法解析此文件格式: ${file.name}，请确保后端服务已启动]`;
                }
            } catch (err) {
                if (err.name === 'AbortError') {
                    content = `[文件解析超时: ${file.name}，文件可能过大，请尝试较小的文件]`;
                } else {
                    content = `[文件解析失败: ${err.message}]`;
                }
            }
        } else {
            content = await file.text();
        }

        fileStore.openedFile = {
            name: file.name,
            path: file.name,
            content,
            handle: fileHandle,
            isDirty: false,
            isConverted: binaryExts.includes(ext) && ext !== 'pdf',
            isImage: isImage,
            isPdf: ext === 'pdf',
            pdfDataUrl: ext === 'pdf' && typeof pdfDataUrl !== 'undefined' ? pdfDataUrl : '',
        };
        const existing = fileStore.files.find(f => f.path === file.name);
        if (!existing) {
            fileStore.files.push({
                name: file.name,
                path: file.name,
                handle: fileHandle,
                isDirectory: false,
            });
        }
        renderFileTree();
        updateStats();
        uploadSingleFile(file).catch(err => {
            console.warn('sync opened file to server failed:', err);
        });
        if (isImage || ext === 'pdf') {
            _editorPreviewState['kg'] = true;
            _editorPreviewState['rag'] = true;
            updateEditorContent();
        if (currentPage === 'kg') {
            updateEditorContent();
        } else {
            switchRAGView('editor');
        }
        }
        showToast(`已打开文件: ${file.name}`, 'success');
    } catch (err) {
        if (err.name !== 'AbortError') {
            showToast('打开文件失败: ' + err.message, 'error');
        }
    }
}

async function readFileContent(fileEntry) {
    try {
        const file = await fileEntry.handle.getFile();
        const ext = file.name.split('.').pop().toLowerCase();
        const binaryExts = ['pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt'];
        if (isImageFile(file.name)) {
            return await readFileAsDataURL(file);
        }
        if (binaryExts.includes(ext)) {
            if (ext === 'pdf') {
                try {
                    const mdText = await extractPdfText(file);
                    return { markdown: mdText, dataUrl: await readFileAsDataURL(file) };
                } catch (pdfErr) {
                    return await readFileAsDataURL(file);
                }
            }
            try {
                const formData = new FormData();
                formData.append('file', file);
                const data = await api('POST', '/documents/convert', formData, 300000);
                if (data.success && data.markdown_content) {
                    return data.markdown_content;
                }
                return `[无法解析此文件格式: ${file.name}，请确保后端服务已启动]`;
            } catch (err) {
                if (err.name === 'AbortError') {
                    return `[文件解析超时: ${file.name}，文件可能过大]`;
                }
                try {
                    const mdText = await offlineConvertFile(file, ext);
                    if (mdText) return mdText;
                } catch (offlineErr) {
                    console.warn('离线转换也失败:', offlineErr);
                }
                return `[文件解析失败: ${err.message}，请确保后端服务已启动]`;
            }
        }
        return await file.text();
    } catch (err) {
        throw new Error(`无法读取文件: ${err.message}`);
    }
}

async function saveLocalFile() {
    if (!requireWriteAccess()) return;
    if (!fileStore.openedFile) return;
    try {
        if (fileStore.openedFile.source === 'server' && fileStore.openedFile.path) {
            await saveOpenedCloudFile({ silent: false });
        } else if (fileStore.openedFile.handle) {
            const writable = await fileStore.openedFile.handle.createWritable();
            await writable.write(fileStore.openedFile.content);
            await writable.close();
            fileStore.openedFile.isDirty = false;
            showToast('文件已保存', 'success');
        } else {
            const blob = new Blob([fileStore.openedFile.content], { type: 'text/plain;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = fileStore.openedFile.name;
            a.click();
            URL.revokeObjectURL(url);
            showToast('文件已下载', 'success');
        }
    } catch (err) {
        showToast('保存失败: ' + err.message, 'error');
    }
}

async function uploadToServer() {
    if (!requireWriteAccess()) return;
    const input = document.getElementById('upload-input');
    input.onchange = async () => {
        const files = Array.from(input.files);
        await uploadFilesToServer(files);
        input.value = '';
    };
    input.click();
}

async function uploadFolderToServer() {
    if (!requireWriteAccess()) return;
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.webkitdirectory = true;
    input.directory = true;
    input.onchange = async () => {
        const files = Array.from(input.files || []);
        await uploadFilesToServer(files, { keepRelativePath: true });
        input.remove();
    };
    input.click();
}

function scheduleStableKGGraphRefresh(delay = 120) {
    if (currentPage !== 'kg' || typeof refreshKGGraph !== 'function') return;
    if (_kgGraphStableRefreshTimer) clearTimeout(_kgGraphStableRefreshTimer);
    _kgGraphStableRefreshTimer = setTimeout(() => {
        refreshKGGraph().catch(err => console.warn('refresh KG graph failed:', err));
        setTimeout(() => {
            if (typeof resizeKGCanvas === 'function') resizeKGCanvas();
            refreshKGGraph().catch(err => console.warn('refresh KG graph failed:', err));
        }, 650);
    }, delay);
}

async function uploadFilesToServer(files, options = {}) {
    if (!files || !files.length) return;
    const batchId = 'upload-progress-batch-' + Date.now();
    const total = files.length;
    showProgress(batchId, `正在上传 ${total} 个文件...`, 0);
    let ok = 0;
    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const relativePath = options.keepRelativePath ? (file.webkitRelativePath || file.name) : file.name;
        const folder = relativePath.includes('/') ? relativePath.substring(0, relativePath.lastIndexOf('/')) : '';
        showProgress(batchId, `正在处理 ${i + 1}/${total}: ${file.name}`, Math.round((i / total) * 90));
        try {
            await uploadSingleFile(file, folder, { refresh: false, toast: false, progress: false });
            ok++;
        } catch (err) {
            console.warn('upload file failed:', file.name, err);
            showToast(`${file.name} 上传失败: ${err.message || '未知错误'}`, 'error');
        }
    }
    await loadServerFileResources({ silent: true });
    scheduleStableKGGraphRefresh(80);
    showProgress(batchId, `上传完成 ${ok}/${total}`, 100);
    setTimeout(() => hideProgress(batchId), 1200);
    showToast(options.keepRelativePath ? `文件夹上传完成：${ok}/${total} 个文件` : `文件上传完成：${ok}/${total} 个文件`, ok ? 'success' : 'error');
}

async function uploadSingleFile(file, folder = '', options = {}) {
    if (!requireWriteAccess()) return null;
    const progressId = 'upload-progress-' + Date.now();
    const showOwnProgress = options.progress !== false;
    if (showOwnProgress) showProgress(progressId, `${file.name} 正在上传...`, 0);
    try {
        const formData = new FormData();
        formData.append('file', file);
        if (folder) formData.append('folder', folder);

        if (showOwnProgress) showProgress(progressId, `${file.name} 正在保存到云端文件库...`, 45);

        const data = await api('POST', '/file-resources/upload', formData, 300000);
        if (!data || data.success !== true) {
            throw new Error(data?.message || '服务器未确认文件已保存');
        }

        // The upload itself has succeeded at this point. Keep UI refreshes
        // best-effort so a stale/large file tree cannot turn a successful
        // server write into a misleading “上传失败” toast.
        try {
            if (options.refresh !== false && data.files) {
                fileStore.rootDirName = '我的文档库';
                fileStore.rootDirHandle = null;
                fileStore.files = data.files;
                renderFileTree();
                scheduleStableKGGraphRefresh(80);
            } else if (options.refresh !== false) {
                await loadServerFileResources({ silent: true });
                scheduleStableKGGraphRefresh(80);
            }
        } catch (refreshErr) {
            console.warn('上传后刷新文件树失败，文件已保存:', refreshErr);
        }

        if (showOwnProgress) {
            showProgress(progressId, `${file.name} 已保存`, 100);
            setTimeout(() => hideProgress(progressId), 1500);
        }

        if (options.toast !== false) showToast(`${file.name} 已保存到文件资源库`, 'success');
        try { updateStats(); } catch (statsErr) { console.warn('上传后更新统计失败:', statsErr); }
        return data;
    } catch (err) {
        if (showOwnProgress) hideProgress(progressId);
        const ext = file.name.split('.').pop().toLowerCase();
        if (ext === 'pdf' && _backendOnline === false) {
            try {
                const mdText = await extractPdfText(file);
                showToast(`${file.name} 已通过本地解析提取文本（后端离线，向量嵌入将在本地生成）`, 'info');
                if (fileStore.openedFile) {
                    fileStore.openedFile.content = mdText;
                    updateEditorContent();
                }
            } catch (pdfErr) {
                showToast(`${file.name} 上传失败: ${pdfErr.message}`, 'error');
            }
        } else {
            if (options.toast !== false) showToast(`${file.name} 上传失败: ${err.message || '未知错误'}`, 'error');
            throw err;
        }
    }
}

async function uploadAndBuildKG(file) {
    const progressId = 'upload-progress-' + Date.now();
    showProgress(progressId, `${file.name} 正在上传...`, 0);
    try {
        const formData = new FormData();
        formData.append('file', file);

        showProgress(progressId, `${file.name} 正在解析并构建图谱...`, 30);

        const data = await api('POST', '/documents/upload-and-build-kg', formData);

        showProgress(progressId, `${file.name} 图谱构建完成`, 100);
        setTimeout(() => hideProgress(progressId), 1500);

        showToast(`${file.name} 已处理并构建图谱`, 'success');
        if (currentPage === 'kg') await refreshKGGraph();
        updateStats();
    } catch (err) {
        hideProgress(progressId);
        showToast(`${file.name} 构建图谱失败: ${err.message || '未知错误'}`, 'error');
    }
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

function renderFileTree() {
    const tree = document.getElementById('file-tree');
    if (!fileStore.files.length) {
        tree.innerHTML = `
            <div class="tree-empty">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                <p>点击上方按钮打开文件夹</p>
                <p class="hint">或直接打开单个文件</p>
            </div>`;
        return;
    }

    let html = '';
    if (fileStore.rootDirName) {
        html += `<div class="tree-item" style="--depth:0;font-weight:600" onclick="toggleRootDir()">
            <span class="icon folder">${getFileIconSvg('folder')}</span>
            <span class="name">${escapeHtml(fileStore.rootDirName)}</span>
        </div>`;
    }

    html += renderTreeItems(fileStore.files, fileStore.rootDirName ? 1 : 0);
    tree.innerHTML = html;

    tree.ondragover = function(e) {
        if (_dragSourcePaths.length && e.target === tree) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            tree.classList.add('drag-over-root');
        }
    };
    tree.ondragleave = function(e) {
        if (e.target === tree) {
            tree.classList.remove('drag-over-root');
        }
    };
    tree.ondrop = function(e) {
        tree.classList.remove('drag-over-root');
        if (_dragSourcePaths.length && e.target === tree) {
            e.preventDefault();
            const affectedParents = new Set(['']);
            _dragSourcePaths.forEach(path => affectedParents.add(getParentPath(path) || ''));
            const moveRefs = _dragSourcePaths
                .map(path => ({ source_path: path, entry: findFileEntry(fileStore.files, path) }))
                .filter(item => item.entry && item.entry.source === 'server');
            const moved = moveFileEntriesToRoot(_dragSourcePaths);
            if (moved) {
                const serverOps = moveRefs.map(item => ({
                    source_path: item.source_path,
                    target_path: item.entry.path,
                }));
                _fileTreeSelectedPaths.clear();
                renderFileTree();
                syncServerFileTreeChanges(serverOps, Array.from(affectedParents));
                showToast(`已移动 ${_dragSourcePaths.length} 项到根目录`, 'success');
            }
            _dragSourcePath = null;
            _dragSourcePaths = [];
        }
    };

    // 文件树变更后自动保存
    if (typeof AutoSave !== 'undefined') {
        AutoSave.debounceSave('fileTree', () => AutoSave.saveFileState(), 500);
    }
}

function captureFileTreeExpandedState() {
    document.querySelectorAll('.dir-children[data-path]').forEach(el => {
        const path = el.dataset.path;
        if (!path) return;
        if (el.style.display !== 'none') _fileTreeExpandedPaths.add(path);
        else _fileTreeExpandedPaths.delete(path);
    });
}

// ========== 文件删除与右键菜单 ==========

// 从文件树中删除文件/文件夹
async function deleteFileFromTree(path) {
    if (!requireWriteAccess()) return;
    const entry = findFileEntry(fileStore.files, path);
    if (!entry) return;
    const name = entry.name || path;
    const itemLabel = entry.isDirectory ? '文件夹' : '文件';
    if (entry.source === 'server') {
        if (!confirm(`确定要删除${itemLabel} "${name}" 吗？${entry.isDirectory ? '\n该文件夹下的所有内容也会一起删除。' : ''}`)) return;
        try {
            const data = await deleteServerFileResource(path);
            if (typeof window.removeRAGComposerResourcePath === 'function') window.removeRAGComposerResourcePath(path);
            pruneTreeStatePaths(path);
            fileStore.files = data.files || [];
            // Persist the authoritative server response immediately. If a
            // subsequent refresh briefly fails, the deleted item must not be
            // restored from the previous local tree snapshot.
            cacheServerFileTree(fileStore.rootDirName || '我的文档库', fileStore.files);
            if (fileStore.selectedFile === path || isDescendantOrSelf(path, fileStore.selectedFile?.path || '')) {
                fileStore.selectedFile = null;
            }
            if (isDescendantOrSelf(path, fileStore.openedFile?.path || '')) {
                fileStore.openedFile = null;
            }
            renderFileTree();
            updateSidebarStats();
            updateEditorContent();
            showToast(`已删除${itemLabel}: ${name}`, 'success');
            return;
        } catch (err) {
            showToast(`删除${itemLabel}失败: ` + (err.message || '未知错误'), 'error');
            return;
        }
    }
    if (!confirm(`确定要删除${itemLabel} "${name}" 吗？\n此操作仅从列表中移除，不会删除磁盘上的文件。`)) return;

    const parentPath = path.includes('/') ? path.substring(0, path.lastIndexOf('/')) : null;
    if (removeEntryFromParent(fileStore.files, path, parentPath)) {
        if (typeof window.removeRAGComposerResourcePath === 'function') window.removeRAGComposerResourcePath(path);
        pruneTreeStatePaths(path);
        // 如果删除的是当前选中的文件，清空编辑器
        if (fileStore.selectedFile === path || isDescendantOrSelf(path, fileStore.selectedFile?.path || '')) {
            fileStore.selectedFile = null;
        }
        if (isDescendantOrSelf(path, fileStore.openedFile?.path || '')) {
            fileStore.openedFile = null;
            const editor = document.getElementById('editor');
            if (editor) editor.value = '';
        }
        renderFileTree();
        updateSidebarStats();
        updateEditorContent();
        showToast(`已移除 "${name}"`, 'success');
    }
}

// 清空所有文件列表
function clearAllFiles() {
    if (!requireWriteAccess()) return;
    if (!fileStore.files.length) {
        showToast('文件列表已为空', 'info');
        return;
    }
    if (!confirm(`确定要清空所有文件吗？\n共 ${fileStore.files.length} 个项目，此操作仅从列表中移除。`)) return;
    fileStore.files = [];
    fileStore.rootDirName = null;
    fileStore.rootDirHandle = null;
    fileStore.selectedFile = null;
    fileStore.openedFile = null;
    const editor = document.getElementById('editor');
    if (editor) editor.value = '';
    renderFileTree();
    updateSidebarStats();
    updateEditorContent();
    showToast('已清空文件列表', 'success');
}

// 右键上下文菜单
let _contextMenu = null;

function showFileContextMenu(e, path, isDir) {
    if (!requireWriteAccess()) return;
    e.preventDefault();
    e.stopPropagation();
    hideFileContextMenu();

    const entry = findFileEntry(fileStore.files, path);
    const name = entry ? entry.name : path;

    const menu = document.createElement('div');
    menu.id = 'file-context-menu';
    menu.style.cssText = 'position:fixed;z-index:99999;background:var(--bg-elevated);border:1px solid var(--border);border-radius:8px;box-shadow:var(--shadow-lg);padding:4px;min-width:160px;font-size:13px;';

    const items = [
        { label: '重命名', icon: '✎', action: () => renameFileTreeItem(path) },
        { label: isDir ? '删除文件夹' : '删除', icon: '🗑️', action: () => deleteFileFromTree(path), danger: true },
    ];

    if (isDir) {
        items.splice(1, 0, { label: '在此新建文件', icon: '+', action: () => { _fileTreeSelectedPaths.clear(); _fileTreeSelectedPaths.add(path); fileStore.selectedFile = entry; createNewFile(); } });
        items.splice(2, 0, { label: '在此新建文件夹', icon: '+', action: () => { _fileTreeSelectedPaths.clear(); _fileTreeSelectedPaths.add(path); fileStore.selectedFile = entry; createNewFolder(); } });
    } else {
        const ext = name.split('.').pop().toLowerCase();
        const convertibleExts = ['pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'csv', 'html', 'htm', 'png', 'jpg', 'jpeg', 'bmp', 'tiff', 'tif', 'gif', 'webp', 'svg', 'json', 'txt'];
        if (convertibleExts.includes(ext)) {
            items.splice(0, 0, { label: '转为 Markdown', icon: '📄', action: () => convertFileInTree(path) });
        }
        items.splice(0, 0, { label: '构建图谱', icon: '⚡', action: () => buildKGFromFile(path) });
        items.splice(0, 0, { label: '拆解三元组', icon: '🔗', action: () => extractTriplesFromFile(path) });
    }

    items.forEach(item => {
        const btn = document.createElement('div');
        btn.style.cssText = `padding:6px 12px;cursor:pointer;border-radius:4px;display:flex;align-items:center;gap:8px;color:${item.danger ? 'var(--danger)' : 'var(--text-primary)'};`;
        btn.textContent = `${item.icon} ${item.label}`;
        btn.onmouseenter = () => btn.style.background = 'var(--bg-hover)';
        btn.onmouseleave = () => btn.style.background = 'transparent';
        btn.onclick = () => { hideFileContextMenu(); item.action(); };
        menu.appendChild(btn);
    });

    // 定位菜单
    const x = Math.min(e.clientX, window.innerWidth - 180);
    const y = Math.min(e.clientY, window.innerHeight - menu.children.length * 36 - 20);
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';

    document.body.appendChild(menu);
    _contextMenu = menu;

    // 点击其他地方关闭
    setTimeout(() => {
        document.addEventListener('click', hideFileContextMenu, { once: true });
    }, 10);
}

function hideFileContextMenu() {
    if (_contextMenu) {
        _contextMenu.remove();
        _contextMenu = null;
    }
}

let _renameTargetPath = '';

function applyLocalRenameEntry(entry, oldPath, newPath, newName, parentPath) {
    remapTreeStatePaths(oldPath, newPath);
    entry.name = newName;
    updateEntryPath(entry, newPath);
    fileStore.selectedFile = entry;
    if (fileStore.openedFile && (fileStore.openedFile.path === oldPath || fileStore.openedFile.path === newPath) && !entry.isDirectory) {
        fileStore.openedFile.name = newName;
        fileStore.openedFile.path = newPath;
    }
    _fileTreeSelectedPaths.clear();
    _fileTreeSelectedPaths.add(newPath);
    if (entry.isDirectory) _fileTreeExpandedPaths.add(newPath);
    renderFileTree();
    expandTreePath(parentPath || newPath);
    updateEditorContent();
}

function renameFileTreeItem(path) {
    if (!requireWriteAccess()) return;
    const entry = findFileEntry(fileStore.files, path);
    if (!entry) return;
    _renameTargetPath = path;
    const dialog = document.createElement('div');
    dialog.className = 'new-item-dialog';
    dialog.id = 'rename-item-dialog';
    const parentPath = getParentPath(path);
    dialog.innerHTML = `
        <div class="dialog-box">
            <div class="dialog-title">重命名${entry.isDirectory ? '文件夹' : '文件'}</div>
            <div class="dialog-target">当前位置：${escapeHtml(parentPath || (fileStore.rootDirName || '我的文档库'))}</div>
            <input class="dialog-input" id="rename-item-name" value="${escapeHtml(entry.name)}" autofocus>
            <div class="dialog-actions">
                <button class="dialog-btn" onclick="closeRenameDialog()">取消</button>
                <button class="dialog-btn primary" onclick="confirmRenameItem()">保存</button>
            </div>
        </div>`;
    document.body.appendChild(dialog);
    const input = document.getElementById('rename-item-name');
    input.focus();
    input.select();
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') confirmRenameItem();
        if (e.key === 'Escape') closeRenameDialog();
    });
}

function closeRenameDialog() {
    const dialog = document.getElementById('rename-item-dialog');
    if (dialog) dialog.remove();
    _renameTargetPath = '';
}

async function confirmRenameItem() {
    if (!requireWriteAccess()) return;
    const entry = findFileEntry(fileStore.files, _renameTargetPath);
    const input = document.getElementById('rename-item-name');
    if (!entry || !input) return;
    let newName = input.value.trim();
    if (!newName) {
        showToast('请输入新名称', 'warning');
        return;
    }
    if (/[\\/]/.test(newName)) {
        showToast('名称里不能包含斜杠，需要移动请直接拖拽', 'warning');
        return;
    }
    if (!entry.isDirectory && !newName.includes('.') && entry.name.includes('.')) {
        newName += '.' + entry.name.split('.').pop();
    }
    if (newName === entry.name) {
        closeRenameDialog();
        return;
    }

    const oldPath = entry.path;
    const parentPath = getParentPath(oldPath);
    const newPath = parentPath ? `${parentPath}/${newName}` : newName;

    try {
        if (entry.source === 'server') {
            await moveServerFileResources([{ source_path: oldPath, target_path: newPath }]);
            applyLocalRenameEntry(entry, oldPath, newPath, newName, parentPath);
            reorderServerFileResources(parentPath || '', getOrderedPathsForParent(parentPath))
                .catch(err => console.warn('rename order sync failed:', err));
        } else {
            applyLocalRenameEntry(entry, oldPath, newPath, newName, parentPath);
        }
        closeRenameDialog();
        showToast(`已重命名为: ${newName}`, 'success');
    } catch (err) {
        if (entry.source === 'server') {
            applyLocalRenameEntry(entry, oldPath, newPath, newName, parentPath);
            enqueuePendingCloudMove(oldPath, newPath);
            markBackendOnline(false);
            closeRenameDialog();
            showToast(`已本地重命名为 ${newName}，云端恢复后自动同步`, 'warning');
            return;
        }
        showToast('重命名失败: ' + (err.message || '未知错误'), 'error');
    }
}

function updateSidebarStats() {
    updateStats();
}

function renderTreeItems(items, depth) {
    return items.map(item => {
        if (item.isDirectory) {
            const childCount = item.children ? item.children.length : 0;
            const selectedClass = _fileTreeSelectedPaths.has(item.path) ? ' selected' : '';
            const isExpanded = _fileTreeExpandedPaths.has(item.path) || item._expandedAfterDrop || item.expanded;
            return `
                <div class="tree-item tree-dir-item${selectedClass}" style="--depth:${depth}" data-path="${escapeHtml(item.path)}" data-dir="true" draggable="true"
                    onclick="handleTreeItemClick(event, '${escapeHtml(item.path)}', true, this)"
                    oncontextmenu="showFileContextMenu(event, '${escapeHtml(item.path)}', true)"
                    ondragstart="handleTreeDragStart(event)"
                    ondragover="handleTreeDragOver(event)"
                    ondragleave="handleTreeDragLeave(event)"
                    ondrop="handleTreeDrop(event)"
                    ondragend="handleTreeDragEnd(event)">
                    <span class="icon folder">${getFileIconSvg('folder')}</span>
                    <span class="name">${escapeHtml(item.name)}</span>
                    <span class="badge">${childCount}</span>
                    <div class="tree-file-actions">
                        <button class="tree-action-btn" onclick="event.stopPropagation(); renameFileTreeItem('${escapeHtml(item.path)}')" title="重命名">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
                        </button>
                        <button class="tree-action-btn tree-action-delete" onclick="event.stopPropagation(); deleteFileFromTree('${escapeHtml(item.path)}')" title="删除">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                        </button>
                    </div>
                </div>
                <div class="dir-children" data-path="${escapeHtml(item.path)}" style="display:${isExpanded ? 'block' : 'none'}">
                    ${item.children ? renderTreeItems(item.children, depth + 1) : ''}
                </div>`;
        }
        const iconType = getFileIcon(item.name);
        const selectedClass = _fileTreeSelectedPaths.has(item.path) ? ' selected' : '';
        return `
            <div class="tree-item tree-file-item${selectedClass}" style="--depth:${depth}" data-path="${escapeHtml(item.path)}" draggable="true"
                onclick="handleTreeItemClick(event, '${escapeHtml(item.path)}', false, this)"
                oncontextmenu="showFileContextMenu(event, '${escapeHtml(item.path)}', false)"
                ondragstart="handleTreeDragStart(event)"
                ondragover="handleTreeDragOver(event)"
                ondragleave="handleTreeDragLeave(event)"
                ondrop="handleTreeDrop(event)"
                ondragend="handleTreeDragEnd(event)">
                <span class="icon ${iconType}">${getFileIconSvg(iconType)}</span>
                <span class="name">${escapeHtml(item.name)}</span>
                <div class="tree-file-actions">
                    <button class="tree-action-btn" onclick="event.stopPropagation(); renameFileTreeItem('${escapeHtml(item.path)}')" title="重命名">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
                    </button>
                    <button class="tree-action-btn tree-action-delete" onclick="event.stopPropagation(); deleteFileFromTree('${escapeHtml(item.path)}')" title="删除">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
            </div>`;
    }).join('');
}

function toggleDir(el, path) {
    const children = document.querySelector(`.dir-children[data-path="${path}"]`);
    if (children) {
        const visible = children.style.display !== 'none';
        children.style.display = visible ? 'none' : 'block';
        if (visible) _fileTreeExpandedPaths.delete(path);
        else _fileTreeExpandedPaths.add(path);
        el.style.opacity = visible ? '1' : '1';
    }
}

function toggleRootDir() {
    const items = document.querySelectorAll('.tree-item:not([style*="--depth:0"])');
    const firstItem = items[0];
    if (firstItem) {
        const visible = firstItem.style.display !== 'none';
        items.forEach(el => el.style.display = visible ? 'none' : '');
        document.querySelectorAll('.dir-children').forEach(el => el.style.display = 'none');
        if (visible) _fileTreeExpandedPaths.clear();
    }
}

function handleTreeItemClick(event, path, isDir, el) {
    event.stopPropagation();
    const multiSelect = event.ctrlKey || event.metaKey;
    const entry = findFileEntry(fileStore.files, path);

    if (multiSelect) {
        if (_fileTreeSelectedPaths.has(path)) {
            _fileTreeSelectedPaths.delete(path);
        } else {
            _fileTreeSelectedPaths.add(path);
        }
        if (isDir && entry?.isDirectory) {
            fileStore.lastCreateParentPath = entry.path;
            fileStore.selectedFile = entry;
        }
        syncFileTreeSelectionClasses();
        return;
    }

    _fileTreeSelectedPaths.clear();
    _fileTreeSelectedPaths.add(path);
    syncFileTreeSelectionClasses();
    fileStore.selectedFile = entry;

    if (isDir) {
        if (entry?.isDirectory) fileStore.lastCreateParentPath = entry.path;
        toggleDir(el, path);
    } else {
        fileStore.lastCreateParentPath = entry?.path && entry.path.includes('/')
            ? entry.path.substring(0, entry.path.lastIndexOf('/'))
            : '';
        selectFile(path);
    }
}

function syncFileTreeSelectionClasses() {
    document.querySelectorAll('.tree-item[data-path]').forEach(el => {
        const path = el.dataset.path;
        el.classList.toggle('selected', _fileTreeSelectedPaths.has(path));
    });
}

async function selectFile(path, options = {}) {
    const requestId = ++kgFileSelectRequestId;
    document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
    const selectorPath = String(path || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
    const el = document.querySelector(`.tree-item[data-path="${selectorPath}"]`);
    if (el) el.classList.add('active');

    const fileEntry = findFileEntry(fileStore.files, path);
    if (!fileEntry || fileEntry.isDirectory) return;

    fileStore.selectedFile = fileEntry;
    showFileOpenLoading(fileEntry);

    try {
        let content = '';
        let pdfDataUrl = '';
        let refreshServerContent = false;
        const ext = fileEntry.name.split('.').pop().toLowerCase();
        const isPdf = ext === 'pdf';
        const isImg = isImageFile(fileEntry.name);
        const isSameOpenedFile = fileStore.openedFile
            && (fileStore.openedFile.path === fileEntry.path || fileStore.openedFile.name === fileEntry.name);

        if (isPdf) {
            if (isSameOpenedFile && fileStore.openedFile.pdfDataUrl) {
                pdfDataUrl = fileStore.openedFile.pdfDataUrl;
            } else if (fileEntry.handle) {
                const localPdf = await fileEntry.handle.getFile();
                if (requestId !== kgFileSelectRequestId) return;
                pdfDataUrl = URL.createObjectURL(localPdf);
            } else if (fileEntry.source === 'server') {
                pdfDataUrl = getFastServerPdfPreviewUrl(fileEntry.path);
            } else if (typeof fileEntry.content === 'string' && fileEntry.content.startsWith('data:')) {
                pdfDataUrl = fileEntry.content;
            }
        } else if (isImg) {
            if (isSameOpenedFile && fileStore.openedFile.content) {
                content = fileStore.openedFile.content;
            } else if (fileEntry.handle) {
                const localImage = await fileEntry.handle.getFile();
                if (requestId !== kgFileSelectRequestId) return;
                content = await readFileAsDataURL(localImage);
                if (requestId !== kgFileSelectRequestId) return;
            } else if (fileEntry.source === 'server') {
                content = getServerFileResourceRawUrl(fileEntry.path);
            } else if (typeof fileEntry.content === 'string' && /^(data:|blob:|https?:\/\/|\/)/i.test(fileEntry.content)) {
                content = fileEntry.content;
            }
        } else if (fileEntry.handle) {
            content = await readFileContent(fileEntry);
            if (requestId !== kgFileSelectRequestId) return;
        } else if (typeof fileEntry.content === 'string') {
            content = fileEntry.content;
        } else if (isSameOpenedFile) {
            content = fileStore.openedFile.content || '';
        } else if (fileEntry.source === 'server') {
            let cachedFile = null;
            if (typeof LocalDB !== 'undefined') {
                cachedFile = await LocalDB.getFile(fileEntry.path || fileEntry.name).catch(() => null);
            }
            if (requestId !== kgFileSelectRequestId) return;
            if (cachedFile && typeof cachedFile.content === 'string' && cachedFile.content.length > 0) {
                content = cachedFile.content;
                fileEntry.content = content;
                refreshServerContent = true;
            } else {
                const data = await readServerFileResource(fileEntry.path);
                if (requestId !== kgFileSelectRequestId) return;
                content = data.content || '';
                fileEntry.content = content;
            }
        } else if (typeof LocalDB !== 'undefined') {
            const savedFile = await LocalDB.getFile(fileEntry.path || fileEntry.name);
            if (requestId !== kgFileSelectRequestId) return;
            if (savedFile && typeof savedFile.content === 'string') {
                content = savedFile.content;
                fileEntry.content = content;
            }
        } else if (fileEntry.isNew) {
            content = '';
        }

        const binaryExts = ['pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt'];
        const needsReload = !isPdf && !content && !fileEntry.handle && !fileEntry.isNew;
        if (needsReload) {
            content = '本地文件内容未缓存。请重新选择左侧文件所在的文件夹后，再点击此 Markdown 文件查看内容。';
        }

        if (isPdf && content && typeof content === 'object' && content.markdown) {
            pdfDataUrl = content.dataUrl;
            content = '';
        } else if (isPdf && typeof content === 'string' && content.startsWith('data:')) {
            pdfDataUrl = content;
            content = '';
        }

        if (fileStore.openedFile?.pdfObjectUrl && fileStore.openedFile.pdfObjectUrl !== pdfDataUrl) {
            URL.revokeObjectURL(fileStore.openedFile.pdfObjectUrl);
        }

        fileStore.openedFile = {
            name: fileEntry.name,
            path: fileEntry.path,
            content,
            handle: fileEntry.handle,
            isDirty: false,
            isConverted: binaryExts.includes(ext) && !isPdf,
            isImage: isImg,
            isPdf: isPdf,
            pdfDataUrl: pdfDataUrl,
            pdfObjectUrl: pdfDataUrl.startsWith('blob:') ? pdfDataUrl : '',
            needsReload,
            source: fileEntry.source,
        };

        if (isEditableCloudFile(fileStore.openedFile)) {
            const draft = loadCloudDraft(fileEntry.path);
            if (draft && typeof draft.content === 'string' && draft.content !== content) {
                fileStore.openedFile.content = draft.content;
                fileStore.openedFile.isDirty = true;
                content = draft.content;
                setCloudSaveStatus('error', '本地草稿，待同步');
                showToast('已恢复未同步的本地草稿，服务器恢复后请点击保存', 'warning');
            }
        }

        if (typeof LocalDB !== 'undefined' && typeof content === 'string' && content.trim() && !isImg) {
            LocalDB.saveFile({
                path: fileEntry.path || fileEntry.name,
                name: fileEntry.name,
                content,
                isDirectory: false,
                isDirty: false,
            }).catch(err => console.warn('保存文件缓存失败:', err));
        }

        if (typeof kgCurrentDocKey !== 'undefined') {
            kgCurrentDocKey = fileEntry.path || fileEntry.name;
        }

        if (isImg || isPdf) {
            _editorPreviewState['kg'] = true;
            _editorPreviewState['rag'] = true;
        }
        const viewableExts = ['md', 'markdown', 'txt', 'csv', 'json', 'html', 'htm', 'pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'];
        if (viewableExts.includes(ext) && currentPage !== 'kg') {
            switchRAGView('editor');
        } else {
            updateEditorContent({
                refreshGraph: options.refreshGraph !== false,
                reuseGraph: true,
            });
        }
        if (refreshServerContent) {
            refreshOpenedServerFileInBackground(fileEntry, content, requestId);
        }
    } catch (err) {
        if (requestId !== kgFileSelectRequestId) return;
        if (fileEntry.source === 'server' && /404|file not found|not found|不存在/i.test(String(err.message || ''))) {
            await loadServerFileResources({ silent: true });
        }
        showToast('读取文件失败: ' + err.message, 'error');
    }
}

async function refreshOpenedServerFileInBackground(fileEntry, cachedContent, requestId) {
    try {
        const data = await readServerFileResource(fileEntry.path);
        const freshContent = data.content || '';
        if (requestId !== kgFileSelectRequestId) return;
        if (!fileStore.openedFile || fileStore.openedFile.path !== fileEntry.path || fileStore.openedFile.isDirty) return;
        if (freshContent === cachedContent) return;
        fileEntry.content = freshContent;
        fileStore.openedFile.content = freshContent;
        if (typeof LocalDB !== 'undefined') {
            LocalDB.saveFile({
                path: fileEntry.path || fileEntry.name,
                name: fileEntry.name,
                content: freshContent,
                isDirectory: false,
                isDirty: false,
            }).catch(() => {});
        }
        updateEditorContent({ refreshGraph: false, reuseGraph: true });
        scheduleKGReaderDerivedRefresh({ outlineDelay: 120, graphDelay: 600, refreshGraph: currentPage === 'kg' });
    } catch (err) {
        console.warn('background file refresh failed:', fileEntry.path, err);
    }
}

function showFileOpenLoading(fileEntry) {
    const reader = document.getElementById('kg-md-reader');
    if (!reader || currentPage !== 'kg') return;
    reader.classList.remove('kg-reader-pdf-mode');
    reader.innerHTML = `
        <div class="kg-reader-loading" role="status" aria-live="polite">
            <span class="kg-reader-loading-spinner"></span>
            <strong>正在打开 ${escapeHtml(fileEntry?.name || '文件')}</strong>
            <span>正在从云端读取内容...</span>
        </div>`;
}

function findFileEntry(files, path) {
    for (const f of files) {
        if (f.path === path) return f;
        if (f.children) {
            const found = findFileEntry(f.children, path);
            if (found) return found;
        }
    }
    return null;
}

function remapPathPrefix(path, oldPrefix, newPrefix) {
    if (!path) return path;
    if (path === oldPrefix) return newPrefix;
    if (path.startsWith(oldPrefix + '/')) return newPrefix + path.slice(oldPrefix.length);
    return path;
}

function remapTreeStatePaths(oldPrefix, newPrefix) {
    for (const path of Array.from(_fileTreeExpandedPaths)) {
        const nextPath = remapPathPrefix(path, oldPrefix, newPrefix);
        if (nextPath !== path) {
            _fileTreeExpandedPaths.delete(path);
            _fileTreeExpandedPaths.add(nextPath);
        }
    }
    for (const path of Array.from(_fileTreeSelectedPaths)) {
        const nextPath = remapPathPrefix(path, oldPrefix, newPrefix);
        if (nextPath !== path) {
            _fileTreeSelectedPaths.delete(path);
            _fileTreeSelectedPaths.add(nextPath);
        }
    }
    if (fileStore.openedFile) {
        fileStore.openedFile.path = remapPathPrefix(fileStore.openedFile.path, oldPrefix, newPrefix);
    }
    if (fileStore.selectedFile) {
        fileStore.selectedFile.path = remapPathPrefix(fileStore.selectedFile.path, oldPrefix, newPrefix);
    }
}

function pruneTreeStatePaths(prefix) {
    for (const path of Array.from(_fileTreeExpandedPaths)) {
        if (isDescendantOrSelf(prefix, path)) _fileTreeExpandedPaths.delete(path);
    }
    for (const path of Array.from(_fileTreeSelectedPaths)) {
        if (isDescendantOrSelf(prefix, path)) _fileTreeSelectedPaths.delete(path);
    }
}

let _dragSourcePath = null;
let _dragSourcePaths = [];
let _dragExpandTimer = null;
const _fileTreeSelectedPaths = new Set();
const _fileTreeExpandedPaths = new Set();

function handleTreeDragStart(e, path) {
    path = path || e.currentTarget?.dataset?.path || '';
    if (!path) {
        e.preventDefault();
        return;
    }
    _dragSourcePath = path;
    if (!_fileTreeSelectedPaths.has(path)) {
        _fileTreeSelectedPaths.clear();
        _fileTreeSelectedPaths.add(path);
        syncFileTreeSelectionClasses();
    }
    _dragSourcePaths = normalizeDraggedPaths(Array.from(_fileTreeSelectedPaths));
    _kgActiveDraggedFilePaths = _dragSourcePaths.slice();
    e.dataTransfer.effectAllowed = (typeof hasWriteAccess === 'function' && hasWriteAccess()) ? 'copyMove' : 'copy';
    e.dataTransfer.setData('application/x-knowledge-hub-file-paths', JSON.stringify(_dragSourcePaths));
    e.dataTransfer.setData('application/x-knowledge-file-paths', JSON.stringify(_dragSourcePaths));
    e.dataTransfer.setData('application/x-knowledge-file', JSON.stringify(_dragSourcePaths[0] || ''));
    e.dataTransfer.setData('text/plain', _dragSourcePaths.join('\n'));
    e.dataTransfer.setData('text/uri-list', _dragSourcePaths.join('\n'));
    requestAnimationFrame(() => {
        e.target.classList.add('dragging');
    });
}

function handleTreeDragOver(e, targetPath) {
    targetPath = targetPath || e.currentTarget?.dataset?.path || '';
    e.preventDefault();
    if (!_dragSourcePaths.length) return;
    if (typeof hasWriteAccess === 'function' && !hasWriteAccess()) {
        e.dataTransfer.dropEffect = 'none';
        e.currentTarget.classList.remove('drop-before', 'drop-inside', 'drop-after');
        return;
    }

    if (!isValidDropTargets(_dragSourcePaths, targetPath)) {
        e.dataTransfer.dropEffect = 'none';
        e.currentTarget.classList.remove('drop-before', 'drop-inside', 'drop-after');
        return;
    }

    e.dataTransfer.dropEffect = 'move';

    const el = e.currentTarget;
    const rect = el.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const h = rect.height;

    el.classList.remove('drop-before', 'drop-inside', 'drop-after');

    const targetEntry = findFileEntry(fileStore.files, targetPath);
    if (targetEntry && targetEntry.isDirectory) {
        if (y < h * 0.25) {
            el.classList.add('drop-before');
        } else if (y > h * 0.75) {
            el.classList.add('drop-after');
        } else {
            el.classList.add('drop-inside');
            if (_dragExpandTimer) clearTimeout(_dragExpandTimer);
            _dragExpandTimer = setTimeout(() => {
                const children = document.querySelector(`.dir-children[data-path="${targetPath}"]`);
                if (children && children.style.display === 'none') {
                    children.style.display = 'block';
                    _fileTreeExpandedPaths.add(targetPath);
                }
                _dragExpandTimer = null;
            }, 600);
        }
    } else {
        if (y < h * 0.5) {
            el.classList.add('drop-before');
        } else {
            el.classList.add('drop-after');
        }
    }

    if (targetEntry && targetEntry.isDirectory && y >= h * 0.25 && y <= h * 0.75) {
        // already handled above
    } else {
        if (_dragExpandTimer) {
            clearTimeout(_dragExpandTimer);
            _dragExpandTimer = null;
        }
    }
}

function handleTreeDragLeave(e) {
    e.currentTarget.classList.remove('drop-before', 'drop-inside', 'drop-after');
}

async function handleTreeDrop(e, targetPath) {
    targetPath = targetPath || e.currentTarget?.dataset?.path || '';
    if (!requireWriteAccess()) {
        e.preventDefault();
        return;
    }
    e.preventDefault();
    e.stopPropagation();

    const el = e.currentTarget;
    el.classList.remove('drop-before', 'drop-inside', 'drop-after');

    if (!_dragSourcePaths.length || _dragSourcePaths.includes(targetPath)) return;

    if (!isValidDropTargets(_dragSourcePaths, targetPath)) {
        showToast('无法移动到该位置', 'warning');
        return;
    }

    const rect = el.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const h = rect.height;

    const targetEntry = findFileEntry(fileStore.files, targetPath);
    let dropPosition;
    if (targetEntry && targetEntry.isDirectory) {
        if (y < h * 0.25) dropPosition = 'before';
        else if (y > h * 0.75) dropPosition = 'after';
        else dropPosition = 'inside';
    } else {
        dropPosition = y < h * 0.5 ? 'before' : 'after';
    }

    const affectedParents = new Set([getParentPath(targetPath) || '']);
    if (targetEntry?.isDirectory && dropPosition === 'inside') {
        affectedParents.add(targetPath);
    }
    _dragSourcePaths.forEach(path => affectedParents.add(getParentPath(path) || ''));

    const moveRefs = _dragSourcePaths
        .map(path => ({ source_path: path, entry: findFileEntry(fileStore.files, path) }))
        .filter(item => item.entry && item.entry.source === 'server');
    const moved = moveFileEntries(_dragSourcePaths, targetPath, dropPosition);
    if (moved) {
        const serverOps = moveRefs.map(item => ({
            source_path: item.source_path,
            target_path: item.entry.path,
        }));
        _fileTreeSelectedPaths.clear();
        if (dropPosition === 'inside' && targetEntry && targetEntry.isDirectory) {
            targetEntry._expandedAfterDrop = true;
            _fileTreeExpandedPaths.add(targetPath);
        }
        renderFileTree();
        if (dropPosition === 'inside') {
            const children = document.querySelector(`.dir-children[data-path="${targetPath}"]`);
            if (children) children.style.display = 'block';
        }
        await syncServerFileTreeChanges(serverOps, Array.from(affectedParents));
        showToast(`已移动 ${_dragSourcePaths.length} 项`, 'success');
    }

    _dragSourcePath = null;
    _dragSourcePaths = [];
}

function handleTreeDragEnd(e) {
    _dragSourcePath = null;
    _dragSourcePaths = [];
    _kgActiveDraggedFilePaths = [];
    if (_dragExpandTimer) {
        clearTimeout(_dragExpandTimer);
        _dragExpandTimer = null;
    }
    document.querySelectorAll('.tree-item').forEach(el => {
        el.classList.remove('dragging', 'drop-before', 'drop-inside', 'drop-after');
    });
}

function isValidDropTarget(sourcePath, targetPath) {
    if (sourcePath === targetPath) return false;

    if (targetPath.startsWith(sourcePath + '/')) return false;

    const sourceParts = sourcePath.split('/');
    const targetParts = targetPath.split('/');
    if (sourceParts.length > 1 && targetParts.length > 1) {
        const sourceParent = sourceParts.slice(0, -1).join('/');
        if (targetPath === sourceParent) return true;
    }

    return true;
}

function isValidDropTargets(sourcePaths, targetPath) {
    return normalizeDraggedPaths(sourcePaths).every(sourcePath => isValidDropTarget(sourcePath, targetPath));
}

function isDescendantOrSelf(ancestorPath, descendantPath) {
    if (ancestorPath === descendantPath) return true;
    return descendantPath.startsWith(ancestorPath + '/');
}

function normalizeDraggedPaths(paths) {
    const unique = Array.from(new Set(paths.filter(Boolean)));
    return unique.filter(path => !unique.some(other => other !== path && isDescendantOrSelf(other, path)));
}

function getParentPath(path) {
    return path.includes('/') ? path.substring(0, path.lastIndexOf('/')) : null;
}

function getSiblingList(parentPath) {
    if (!parentPath) return fileStore.files;
    const parent = findFileEntry(fileStore.files, parentPath);
    return parent?.children || null;
}

function getOrderedPathsForParent(parentPath) {
    const siblings = getSiblingList(parentPath);
    return (siblings || []).map(item => item.path).filter(Boolean);
}

async function syncServerFileTreeChanges(serverOps, orderParentPaths = []) {
    const parents = Array.from(new Set((orderParentPaths || []).map(path => path || '')));
    const orderPayloads = parents.map(parentPath => ({
        parentPath,
        orderedPaths: getOrderedPathsForParent(parentPath),
    }));

    try {
        if (serverOps && serverOps.length) {
            await moveServerFileResources(serverOps);
        }
        let lastData = null;
        for (const payload of orderPayloads) {
            if (payload.orderedPaths.length) {
                try {
                    lastData = await reorderServerFileResources(payload.parentPath, payload.orderedPaths);
                } catch (orderErr) {
                    console.warn('file tree order sync failed:', orderErr);
                }
            }
        }
        if (lastData?.files) {
            fileStore.rootDirName = '我的文档库';
            fileStore.rootDirHandle = null;
            fileStore.files = lastData.files;
        } else if (serverOps && serverOps.length) {
            await loadServerFileResources({ silent: true });
        }
        renderFileTree();
        updateStats();
    } catch (err) {
        showToast('同步文件树失败: ' + (err.message || '未知错误'), 'error');
    }
}

function moveFileEntriesToRoot(sourcePaths) {
    const paths = normalizeDraggedPaths(sourcePaths);
    const entries = [];
    for (const path of paths) {
        const entry = findFileEntry(fileStore.files, path);
        if (!entry) continue;
        if (removeEntryFromParent(fileStore.files, path, getParentPath(path))) {
            updateEntryPath(entry, entry.name);
            entries.push(entry);
        }
    }
    fileStore.files.push(...entries);
    return entries.length > 0;
}

function moveFileEntries(sourcePaths, targetPath, position) {
    const paths = normalizeDraggedPaths(sourcePaths);
    if (paths.length === 1) return moveFileEntry(paths[0], targetPath, position);
    if (!isValidDropTargets(paths, targetPath)) return false;

    const targetEntry = findFileEntry(fileStore.files, targetPath);
    const entries = [];
    for (const path of paths) {
        const entry = findFileEntry(fileStore.files, path);
        if (entry) entries.push({ path, entry });
    }
    if (!entries.length) return false;

    if (targetEntry?.isDirectory && position === 'inside') {
        for (const { path, entry } of entries) {
            if (removeEntryFromParent(fileStore.files, path, getParentPath(path))) {
                updateEntryPath(entry, `${targetPath}/${entry.name}`);
                targetEntry.children = targetEntry.children || [];
                targetEntry.children.push(entry);
            }
        }
        return true;
    }

    const targetParentPath = getParentPath(targetPath);
    for (const { path } of entries) {
        removeEntryFromParent(fileStore.files, path, getParentPath(path));
    }

    const siblings = getSiblingList(targetParentPath);
    if (!siblings) return false;
    let targetIndex = siblings.findIndex(item => item.path === targetPath);
    if (targetIndex === -1) {
        fileStore.files.push(...entries.map(({ entry }) => {
            updateEntryPath(entry, entry.name);
            return entry;
        }));
        return true;
    }

    let insertIndex = position === 'before' ? targetIndex : targetIndex + 1;
    const movedEntries = entries.map(({ entry }) => {
        updateEntryPath(entry, targetParentPath ? `${targetParentPath}/${entry.name}` : entry.name);
        return entry;
    });
    siblings.splice(insertIndex, 0, ...movedEntries);
    return true;
}

function moveFileEntry(sourcePath, targetPath, position) {
    const sourceEntry = findFileEntry(fileStore.files, sourcePath);
    if (!sourceEntry) return false;

    const sourceParentPath = sourcePath.includes('/') ? sourcePath.substring(0, sourcePath.lastIndexOf('/')) : null;

    if (!removeEntryFromParent(fileStore.files, sourcePath, sourceParentPath)) return false;

    const targetEntry = findFileEntry(fileStore.files, targetPath);
    if (!targetEntry) {
        const parentPath = targetPath.includes('/') ? targetPath.substring(0, targetPath.lastIndexOf('/')) : null;
        insertEntry(fileStore.files, sourceEntry, targetPath, position, parentPath);
    } else if (targetEntry.isDirectory && position === 'inside') {
        updateEntryPath(sourceEntry, targetPath + '/' + sourceEntry.name);
        targetEntry.children.push(sourceEntry);
    } else {
        const parentPath = targetPath.includes('/') ? targetPath.substring(0, targetPath.lastIndexOf('/')) : null;
        insertEntry(fileStore.files, sourceEntry, targetPath, position, parentPath);
    }

    return true;
}

function removeEntryFromParent(files, path, parentPath) {
    if (parentPath) {
        const parent = findFileEntry(files, parentPath);
        if (parent && parent.children) {
            const idx = parent.children.findIndex(f => f.path === path);
            if (idx !== -1) {
                parent.children.splice(idx, 1);
                return true;
            }
        }
    } else {
        const idx = files.findIndex(f => f.path === path);
        if (idx !== -1) {
            files.splice(idx, 1);
            return true;
        }
    }
    return false;
}

function insertEntry(files, entry, targetPath, position, parentPath) {
    if (parentPath) {
        const parent = findFileEntry(files, parentPath);
        if (parent && parent.children) {
            const idx = parent.children.findIndex(f => f.path === targetPath);
            if (idx !== -1) {
                updateEntryPath(entry, parentPath + '/' + entry.name);
                parent.children.splice(position === 'before' ? idx : idx + 1, 0, entry);
                return;
            }
        }
    }

    const idx = files.findIndex(f => f.path === targetPath);
    if (idx !== -1) {
        if (!parentPath) {
            updateEntryPath(entry, entry.name);
        }
        files.splice(position === 'before' ? idx : idx + 1, 0, entry);
    }
}

function updateEntryPath(entry, newPath) {
    const oldPath = entry.path;
    entry.path = newPath;
    remapTreeStatePaths(oldPath, newPath);

    if (entry.isDirectory && entry.children) {
        entry.children.forEach(child => {
            updateEntryPath(child, newPath + '/' + child.name);
        });
    }

    if (fileStore.openedFile && fileStore.openedFile.path === oldPath) {
        fileStore.openedFile.path = newPath;
    }
    if (fileStore.selectedFile && fileStore.selectedFile.path === oldPath) {
        fileStore.selectedFile.path = newPath;
    }
}

function filterFiles(query) {
    const items = document.querySelectorAll('.tree-item');
    const q = query.toLowerCase();
    items.forEach(el => {
        const name = el.querySelector('.name')?.textContent?.toLowerCase() || '';
        el.style.display = !q || name.includes(q) ? '' : 'none';
    });
}

function countFileResourceStats(items = fileStore.files) {
    const stats = { files: 0, folders: 0 };
    for (const item of items || []) {
        if (item.isDirectory) {
            stats.folders += 1;
            const childStats = countFileResourceStats(item.children || []);
            stats.files += childStats.files;
            stats.folders += childStats.folders;
        } else {
            stats.files += 1;
        }
    }
    return stats;
}

function updateStats() {
    const filesEl = document.getElementById('stat-files');
    const nodesEl = document.getElementById('stat-nodes');
    const stats = countFileResourceStats();
    const markdownFileCount = getMarkdownFileEntriesForStats(fileStore.files).length;
    const graphNodes = (typeof kgGraphData !== 'undefined' && Array.isArray(kgGraphData.nodes))
        ? kgGraphData.nodes
        : [];
    const graphNodeCount = graphNodes.length || markdownFileCount;
    if (filesEl) filesEl.textContent = `${stats.files} 文件 / ${stats.folders} 文件夹`;
    if (nodesEl) nodesEl.textContent = `${graphNodeCount} 图谱节点`;
}

function getMarkdownFileEntriesForStats(items = []) {
    const result = [];
    for (const item of items || []) {
        if (item.isDirectory) {
            result.push(...getMarkdownFileEntriesForStats(item.children || []));
        } else if (/\.(md|markdown)$/i.test(item.name || '')) {
            result.push(item);
        }
    }
    return result;
}

async function buildKGFromContent(content, filename) {
    try {
        const data = await api('POST', '/knowledge-graph/build', { text: content, title: filename });
        showToast(`从 "${filename}" 构建图谱完成`, 'success');
        if (currentPage === 'kg') await refreshKGGraph();
    } catch (err) {
        // silent fail for auto-build
    }
}

function initResizers() {
    setupResizer('sidebar-resizer', 'sidebar', 'width', 200, 400);
    setupResizer('kg-resizer', 'kg-right-panel', 'width', 240, 500);
    setupResizer('rag-resizer', 'rag-right-panel', 'width', 240, 500);
    setupResizer('wiki-resizer', 'wiki-right-panel', 'width', 240, 500);
}

function setupResizer(resizerId, targetId, prop, min, max) {
    const resizer = document.getElementById(resizerId);
    const target = document.getElementById(targetId);
    if (!resizer || !target) return;

    let startX, startSize;
    let resizeFrame = 0;
    let pendingSize = 0;
    const direction = targetId === 'sidebar' ? 1 : -1;

    const onMouseDown = (e) => {
        startX = e.clientX;
        startSize = target.getBoundingClientRect()[prop === 'width' ? 'width' : 'height'];
        resizer.classList.add('active');
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    };

    const onMouseMove = (e) => {
        const delta = e.clientX - startX;
        pendingSize = Math.max(min, Math.min(max, startSize + delta * direction));
        if (resizeFrame) return;
        resizeFrame = requestAnimationFrame(() => {
            resizeFrame = 0;
            target.style.width = pendingSize + 'px';
            if (targetId === 'sidebar') {
                target.style.flex = 'none';
                document.documentElement.style.setProperty('--sidebar-w', `${pendingSize}px`);
            }
        });
    };

    const onMouseUp = () => {
        if (resizeFrame) {
            cancelAnimationFrame(resizeFrame);
            resizeFrame = 0;
            target.style.width = pendingSize + 'px';
        }
        resizer.classList.remove('active');
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        if (currentPage === 'kg') {
            const canvas = document.getElementById('kg-mini-canvas');
            if (canvas) resizeKGCanvas();
        }
    };

    resizer.addEventListener('mousedown', onMouseDown);
}

document.addEventListener('DOMContentLoaded', () => {
    const bootStep = (label, fn) => {
        try {
            const result = fn();
            if (result && typeof result.catch === 'function') {
                result.catch(err => console.error(`[boot] ${label} failed`, err));
            }
        } catch (err) {
            console.error(`[boot] ${label} failed`, err);
        }
    };

    document.documentElement.removeAttribute('data-theme');
    try { localStorage.setItem('theme', 'light'); } catch {}
    restoreCachedServerFileTree();
    updateOnlineStatus();
    bootStep('backend monitor', () => startCloudConnectionMonitor());
    bootStep('backend check', () => checkBackendOnline({ force: true }));
    document.body.classList.toggle('kg-layout-active', currentPage === 'kg');
    document.body.classList.toggle('focused-layout-active', currentPage === 'kg' || currentPage === 'rag');
    document.body.classList.toggle('rag-layout-active', currentPage === 'rag');
    document.body.dataset.currentPage = currentPage;
    bootStep('resizers', () => initResizers());
    bootStep('document drag links', () => installKGDocumentDropFallback());
    bootStep('kg page', () => {
        if (typeof initKGPage === 'function') initKGPage();
    });
    bootStep('stats', () => updateStats());
    bootStep('server files', () => loadServerFileResources({ silent: true }));
    bootStep('autosave', () => {
        if (typeof AutoSave !== 'undefined' && AutoSave && typeof AutoSave.init === 'function') {
            AutoSave.init();
        }
    });

    bootStep('enhancements', () => {
        if (typeof Enhancements !== 'undefined' && Enhancements && typeof Enhancements.init === 'function') {
            Enhancements.init();
        }
    });

    window.addEventListener('resize', () => {
        if (currentPage === 'kg') resizeKGCanvas();
    });

    window.addEventListener('beforeunload', (e) => {
        if (isEditableCloudFile() && fileStore.openedFile.isDirty) {
            tryKeepaliveCloudSave();
            e.preventDefault();
            e.returnValue = '';
        }
    });

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden' && isEditableCloudFile() && fileStore.openedFile.isDirty) {
            tryKeepaliveCloudSave();
        }
    });

    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            e.preventDefault();
            saveLocalFile();
        }
    });
    document.addEventListener('click', handleKGDocumentLinkClick);

    const editorContent = document.getElementById('kg-editor-content');
    if (editorContent) {
        editorContent.addEventListener('input', () => {
            if (fileStore.openedFile) {
                markOpenedFileContentChanged(editorContent.value);
            }
        });
    }

    const ragEditorContent = document.getElementById('rag-editor-content');
    if (ragEditorContent) {
        ragEditorContent.addEventListener('input', () => {
            if (fileStore.openedFile) {
                markOpenedFileContentChanged(ragEditorContent.value);
            }
        });
    }

    const dropZone = document.getElementById('convert-drop-zone');
    if (dropZone) {
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('drag-over');
        });
        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('drag-over');
        });
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('drag-over');
            if (e.dataTransfer.files.length) {
                handleConvertFile(e.dataTransfer.files);
            }
        });
    }
});

const EVAL_METRICS = {
    context_precision: { label: 'Context Precision', desc: '上下文精度', detail: '检索结果与问题的相关性排序质量，过滤噪声、无关内容越少分越高' },
    context_recall: { label: 'Context Recall', desc: '上下文召回率', detail: '检索是否覆盖回答所需的全部关键信息，缺关键信息则低分' },
    faithfulness: { label: 'Faithfulness', desc: '忠实度（防幻觉）', detail: '答案是否严格基于检索上下文，无中生有/编造即幻觉，分低' },
    answer_relevancy: { label: 'Answer Relevancy', desc: '答案相关性', detail: '答案是否直接回应问题，通顺但答非所问则低分' },
    answer_correctness: { label: 'Answer Correctness', desc: '答案正确性', detail: '答案与标准答案的事实一致性，兼顾精确与完整' },
    semantic_similarity: { label: 'Semantic Similarity', desc: '语义相似度', detail: '答案与标准答案的语义重合度（用Embedding计算）' },
};

function openEvalDialog(source) {
    const chatId = source === 'kg' ? 'kg-chat-messages' : 'rag-chat-messages';
    const chatEl = document.getElementById(chatId);
    if (!chatEl) return;

    const pairs = [];
    const msgs = chatEl.querySelectorAll('.chat-msg');
    let lastQuestion = '';
    let lastAnswer = '';
    let lastContexts = [];

    msgs.forEach(msg => {
        const isUser = msg.classList.contains('user');
        const bubble = msg.querySelector('.chat-bubble');
        if (!bubble) return;
        const text = bubble.textContent.trim();
        if (!text) return;

        if (isUser) {
            lastQuestion = text;
            lastAnswer = '';
            lastContexts = [];
        } else {
            const ctxItems = bubble.querySelectorAll('.context-item');
            ctxItems.forEach(ci => lastContexts.push(ci.textContent.trim()));
            lastAnswer = text;
            if (lastQuestion) {
                pairs.push({ question: lastQuestion, answer: lastAnswer, contexts: [...lastContexts] });
            }
            lastQuestion = '';
        }
    });

    if (!pairs.length) {
        showToast('暂无对话记录可供评估，请先进行问答', 'warning');
        return;
    }

    const existing = document.getElementById('eval-dialog');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'new-item-dialog';
    overlay.id = 'eval-dialog';

    const pairOptions = pairs.map((p, i) => `<option value="${i}">#${i + 1} ${escapeHtml(p.question.slice(0, 30))}...</option>`).join('');

    overlay.innerHTML = `
        <div class="dialog-box eval-dialog-box">
            <div class="dialog-title">系统评估 (RAGAS)</div>
            <div class="eval-subtitle">基于 LLM 裁判的 RAG 系统质量评估框架</div>
            <div class="eval-form">
                <div class="eval-form-row">
                    <label>选择对话</label>
                    <select id="eval-pair-select" class="eval-select">${pairOptions}</select>
                </div>
                <div class="eval-form-row">
                    <label>标准答案 <span class="eval-hint">(可选，用于正确性和相似度评估)</span></label>
                    <textarea id="eval-ground-truth" class="eval-textarea" rows="2" placeholder="输入标准答案（可选）..."></textarea>
                </div>
            </div>
            <div class="eval-actions">
                <button class="dialog-btn" onclick="closeEvalDialog()">取消</button>
                <button class="dialog-btn primary eval-run-btn" onclick="runEvaluation('${source}')">开始评估</button>
            </div>
            <div id="eval-results" class="eval-results" style="display:none"></div>
        </div>
    `;

    document.body.appendChild(overlay);
    window._evalPairs = pairs;
    window._evalSource = source;
}

function closeEvalDialog() {
    const dialog = document.getElementById('eval-dialog');
    if (dialog) dialog.remove();
    window._evalPairs = null;
    window._evalSource = null;
}

async function runEvaluation(source) {
    const pairs = window._evalPairs;
    const idx = parseInt(document.getElementById('eval-pair-select')?.value || '0');
    const pair = pairs[idx];
    const groundTruth = document.getElementById('eval-ground-truth')?.value?.trim() || '';

    if (!pair) {
        showToast('请选择对话', 'warning');
        return;
    }

    const runBtn = document.querySelector('.eval-run-btn');
    if (runBtn) {
        runBtn.disabled = true;
        runBtn.textContent = '评估中...';
    }

    const resultsEl = document.getElementById('eval-results');
    if (resultsEl) {
        resultsEl.style.display = 'block';
        resultsEl.innerHTML = '<div class="eval-loading">正在评估中，LLM 逐项打分...</div>';
    }

    try {
        const data = await api('POST', '/evaluate', {
            question: pair.question,
            answer: pair.answer,
            contexts: pair.contexts,
            ground_truth: groundTruth,
        });

        if (data.success && data.evaluation) {
            renderEvalResults(data.evaluation);
        } else {
            resultsEl.innerHTML = '<div class="eval-error">评估失败，请重试</div>';
        }
    } catch (err) {
        if (resultsEl) {
            resultsEl.innerHTML = `<div class="eval-error">评估失败: ${escapeHtml(err.message)}</div>`;
        }
    } finally {
        if (runBtn) {
            runBtn.disabled = false;
            runBtn.textContent = '开始评估';
        }
    }
}

function renderEvalResults(evalData) {
    const resultsEl = document.getElementById('eval-results');
    if (!resultsEl) return;

    const overall = evalData.overall_score || 0;
    const overallColor = overall >= 0.7 ? '#10b981' : overall >= 0.4 ? '#f59e0b' : '#ef4444';

    let html = `<div class="eval-overall">
        <div class="eval-overall-score" style="color:${overallColor}">${(overall * 100).toFixed(1)}</div>
        <div class="eval-overall-label">综合评分</div>
    </div>`;

    html += '<div class="eval-metrics">';

    const categories = [
        { title: '检索质量', keys: ['context_precision', 'context_recall'] },
        { title: '生成质量', keys: ['faithfulness', 'answer_relevancy'] },
        { title: '端到端扩展', keys: ['answer_correctness', 'semantic_similarity'] },
    ];

    categories.forEach(cat => {
        const hasAny = cat.keys.some(k => evalData[k] !== null && evalData[k] !== undefined);
        if (!hasAny) return;

        html += `<div class="eval-category">
            <div class="eval-cat-title">${cat.title}</div>`;

        cat.keys.forEach(key => {
            const val = evalData[key];
            const meta = EVAL_METRICS[key];
            if (!meta || val === null || val === undefined) {
                html += `<div class="eval-metric-row eval-metric-na">
                    <div class="eval-metric-info">
                        <span class="eval-metric-label">${meta?.label || key}</span>
                        <span class="eval-metric-desc">${meta?.desc || ''}</span>
                    </div>
                    <span class="eval-metric-value na">N/A</span>
                </div>`;
                return;
            }

            const pct = (val * 100).toFixed(1);
            const color = val >= 0.7 ? '#10b981' : val >= 0.4 ? '#f59e0b' : '#ef4444';

            html += `<div class="eval-metric-row">
                <div class="eval-metric-info">
                    <span class="eval-metric-label">${meta.label}</span>
                    <span class="eval-metric-desc">${meta.desc}</span>
                    <span class="eval-metric-detail">${meta.detail}</span>
                </div>
                <div class="eval-metric-right">
                    <div class="eval-bar-bg"><div class="eval-bar-fill" style="width:${pct}%;background:${color}"></div></div>
                    <span class="eval-metric-value" style="color:${color}">${pct}</span>
                </div>
            </div>`;
        });

        html += '</div>';
    });

    html += '</div>';
    resultsEl.innerHTML = html;
}

async function offlineConvertFile(file, ext) {
    try {
        if (ext === 'pdf') {
            const mdText = await extractPdfText(file);
            showToast('PDF文本已通过本地解析提取', 'success');
            return mdText;
        }
        if (ext === 'docx' || ext === 'doc') {
            const mdText = await convertDocxToMarkdown(file);
            showToast('Word文档已通过本地解析转换', 'success');
            return mdText;
        }
        if (ext === 'xlsx' || ext === 'xls') {
            const mdText = await convertXlsxToMarkdown(file);
            showToast('Excel表格已通过本地解析转换', 'success');
            return mdText;
        }
        if (ext === 'pptx' || ext === 'ppt') {
            const mdText = await convertPptxToMarkdown(file);
            showToast('PPT已通过本地解析转换', 'success');
            return mdText;
        }
        const textExts = ['txt', 'md', 'markdown', 'csv', 'json', 'yaml', 'yml', 'xml', 'rtf', 'html', 'htm'];
        if (textExts.includes(ext)) {
            const text = await file.text();
            if (!text.trim()) {
                showToast('文件内容为空', 'warning');
                return null;
            }
            const mdText = convertTextToMarkdown(text, ext);
            showToast('文本文件已通过本地解析转换', 'success');
            return mdText;
        }
        const imageExts = ['png', 'jpg', 'jpeg', 'bmp', 'tiff', 'tif', 'gif', 'webp', 'svg'];
        if (imageExts.includes(ext)) {
            const dataUrl = await readFileAsDataURL(file);
            const mdText = `![${file.name}](${dataUrl})`;
            showToast('图片已转换为Markdown引用', 'success');
            return mdText;
        }
        const text = await file.text();
        if (text && text.trim()) {
            const mdText = convertTextToMarkdown(text, ext);
            showToast('文件已通过本地解析转换', 'success');
            return mdText;
        }
        return null;
    } catch (err) {
        console.error('离线转换失败:', err);
        showToast('本地转换失败: ' + err.message, 'error');
        return null;
    }
}

async function convertFileInTree(filePath) {
    const fileEntry = findFileEntry(fileStore.files, filePath);
    if (!fileEntry || fileEntry.isDirectory) {
        showToast('请选择一个文件', 'error');
        return;
    }

    const ext = fileEntry.name.split('.').pop().toLowerCase();
    const mdName = fileEntry.name.replace(/\.[^.]+$/, '') + '.md';

    const mdExistingPath = filePath.replace(/\.[^.]+$/, '') + '.md';
    const mdExisting = findFileEntry(fileStore.files, mdExistingPath);
    if (mdExisting) {
        if (!confirm('该文件的 Markdown 版本已存在，是否覆盖？')) return;
        const parentPath = mdExistingPath.includes('/') ? mdExistingPath.substring(0, mdExistingPath.lastIndexOf('/')) : null;
        if (parentPath) {
            const parent = findFileEntry(fileStore.files, parentPath);
            if (parent && parent.children) {
                parent.children = parent.children.filter(f => f.path !== mdExistingPath);
            }
        } else {
            fileStore.files = fileStore.files.filter(f => f.path !== mdExistingPath);
        }
    }

    showToast('正在转换文件…', 'info');

    try {
        let file;
        let mdContent = null;

        if (fileStore.openedFile && fileStore.openedFile.path === filePath && fileStore.openedFile.isConverted) {
            mdContent = fileStore.openedFile.content;
        }

        if (mdContent === null) {
            if (fileEntry.handle) {
                try {
                    file = await fileEntry.handle.getFile();
                } catch (handleErr) {
                    if (fileStore.openedFile && fileStore.openedFile.path === filePath && fileStore.openedFile.content) {
                        const mimeMap = {
                            'pdf': 'application/pdf',
                            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                            'doc': 'application/msword',
                            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            'xls': 'application/vnd.ms-excel',
                            'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                            'ppt': 'application/vnd.ms-powerpoint',
                            'csv': 'text/csv',
                            'json': 'application/json',
                            'html': 'text/html',
                            'htm': 'text/html',
                            'txt': 'text/plain',
                            'md': 'text/markdown',
                            'png': 'image/png',
                            'jpg': 'image/jpeg',
                            'jpeg': 'image/jpeg',
                        };
                        const mime = mimeMap[ext] || 'application/octet-stream';
                        const isBinary = ['pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'png', 'jpg', 'jpeg', 'bmp', 'tiff'].includes(ext);
                        if (isBinary && fileStore.openedFile.content && typeof fileStore.openedFile.content === 'string' && !fileStore.openedFile.content.startsWith('data:')) {
                            showToast('文件内容为已转换文本，无法重新上传转换，请重新打开文件', 'warning');
                            return;
                        }
                        const blob = new Blob([fileStore.openedFile.content], { type: mime });
                        file = new File([blob], fileEntry.name, { type: mime });
                    } else {
                        showToast('无法读取该文件，文件访问权限可能已过期，请重新打开', 'error');
                        return;
                    }
                }
            } else if (fileStore.openedFile && fileStore.openedFile.path === filePath && fileStore.openedFile.content) {
                const mimeMap = {
                    'pdf': 'application/pdf',
                    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'doc': 'application/msword',
                    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'xls': 'application/vnd.ms-excel',
                    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    'ppt': 'application/vnd.ms-powerpoint',
                    'csv': 'text/csv',
                    'json': 'application/json',
                    'html': 'text/html',
                    'htm': 'text/html',
                    'txt': 'text/plain',
                    'md': 'text/markdown',
                    'png': 'image/png',
                    'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg',
                };
                const mime = mimeMap[ext] || 'application/octet-stream';
                const isBinary = ['pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'png', 'jpg', 'jpeg', 'bmp', 'tiff'].includes(ext);
                if (isBinary && typeof fileStore.openedFile.content === 'string' && !fileStore.openedFile.content.startsWith('data:')) {
                    showToast('文件内容为已转换文本，无法重新上传转换，请重新打开文件', 'warning');
                    return;
                }
                const blob = new Blob([fileStore.openedFile.content], { type: mime });
                file = new File([blob], fileEntry.name, { type: mime });
            } else if (fileEntry.isNew && fileStore.openedFile && fileStore.openedFile.path === filePath) {
                const blob = new Blob([fileStore.openedFile.content || ''], { type: 'text/plain' });
                file = new File([blob], fileEntry.name);
            } else {
                showToast('无法读取该文件，请先点击文件打开后再转换', 'error');
                return;
            }

            const formData = new FormData();
            formData.append('file', file);

            let data;
            try {
                data = await api('POST', '/documents/convert', formData, 300000);
            } catch (apiErr) {
                mdContent = await offlineConvertFile(file, ext);
                if (!mdContent) {
                    showToast('转换失败: ' + apiErr.message, 'error');
                    return;
                }
            }

            if (!mdContent) {
                if (!data.success) {
                    mdContent = await offlineConvertFile(file, ext);
                    if (!mdContent) {
                        showToast('转换失败: ' + (data.detail || data.error || '未知错误'), 'error');
                        return;
                    }
                } else {
                    mdContent = data.markdown_content;
                }
            }
        }
        const mdPath = filePath.replace(/\.[^.]+$/, '') + '.md';

        const mdEntry = {
            name: mdName,
            path: mdPath,
            isDirectory: false,
            isNew: true,
        };

        if (fileStore.rootDirHandle) {
            try {
                const dirPath = filePath.includes('/') ? filePath.substring(0, filePath.lastIndexOf('/')) : '';
                let dirHandle = fileStore.rootDirHandle;
                if (dirPath) {
                    const parts = dirPath.split('/');
                    for (const part of parts) {
                        dirHandle = await dirHandle.getDirectoryHandle(part);
                    }
                }
                const newFileHandle = await dirHandle.getFileHandle(mdName, { create: true });
                const writable = await newFileHandle.createWritable();
                await writable.write(mdContent);
                await writable.close();
                mdEntry.handle = newFileHandle;
                mdEntry.isNew = false;
            } catch (writeErr) {
                console.warn('无法写入到本地文件系统:', writeErr);
            }
        }

        const parentPath = filePath.includes('/') ? filePath.substring(0, filePath.lastIndexOf('/')) : null;
        if (parentPath) {
            const parent = findFileEntry(fileStore.files, parentPath);
            if (parent && parent.children) {
                const srcIdx = parent.children.findIndex(f => f.path === filePath);
                parent.children.splice(srcIdx + 1, 0, mdEntry);
            }
        } else {
            const srcIdx = fileStore.files.findIndex(f => f.path === filePath);
            fileStore.files.splice(srcIdx + 1, 0, mdEntry);
        }

        fileStore.openedFile = {
            name: mdName,
            path: mdPath,
            content: mdContent,
            handle: mdEntry.handle || null,
            isDirty: false,
        };

        renderFileTree();
        updateEditorContent();
        showToast(`已转换: ${fileEntry.name} → ${mdName}`, 'success');
    } catch (err) {
        showToast('转换失败: ' + (err.message || '未知错误'), 'error');
    }
}

async function extractTriplesFromFile(filePath) {
    const fileEntry = findFileEntry(fileStore.files, filePath);
    if (!fileEntry || fileEntry.isDirectory) {
        showToast('请选择一个文件', 'error');
        return;
    }

    let content = '';
    try {
        if (fileEntry.handle) {
            content = await readFileContent(fileEntry);
        } else if (fileEntry.source === 'server') {
            const data = await readServerFileResource(fileEntry.path);
            content = data.content || '';
        } else if (fileStore.openedFile && fileStore.openedFile.path === filePath) {
            content = fileStore.openedFile.content || '';
        }
    } catch (err) {
        showToast('读取文件失败: ' + err.message, 'error');
        return;
    }

    if (!content.trim()) {
        showToast('文件内容为空', 'error');
        return;
    }

    showToast('正在拆解三元组，请稍候…', 'info');
    showTripleDialog(fileEntry.name, content, false, filePath);
}

async function buildKGFromFile(filePath) {
    const fileEntry = findFileEntry(fileStore.files, filePath);
    if (!fileEntry || fileEntry.isDirectory) {
        showToast('请选择一个文件', 'error');
        return;
    }

    let content = '';
    try {
        if (fileEntry.handle) {
            content = await readFileContent(fileEntry);
        } else if (fileEntry.source === 'server') {
            const data = await readServerFileResource(fileEntry.path);
            content = data.content || '';
        } else if (fileStore.openedFile && fileStore.openedFile.path === filePath) {
            content = fileStore.openedFile.content || '';
        }
    } catch (err) {
        showToast('读取文件失败: ' + err.message, 'error');
        return;
    }

    if (!content.trim()) {
        showToast('文件内容为空', 'error');
        return;
    }

    showToast('正在拆解三元组并构建图谱，请稍候…', 'info');
    showTripleDialog(fileEntry.name, content, true, filePath);
}

function showTripleDialog(fileName, content, autoBuild, filePath) {
    const existing = document.getElementById('triple-dialog-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'triple-dialog-overlay';
    overlay.className = 'dialog-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="dialog-box triple-dialog-box">
            <div class="dialog-header">
                <h3>${autoBuild ? '构建知识图谱' : '拆解三元组'}</h3>
                <button class="dialog-close" onclick="document.getElementById('triple-dialog-overlay').remove()">&times;</button>
            </div>
            <div class="dialog-body">
                <div class="triple-file-info">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                    <span>${escapeHtml(fileName)}</span>
                    <span class="triple-file-size">${content.length} 字符</span>
                </div>
                <div class="triple-flow">
                    <div class="triple-flow-step">
                        <div class="triple-flow-icon">📄</div>
                        <div class="triple-flow-label">文档</div>
                    </div>
                    <div class="triple-flow-arrow">→</div>
                    <div class="triple-flow-step">
                        <div class="triple-flow-icon">⚙️</div>
                        <div class="triple-flow-label">LLM拆解</div>
                    </div>
                    <div class="triple-flow-arrow">→</div>
                    <div class="triple-flow-step">
                        <div class="triple-flow-icon">🔗</div>
                        <div class="triple-flow-label">三元组</div>
                    </div>
                    ${autoBuild ? `
                    <div class="triple-flow-arrow">→</div>
                    <div class="triple-flow-step">
                        <div class="triple-flow-icon">🌐</div>
                        <div class="triple-flow-label">图谱</div>
                    </div>` : ''}
                </div>
                <div id="triple-results" class="triple-results">
                    <div class="triple-loading">
                        <div class="triple-spinner"></div>
                        <span class="triple-loading-text">正在${autoBuild ? '拆解三元组并构建图谱' : '拆解三元组'}，请稍候…</span>
                        <span class="triple-loading-hint">LLM 正在分析文档内容并提取实体关系</span>
                    </div>
                </div>
            </div>
            <div class="dialog-footer">
                <button class="btn btn-secondary" onclick="document.getElementById('triple-dialog-overlay').remove()">关闭</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    runTripleExtraction(fileName, content, autoBuild, filePath);
}

async function runTripleExtraction(fileName, content, autoBuild, filePath) {
    const resultsEl = document.getElementById('triple-results');
    if (!resultsEl) return;

    try {
        const docKey = filePath || fileName;
        const endpoint = autoBuild ? '/knowledge-graph/build' : '/knowledge-graph/extract';
        const reqBody = {
            text: content,
            title: fileName,
        };
        if (autoBuild) {
            reqBody.doc_key = docKey;
        }
        const data = await api('POST', endpoint, reqBody);

        if (!data.success) {
            resultsEl.innerHTML = `<div class="triple-error">操作失败，请重试</div>`;
            return;
        }

        const triples = data.triples || [];
        const entities = data.entities || [];
        const relations = data.relations || [];
        const nodes = data.nodes || [];
        const edges = data.edges || [];

        if (triples.length === 0 && relations.length > 0) {
            for (const rel of relations) {
                triples.push({
                    subject: rel.head || '',
                    relation: rel.relation || '',
                    object: rel.tail || '',
                });
            }
        }

        if (autoBuild) {
            const entityCount = data.entity_count || entities.length || nodes.length;
            const relationCount = data.relation_count || relations.length || edges.length;

            let html = `<div class="triple-success">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                图谱构建成功！${entityCount} 个实体，${relationCount} 个关系
            </div>`;

            if (triples.length) {
                html += renderTripleTable(triples);
            }

            html += `<button class="btn btn-primary triple-goto-btn" onclick="document.getElementById('triple-dialog-overlay').remove(); navigateTo('kg');">
                查看知识图谱
            </button>`;

            resultsEl.innerHTML = html;
            if (typeof refreshKGGraph === 'function') await refreshKGGraph(docKey);
        } else if (triples.length) {
            let html = `<div class="triple-success">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                成功拆解 ${triples.length} 个三元组
            </div>`;

            html += renderTripleTable(triples);

            html += `<button class="btn btn-primary triple-goto-btn" onclick="buildTriplesToGraph(this)" data-triples='${escapeHtml(JSON.stringify(triples))}'>
                将三元组构建到图谱
            </button>`;

            resultsEl.innerHTML = html;
        } else {
            resultsEl.innerHTML = `<div class="triple-empty">未提取到三元组，请检查文档内容</div>`;
        }
    } catch (err) {
        resultsEl.innerHTML = `<div class="triple-error">操作失败: ${escapeHtml(err.message)}</div>`;
    }
}

function renderTripleTable(triples) {
    let html = '<div class="triple-table-wrap"><table class="triple-table"><thead><tr><th>主体</th><th>关系</th><th>客体</th></tr></thead><tbody>';
    triples.forEach(t => {
        html += `<tr>
            <td><span class="triple-entity">${escapeHtml(t.subject || t.head || '')}</span></td>
            <td><span class="triple-relation">${escapeHtml(t.relation || t.predicate || '')}</span></td>
            <td><span class="triple-entity">${escapeHtml(t.object || t.tail || '')}</span></td>
        </tr>`;
    });
    html += '</tbody></table></div>';
    return html;
}

async function buildTriplesToGraph(btn) {
    const triplesJson = btn.getAttribute('data-triples');
    if (!triplesJson) return;

    const triples = JSON.parse(triplesJson);
    const text = triples.map(t => `${t.subject || t.head} | ${t.relation || t.predicate} | ${t.object || t.tail}`).join('\n');

    btn.disabled = true;
    btn.textContent = '构建中…';

    try {
        const data = await api('POST', '/knowledge-graph/build', {
            text: text,
            title: '三元组导入',
            doc_key: '三元组导入_' + Date.now(),
        });

        if (data.success) {
            btn.textContent = '构建成功';
            btn.classList.remove('btn-primary');
            btn.classList.add('btn-secondary');
            if (typeof refreshKGGraph === 'function') await refreshKGGraph(data.doc_key);
        } else {
            btn.textContent = '构建失败';
        }
    } catch (err) {
        btn.textContent = '构建失败';
    }
}

async function showDataInfo() {
    const isOnlineNow = await checkBackendOnline();
    const overlayNew = document.createElement('div');
    overlayNew.className = 'new-item-dialog';
    overlayNew.id = 'data-info-dialog';
    overlayNew.innerHTML = `
        <div class="dialog-box" style="width:520px;max-height:80vh;overflow-y:auto">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
                <div style="font-size:18px;font-weight:700;color:var(--text-primary)">数据存储信息</div>
                <button class="icon-btn" onclick="document.getElementById('data-info-dialog').remove()">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            <div style="display:flex;flex-direction:column;gap:16px">
                <div style="background:var(--bg-hover);border-radius:var(--radius-md);padding:14px">
                    <div style="font-size:14px;font-weight:600;color:var(--text-primary);margin-bottom:8px">云端持久化</div>
                    <div style="font-size:13px;color:var(--text-secondary);line-height:1.8">
                        <div><strong>服务器数据目录</strong>：知识库、向量库、知识图谱、Wiki、云端文件资源、RAG 会话都会保存在服务器 DATA_DIR。</div>
                        <div style="padding-left:24px;color:var(--text-muted)">浏览器 IndexedDB 只保留少量离线缓存和界面状态，不再作为主要数据源。</div>
                        <div style="padding-left:24px;color:var(--text-muted)">换电脑访问同一个域名，也能读取云端文件、知识库和会话历史。</div>
                    </div>
                </div>
                <div style="background:var(--bg-hover);border-radius:var(--radius-md);padding:14px">
                    <div style="font-size:14px;font-weight:600;color:var(--text-primary);margin-bottom:8px">连接状态</div>
                    <div style="font-size:13px;color:var(--text-secondary)">
                        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${isOnlineNow ? '#22c55e' : '#f59e0b'};margin-right:6px"></span>
                        ${isOnlineNow ? '后端在线，正在使用云端持久化数据' : '后端离线，当前只能使用浏览器本地缓存'}
                    </div>
                </div>
                <div style="font-size:12px;color:var(--text-muted);line-height:1.6;border-top:1px solid var(--border);padding-top:12px">
                    <strong>备份建议：</strong>云端数据在服务器磁盘，后续建议定期备份服务器 DATA_DIR。浏览器本地导出只适合作为临时缓存备份。
                </div>
            </div>
        </div>`;
    document.body.appendChild(overlayNew);
    return;

    const isOnline = await checkBackendOnline();
    const allData = await LocalDB.exportAll();
    const fileCount = (allData.files || []).length;
    const kbCount = (allData.knowledgeBases || []).length;
    const graphCount = (allData.knowledgeGraph || []).length;
    const wikiCount = (allData.wikiPages || []).length;
    const lastSave = AutoSave.formatLastSaveTime();

    const overlay = document.createElement('div');
    overlay.className = 'new-item-dialog';
    overlay.id = 'data-info-dialog';
    overlay.innerHTML = `
        <div class="dialog-box" style="width:520px;max-height:80vh;overflow-y:auto">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
                <div style="font-size:18px;font-weight:700;color:var(--text-primary)">数据存储信息</div>
                <button class="icon-btn" onclick="document.getElementById('data-info-dialog').remove()">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            <div style="display:flex;flex-direction:column;gap:16px">
                <div style="background:var(--bg-hover);border-radius:var(--radius-md);padding:14px">
                    <div style="font-size:14px;font-weight:600;color:var(--text-primary);margin-bottom:8px">存储位置</div>
                    <div style="font-size:13px;color:var(--text-secondary);line-height:1.8">
                        <div>📦 <strong>IndexedDB</strong>（浏览器本地数据库）</div>
                        <div style="padding-left:24px;color:var(--text-muted)">所有文件、知识库、图谱数据均保存在此</div>
                        <div style="padding-left:24px;color:var(--text-muted)">关闭浏览器后数据不会丢失</div>
                        <div style="padding-left:24px;color:var(--text-muted)">清除浏览器数据会导致数据丢失</div>
                    </div>
                </div>
                <div style="background:var(--bg-hover);border-radius:var(--radius-md);padding:14px">
                    <div style="font-size:14px;font-weight:600;color:var(--text-primary);margin-bottom:8px">连接状态</div>
                    <div style="font-size:13px;color:var(--text-secondary)">
                        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${isOnline ? '#22c55e' : '#f59e0b'};margin-right:6px"></span>
                        ${isOnline ? '后端在线 — 数据同步到服务器' : '本地模式 — 数据仅保存在浏览器本地'}
                    </div>
                </div>
                <div style="background:var(--bg-hover);border-radius:var(--radius-md);padding:14px">
                    <div style="font-size:14px;font-weight:600;color:var(--text-primary);margin-bottom:8px">本地数据统计</div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px;color:var(--text-secondary)">
                        <div>📄 文件: ${fileCount}</div>
                        <div>📚 知识库: ${kbCount}</div>
                        <div>🔗 图谱: ${graphCount}</div>
                        <div>📝 Wiki页面: ${wikiCount}</div>
                    </div>
                    <div style="font-size:12px;color:var(--text-muted);margin-top:8px">上次自动保存: ${lastSave}</div>
                </div>
                <div style="display:flex;gap:8px">
                    <button class="dialog-btn primary" onclick="exportLocalData()" style="flex:1">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        导出备份
                    </button>
                    <button class="dialog-btn" onclick="importLocalData()" style="flex:1">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                        导入备份
                    </button>
                </div>
                <div style="font-size:12px;color:var(--text-muted);line-height:1.6;border-top:1px solid var(--border);padding-top:12px">
                    <strong>安全提示：</strong>数据存储在浏览器 IndexedDB 中，仅限当前设备当前浏览器访问。建议定期导出备份。清除浏览器缓存或使用隐私模式会导致数据丢失。
                </div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
}

function getDefaultModelApiKeyCollection() {
    return {
        base_llm: {
            label: '基础模型',
            provider: '智谱 GLM',
            model: 'glm-4-flash',
            base_url: 'https://open.bigmodel.cn/api/paas/v4',
            api_key_masked: '',
            has_api_key: false,
            enabled: true,
            note: '主问答、摘要、文档理解优先使用',
        },
        fallback_llm: {
            label: '兜底基础模型',
            provider: 'DeepSeek',
            model: 'deepseek-chat',
            base_url: 'https://api.deepseek.com',
            api_key_masked: '',
            has_api_key: false,
            enabled: true,
            note: '主模型失败或限流时兜底',
        },
        embedding: {
            label: 'Embedding 模型',
            provider: 'Google Gemini',
            model: 'gemini-embedding-001',
            base_url: 'https://generativelanguage.googleapis.com/v1beta',
            api_key_masked: '',
            has_api_key: false,
            enabled: true,
            dimension: 2560,
            note: 'Google 原生 Embedding（切换后需重建索引）',
        },
        rerank: {
            label: 'Rerank 模型',
            provider: 'SiliconFlow',
            model: 'BAAI/bge-reranker-v2-m3',
            base_url: 'https://api.siliconflow.cn/v1',
            api_key_masked: '',
            has_api_key: false,
            enabled: true,
            note: 'SiliconFlow bge-reranker-v2-m3 远程重排（需 API Key）',
        },
    };
}

function renderModelApiKeyRows(collection) {
    const defaults = getDefaultModelApiKeyCollection();
    return Object.keys(defaults).map(slot => {
        const item = { ...defaults[slot], ...(collection?.[slot] || {}) };
        const statusText = item.has_api_key ? `已保存：${item.api_key_masked || '******'}` : '未保存密钥';
        return `
            <section class="model-key-card" data-model-key-slot="${escapeHtml(slot)}">
                <div class="model-key-card-head">
                    <div>
                        <div class="model-key-title">${escapeHtml(item.label)}</div>
                        <div class="model-key-subtitle">${escapeHtml(item.note || '')}</div>
                    </div>
                    <label class="model-key-toggle">
                        <input type="checkbox" data-field="enabled" ${item.enabled ? 'checked' : ''}>
                        <span>启用</span>
                    </label>
                </div>
                <div class="model-key-grid">
                    <label>
                        <span>服务商</span>
                        <input data-field="provider" value="${escapeHtml(item.provider || '')}" placeholder="例如：智谱 GLM">
                    </label>
                    <label>
                        <span>模型名</span>
                        <input data-field="model" value="${escapeHtml(item.model || '')}" placeholder="例如：glm-4-flash、deepseek-chat 或 BAAI/bge-reranker-v2-m3">
                    </label>
                    ${slot === 'embedding' ? `<label>
                        <span>向量维度</span>
                        <input data-field="dimension" type="number" min="128" max="3072" step="1" value="${Number(item.dimension || 2560)}" placeholder="Gemini 支持 128-3072">
                    </label>` : ''}
                    <label class="model-key-wide">
                        <span>Base URL <small style="color:var(--text-muted)">${slot === 'embedding' && (String(item.provider || '').toLowerCase().includes('google') || String(item.model || '').toLowerCase().includes('gemini')) ? '(Gemini 原生 v1beta 地址)' : slot === 'rerank' ? '(/rerank 兼容接口根地址)' : '(OpenAI 兼容接口根地址)'}</small></span>
                        <input data-field="base_url" value="${escapeHtml(item.base_url || '')}" placeholder="模型服务地址，可为空">
                    </label>
                    <label class="model-key-wide">
                        <span>API Key</span>
                        <input data-field="api_key" type="password" autocomplete="off" placeholder="${escapeHtml(statusText)}">
                    </label>
                    <label class="model-key-clear">
                        <input type="checkbox" data-field="clear_api_key">
                        <span>清空已保存 API Key</span>
                    </label>
                    <div class="model-key-test-row">
                        <button type="button" class="dialog-btn" data-test-slot="${escapeHtml(slot)}" onclick="testModelApiKeyConnection('${escapeHtml(slot)}', this)">测试真实连接</button>
                        <span class="model-key-test-result" data-test-result="${escapeHtml(slot)}">未测试</span>
                    </div>
                </div>
            </section>
        `;
    }).join('');
}

async function testModelApiKeyConnection(slot, button) {
    const card = document.querySelector(`[data-model-key-slot="${CSS.escape(slot)}"]`);
    if (!card) return;
    const config = {};
    card.querySelectorAll('[data-field]').forEach(input => {
        if (input.type !== 'checkbox') config[input.dataset.field] = input.value.trim();
    });
    const result = card.querySelector(`[data-test-result="${CSS.escape(slot)}"]`);
    if (button) { button.disabled = true; button.textContent = '连接测试中...'; }
    if (result) { result.textContent = '正在调用真实接口'; result.className = 'model-key-test-result'; }
    try {
        const data = await api('POST', '/wiki/system/model-api-keys/test', { slot, config }, 30000);
        const successDetail = data.dimension
            ? `${data.dimension}维`
            : (data.result_count !== undefined ? `${data.result_count} 条重排结果` : '接口已响应');
        const errorLabels = {
            QUOTA_INSUFFICIENT: '额度/计费资源不足',
            AUTH_ERROR: 'API Key 或模型权限错误',
            MODEL_NOT_FOUND: '模型不存在或地址错误',
            NETWORK_ERROR: '服务器网络不可达',
            RATE_LIMIT: '服务商限流',
        };
        const errorLabel = errorLabels[data.error_code] || data.error_code || 'UNKNOWN_ERROR';
        const message = data.success
            ? `真实连接成功 · ${data.latency_ms || 0}ms · ${successDetail}`
            : `真实连接失败 · ${errorLabel}${data.http_status ? ` · HTTP ${data.http_status}` : ''} · ${data.message || '服务返回失败'}${data.hint ? `（${data.hint}）` : ''}`;
        if (result) {
            result.textContent = message;
            result.className = `model-key-test-result ${data.success ? 'success' : 'error'}`;
        }
    } catch (err) {
        if (result) { result.textContent = `请求失败：${err.message || '网络错误'}`; result.className = 'model-key-test-result error'; }
    } finally {
        if (button) { button.disabled = false; button.textContent = '测试真实连接'; }
    }
}

async function openModelApiKeysDialog() {
    if (typeof hasWriteAccess === 'function' && !hasWriteAccess()) {
        showToast('游客模式不能修改模型密钥', 'warning');
        return;
    }
    let collection = getDefaultModelApiKeyCollection();
    let updatedAt = '';
    try {
        const data = await api('GET', '/wiki/system/model-api-keys', null, 30000);
        collection = { ...collection, ...(data.collection || {}) };
        updatedAt = data.updated_at || '';
    } catch (err) {
        showToast('模型密钥配置读取失败，将显示默认槽位: ' + (err.message || '未知错误'), 'warning');
    }

    const overlay = document.createElement('div');
    overlay.className = 'new-item-dialog';
    overlay.id = 'model-api-keys-dialog';
    overlay.innerHTML = `
        <div class="dialog-box model-key-dialog">
            <div class="model-key-dialog-head">
                <div>
                    <div class="model-key-dialog-title">模型 API Key 集合</div>
                    <div class="model-key-dialog-note">密钥会保存到服务器 DATA_DIR/system_config/model_config.json，不会只存在浏览器里。</div>
                </div>
                <button class="icon-btn" onclick="closeModelApiKeysDialog()" title="关闭">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            <div class="model-key-list">
                ${renderModelApiKeyRows(collection)}
            </div>
            <div class="model-key-footer">
                <span>${updatedAt ? `上次更新：${escapeHtml(updatedAt.replace('T', ' ').slice(0, 19))}` : '首次配置时请输入对应 API Key'}</span>
                <div class="model-key-actions">
                    <button class="dialog-btn" onclick="closeModelApiKeysDialog()">取消</button>
                    <button class="dialog-btn primary" onclick="saveModelApiKeysDialog()">保存集合</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
}

function closeModelApiKeysDialog() {
    document.getElementById('model-api-keys-dialog')?.remove();
}

function collectModelApiKeyPayload() {
    const collection = {};
    document.querySelectorAll('[data-model-key-slot]').forEach(card => {
        const slot = card.dataset.modelKeySlot;
        const item = {};
        card.querySelectorAll('[data-field]').forEach(input => {
            const field = input.dataset.field;
            if (input.type === 'checkbox') {
                item[field] = input.checked;
            } else {
                item[field] = input.value.trim();
            }
        });
        collection[slot] = item;
    });
    return { collection };
}

async function saveModelApiKeysDialog() {
    if (typeof requireWriteAccess === 'function' && !requireWriteAccess()) return;
    const btn = document.querySelector('#model-api-keys-dialog .dialog-btn.primary');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '保存中...';
    }
    try {
        const saved = await api('POST', '/wiki/system/model-api-keys', collectModelApiKeyPayload(), 30000);
        if (saved.reindex_required) {
            showToast('模型已保存。Embedding 已变更，请对现有知识库执行“重建索引”', 'warning');
        } else {
            showToast('模型 API Key 集合已保存到服务器', 'success');
        }
        closeModelApiKeysDialog();
    } catch (err) {
        showToast('保存失败: ' + (err.message || '未知错误'), 'error');
        if (btn) {
            btn.disabled = false;
            btn.textContent = '保存集合';
        }
    }
}

async function openWeChatMcpDialog() {
    if (typeof hasWriteAccess === 'function' && !hasWriteAccess()) {
        showToast('游客模式不能配置微信公众号 MCP', 'warning');
        return;
    }
    let status = {};
    try { status = await api('GET', '/mcp/wechat/status', null, 15000); } catch (err) { showToast('公众号状态读取失败：' + (err.message || '未知错误'), 'warning'); }
    document.getElementById('wechat-mcp-dialog')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'wechat-mcp-dialog'; overlay.className = 'dialog-overlay';
    overlay.innerHTML = `<div class="dialog-box modal-dialog wechat-mcp-dialog-box"><div class="dialog-header"><div><h3>微信公众号 MCP</h3><p class="dialog-subtitle">连接后可将已审核内容同步为公众号草稿。</p></div><button class="dialog-close" onclick="closeWeChatMcpDialog()" title="关闭">×</button></div><div class="dialog-body">
        <p class="dialog-help">密钥仅保存在服务器。同步默认创建草稿，正式发布仍需一次性审批。</p>
        <label class="form-label checkbox-label"><input id="wechat-mcp-enabled" type="checkbox" ${status.enabled ? 'checked' : ''}> <span>启用微信公众号 MCP</span></label>
        <label class="form-label">AppID<input id="wechat-app-id" type="text" autocomplete="off" placeholder="公众号 AppID"></label>
        <label class="form-label">AppSecret<input id="wechat-app-secret" type="password" autocomplete="new-password" placeholder="留空表示保留服务器已保存值"></label>
        <label class="form-label">默认封面 media_id<input id="wechat-cover-media-id" type="text" autocomplete="off" placeholder="可选；同步草稿时必需封面"></label>
        <div id="wechat-mcp-status" class="dialog-status">当前状态：${status.configured ? '已配置' : '未配置'}；${status.last_error ? escapeHtml(status.last_error) : '未测试'}</div>
    </div><div class="dialog-footer"><button class="dialog-btn" onclick="testWeChatMcpConnection()">测试真实连接</button><button class="dialog-btn primary" onclick="saveWeChatMcpConfig()">保存配置</button></div></div>`;
    document.body.appendChild(overlay);
}

function closeWeChatMcpDialog() { document.getElementById('wechat-mcp-dialog')?.remove(); }

async function testWeChatMcpConnection() {
    const target = document.getElementById('wechat-mcp-status');
    if (target) target.textContent = '正在调用微信真实接口…';
    try {
        const data = await api('POST', '/mcp/wechat/test', null, 30000);
        if (target) target.textContent = data.success ? '连接成功：access_token 获取正常' : `连接失败：${data.message || data.status?.last_error || '请检查配置、IP 白名单和网络出口'}`;
    } catch (err) { if (target) target.textContent = `请求失败：${err.message || '网络错误'}`; }
}

async function saveWeChatMcpConfig() {
    try {
        await api('POST', '/mcp/wechat/config', {
            enabled: !!document.getElementById('wechat-mcp-enabled')?.checked,
            app_id: document.getElementById('wechat-app-id')?.value.trim() || '',
            app_secret: document.getElementById('wechat-app-secret')?.value.trim() || '',
            default_cover_media_id: document.getElementById('wechat-cover-media-id')?.value.trim() || '',
        });
        showToast('微信公众号 MCP 配置已保存', 'success'); closeWeChatMcpDialog();
    } catch (err) { showToast('保存失败：' + (err.message || '未知错误'), 'error'); }
}

async function exportLocalData() {
    try {
        const data = await LocalDB.exportAll();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `knowledge-hub-backup-${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
        showToast('数据备份已导出', 'success');
    } catch (err) {
        showToast('导出失败: ' + err.message, 'error');
    }
}

function importLocalData() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async () => {
        const file = input.files[0];
        if (!file) return;
        try {
            const text = await file.text();
            const data = JSON.parse(text);
            await LocalDB.importAll(data);
            showToast('数据已导入，即将刷新页面', 'success');
            setTimeout(() => location.reload(), 1000);
        } catch (err) {
            showToast('导入失败: ' + err.message, 'error');
        }
    };
    input.click();
}



