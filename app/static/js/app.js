const SERVICE_ORDER = ["simple_service", "purchase_consult"];
const SERVICE_META = window.SERVICE_META || {};
const ADMIN_KEY_STORAGE = "codenote_staff_call_admin_key";
const VOICE_STORAGE = "codenote_staff_call_voice_enabled";

const appState = {
  route: window.location.pathname,
  customerStores: [],
  adminStores: [],
  customerSearch: "",
  adminSearch: "",
  manageSearch: "",
  selectedCustomerStore: null,
  selectedAdminStore: null,
  displayStore: null,
  ticket: null,
  adminKey: localStorage.getItem(ADMIN_KEY_STORAGE) || "",
  adminState: null,
  displayState: null,
  modalOpen: false,
  managementUnlocked: false,
  voiceEnabled: localStorage.getItem(VOICE_STORAGE) === "1",
  lastCallId: 0,
  pollingTimer: null,
  callTimer: null,
};

const $app = document.getElementById("app");
const $overlay = document.getElementById("callOverlay");
const $callCard = document.getElementById("callCard");
const $overlayService = document.getElementById("overlayService");
const $overlayNumber = document.getElementById("overlayNumber");
const $overlayMessage = document.getElementById("overlayMessage");
const $closeOverlay = document.getElementById("closeOverlay");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function serviceMeta(serviceType) {
  return SERVICE_META[serviceType] || {
    customer_label: serviceType,
    admin_label: serviceType,
    voice_label: serviceType,
    theme: "blue",
  };
}

function nowLabel() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  const hour = String(now.getHours()).padStart(2, "0");
  const minute = String(now.getMinutes()).padStart(2, "0");
  return `${month}월${day}일${hour}시${minute}분`;
}

function setPolling(callback, milliseconds = 1000) {
  clearPolling();
  appState.pollingTimer = window.setInterval(callback, milliseconds);
}

function clearPolling() {
  if (appState.pollingTimer) {
    window.clearInterval(appState.pollingTimer);
    appState.pollingTimer = null;
  }
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.json);
  }
  if (options.admin) {
    headers.set("X-Admin-Key", appState.adminKey);
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let message = "요청 처리 중 오류가 발생했습니다";
    try {
      const payload = await response.json();
      message = payload.detail || payload.message || message;
    } catch (_error) {
      message = await response.text();
    }
    throw new Error(message);
  }
  return response.json();
}

function go(path) {
  window.location.href = path;
}

function headerHtml(title, subtitle = "", actions = "") {
  return `
    <header class="topbar">
      <div>
        <h1 class="brand-title">${escapeHtml(title)}</h1>
        ${subtitle ? `<p class="sub-title">${escapeHtml(subtitle)}</p>` : ""}
      </div>
      ${actions ? `<div class="admin-header-actions">${actions}</div>` : ""}
    </header>
  `;
}

function renderHome() {
  $app.innerHTML = `
    <main class="home-wrap">
      <section class="home-card">
        <div class="home-logo">C</div>
        <h1 class="brand-title">직원 호출</h1>
        <p class="sub-title">고객 번호표 발급과 관리자 호출을 한 화면에서 관리합니다.</p>
        <div class="home-buttons">
          <button class="btn btn-primary" data-link="/customer">고객</button>
          <button class="btn btn-ghost" data-link="/admin">관리자</button>
        </div>
      </section>
    </main>
  `;
}

function renderStoreList(stores, actionName) {
  if (!stores.length) {
    return `<div class="queue-empty">표시할 매장이 없습니다</div>`;
  }
  return stores.map((store) => `
    <button class="store-item" type="button" data-action="${actionName}" data-store-id="${store.id}">
      <span class="store-name">${escapeHtml(store.name)}</span>
      <span class="store-status">${store.is_active ? "사용 가능" : "비활성"}</span>
    </button>
  `).join("");
}

