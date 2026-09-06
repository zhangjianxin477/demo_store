import io
import zipfile

from app.doc_processor.converter import file_converter


def test_markdown_cleaning_preserves_tables_and_code():
    source = """# Test

| name | value |
| --- | --- |
| a | b |

```python
value  =  1
print(value)
```
"""
    result = file_converter.convert(source.encode("utf-8"), "sample.markdown")

    assert result.success
    assert "| --- | --- |" in result.markdown_content
    assert "value  =  1" in result.markdown_content
    assert result.metadata["cleaning"]["pipeline"] == "structure_preserving_v2"


def test_csv_escapes_markdown_separators():
    result = file_converter.convert("name,note\na,a|b\n".encode("utf-8"), "sample.csv")

    assert result.success
    assert "a\\|b" in result.markdown_content


def test_yaml_and_xml_become_structured_markdown():
    yaml_result = file_converter.convert("name: CoreNote\ntags:\n  - rag\n  - graph\n".encode("utf-8"), "sample.yaml")
    xml_result = file_converter.convert("<root><name>CoreNote</name><tag>rag</tag><tag>graph</tag></root>".encode("utf-8"), "sample.xml")

    assert yaml_result.success
    assert yaml_result.metadata["source_format"] == "yaml"
    assert "CoreNote" in yaml_result.markdown_content
    assert xml_result.success
    assert xml_result.metadata["root_element"] == "root"
    assert "CoreNote" in xml_result.markdown_content


def test_rtf_and_svg_extract_visible_text():
    rtf_result = file_converter.convert(b"{\\rtf1\\ansi CoreNote\\par Knowledge Hub}", "sample.rtf")
    svg_result = file_converter.convert(
        b'<svg xmlns="http://www.w3.org/2000/svg"><title>Graph</title><text>CoreNote</text></svg>',
        "sample.svg",
    )

    assert rtf_result.success
    assert "CoreNote" in rtf_result.markdown_content
    assert "Knowledge Hub" in rtf_result.markdown_content
    assert svg_result.success
    assert "CoreNote" in svg_result.markdown_content


def test_odt_extracts_headings_paragraphs_and_tables():
    content_xml = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
 <office:body><office:text>
  <text:h text:outline-level="1">Overview</text:h>
  <text:p>CoreNote content</text:p>
  <table:table>
   <table:table-row><table:table-cell><text:p>Name</text:p></table:table-cell><table:table-cell><text:p>Value</text:p></table:table-cell></table:table-row>
   <table:table-row><table:table-cell><text:p>Graph</text:p></table:table-cell><table:table-cell><text:p>Enabled</text:p></table:table-cell></table:table-row>
  </table:table>
 </office:text></office:body>
</office:document-content>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("content.xml", content_xml)

    result = file_converter.convert(buffer.getvalue(), "sample.odt")

    assert result.success
    assert "## Overview" in result.markdown_content
    assert "CoreNote content" in result.markdown_content
    assert "| Name | Value |" in result.markdown_content


def test_epub_extracts_html_chapters():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("OEBPS/chapter1.xhtml", "<html><body><h1>Start</h1><p>CoreNote chapter</p></body></html>")

    result = file_converter.convert(buffer.getvalue(), "sample.epub")

    assert result.success
    assert result.metadata["chapter_count"] == 1
    assert "CoreNote chapter" in result.markdown_content
