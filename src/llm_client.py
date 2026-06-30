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
        # Gemini (Google Generative Language API) 설정
        gemini_cfg = config["llm"].get("gemini", {})
        self.model = str(gemini_cfg.get("model", "gemini-2.5-flash"))
        self.base_url = str(
            gemini_cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta")
        ).rstrip("/")
        # 키: config 우선, 없으면 .env의 GEMINI_API_KEY → GOOGLE_API_KEY 순
        self.api_key = str(
            gemini_cfg.get("api_key", "")
            or os.getenv("GEMINI_API_KEY", "")
            or os.getenv("GOOGLE_API_KEY", "")
        )
        rules_path = project_root / "data" / "naming_rules.md"
        self.naming_rules = rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""

    def extract_metadata(self, payload: dict) -> dict | None:
        if not self.enabled:
            return None
        if not self.api_key:
            return None
        prompt = self._build_prompt(payload)
        return self._call_gemini(prompt)

    def test_connection(self) -> dict:
        """Gemini API 키/모델 연결을 가볍게 점검한다. UI·CLI에서 사용.

        반환: {ok, model, base_url, status?, message?, error?}
        """
        result: dict = {"ok": False, "model": self.model, "base_url": self.base_url}
        if not self.api_key:
            result["error"] = ".env(또는 config.yaml)에 GEMINI_API_KEY가 설정되지 않았습니다."
            return result
        # 키 노출 방지를 위해 마스킹된 미리보기 제공
        key = self.api_key
        result["key_preview"] = (key[:6] + "…" + key[-4:]) if len(key) > 12 else "****"
        try:
            import requests
            resp = requests.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": "Reply with the single word OK."}]}],
                    "generationConfig": {"temperature": 0, "maxOutputTokens": 5},
                },
                timeout=20,
            )
            result["status"] = resp.status_code
            if resp.ok:
                result["ok"] = True
                result["message"] = f"연결 성공 (모델 응답: {self._extract_text_from_gemini(resp.json()) or 'OK'})"
                return result
            # 오류 메시지 추출
            try:
                err = resp.json().get("error", {}).get("message") or resp.text[:300]
            except Exception:
                err = resp.text[:300]
            if resp.status_code in (400, 401, 403):
                result["error"] = f"인증/권한 실패({resp.status_code}): API 키가 올바르지 않거나 권한이 없습니다. {err}"
            elif resp.status_code == 404:
                result["error"] = f"모델/엔드포인트를 찾을 수 없음(404): 모델명 '{self.model}' 또는 base_url을 확인하세요. {err}"
            elif resp.status_code == 429:
                result["error"] = f"요청 한도 초과(429): {err}"
            else:
                result["error"] = f"HTTP {resp.status_code}: {err}"
            return result
        except Exception as exc:
            result["error"] = f"연결 오류: {type(exc).__name__}: {exc}"
            return result

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
            headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
            resp = requests.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                headers=headers,
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 30},
                },
                timeout=30,
            )
            if resp.ok:
                return self._extract_text_from_gemini(resp.json())
        except Exception:
            pass
        return ""

    def _call_gemini(self, prompt: str) -> dict | None:
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        try:
            import requests

            response = requests.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                headers=headers,
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": self.temperature,
                        # JSON 스키마 응답을 강제해 파싱 신뢰도를 높인다
                        "responseMimeType": "application/json",
                    },
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            raw = self._extract_text_from_gemini(response.json())
            return self._parse_json(raw or "")
        except Exception:
            return None

    def _build_prompt(self, payload: dict) -> str:
        schema = {
            "summary": "",
            "document_abstract": "",
            "document_title": "",
            "original_title": "",
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
            "CONTEXT — these are NON-CORE '기타문서' (miscellaneous documents), NOT core pleadings.\n"
            "  Apply legal classification LOOSELY. Do NOT force a document into a legal category.\n"
            "  Only assign a category label when the document CLEARLY matches it; otherwise default to '기타'\n"
            "  and name the document by its own concrete subject.\n\n"
            "doc_type rules (a WEAK hint, not mandatory — choose ONE or '기타'):\n"
            "  Reference taxonomy (use ONLY on clear keyword/structure match):\n"
            "    소장      — complaint, petition, summons, writ of summons, notice of appeal (강한 단서)\n"
            "    답변서    — answer, defence/defense, jawapan, response/reply to a claim\n"
            "    준비서면  — motion(강한 단서), opposition, memorandum, brief, submission, affidavit, application\n"
            "    판결      — judgment, decision, order, decree, sentença/sentencia, ordonnance, signed/court order\n"
            "    검토의견서 — opinion, legal opinion, counsel memo, advisory, analysis, advice\n"
            "    내부보고서 — 내부 보고·현황·요약 report (internal)\n"
            "    합의서    — agreement, settlement, release, rule 11 agreement, MOU\n"
            "    진술서    — witness statement, affidavit/declaration of a person\n"
            "    기타      — DEFAULT. subpoena, exhibit, transcript, certificate, invoice, notice, deed,\n"
            "                form, notification, and anything not clearly above. Most 기타문서 fall here.\n\n"
            "document_title rules:\n"
            "  - Korean, concise (≈ up to 12 chars / 30 if it is the document's own proper name).\n"
            "  - For a clearly-classified doc: use the category + specific issue (예: '가압류결정', '검토의견서').\n"
            "  - For '기타' (default): use the document's OWN specific subject/name faithfully\n"
            "    (예: '세관통관확인서', '대금영수증', '여권사본', '공증인증서').\n"
            "  - Use English ONLY for proper nouns or untranslatable terms (예: 'Power of Attorney').\n"
            "  - STRICTLY FORBIDDEN: vague/umbrella words such as '관련문서', '관련 문서', '문서', '자료',\n"
            "    'document', 'related', 'submission' used alone. Be specific.\n\n"
            "original_title rules (해외건 only):\n"
            "  - If this is an overseas document with an English/original-language title visible in the body\n"
            "    (e.g. a header like 'POWER OF ATTORNEY', 'CERTIFICATE OF INCORPORATION'), return it concisely.\n"
            "  - Domestic Korean documents OR no clear original title → return empty string \"\".\n"
            "  - Do NOT duplicate document_title; do NOT invent a title.\n\n"
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
    def _extract_text_from_gemini(data: dict) -> str:
        """Gemini generateContent 응답에서 텍스트를 추출한다.

        구조: candidates[].content.parts[].text
        """
        texts: list[str] = []
        for candidate in data.get("candidates", []) or []:
            content = candidate.get("content", {}) or {}
            for part in content.get("parts", []) or []:
                text = part.get("text")
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


def _cli_test_key() -> int:
    """CLI: Gemini API 키 연결 테스트.

    사용법:  python src/llm_client.py   (config.yaml + .env 사용)
    """
    from config_loader import load_config

    project_root = Path(__file__).resolve().parent.parent
    config = load_config(str(project_root / "config.yaml"))
    client = LLMClient(config, project_root)
    print("[llm-test] Gemini API 키 연결을 확인합니다...")
    print(f"  base_url = {client.base_url}")
    print(f"  model    = {client.model}")
    res = client.test_connection()
    if res.get("key_preview"):
        print(f"  api_key  = {res['key_preview']}")
    if res.get("ok"):
        print(f"\n✅ {res.get('message', '연결 성공')}")
        return 0
    print(f"\n❌ 실패: {res.get('error', '알 수 없는 오류')}")
    print("  • .env의 GEMINI_API_KEY 값을 확인하세요.")
    print("  • config.yaml의 llm.gemini.model / base_url 을 확인하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli_test_key())