function renderCustomer() {
  const selected = appState.selectedCustomerStore;
  const actions = `<button class="btn btn-ghost btn-small" data-link="/">처음으로</button>`;

  if (!selected) {
    $app.innerHTML = `
      ${headerHtml("고객", "매장을 검색하고 선택하세요.", actions)}
      <section class="layout-card">
        <div class="search-row">
          <input id="customerSearch" class="input" value="${escapeHtml(appState.customerSearch)}" placeholder="매장명 검색" />
          <button class="btn btn-primary" data-action="customerSearch">검색</button>
        </div>
        <div class="store-list">${renderStoreList(appState.customerStores, "selectCustomerStore")}</div>
      </section>
    `;
    return;
  }

  const ticket = appState.ticket;
  $app.innerHTML = `
    ${headerHtml(
      selected.name,
      "업무를 선택하면 번호표가 발급됩니다.",
      `<button class="btn btn-ghost btn-small" data-action="changeCustomerStore">매장 변경</button><button class="btn btn-ghost btn-small" data-link="/">처음으로</button>`
    )}
    <section class="layout-card">
      ${ticket ? renderTicketResult(ticket) : renderCustomerServiceButtons()}
    </section>
  `;
}

function renderCustomerServiceButtons() {
  return `
    <div class="service-select">
      <button class="btn btn-primary" data-action="issueTicket" data-service="simple_service">간단서비스</button>
      <button class="btn btn-red" data-action="issueTicket" data-service="purchase_consult">구매문의</button>
    </div>
  `;
}

function renderTicketResult(ticket) {
  const meta = serviceMeta(ticket.service_type);
  const isRed = meta.theme === "red";
  return `
    <div class="ticket-result">
      <div class="ticket-label">${escapeHtml(meta.customer_label)}</div>
      <div class="ticket-number ${isRed ? "red" : ""}">${ticket.ticket_number}</div>
      <div class="ticket-guide">번호표가 발급되었습니다.</div>
      <p class="sub-title">호출될 때까지 잠시만 기다려주세요.</p>
      <div class="service-select mt-3">
        <button class="btn ${isRed ? "btn-red" : "btn-primary"}" data-action="issueAgain">같은 업무 번호표 추가 발급</button>
        <button class="btn btn-ghost" data-action="clearTicket">다른 업무 선택</button>
      </div>
    </div>
  `;
}

function renderAdmin() {
  if (!appState.adminKey) {
    clearPolling();
    $app.innerHTML = `
      ${headerHtml("관리자", "인증키를 입력하세요.", `<button class="btn btn-ghost btn-small" data-link="/">처음으로</button>`)}
      <section class="layout-card">
        <div class="search-row">
          <input id="adminKey" class="input" type="password" placeholder="인증키" autocomplete="current-password" />
          <button class="btn btn-primary" data-action="adminLogin">확인</button>
        </div>
        <p class="sub-title">기본 인증키는 서버 환경변수 ADMIN_KEY로 변경할 수 있습니다.</p>
      </section>
    `;
    return;
  }

  if (!appState.selectedAdminStore) {
    clearPolling();
    $app.innerHTML = `
      ${headerHtml(
        "관리자",
        "관리할 매장을 선택하세요.",
        `<button class="btn btn-ghost btn-small" data-action="openManage">⚙ 매장관리</button><button class="btn btn-ghost btn-small" data-action="logoutAdmin">로그아웃</button><button class="btn btn-ghost btn-small" data-link="/">처음으로</button>`
      )}
      <section class="layout-card">
        <div class="search-row">
          <input id="adminStoreSearch" class="input" value="${escapeHtml(appState.adminSearch)}" placeholder="매장명 검색" />
          <button class="btn btn-primary" data-action="adminStoreSearch">검색</button>
        </div>
        <div class="store-list">${renderStoreList(appState.adminStores.filter((store) => store.is_active), "selectAdminStore")}</div>
      </section>
      ${renderManageModal()}
    `;
    return;
  }

  const displayUrl = `/display?store_id=${appState.selectedAdminStore.id}`;
  $app.innerHTML = `
    ${headerHtml(
      appState.selectedAdminStore.name,
      "갤럭시 컨설턴트와 구매상담을 각각 따로 호출합니다.",
      `<a class="btn btn-ghost btn-small" href="${displayUrl}" target="_blank" rel="noopener">호출화면</a><button class="btn btn-ghost btn-small" data-action="changeAdminStore">매장 변경</button><button class="btn btn-ghost btn-small" data-action="openManage">⚙ 매장관리</button><button class="btn btn-ghost btn-small" data-action="logoutAdmin">로그아웃</button>`
    )}
    <section class="grid-two">
      ${SERVICE_ORDER.map((serviceType) => renderAdminServiceCard(serviceType)).join("")}
    </section>
    ${renderManageModal()}
  `;
}

