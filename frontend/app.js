'use strict';

// ── DOM refs ──────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const secInput     = $('sec-input');
const secScanning  = $('sec-scanning');
const secResults   = $('sec-results');
const errorBanner  = $('error-banner');
const btnSurvey    = $('btn-survey');
const scanCount    = $('scan-count');
const scanFolders  = $('scan-folders');
const scanStage    = $('scan-stage');
const scanFolder   = $('scan-folder');


// ── Init ──────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  // 마지막 입력 URL 복원 (localStorage)
  const savedSiteUrl = localStorage.getItem('legaldb_last_site_url');
  if (savedSiteUrl) $('site-url').value = savedSiteUrl;

  try {
    const res = await fetch('/api/config');
    if (res.ok) { await res.json(); /* config prefill 사용 안 함 */ }
  } catch (_) { /* config prefill is optional */ }

  // 캐시된 현황 조회 결과가 있으면 즉시 표시
  try {
    const res = await fetch('/api/survey/cache');
    if (res.ok) {
      const cache = await res.json();
      if (cache.available) {
        renderResults(cache.survey_data);
        _showCacheBar(cache.scanned_at);
      }
    }
  } catch (_) { /* cache is optional */ }

  // 롤백 이력은 항상 로드 (현황 조회 여부와 무관)
  loadRollbackList();
});

function _showCacheBar(scannedAt) {
  const d = new Date(scannedAt);
  const fmt = d.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' })
    + ' ' + d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
  $('survey-last-scanned').textContent = `마지막 조회: ${fmt}`;
  $('survey-cache-bar').classList.remove('hidden');
}

function startDeltaRefresh() {
  const btn = $('btn-delta-refresh');
  btn.disabled = true;
  btn.textContent = '확인 중...';
  $('delta-summary').classList.add('hidden');

  const siteUrl          = $('site-url').value.trim();
  const rootFolder       = $('root-folder').value.trim();
  const folderSharingUrl = $('folder-sharing-url').value.trim();
  const params = new URLSearchParams({ site_url: siteUrl, root_folder: rootFolder, folder_sharing_url: folderSharingUrl });
  const es = new EventSource(`/api/survey/delta/stream?${params}`);

  es.addEventListener('message', e => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }

    if (msg.type === 'status') {
      btn.textContent = msg.message;
    } else if (msg.type === 'complete') {
      es.close();
      btn.disabled = false;
      btn.textContent = '🔄 새로고침';
      _showCacheBar(msg.scanned_at);
      _showDeltaSummary(msg.changes);
      _injectFolderDeltaBadges(msg.changes.by_folder || {});
      // 통계 카드 수치 갱신
      const d = msg.survey_data;
      $('stat-total').textContent       = d.total_files.toLocaleString('ko-KR');
      $('stat-supported').textContent   = d.supported_files.toLocaleString('ko-KR');
      $('stat-unsupported').textContent = d.unsupported_files.toLocaleString('ko-KR');
    } else if (msg.type === 'resync_required') {
      es.close();
      btn.disabled = false;
      btn.textContent = '🔄 새로고침';
      // 델타 토큰 만료 → 전체 재조회 자동 전환
      startSurvey();
    } else if (msg.type === 'error') {
      es.close();
      btn.disabled = false;
      btn.textContent = '🔄 새로고침';
      showError(msg.message);
    }
  });

  es.onerror = () => {
    es.close();
    btn.disabled = false;
    btn.textContent = '🔄 새로고침';
  };
}

function _showDeltaSummary(changes) {
  const el = $('delta-summary');
  if (!changes || (changes.added === 0 && changes.deleted === 0 && changes.modified === 0)) {
    el.innerHTML = '<span class="delta-no-change">변경 사항 없음</span>';
  } else {
    const parts = [];
    if (changes.added > 0)    parts.push(`<span class="delta-badge delta-badge-add">+${changes.added}개 추가</span>`);
    if (changes.deleted > 0)  parts.push(`<span class="delta-badge delta-badge-del">-${changes.deleted}개 삭제</span>`);
    if (changes.modified > 0) parts.push(`<span class="delta-badge delta-badge-mod">${changes.modified}개 변경</span>`);
    el.innerHTML = parts.join('') + '<span class="delta-since">이전 조회 대비</span>';
  }
  el.classList.remove('hidden');
}

function _injectFolderDeltaBadges(byFolder) {
  // 기존 배지 초기화
  $('folder-tree').querySelectorAll('.folder-delta-badges').forEach(el => { el.innerHTML = ''; });
  if (!byFolder) return;
  $('folder-tree').querySelectorAll('[data-delta-path]').forEach(header => {
    const counts = byFolder[header.dataset.deltaPath];
    if (!counts) return;
    const span = header.querySelector('.folder-delta-badges');
    if (!span) return;
    const parts = [];
    if (counts.added)    parts.push(`<span class="fdelta-badge fdelta-add">+${counts.added}</span>`);
    if (counts.deleted)  parts.push(`<span class="fdelta-badge fdelta-del">-${counts.deleted}</span>`);
    if (counts.modified) parts.push(`<span class="fdelta-badge fdelta-mod">변경 ${counts.modified}</span>`);
    span.innerHTML = parts.join('');
  });
}

