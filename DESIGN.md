# 법무 문서 DB 파일명 자동화 시스템 설계 문서

> 최종 업데이트: 2026-05-13

---

## 이 문서의 구성

| 섹션 | 대상 독자 |
|------|----------|
| 1~4 | **현업 담당자** — 무엇을 만들었고, 어떻게 쓰는지 |
| 5~14 | **개발·운영 담당자** — 기술 구조, 파이프라인, API, 설정 방법 |

---

# PART 1. 현업 담당자용 안내

## 1. 이 시스템은 무엇인가요?

**한 줄 요약**: "SharePoint 법무 폴더의 파일들을 AI가 읽고, 표준 파일명으로 자동 제안·변경해주는 웹 기반 시스템"

법무 문서의 파일명이 작성자마다 달라 검색과 관리가 어려운 문제를 해결합니다.  
SharePoint에 저장된 PDF·Word·PPT·Excel·이미지·이메일 파일을 자동으로 분석해 아래 형식의 표준 파일명을 제안합니다.

```
법무실_사건명_문서명_날짜.확장자
예) 법무실_두바이 Sunrise 소송_법률통지_Legal Notice_140518.pdf
```

담당자는 웹 브라우저에서 AI 제안 파일명을 검토·승인하고, 버튼 하나로 SharePoint 파일명을 일괄 변경합니다. 실수했을 경우 롤백(원래 이름 복원)도 웹에서 바로 실행합니다.

---

## 2. 작업 흐름 (4단계 Web UI)

서버 실행 후 브라우저에서 `http://127.0.0.1:8000` 접속.

```
┌─────────────────────────────────────────────────────────────┐
│ STEP 1  사이트 조회                                          │
│  - SharePoint URL + 루트 폴더 입력 후 [현황 조회] 클릭       │
│  - 실시간 폴더 탐색 진행 표시                                │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ STEP 2  사전 점검                                            │
│  - 폴더 트리, 파일 수, 확장자 분포, 예상 비용 확인           │
│  - 처리할 폴더를 체크박스로 선택 (미선택 시 전체)            │
│  - 🔄 새로고침으로 변경된 파일만 델타 업데이트 가능          │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ STEP 3  AI 분석                                              │
│  - [분석 시작] 클릭 → 실시간 진행 스트리밍 표시              │
│  - OCR + LLM이 각 파일에서 문서 정보 추출                    │
│  - 완료 시 Excel 다운로드 가능                               │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ STEP 4  검토 / 확정                                          │
│  - 자동승인 / 검토필요 / 중복 필터로 분류 확인               │
│  - 승인할 파일 체크 후 [선택 파일명 변경] 클릭               │
│  - 행 단위로 처리중 → 완료 상태 순차 표시                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ STEP 5  변경 / 롤백                                          │
│  - 변경 이력 파일 목록 항상 표시 (조회 완료 여부 무관)       │
│  - 페이지당 5개 표시, 이전/다음 페이지 탐색 가능             │
│  - [롤백 실행] 클릭 → 폴더 범위 선택 후 선택적 복원         │
│  - [🗑] 삭제 버튼으로 불필요한 이력 파일 제거 가능           │
└─────────────────────────────────────────────────────────────┘
```

### 서버 실행 방법

```bash
python src/api_server.py
# → http://127.0.0.1:8000 자동 열림
```

> **CLI 모드도 지원** (레거시): `python src/main.py --mode analyze --source sharepoint`

---

## 3. 파일명 명명 규칙

### 기본 패턴

```
법무실_사건명_문서명_날짜.확장자
```

### 각 구성 요소 결정 방법

| 구성 요소 | 결정 방식 | 예시 |
|----------|----------|------|
| 조직명 | 고정값 (`법무실`) | 법무실 |
| 사건명 | **폴더 구조 3번째 레벨** (LLM 무시) | 두바이 Sunrise 소송 |
| 문서명 | 문서 종류 키워드 매핑 → LLM 결과 → 요약 추출 | 법률통지 |
| 날짜 | **문서 본문 날짜 우선** → 파일명 날짜 보조 | 140518 (YYMMDD) |

### 폴더 구조와 사건명 대응

```
법무DB (테스트)          ← 루트 폴더 (제외)
  소송 및 중재           ← 대분류 (파일명 미사용)
  두바이 Sunrise 소송    ← 사건명 ★ 파일명에 사용
    1. 사건기록          ← 소분류 (파일명 미사용)
      파일.pdf
```

### 수동 검토 대상 (UI에서 "검토필요" 표시)

- 날짜 불명 (`date_unknown`)
- 텍스트 추출 실패
- 신뢰도 < 0.78
- 중복 파일명 → 자동으로 `(1)`, `(2)` 일련번호 부여 후 "중복" 표시

---

## 4. 출력 파일 구조