function renderAdminServiceCard(serviceType) {
  const meta = serviceMeta(serviceType);
  const serviceState = appState.adminState?.services?.[serviceType];
  const theme = meta.theme;
  const current = serviceState?.current_number;
  const waitingCount = serviceState?.waiting_count ?? 0;
  const nextNumber = serviceState?.next_number ?? 1;
  const queue = serviceState?.waiting_tickets || [];
  return `
    <article class="service-card ${theme}">
      <div class="service-head">
        <div class="service-title-wrap">
          <div class="service-icon ${theme}">${theme === "red" ? "▣" : "♡"}</div>
          <div>
            <div class="service-small ${theme}">${theme === "red" ? "Premium Manager" : "Galaxy AI Consultant"}</div>
            <div class="service-title">${escapeHtml(meta.admin_label)}</div>
          </div>
        </div>
        <div class="now-time">${nowLabel()}</div>
      </div>

      <div class="current-box">
        ${current ? `<div class="current-number ${theme}">${current}</div>` : `<div class="current-number wait">대기중</div>`}
      </div>

      <div class="state-mini">
        <div class="state-pill"><span>현재 대기인원</span><strong>${waitingCount}명</strong></div>
        <div class="state-pill"><span>다음 발급번호</span><strong>${nextNumber}번</strong></div>
      </div>

      <div class="action-grid">
        <button class="btn ${theme === "red" ? "btn-red" : "btn-primary"}" data-action="call" data-call-type="normal" data-service="${serviceType}">호출</button>
        <button class="btn ${theme === "red" ? "btn-red-soft" : "btn-blue-soft"}" data-action="call" data-call-type="recall" data-service="${serviceType}">재호출</button>
        <button class="btn ${theme === "red" ? "btn-outline-red" : "btn-outline-blue"}" data-action="directCall" data-service="${serviceType}">지정호출</button>
        <button class="btn btn-danger" data-action="resetService" data-service="${serviceType}">초기화</button>
      </div>

      <div class="queue-title">대기 목록</div>
      ${queue.length ? `<div class="queue-list">${queue.map((ticket) => `<span class="queue-chip">${ticket.ticket_number}</span>`).join("")}</div>` : `<div class="queue-empty">대기 중인 고객이 없습니다</div>`}
    </article>
  `;
}

function renderManageModal() {
  if (!appState.modalOpen) return "";
  return `
    <div class="modal-backdrop" data-action="modalBackdrop">
      <section class="modal" role="dialog" aria-modal="true" aria-label="매장 관리">
        <div class="modal-head">
          <div class="modal-title">매장 관리</div>
          <button class="close-btn" type="button" data-action="closeManage">×</button>
        </div>
        ${appState.managementUnlocked ? renderManageContent() : renderManageUnlock()}
      </section>
    </div>
  `;
}

