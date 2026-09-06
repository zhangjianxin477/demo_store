"""核心模块单元测试 - O12: 测试覆盖率提升。

覆盖 auth、path_security、task_tracker、inverted_index、monitoring 等核心模块。
"""

import os
import sys
import json
import time
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

# 确保可以导入 app 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPathSecurity(unittest.TestCase):
    """路径安全校验模块测试。"""

    def setUp(self):
        # 动态导入，避免缺少依赖导致导入失败
        try:
            from app.core.path_security import sanitize_filename, validate_path
            self.sanitize_filename = sanitize_filename
            self.validate_path = validate_path
        except ImportError:
            self.skipTest("path_security 模块导入失败")

    def test_sanitize_filename_removes_traversal(self):
        """测试文件名清理：移除路径遍历字符。"""
        self.assertNotIn("..", self.sanitize_filename("../../../etc/passwd"))
        self.assertNotIn("/", self.sanitize_filename("dir/file.txt"))
        self.assertNotIn("\\", self.sanitize_filename("dir\\file.txt"))

    def test_sanitize_filename_preserves_normal(self):
        """测试文件名清理：保留正常文件名。"""
        self.assertEqual(self.sanitize_filename("document.pdf"), "document.pdf")
        self.assertIn("test", self.sanitize_filename("test file.txt"))

    def test_validate_path_prevents_traversal(self):
        """测试路径验证：阻止路径遍历攻击。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                self.validate_path(tmpdir, "../../../etc/passwd")

    def test_validate_path_allows_normal(self):
        """测试路径验证：允许正常路径。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self.validate_path(tmpdir, "test.txt", check_extension=False)
            self.assertTrue(result.startswith(tmpdir))

    def test_validate_path_blocks_dangerous_extension(self):
        """测试路径验证：阻止危险扩展名。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                self.validate_path(tmpdir, "malware.exe")


class TestTaskTracker(unittest.TestCase):
    """任务追踪模块测试。"""

    def setUp(self):
        try:
            from app.core.task_tracker import TaskTracker, TaskStatus
            self.TaskTracker = TaskTracker
            self.TaskStatus = TaskStatus
        except ImportError:
            self.skipTest("task_tracker 模块导入失败")

    def test_create_task(self):
        """测试任务创建。"""
        tracker = self.TaskTracker()
        task = tracker.create_task("test-001", "document_upload")
        self.assertEqual(task.task_id, "test-001")
        self.assertEqual(task.task_type, "document_upload")
        self.assertEqual(task.status, self.TaskStatus.PENDING)

    def test_update_task_progress(self):
        """测试任务进度更新。"""
        tracker = self.TaskTracker()
        tracker.create_task("test-002")
        task = tracker.update_task("test-002", progress=50.0, message="处理中")
        self.assertEqual(task.progress, 50.0)
        self.assertEqual(task.message, "处理中")

    def test_complete_task(self):
        """测试任务完成。"""
        tracker = self.TaskTracker()
        tracker.create_task("test-003")
        tracker.update_task("test-003", status=self.TaskStatus.COMPLETED, result={"chunks": 10})
        task = tracker.get_task("test-003")
        self.assertEqual(task.status, self.TaskStatus.COMPLETED)
        self.assertEqual(task.result["chunks"], 10)

    def test_fail_task(self):
        """测试任务失败。"""
        tracker = self.TaskTracker()
        tracker.create_task("test-004")
        tracker.update_task("test-004", status=self.TaskStatus.FAILED, error="解析失败")
        task = tracker.get_task("test-004")
        self.assertEqual(task.status, self.TaskStatus.FAILED)
        self.assertEqual(task.error, "解析失败")

    def test_nonexistent_task(self):
        """测试查询不存在的任务。"""
        tracker = self.TaskTracker()
        self.assertIsNone(tracker.get_task("nonexistent"))
        self.assertIsNone(tracker.update_task("nonexistent", progress=50))

    def test_ttl_expiry(self):
        """测试任务 TTL 过期清理。"""
        tracker = self.TaskTracker(ttl_seconds=1)
        tracker.create_task("test-ttl")
        time.sleep(1.5)
        # 创建新任务触发清理
        tracker.create_task("test-ttl2")
        self.assertIsNone(tracker.get_task("test-ttl"))

    def test_list_tasks(self):
        """测试任务列表查询。"""
        tracker = self.TaskTracker()
        tracker.create_task("list-1")
        tracker.create_task("list-2")
        tasks = tracker.list_tasks()
        self.assertEqual(len(tasks), 2)

    def test_concurrent_access(self):
        """测试并发访问安全性。"""
        tracker = self.TaskTracker()
        errors = []

        def create_and_update(task_id):
            try:
                tracker.create_task(task_id)
                tracker.update_task(task_id, progress=50.0)
                tracker.update_task(task_id, status=self.TaskStatus.COMPLETED)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_and_update, args=(f"concurrent-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        tasks = tracker.list_tasks()
        self.assertEqual(len(tasks), 10)

    def test_agent_task_payload_survives_restart(self):
        """Agent 任务的原始参数应能持久化，供服务重启后恢复。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = os.path.join(tmpdir, "tasks.json")
            tracker = self.TaskTracker(storage_path=storage_path)
            tracker.create_task("agent-persist", "agent_rag")
            tracker.update_task(
                "agent-persist",
                result={"_payload": {"goal": "总结三篇文章", "template": "research"}},
            )

            restored = self.TaskTracker(storage_path=storage_path)
            task = restored.get_task("agent-persist")
            self.assertIsNotNone(task)
            self.assertEqual(task.task_type, "agent_rag")
            self.assertEqual(task.result["_payload"]["goal"], "总结三篇文章")

    def test_non_agent_tasks_are_not_persisted(self):
        """文档上传等高频任务保持原有内存行为，避免污染持久化文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = os.path.join(tmpdir, "tasks.json")
            tracker = self.TaskTracker(storage_path=storage_path)
            tracker.create_task("upload-persist", "document_upload")
            restored = self.TaskTracker(storage_path=storage_path)
            self.assertIsNone(restored.get_task("upload-persist"))


class TestInvertedIndex(unittest.TestCase):
    """倒排索引模块测试。"""

    def setUp(self):
        try:
            from app.rag.inverted_index import InvertedIndex
            self.InvertedIndex = InvertedIndex
        except ImportError:
            self.skipTest("inverted_index 模块导入失败")

        # 使用临时目录避免污染数据
        self._orig_data_dir = None
        try:
            from app.core import config
            self._orig_data_dir = config.settings.DATA_DIR
            config.settings.DATA_DIR = tempfile.mkdtemp()
        except Exception:
            pass

    def test_add_and_search(self):
        """测试添加文档和搜索。"""
        idx = self.InvertedIndex()
        idx.add_document(0, "知识图谱是一种结构化的知识表示方法")
        idx.add_document(1, "向量搜索是信息检索的核心技术")
        idx.add_document(2, "知识表示与向量检索相结合")

        results = idx.search("知识图谱")
        self.assertGreater(len(results), 0)
        # 文档0应该排在最前
        self.assertEqual(results[0]["doc_id"], 0)

    def test_remove_document(self):
        """测试移除文档。"""
        idx = self.InvertedIndex()
        idx.add_document(0, "测试文档")
        self.assertTrue(idx.remove_document(0))
        self.assertFalse(idx.remove_document(999))

    def test_batch_add(self):
        """测试批量添加。"""
        idx = self.InvertedIndex()
        docs = [(i, f"文档{i}的内容包含关键词和术语") for i in range(10)]
        idx.add_documents_batch(docs)
        self.assertEqual(idx._total_docs, 10)

    def test_empty_search(self):
        """测试空索引搜索。"""
        idx = self.InvertedIndex()
        results = idx.search("测试")
        self.assertEqual(len(results), 0)

    def test_stats(self):
        """测试索引统计。"""
        idx = self.InvertedIndex()
        idx.add_document(0, "测试统计信息")
        stats = idx.get_stats()
        self.assertEqual(stats["total_docs"], 1)
        self.assertGreater(stats["vocab_size"], 0)


class TestMonitoring(unittest.TestCase):
    """监控模块测试。"""

    def setUp(self):
        try:
            from app.core.monitoring import MetricsCollector
            self.MetricsCollector = MetricsCollector
        except ImportError:
            self.skipTest("monitoring 模块导入失败")

    def test_counter(self):
        """测试计数器。"""
        m = self.MetricsCollector()
        m.inc_counter("requests_total")
        m.inc_counter("requests_total", 2)
        self.assertEqual(m._counters["requests_total"], 3.0)

    def test_gauge(self):
        """测试仪表盘。"""
        m = self.MetricsCollector()
        m.set_gauge("active_connections", 10)
        self.assertEqual(m._gauges["active_connections"], 10.0)
        m.set_gauge("active_connections", 5)
        self.assertEqual(m._gauges["active_connections"], 5.0)

    def test_histogram(self):
        """测试直方图。"""
        m = self.MetricsCollector()
        m.observe_histogram("request_duration", 0.1)
        m.observe_histogram("request_duration", 0.5)
        m.observe_histogram("request_duration", 1.0)
        self.assertEqual(len(m._histograms["request_duration"]), 3)

    def test_time_histogram(self):
        """测试计时上下文管理器。"""
        m = self.MetricsCollector()
        with m.time_histogram("operation_duration"):
            time.sleep(0.01)
        self.assertEqual(len(m._histograms["operation_duration"]), 1)
        self.assertGreater(m._histograms["operation_duration"][0], 0)

    def test_metrics_text_output(self):
        """测试 Prometheus 文本格式输出。"""
        m = self.MetricsCollector()
        m.inc_counter("test_counter")
        m.set_gauge("test_gauge", 42)
        text = m.get_metrics_text()
        self.assertIn("test_counter", text)
        self.assertIn("test_gauge", text)

    def test_labeled_metrics(self):
        """测试带标签的指标。"""
        m = self.MetricsCollector()
        m.inc_counter("http_requests", labels={"method": "GET", "status": "200"})
        m.inc_counter("http_requests", labels={"method": "POST", "status": "201"})
        stats = m.get_stats()
        self.assertIn("http_requests{method=\"GET\",status=\"200\"}", stats["counters"])


class TestAuth(unittest.TestCase):
    """认证模块测试。"""

    def setUp(self):
        try:
            from app.core.auth import create_access_token, decode_token, verify_api_key, generate_api_key
            self.create_access_token = create_access_token
            self.decode_token = decode_token
            self.verify_api_key = verify_api_key
            self.generate_api_key = generate_api_key
        except ImportError:
            self.skipTest("auth 模块导入失败")

    def test_create_and_decode_token(self):
        """测试 JWT Token 创建和解码。"""
        token = self.create_access_token(user_id="test_user", permissions=["read", "write"])
        self.assertIsNotNone(token)
        payload = self.decode_token(token)
        self.assertEqual(payload["sub"], "test_user")
        self.assertEqual(payload["permissions"], ["read", "write"])
        self.assertEqual(payload["type"], "access")

    def test_invalid_token(self):
        """测试无效 Token 解码。"""
        payload = self.decode_token("invalid.token.here")
        self.assertIsNone(payload)

    def test_api_key_generation(self):
        """测试 API Key 生成。"""
        key_info = self.generate_api_key(name="test_key", permissions=["read"])
        self.assertIn("key", key_info)
        self.assertTrue(key_info["key"].startswith("kh_"))

    def test_api_key_verification(self):
        """测试 API Key 验证。"""
        key_info = self.generate_api_key(name="test_key", permissions=["read"])
        verified = self.verify_api_key(key_info["key"])
        self.assertIsNotNone(verified)
        self.assertEqual(verified["name"], "test_key")

    def test_invalid_api_key(self):
        """测试无效 API Key 验证。"""
        result = self.verify_api_key("kh_invalid_key")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
