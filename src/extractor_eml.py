from __future__ import annotations

import email
import email.policy
import re
from pathlib import Path

from models import ExtractionResult


def _strip_html(html: str) -> str:
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    return re.sub(r"\s+", " ", text).strip()


def extract_eml(path: Path, max_chars: int = 8000) -> ExtractionResult:
    try:
        raw = path.read_bytes()
        msg = email.message_from_bytes(raw, policy=email.policy.compat32)

        # Headers
        subject = msg.get("Subject", "") or ""
        from_addr = msg.get("From", "") or ""
        to_addr = msg.get("To", "") or ""
        date_str = msg.get("Date", "") or ""

        # Decode encoded headers (e.g. =?UTF-8?B?...?=)
        from email.header import decode_header, make_header
        def _decode_header(value: str) -> str:
            try:
                return str(make_header(decode_header(value)))
            except Exception:
                return value

        subject = _decode_header(subject)
        from_addr = _decode_header(from_addr)
        to_addr = _decode_header(to_addr)

        header_block = f"Subject: {subject}\nFrom: {from_addr}\nTo: {to_addr}\nDate: {date_str}"

        # Body
        plain_parts: list[str] = []
        html_parts: list[str] = []
        attachments: list[str] = []

        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            filename = part.get_filename()

            if filename:
                attachments.append(_decode_header(filename))
                continue

            if content_type == "text/plain" and "attachment" not in disposition:
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        plain_parts.append(payload.decode(charset, errors="replace"))
                    except Exception:
                        plain_parts.append(payload.decode("utf-8", errors="replace"))

            elif content_type == "text/html" and "attachment" not in disposition:
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        html_parts.append(_strip_html(payload.decode(charset, errors="replace")))
                    except Exception:
                        html_parts.append(_strip_html(payload.decode("utf-8", errors="replace")))

        # Prefer plain text; fall back to HTML
        body = "\n".join(plain_parts).strip()
        if not body:
            body = "\n".join(html_parts).strip()

        # Attachments summary
        attachment_line = ""
        if attachments:
            attachment_line = "\n[첨부파일] " + ", ".join(attachments)

        combined = f"{header_block}\n\n{body}{attachment_line}".strip()

        if not combined or combined == header_block:
            return ExtractionResult(
                file_type="eml",
                extraction_status="empty_text",
                notes=["No usable text extracted from EML."],
            )

        return ExtractionResult(
            file_type="eml",
            extraction_status="success",
            extracted_text=combined,
            text_excerpt=combined[:max_chars],
            page_count=1,
            notes=[
                f"EML extracted. subject='{subject}' attachments={len(attachments)}"
            ],
        )

    except Exception as exc:
        return ExtractionResult(
            file_type="eml",
            extraction_status=f"error:{exc}",
            notes=["EML extraction failed."],
        )