| 경로 | 내용 |
|------|------|
| `output/review/rename_review_날짜.xlsx` | 분석 결과 리뷰 Excel |
| `output/rollback/rollback_mapping_날짜.json` | 롤백용 원본→변경 매핑 |
| `output/logs/rename_log_날짜.jsonl` | 이름 변경 상세 로그 |
| `output/logs/rename_result_날짜.csv` | 이름 변경 결과 요약 |
| `temp/analysis_cache.json` | 분석 캐시 (재실행 속도 향상) |
| `temp/sp_downloads/` | SharePoint에서 다운로드한 임시 파일 |

---

# PART 2. 개발·운영 담당자용 기술 문서

## 5. 시스템 아키텍처

```
[브라우저 Web UI]  http://127.0.0.1:8000
  frontend/index.html + app.js + style.css
        │  HTTP REST + SSE(Server-Sent Events)
        ▼
[FastAPI 서버]  src/api_server.py
  - /api/survey/stream     : 폴더 트리 수집 (SSE)
  - /api/analyze/start     : 분석 시작 (비동기)
  - /api/analyze/stream    : 분석 진행 (SSE)
  - /api/rename            : 파일명 변경
  - /api/rollback          : 롤백 (folder_paths 필터 지원)
  - /api/rollback/preview  : 롤백 대상 사전 확인
        │
        ├──► [sp_survey.py]        폴더 트리 집계 / 비용 예측
        ├──► [scanner.py]          FileRecord 목록 생성
        ├──► [extractors]          텍스트·메타데이터 추출
        │      extractor_pdf.py    pypdf → Advanced OCR → ocrmypdf
        │      extractor_docx.py   python-docx
        │      extractor_pptx.py   python-pptx
        │      extractor_xlsx.py   openpyxl
        │      extractor_image.py  Tesseract OCR
        │      extractor_eml.py    이메일 파싱
        ├──► [llm_client.py]       Gemini 2.5 Flash 호출
        ├──► [naming_engine.py]    파일명 제안 + 중복 처리
        ├──► [rename_executor.py]  파일명 변경 실행
        ├──► [rollback_executor.py] 롤백 실행
        └──► [sharepoint_client.py] MS Graph API (인증·다운로드·변경)

[SharePoint]  MS Graph API (Files.ReadWrite.All)
```

---

## 6. 파일 구성

| 파일 | 역할 |
|------|------|
| `src/api_server.py` | **FastAPI 웹 서버** — REST API + SSE 스트리밍, 분석 세션 관리 |
| `src/sp_survey.py` | **현황 조회** — 폴더 트리 빌드, 파일 수 집계, 비용 예측 |
| `src/main.py` | CLI 진입점. analyze / rename / rollback 모드 |
| `src/sharepoint_client.py` | MS Graph API 클라이언트 (인증, 폴더 트리, 다운로드, 이름 변경) |
| `src/scanner.py` | 로컬 / SharePoint 파일 목록 스캔 → FileRecord 생성 |
| `src/models.py` | 데이터 모델 (FileRecord, ExtractionResult, NamingResult, AnalysisRecord) |
| `src/naming_engine.py` | 파일명 제안 로직, 중복 처리 |
| `src/llm_client.py` | OpenAI API 호출 및 프롬프트 관리 |
| `src/excel_writer.py` | 리뷰 Excel 생성 (MIP 암호화 대응) |
| `src/rename_executor.py` | 파일명 변경 실행 (SharePoint / 로컬), rollback 매핑 생성 |
| `src/rollback_executor.py` | 파일명 롤백 실행, folder_paths 범위 필터 |
| `src/extractor_pdf.py` | PDF 텍스트 추출 (텍스트레이어 → Advanced OCR → ocrmypdf) |
| `src/extractor_docx.py` | Word 문서 추출 |
| `src/extractor_pptx.py` | PowerPoint 추출 |
| `src/extractor_xlsx.py` | Excel 추출 (openpyxl) |
| `src/extractor_image.py` | 이미지 OCR (Tesseract) |
| `src/extractor_eml.py` | 이메일(.eml) 파싱 |
| `src/ocr_advanced.py` | 고품질 OCR 파이프라인 (400 DPI, 전처리) |
| `src/ocr_runner.py` | ocrmypdf 래퍼 |
| `src/config_loader.py` | config.yaml 로딩 및 경로 처리 |
| `frontend/index.html` | Web UI HTML |
| `frontend/app.js` | Web UI 로직 (폴더 트리, 분석, 검토, 이름 변경, 롤백) |
| `frontend/style.css` | Web UI 스타일 |
| `config.yaml` | 전체 설정 파일 |
| `data/naming_rules.md` | LLM 프롬프트에 포함되는 파일명 규칙 |
| `test_sp_single.py` | 단일 파일 SharePoint 분석 테스트 도구 |