function renderManageUnlock() {
  return `
    <p class="sub-title">매장 생성, 수정, 삭제, 백업, 복원은 인증키를 다시 입력해야 사용할 수 있습니다.</p>
    <div class="search-row mt-2">
      <input id="manageKey" class="input" type="password" placeholder="인증키 재입력" />
      <button class="btn btn-primary" data-action="unlockManage">확인</button>
    </div>
  `;
}

function renderManageContent() {
  return `
    <div class="notice">삭제는 실제 데이터 삭제가 아니라 비활성화 처리입니다. 백업 ZIP은 매장, 번호표, 호출기록을 포함합니다.</div>

    <div class="manage-row mt-3">
      <input id="addStoreName" class="input" placeholder="새 매장명" />
      <button class="btn btn-primary" data-action="addStore">매장 생성</button>
    </div>

    <div class="manage-row">
      <input id="manageSearch" class="input" value="${escapeHtml(appState.manageSearch)}" placeholder="매장 검색" />
      <button class="btn btn-ghost" data-action="searchManage">검색</button>
    </div>

    <div class="store-list mt-2">
      ${appState.adminStores.length ? appState.adminStores.map((store) => renderManageStoreRow(store)).join("") : `<div class="queue-empty">매장이 없습니다</div>`}
    </div>

    <div class="grid-two mt-3">
      <button class="btn btn-primary" data-action="downloadBackup">ZIP 백업 다운로드</button>
      <div>
        <input id="restoreFile" class="file-input" type="file" accept=".zip,application/zip" />
        <button class="btn btn-red mt-1" data-action="restoreBackup">ZIP 복원</button>
      </div>
    </div>
  `;
}

function renderManageStoreRow(store) {
  return `
    <div class="manage-store">
      <div>
        <div class="store-name">${escapeHtml(store.name)}</div>
        <div class="store-status">${store.is_active ? "사용중" : "비활성"}</div>
      </div>
      <div class="manage-actions">
        <button class="btn btn-ghost btn-small" data-action="editStore" data-store-id="${store.id}">수정</button>
        ${store.is_active
          ? `<button class="btn btn-danger btn-small" data-action="deleteStore" data-store-id="${store.id}">삭제</button>`
          : `<button class="btn btn-primary btn-small" data-action="restoreStore" data-store-id="${store.id}">복구</button>`}
      </div>
    </div>
  `;
}

function renderDisplay() {
  const selected = appState.displayStore;
  if (!selected) {
    $app.innerHTML = `
      ${headerHtml("호출 화면", "매장을 선택하면 호출 팝업과 음성이 표시됩니다.", `<button class="btn btn-ghost btn-small" data-link="/">처음으로</button>`)}
      <section class="layout-card">
        <div class="search-row">
          <input id="displaySearch" class="input" value="${escapeHtml(appState.customerSearch)}" placeholder="매장명 검색" />
          <button class="btn btn-primary" data-action="displaySearch">검색</button>
        </div>
        <div class="store-list">${renderStoreList(appState.customerStores, "selectDisplayStore")}</div>
      </section>
    `;
    return;
  }

  $app.innerHTML = `
    ${headerHtml(
      selected.name,
      "관리자가 호출하면 이 화면 중앙에 크게 표시됩니다.",
      `<button class="btn btn-primary btn-small" data-action="enableVoice">${appState.voiceEnabled ? "음성 켜짐" : "음성 시작"}</button><button class="btn btn-ghost btn-small" data-action="changeDisplayStore">매장 변경</button>`
    )}
    ${appState.voiceEnabled ? "" : `<div class="notice mt-1">브라우저 정책 때문에 호출 음성은 이 화면에서 '음성 시작'을 한 번 눌러야 안정적으로 나옵니다.</div>`}
    <section class="grid-two mt-2">
      ${SERVICE_ORDER.map((serviceType) => renderDisplayServiceCard(serviceType)).join("")}
    </section>
  `;
}

