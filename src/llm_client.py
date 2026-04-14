from __future__ import annotations

from pathlib import Path
import json
import os
import re

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


class OllamaClient:
    def __init__(self, config: dict, project_root: Path) -> None:
        env_path = project_root / ".env"
        if load_dotenv is not None and env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)

        self.enabled = bool(config["llm"].get("enabled", True))
        self.provider = str(config["llm"].get("provider", "ollama")).lower().strip()
        self.endpoint = config["llm"].get("endpoint", "http://127.0.0.1:11434/api/generate")
        self.model = config["llm"].get("primary_model", "qwen3.5:latest")
        self.temperature = float(config["llm"].get("temperature", 0.1))
        self.timeout_seconds = int(config["llm"].get("timeout_seconds", 120))
        openai_cfg = config["llm"].get("openai", {})
        self.openai_model = str(openai_cfg.get("model", "gpt-4.1-mini"))
        self.openai_base_url = str(openai_cfg.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        self.openai_api_key = str(openai_cfg.get("api_key", "") or os.getenv("OPENAI_API_KEY", ""))
        rules_path = project_root / "data" / "naming_rules.md"
        self.naming_rules = rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""

    def extract_metadata(self, payload: dict) -> dict | None:
        if not self.enabled:
            return None
        prompt = self._build_prompt(payload)
        if self.provider == "openai":
            return self._extract_via_openai(prompt)
        return self._extract_via_ollama(prompt)

    def _extract_via_ollama(self, prompt: str) -> dict | None:
        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature},
        }
        try:
            import requests

            response = requests.post(self.endpoint, json=body, timeout=self.timeout_seconds)
            response.raise_for_status()
            raw = response.json().get("response", "").strip()
            return self._parse_json(raw)
        except Exception:
            return None

    def _extract_via_openai(self, prompt: str) -> dict | None:
        if not self.openai_api_key:
            return None
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            import requests

            payload = {
                "model": self.openai_model,
                "input": prompt,
                "temperature": self.temperature,
            }
            # Prefer Responses API.
            response = requests.post(
                f"{self.openai_base_url}/responses",
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
            if response.ok:
                raw = self._extract_text_from_responses(response.json())
                return self._parse_json(raw or "")

            # Fallback: Chat Completions API.
            chat_payload = {
                "model": self.openai_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
            }
            chat_response = requests.post(
                f"{self.openai_base_url}/chat/completions",
                headers=headers,
                json=chat_payload,
                timeout=self.timeout_seconds,
            )
            chat_response.raise_for_status()
            raw = (
                chat_response.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            return self._parse_json(raw)
        except Exception:
            return None

    def _build_prompt(self, payload: dict) -> str:
        schema = {
            "summary": "",
            "document_title": "",
            "doc_type": "",
            "case_or_project_name": "",
            "institution_or_lawfirm": "",
            "document_date": "",
            "english_keyword": "",
            "suggested_filename": "",
            "reason": "",
            "confidence": 0.0,
            "needs_manual_review": True,
        }
        return (
            "You standardize internal legal document filenames.\n"
            "Output JSON only and follow the schema exactly.\n"
            "summary rules: exactly one sentence, max 80 characters, no greeting/signature/disclaimer boilerplate.\n"
            "Write a meaningful summary that describes the specific legal action, parties, and subject matter.\n"
            "Example good summaries: '두바이 Sunrise 소송 관련 법률통지문 (Al Dhaheri)', '수단 대우아파트 PJT 토지매매 예비계약서', 'SIAC 중재 청구서 - 계약 위반 손해배상 청구'\n"
            "Use document body meaning, not filename tokens.\n"
            "Do not copy date prefixes, bracket tags, or page markers into summary.\n"
            "If OCR text is noisy, infer the legal core topic from repeated meaningful phrases.\n"
            "Prefer legal core action/subject over generic wording. Include parties or project name when identifiable.\n"
            "document_title rules: derived from summary, max 10 chars.\n"
            "Keep English as English, Korean as Korean. Non-English/Korean should be represented in Korean.\n\n"
            f"Naming rules:\n{self.naming_rules}\n\n"
            f"Input payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            f"Output schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def _extract_text_from_responses(data: dict) -> str:
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"].strip()
        output = data.get("output", [])
        texts: list[str] = []
        for item in output:
            for content in item.get("content", []):
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        return "\n".join(texts).strip()

    @staticmethod
    def _parse_json(raw: str) -> dict | None:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
