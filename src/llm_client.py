from __future__ import annotations

from pathlib import Path
import json
import os
import re

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


class LLMClient:
    def __init__(self, config: dict, project_root: Path) -> None:
        env_path = project_root / ".env"
        if load_dotenv is not None and env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)

        self.enabled = bool(config["llm"].get("enabled", True))
        self.temperature = float(config["llm"].get("temperature", 0.1))
        self.timeout_seconds = int(config["llm"].get("timeout_seconds", 120))
        openai_cfg = config["llm"].get("openai", {})
        self.model = str(openai_cfg.get("model", "gpt-4.1-mini"))
        self.base_url = str(openai_cfg.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = str(openai_cfg.get("api_key", "") or os.getenv("OPENAI_API_KEY", ""))
        rules_path = project_root / "data" / "naming_rules.md"
        self.naming_rules = rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""

    def extract_metadata(self, payload: dict) -> dict | None:
        if not self.enabled:
            return None
        if not self.api_key:
            return None
        prompt = self._build_prompt(payload)
        return self._call_openai(prompt)

    def generate_short_title(self, summary: str) -> str:
        """summary에서 10자 이내 파일명 제목을 생성한다. 실패 시 빈 문자열 반환."""
        if not self.enabled or not self.api_key or not summary:
            return ""
        prompt = (
            "You are a Korean legal document naming assistant.\n"
            "Create a concise file name title (maximum 10 characters) from the summary below.\n\n"
            "Rules:\n"
            "- Korean preferred; English only for proper nouns or untranslatable legal terms\n"
            "- Must convey the core legal action or document type specifically\n"
            "- Do NOT use generic words: 문서, 파일, 자료, document\n"
            "- Return ONLY the title text, no explanation, no punctuation\n\n"
            f"Summary: {summary}\n\nTitle:"
        )
        try:
            import requests
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0, "max_tokens": 20},
                timeout=30,
            )
            if resp.ok:
                return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        except Exception:
            pass
        return ""

    def _call_openai(self, prompt: str) -> dict | None:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            import requests

            # Responses API 시도
            response = requests.post(
                f"{self.base_url}/responses",
                headers=headers,
                json={"model": self.model, "input": prompt, "temperature": self.temperature},
                timeout=self.timeout_seconds,
            )
            if response.ok:
                raw = self._extract_text_from_responses(response.json())
                return self._parse_json(raw or "")

            # Chat Completions API 폴백
            chat_response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": self.temperature,
                },
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
            "document_abstract": "",
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
            # Legal metadata for chatbot search index
            "case_name_normalized": None,
            "case_alias": None,
            "case_type": None,
            "dispute_type": None,
            "document_category": None,
            "document_type_normalized": None,
            "procedure_stage": None,
            "document_purpose": None,
            "legal_issue_primary": None,
            "legal_issue_secondary": None,
            "issue_tags": None,
            "claim_type": None,
            "party_our_side": None,
            "party_counterparty": None,
            "party_role": None,
            "law_firm_name_normalized": None,
            "institution_role": None,
            "country_region": None,
            "amount_mentioned": None,
            "claim_amount": None,
            "currency": None,
            "amount_context": None,
            "event_date": None,
            "date_type": None,
            "next_action_date": None,
            "timeline_summary": None,
            "lawyer_summary": None,
            "search_summary": None,
            "recommended_use": None,
            "review_priority": None,
            "review_priority_reason": None,
            "metadata_limitations": None,
            "needs_legal_review": None,
        }
        return (
            "You standardize internal legal document filenames for a Korean legal affairs team.\n"
            "Output JSON only and follow the schema exactly.\n\n"
            "CORE PRINCIPLE: The filename is NOT a keyword list. It must be a title that lets a legal professional\n"
            "instantly understand the document's nature and key issue — like a proper case file label.\n\n"
            "summary rules: exactly one sentence, max 80 characters, no greeting/signature/disclaimer boilerplate.\n"
            "Write a meaningful summary describing the specific legal action, parties, and subject matter.\n"
            "Example good summaries: '두바이 Sunrise 소송 관련 법률통지문 (Al Dhaheri)', '수단 대우아파트 PJT 토지매매 예비계약서', 'SIAC 중재 청구서 - 계약 위반 손해배상 청구'\n"
            "Use document body meaning, not filename tokens.\n"
            "Do not copy date prefixes, bracket tags, or page markers into summary.\n"
            "If OCR text is noisy, infer the legal core topic from repeated meaningful phrases.\n\n"
            "document_abstract rules (CRITICAL — this field is used as a knowledge base for AI chatbot Q&A):\n"
            "  Write 3–5 natural Korean sentences, max 400 characters total.\n"
            "  Cover ALL of the following that are present in the document:\n"
            "    1) 문서 성격·목적 (어떤 법적 행위를 담은 문서인가)\n"
            "    2) 당사자 (원고/피고, 갑/을, 발신인/수신인 등 실명 명시)\n"
            "    3) 핵심 법적 쟁점 또는 계약 내용\n"
            "    4) 주요 일자 (계약일·제출일·판결일 등)\n"
            "    5) 금액·규모 (통화 포함, 있는 경우)\n"
            "    6) 현재 상태 또는 결과 (진행중·완료·항소 등, 알 수 있는 경우)\n"
            "  Write in flowing sentences, not bullet points or pipe-delimited format.\n"
            "  Example: '두바이 Sunrise 건설 프로젝트 관련 손해배상 청구 계약서로, 포스코인터내셔널(갑)과 Al Dhaheri Trading LLC(을) 간 체결되었다. 공사 완료 후 대금 USD 2,340,000 미지급을 원인으로 하며, 계약 체결일은 2014년 5월 19일이다. SIAC 중재 절차가 진행 중이며, 대리 법무법인은 Pillsbury Winthrop이다.'\n"
            "  If OCR quality is poor, write what can be inferred with a note: '(OCR 품질 낮아 일부 내용 불명확)'\n\n"
            "doc_type rules (CRITICAL — pick exactly ONE from this list):\n"
            "  소송진행보고  — 소송 현황·일정·심리기일 보고, 변호사 의견 보고\n"
            "  심리결과보고  — 판결문, 결정문, 심리 결과 요약\n"
            "  법원명령      — 법원 명령·결정·가처분·가압류 결정\n"
            "  상대방서면    — 상대방이 제출한 소장·준비서면·답변서\n"
            "  증거서류      — 계약서·영수증·인증서·등기 등 증거로 쓰이는 원본 문서\n"
            "  법률의견      — 변호사 의견서, 내부 검토 의견, 법적 리스크 검토\n"
            "  계약서        — 계약서·협약서·양해각서·합의서·위임장 원본\n"
            "  통지서        — 법률통지·내용증명·청구서·이행촉구서·품의서\n"
            "  기타          — 위 8가지에 해당하지 않을 때만 사용\n\n"
            "document_title rules:\n"
            "  - Max 10 chars. Derived from doc_type + core legal issue of this specific document.\n"
            "  - Use Korean. Use English ONLY for: proper nouns, original document title, legally critical original-language terms.\n"
            "  - DO NOT use generic words alone: '문서', 'document', 'submission', 'hearing', 'delay' as standalone title.\n"
            "  - Good examples: '관세소송보고', '가압류결정', '위임장', '세관심리결과', 'Settlement Agreement', 'Power of Attorney'\n\n"
            "document_date rules (CRITICAL — follow this order strictly):\n"
            "  1. The filename often contains the most reliable date (e.g. _200303, _20200312). Treat it as a strong prior.\n"
            "  2. Override with a body date ONLY if it clearly shows issue/signing date near the top AND within 5 years of filename date.\n"
            "  3. Do NOT use reference dates, cited case dates, or dates predating the filename date by more than 5 years.\n"
            "  4. If multiple dates appear, prefer the date nearest the document header or signature block.\n"
            "  5. If you chose a body date over the filename date, briefly note why in the 'reason' field (in Korean).\n"
            "  6. Format: YYMMDD exactly 6 digits (e.g. 18 May 2014 → 140518, 2012-06-30 → 120630).\n"
            "     NEVER return 8-digit YYYYMMDD. NEVER return separators.\n"
            "     If only year+month are known, use day=01 (e.g. April 1993 → 930401).\n"
            "     If no date found, return empty string.\n\n"
            "english_keyword rules:\n"
            "  - Use English ONLY for: (a) proper nouns, (b) original document title in English, (c) legally critical original-language terms with no good Korean equivalent.\n"
            "  - DO NOT use English for general legal concepts that have natural Korean equivalents.\n"
            "  - DO NOT use: company names (Daewoo, Posco, Sunrise, NAM, Kwanika, MCC, Rusconi, Pillsbury), generic words (international, corporation, company, limited, com).\n"
            "  - If multiple legal terms apply, join up to 3 with commas (max 6 words, e.g. 'Liquidation, Customs Litigation, Secondary Tax Liability').\n"
            "  - Return empty string if no English term is genuinely necessary.\n\n"
            "reason rules:\n"
            "  - Write in Korean only. Briefly explain the naming decision or any uncertainty.\n"
            "  - Example: '본문 서명란 날짜가 파일명 날짜보다 명확하여 본문 기준으로 선택', '문서 제목 불명확하여 내용 기반으로 추론'\n"
            "  - Leave empty string if there is nothing notable to explain.\n\n"
            "Legal metadata extraction rules (for chatbot search index):\n"
            "  CRITICAL CONSTRAINTS — strictly follow:\n"
            "  - Use null for ANY field that is uncertain or not explicitly stated in the document.\n"
            "  - Do NOT infer legal conclusions, win probability, liability, or judgment interpretation.\n"
            "  - Extract amounts, dates, parties, institutions ONLY when explicitly stated in the text.\n"
            "  - If OCR/text quality is poor, use null liberally and note in metadata_limitations.\n\n"
            "  case_name_normalized: standardized case name. Match folder name convention if identifiable.\n"
            "  case_alias: short alias or abbreviation for the case (null if none).\n"
            "  case_type: SINGLE value from [소송, 중재, 계약검토, 자문, 클레임, 보고]. null if unclear.\n"
            "  dispute_type: MULTIPLE allowed, comma-separated. Values from [미수금, 계약불이행, 계약해지, 손해배상, 품질하자, 납기지연, 관할, 준거법, 기타].\n"
            "  document_category: SINGLE from [소송문서, 중재문서, 계약문서, 자문문서, 보고문서, 통지문, 기타].\n"
            "  document_type_normalized: standardized document type (e.g. '준비서면', '의견서', 'Settlement Agreement').\n"
            "  procedure_stage: SINGLE from [사전통지, 소제기, 답변, 증거제출, 심리, 판결, 판정, 합의, 집행, 종결, 기타].\n"
            "  document_purpose: one sentence — why was this document created?\n"
            "  legal_issue_primary: the single most important legal issue in Korean.\n"
            "  legal_issue_secondary: up to 3 secondary issues, comma-separated. null if none.\n"
            "  issue_tags: search keywords, semicolon-separated (e.g. '손해배상;계약위반;SIAC중재;가압류'). Max 8 tags.\n"
            "  claim_type: MULTIPLE allowed, comma-separated. Values from [금전청구, 손해배상청구, 가압류, 계약해지, 이행청구, 방어, 기타].\n"
            "  party_our_side: our company or affiliate name exactly as stated in document. null if not identifiable.\n"
            "  party_counterparty: counterparty name exactly as stated. null if not identifiable.\n"
            "  party_role: MULTIPLE allowed, comma-separated (e.g. '원고,피고' or '신청인,피신청인,채권자').\n"
            "  law_firm_name_normalized: law firm name, normalized. null if not mentioned.\n"
            "  institution_role: role of institution (e.g. '법원', '중재기관', '당사 대리인', '상대방 대리인'). null if none.\n"
            "  country_region: country or region most relevant to this dispute (e.g. '두바이', '수단', '대한민국').\n"
            "  amount_mentioned: true if any monetary amount is explicitly stated in the text, false otherwise.\n"
            "  claim_amount: main claimed or mentioned amount as string (e.g. 'USD 2,340,000'). null if none.\n"
            "  currency: currency code (e.g. 'USD', 'KRW', 'AED'). null if no amount.\n"
            "  amount_context: what the amount represents in Korean (e.g. '미지급 공사대금', '손해배상 청구액'). null if none.\n"
            "  event_date: most important event date in YYMMDD format (same rules as document_date). null if none.\n"
            "  date_type: SINGLE from [작성일, 접수일, 심리일, 판결일, 예정일, 기타]. null if no date.\n"
            "  next_action_date: next scheduled date if explicitly mentioned, YYMMDD format. null if none.\n"
            "  timeline_summary: one Korean sentence — what role does this document play in the case timeline?\n"
            "  lawyer_summary: 2–3 Korean sentences for a lawyer selecting documents for review.\n"
            "  search_summary: keyword-focused Korean summary for chatbot retrieval. Max 200 characters.\n"
            "  recommended_use: one Korean sentence on how this document can be used (e.g. '계약 위반 입증 증거로 활용').\n"
            "  review_priority: SINGLE from [High, Medium, Low]. High = urgent or legally critical action required.\n"
            "  review_priority_reason: brief Korean reason for the priority level.\n"
            "  metadata_limitations: note extraction limits due to OCR issues, incompleteness, or uncertainty. null if none.\n"
            "  needs_legal_review: true if a lawyer should personally review this document; false otherwise.\n\n"
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


# 하위 호환성을 위한 별칭
OllamaClient = LLMClient