function renderDisplayServiceCard(serviceType) {
  const meta = serviceMeta(serviceType);
  const serviceState = appState.displayState?.services?.[serviceType];
  const theme = meta.theme;
  const current = serviceState?.current_number;
  const waitingCount = serviceState?.waiting_count ?? 0;
  return `
    <article class="service-card ${theme}">
      <div class="service-head">
        <div class="service-title-wrap">
          <div class="service-icon ${theme}">${theme === "red" ? "▣" : "♡"}</div>
          <div>
            <div class="service-small ${theme}">${theme === "red" ? "Premium Manager" : "Galaxy AI Consultant"}</div>
            <div class="service-title">${escapeHtml(meta.customer_label)} 코너</div>
          </div>
        </div>
        <div class="now-time">${nowLabel()}</div>
      </div>
      <div class="current-box">
        ${current ? `<div class="current-number ${theme}">${current}</div>` : `<div class="current-number wait">대기중</div>`}
      </div>
      <div class="queue-title">대기 목록</div>
      <div class="state-pill"><span>대기 인원</span><strong>${waitingCount}명</strong></div>
    </article>
  `;
}

async function loadCustomerStores() {
  const payload = await apiFetch(`/api/stores?search=${encodeURIComponent(appState.customerSearch)}`);
  appState.customerStores = payload.stores;
}

async function loadAdminStores() {
  const payload = await apiFetch(`/api/admin/stores?search=${encodeURIComponent(appState.adminSearch || appState.manageSearch)}`, { admin: true });
  appState.adminStores = payload.stores;
}

async function loadAdminState() {
  if (!appState.selectedAdminStore) return;
  const payload = await apiFetch(`/api/state/${appState.selectedAdminStore.id}`);
  appState.adminState = payload.state;
  appState.selectedAdminStore = payload.state.store;
  renderAdmin();
}

async function loadDisplayState(initial = false) {
  if (!appState.displayStore) return;
  const payload = await apiFetch(`/api/state/${appState.displayStore.id}`);
  appState.displayState = payload.state;
  appState.displayStore = payload.state.store;
  if (initial) {
    appState.lastCallId = payload.state.last_call_id || 0;
  }
  renderDisplay();
}

async function pollDisplayCalls() {
  if (!appState.displayStore) return;
  const payload = await apiFetch(`/api/calls?store_id=${appState.displayStore.id}&after_id=${appState.lastCallId}`);
  if (payload.calls.length) {
    for (const call of payload.calls) {
      appState.lastCallId = Math.max(appState.lastCallId, call.id);
      if (call.ticket_number) {
        showCallPopup(call, appState.voiceEnabled);
      }
    }
  }
  await loadDisplayState(false);
}

async function adminLogin(key) {
  await apiFetch("/api/admin/login", { method: "POST", json: { key } });
  appState.adminKey = key;
  localStorage.setItem(ADMIN_KEY_STORAGE, key);
  await loadAdminStores();
  renderAdmin();
}

async function issueTicket(serviceType) {
  if (!appState.selectedCustomerStore) return;
  const payload = await apiFetch("/api/tickets", {
    method: "POST",
    json: {
      store_id: appState.selectedCustomerStore.id,
      service_type: serviceType,
    },
  });
  appState.ticket = payload.ticket;

  // TODO: 나중에 기존 번호표 출력 앱과 하이브리드/WebView로 묶을 때 이 위치에 연결한다.
  // 예시: window.ReactNativeWebView?.postMessage(JSON.stringify({ type: "PRINT_TICKET", ticket: payload.ticket }));
  // 예시: Android.printTicket(JSON.stringify(payload.ticket));

  renderCustomer();
}

async function callService(serviceType, callType, ticketNumber = null) {
  if (!appState.selectedAdminStore) return;
  const payload = await apiFetch("/api/admin/call", {
    method: "POST",
    admin: true,
    json: {
      store_id: appState.selectedAdminStore.id,
      service_type: serviceType,
      call_type: callType,
      ticket_number: ticketNumber,
    },
  });
  appState.adminState = payload.state;
  showCallPopup(payload.call, true);
  renderAdmin();
}

