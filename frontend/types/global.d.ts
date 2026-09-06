/**
 * 全局类型定义 - O11: TypeScript 迁移基础类型
 */

// ============ API 相关类型 ============

interface ApiResponse<T = unknown> {
    code?: number;
    message?: string;
    data?: T;
}

interface PaginatedResponse<T> extends ApiResponse<T[]> {
    total: number;
    page: number;
    page_size: number;
}

// ============ 知识库相关类型 ============

interface KnowledgeBase {
    id: string;
    name: string;
    description?: string;
    document_count: number;
    chunk_count: number;
    created_at: string;
    updated_at: string;
}

interface Document {
    id: string;
    kb_id: string;
    filename: string;
    file_type: string;
    file_size: number;
    chunk_count: number;
    status: 'pending' | 'processing' | 'completed' | 'failed';
    created_at: string;
}

interface Chunk {
    id: string;
    doc_id: string;
    kb_id: string;
    content: string;
    metadata?: Record<string, unknown>;
    score?: number;
}

// ============ 搜索相关类型 ============

type SearchMode = 'vector' | 'keyword' | 'hybrid';

interface SearchRequest {
    query: string;
    kb_id: string;
    mode: SearchMode;
    top_k?: number;
    session_id?: string;
}

interface SearchResult {
    chunk_id: string;
    content: string;
    score: number;
    source: string;
    metadata?: Record<string, unknown>;
    highlight?: string;
}

interface RAGResponse {
    answer: string;
    sources: SearchResult[];
    rewritten_query?: string;
    mode: SearchMode;
}

// ============ 知识图谱相关类型 ============

interface KGNode {
    id: string;
    label: string;
    type: string;
    properties?: Record<string, unknown>;
}

interface KGEdge {
    id: string;
    source: string;
    target: string;
    label: string;
    properties?: Record<string, unknown>;
}

interface KGGraph {
    nodes: KGNode[];
    edges: KGEdge[];
}

// ============ Wiki 相关类型 ============

interface WikiPage {
    id: string;
    title: string;
    content: string;
    parent_id?: string;
    order: number;
    created_at: string;
    updated_at: string;
}

// ============ 认证相关类型 ============

interface AuthToken {
    access_token: string;
    refresh_token?: string;
    token_type: string;
    expires_in: number;
}

interface ApiKeyInfo {
    key: string;
    name: string;
    permissions: string[];
    created_at: string;
}

// ============ 任务追踪类型 ============

type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed';

interface TaskInfo {
    task_id: string;
    task_type: string;
    status: TaskStatus;
    progress: number;
    message?: string;
    result?: Record<string, unknown>;
    error?: string;
    created_at: string;
    updated_at: string;
}

// ============ 嵌入模型相关类型 ============

interface EmbeddingProgress {
    status: string;
    progress: number;
    message?: string;
}

interface EmbeddingResult {
    embedding: number[];
    dimension: number;
}

// ============ 前端组件类型 ============

interface ToastOptions {
    message: string;
    type?: 'info' | 'success' | 'warning' | 'error';
    duration?: number;
}

interface FileItemData {
    name: string;
    path: string;
    type: 'folder' | 'file-md' | 'file-pdf' | 'file-img' | 'file';
    depth?: number;
    badge?: string;
}

// ============ 全局声明 ============

declare global {
    interface Window {
        pdfjsLib?: {
            getDocument: (options: { data: ArrayBuffer }) => { promise: Promise<PDFDocument> };
        };
    }

    interface PDFDocument {
        numPages: number;
        getPage: (num: number) => Promise<PDFPage>;
    }

    interface PDFPage {
        getTextContent: () => Promise<PDFTextContent>;
    }

    interface PDFTextContent {
        items: PDFTextItem[];
    }

    interface PDFTextItem {
        str: string;
        transform: number[];
        width?: number;
        height?: number;
    }
}

export {};
