import logging
import json
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class ConversationMemory:
    def __init__(self, max_turns: int = 10, max_sessions: int = 200, ttl: int = 30 * 24 * 3600):
        self._max_turns = max_turns
        self._max_sessions = max_sessions
        self._ttl = ttl
        self._sessions: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._storage_dir = os.path.join(settings.DATA_DIR, "conversations")
        self._storage_file = os.path.join(self._storage_dir, "rag_sessions.json")
        os.makedirs(self._storage_dir, exist_ok=True)
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not os.path.exists(self._storage_file):
            return
        try:
            with open(self._storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            sessions = data.get("sessions", {})
            ordered = sorted(
                sessions.items(),
                key=lambda item: item[1].get("last_active", item[1].get("created_at", 0)),
            )
            now = time.time()
            for sid, session in ordered[-self._max_sessions:]:
                if now - session.get("last_active", now) <= self._ttl:
                    self._sessions[sid] = {
                        "messages": session.get("messages", []),
                        "created_at": session.get("created_at", now),
                        "last_active": session.get("last_active", now),
                        "metadata": session.get("metadata", {}),
                    }
            logger.info("Loaded %s RAG conversation sessions", len(self._sessions))
        except Exception as e:
            logger.warning("Failed to load RAG conversation sessions: %s", e)

    def _save_to_disk_unlocked(self) -> None:
        try:
            os.makedirs(self._storage_dir, exist_ok=True)
            payload = {
                "version": 1,
                "updated_at": time.time(),
                "sessions": dict(self._sessions),
            }
            tmp_file = self._storage_file + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_file, self._storage_file)
        except Exception as e:
            logger.warning("Failed to save RAG conversation sessions: %s", e)

    def create_session(self, session_id: Optional[str] = None) -> str:
        if session_id and self._get_session(session_id):
            return session_id

        if not session_id:
            import uuid
            session_id = str(uuid.uuid4())

        with self._lock:
            self._evict_if_needed()
            self._sessions[session_id] = {
                "messages": [],
                "created_at": time.time(),
                "last_active": time.time(),
                "metadata": {},
            }
            self._save_to_disk_unlocked()
        return session_id

    def add_message(self, session_id: str, role: str, content: str, metadata: Optional[Dict] = None) -> None:
        with self._lock:
            session = self._get_or_create_session(session_id)
            session["messages"].append({
                "role": role,
                "content": content,
                "timestamp": time.time(),
                "metadata": metadata or {},
            })
            session["last_active"] = time.time()
            self._sessions.move_to_end(session_id)
            self._save_to_disk_unlocked()

    def get_history(self, session_id: str, last_n: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
        with self._lock:
            session = self._get_session(session_id)
            if not session:
                return None

            messages = session["messages"]
            if last_n:
                messages = messages[-last_n:]

            return [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "timestamp": m.get("timestamp", 0),
                    "metadata": m.get("metadata", {}),
                }
                for m in messages
            ]

    def get_context_window(self, session_id: str, max_tokens: int = 2000) -> List[Dict[str, str]]:
        with self._lock:
            session = self._get_session(session_id)
            if not session:
                return []

            result = []
            token_count = 0
            for msg in reversed(session["messages"]):
                if msg.get("metadata", {}).get("type") == "rolling_summary":
                    continue
                msg_tokens = len(msg.get("content", "")) // 2 + 1
                if token_count + msg_tokens > max_tokens and result:
                    break
                result.insert(0, {"role": msg["role"], "content": msg["content"]})
                token_count += msg_tokens

            summary = self._summary_from_session(session)
            if summary:
                result.insert(0, {
                    "role": "system",
                    "content": f"历史对话摘要：{summary}",
                })

            return result

    def build_context_for_llm(self, session_id: str, current_question: str, max_tokens: int = 1500) -> List[Dict[str, str]]:
        history = self.get_context_window(session_id, max_tokens=max_tokens)
        messages = []
        for idx, msg in enumerate(history):
            role = msg.get("role")
            content = msg.get("content", "")
            if idx == len(history) - 1 and role == "user" and content.strip() == current_question.strip():
                continue
            if role in {"system", "user", "assistant"}:
                messages.append({"role": role, "content": content})
        return messages

    def rewrite_with_context(self, session_id: str, current_question: str) -> str:
        question = self.normalize_query_text(current_question)
        history = self.get_history(session_id, last_n=6)
        if not history:
            return question

        recent_user_msgs = [
            m["content"] for m in history
            if m.get("role") == "user" and m.get("content") and m.get("content") != current_question
        ]
        if not recent_user_msgs:
            return question

        if not self._needs_context_resolution(question):
            return question

        context = recent_user_msgs[-1]
        entity = self._extract_context_entity(context)
        if not entity:
            return question

        resolved = question
        for token in ("这个", "这个问题", "它", "该内容", "上述内容", "前面那个", "上面那个", "这里"):
            if token in resolved:
                resolved = resolved.replace(token, entity, 1)
                break

        if resolved == question and (len(question) <= 12 or question.startswith(("那", "所以", "还有", "另外", "继续", "怎么", "为什么"))):
            resolved = f"{entity} {question}"

        return self.normalize_query_text(resolved)

    def normalize_query_text(self, question: str) -> str:
        text = re.sub(r"\s+", " ", (question or "").strip())
        text = re.sub(r"^(请问|我想问一下|帮我看一下|帮我查一下|麻烦问下|那个|就是|嗯|啊)[，,。.\s]*", "", text)
        text = re.sub(r"[吗呢啊呀吧嘛]+[？?]?$", "？", text)
        if text and not text.endswith(("?", "？", "。", "！", "!")):
            text += "？"
        return text

    def needs_compression(self, session_id: str) -> bool:
        if not settings.CONVERSATION_SUMMARY_ENABLED:
            return False
        with self._lock:
            session = self._get_session(session_id)
            if not session:
                return False
            ordinary = [
                m for m in session["messages"]
                if m.get("role") in {"user", "assistant"} and m.get("content")
            ]
            return len(ordinary) >= settings.CONVERSATION_SUMMARY_TRIGGER_MESSAGES

    def get_messages_for_compression(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            session = self._get_session(session_id)
            if not session:
                return None

            ordinary_indices = [
                idx for idx, msg in enumerate(session["messages"])
                if msg.get("role") in {"user", "assistant"} and msg.get("content")
            ]
            keep_recent = max(settings.CONVERSATION_SUMMARY_KEEP_RECENT_MESSAGES, 2)
            batch_size = max(settings.CONVERSATION_SUMMARY_BATCH_MESSAGES, 2)
            compressible = ordinary_indices[:-keep_recent]
            if len(compressible) < batch_size:
                return None
            selected_indices = compressible[:batch_size]
            return {
                "existing_summary": self._summary_from_session(session),
                "messages": [session["messages"][idx] for idx in selected_indices],
                "indices": selected_indices,
            }

    def apply_summary_compression(self, session_id: str, indices: List[int], summary: str) -> bool:
        summary = (summary or "").strip()
        if not indices or not summary:
            return False

        summary = summary[:settings.CONVERSATION_SUMMARY_MAX_CHARS]
        with self._lock:
            session = self._get_session(session_id)
            if not session:
                return False

            index_set = set(indices)
            kept_messages = [msg for idx, msg in enumerate(session["messages"]) if idx not in index_set]
            previous_summary_index = next(
                (idx for idx, msg in enumerate(kept_messages) if msg.get("metadata", {}).get("type") == "rolling_summary"),
                None,
            )
            summary_message = {
                "role": "system",
                "content": f"历史对话摘要：{summary}",
                "timestamp": time.time(),
                "metadata": {"type": "rolling_summary"},
            }
            if previous_summary_index is None:
                kept_messages.insert(0, summary_message)
            else:
                kept_messages[previous_summary_index] = summary_message

            session["messages"] = kept_messages
            session["metadata"]["rolling_summary"] = summary
            session["last_active"] = time.time()
            self._sessions.move_to_end(session_id)
            self._save_to_disk_unlocked()
            return True

    def get_summary(self, session_id: str) -> str:
        with self._lock:
            session = self._get_session(session_id)
            return self._summary_from_session(session) if session else ""

    def clear_session(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                self._save_to_disk_unlocked()
                return True
            return False

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            result = []
            for sid, session in self._sessions.items():
                messages = session.get("messages", [])
                visible_messages = [
                    m for m in messages
                    if m.get("metadata", {}).get("type") != "rolling_summary"
                ]
                first_user = next((m for m in visible_messages if m.get("role") == "user" and m.get("content")), None)
                last_msg = visible_messages[-1] if visible_messages else None
                result.append({
                    "session_id": sid,
                    "message_count": len(visible_messages),
                    "created_at": session["created_at"],
                    "last_active": session["last_active"],
                    "title": (first_user.get("content", "")[:36] if first_user else "新会话"),
                    "preview": (last_msg.get("content", "")[:72] if last_msg else ""),
                    "last_role": last_msg.get("role", "") if last_msg else "",
                    "has_summary": bool(self._summary_from_session(session)),
                })
            return sorted(result, key=lambda item: item["last_active"], reverse=True)

    def _summary_from_session(self, session: Optional[Dict[str, Any]]) -> str:
        if not session:
            return ""
        summary = session.get("metadata", {}).get("rolling_summary", "")
        if summary:
            return summary
        for msg in session.get("messages", []):
            if msg.get("metadata", {}).get("type") == "rolling_summary":
                return msg.get("content", "").replace("历史对话摘要：", "", 1).strip()
        return ""

    def _needs_context_resolution(self, question: str) -> bool:
        if len(question) <= 12:
            return True
        return any(token in question for token in ("这个", "它", "上述", "前面", "上面", "这里", "该内容"))

    def _extract_context_entity(self, context: str) -> str:
        text = self.normalize_query_text(context).strip("？?。!")
        quoted = re.findall(r"[《“\"]([^《》“”\"]{2,40})[》”\"]", text)
        if quoted:
            return quoted[-1]
        terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]{1,40}|[\u4e00-\u9fff]{2,18}", text)
        stopwords = {"什么", "怎么", "为什么", "是否", "可以", "需要", "这个", "那个", "帮我", "一下"}
        candidates = [term for term in terms if term not in stopwords]
        return candidates[-1] if candidates else text[:40]

    def _get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self._sessions.get(session_id)
        if session and time.time() - session["last_active"] > self._ttl:
            del self._sessions[session_id]
            self._save_to_disk_unlocked()
            return None
        return session

    def _get_or_create_session(self, session_id: str) -> Dict[str, Any]:
        session = self._get_session(session_id)
        if session:
            return session
        self._evict_if_needed()
        now = time.time()
        self._sessions[session_id] = {
            "messages": [],
            "created_at": now,
            "last_active": now,
            "metadata": {},
        }
        self._save_to_disk_unlocked()
        return self._sessions[session_id]

    def _evict_if_needed(self):
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if now - s["last_active"] > self._ttl]
        for sid in expired:
            del self._sessions[sid]

        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)

        if expired:
            self._save_to_disk_unlocked()


conversation_memory = ConversationMemory()