// ── Sharing URL Test ──────────────────────────────────────────────────
async function testSharingUrl() {
  const btn     = $('btn-test-sharing-url');
  const result  = $('sharing-url-test-result');
  const url     = $('folder-sharing-url').value.trim();
  const siteUrl = $('site-url').value.trim();
  if (!siteUrl && !url) {
    result.className = 'sharing-url-test-result sharing-url-test-error';
    result.textContent = '사이트 URL을 먼저 입력하세요.';
    return;
  }
  btn.disabled = true;
  btn.textContent = '테스트 중...';
  result.className = 'sharing-url-test-result sharing-url-test-pending';
  result.textContent = 'MS Graph에 연결 중...';

  try {
    const res = await fetch('/api/sp/test-sharing-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        site_url:           $('site-url').value.trim(),
        root_folder:        $('root-folder').value.trim(),
        folder_sharing_url: url,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      result.className = 'sharing-url-test-result sharing-url-test-ok';
      result.textContent =
        `✓ 연결 성공  |  폴더: ${data.item_name}  |  Drive ID: ${data.drive_id_short}`;
    } else {
      result.className = 'sharing-url-test-result sharing-url-test-error';
      result.textContent = `✗ 연결 실패: ${data.error}`;
    }
  } catch (e) {
    result.className = 'sharing-url-test-result sharing-url-test-error';
    result.textContent = `✗ 오류: ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = '연결 테스트';
  }
}

// ── Survey ────────────────────────────────────────────────────────────
function startSurvey() {
  const siteUrl          = $('site-url').value.trim();
  const rootFolder       = $('root-folder').value.trim();
  const folderSharingUrl = $('folder-sharing-url').value.trim();

  if (!siteUrl) {
    showError('사이트 URL을 입력하세요.');
    return;
  }

  // 마지막 입력 URL 저장
  try { localStorage.setItem('legaldb_last_site_url', siteUrl); } catch (_) {}

  hideError();
  // 기존 캐시 표시 초기화 — 전체 재조회이므로 이전 결과와 캐시 바 제거
  $('survey-cache-bar').classList.add('hidden');
  $('delta-summary').classList.add('hidden');
  showScanning();

  const params = new URLSearchParams({ site_url: siteUrl, root_folder: rootFolder, folder_sharing_url: folderSharingUrl });
  const es = new EventSource(`/api/survey/stream?${params}`);

  es.addEventListener('message', e => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }

    if (msg.type === 'status') {
      const stageLabels = {
        auth:  'SharePoint 인증 중...',
        drive: '드라이브 연결 확인 중...',
        scan:  '폴더 탐색 중',
        build: '결과 집계 중...',
      };
      scanStage.textContent = stageLabels[msg.stage] || msg.stage;
      // 인증 단계에서 터미널 안내 표시
      $('auth-hint').classList.toggle('hidden', msg.stage !== 'auth');
      if (msg.stage === 'scan') {
        scanFolder.textContent = msg.message;
        if (msg.files  != null) scanCount.textContent   = msg.files.toLocaleString('ko-KR');
        if (msg.folders != null) scanFolders.textContent = msg.folders.toLocaleString('ko-KR');
      } else {
        scanFolder.textContent = '';
      }
    } else if (msg.type === 'progress') {
      if (msg.files   != null) scanCount.textContent   = msg.files.toLocaleString('ko-KR');
      if (msg.folders != null) scanFolders.textContent = msg.folders.toLocaleString('ko-KR');
    } else if (msg.type === 'complete') {
      es.close();
      renderResults(msg.data);
      if (msg.scanned_at) _showCacheBar(msg.scanned_at);
      showToast(`전체 조회 완료: 총 ${(msg.data.total_files || 0).toLocaleString('ko-KR')}개 파일`, 'info', 4000);
    } else if (msg.type === 'error') {
      es.close();
      showInput();
      showError(msg.message || '알 수 없는 오류가 발생했습니다.');
      $('auth-hint').classList.remove('hidden');
    }
  });

  es.onerror = () => {
    es.close();
    showInput();
    showError('서버와의 연결이 끊어졌습니다. 서버가 실행 중인지 확인하세요.');
  };
}

// ── Render Results ────────────────────────────────────────────────────
function renderResults(data) {
  // Site URL label
  $('result-site-url').textContent = data.site_url;

  // Stat cards
  $('stat-total').textContent       = data.total_files.toLocaleString('ko-KR');
  $('stat-size').textContent        = data.total_size_human;
  $('stat-supported').textContent   = data.supported_files.toLocaleString('ko-KR');
  $('stat-unsupported').textContent = data.unsupported_files.toLocaleString('ko-KR');

  // Extensions table
  const tbody = $('ext-tbody');
  tbody.innerHTML = '';
  for (const row of data.ext_breakdown) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="ext-name">${esc(row.ext)}</td>
      <td class="ext-count">${row.count.toLocaleString('ko-KR')}</td>
      <td class="ext-support">${row.supported
        ? '<span class="badge-ok">✓ 지원</span>'
        : '<span class="badge-no">✗ 불가</span>'}</td>`;
    tbody.appendChild(tr);
  }

  // Folder tree
  _selectedFolders.clear();
  _nodeIndex = {};
  _loadRenameHistory();
  const treeEl = $('folder-tree');
  treeEl.innerHTML = '';
  treeEl.appendChild(buildTreeNode(data.folder_tree, true));
  updateScopeDisplay();

  // Cost estimate
  const c = data.cost_estimate;
  $('cost-llm').textContent  = c.llm_files.toLocaleString('ko-KR') + ' 파일';
  $('cost-ocr').textContent  = c.ocr_files.toLocaleString('ko-KR') + ' 파일';
  $('cost-time').textContent = c.est_duration;
  $('cost-usd').textContent  = '$' + c.est_cost_usd.toFixed(2);

  showResults();
}

// ── Folder Tree Builder ───────────────────────────────────────────────
function buildTreeNode(node, isRoot, parentPath = '') {
  const myPath = isRoot ? '' : (parentPath ? parentPath + '/' + node.name : node.name);
  if (!isRoot) _nodeIndex[myPath] = node;
  const wrapper = document.createElement('div');
  wrapper.className = 'tree-node';

  const hasChildren = node.children && node.children.length > 0;
  const countStr = `${node.direct_files} / ${node.total_files.toLocaleString('ko-KR')}`;
  const folderSvg = `<svg class="tree-folder-icon" width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H2v16h20V6H12l-2-2z"/></svg>`;

  const makeCheckbox = (path) => {
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'folder-check';
    cb.dataset.path = path;
    cb.addEventListener('click', e => e.stopPropagation());
    cb.addEventListener('change', () => onFolderCheck(path, cb.checked, cb));
    return cb;
  };

  const badgeInner = (!isRoot && _renameHistory[myPath])
    ? `<span class="rename-badge">✓ ${_fmtRenameDate(_renameHistory[myPath].lastRenamedAt)}</span>`
    : '';
  // badge-cell은 배지 유무와 관계없이 항상 고정폭으로 렌더링 → 컬럼 정렬 유지
  const badgeCell  = isRoot ? '' : `<span class="rename-badge-cell">${badgeInner}</span>`;
  const tailHtml   = `<span class="tree-counts">${countStr}</span>`;

  if (hasChildren) {
    const details = document.createElement('details');
    if (isRoot) details.open = true;

    const summary = document.createElement('summary');
    if (node.path) summary.dataset.deltaPath = node.path;
    if (!isRoot) summary.appendChild(makeCheckbox(myPath));
    summary.insertAdjacentHTML('beforeend',
      `<span class="tree-chevron">▶</span>${folderSvg}` +
      `<span class="tree-name">${esc(node.name)}</span>` +
      `<span class="folder-delta-badges"></span>` +
      badgeCell + tailHtml);

    const childrenDiv = document.createElement('div');
    childrenDiv.className = 'tree-children';
    for (const child of node.children) {
      childrenDiv.appendChild(buildTreeNode(child, false, myPath));
    }

    details.appendChild(summary);
    details.appendChild(childrenDiv);
    wrapper.appendChild(details);
  } else {
    const leaf = document.createElement('div');
    leaf.className = 'tree-leaf';
    if (node.path) leaf.dataset.deltaPath = node.path;
    if (!isRoot) leaf.appendChild(makeCheckbox(myPath));
    leaf.insertAdjacentHTML('beforeend',
      `<span class="tree-chevron"></span>${folderSvg}` +
      `<span class="tree-name">${esc(node.name)}</span>` +
      `<span class="folder-delta-badges"></span>` +
      badgeCell + tailHtml);
    wrapper.appendChild(leaf);
  }

  return wrapper;
}

// ── Folder Scope Selection ────────────────────────────────────────────
function onFolderCheck(path, checked, cbEl) {
  if (checked) _selectedFolders.add(path);
  else         _selectedFolders.delete(path);
  cascadeDown(cbEl, checked);
  cascadeUp(cbEl);
  updateScopeDisplay();
}