---

## 7. REST API 엔드포인트

### 현황 조회

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/config` | config.yaml의 기본 site_url, root_folder 반환 |
| `GET` | `/api/survey/stream` | SharePoint 폴더 트리 수집 (SSE). 파라미터: `site_url`, `root_folder` |
| `GET` | `/api/survey/cache` | 캐시된 현황 조회 결과 반환 (`available`, `scanned_at`, `survey_data`) |
| `GET` | `/api/survey/delta/stream` | delta 쿼리로 변경분만 조회 (SSE, 수초). 결과: `added`/`deleted`/`modified` 건수 |

`survey/stream` 완료 이벤트 응답 (요약):
```json
{
  "type": "complete",
  "data": {
    "site_url": "...",
    "total_files": 235,
    "total_size_human": "1.2 GB",
    "supported_files": 220,
    "ext_breakdown": [{"ext": ".pdf", "count": 120, "supported": true}, ...],
    "cost_estimate": {"llm_files": 220, "ocr_files": 140, "est_cost_usd": 0.23, "est_duration": "12분 50초"},
    "folder_tree": { "name": "법무DB (테스트)", "children": [...], "total_files": 235 }
  }
}
```

### 분석

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/analyze/start` | 분석 시작. Body: `AnalyzeRequest` |
| `POST` | `/api/analyze/stop` | 분석 중단 |
| `GET` | `/api/analyze/stream` | 분석 진행 상황 (SSE) |
| `GET` | `/api/analyze/results` | 최종 결과 (conflict 반영) |
| `GET` | `/api/analyze/status` | 현재 상태 요약 |
| `GET` | `/api/analyze/download/excel` | 리뷰 Excel 다운로드 |

`AnalyzeRequest` 스키마:
```json
{
  "site_url": "https://...",
  "root_folder": "법무DB (테스트)",
  "max_files": 0,
  "fast": false,
  "clear_cache": false,
  "folder_paths": ["소송 및 중재/두바이 Sunrise 소송"]
}
```
- `folder_paths` 빈 배열 → 전체 처리
- `folder_paths` 지정 → 해당 폴더(하위 포함)만 처리

분석 SSE 이벤트 타입: `scanning` → `start` → `record`(×N) → `complete` | `cancelled` | `error`

