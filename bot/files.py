import io
import re
import zipfile

TEXT_EXT = (".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".xml", ".yml", ".yaml", ".log", ".sql", ".java", ".c", ".cpp", ".css")


def extract_text(name: str, data: bytes) -> str:
    n = (name or "").lower()
    if n.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:40])
    if n.endswith(".docx"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    if n.endswith(TEXT_EXT):
        return data.decode("utf-8", "ignore")
    raise ValueError("Unsupported file type. Send PDF, DOCX, TXT, MD, CSV or code files.")