function cascadeDown(cbEl, checked) {
  const myNode = cbEl.closest('.tree-node');
  if (!myNode) return;
  myNode.querySelectorAll('.folder-check').forEach(cb => {
    if (cb === cbEl) return;
    cb.checked = checked;
    cb.indeterminate = false;
    if (checked) _selectedFolders.add(cb.dataset.path);
    else         _selectedFolders.delete(cb.dataset.path);
  });
}

function cascadeUp(cbEl) {
  let currentNode = cbEl.closest('.tree-node');
  while (currentNode) {
    const parentChildren = currentNode.parentElement;
    if (!parentChildren || !parentChildren.classList.contains('tree-children')) break;

    const parentDetails = parentChildren.parentElement;
    if (!parentDetails || parentDetails.tagName !== 'DETAILS') break;

    const parentCb = parentDetails.querySelector(':scope > summary > .folder-check');
    if (!parentCb) break;

    const directCbs = [];
    parentChildren.querySelectorAll(':scope > .tree-node').forEach(tn => {
      const c = tn.querySelector(':scope > details > summary > .folder-check')
             || tn.querySelector(':scope > .tree-leaf > .folder-check');
      if (c) directCbs.push(c);
    });
    if (!directCbs.length) break;

    const allChecked  = directCbs.every(c => c.checked && !c.indeterminate);
    const someSelected = directCbs.some(c => c.checked || c.indeterminate);

    if (allChecked) {
      parentCb.checked = true;
      parentCb.indeterminate = false;
      _selectedFolders.add(parentCb.dataset.path);
    } else if (someSelected) {
      parentCb.checked = false;
      parentCb.indeterminate = true;
      _selectedFolders.delete(parentCb.dataset.path);
    } else {
      parentCb.checked = false;
      parentCb.indeterminate = false;
      _selectedFolders.delete(parentCb.dataset.path);
    }

    currentNode = parentDetails.closest('.tree-node');
  }
}

function humanSize(bytes) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let n = bytes;
  for (const u of units) {
    if (n < 1024) return n.toFixed(1) + ' ' + u;
    n /= 1024;
  }
  return n.toFixed(1) + ' PB';
}

function updateScopeDisplay() {
  const countEl = $('scope-sel-count');
  const hintEl  = $('scope-sel-hint');
  const sumEl   = $('scope-summary');
  if (!countEl) return;
  const count = _selectedFolders.size;
  if (count === 0) {
    countEl.textContent = '미선택 (전체)';
    countEl.classList.remove('scope-count--active');
    if (hintEl) hintEl.textContent = '체크박스로 처리할 폴더를 선택하세요';
    if (sumEl) sumEl.classList.add('hidden');
  } else {
    countEl.textContent = `${count}개 폴더 선택됨`;
    countEl.classList.add('scope-count--active');
    if (hintEl) hintEl.textContent = '선택된 폴더만 분석합니다';

    // 중복 카운트 방지: 하위 경로가 이미 포함된 상위가 선택된 경우 pruned 경로만 사용
    const pruned = getSelectedPaths();
    let totalFiles = 0, totalSize = 0, supported = 0, unsupported = 0, hasDetail = false;
    for (const path of pruned) {
      const node = _nodeIndex[path];
      if (!node) continue;
      totalFiles += node.total_files || 0;
      totalSize  += node.total_size  || 0;
      if (typeof node.supported_files !== 'undefined') {
        supported   += node.supported_files;
        unsupported += node.unsupported_files;
        hasDetail = true;
      }
    }

    if (sumEl) {
      $('scope-sum-files').textContent = totalFiles.toLocaleString('ko-KR');
      $('scope-sum-size').textContent  = humanSize(totalSize);
      const sizeEl   = $('scope-sum-size-wrap');
      const detailEl = $('scope-sum-detail');
      if (sizeEl)   sizeEl.classList.toggle('hidden', totalSize === 0);
      if (detailEl) detailEl.classList.toggle('hidden', !hasDetail);
      if (hasDetail) {
        $('scope-sum-ready').textContent = supported.toLocaleString('ko-KR');
        $('scope-sum-skip').textContent  = unsupported.toLocaleString('ko-KR');
      }
      sumEl.classList.remove('hidden');
    }
  }
}

function getSelectedPaths() {
  const paths = [..._selectedFolders].sort();
  // Prune descendants: if "A" and "A/B" are both selected, only keep "A"
  return paths.filter((p, _, arr) =>
    !arr.some(other => other !== p && p.startsWith(other + '/'))
  );
}

function selectAllFolders() {
  const tree = $('folder-tree');
  if (!tree) return;
  tree.querySelectorAll('.folder-check').forEach(cb => {
    cb.checked = true;
    cb.indeterminate = false;
    _selectedFolders.add(cb.dataset.path);
  });
  updateScopeDisplay();
}

function clearFolderSelection() {
  const tree = $('folder-tree');
  if (!tree) return;
  tree.querySelectorAll('.folder-check').forEach(cb => {
    cb.checked = false;
    cb.indeterminate = false;
  });
  _selectedFolders.clear();
  updateScopeDisplay();
}