### 파일명 변경 / 롤백

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/rename` | 선택된 파일 일괄 변경. Body: `RenameRequest` |
| `GET` | `/api/rollback/list` | 롤백 파일 목록 (최근 20개) |
| `POST` | `/api/rollback/preview` | 롤백 대상 사전 확인 (실행 없음) |
| `POST` | `/api/rollback` | 롤백 실행. `folder_paths` 지정 시 범위 복원 |

`RollbackRequest` 스키마:
```json
{
  "rollback_file": "/path/to/rollback_mapping_날짜.json",
  "site_url": "https://...",
  "root_folder": "법무DB (테스트)",
  "folder_paths": []
}
```
- `folder_paths` 빈 배열 → 전체 복원
- `folder_paths` 지정 → 해당 폴더 파일만 복원

---

## 8. Web UI 주요 기능

### 폴더 범위 선택
- 폴더 트리의 체크박스로 처리 범위 선택
- 부모 체크 → 하위 전체 자동 체크 (cascade down)
- 하위 일부만 체크 → 부모 indeterminate 표시 (cascade up)
- `getSelectedPaths()`: 중복 경로 제거 (상위 선택 시 하위 생략)
- 선택 범위는 **분석**·**롤백** 모두 적용

### 폴더별 작업 이력 배지
- 파일명 변경 완료 후 해당 폴더에 `✓ YYYY.MM.DD HH:mm` 배지 표시
- localStorage(`legaldb_rename_hx`)에 폴더 경로 → 마지막 변경 일시 저장
- 브라우저 새로고침 후에도 유지

### 롤백 실행 이력
- localStorage(`legaldb_rollback_hx`)에 파일명·실행 일시·복원 수·폴더 범위 저장
- 롤백 목록에 "✓ 실행됨" 배지 + 실행 일시·복원 건수·범위 표시
- 재실행 가능

### 파일명 변경 시 순차 상태 표시
- 변경 버튼 클릭 → 선택 행 전체 "처리중"(pulsing) 표시
- 완료 순서대로 40ms 스태거로 "완료"/"실패" 전환

### 현황 조회 캐시 + 델타 새로고침
- 현황 조회(전체 스캔) 완료 시 결과를 `temp/survey_cache.json`에 저장 (deltaLink + file_index 포함)
- **[현황 조회] 클릭 시 기존 캐시 파일 즉시 삭제 후 전체 재스캔** — 캐시 재사용 없음
- 앱 재시작 시 캐시가 있으면 즉시 표시 (0초) — 전체 스캔 불필요
- Step 1 "현황 조회" 버튼 오른쪽에 "마지막 조회: YYYY.MM.DD HH:mm" + [🔄 새로고침] 버튼 인라인 표시 (세로 구분선으로 구분)
- [현황 조회] 클릭 시 캐시 바·델타 요약도 즉시 숨김 (완료 후 새 시각으로 재표시)
- [새로고침] 클릭 → `/api/survey/delta/stream` 호출 → MS Graph delta 쿼리로 변경분만 조회 (수초)
- 변경 결과를 색상 배지로 표시: `+N개 추가` (녹색) / `-N개 삭제` (빨강) / `N개 변경` (노랑) — 하단 종합 요약
- **폴더별 인라인 배지**: 변경된 폴더 행에 `+N` / `-N` / `변경 N` 소형 배지 표시 (by_folder 집계)
- file_index 및 통계(총 파일수·처리가능·불가)를 자동 업데이트 후 캐시 갱신

### 현재 파일명 클릭 → SharePoint 문서 열기
- 검토 테이블의 현재 파일명 셀을 클릭하면 SharePoint 문서 뷰어로 이동 (새 탭)
- `sharepoint_web_url` 값이 있는 경우 `<a target="_blank">` 링크로 렌더링
- `sharepoint_web_url`이 없는 경우(로컬 모드) 일반 텍스트로 표시

### 제안 파일명 수동 수정 (연필 아이콘)
- 검토 테이블의 제안 파일명 셀 오른쪽에 연필 아이콘 상시 표시 (14px, 파란색 굵게)
- 클릭 시 인라인 편집 모드 전환: 입력창(셀 너비의 2배, 최소 420px) + 저장/취소 버튼
- 저장 시 `rec.suggested_file_name`, `rec.suggested_full_path` 갱신, `rec.manually_edited = true` 설정
- 수동 수정된 행에 `✎` 마크 표시
- 파일명 변경 실행 시 수동 수정된 이름 그대로 적용

### 수동 수정 Excel 반영
- 파일명 변경 실행 시 `manually_edited: true/false`를 서버에 전송
- 수동 수정 항목이 있으면 서버가 `AnalysisRecord` 갱신 후 Excel 재생성
- 재생성된 Excel의 변경 내용:
  - `suggested_file_name` 컬럼: 수동 수정된 파일명 반영
  - `manually_edited` 컬럼 (HEADERS 추가): 수동 수정 행에 `✓` 표기
  - 수동 수정 셀: 파란색 배경 + 굵은 텍스트 강조
- 롤백 JSON은 실제 변경된 파일명 기록 → 수동 수정 여부와 무관하게 롤백 정상 작동

### 새 분석 시작 시 초기화
- 분석 시작 버튼 클릭 → 이전 검토 테이블 즉시 숨김
- 분석 완료 후 새 결과로 검토 섹션 재표시

---

## 9. 데이터 모델

### FileRecord
| 필드 | 설명 |
|------|------|
| `seq` | 순번 |
| `root_path` | 루트 폴더 절대 경로 |
| `original_full_path` | 전체 경로 (로컬) 또는 표시용 경로 (SP) |
| `original_dir_path` | 폴더 경로 |
| `original_file_name` | 원본 파일명 |
| `file_extension` | 확장자 |
| `file_size` | 파일 크기 (bytes) |
| `relative_path_from_root` | 루트 폴더 기준 상대 경로 (사건명 추출 + 폴더 범위 필터링에 사용) |
| `sharepoint_item_id` | SharePoint DriveItem ID (SP 모드만) |
| `sharepoint_web_url` | SharePoint 파일 URL (SP 모드만) |

### NamingResult (주요 필드)
| 필드 | 설명 |
|------|------|
| `suggested_file_name` | 제안 파일명 |
| `extracted_case_name` | 사건명 (폴더 구조에서 추출) |
| `extracted_document_title` | 문서명 |
| `extracted_date` | 날짜 (YYMMDD) |
| `confidence` | 신뢰도 (0.0~1.0) |
| `needs_manual_review` | 수동 검토 필요 여부 |
| `conflict_detected` | 중복 파일명 감지 여부 |
| `rename_status` | 이름 변경 상태 (`success`, `error` 등) |
| `manually_edited` | 사용자 수동 수정 여부 (UI에서 연필 아이콘으로 수정 시 `True`) |
| `document_abstract` | AI 챗봇 지식베이스용 구조화 요약 (3~5문장, 최대 400자, 당사자·쟁점·일자·금액·상태 포함) |

### AnalysisRecord
| 필드 | 설명 |
|------|------|
| `file_record` | FileRecord |
| `extraction` | ExtractionResult |
| `naming` | NamingResult |
| `legal_metadata` | dict — LLM이 추출한 33개 법무 메타데이터 필드. 불확실 항목은 null. Excel 출력에만 포함. |

### AnalysisSession (메모리)
서버 내 단일 인스턴스. 분석 진행 상태, 결과 레코드, 오류 목록을 보관.  
`_session_lock`(threading.Lock)으로 스레드 안전 접근. SSE 구독자(`_analysis_subs`)에 이벤트 브로드캐스트.  
`all_analysis_records`: 분석 완료 후 `AnalysisRecord` 객체 리스트 보관 → 파일명 변경 후 Excel 재생성에 사용.

---

## 10. SharePoint 인증 흐름

```
1. MSAL 토큰 캐시 확인 (temp/sp_token_cache.json)
        │ 캐시 유효
        ▼
   토큰 재사용 (사용자 개입 없음)

        │ 캐시 없음 / 만료
        ▼
