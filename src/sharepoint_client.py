from __future__ import annotations

import base64
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from msal import PublicClientApplication, SerializableTokenCache


GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Files.ReadWrite.All (위임됨) — 관리자 동의 불필요
# Sites.Read.All 은 사용하지 않음 — 회사 보안 정책상 관리자 동의 필요
# 드라이브 ID 해석은 folder_sharing_url 방식으로 대체
SCOPES_BASE = ["Files.ReadWrite.All"]

# Strip "/drives/{any}/root:" prefix from parentReference.path
_DRIVE_ROOT_RE = re.compile(r"^/drives/[^/]+/root:", re.IGNORECASE)


class SharePointClient:
    """MS Graph API client for SharePoint file operations.

    Handles device-code authentication with MSAL token caching, recursive
    file listing, per-file download, and item rename via PATCH.
    """

    def __init__(self, sp_config: dict, token_cache_path: Path | None = None) -> None:
        self.tenant_id: str = sp_config["tenant_id"]
        self.client_id: str = sp_config["client_id"]
        self.site_url: str = sp_config["site_url"].rstrip("/")
        self.drive_name: str = sp_config.get("drive_name", "Documents")
        self.root_folder: str = sp_config.get("root_folder", "").strip("/")

        self._token_cache_path = token_cache_path
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._site_id: str | None = None
        # drive_id can be pre-set in config to skip Sites.Read.All resolution
        self._drive_id: str | None = sp_config.get("drive_id") or None
        # folder_sharing_url: if set, drive_id is resolved from it (no Sites.Read.All needed)
        self._folder_sharing_url: str = (sp_config.get("folder_sharing_url") or "").strip()
        self._sharing_url_root_id: str | None = None
        # ssl_verify: set False when corporate SSL inspection proxy is in use
        self._ssl_verify: bool = bool(sp_config.get("ssl_verify", True))
        if not self._ssl_verify:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._msal_app = self._build_msal_app()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _build_msal_app(self) -> PublicClientApplication:
        cache = SerializableTokenCache()
        if self._token_cache_path and self._token_cache_path.exists():
            cache.deserialize(self._token_cache_path.read_text(encoding="utf-8"))
        return PublicClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=cache,
        )

    def _save_token_cache(self) -> None:
        if self._token_cache_path and self._msal_app.token_cache.has_state_changed:
            self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_cache_path.write_text(
                self._msal_app.token_cache.serialize(), encoding="utf-8"
            )

    def authenticate(self, need_site_scan: bool = False) -> None:
        """Acquire access token; uses silent refresh first, then device code.

        Sites.Read.All 은 사용하지 않음. Files.ReadWrite.All 만 요청.
        need_site_scan 파라미터는 하위 호환성을 위해 유지하되 무시됨.
        """
        scopes = SCOPES_BASE
        accounts = self._msal_app.get_accounts()
        result = None
        if accounts:
            result = self._msal_app.acquire_token_silent(scopes, account=accounts[0])
        if not result:
            flow = self._msal_app.initiate_device_flow(scopes=scopes)
            code = flow["user_code"]
            url = flow["verification_uri"]

            print(f"\n[sharepoint] ══════════════════════════════════════════")
            print(f"[sharepoint]  인증 코드  : {code}")
            print(f"[sharepoint]  인증 URL   : {url}")
            print(f"[sharepoint]  위 URL을 브라우저에서 열고 코드를 입력하세요.")
            print(f"[sharepoint] ══════════════════════════════════════════\n")

            # 브라우저 자동 로그인 시도 (실패해도 수동 로그인으로 진행 가능)
            import threading
            done_event = threading.Event()
            login_thread = threading.Thread(
                target=self._auto_login,
                args=(url, code, done_event),
                daemon=True,
            )
            login_thread.start()

            # MSAL 폴링 (로그인 완료될 때까지 대기)
            result = self._msal_app.acquire_token_by_device_flow(flow)

            # 로그인 완료 → 브라우저 스레드 종료 신호
            done_event.set()
            login_thread.join(timeout=5)
        if "access_token" not in result:
            raise RuntimeError(f"인증 실패: {result.get('error_description', result)}")
        self._token = result["access_token"]
        self._token_expires_at = time.monotonic() + result.get("expires_in", 3600) - 60
        self._save_token_cache()
        print(f"[sharepoint] 인증 성공 (scopes={scopes})")

    def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        self.authenticate()
        return self._token  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Low-level HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Accept": "application/json",
        }

    def _get(self, url: str, params: dict | None = None) -> dict:
        resp = requests.get(url, headers=self._headers(), params=params,
                            timeout=30, verify=self._ssl_verify)
        if not resp.ok:
            try:
                err_body = resp.json()
                err = err_body.get("error", {})
                code = err.get("code", "")
                msg  = err.get("message", "")
                detail = f" [{code}] {msg}" if code else f" {resp.text[:300]}"
            except Exception:
                detail = f" {resp.text[:300]}"
            raise requests.HTTPError(
                f"HTTP {resp.status_code}{detail}",
                response=resp,
            )
        return resp.json()

    def _patch(self, url: str, body: dict) -> dict:
        resp = requests.patch(
            url,
            headers={**self._headers(), "Content-Type": "application/json"},
            json=body,
            timeout=30,
            verify=self._ssl_verify,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Site / drive resolution
    # ------------------------------------------------------------------

    def _resolve_drive(self) -> str:
        """Return drive_id, resolving on first call.

        Resolution order:
        1. Already cached
        2. folder_sharing_url  →  /shares/{encoded}/driveItem  (Files.ReadWrite.All)
        3. SharePoint REST API  →  /_api/v2.1/drives  (AllSites.Read scope, 자동 fallback)
        """
        if self._drive_id:
            return self._drive_id

        # ── 2. folder_sharing_url 방식 ────────────────────────────────
        if self._folder_sharing_url:
            try:
                item = self.get_item_by_sharing_url(self._folder_sharing_url)
                if self._drive_id:
                    self._sharing_url_root_id = item.get("id")
                    print(f"[sharepoint] drive_id 확인 (공유 URL): {self._drive_id[:16]}...")
                    return self._drive_id
                print(f"[sharepoint] 공유 URL 응답에 driveId 없음, SP REST API fallback 시도")
            except Exception as exc:
                print(f"[sharepoint] 공유 URL 해석 실패 ({exc}), SP REST API fallback 시도")

        # ── 3. SharePoint REST API fallback (AllSites.Read 범위) ──────
        if self.site_url:
            try:
                drive_id = self._resolve_drive_via_sp_rest()
                self._drive_id = drive_id
                print(f"[sharepoint] drive_id 확인 (SP REST API): {drive_id[:16]}...")
                return self._drive_id
            except Exception as exc:
                raise RuntimeError(
                    f"드라이브 ID 해석 실패 (SP REST API): {exc}\n"
                    "Azure AD 앱에 'AllSites.Read' (SharePoint) 권한이 승인되어 있는지 확인하세요."
                ) from exc

        raise RuntimeError(
            "드라이브 ID를 확인할 수 없습니다.\n"
            "사이트 URL을 입력하거나 folder_sharing_url을 설정하세요."
        )

    def _resolve_drive_via_sp_rest(self) -> str:
        """SharePoint REST API /_api/v2.1/drives 로 드라이브 ID를 조회합니다.

        AllSites.Read (SharePoint 위임 권한) 범위의 토큰을 사용합니다.
        동일 MSAL 앱에서 리프레시 토큰으로 무인(silent) 취득을 시도합니다.
        """
        parsed = urlparse(self.site_url)
        hostname = parsed.netloc          # e.g. poscointl1.sharepoint.com
        site_path = parsed.path.rstrip("/")  # e.g. /sites/DB2

        sp_scopes = [f"https://{hostname}/AllSites.Read"]

        # 1차: 캐시 토큰 (silent)
        accounts = self._msal_app.get_accounts()
        sp_result = None
        if accounts:
            sp_result = self._msal_app.acquire_token_silent(sp_scopes, account=accounts[0])

        # 2차: 디바이스 코드 플로우 (필요 시)
        if not sp_result or "access_token" not in sp_result:
            print(f"[sharepoint] SharePoint 범위 토큰 취득 중 (AllSites.Read)…")
            flow = self._msal_app.initiate_device_flow(scopes=sp_scopes)
            print(f"[sharepoint]  인증 코드: {flow['user_code']}  URL: {flow['verification_uri']}")
            sp_result = self._msal_app.acquire_token_by_device_flow(flow)

        if not sp_result or "access_token" not in sp_result:
            raise RuntimeError(
                f"AllSites.Read 토큰 취득 실패: {sp_result.get('error_description', sp_result)}"
            )

        sp_token = sp_result["access_token"]
        self._save_token_cache()

        # SharePoint REST API v2.1 → MS Graph 호환 드라이브 목록
        drives_url = f"https://{hostname}{site_path}/_api/v2.1/drives"
        resp = requests.get(
            drives_url,
            headers={"Authorization": f"Bearer {sp_token}", "Accept": "application/json"},
            timeout=30,
            verify=self._ssl_verify,
        )
        if not resp.ok:
            try:
                err = resp.json().get("error", {})
                detail = f" [{err.get('code')}] {err.get('message')}"
            except Exception:
                detail = f" {resp.text[:200]}"
            raise RuntimeError(f"HTTP {resp.status_code}{detail}")

        drives = resp.json().get("value", [])
        if not drives:
            raise RuntimeError(f"드라이브 목록이 비어 있습니다. (사이트: {self.site_url})")

        # drive_name이 지정된 경우 이름으로 매칭
        for drive in drives:
            if drive.get("name") == self.drive_name:
                return drive["id"]

        # 첫 번째 드라이브 사용 (일반적으로 Documents 라이브러리)
        print(f"[sharepoint] drive_name='{self.drive_name}' 미매칭 → 첫 번째 드라이브 사용: {drives[0].get('name')}")
        return drives[0]["id"]

    # ------------------------------------------------------------------
    # File listing
    # ------------------------------------------------------------------

    def list_files_recursive(self) -> list[dict]:
        """Return a flat list of all file DriveItems under root_folder.

        If drive_id was bootstrapped from a folder sharing URL, the resolved
        folder item is reused as the root to avoid a second API call.
        """
        drive_id = self._resolve_drive()

        # Use the sharing-URL-resolved folder item if available.
        if self._sharing_url_root_id:
            root_id = self._sharing_url_root_id
        elif self.root_folder:
            folder_data = self._get(
                f"{GRAPH_BASE}/drives/{drive_id}/root:/{self.root_folder}"
            )
            root_id = folder_data["id"]
        else:
            root_data = self._get(f"{GRAPH_BASE}/drives/{drive_id}/root")
            root_id = root_data["id"]

        all_files: list[dict] = []
        self._collect_files(drive_id, root_id, all_files)
        return all_files

    def _list_children(self, drive_id: str, folder_id: str) -> list[dict]:
        items: list[dict] = []
        url: str | None = (
            f"{GRAPH_BASE}/drives/{drive_id}/items/{folder_id}/children"
            "?$select=id,name,size,lastModifiedDateTime,file,folder,parentReference,webUrl"
        )
        while url:
            data = self._get(url)
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return items

    def _collect_files(self, drive_id: str, folder_id: str, result: list[dict]) -> None:
        for item in self._list_children(drive_id, folder_id):
            if "file" in item:
                result.append(item)
            elif "folder" in item:
                self._collect_files(drive_id, item["id"], result)

    # ------------------------------------------------------------------
    # Survey / inventory
    # ------------------------------------------------------------------

    def build_folder_tree(self, progress_callback=None, folder_callback=None) -> dict:
        """Build a recursive folder tree with per-folder file counts for survey mode.

        progress_callback(n: int)   — called once per file found (n=1).
        folder_callback(path: str)  — called when entering each folder.
        """
        drive_id = self._resolve_drive()

        if self._sharing_url_root_id:
            root_id = self._sharing_url_root_id
            root_item = self._get(f"{GRAPH_BASE}/drives/{drive_id}/items/{root_id}"
                                  "?$select=id,name,size,lastModifiedDateTime,file,folder,parentReference,webUrl")
        elif self.root_folder:
            root_item = self._get(f"{GRAPH_BASE}/drives/{drive_id}/root:/{self.root_folder}"
                                  "?$select=id,name,size,lastModifiedDateTime,file,folder,parentReference,webUrl")
            root_id = root_item["id"]
        else:
            root_item = self._get(f"{GRAPH_BASE}/drives/{drive_id}/root"
                                  "?$select=id,name,size,lastModifiedDateTime,file,folder,parentReference,webUrl")
            root_id = root_item["id"]

        root_name = root_item.get("name", self.root_folder or "root")
        root_path = "/" + root_name
        return self._build_tree_node(drive_id, root_id, root_name, root_path, progress_callback, folder_callback)

    def _build_tree_node(
        self,
        drive_id: str,
        folder_id: str,
        folder_name: str,
        folder_path: str,
        progress_callback=None,
        folder_callback=None,
    ) -> dict:
        node: dict = {
            "name": folder_name,
            "path": folder_path,
            "files": [],
            "children": [],
            "total_files": 0,
            "total_size": 0,
        }
        for item in self._list_children(drive_id, folder_id):
            if "file" in item:
                ext = Path(item["name"]).suffix.lower()
                size = int(item.get("size") or 0)
                node["files"].append({"name": item["name"], "ext": ext, "size": size})
                node["total_files"] += 1
                node["total_size"] += size
                if progress_callback:
                    progress_callback(1)
            elif "folder" in item:
                child_path = folder_path.rstrip("/") + "/" + item["name"]
                if folder_callback:
                    folder_callback(child_path)
                child = self._build_tree_node(
                    drive_id, item["id"], item["name"], child_path, progress_callback, folder_callback
                )
                node["children"].append(child)
                node["total_files"] += child["total_files"]
                node["total_size"] += child["total_size"]
        return node

    # ------------------------------------------------------------------
    # Delta-based survey (cache + incremental refresh)
    # ------------------------------------------------------------------

    def build_folder_tree_with_delta(
        self, progress_callback=None, folder_callback=None
    ) -> tuple[dict, str, dict]:
        """Full scan via delta endpoint. Returns (tree, delta_link, file_index).

        Uses /items/{id}/delta instead of recursive children so that the
        deltaLink token is captured in one pass — no extra round-trip needed.
        """
        drive_id = self._resolve_drive()

        if self._sharing_url_root_id:
            root_id = self._sharing_url_root_id
            root_item = self._get(
                f"{GRAPH_BASE}/drives/{drive_id}/items/{root_id}?$select=id,name"
            )
        elif self.root_folder:
            root_item = self._get(
                f"{GRAPH_BASE}/drives/{drive_id}/root:/{self.root_folder}?$select=id,name"
            )
            root_id = root_item["id"]
        else:
            root_item = self._get(
                f"{GRAPH_BASE}/drives/{drive_id}/root?$select=id,name"
            )
            root_id = root_item["id"]

        root_name = root_item.get("name", self.root_folder or "root")
        root_path = "/" + root_name

        url: str | None = (
            f"{GRAPH_BASE}/drives/{drive_id}/items/{root_id}/delta"
            "?$select=id,name,size,lastModifiedDateTime,file,folder,parentReference,webUrl,deleted"
        )
        all_items: list[dict] = []
        delta_link = ""

        while url:
            data = self._get(url)
            batch = data.get("value", [])
            for item in batch:
                if item.get("deleted"):
                    continue
                if "file" in item and progress_callback:
                    progress_callback(1)
                elif "folder" in item and item["id"] != root_id and folder_callback:
                    parent_raw = item.get("parentReference", {}).get("path", "")
                    parent_clean = _DRIVE_ROOT_RE.sub("", parent_raw)
                    folder_callback(parent_clean.rstrip("/") + "/" + item["name"])
            all_items.extend(batch)
            url = data.get("@odata.nextLink")
            if "@odata.deltaLink" in data:
                delta_link = data["@odata.deltaLink"]

        tree, file_index = self._items_to_tree(all_items, root_id, root_name, root_path)
        return tree, delta_link, file_index

    def _items_to_tree(
        self, items: list[dict], root_id: str, root_name: str, root_path: str
    ) -> tuple[dict, dict]:
        """Convert flat delta items list to (tree_node, file_index)."""
        # Deduplicate: delta API may return the same item ID across paginated pages.
        # Last occurrence wins (most recent state from the API).
        seen: dict[str, dict] = {}
        for item in items:
            seen[item["id"]] = item
        items = list(seen.values())

        # Pass 1: create folder nodes
        nodes: dict[str, dict] = {
            root_id: {
                "name": root_name, "path": root_path,
                "files": [], "children": [], "total_files": 0, "total_size": 0,
            }
        }
        for item in items:
            if "folder" not in item or item.get("deleted") or item["id"] == root_id:
                continue
            parent_raw = item.get("parentReference", {}).get("path", "")
            parent_clean = _DRIVE_ROOT_RE.sub("", parent_raw)
            item_path = parent_clean.rstrip("/") + "/" + item["name"]
            nodes[item["id"]] = {
                "name": item["name"], "path": item_path,
                "files": [], "children": [], "total_files": 0, "total_size": 0,
            }

        # Pass 2: assign files and sub-folders to parents
        file_index: dict[str, dict] = {}
        for item in items:
            if item.get("deleted"):
                continue
            parent_id = item.get("parentReference", {}).get("id", "")
            if parent_id not in nodes:
                continue
            if "file" in item:
                ext = Path(item["name"]).suffix.lower()
                size = int(item.get("size") or 0)
                nodes[parent_id]["files"].append({"name": item["name"], "ext": ext, "size": size})
                parent_raw = item.get("parentReference", {}).get("path", "")
                file_index[item["id"]] = {
                    "name": item["name"],
                    "folder_path": _DRIVE_ROOT_RE.sub("", parent_raw),
                    "size": size,
                    "modified": item.get("lastModifiedDateTime", ""),
                    "ext": ext,
                    "web_url": item.get("webUrl", ""),
                }
            elif "folder" in item and item["id"] in nodes:
                nodes[parent_id]["children"].append(nodes[item["id"]])

        # Pass 3: calculate totals bottom-up
        def _calc(node: dict) -> None:
            for child in node["children"]:
                _calc(child)
                node["total_files"] += child["total_files"]
                node["total_size"] += child["total_size"]
            node["total_files"] += len(node["files"])
            node["total_size"] += sum(f["size"] for f in node["files"])

        root_node = nodes[root_id]
        _calc(root_node)
        return root_node, file_index

    def get_delta(self, delta_link: str) -> tuple[list[dict], str]:
        """Call deltaLink, return (changed_items, new_delta_link). Fast — only changes."""
        all_items: list[dict] = []
        new_delta_link = delta_link
        url: str | None = delta_link
        while url:
            data = self._get(url)
            all_items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            if "@odata.deltaLink" in data:
                new_delta_link = data["@odata.deltaLink"]
        return all_items, new_delta_link

    # ------------------------------------------------------------------
    # Path helpers (for building FileRecord fields)
    # ------------------------------------------------------------------

    def item_folder_path(self, item: dict) -> str:
        """Return the human-readable absolute folder path for a drive item.

        e.g. "/drives/b!xxx/root:/법무DB/계약" → "/법무DB/계약"
        """
        raw = item.get("parentReference", {}).get("path", "")
        cleaned = _DRIVE_ROOT_RE.sub("", raw)  # strips /drives/.../root:
        return cleaned or "/"

    def item_relative_path(self, item: dict) -> str:
        """Return path of the item relative to root_folder.

        e.g. root_folder="법무DB", file at "/법무DB/계약/계약서.pdf" → "계약/계약서.pdf"
        """
        folder_abs = self.item_folder_path(item).lstrip("/")
        name = item["name"]
        full_rel = f"{folder_abs}/{name}".lstrip("/")
        if self.root_folder:
            prefix = self.root_folder.rstrip("/") + "/"
            if full_rel.startswith(prefix):
                return full_rel[len(prefix):]
        return full_rel

    # ------------------------------------------------------------------
    # Download / rename
    # ------------------------------------------------------------------

    def download_file(self, item_id: str, item_name: str, dest_dir: Path) -> Path:
        """Download a drive item to dest_dir. Returns the local file path.

        Uses item_id as a stable cache key: if the temp file already exists
        it is returned immediately without re-downloading.
        """
        drive_id = self._resolve_drive()
        suffix = Path(item_name).suffix or ".tmp"
        dest = dest_dir / f"sp_{item_id[:20]}{suffix}"
        if dest.exists():
            return dest
        dest_dir.mkdir(parents=True, exist_ok=True)
        url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
        headers = {"Authorization": f"Bearer {self._ensure_token()}"}
        resp = requests.get(url, headers=headers, allow_redirects=True, stream=True,
                            timeout=120, verify=self._ssl_verify)
        resp.raise_for_status()
        with dest.open("wb") as fp:
            for chunk in resp.iter_content(chunk_size=65536):
                fp.write(chunk)
        return dest

    def _auto_login(self, url: str, code: str, done_event) -> None:
        """Selenium + Edge(기본 설치)로 device code 입력 → 계정 선택 → 비밀번호 자동 처리."""
        import os
        import time
        from dotenv import load_dotenv
        # 프로젝트 루트의 .env를 명시적으로 로드 (override=True: 이미 설정된 값도 덮어씀)
        _env_path = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(dotenv_path=_env_path, override=True)
        email = os.getenv("SP_EMAIL", "")
        password = os.getenv("SP_PASSWORD", "")

        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.edge.options import Options
            from selenium.common.exceptions import TimeoutException
        except ImportError:
            print("[sharepoint] selenium 미설치 → 브라우저에서 수동 로그인 하세요.")
            import webbrowser
            webbrowser.open(url)
            return

        def wait_for(driver, by, selector, timeout=10):
            try:
                return WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((by, selector))
                )
            except TimeoutException:
                return None

        options = Options()
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--ignore-ssl-errors")

        driver = webdriver.Edge(options=options)
        try:
            # ── 1. 인증 코드 입력 ────────────────────────────────────────
            driver.get(url)
            el = wait_for(driver, By.NAME, "otc", timeout=15)
            if el:
                el.clear()
                el.send_keys(code)
                el.submit()
                print("[sharepoint] ✓ 인증 코드 입력 완료")
            time.sleep(2)

            # ── 2. 계정 선택 ─────────────────────────────────────────────
            if email:
                # data-test-id 방식
                el = wait_for(driver, By.CSS_SELECTOR, f'[data-test-id="{email}"]', timeout=6)
                if el:
                    el.click()
                    print(f"[sharepoint] ✓ 계정 선택: {email}")
                else:
                    # 텍스트 포함 div 방식
                    try:
                        els = driver.find_elements(By.XPATH, f'//*[contains(text(),"{email}")]')
                        if els:
                            els[0].click()
                            print(f"[sharepoint] ✓ 계정 선택 (텍스트): {email}")
                    except Exception:
                        pass
                time.sleep(2)

            # ── 3. 비밀번호 입력 ─────────────────────────────────────────
            if password:
                el = wait_for(driver, By.CSS_SELECTOR, 'input[type="password"]', timeout=10)
                if el:
                    el.clear()
                    el.click()
                    # send_keys로 한 글자씩 입력 (React 폼 이벤트 정상 발생)
                    for ch in password:
                        el.send_keys(ch)
                    time.sleep(0.5)
                    # 제출 버튼 클릭 (el.submit() 대신 — React 폼에서 신뢰성 높음)
                    btn = wait_for(driver, By.ID, "idSIButton9", timeout=5)
                    if btn:
                        btn.click()
                    else:
                        el.submit()
                    print("[sharepoint] ✓ 비밀번호 입력 완료")
                else:
                    print("[sharepoint] ✗ 비밀번호 입력란을 찾지 못했습니다. 수동으로 입력하세요.")
                time.sleep(2)

            # ── 4. "로그인 상태 유지?" → 예 ──────────────────────────────
            el = wait_for(driver, By.ID, "idSIButton9", timeout=8)
            if el:
                el.click()
                print("[sharepoint] ✓ 로그인 상태 유지 선택")

            # ── 5. MSAL 토큰 수신까지 대기 ───────────────────────────────
            print("[sharepoint] 로그인 완료 대기 중...")
            done_event.wait(timeout=120)

        except Exception as exc:
            print(f"[sharepoint] 자동 로그인 중 오류: {exc}")
            print("[sharepoint] 브라우저에서 수동으로 로그인을 완료하세요.")
            done_event.wait(timeout=120)
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def get_item_by_sharing_url(self, sharing_url: str) -> dict:
        """Resolve a SharePoint sharing link to a DriveItem.

        Works with any /:b:/, /:w:/, /:x:/, /:f:/ etc. sharing URL.
        Also caches the drive_id so subsequent download/rename calls work
        without requiring a separate site/drive resolution step.

        Drive ID resolution order:
        1. parentReference.driveId  in the driveItem response
        2. /shares/{id}/drive endpoint (fallback for links where parentReference lacks driveId)
        """
        encoded = base64.urlsafe_b64encode(sharing_url.encode("utf-8")).rstrip(b"=").decode("ascii")
        shares_id = f"u!{encoded}"
        item = self._get(
            f"{GRAPH_BASE}/shares/{shares_id}/driveItem",
            params={
                "$select": "id,name,size,lastModifiedDateTime,file,folder,parentReference,webUrl"
            },
        )
        # 1st try: parentReference.driveId (most common)
        if not self._drive_id:
            drive_id = item.get("parentReference", {}).get("driveId")
            if drive_id:
                self._drive_id = drive_id
                print(f"[sharepoint] drive_id 확인 (parentReference): {drive_id[:16]}...")

        # 2nd try: /shares/{id}/drive endpoint (fallback)
        if not self._drive_id:
            print(f"[sharepoint] parentReference에 driveId 없음, /shares/.../drive 엔드포인트로 재시도")
            drive_data = self._get(f"{GRAPH_BASE}/shares/{shares_id}/drive")
            drive_id = drive_data.get("id")
            if drive_id:
                self._drive_id = drive_id
                print(f"[sharepoint] drive_id 확인 (/drive 엔드포인트): {drive_id[:16]}...")

        return item

    def get_item_by_path(self, path_in_drive: str) -> dict:
        """Fetch a single DriveItem by its path relative to the drive root.

        Examples
        --------
        path_in_drive = "법무DB/계약/계약서.pdf"
        path_in_drive = "Shared Documents/법무DB/계약서.pdf"
        """
        drive_id = self._resolve_drive()
        path = path_in_drive.strip("/")
        url = (
            f"{GRAPH_BASE}/drives/{drive_id}/root:/{path}"
            "?$select=id,name,size,lastModifiedDateTime,file,folder,parentReference,webUrl"
        )
        return self._get(url)

    def rename_item(self, item_id: str, new_name: str) -> dict:
        """Rename a drive item via PATCH. Returns the updated item dict."""
        drive_id = self._resolve_drive()
        return self._patch(
            f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}",
            {"name": new_name},
        )