// ── UI State helpers ──────────────────────────────────────────────────
function showScanning() {
  btnSurvey.disabled = true;
  $('btn-delta-refresh').disabled = true;
  scanCount.textContent = '0';
  scanFolders.textContent = '0';
  scanStage.textContent = '연결 중...';
  scanFolder.textContent = '';
  secInput.classList.remove('hidden');
  secScanning.classList.remove('hidden');
  secResults.classList.add('hidden');
  secScanning.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function showResults() {
  btnSurvey.disabled = false;
  $('btn-delta-refresh').disabled = false;
  secScanning.classList.add('hidden');
  secResults.classList.remove('hidden');
  $('hbadge-sp').classList.remove('hidden');
  setStep(2);
}

function showInput() {
  btnSurvey.disabled = false;
  $('btn-delta-refresh').disabled = false;
  secScanning.classList.add('hidden');
}

function showError(msg) {
  // 분석 전 단계(조회 오류)는 인라인 배너도 유지
  $('error-msg').textContent = msg;
  errorBanner.classList.remove('hidden');
  showToast(msg, 'error');
}

function hideError() {
  errorBanner.classList.add('hidden');
  $('auth-hint').classList.add('hidden');
}

// ── Toast ─────────────────────────────────────────────────────────────
function showToast(msg, type = 'info', duration = 6000) {
  const icons = { success: '✅', error: '❌', info: 'ℹ️' };
  const container = $('toast-container');

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML =
    `<span class="toast-icon">${icons[type] || icons.info}</span>` +
    `<span class="toast-body">${esc(msg)}</span>` +
    `<button class="toast-close" aria-label="닫기">✕</button>`;

  const remove = () => {
    toast.classList.add('toast-fade-out');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  };

  toast.querySelector('.toast-close').addEventListener('click', remove);
  container.appendChild(toast);
  setTimeout(remove, duration);
}

// ── Utility ───────────────────────────────────────────────────────────
function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Phase 2/3 state ───────────────────────────────────────────────────
let _allRecords      = [];
let _filteredRecords = [];
let _selectedSeqs    = new Set();
let _selectedFolders = new Set();
let _nodeIndex       = {};
let _analysisEs      = null;
let _elapsedInterval = null;
let _analysisStartTs = null;
let _analysisSiteUrl          = '';
let _analysisRootFolder       = '';
let _analysisFolderSharingUrl = '';
let _liveFeedCount = 0;
let _cacheHits = 0;
let _reviewCount = 0;
let _errorCount  = 0;
const LIVE_FEED_MAX = 150;

// ── Stepper ───────────────────────────────────────────────────────────
function setStep(n) {
  for (let i = 1; i <= 5; i++) {
    const el = $(`step-${i}`);
    if (!el) continue;
    el.classList.remove('active', 'done');
    if (i < n)      el.classList.add('done');
    else if (i === n) el.classList.add('active');
  }
}

// ── Action Bar ────────────────────────────────────────────────────────
function updateActionBar(records) {
  if (!records || !records.length) return;
  const auto     = records.filter(r => !r.needs_manual_review && !r.conflict_detected && r.rename_status !== 'error' && !isOutOfScope(r)).length;
  const review   = records.filter(r => r.needs_manual_review && !r.conflict_detected).length;
  const conflict = records.filter(r => r.conflict_detected).length;
  const error    = records.filter(r => r.rename_status === 'error').length;
  $('ab-total').textContent    = records.length.toLocaleString('ko-KR');
  $('ab-auto').textContent     = auto.toLocaleString('ko-KR');
  $('ab-review').textContent   = review.toLocaleString('ko-KR');
  $('ab-conflict').textContent = conflict.toLocaleString('ko-KR');
  $('ab-error').textContent    = error.toLocaleString('ko-KR');
}

// ── Phase 2: Analysis ─────────────────────────────────────────────────
function showAnalysisSection() {
  _analysisSiteUrl          = $('site-url').value.trim();
  _analysisRootFolder       = $('root-folder').value.trim();
  _analysisFolderSharingUrl = $('folder-sharing-url').value.trim();
  setStep(3);
  $('sec-analysis').classList.remove('hidden');
  $('sec-analysis').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function startAnalysis() {
  const maxFiles    = parseInt($('analysis-max-files').value) || 0;
  const fast        = $('analysis-fast').checked;
  const clearCache  = $('analysis-clear-cache').checked;

  $('btn-analyze').disabled = true;
  setBadge('analysis-status-badge', 'scanning', '스캔중');
  $('sec-analysis-progress').classList.remove('hidden');
  $('live-tbody').innerHTML = '';
  _liveFeedCount = 0;
  _allRecords    = [];
  _filteredRecords = [];
  _selectedSeqs.clear();
  _cacheHits     = 0;
  _reviewCount   = 0;
  _errorCount    = 0;
  updateAnalysisStats(0, 0, 0, 0);
  updateAnalysisProgress(0, 0);

  // 이전 분석 결과(검토 섹션) 초기화
  $('sec-review').classList.add('hidden');
  $('review-tbody').innerHTML = '';
  $('review-hint').textContent = '';
  updateActionBar([]);
  updateSelectedCount();
  $('download-btns').classList.add('hidden');
  setStep(3);

  try {
    const res = await fetch('/api/analyze/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: (() => {
        const fp = getSelectedPaths();
        console.log('[분석 시작] folder_paths:', fp.length ? fp : '(전체)');
        return JSON.stringify({
          site_url: _analysisSiteUrl,
          root_folder: _analysisRootFolder,
          folder_sharing_url: _analysisFolderSharingUrl,
          max_files: maxFiles,
          fast,
          clear_cache: clearCache,
          folder_paths: fp,
        });
      })(),
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try { const d = await res.json(); msg = d.error || d.detail || msg; } catch (_) {}
      showError('분석 시작 실패: ' + msg);
      $('btn-analyze').disabled = false;
      setBadge('analysis-status-badge', 'error', '오류');
      return;
    }
    const data = await res.json();
    if (data.error) {
      showError(data.error);
      $('btn-analyze').disabled = false;
      setBadge('analysis-status-badge', 'error', '오류');
      return;
    }
  } catch (e) {
    showError('분석 시작 실패: ' + e.message);
    $('btn-analyze').disabled = false;
    setBadge('analysis-status-badge', 'error', '오류');
    return;
  }

  $('btn-analyze').classList.add('hidden');
  $('btn-stop').classList.remove('hidden');

  _analysisStartTs = Date.now();
  _elapsedInterval = setInterval(() => {
    const sec = Math.round((Date.now() - _analysisStartTs) / 1000);
    $('analysis-elapsed').textContent = sec < 60
      ? sec + '초'
      : Math.floor(sec / 60) + '분 ' + (sec % 60) + '초';
  }, 1000);

  if (_analysisEs) _analysisEs.close();
  _analysisEs = new EventSource('/api/analyze/stream');
  _analysisEs.addEventListener('message', e => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    handleAnalysisEvent(msg);
  });
  _analysisEs.onerror = () => {
    stopElapsed();
    showError('분석 스트림 연결이 끊어졌습니다.');
    $('btn-analyze').disabled = false;
  };
}

function handleAnalysisEvent(msg) {
  switch (msg.type) {
    case 'sync':
      setBadge('analysis-status-badge', msg.status, statusLabel(msg.status));
      for (const rec of (msg.records || [])) {
        _allRecords.push(rec);
        if (rec.needs_manual_review) _reviewCount++;
        if (rec.rename_status === 'error') _errorCount++;
        appendLiveFeedRow(rec);
      }
      updateAnalysisStats(msg.processed, msg.cache_hits, _reviewCount, _errorCount);
      if (msg.total > 0) updateAnalysisProgress(msg.processed, msg.total);
      if (msg.status === 'complete') {
        resetAnalysisButtons();
        $('download-btns').classList.remove('hidden');
        showReviewSection(msg.records || _allRecords);
      } else if (msg.status === 'cancelled') {
        resetAnalysisButtons();
        setBadge('analysis-status-badge', 'idle', '중단됨');
      } else if (msg.status === 'running' || msg.status === 'scanning') {
        $('btn-analyze').classList.add('hidden');
        $('btn-stop').classList.remove('hidden');
      }
      break;

    case 'scanning':
      setBadge('analysis-status-badge', 'scanning', '스캔중');
      break;

    case 'start':
      setBadge('analysis-status-badge', 'running', '분석중');
      updateAnalysisProgress(0, msg.total || 0);
      break;

    case 'record': {
      const rec = msg.record;
      _allRecords.push(rec);
      if (msg.cache_hit)           _cacheHits++;
      if (rec.needs_manual_review) _reviewCount++;
      if (rec.rename_status === 'error') _errorCount++;
      updateAnalysisProgress(msg.processed, msg.total);
      updateAnalysisStats(msg.processed, _cacheHits, _reviewCount, _errorCount);
      appendLiveFeedRow(rec);
      if (msg.active && msg.active.length) {
        $('active-files-row').classList.remove('hidden');
        $('active-files').textContent = msg.active.slice(0, 3).join(', ');
      }
      break;
    }

    case 'complete':
      _analysisEs.close();
      stopElapsed();
      resetAnalysisButtons();
      setBadge('analysis-status-badge', 'complete', '완료');
      updateAnalysisStats(msg.processed, msg.cache_hits, msg.manual_review, msg.errors);
      updateAnalysisProgress(msg.processed, msg.total);
      $('active-files-row').classList.add('hidden');
      $('download-btns').classList.remove('hidden');
      // Fetch final conflict-resolved records (mark_conflicts runs after streaming)
      fetch('/api/analyze/results')
        .then(r => r.json())
        .then(data => showReviewSection(data.records || _allRecords))
        .catch(() => showReviewSection(_allRecords));
      break;

    case 'cancelled':
      _analysisEs.close();
      stopElapsed();
      resetAnalysisButtons();
      setBadge('analysis-status-badge', 'idle', '중단됨');
      updateAnalysisProgress(msg.processed, msg.total);
      $('active-files-row').classList.add('hidden');
      showToast(`분석 중단: ${msg.processed}/${msg.total}개 처리됨`, 'info');
      break;

    case 'error':
      _analysisEs.close();
      stopElapsed();
      resetAnalysisButtons();
      setBadge('analysis-status-badge', 'error', '오류');
      showError(msg.message || '분석 중 오류 발생');
      break;
  }
}

