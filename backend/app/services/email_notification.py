import os
import json
import uuid
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailNotificationService:
    def __init__(self):
        self._config_dir = os.path.join(settings.DATA_DIR, "email_config")
        os.makedirs(self._config_dir, exist_ok=True)
        self._config_file = os.path.join(self._config_dir, "config.json")
        self._queue_file = os.path.join(self._config_dir, "queue.json")
        self._config: Dict = {}
        self._queue: List[Dict] = []
        self._load_config()
        self._load_queue()

    def _load_config(self):
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
            except Exception as e:
                logger.error(f"邮件配置加载失败: {e}")
        if not self._config:
            self._config = {
                "enabled": False,
                "smtp_host": "",
                "smtp_port": 587,
                "smtp_user": "",
                "smtp_password": "",
                "use_tls": True,
                "use_ssl": False,
                "from_address": "",
                "subscribers": [],
                "notify_on_page_update": True,
                "notify_on_comment": True,
                "notify_on_annotation": True,
                "notify_on_share": False,
            }
        # Cloud deployments may use ephemeral disks. Environment variables
        # provide a durable Secret-based fallback while UI-saved values remain
        # authoritative when no corresponding Secret is present.
        env_map = {
            "EMAIL_ENABLED": ("enabled", lambda v: str(v).lower() in {"1", "true", "yes", "on"}),
            "SMTP_HOST": ("smtp_host", str),
            "SMTP_PORT": ("smtp_port", lambda v: int(v)),
            "SMTP_USER": ("smtp_user", str),
            "SMTP_PASSWORD": ("smtp_password", str),
            "SMTP_FROM": ("from_address", str),
            "SMTP_USE_TLS": ("use_tls", lambda v: str(v).lower() in {"1", "true", "yes", "on"}),
            "SMTP_USE_SSL": ("use_ssl", lambda v: str(v).lower() in {"1", "true", "yes", "on"}),
        }
        for env_name, (key, converter) in env_map.items():
            raw = os.getenv(env_name)
            if raw is None or raw == "":
                continue
            try:
                self._config[key] = converter(raw)
            except (TypeError, ValueError):
                logger.warning("忽略无效邮件环境变量: %s", env_name)

    def _save_config(self):
        with open(self._config_file, "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

    def _load_queue(self):
        if os.path.exists(self._queue_file):
            try:
                with open(self._queue_file, "r", encoding="utf-8") as f:
                    self._queue = json.load(f)
            except Exception as e:
                self._queue = []

    def _save_queue(self):
        with open(self._queue_file, "w", encoding="utf-8") as f:
            json.dump(self._queue, f, ensure_ascii=False, indent=2)

    def get_config(self) -> Dict:
        safe_config = self._config.copy()
        if safe_config.get("smtp_password"):
            safe_config["smtp_password"] = "******"
        return safe_config

    def update_config(self, **kwargs) -> Dict:
        for key, value in kwargs.items():
            if key == "smtp_password" and value == "******":
                continue
            self._config[key] = value
        self._save_config()
        return {"success": True, "config": self.get_config()}

    def test_connection(self) -> Dict:
        if not self._config.get("smtp_host"):
            return {"success": False, "error": "SMTP主机未配置"}
        try:
            smtp = self._connect_smtp(timeout=10)
            if self._config.get("smtp_user"):
                smtp.login(self._config["smtp_user"], self._config["smtp_password"])
            try:
                smtp.quit()
            except Exception:
                smtp.close()
            return {"success": True, "message": "SMTP连接测试成功", "smtp_host": self._config.get("smtp_host"), "smtp_port": self._config.get("smtp_port")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test_send(self, recipient: str) -> Dict:
        """Send a real, explicitly requested test message via configured SMTP."""
        target = str(recipient or "").strip()
        if not target or "@" not in target:
            return {"success": False, "error": "请输入有效的测试收件人邮箱"}
        if not self._config.get("smtp_host"):
            return {"success": False, "error": "SMTP主机未配置"}
        email_data = {
            "id": str(uuid.uuid4())[:8],
            "event_type": "smtp_test",
            "title": "Knowledge Hub SMTP 测试",
            "content": "这是一封真实测试邮件。收到此邮件表示当前 SMTP 配置可以完成发信。",
            "html_content": "<p>这是一封真实测试邮件。收到此邮件表示当前 SMTP 配置可以完成发信。</p>",
            "recipients": [target],
        }
        result = self._send_email(email_data)
        return {**result, "recipient": target}

    def _connect_smtp(self, timeout: int = 15):
        """连接普通 SMTP；465 端口自动使用 SSL，587 使用 STARTTLS。"""
        import smtplib
        host = self._config.get("smtp_host", "")
        port = int(self._config.get("smtp_port", 587) or 587)
        if bool(self._config.get("use_ssl", False)) or port == 465:
            return smtplib.SMTP_SSL(host, port, timeout=timeout)
        smtp = smtplib.SMTP(host, port, timeout=timeout)
        if self._config.get("use_tls", True):
            smtp.starttls()
        return smtp

    def add_subscriber(self, email: str, notify_types: Optional[List[str]] = None) -> Dict:
        subscribers = self._config.get("subscribers", [])
        for s in subscribers:
            if s.get("email") == email:
                return {"success": False, "error": "该邮箱已订阅"}
        subscriber = {
            "email": email,
            "notify_types": notify_types or ["page_update", "comment", "annotation"],
            "subscribed_at": datetime.now().isoformat(),
        }
        subscribers.append(subscriber)
        self._config["subscribers"] = subscribers
        self._save_config()
        return {"success": True, "subscriber": subscriber}

    def remove_subscriber(self, email: str) -> bool:
        subscribers = self._config.get("subscribers", [])
        new_list = [s for s in subscribers if s.get("email") != email]
        if len(new_list) == len(subscribers):
            return False
        self._config["subscribers"] = new_list
        self._save_config()
        return True

    def notify(self, event_type: str, title: str, content: str,
               page_id: str = "", author: str = "system") -> Dict:
        if not self._config.get("enabled"):
            return {"success": False, "error": "邮件通知未启用"}

        notify_key = f"notify_on_{event_type}"
        if not self._config.get(notify_key, True):
            return {"success": False, "error": f"事件类型 {event_type} 的通知未启用"}

        subscribers = self._config.get("subscribers", [])
        recipients = []
        for s in subscribers:
            if event_type in s.get("notify_types", []):
                recipients.append(s["email"])

        if not recipients:
            return {"success": False, "error": "无订阅者"}

        email_data = {
            "id": str(uuid.uuid4())[:8],
            "event_type": event_type,
            "title": title,
            "content": content,
            "page_id": page_id,
            "author": author,
            "recipients": recipients,
            "status": "queued",
            "created_at": datetime.now().isoformat(),
        }
        self._queue.append(email_data)
        self._save_queue()

        result = self._send_email(email_data)
        if result["success"]:
            email_data["status"] = "sent"
            email_data["sent_at"] = datetime.now().isoformat()
        else:
            email_data["status"] = "failed"
            email_data["error"] = result.get("error", "")
        self._save_queue()
        return result

    def _send_email(self, email_data: Dict) -> Dict:
        if not self._config.get("smtp_host"):
            return {"success": False, "error": "SMTP未配置"}

        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[Knowledge Hub] {email_data['title']}"
            msg["From"] = self._config.get("from_address", self._config.get("smtp_user", ""))
            msg["To"] = ", ".join(email_data["recipients"])

            html_content = email_data.get("html_content") or f"""
<html><body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px">
<h2 style="color:#6366f1">{email_data['title']}</h2>
<p>{email_data['content']}</p>
<hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0">
<p style="color:#9ca3af;font-size:12px">
此邮件由 Knowledge Hub 自动发送 · {datetime.now().strftime('%Y-%m-%d %H:%M')}
</p></body></html>"""
            msg.attach(MIMEText(str(email_data.get("content") or ""), "plain", "utf-8"))
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            smtp = self._connect_smtp(timeout=15)
            if self._config.get("smtp_user"):
                smtp.login(self._config["smtp_user"], self._config["smtp_password"])
            smtp.sendmail(msg["From"], email_data["recipients"], msg.as_string())
            smtp.quit()

            logger.info(f"邮件发送成功: {email_data['title']} -> {email_data['recipients']}")
            return {"success": True, "recipients": email_data["recipients"]}
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return {"success": False, "error": str(e)}

    def get_queue(self, status: Optional[str] = None) -> List[Dict]:
        if status:
            return [e for e in self._queue if e.get("status") == status]
        return self._queue[-50:]

    def retry_failed(self) -> Dict:
        failed = [e for e in self._queue if e.get("status") == "failed"]
        success_count = 0
        for email_data in failed:
            result = self._send_email(email_data)
            if result["success"]:
                email_data["status"] = "sent"
                email_data["sent_at"] = datetime.now().isoformat()
                success_count += 1
        self._save_queue()
        return {"success": True, "retried": len(failed), "succeeded": success_count}

    def queue_briefing(self, title: str, markdown: str, html: str,
                       recipients: List[str], idempotency_key: str = "") -> Dict:
        """将日报以纯文本 + HTML 入队并真实发送。"""
        recipients = [str(item).strip() for item in (recipients or []) if str(item).strip()]
        if not recipients:
            return {"success": False, "status": "skipped", "error": "未配置日报收件人"}
        if not self._config.get("enabled"):
            return {"success": False, "status": "skipped", "error": "邮件通知未启用"}
        if idempotency_key:
            for item in self._queue:
                if item.get("idempotency_key") == idempotency_key and item.get("status") in {"queued", "sent"}:
                    return {"success": item.get("status") == "sent", "status": item.get("status"), "id": item.get("id"), "deduplicated": True}
        email_data = {
            "id": str(uuid.uuid4())[:8],
            "event_type": "passive_briefing",
            "title": title,
            "content": markdown,
            "html_content": html,
            "recipients": recipients,
            "idempotency_key": idempotency_key,
            "status": "queued",
            "created_at": datetime.now().isoformat(),
        }
        self._queue.append(email_data)
        self._save_queue()
        result = self._send_email(email_data)
        if result.get("success"):
            email_data["status"] = "sent"
            email_data["sent_at"] = datetime.now().isoformat()
        else:
            email_data["status"] = "failed"
            email_data["error"] = result.get("error", "")
        self._save_queue()
        return {**result, "status": email_data["status"], "id": email_data["id"], "recipients": recipients}


email_notification_service = EmailNotificationService()