2. 디바이스 코드 발급
        │
        ▼
3. Selenium + Edge 자동 실행
   - 인증 URL 열기
   - 인증 코드 자동 입력
   - 계정 선택 (.env의 SP_EMAIL)
   - 비밀번호 입력 (.env의 SP_PASSWORD)
   - "로그인 상태 유지" 클릭
        │
        ▼
4. MSAL 토큰 수신 → 캐시 저장
        │
        ▼
5. 드라이브 ID 해석
   - config.yaml의 folder_sharing_url로 drive_id 자동 해석
   - Sites.Read.All 권한 불필요
```

서버 시작 시(`lifespan`) 사전 인증 시도 → 토큰 캐시 활성화  
이후 요청에서는 캐시 재사용, 필요 시에만 재인증.

---

## 11. OCR 파이프라인

PDF에 텍스트 레이어가 없거나 부족할 때 순서대로 시도합니다.

```
1. pypdf 텍스트 추출 (force_ocr_threshold_chars=80)
        │ 텍스트 충분
        ▼
   바로 사용

        │ 텍스트 부족
        ▼
2. Advanced OCR (ocr_advanced.py)
   - 400 DPI로 페이지 렌더링
   - 전처리: 그레이스케일, 대비, 이진화, 노이즈 제거, 샤프닝
   - Tesseract (--oem 1 --psm 6) → 실패 시 --psm 11 재시도
   - 결과 불량 시 본문 영역 자동 크롭 후 재시도
   - timeout_seconds 초과 시 처리된 페이지까지만 사용

        │ Advanced OCR 실패
        ▼
3. ocrmypdf 폴백 (fallback_to_ocrmypdf: true)
```

---

## 12. LLM 프롬프트 구조

Gemini 2.5 Flash에 전달되는 컨텍스트:

```
[시스템 지시]
- 표준 파일명 생성 역할
- summary, document_title, doc_type, document_date 등 JSON 스키마 준수
- 날짜 우선순위: 문서 본문 > 파일명 (CRITICAL 지시)
- 사건명은 폴더 구조에서 가져오므로 LLM 추출값 미사용

[naming_rules.md 내용]

[입력 페이로드]
- file_path, file_extension, parent_folder, relative_path
- extracted_text_excerpt (최대 llm_excerpt_chars자)
  - 앞 60% : 문서 앞부분 고정
  - 뒤 40% : 핵심 문장 추출 (키워드 스코어링)
