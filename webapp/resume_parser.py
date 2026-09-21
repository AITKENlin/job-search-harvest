"""简历文本提取：支持 PDF / DOCX / TXT。无依赖时降级为可读提示。"""
import io


def extract_text(file_storage):
    """file_storage: Flask 的 request.files 对象，含 .filename 与 .read()。"""
    if file_storage is None:
        return ""
    name = (getattr(file_storage, "filename", "") or "").lower()
    data = file_storage.read()

    if name.endswith(".txt"):
        try:
            return data.decode("utf-8", "ignore")
        except Exception:
            return data.decode("gbk", "ignore")

    if name.endswith(".pdf"):
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=data, filetype="pdf")
            return "\n".join(page.get_text() for page in doc)
        except Exception as e:
            return "[PDF 解析失败：请安装 PyMuPDF（pip install pymupdf）或直接粘贴文字] " + str(e)

    if name.endswith(".docx"):
        try:
            import docx
            d = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in d.paragraphs if p.text)
        except Exception as e:
            return "[DOCX 解析失败：请安装 python-docx（pip install python-docx）或直接粘贴文字] " + str(e)

    # 兜底：当文本试着解码
    try:
        return data.decode("utf-8", "ignore")
    except Exception:
        return ""
