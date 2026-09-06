/**
 * 文件转换模块 - O2: 从 app.js 拆分
 * 负责 PDF/DOCX/XLSX/PPTX 等格式的本地转换
 */

// 使用 var 避免与 app.js 中的声明冲突
var _pdfjsLoaded = false;
var _mammothLoaded = false;
var _xlsxLoaded = false;

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

async function loadJsZip() {
    if (window.JSZip) return;
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js';
        script.onload = resolve;
        script.onerror = () => reject(new Error('JSZip 加载失败'));
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
        await loadJsZip();
        const zip = await JSZip.loadAsync(file);
        const slideFiles = Object.keys(zip.files)
            .filter(name => name.match(/ppt\/slides\/slide\d+\.xml$/))
            .sort((a, b) => {
                const numA = parseInt(a.match(/slide(\d+)/)?.[1] || '0');
                const numB = parseInt(b.match(/slide(\d+)/)?.[1] || '0');
                return numA - numB;
            });

        let md = '';
        for (const slidePath of slideFiles) {
            const xml = await zip.file(slidePath).async('string');
            const textContent = xml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
            if (textContent) {
                md += `### 幻灯片\n\n${textContent}\n\n`;
            }
        }
        return md || '（无法提取 PPTX 内容）';
    } catch (e) {
        return `[PPTX 解析失败: ${e.message}]`;
    }
}

function convertTextToMarkdown(text, ext) {
    switch (ext) {
        case 'md': case 'markdown': return text;
        case 'txt': return text;
        case 'csv': {
            const lines = text.split('\n').filter(l => l.trim());
            if (lines.length === 0) return '';
            const rows = lines.map(l => l.split(',').map(c => c.trim().replace(/^"|"$/g, '')));
            const maxCols = Math.max(...rows.map(r => r.length));
            let md = '| ' + rows[0].concat(Array(maxCols - rows[0].length).fill('')).join(' | ') + ' |\n';
            md += '| ' + Array(maxCols).fill('---').join(' | ') + ' |\n';
            for (let i = 1; i < rows.length; i++) {
                md += '| ' + rows[i].concat(Array(maxCols - rows[i].length).fill('')).join(' | ') + ' |\n';
            }
            return md;
        }
        case 'json': {
            try {
                const obj = JSON.parse(text);
                return '```json\n' + JSON.stringify(obj, null, 2) + '\n```';
            } catch { return text; }
        }
        case 'html': case 'htm': return text;
        default: return text;
    }
}

async function extractPdfText(file) {
    await loadPdfJs();
    const arrayBuffer = await file.arrayBuffer();
    const pdf = await window.pdfjsLib.getDocument({ data: arrayBuffer }).promise;
    const numPages = pdf.numPages;
    let allText = '';

    for (let pageNum = 1; pageNum <= numPages; pageNum++) {
        const page = await pdf.getPage(pageNum);
        const textContent = await page.getTextContent();
        const items = textContent.items;

        if (items.length === 0) continue;

        const pageText = buildPageText(items);
        allText += pageText + '\n\n';
    }

    return cleanPdfText(mergeBrokenChineseLines(allText));
}

function buildPageText(items) {
    const lines = [];
    let currentLine = '';
    let lastY = null;
    let lastX = null;
    const LINE_THRESHOLD = 3;
    const SPACE_THRESHOLD = 8;

    for (const item of items) {
        const text = item.str;
        const transform = item.transform;
        const y = Math.round(transform[5]);
        const x = Math.round(transform[4]);

        if (lastY !== null && Math.abs(y - lastY) > LINE_THRESHOLD) {
            if (currentLine.trim()) lines.push(currentLine.trim());
            currentLine = text;
        } else if (lastX !== null && (x - lastX) > SPACE_THRESHOLD) {
            currentLine += ' ' + text;
        } else {
            currentLine += text;
        }

        lastY = y;
        lastX = x + (item.width || 0);
    }

    if (currentLine.trim()) lines.push(currentLine.trim());
    return lines.join('\n');
}