```

파일당 예상 비용: Gemini 2.5 Flash 기준 약 **$0.0029** (입력 ~3,800 토큰 + 출력 ~700 토큰)  
※ 법무 메타데이터 33개 필드 추가 이후 기준. 이전: $0.00103 (입력 ~1,500 + 출력 ~400)

**법무 메타데이터 추출 (AI 챗봇 검색 인덱스용, Excel 전용)**  
LLM이 추가로 추출하는 33개 필드 (불확실 시 null 반환, 법적 결론 추론 금지):

| 구분 | 단일값 필드 | 복수값 필드 (쉼표 구분) |
|------|-------------|------------------------|
| 사건 | case_name_normalized, case_alias, case_type | dispute_type |
| 문서 | document_category, document_type_normalized, procedure_stage, document_purpose | — |
| 쟁점 | legal_issue_primary, legal_issue_secondary | issue_tags (세미콜론) |
| 청구 | — | claim_type |
| 당사자 | party_our_side, party_counterparty, law_firm_name_normalized, institution_role | party_role |
| 지역 | country_region | — |
| 금액 | amount_mentioned, claim_amount, currency, amount_context | — |
| 일자 | event_date, date_type, next_action_date | — |
| 요약 | timeline_summary, lawyer_summary, search_summary, recommended_use | — |
| 검토 | review_priority, review_priority_reason, metadata_limitations, needs_legal_review | — |

이 필드들은 `AnalysisRecord.legal_metadata` dict에 저장되며, Excel 출력(`to_excel_row()`)에만 포함됩니다.  
API 응답 (`/api/analyze/results`) 및 분석 캐시에는 포함되지 않습니다.

---

## 13. 기술 스택

| 구분 | 기술 | 비고 |
|------|------|------|
| 언어 | Python 3.12 | |
| **웹 서버** | **FastAPI + uvicorn** | REST API + SSE |
| **웹 프론트엔드** | **Vanilla JS + CSS** | 빌드 도구 없음 |
| SharePoint 연동 | MSAL (msal≥1.28.0) + MS Graph API | Files.ReadWrite.All |
| 브라우저 자동화 | Selenium + Edge | 디바이스 코드 자동 입력 |
| PDF 텍스트 추출 | pypdf, pypdfium2 | |
| OCR | Tesseract, ocrmypdf | |
| 이미지 처리 | Pillow, OpenCV | OCR 전처리 |
| Word/PPT 추출 | python-docx, python-pptx | |
| Excel 처리 | openpyxl, pywin32 | MIP 암호화 파일 대응 |
| AI 메타데이터 추출 | Google Gemini 2.5 Flash | .env GEMINI_API_KEY |
| HTTP 통신 | requests | |
| 설정 관리 | PyYAML | config.yaml |

---

## 14. 설정 파일 (config.yaml) 주요 항목

### SharePoint 설정
```yaml
sharepoint:
  tenant_id: "..."
  client_id: "..."
  site_url: "https://poscointl1.sharepoint.com/sites/DX-DB"
  drive_name: "Documents"
  root_folder: "법무DB (테스트)"
  folder_sharing_url: "https://..."   # drive_id 자동 해석용 (Sites.Read.All 불필요)
  token_cache_path: "./temp/sp_token_cache.json"
  ssl_verify: false                   # 회사 SSL 검사 프록시 환경
```

### LLM 설정
```yaml
llm:
  provider: "gemini"
  enabled: true
  temperature: 0.1
  timeout_seconds: 90
  gemini:
    model: "gemini-2.5-flash"
    base_url: "https://generativelanguage.googleapis.com/v1beta"
    api_key: ""   # 또는 .env의 GEMINI_API_KEY
```

### 성능 설정
```yaml
performance:
  workers: 6              # 병렬 처리 워커 수
  ocr_workers: 3          # 동시 OCR 작업 수
  llm_workers: 4          # 동시 LLM 호출 수
  extract_max_chars: 15000  # 텍스트 추출 최대 글자 수
  llm_excerpt_chars: 5000   # LLM에 전달하는 최대 글자 수
  fast_disables_ocr: true   # --fast 모드 시 OCR 비활성화
  fast_disables_llm: true   # --fast 모드 시 LLM 비활성화
```

### 파일명 설정
```yaml
naming:
  org_name: "법무실"
  confidence_threshold: 0.78
  max_filename_length: 180
```

---

## 15. 환경 변수 (.env)

| 변수명 | 설명 |
|--------|------|
| `GEMINI_API_KEY` | Google Gemini API 키 (`GOOGLE_API_KEY`로도 인식) |
| `SP_EMAIL` | SharePoint 로그인 계정 (Selenium 자동 로그인) |
| `SP_PASSWORD` | SharePoint 로그인 비밀번호 (Selenium 자동 로그인) |

---

## 16. 실행 명령어

### 웹 서버 (권장)
```bash
python src/api_server.py
# 브라우저에서 http://127.0.0.1:8000 접속
```

### CLI (레거시)
```bash
# 전체 분석
python src/main.py --mode analyze --source sharepoint

# 빠른 초안 (OCR + LLM 비활성화)
python src/main.py --mode analyze --source sharepoint --fast

# 일부 파일만
python src/main.py --mode analyze --source sharepoint --max-files 50

# 이름 변경
python src/main.py --mode rename --source sharepoint \
  --review-file "output/review/rename_review_YYYYMMDD_HHMMSS.xlsx"

# 롤백
python src/main.py --mode rollback --source sharepoint \
  --rollback-file "output/rollback/rollback_mapping_YYYYMMDD_HHMMSS.json"