function updateAnalysisProgress(processed, total) {
  const pct = total > 0 ? Math.round(processed / total * 100) : 0;
  $('analysis-progress-bar').style.width = pct + '%';
  $('analysis-progress-text').textContent =
    processed.toLocaleString('ko-KR') + ' / ' + total.toLocaleString('ko-KR');
}

function updateAnalysisStats(processed, cache, review, errors) {
  $('a-stat-processed').textContent = processed.toLocaleString('ko-KR');
  $('a-stat-cache').textContent     = cache.toLocaleString('ko-KR');
  $('a-stat-review').textContent    = review.toLocaleString('ko-KR');
  $('a-stat-errors').textContent    = errors.toLocaleString('ko-KR');
}

function appendLiveFeedRow(rec) {
  _liveFeedCount++;
  const tbody = $('live-tbody');
  while (tbody.rows.length >= LIVE_FEED_MAX) tbody.deleteRow(0);

  const tr = document.createElement('tr');
  if (rec.needs_manual_review)     tr.classList.add('row-review');
  if (rec.rename_status === 'error') tr.classList.add('row-error');
  const reason = rec.reason || '';
  tr.innerHTML =
    `<td class="col-seq">${rec.seq}</td>` +
    `<td class="cell-name" title="${esc(rec.original_full_path)}">${_fileNameCell(rec)}</td>` +
    `<td class="cell-name" title="${esc(rec.suggested_full_path)}">${esc(rec.suggested_file_name)}</td>` +
    `<td class="col-conf">${confBadge(rec.confidence)}</td>` +
    `<td class="col-status">${statusBadge(rec)}</td>` +
    `<td class="cell-reason" title="${esc(reason)}">${esc(reason.slice(0, 60))}${reason.length > 60 ? '…' : ''}</td>`;
  // Insert in seq order
  let inserted = false;
  for (let i = tbody.rows.length - 1; i >= 0; i--) {
    const rowSeq = parseInt(tbody.rows[i].cells[0].textContent);
    if (rec.seq >= rowSeq) {
      tbody.rows[i].insertAdjacentElement('afterend', tr);
      inserted = true;
      break;
    }
  }
  if (!inserted) tbody.insertBefore(tr, tbody.firstChild);

  const wrap = tbody.closest('.live-feed');
  if (wrap) wrap.scrollTop = wrap.scrollHeight;
}

function stopElapsed() {
  if (_elapsedInterval) { clearInterval(_elapsedInterval); _elapsedInterval = null; }
}

function resetAnalysisButtons() {
  $('btn-analyze').disabled = false;
  $('btn-analyze').classList.remove('hidden');
  $('btn-stop').classList.add('hidden');
  $('btn-stop').disabled = false;
  $('btn-stop').textContent = '분석 중단';
}

async function stopAnalysis() {
  const btn = $('btn-stop');
  btn.disabled = true;
  btn.textContent = '중단 요청 중...';
  try {
    await fetch('/api/analyze/stop', { method: 'POST' });
  } catch (e) {
    showError('중단 요청 실패: ' + e.message);
    btn.disabled = false;
    btn.textContent = '분석 중단';
  }
}

function statusLabel(s) {
  return { idle: '대기중', scanning: '스캔중', running: '분석중', complete: '완료', error: '오류' }[s] || s;
}

function setBadge(id, status, label) {
  const el = $(id);
  el.textContent = label;
  el.className = 'status-badge badge-' + status;
}

function confBadge(conf) {
  const pct = Math.round((conf || 0) * 100);
  const cls = pct >= 80 ? 'conf-high' : pct >= 50 ? 'conf-mid' : 'conf-low';
  return `<span class="conf-badge ${cls}">${pct}%</span>`;
}

function statusBadge(rec) {
  if (rec.is_renamed)               return `<span class="sbadge sbadge-renamed">완료</span>`;
  if (rec.rename_failed)            return `<span class="sbadge sbadge-rename-failed">실패</span>`;
  if (rec.rename_status === 'error') return `<span class="sbadge sbadge-error">오류</span>`;
  if (rec.rename_status === 'out_of_scope') return `<span class="sbadge sbadge-skip" title="'4. 기타문서' 경로가 아니어서 개명 대상에서 제외 — 원본명 유지">대상외</span>`;
  if (rec.conflict_detected)        return `<span class="sbadge sbadge-conflict">중복</span>`;
  if (rec.needs_manual_review)      return `<span class="sbadge sbadge-review">검토필요</span>`;
  return `<span class="sbadge sbadge-ok">자동승인</span>`;
}

// 개명 대상 외(out_of_scope) 레코드 판별 — 자동승인/선택 집계에서 제외
function isOutOfScope(rec) {
  return rec.rename_status === 'out_of_scope';
}

// ── LLM API key test ─────────────────────────────────────────────────
async function testLlmKey() {
  const btn = $('btn-llm-test');
  if (btn) { btn.disabled = true; btn.dataset._label = btn.textContent; btn.textContent = '확인 중...'; }
  try {
    const res = await fetch('/api/llm/test', { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      showToast(`✅ OpenAI 연결 성공 — 모델 ${data.model}${data.key_preview ? ' · 키 ' + data.key_preview : ''}`, 'success', 6000);
    } else {
      showToast(`❌ OpenAI 연결 실패: ${data.error || '알 수 없는 오류'}`, 'error', 9000);
    }
  } catch (e) {
    showToast('❌ API 키 테스트 요청 실패: ' + e.message, 'error', 8000);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset._label || '🔑 API 키 테스트'; }
  }
}

// ── File name cell (with SharePoint link) ────────────────────────────
function _fileNameCell(rec) {
  const name = esc(rec.original_file_name);
  const url  = rec.sharepoint_web_url;
  if (url) {
    return `<a class="file-link" href="${esc(url)}" target="_blank" rel="noopener" title="${esc(rec.original_full_path)}">${name}</a>`;
  }
  return name;
}