function detectColumnBoundary(lines, minX, maxX) {
    const gaps = [];
    const gapSize = (maxX - minX) / 50;
    for (let x = minX + gapSize * 5; x < maxX - gapSize * 5; x += gapSize) {
        let hasLeft = false, hasRight = false;
        for (const line of lines) {
            for (const item of line.items) {
                const ix = item.transform[4];
                if (Math.abs(ix - x) < gapSize * 2) continue;
                if (ix < x) hasLeft = true;
                if (ix > x) hasRight = true;
            }
        }
        if (hasLeft && hasRight) {
            let gapCount = 0;
            for (const line of lines) {
                let hasItemNear = false;
                for (const item of line.items) {
                    if (Math.abs(item.transform[4] - x) < gapSize * 3) {
                        hasItemNear = true;
                        break;
                    }
                }
                if (!hasItemNear) gapCount++;
            }
            if (gapCount > lines.length * 0.3) {
                gaps.push({ x, gapCount });
            }
        }
    }

    if (gaps.length > 0) {
        gaps.sort((a, b) => b.gapCount - a.gapCount);
        return gaps[0].x;
    }
    return null;
}

function reorderColumns(lines, boundary) {
    const leftItems = [];
    const rightItems = [];

    for (const line of lines) {
        for (const item of line.items) {
            if (item.transform[4] < boundary) {
                leftItems.push(item);
            } else {
                rightItems.push(item);
            }
        }
    }

    const result = [];
    const processItems = (items) => {
        items.sort((a, b) => b.transform[5] - a.transform[5] || a.transform[4] - b.transform[4]);
        let currentLine = '';
        let lastY = null;
        for (const item of items) {
            const y = Math.round(item.transform[5]);
            if (lastY !== null && Math.abs(y - lastY) > 3) {
                if (currentLine.trim()) result.push(currentLine.trim());
                currentLine = item.str;
            } else {
                currentLine += item.str;
            }
            lastY = y;
        }
        if (currentLine.trim()) result.push(currentLine.trim());
    };

    processItems(leftItems);
    result.push('');
    processItems(rightItems);
    return result.join('\n');
}

function cleanPdfText(text) {
    text = text.replace(/\n{3,}/g, '\n\n');
    text = text.replace(/(\S)\n(\S)/g, '$1 $2');
    text = text.replace(/([\u4e00-\u9fff])\s+([\u4e00-\u9fff])/g, '$1$2');
    return text.trim();
}

function mergeBrokenChineseLines(text) {
    const lines = text.split('\n');
    const result = [];
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) { result.push(''); continue; }

        const prevLine = result.length > 0 ? result[result.length - 1] : '';
        const prevEndsCJK = /[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]$/.test(prevLine);
        const currStartsCJK = /^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/.test(line);

        if (prevEndsCJK && currStartsCJK && !prevLine.endsWith('。') && !prevLine.endsWith('！')
            && !prevLine.endsWith('？') && !prevLine.endsWith('；') && !prevLine.endsWith('：')
            && !prevLine.endsWith('.') && !prevLine.endsWith('!') && !prevLine.endsWith('?')) {
            result[result.length - 1] = prevLine + line;
        } else {
            result.push(line);
        }
    }
    return result.join('\n');
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

async function offlineConvertFile(file, ext) {
    switch (ext) {
        case 'pdf': return await extractPdfText(file);
        case 'docx': case 'doc': return await convertDocxToMarkdown(file);
        case 'xlsx': case 'xls': return await convertXlsxToMarkdown(file);
        case 'pptx': return await convertPptxToMarkdown(file);
        case 'txt': case 'md': case 'markdown': return await file.text();
        case 'csv': return convertTextToMarkdown(await file.text(), 'csv');
        case 'json': return convertTextToMarkdown(await file.text(), 'json');
        default: throw new Error(`不支持的格式: ${ext}`);
    }
}

// 导出到全局
window.loadPdfJs = loadPdfJs;
window.loadMammothJs = loadMammothJs;
window.loadXlsxJs = loadXlsxJs;
window.loadJsZip = loadJsZip;
window.convertDocxToMarkdown = convertDocxToMarkdown;
window.convertXlsxToMarkdown = convertXlsxToMarkdown;
window.convertPptxToMarkdown = convertPptxToMarkdown;
window.convertTextToMarkdown = convertTextToMarkdown;
window.extractPdfText = extractPdfText;
window.chunkText = chunkText;
window.offlineConvertFile = offlineConvertFile;