```

### 단일 파일 테스트
```bash
python test_sp_single.py --sharing-url "https://poscointl1.sharepoint.com/:b:/s/DX-DB/..."
python test_sp_single.py --sharing-url "https://..." --fast
```

---

## 17. 개발 이력

| 날짜 | 내용 |
|------|------|
| 2026-04-16 | 초기 커밋: 법무 문서 DB 파일명 자동화 시스템 (로컬 모드) |
| 2026-04-16 | MS Graph API SharePoint 연동 추가 (`--source sharepoint`) |
| 2026-04-16 | MSAL 디바이스 코드 인증 + 토큰 캐시 구현 |
| 2026-04-16 | Selenium Edge 브라우저 자동 로그인 구현 |
| 2026-04-16 | SSL 검사 프록시 환경 대응 (`ssl_verify: false`) |
| 2026-04-16 | 단일 파일 테스트 도구 (`test_sp_single.py`) 추가 |
| 2026-04-17 | 사건명 결정 규칙 변경: LLM → 폴더 구조 3번째 레벨 고정 |
| 2026-04-17 | `folder_sharing_url`로 drive_id 자동 해석 (Sites.Read.All 불필요) |
| 2026-04-17 | 중복 파일명 자동 해결: `(1)`, `(2)` 일련번호 부여 |
| 2026-04-17 | LLM 날짜 추출 규칙 강화: 문서 본문 > 파일명 |
| 2026-04-17 | MIP 암호화 Excel 자동 처리 (win32com 직접 읽기) |
| 2026-04-26 | Ollama 코드 제거, OpenAI 단일 경로로 정리 |
| 2026-04-26 | DESIGN.md 초안 작성 |
| 2026-05-01 | **FastAPI 웹 서버 (`api_server.py`) 추가** — REST API + SSE |
| 2026-05-01 | **Web UI (`frontend/`) 구현** — 현황조회·분석·검토·변경·롤백 4단계 |
| 2026-05-01 | `sp_survey.py` 추가 — SharePoint 폴더 트리 빌드 및 비용 예측 |
| 2026-05-01 | `extractor_xlsx.py` 추가 (Excel 텍스트 추출) |
| 2026-05-05 | 폴더 체크박스 cascade 선택 + 분석 범위 필터링 |
| 2026-05-05 | 자연수 정렬 (`_natural_key`) — 폴더 번호 순 표시 |
| 2026-05-07 | 파일명 변경 시 행 단위 순차 상태 표시 (처리중 → 완료) |
| 2026-05-07 | 폴더별 마지막 파일명 변경 일시 배지 (localStorage 영속) |
| 2026-05-07 | 롤백 실행 이력 표시 (실행 일시 · 복원 수 · 폴더 범위) |
| 2026-05-11 | 롤백 폴더 범위 필터 구현 (`folder_paths` 파라미터) |
| 2026-05-11 | 새 분석 시작 시 이전 검토 결과 자동 초기화 |
| 2026-05-11 | 타임스탬프 배지 날짜+시간 표시, 절대 위치 컬럼 정렬 |
| 2026-05-11 | DESIGN.md 전면 업데이트 |
| 2026-05-11 | 제안 파일명 수동 수정 기능 (연필 아이콘 인라인 편집) |
| 2026-05-11 | 연필 아이콘 시각 개선: 파란색(`--blue-500`), 14px, drop-shadow |
| 2026-05-11 | 파일명 수동 편집 입력창 폭 2배 확장 (셀 너비의 200%, 최소 420px) |
| 2026-05-11 | 현재 파일명 클릭 시 SharePoint 문서 뷰어 새 탭 열기 (`sharepoint_web_url` 활용) |
| 2026-05-11 | 현황 조회 캐시 + MS Graph delta 새로고침 구현 (앱 재시작 시 즉시 표시, 재조회 수초) |
| 2026-05-11 | 수동 수정 내용 Excel 반영: `manually_edited` 컬럼 추가, 파일명 변경 실행 후 Excel 자동 재생성 |
| 2026-05-11 | `document_abstract` 필드 추가: AI 챗봇 지식베이스용 구조화 요약 (당사자·쟁점·일자·금액·상태, 3~5문장 400자 이내) |
| 2026-05-12 | delta API 중복 항목 버그 수정: `_items_to_tree`에 ID 기준 중복 제거 추가 (폴더 중복·파일 수 과다 계상 해결) |
| 2026-05-12 | 새로고침 버튼 위치 이동: 폴더 구조 카드 → Step 1 "현황 조회" 버튼 영역 인라인 표시 |
| 2026-05-12 | 전체 스캔 완료 시 `scanned_at` SSE 이벤트 포함 → 버튼 즉시 표시 |
| 2026-05-12 | [현황 조회] 클릭 시 기존 캐시 삭제 후 완전 재스캔 (캐시 재활용 없음), 완료 토스트 추가 |
| 2026-05-12 | 델타 새로고침 시 폴더별 인라인 변경 배지 표시 (`+N` 추가·`-N` 삭제·`변경 N`) |
| 2026-05-12 | 스캔 시작 시 스캐닝 섹션으로 자동 스크롤, 카운터 초기화 |
| 2026-05-12 | 롤백 섹션을 `sec-results` 밖으로 독립 — 현황 조회 없이도 항상 표시, 페이지 로드 시 자동 로드 |
| 2026-05-12 | Step 2 "사전 점검" 카드 헤더 추가 — 현황 조회 결과 섹션에 스텝 배지·제목 표시 |
| 2026-05-12 | 롤백 이력 페이지네이션 구현 — 페이지당 5개, 이전/다음 탐색 |
| 2026-05-12 | 롤백 이력 파일 삭제 기능 — DELETE `/api/rollback/delete`, 경로 보안 검증 포함 |
| 2026-05-12 | 롤백 파일 조건부 생성 — 성공한 파일명 변경이 1개 이상일 때만 `rollback_mapping_*.json` 저장 (빈 파일 누적 방지) |
| 2026-05-12 | 롤백 API 분석 중 차단 — `scanning`/`running` 상태일 때 `/api/rollback` 요청 거부, 분석 완료·중단 후 실행 유도 |
| 2026-05-12 | 검토필요 사유 표시 — `evaluate_manual_review()`가 `(bool, str)` 반환, `result.reason`에 `[사유]` 형식으로 자동 추가 |
| 2026-05-12 | 사유 전면 한글화 — `main.py` 하드코딩 3곳, `api_server.py` 예외 메시지, LLM 프롬프트 `reason` 필드 한글 작성 지시 추가 |
| 2026-05-12 | **법무 메타데이터 33개 필드 추가** — AI 챗봇 검색 인덱스용, Excel 전용 출력. LLM 스키마 확장, `AnalysisRecord.legal_metadata` dict, `_extract_legal_metadata()`, `CACHE_SCHEMA_VERSION` 4→5, 컬럼 너비·wrap_text 적용 |
| 2026-05-12 | 파일당 LLM 예상 비용 업데이트: $0.00103 → $0.0028 (sp_survey.py 반영) |
| 2026-05-13 | `Sites.Read.All` 완전 제거: `SCOPES_SCAN` 상수 삭제, `authenticate()`는 항상 `Files.ReadWrite.All`만 요청 — 관리자 동의 팝업 원천 차단 |
| 2026-05-13 | `_auto_login()` 비밀번호 입력 방식 변경: JS value 주입 → `send_keys()` 한 글자씩 + `idSIButton9` 버튼 클릭 (React 폼 이벤트 정상 인식) |
| 2026-05-13 | `folder_sharing_url` 파라미터 전체 체인 추가: Step 1 UI 입력 → `survey_stream` / `survey_delta_stream` / `_run_analysis` / `AnalyzeRequest` / `RenameRequest` / `RollbackRequest` → `_make_sp_client` → `SharePointClient` 설정 |
| 2026-05-13 | `_resolve_drive()` 개선: Sites API 폴백 완전 제거, `folder_sharing_url` 없으면 명확한 안내 메시지 RuntimeError 발생 |
| 2026-05-13 | `_get()` 에러 개선: `raise_for_status()` 대신 MS Graph JSON 에러 본문(`error.code`, `error.message`) 포함 HTTPError 발생 — UI에 실제 오류 원인 표시 |
| 2026-05-13 | `POST /api/sp/test-sharing-url` 진단 엔드포인트 추가 — 공유 URL의 MS Graph 해석 가능 여부 즉시 테스트 (드라이브 ID, 폴더명 반환) |
| 2026-05-13 | Step 1 UI에 "연결 테스트" 버튼 추가 — 공유 URL 옆, 클릭 시 서버 없이 즉시 MS Graph 연결 확인, 결과 인라인 표시 (성공/실패) |
| 2026-05-13 | `get_item_by_sharing_url()` Drive ID 해석 2단계 fallback 추가 — ① `parentReference.driveId` → ② `/shares/{id}/drive` 엔드포인트 (/:f:/ 폴더 공유 링크 호환성 개선) |
| 2026-05-13 | `_resolve_drive()` 3단계 해석 체계로 개편 — ① 캐시 → ② 공유 URL(Files.ReadWrite.All) → ③ SharePoint REST API `/_api/v2.1/drives`(AllSites.Read 범위, 자동 fallback) |
| 2026-05-13 | `_resolve_drive_via_sp_rest()` 신규 추가 — AllSites.Read 범위 토큰 취득(silent 우선, 디바이스 코드 fallback) → SP REST v2.1로 드라이브 목록 조회 → drive_name 매칭 |
| 2026-05-13 | `folder_sharing_url` 선택사항으로 변경 — 없어도 SP REST API fallback으로 자동 해결, UI 라벨 업데이트 |
| 2026-05-13 | `/api/sp/test-sharing-url` 개선 — `_resolve_drive()` 전체 체인 실행, 공유 URL 없이 사이트 URL만으로도 테스트 가능 |
| 2026-05-14 | 롤백 파일에 `site_url` 저장 — `rollback_mapping_*.json` 포맷을 `{"site_url":"…","items":[…]}` 엔벨로프로 변경 (이전 flat-array 포맷 하위 호환 유지). `/api/rollback/list` 응답에 `site_url` 포함. 롤백 목록 UI에 사이트 URL 표시 (`rollback-meta` 영역) |