// ── Proposed name cell helpers ───────────────────────────────────────
const _PENCIL_SVG =
  `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">` +
  `<path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25z"/>` +
  `<path d="M20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>` +
  `</svg>`;

function _proposedCellHtml(rec) {
  const editedMark = rec.manually_edited
    ? `<span class="name-edited-mark" title="수동 수정됨">✎</span>` : '';
  return `<td class="cell-name cell-name-proposed">` +
    `<span class="proposed-text" title="${esc(rec.suggested_full_path)}">${esc(rec.suggested_file_name)}</span>` +
    editedMark +
    `<button class="btn-edit-name" title="파일명 직접 수정" onclick="startEditName(${rec.seq})">${_PENCIL_SVG}</button>` +
    `</td>`;
}

function startEditName(seq) {
  const rec = _allRecords.find(r => r.seq === seq);
  if (!rec) return;
  const cb = $('review-tbody')?.querySelector(`input[data-seq="${seq}"]`);
  if (!cb) return;
  const td = cb.closest('tr').cells[2];

  td.className = 'cell-name cell-name-proposed cell-editing';
  td.innerHTML =
    `<input class="name-edit-input" value="${esc(rec.suggested_file_name)}" />` +
    `<div class="name-edit-btns">` +
      `<button class="btn-name-save"  title="저장 (Enter)" onclick="saveEditName(${seq})">✓</button>` +
      `<button class="btn-name-cancel" title="취소 (Esc)"  onclick="cancelEditName(${seq})">✕</button>` +
    `</div>`;

  const input = td.querySelector('.name-edit-input');
  input.focus();
  // 확장자 앞까지만 선택
  const dotIdx = input.value.lastIndexOf('.');
  input.setSelectionRange(0, dotIdx > 0 ? dotIdx : input.value.length);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); saveEditName(seq); }
    if (e.key === 'Escape') { e.preventDefault(); cancelEditName(seq); }
  });
}

function saveEditName(seq) {
  const rec = _allRecords.find(r => r.seq === seq);
  if (!rec) return;
  const cb = $('review-tbody')?.querySelector(`input[data-seq="${seq}"]`);
  if (!cb) return;
  const td = cb.closest('tr').cells[2];
  const input = td.querySelector('.name-edit-input');
  if (!input) return;

  const newName = input.value.trim();
  if (!newName) { showToast('파일명을 입력하세요.', 'error'); input.focus(); return; }
  if (/[\\/:*?"<>|]/.test(newName)) {
    showToast('파일명에 사용할 수 없는 문자: \\ / : * ? " < > |', 'error');
    input.focus(); return;
  }

  rec.suggested_file_name = newName;
  // full_path의 파일명 부분만 교체
  if (rec.suggested_full_path) {
    const normalized = rec.suggested_full_path.replace(/\\/g, '/');
    const dir = normalized.substring(0, normalized.lastIndexOf('/') + 1);
    rec.suggested_full_path = dir + newName;
  }
  rec.manually_edited = true;

  td.className = 'cell-name cell-name-proposed';
  td.outerHTML = _proposedCellHtml(rec);  // replace entire cell
}

function cancelEditName(seq) {
  const rec = _allRecords.find(r => r.seq === seq);
  if (!rec) return;
  const cb = $('review-tbody')?.querySelector(`input[data-seq="${seq}"]`);
  if (!cb) return;
  const td = cb.closest('tr').cells[2];
  td.outerHTML = _proposedCellHtml(rec);
}

// ── Phase 3: Review ───────────────────────────────────────────────────
function showReviewSection(records) {
  _allRecords = records;
  applyFilter();
  updateActionBar(records);
  setStep(4);
  $('sec-review').classList.remove('hidden');
  $('sec-review').scrollIntoView({ behavior: 'smooth', block: 'start' });
  loadRollbackList();
}

function applyFilter() {
  const filter = document.querySelector('input[name="r-filter"]:checked')?.value || 'all';
  if (filter === 'auto') {
    _filteredRecords = _allRecords.filter(r => !r.needs_manual_review && !r.conflict_detected && r.rename_status !== 'error' && !isOutOfScope(r));
  } else if (filter === 'review') {
    _filteredRecords = _allRecords.filter(r => r.needs_manual_review && !r.conflict_detected);
  } else if (filter === 'conflict') {
    _filteredRecords = _allRecords.filter(r => r.conflict_detected);
  } else {
    _filteredRecords = [..._allRecords];
  }

  $('review-hint').textContent = _filteredRecords.length.toLocaleString('ko-KR') + '개 항목';

  const tbody = $('review-tbody');
  tbody.innerHTML = '';
  for (const rec of _filteredRecords) {
    const checked = _selectedSeqs.has(rec.seq);
    const tr = document.createElement('tr');
    if (rec.needs_manual_review) tr.classList.add('row-review');
    if (rec.conflict_detected)   tr.classList.add('row-conflict');
    tr.innerHTML =
      `<td class="col-check"><input type="checkbox" data-seq="${rec.seq}" ${checked ? 'checked' : ''} onchange="toggleSelect(${rec.seq}, this.checked)"></td>` +
      `<td class="cell-name" title="${esc(rec.original_full_path)}">${_fileNameCell(rec)}</td>` +
      _proposedCellHtml(rec) +
      `<td class="col-conf">${confBadge(rec.confidence)}</td>` +
      `<td class="col-rstatus">${statusBadge(rec)}</td>` +
      `<td class="cell-summary" title="${esc(rec.summary)}">${esc((rec.summary || '').slice(0, 60))}${(rec.summary || '').length > 60 ? '…' : ''}</td>`;
    tbody.appendChild(tr);
  }
  updateSelectedCount();
}

function toggleSelect(seq, checked) {
  if (checked) _selectedSeqs.add(seq);
  else _selectedSeqs.delete(seq);
  updateSelectedCount();
  const allChecked = _filteredRecords.length > 0 && _filteredRecords.every(r => _selectedSeqs.has(r.seq));
  const someChecked = _selectedSeqs.size > 0;
  $('check-all').checked = allChecked;
  $('check-all').indeterminate = !allChecked && someChecked;
}

function toggleSelectAll(checked) {
  _filteredRecords.forEach(r => checked ? _selectedSeqs.add(r.seq) : _selectedSeqs.delete(r.seq));
  $('review-tbody').querySelectorAll('input[type=checkbox]').forEach(cb => { cb.checked = checked; });
  updateSelectedCount();
}

function updateSelectedCount() {
  $('selected-count').textContent = _selectedSeqs.size.toLocaleString('ko-KR') + '개 선택됨';
  $('btn-rename').disabled = _selectedSeqs.size === 0;
}

function _setRowStatus(seq, badgeHtml, addCls) {
  const cb = $('review-tbody')?.querySelector(`input[data-seq="${seq}"]`);
  if (!cb) return;
  const tr = cb.closest('tr');
  if (!tr) return;
  if (tr.cells[4]) tr.cells[4].innerHTML = badgeHtml;
  if (addCls) tr.classList.add(addCls);
}

async function executeRename() {
  const selected = _allRecords.filter(r => _selectedSeqs.has(r.seq));
  if (!selected.length) return;

  const btn = $('btn-rename');
  btn.disabled = true;
  const origHTML = btn.innerHTML;
  btn.textContent = '처리중...';

  // 즉시 선택된 모든 행을 "처리중" 상태로 표시
  selected.forEach(r =>
    _setRowStatus(r.seq, `<span class="sbadge sbadge-processing">처리중</span>`)
  );

  try {
    const res = await fetch('/api/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        items: selected.map(r => ({
          original_full_path:  r.original_full_path,
          suggested_full_path: r.suggested_full_path,
          sharepoint_item_id:  r.sharepoint_item_id || '',
          manually_edited:     r.manually_edited || false,
        })),
        site_url:           _analysisSiteUrl,
        root_folder:        _analysisRootFolder,
        folder_sharing_url: _analysisFolderSharingUrl,
      }),
    });
    const data = await res.json();
    if (data.error) {
      // 실패 시 원래 상태로 복원
      selected.forEach(r => _setRowStatus(r.seq, statusBadge(r)));
      showError(data.error);
    } else {
      const ok   = data.success_count ?? 0;
      const fail = (data.processed_count ?? ok) - ok;

      // 파일별 결과 맵 구성 (original_full_path → status)
      const resultMap = {};
      (data.file_results || []).forEach(fr => { resultMap[fr.original_full_path] = fr.status; });

      // 폴더 이름 변경 이력 저장
      _markFoldersRenamed(selected);

      // 순차적으로 "완료"/"실패" 상태로 전환 (40ms 스태거)
      for (let i = 0; i < selected.length; i++) {
        const r = selected[i];
        const fileStatus = resultMap[r.original_full_path] ?? 'success';
        if (fileStatus === 'rename_failed') {
          r.rename_failed = true;
        } else {
          r.is_renamed = true;
        }
        _setRowStatus(r.seq, statusBadge(r), r.is_renamed ? 'row-renamed' : null);
        if (i < selected.length - 1) {
          await new Promise(resolve => setTimeout(resolve, 40));
        }
      }

      showSuccessBanner(`파일명 변경 완료: ${ok}개 성공` + (fail ? `, ${fail}개 실패` : ''));
      setStep(5);

      // 체크박스 선택 상태 해제 (테이블 재빌드 없이)
      _selectedSeqs.clear();
      $('review-tbody').querySelectorAll('input[type=checkbox]').forEach(cb => { cb.checked = false; });
      $('check-all').checked = false;
      $('check-all').indeterminate = false;
      updateSelectedCount();
      updateActionBar(_allRecords);
      await loadRollbackList();
    }
  } catch (e) {
    selected.forEach(r => _setRowStatus(r.seq, statusBadge(r)));
    showError('파일명 변경 실패: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = origHTML;
  }
}

