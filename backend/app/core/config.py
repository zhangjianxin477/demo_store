from pydantic_settings import BaseSettings
from typing import Optional, List
import os


class Settings(BaseSettings):
    APP_NAME: str = "Knowledge Hub"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = "development"
    DEBUG: bool = False

    TENANT_ID: str = "default"

    HOST: str = "0.0.0.0"
    PORT: int = 8080

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    OPENAI_MODEL: str = "glm-4-flash"
    OPENAI_TEMPERATURE: float = 0.7
    OPENAI_MAX_TOKENS: int = 2000
    EMBEDDING_API_KEY: Optional[str] = None
    EMBEDDING_BASE_URL: Optional[str] = None
    EMBEDDING_MODEL: str = "Pro/BAAI/bge-m3"
    EMBEDDING_DIMENSION: int = 1024
    # Native Google Gemini Embeddings settings.  The Gemini adapter is
    # selected when the configured embedding model/provider contains
    # ``gemini``/``google``; it does not use the OpenAI SDK endpoint.
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"
    GEMINI_EMBEDDING_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    GEMINI_EMBEDDING_DIMENSION: int = 2560
    EMBEDDING_PREFER_LOCAL: bool = False
    EMBEDDING_LOCAL_ENABLED: bool = True
    LOCAL_EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 100
    CHUNK_STRATEGY_DEFAULT: str = "layout_aware"
    CHILD_CHUNK_SIZE: int = 350
    CHILD_CHUNK_MIN_SIZE: int = 120
    CHILD_CHUNK_MAX_SIZE: int = 450
    CHILD_CHUNK_OVERLAP: int = 50

    LAYOUT_ANALYSIS_ENABLED: bool = True
    LAYOUT_YOLO_MODEL: str = "doclayout_yolo_docstructbench_imgsz640.pt"
    TABLE_STRUCTURED_EXTRACTION: bool = True
    IMAGE_EXTRACTION_ENABLED: bool = True
    OCR_MODE: str = "off"  # off | local

    RAG_TOP_K: int = 5
    RAG_SIMILARITY_THRESHOLD: float = 0.65
    RAG_RETRIEVAL_MIN_K: int = 20
    RAG_RETRIEVAL_MULTIPLIER: int = 8
    QUERY_REWRITE_ENABLED: bool = True
    QUERY_REWRITE_AUTO_LLM_ENABLED: bool = False
    QUERY_EXPANSION_ENABLED: bool = True
    QUERY_REWRITE_MIN_OVERLAP: float = 0.25
    HYBRID_SEARCH_WEIGHT_VECTOR: float = 0.6
    HYBRID_SEARCH_WEIGHT_KEYWORD: float = 0.4
    BM25_K1: float = 1.5
    BM25_B: float = 0.75

    RERANK_ENABLED: bool = True
    RERANK_USE_LLM: bool = True
    RERANK_KEYWORD_BONUS: float = 0.3
    RERANK_BIGRAM_BONUS: float = 0.2
    RERANK_EXACT_BONUS: float = 0.05
    RERANK_EXACT_CAP: float = 0.2
    RERANK_TITLE_BONUS: float = 0.1
    RERANK_TITLE_CAP: float = 0.3
    RERANK_LENGTH_PENALTY_FACTOR: float = 0.05

    QUERY_CACHE_ENABLED: bool = True
    QUERY_CACHE_TTL: int = 300
    QUERY_CACHE_MAX_SIZE: int = 200

    CONVERSATION_SUMMARY_ENABLED: bool = True
    # Approximately 20 user/assistant turns (40 messages) before rolling
    # compression. Keep a short recent window so follow-up questions retain
    # local context while older history is represented by a summary.
    CONVERSATION_SUMMARY_TRIGGER_MESSAGES: int = 40
    CONVERSATION_SUMMARY_KEEP_RECENT_MESSAGES: int = 12
    CONVERSATION_SUMMARY_BATCH_MESSAGES: int = 20
    CONVERSATION_SUMMARY_MAX_CHARS: int = 1600

    DATA_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "data")
    UPLOAD_DIR: str = ""
    VECTOR_STORE_DIR: str = ""
    KNOWLEDGE_BASE_DIR: str = ""

    SUPABASE_URL: Optional[str] = None
    SUPABASE_KEY: Optional[str] = None
    SUPABASE_BUCKET: str = "knowledge-hub-files"
    STORAGE_MODE: str = "local"
    VECTOR_BACKEND: str = "local"  # local | supabase | milvus
    MILVUS_URI: Optional[str] = None
    MILVUS_TOKEN: Optional[str] = None
    MILVUS_COLLECTION: str = "knowledge_hub_chunks"
    MILVUS_METRIC_TYPE: str = "COSINE"
    MCP_SERVERS_JSON: Optional[str] = None

    # Passive information ingestion / scheduled briefing.
    PASSIVE_INGEST_ENABLED: bool = True
    PASSIVE_MAX_CONCURRENT_RUNS: int = 1
    PASSIVE_WEEKLY_REVIEW_ENABLED: bool = True
    PASSIVE_WEEKLY_REVIEW_CRON: str = "0 8 * * 1"
    PASSIVE_WEEKLY_REVIEW_LOOKBACK_DAYS: int = 7
    PASSIVE_WEEKLY_REVIEW_MAX_FILES: int = 30
    PASSIVE_WEEKLY_REVIEW_RECIPIENTS: str = ""

    # Hard wall-clock bound for one Agent task. Individual model/tool calls
    # have their own timeouts; this outer limit prevents retries from keeping
    # a request alive indefinitely on a small cloud instance.
    AGENT_TASK_TIMEOUT_SECONDS: int = 180

    # WeChat Official Account MCP. Credentials stay server-side and are never
    # included in runtime descriptions or task results.
    WECHAT_MCP_ENABLED: bool = False
    WECHAT_APP_ID: Optional[str] = None
    WECHAT_APP_SECRET: Optional[str] = None
    WECHAT_DEFAULT_COVER_MEDIA_ID: Optional[str] = None

    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: List[str] = ["*"]
    SECRET_KEY: str = "knowledge-hub-secret-key-change-in-production"
    ACCESS_CONTROL_ENABLED: bool = True

    MAX_FILE_SIZE: int = 200 * 1024 * 1024

    class Config:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), ".env")
        env_file_encoding = "utf-8"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.TENANT_ID and self.TENANT_ID != "default":
            self.DATA_DIR = os.path.join(self.DATA_DIR, self.TENANT_ID)
        self.UPLOAD_DIR = os.path.join(self.DATA_DIR, "uploads")
        self.VECTOR_STORE_DIR = os.path.join(self.DATA_DIR, "vector_store")
        self.KNOWLEDGE_BASE_DIR = os.path.join(self.DATA_DIR, "knowledge_base")
        os.makedirs(self.UPLOAD_DIR, exist_ok=True)
        os.makedirs(self.VECTOR_STORE_DIR, exist_ok=True)
        os.makedirs(self.KNOWLEDGE_BASE_DIR, exist_ok=True)

    @property
    def use_supabase(self) -> bool:
        return (
            self.STORAGE_MODE == "supabase"
            and bool(self.SUPABASE_URL)
            and bool(self.SUPABASE_KEY)
        )

    @property
    def use_milvus(self) -> bool:
        return (
            self.VECTOR_BACKEND.lower() == "milvus"
            and bool(self.MILVUS_URI)
            and bool(self.MILVUS_TOKEN)
        )

    @property
    def ocr_enabled(self) -> bool:
        return self.OCR_MODE.lower() in {"local"}

    @property
    def local_ocr_enabled(self) -> bool:
        return self.OCR_MODE.lower() == "local"


settings = Settings()
