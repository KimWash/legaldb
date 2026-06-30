# 법무 문서 DB 파일명 자동화 시스템 — 설치 가이드

> 대상: 신규 PC에 처음 설치하는 담당자

---

## 1. 사전 요구사항

### 1-1. Python 3.12

1. https://www.python.org/downloads/ 에서 **Python 3.12.x** 다운로드
2. 설치 시 **"Add Python to PATH"** 반드시 체크
3. 설치 확인:
   ```
   python --version
   → Python 3.12.x
   ```

### 1-2. Tesseract OCR (한국어 포함)

1. https://github.com/UB-Mannheim/tesseract/wiki 에서 **tesseract-ocr-w64-setup-5.x.x.exe** 다운로드
2. 설치 경로: **`C:\Program Files\Tesseract-OCR`** (기본값 유지)
3. 설치 중 **"Additional language data"** 목록에서 **Korean** 선택
4. 설치 확인:
   ```
   "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
   → tesseract 5.x.x
   ```

### 1-3. Ghostscript

1. https://ghostscript.com/releases/gsdnld.html 에서 **Ghostscript 10.x (Windows 64-bit)** 다운로드
2. 설치 후 환경변수 PATH에 자동 등록됨
3. 설치 확인:
   ```
   gswin64c --version
   → 10.x.x
   ```

### 1-4. Microsoft Edge

- Windows 11에 기본 설치되어 있으므로 별도 설치 불필요
- SharePoint 자동 로그인에 사용됨

### 1-5. (선택) Intel OCR 가속 — Arc GPU / NPU

`config.yaml`의 `ocr.engine: "accelerated"` 사용 시 OpenVINO + RapidOCR로 Intel Ultra 7 155H의
**Arc GPU → NPU → CPU** 순으로 OCR을 가속합니다. **미설치/미지원 환경에서는 자동으로 Tesseract로 폴백**하므로 선택 사항입니다.

1. 패키지 설치 (requirements.txt에 포함됨):
   ```
   pip install openvino rapidocr-openvino
   ```
2. GPU/NPU 사용을 위한 드라이버:
   - **Arc GPU**: 최신 Intel Graphics Driver 설치 → OpenVINO가 `GPU` 디바이스로 인식
   - **NPU**: Intel NPU(AI Boost) 드라이버 설치 → OpenVINO가 `NPU` 디바이스로 인식
   - 디바이스 인식 확인:
     ```
     python -c "import openvino as ov; print(ov.Core().available_devices)"
     → ['CPU', 'GPU', 'NPU']  (설치된 장치만 표시)
     ```
3. 한국어 인식 모델: RapidOCR 한국어 모델이 필요하며, 최초 실행 시 자동 다운로드되거나
   `ocr.accelerated.lang: "korean"` 설정에 맞는 모델을 사용합니다.
4. 가속을 끄고 기존 Tesseract만 쓰려면 `config.yaml`에서 `ocr.engine: "tesseract"`로 변경하세요.

> 실행 로그에 `[ocr_accelerated] engine ready backend=... device=GPU` 가 보이면 가속이 활성화된 것입니다.
> `unavailable` 메시지가 보이면 Tesseract로 폴백되며 결과 품질에는 영향이 없습니다.

### 1-6. 구 버전 및 한글 문서 파일 추출 사전 요구사항

1. **Microsoft Office (Word & PowerPoint)**:
   - 구 버전 문서 형식(`.doc`, `.ppt`)의 텍스트 추출은 Windows COM 자동화를 사용하므로 PC에 Microsoft Word 및 PowerPoint 프로그램이 정식 설치되어 있어야 합니다.
   - DRM(MIP 등)이 걸리지 않은 일반 파일만 자동으로 추출할 수 있으며, DRM이 적용된 파일은 자동으로 수동 검토 대상으로 전환됩니다.
2. **olefile 라이브러리**:
   - 한글 파일(`.hwp`) 구조 분석을 위해 `olefile` 라이브러리가 사용되며, `requirements.txt`에 등록되어 있습니다.

---

## 2. 프로젝트 파일 복사

아래 폴더/파일을 **제외하고** 프로젝트 전체를 신규 PC로 복사합니다.

| 제외 항목 | 이유 |
|-----------|------|
| `temp/sp_token_cache.json` | 이전 사용자의 SharePoint 인증 토큰 |
| `temp/analysis_cache.json` | 이전 PC 경로 기반 캐시 |
| `temp/sp_downloads/` | 임시 다운로드 파일 |
| `temp/ocr_runs/` | 임시 OCR 결과 파일 |

> `output/rollback/` 폴더는 복원이 필요한 경우에만 복사하세요.

---

## 3. Python 패키지 설치

명령 프롬프트(cmd) 또는 PowerShell을 **관리자 권한**으로 열고 프로젝트 폴더로 이동합니다.

```
cd C:\설치경로\Legal_DB_Rename_Project_MSsharepoint
pip install -r requirements.txt
pip install ocrmypdf
```

설치 확인:
```
pip show fastapi openpyxl pytesseract ocrmypdf olefile
```

---

## 4. 환경 설정

### 4-1. `.env` 파일 생성

프로젝트 루트에 `.env` 파일을 생성하고 아래 내용을 입력합니다.

