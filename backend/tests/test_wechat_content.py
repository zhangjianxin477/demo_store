import unittest

from app.mcp.wechat_content import extract_image_sources, markdown_to_html, prepare_article, sanitize_html


class WeChatContentTests(unittest.TestCase):
    def test_markdown_conversion_keeps_structure(self):
        html = markdown_to_html("# 标题\n\n**重点**\n\n- 一\n- 二\n\n![图](https://example.com/a.jpg)")
        self.assertIn("<h1>标题</h1>", html)
        self.assertIn("<strong>重点</strong>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<img src=\"https://example.com/a.jpg\" />", html)

    def test_sanitize_removes_unsafe_tags_and_attributes(self):
        html = sanitize_html('<script>alert(1)</script><p onclick="bad">正文</p><img src="javascript:bad">')
        self.assertNotIn("script", html)
        self.assertNotIn("onclick", html)
        self.assertNotIn("javascript", html)
        self.assertIn("<p>正文</p>", html)

    def test_prepare_requires_cover_for_sync(self):
        result = prepare_article("标题", "摘要", "正文")
        self.assertFalse(result["can_sync"] if "can_sync" in result else bool(result.get("cover_media_id")))
        self.assertEqual(result["image_sources"], [])

    def test_extract_image_sources_deduplicates_markdown_and_html(self):
        content = '<img src="https://example.com/a.jpg">\n![a](https://example.com/a.jpg)\n![b](https://example.com/b.jpg)'
        self.assertEqual(extract_image_sources(content), ["https://example.com/a.jpg", "https://example.com/b.jpg"])


if __name__ == "__main__":
    unittest.main()