// ── Rename execution history (localStorage) ──────────────────────────
const _RENAME_HX_KEY = 'legaldb_rename_hx';
let _renameHistory = {};

function _fmtRenameDate(isoDate) {
  const d = new Date(isoDate);
  const ymd = d.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' });
  const hm  = d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
  return `${ymd.replace(/\.\s*/g, '.').replace(/\.$/, '')} ${hm}`;
}

function _loadRenameHistory() {
  try { _renameHistory = JSON.parse(localStorage.getItem(_RENAME_HX_KEY) || '{}'); }
  catch (_) { _renameHistory = {}; }
}

function _markFoldersRenamed(records) {
  const now = new Date().toISOString();
  // Collect unique folder paths from renamed records
  const affectedFolders = new Set();
  records.forEach(r => {
    const rp = (r.relative_path || '').replace(/\\/g, '/');
    const folder = rp.includes('/') ? rp.split('/').slice(0, -1).join('/') : '';
    if (folder) affectedFolders.add(folder);
  });
  affectedFolders.forEach(fp => {
    _renameHistory[fp] = { lastRenamedAt: now };
    _applyRenameBadge(fp, now);
  });
  try { localStorage.setItem(_RENAME_HX_KEY, JSON.stringify(_renameHistory)); } catch (_) {}
}

function _applyRenameBadge(folderPath, isoDate) {
  const tree = $('folder-tree');
  if (!tree) return;
  const cb = tree.querySelector(`.folder-check[data-path="${CSS.escape(folderPath)}"]`);
  if (!cb) return;
  const container = cb.parentElement; // summary or .tree-leaf
  const cell = container.querySelector('.rename-badge-cell');
  if (!cell) return;
  cell.innerHTML = `<span class="rename-badge">✓ ${_fmtRenameDate(isoDate)}</span>`;
}

// ── Rollback execution history (localStorage) ────────────────────────
const _ROLLBACK_HX_KEY = 'legaldb_rollback_hx';

function _getRollbackHistory() {
  try { return JSON.parse(localStorage.getItem(_ROLLBACK_HX_KEY) || '{}'); }
  catch (_) { return {}; }
}

function _saveRollbackExecution(filename, restored, folderPaths = []) {
  const hx = _getRollbackHistory();
  hx[filename] = { executedAt: new Date().toISOString(), restored, folderPaths };
  try { localStorage.setItem(_ROLLBACK_HX_KEY, JSON.stringify(hx)); } catch (_) {}
}

// ── Rollback ──────────────────────────────────────────────────────────
const ROLLBACK_PAGE_SIZE = 5;
let _rollbackPage  = 1;
let _rollbackFiles = [];

async function loadRollbackList() {
  try {
    const res = await fetch('/api/rollback/list');
    _rollbackFiles = await res.json();
    _rollbackPage  = 1;
    _renderRollbackPage();
  } catch (e) {
    showError('롤백 목록 불러오기 실패: ' + e.message);
  }
}