```
GEMINI_API_KEY=<Google Gemini API 키>
SP_EMAIL=<본인 회사 이메일>@poscointl.com
SP_PASSWORD=<본인 회사 비밀번호>
```

> Gemini API 키는 https://aistudio.google.com/apikey 에서 발급합니다. (`GOOGLE_API_KEY`로도 인식됨)

> **주의:** 이 파일은 절대 외부에 공유하지 마세요.

### 4-2. `config.yaml` 확인

아래 항목만 확인합니다. 나머지는 수정 불필요합니다.

```yaml
# Tesseract 설치 경로 — 기본값(C:/Program Files/...)으로 설치했으면 수정 불필요
ocr:
  advanced:
    tesseract_cmd: "C:/Program Files/Tesseract-OCR/tesseract.exe"

# SharePoint 접속 정보 — 이미 설정되어 있음. 변경 불필요
sharepoint:
  site_url: "https://poscointl1.sharepoint.com/sites/DX-DB"
  root_folder: "법무DB (테스트)"
  folder_sharing_url: "https://poscointl1.sharepoint.com/:f:/s/DX-DB/..."
```

---

## 5. 서버 실행

```
python src/api_server.py
```

정상 실행 시 아래 메시지가 출력됩니다:

```
INFO:     Started server process [XXXX]
INFO:     Waiting for application startup.
[sharepoint] 인증 성공 (scopes=['Files.ReadWrite.All'])
[server] SharePoint 인증 완료 (토큰 캐시 활성)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

> 토큰 캐시가 없는 **최초 실행**의 경우: 서버 시작 중 터미널에 인증 코드와 URL이 출력되며, Microsoft Edge가 자동으로 열려 `.env`의 이메일/비밀번호로 로그인을 처리합니다. MFA가 설정된 경우 승인만 하면 됩니다.

---

## 6. 브라우저 접속

1. 브라우저에서 `http://127.0.0.1:8000` 접속
2. SharePoint 인증은 **서버 시작 시 이미 완료**되어 있으므로 별도 로그인 불필요
3. 홈 화면에서 바로 사용 시작

> **최초 실행(토큰 없음):** 인증은 서버 시작 단계에서 자동 처리됩니다. Edge 브라우저가 열리고 `.env`의 계정으로 자동 로그인하며, MFA 승인이 필요한 경우에만 사용자 개입이 필요합니다. 인증 완료 후 서버가 준비되면 브라우저에서 접속하세요.
>
> 인증 토큰은 `temp/sp_token_cache.json`에 저장되어 이후 실행부터는 자동 로그인됩니다.

---

## 7. 사용 흐름 요약

```
STEP 1  SharePoint URL 입력 → [현황 조회]
STEP 2  폴더/파일 현황 확인 → 처리할 폴더 선택
STEP 3  [분석 시작] → AI가 파일명 제안 (약 30분 소요)
STEP 4  제안 파일명 검토 → 승인 체크 → [선택 파일명 변경]
STEP 5  필요 시 [롤백 실행]으로 원래 파일명으로 복원
```

---

## 8. 문제 해결

### Tesseract를 찾을 수 없다는 오류

`config.yaml`의 `tesseract_cmd` 경로가 실제 설치 경로와 일치하는지 확인:
```
where tesseract
```

### SharePoint 인증 실패

- `.env` 파일의 이메일/비밀번호 확인
- 회사 VPN 연결 여부 확인
- `temp/sp_token_cache.json` 삭제 후 재시도

### Gemini API 키가 동작하는지 확인

LLM(파일명 분석)이 키 오류 시 **조용히 추론 폴백**으로 동작하므로, 먼저 키를 직접 테스트하세요.

- **CLI**:
  ```
  python src/llm_client.py
  ```
  성공 시 `✅ 연결 성공`, 실패 시 `❌ 실패: 인증/권한 실패(400)...` 등 원인을 출력합니다.
- **웹 UI**: 3단계 'AI 파일명 분석' 카드의 **🔑 API 키 테스트** 버튼 → 토스트로 성공/실패 표시.
- 키는 `.env`의 `GEMINI_API_KEY`, 모델/엔드포인트는 `config.yaml`의 `llm.gemini.model` / `base_url`을 확인하세요.

### `pip install` 중 오류

회사 프록시 환경인 경우:
```
pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
```

### 포트 8000 이미 사용 중

다른 프로세스가 8000 포트를 사용 중인 경우 `api_server.py` 마지막 줄의 포트 번호를 변경:
```python
uvicorn.run(app, host="127.0.0.1", port=8001, reload=False)
```

---

## 9. 설치 확인 체크리스트

| 항목 | 확인 |
|------|------|
| Python 3.12 설치 및 PATH 등록 | ☐ |
| Tesseract OCR + Korean 언어팩 설치 | ☐ |
| Ghostscript 설치 | ☐ |
| `pip install -r requirements.txt` 완료 | ☐ |
| `pip install ocrmypdf` 완료 | ☐ |
| `.env` 파일 생성 및 계정 정보 입력 | ☐ |
| `python src/api_server.py` 정상 실행 | ☐ |
| 브라우저에서 `http://127.0.0.1:8000` 접속 확인 | ☐ |
| SharePoint 인증 완료 | ☐ |
