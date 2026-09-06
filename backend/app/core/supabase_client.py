import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_supabase_client = None


def get_supabase_client():
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    if not settings.use_supabase:
        return None

    try:
        from supabase import create_client, Client
        _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        logger.info(f"Supabase 客户端初始化成功: {settings.SUPABASE_URL}")
        return _supabase_client
    except ImportError:
        logger.warning("supabase 包未安装，请运行: pip install supabase")
        return None
    except Exception as e:
        logger.error(f"Supabase 客户端初始化失败: {e}")
        return None


def ensure_bucket_exists(bucket_name: Optional[str] = None):
    client = get_supabase_client()
    if not client:
        return False

    bucket = bucket_name or settings.SUPABASE_BUCKET
    try:
        buckets = client.storage.list_buckets()
        bucket_names = [b.name for b in buckets]
        if bucket not in bucket_names:
            client.storage.create_bucket(bucket, {"public": False})
            logger.info(f"Supabase 存储桶创建成功: {bucket}")
        return True
    except Exception as e:
        logger.error(f"Supabase 存储桶检查/创建失败: {e}")
        return False


def init_supabase_tables():
    client = get_supabase_client()
    if not client:
        return False

    sql_statements = [
        """
        CREATE TABLE IF NOT EXISTS kb_registry (
            kb_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            chunk_strategy TEXT DEFAULT 'auto',
            chunk_size INTEGER DEFAULT 0,
            chunk_overlap INTEGER DEFAULT 0,
            doc_count INTEGER DEFAULT 0,
            chunk_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS kb_documents (
            doc_id TEXT PRIMARY KEY,
            kb_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            filename TEXT DEFAULT '',
            content_path TEXT DEFAULT '',
            chunk_count INTEGER DEFAULT 0,
            file_size BIGINT DEFAULT 0,
            created_at TEXT DEFAULT '',
            FOREIGN KEY (kb_id) REFERENCES kb_registry(kb_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_kb_documents_kb_id ON kb_documents(kb_id);
        """,
        """
        CREATE TABLE IF NOT EXISTS vector_chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            kb_id TEXT DEFAULT '',
            content TEXT NOT NULL,
            metadata JSONB DEFAULT '{}',
            embedding vector(2048)
        );
        CREATE INDEX IF NOT EXISTS idx_vector_chunks_doc_id ON vector_chunks(doc_id);
        CREATE INDEX IF NOT EXISTS idx_vector_chunks_kb_id ON vector_chunks(kb_id);
        """,
    ]

    for sql in sql_statements:
        try:
            client.rpc("exec_sql", {"query": sql}).execute()
        except Exception:
            try:
                client.postgrest.schema("public").from_("kb_registry").select("kb_id").limit(1).execute()
            except Exception:
                logger.warning(f"Supabase 表初始化需手动执行 SQL，请参考文档")

    logger.info("Supabase 表结构检查完成")
    return True