async function resetSelectedService(serviceType) {
  if (!appState.selectedAdminStore) return;
  const meta = serviceMeta(serviceType);
  const ok = window.confirm(`${meta.admin_label} 번호표를 0으로 초기화할까요?\n다음 번호표는 1번부터 다시 발급됩니다.`);
  if (!ok) return;
  const payload = await apiFetch("/api/admin/reset", {
    method: "POST",
    admin: true,
    json: {
      store_id: appState.selectedAdminStore.id,
      service_type: serviceType,
    },
  });
  appState.adminState = payload.state;
  appState.selectedAdminStore = payload.state.store;
  renderAdmin();
}

async function addStore() {
  const input = document.getElementById("addStoreName");
  const name = input?.value.trim();
  if (!name) {
    alert("매장명을 입력하세요");
    return;
  }
  await apiFetch("/api/admin/stores", { method: "POST", admin: true, json: { name } });
  appState.manageSearch = "";
  appState.adminSearch = "";
  await loadAdminStores();
  renderAdmin();
}

async function editStore(storeId) {
  const store = appState.adminStores.find((item) => item.id === storeId);
  if (!store) return;
  const name = window.prompt("수정할 매장명을 입력하세요", store.name);
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    alert("매장명을 입력하세요");
    return;
  }
  const payload = await apiFetch(`/api/admin/stores/${storeId}`, { method: "PUT", admin: true, json: { name: trimmed } });
  if (appState.selectedAdminStore?.id === storeId) {
    appState.selectedAdminStore = payload.store;
  }
  if (appState.selectedCustomerStore?.id === storeId) {
    appState.selectedCustomerStore = payload.store;
  }
  await loadAdminStores();
  renderAdmin();
}

async function deleteStore(storeId) {
  const store = appState.adminStores.find((item) => item.id === storeId);
  if (!store) return;
  const ok = window.confirm(`${store.name} 매장을 삭제 처리할까요?\n기존 기록은 보존되고 고객 화면에서만 숨겨집니다.`);
  if (!ok) return;
  await apiFetch(`/api/admin/stores/${storeId}`, { method: "DELETE", admin: true });
  if (appState.selectedAdminStore?.id === storeId) {
    appState.selectedAdminStore = null;
    appState.adminState = null;
  }
  await loadAdminStores();
  renderAdmin();
}

async function restoreStore(storeId) {
  await apiFetch(`/api/admin/stores/${storeId}`, { method: "PUT", admin: true, json: { is_active: true } });
  await loadAdminStores();
  renderAdmin();
}

async function downloadBackup() {
  const response = await fetch("/api/admin/backup", { headers: { "X-Admin-Key": appState.adminKey } });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || "백업 다운로드 실패");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `codenote_staff_call_backup_${Date.now()}.zip`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function restoreBackup() {
  const input = document.getElementById("restoreFile");
  const file = input?.files?.[0];
  if (!file) {
    alert("복원할 ZIP 파일을 선택하세요");
    return;
  }
  const ok = window.confirm("현재 서버 데이터가 백업 파일 내용으로 교체됩니다. 복원할까요?");
  if (!ok) return;

  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch("/api/admin/restore", {
    method: "POST",
    headers: { "X-Admin-Key": appState.adminKey },
    body: formData,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || "복원 실패");
  }
  alert("복원이 완료되었습니다");
  appState.selectedAdminStore = null;
  appState.adminState = null;
  appState.manageSearch = "";
  appState.adminSearch = "";
  await loadAdminStores();
  renderAdmin();
}

