import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestRetrievedMedia(unittest.TestCase):
    def test_resolves_relative_markdown_image_inside_resource_root(self):
        from app.rag.media import extract_images

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "自动抓取" / "2026-09-05" / "article.md"
            image = article.parent / "assets" / "cover.png"
            image.parent.mkdir(parents=True)
            article.write_text("![封面](assets/cover.png)", encoding="utf-8")
            image.write_bytes(b"png")

            result = extract_images(article.read_text(encoding="utf-8"), article.relative_to(root).as_posix(), root)

            self.assertEqual(result[0]["url"], "/api/v1/file-resources/raw/自动抓取/2026-09-05/assets/cover.png")
            self.assertEqual(result[0]["caption"], "封面")

    def test_accepts_existing_internal_raw_url_and_rejects_external_url(self):
        from app.rag.media import extract_images

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "自动抓取" / "cover.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"jpg")
            content = (
                "![内部](/api/v1/file-resources/raw/自动抓取/cover.jpg) "
                "![外部](https://example.com/cover.jpg)"
            )

            result = extract_images(content, base=root)

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["source_path"], "自动抓取/cover.jpg")


if __name__ == "__main__":
    unittest.main()