function _renderRollbackPage() {
  const el    = $('rollback-list');
  const pagEl = $('rollback-pagination');
  const total = _rollbackFiles.length;
  const totalPages = Math.max(1, Math.ceil(total / ROLLBACK_PAGE_SIZE));
  if (_rollbackPage > totalPages) _rollbackPage = totalPages;

  const start     = (_rollbackPage - 1) * ROLLBACK_PAGE_SIZE;
  const pageFiles = _rollbackFiles.slice(start, start + ROLLBACK_PAGE_SIZE);

  if (!total) {
    el.innerHTML = '<p class="empty-msg">롤백 파일이 없습니다.</p>';
    pagEl.classList.add('hidden');
    return;
  }

  const hx = _getRollbackHistory();
  el.innerHTML = '';

  for (const f of pageFiles) {
    const exec = hx[f.filename];
    const row  = document.createElement('div');
    row.className = 'rollback-item' + (exec ? ' rollback-item--executed' : '');
    const createdDt = new Date(f.mtime * 1000).toLocaleString('ko-KR');

    let execHtml = '';
    if (exec) {
      const fps = exec.folderPaths || [];
      const scopeHtml = fps.length
        ? `<span class="rollback-exec-scope">(선택 폴더 ${fps.length}개)</span>`
        : `<span class="rollback-exec-scope">(전체)</span>`;
      execHtml =
        `<div class="rollback-exec-row">` +
          `<span class="rollback-exec-badge">✓ 실행됨</span>` +
          `<span class="rollback-exec-dt">${new Date(exec.executedAt).toLocaleString('ko-KR')} · ${exec.restored}개 복원 ${scopeHtml}</span>` +
        `</div>`;
    }

    const siteHtml = f.site_url
      ? `<span class="rollback-site-sep">·</span><span class="rollback-site" title="${esc(f.site_url)}">${esc(f.site_url)}</span>`
      : '';
    row.innerHTML =
      `<div class="rollback-icon">↩</div>` +
      `<div class="rollback-info">` +
        `<span class="rollback-name">${esc(f.filename)}</span>` +
        `<div class="rollback-meta">` +
          `<span class="rollback-dt">생성: ${createdDt}</span>` +
          siteHtml +
        `</div>` +
        execHtml +
      `</div>` +
      `<div class="rollback-actions"></div>`;

    const actions = row.querySelector('.rollback-actions');

    const execBtn = document.createElement('button');
    execBtn.type = 'button';
    execBtn.className = 'btn btn-sm btn-danger';
    execBtn.textContent = exec ? '재실행' : '롤백 실행';
    execBtn.onclick = () => executeRollback(f.path);
    actions.appendChild(execBtn);

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'btn btn-sm btn-delete';
    delBtn.title = '이력 파일 삭제';
    delBtn.innerHTML =
      `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">` +
        `<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>` +
        `<path d="M10 11v6"/><path d="M14 11v6"/>` +
        `<path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>` +
      `</svg>`;
    delBtn.onclick = () => deleteRollbackFile(f.path, f.filename);
    actions.appendChild(delBtn);

    el.appendChild(row);
  }

  // Pagination
  if (totalPages <= 1) {
    pagEl.classList.add('hidden');
  } else {
    pagEl.classList.remove('hidden');
    const prev = _rollbackPage > 1;
    const next = _rollbackPage < totalPages;
    let html = `<span class="pg-info">${total}개 이력</span>`;
    html += `<button class="pg-btn" ${prev ? '' : 'disabled'} onclick="_rollbackGoPage(${_rollbackPage - 1})">‹</button>`;
    for (let p = 1; p <= totalPages; p++) {
      html += `<button class="pg-btn${p === _rollbackPage ? ' pg-btn--active' : ''}" onclick="_rollbackGoPage(${p})">${p}</button>`;
    }
    html += `<button class="pg-btn" ${next ? '' : 'disabled'} onclick="_rollbackGoPage(${_rollbackPage + 1})">›</button>`;
    pagEl.innerHTML = html;
  }
}

function _rollbackGoPage(page) {
  _rollbackPage = page;
  _renderRollbackPage();
}

async function deleteRollbackFile(path, filename) {
  if (!confirm(`"${filename}"\n\n이 롤백 이력을 삭제하시겠습니까?\n삭제 후 해당 이력으로 복원할 수 없습니다.`)) return;
  try {
    const res  = await fetch('/api/rollback/delete', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rollback_file: path }),
    });
    const data = await res.json();
    if (data.error) {
      showError('삭제 실패: ' + data.error);
    } else {
      _rollbackFiles = _rollbackFiles.filter(f => f.path !== path);
      _renderRollbackPage();
      showToast(`"${filename}" 삭제 완료`, 'success', 3000);
    }
  } catch (e) {
    showError('삭제 실패: ' + e.message);
  }
}

function executeRollback(path) {
  const modal     = $('confirm-modal');
  const fileEl    = $('modal-file-name');
  const scopeEl   = $('modal-scope-info');
  const btnOk     = $('modal-confirm');
  const btnCancel = $('modal-cancel');

  const folderPaths = getSelectedPaths();
  fileEl.textContent = path.split(/[\\/]/).pop();

  // 폴더 선택 상태를 즉시 표시 (API 호출 없음)
  if (folderPaths.length === 0) {
    scopeEl.innerHTML = `<span class="modal-scope-all">범위: 전체 파일 복원</span>`;
  } else {
    const scopeNames = folderPaths.slice(0, 3).map(p => esc(p.split('/').pop())).join(', ');
    const extra = folderPaths.length > 3 ? ` 외 ${folderPaths.length - 3}개 폴더` : '';
    scopeEl.innerHTML =
      `<span class="modal-scope-filtered">` +
        `범위: 선택된 <strong>${folderPaths.length}개 폴더</strong>만 복원` +
        `<br><span class="modal-scope-folders">${scopeNames}${extra}</span>` +
      `</span>`;
  }
  modal.classList.remove('hidden');

  const cleanup = () => {
    modal.classList.add('hidden');
    scopeEl.innerHTML = '';
    btnOk.removeEventListener('click', onConfirm);
    btnCancel.removeEventListener('click', onCancel);
    modal.removeEventListener('click', onOverlay);
  };

  const onConfirm = async () => {
    cleanup();
    btnOk.disabled = true;
    btnOk.textContent = '롤백 실행 중...';

    const overlay = document.createElement('div');
    overlay.className = 'rollback-running-overlay';
    overlay.innerHTML =
      `<div class="rollback-running-box">` +
        `<div class="spinner"></div>` +
        `<div>롤백 실행 중입니다. 잠시 기다려 주세요...</div>` +
      `</div>`;
    document.body.appendChild(overlay);

    try {
      const res = await fetch('/api/rollback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          rollback_file:      path,
          site_url:           _analysisSiteUrl,
          root_folder:        _analysisRootFolder,
          folder_sharing_url: _analysisFolderSharingUrl,
          folder_paths:       folderPaths,
        }),
      });
      const data = await res.json();
      if (data.error) {
        showError('롤백 실패: ' + data.error);
      } else {
        const filename = path.split(/[\\/]/).pop();
        _saveRollbackExecution(filename, data.restored ?? 0, folderPaths);
        const scopeNote = folderPaths.length ? ` (선택 폴더 ${folderPaths.length}개)` : '';
        showSuccessBanner(`롤백 완료: ${data.restored ?? 0}개 파일 복원${scopeNote}`);
        await loadRollbackList();
      }
    } catch (e) {
      showError('롤백 실패: ' + e.message);
    } finally {
      overlay.remove();
      btnOk.disabled = false;
      btnOk.textContent = '롤백 실행';
    }
  };

  const onCancel  = () => cleanup();
  const onOverlay = (e) => { if (e.target === modal) cleanup(); };

  btnOk.addEventListener('click', onConfirm);
  btnCancel.addEventListener('click', onCancel);
  modal.addEventListener('click', onOverlay);
}

// ── Download ──────────────────────────────────────────────────────────
function downloadJSON() {
  const ts   = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
  const name = `analysis_results_${ts}.json`;
  const blob = new Blob(
    [JSON.stringify(_allRecords, null, 2)],
    { type: 'application/json' }
  );
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Success Banner → Toast ────────────────────────────────────────────
function showSuccessBanner(msg) {
  showToast(msg, 'success');
}