function speak(text) {
  if (!text || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "ko-KR";
  utterance.rate = 0.92;
  utterance.pitch = 1;
  utterance.volume = 1;
  window.speechSynthesis.speak(utterance);
}

function showCallPopup(call, shouldSpeak) {
  if (!call || !call.ticket_number) return;
  const meta = serviceMeta(call.service_type);
  $callCard.className = `call-card ${meta.theme}`;
  $overlayService.textContent = meta.customer_label;
  $overlayNumber.textContent = call.ticket_number;
  $overlayMessage.textContent = `${meta.voice_label} 창구로 와주세요`;
  $overlay.classList.remove("hidden");

  if (shouldSpeak) speak(call.speech_text);

  if (appState.callTimer) window.clearTimeout(appState.callTimer);
  appState.callTimer = window.setTimeout(() => {
    $overlay.classList.add("hidden");
  }, 8000);
}

async function initCustomer() {
  clearPolling();
  await loadCustomerStores();
  renderCustomer();
}

async function initAdmin() {
  clearPolling();
  if (appState.adminKey) {
    try {
      await apiFetch("/api/admin/login", { method: "POST", json: { key: appState.adminKey } });
      await loadAdminStores();
    } catch (_error) {
      appState.adminKey = "";
      localStorage.removeItem(ADMIN_KEY_STORAGE);
    }
  }
  renderAdmin();
}

async function initDisplay() {
  clearPolling();
  const params = new URLSearchParams(window.location.search);
  const storeId = Number(params.get("store_id"));
  await loadCustomerStores();
  if (storeId) {
    appState.displayStore = appState.customerStores.find((store) => store.id === storeId) || { id: storeId, name: "호출 화면", is_active: true };
    await loadDisplayState(true);
    setPolling(pollDisplayCalls, 1000);
  } else {
    renderDisplay();
  }
}

async function safeRun(task) {
  try {
    await task();
  } catch (error) {
    alert(error.message || "오류가 발생했습니다");
  }
}

$app.addEventListener("click", (event) => {
  const target = event.target.closest("[data-link], [data-action]");
  if (!target) return;

  const link = target.dataset.link;
  if (link) {
    go(link);
    return;
  }

  const action = target.dataset.action;
  const storeId = Number(target.dataset.storeId);
  const serviceType = target.dataset.service;
  const callType = target.dataset.callType;

  if (action === "customerSearch") {
    appState.customerSearch = document.getElementById("customerSearch")?.value.trim() || "";
    safeRun(async () => { await loadCustomerStores(); renderCustomer(); });
  }

  if (action === "selectCustomerStore") {
    appState.selectedCustomerStore = appState.customerStores.find((store) => store.id === storeId) || null;
    appState.ticket = null;
    renderCustomer();
  }

  if (action === "changeCustomerStore") {
    appState.selectedCustomerStore = null;
    appState.ticket = null;
    renderCustomer();
  }

  if (action === "issueTicket") {
    safeRun(() => issueTicket(serviceType));
  }

  if (action === "issueAgain") {
    safeRun(() => issueTicket(appState.ticket.service_type));
  }

  if (action === "clearTicket") {
    appState.ticket = null;
    renderCustomer();
  }

  if (action === "adminLogin") {
    const key = document.getElementById("adminKey")?.value.trim() || "";
    safeRun(() => adminLogin(key));
  }

  if (action === "logoutAdmin") {
    appState.adminKey = "";
    appState.selectedAdminStore = null;
    appState.adminState = null;
    localStorage.removeItem(ADMIN_KEY_STORAGE);
    renderAdmin();
  }

  if (action === "adminStoreSearch") {
    appState.adminSearch = document.getElementById("adminStoreSearch")?.value.trim() || "";
    appState.manageSearch = "";
    safeRun(async () => { await loadAdminStores(); renderAdmin(); });
  }

  if (action === "selectAdminStore") {
    appState.selectedAdminStore = appState.adminStores.find((store) => store.id === storeId) || null;
    safeRun(async () => {
      await loadAdminState();
      setPolling(loadAdminState, 1500);
    });
  }

  if (action === "changeAdminStore") {
    clearPolling();
    appState.selectedAdminStore = null;
    appState.adminState = null;
    renderAdmin();
  }

  if (action === "call") {
    safeRun(() => callService(serviceType, callType));
  }

  if (action === "directCall") {
    const value = window.prompt("호출할 번호를 입력하세요");
    if (value === null) return;
    const ticketNumber = Number(value);
    if (!Number.isInteger(ticketNumber) || ticketNumber < 1) {
      alert("1 이상의 숫자를 입력하세요");
      return;
    }
    safeRun(() => callService(serviceType, "direct", ticketNumber));
  }

  if (action === "resetService") {
    safeRun(() => resetSelectedService(serviceType));
  }

  if (action === "openManage") {
    clearPolling();
    appState.modalOpen = true;
    appState.managementUnlocked = false;
    appState.manageSearch = "";
    safeRun(async () => { await loadAdminStores(); renderAdmin(); });
  }

  if (action === "closeManage") {
    appState.modalOpen = false;
    appState.managementUnlocked = false;
    renderAdmin();
    if (appState.selectedAdminStore) {
      setPolling(loadAdminState, 1500);
    }
  }

  if (action === "unlockManage") {
    const key = document.getElementById("manageKey")?.value.trim() || "";
    safeRun(async () => {
      await apiFetch("/api/admin/login", { method: "POST", json: { key } });
      appState.managementUnlocked = true;
      appState.adminKey = key;
      localStorage.setItem(ADMIN_KEY_STORAGE, key);
      await loadAdminStores();
      renderAdmin();
    });
  }

  if (action === "addStore") {
    safeRun(addStore);
  }

  if (action === "searchManage") {
    appState.manageSearch = document.getElementById("manageSearch")?.value.trim() || "";
    appState.adminSearch = "";
    safeRun(async () => { await loadAdminStores(); renderAdmin(); });
  }

  if (action === "editStore") {
    safeRun(() => editStore(storeId));
  }

  if (action === "deleteStore") {
    safeRun(() => deleteStore(storeId));
  }

  if (action === "restoreStore") {
    safeRun(() => restoreStore(storeId));
  }

  if (action === "downloadBackup") {
    safeRun(downloadBackup);
  }

  if (action === "restoreBackup") {
    safeRun(restoreBackup);
  }

  if (action === "displaySearch") {
    appState.customerSearch = document.getElementById("displaySearch")?.value.trim() || "";
    safeRun(async () => { await loadCustomerStores(); renderDisplay(); });
  }

  if (action === "selectDisplayStore") {
    appState.displayStore = appState.customerStores.find((store) => store.id === storeId) || null;
    safeRun(async () => {
      await loadDisplayState(true);
      setPolling(pollDisplayCalls, 1000);
    });
  }

  if (action === "changeDisplayStore") {
    clearPolling();
    appState.displayStore = null;
    appState.displayState = null;
    appState.lastCallId = 0;
    renderDisplay();
  }

  if (action === "enableVoice") {
    appState.voiceEnabled = true;
    localStorage.setItem(VOICE_STORAGE, "1");
    speak("호출 음성이 켜졌습니다.");
    renderDisplay();
  }
});

$closeOverlay.addEventListener("click", () => {
  $overlay.classList.add("hidden");
});

window.addEventListener("beforeunload", clearPolling);

(async function bootstrap() {
  try {
    if (appState.route === "/customer") {
      await initCustomer();
    } else if (appState.route === "/admin") {
      await initAdmin();
    } else if (appState.route === "/display") {
      await initDisplay();
    } else {
      clearPolling();
      renderHome();
    }
  } catch (error) {
    $app.innerHTML = `
      ${headerHtml("오류", "화면을 불러오지 못했습니다.", `<button class="btn btn-ghost btn-small" data-link="/">처음으로</button>`)}
      <section class="layout-card"><div class="error">${escapeHtml(error.message || error)}</div></section>
    `;
  }
})();
