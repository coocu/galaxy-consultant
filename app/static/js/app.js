const SERVICE_ORDER = ["simple_service", "purchase_consult"];
const INTEGRATED_SERVICE = "integrated";
const SERVICE_META = window.SERVICE_META || {};
const VOICE_STORAGE = "codenote_staff_call_voice_enabled";
const POPUP_VISIBLE_MS = 5000;
const CALL_NUMBER_VISIBLE_MS = POPUP_VISIBLE_MS + 5000;

const appState = {
  route: window.location.pathname,
  customerStores: [],
  adminStores: [],
  customerSearch: "",
  adminSearch: "",
  manageSearch: "",
  selectedCustomerStore: null,
  selectedCustomerService: null,
  selectedAdminStore: null,
  selectedAdminService: null,
  selectedDisplayStore: null,
  selectedDisplayService: null,
  customerState: null,
  adminState: null,
  displayState: null,
  adminAuthenticated: false,
  modalOpen: false,
  viewerSettingsOpen: null,
  adminSettingsOpen: false,
  managementUnlocked: false,
  voiceEnabled: localStorage.getItem(VOICE_STORAGE) !== "0",
  pushSupported: false,
  pushSubscribed: false,
  pushBusy: false,
  pushMessage: "",
  adminTicketSnapshot: {},
  adminTicketSnapshotReady: false,
  adminForegroundNotice: "",
  adminForegroundNoticeTimer: null,
  adminAudioContext: null,
  lastCustomerCallId: 0,
  lastDisplayCallId: 0,
  pollingTimer: null,
  callTimer: null,
  visibleCustomerCalls: {},
  visibleDisplayCalls: {},
  visibleClearTimers: {},
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

function validServiceType(serviceType) {
  return SERVICE_ORDER.includes(serviceType) ? serviceType : null;
}

function validCustomerViewType(serviceType) {
  if (serviceType === INTEGRATED_SERVICE) return INTEGRATED_SERVICE;
  return validServiceType(serviceType);
}

function isIntegratedView(serviceType) {
  return serviceType === INTEGRATED_SERVICE;
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
  const fetchOptions = {
    ...options,
    headers,
    credentials: "same-origin",
  };
  delete fetchOptions.json;
  delete fetchOptions.admin;

  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    fetchOptions.body = JSON.stringify(options.json);
  }

  const response = await fetch(path, fetchOptions);
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

function headerHtml(title, subtitle = "", actions = "", extraClass = "") {
  const className = extraClass ? `topbar ${extraClass}` : "topbar";
  return `
    <header class="${className}">
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
        <p class="sub-title">고객 호출 화면과 관리자 호출을 매장별로 분리합니다.</p>
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
      ${store.code ? `<span class="store-code">점코드 ${escapeHtml(store.code)}</span>` : ""}
      <span class="store-status">${store.is_active ? "사용 가능" : "비활성"}</span>
    </button>
  `).join("");
}

function renderServiceChoice(mode, selectedStore) {
  const title = mode === "admin" ? "관리할 업무를 선택하세요." : "업무를 선택하세요.";
  const action = mode === "admin" ? "selectAdminService" : mode === "display" ? "selectDisplayService" : "selectCustomerService";
  const serviceButtons = SERVICE_ORDER.map((serviceType) => {
    const meta = serviceMeta(serviceType);
    const label = mode === "admin" ? meta.admin_label : meta.customer_label;
    const buttonClass = meta.theme === "red" ? "btn-red" : "btn-primary";
    return `<button class="btn ${buttonClass}" data-action="${action}" data-service="${serviceType}">${escapeHtml(label)}</button>`;
  });

  serviceButtons.push(`<button class="btn btn-green" data-action="${action}" data-service="${INTEGRATED_SERVICE}">통합보기</button>`);

  return `
    <section class="layout-card">
      <h2 class="section-title">${escapeHtml(selectedStore.name)}</h2>
      <p class="sub-title">${title}</p>
      <div class="service-select">
        ${serviceButtons.join("")}
      </div>
    </section>
  `;
}

function renderViewerGear(scope) {
  return `<button class="viewer-gear" type="button" data-action="openViewerSettings" data-scope="${scope}" aria-label="화면 설정">⚙</button>`;
}

function renderViewerSettingsModal(scope) {
  if (appState.viewerSettingsOpen !== scope) return "";
  const isDisplay = scope === "display";
  const changeServiceAction = isDisplay ? "changeDisplayService" : "changeCustomerService";
  const changeStoreAction = isDisplay ? "changeDisplayStore" : "changeCustomerStore";
  return `
    <div class="modal-backdrop" data-action="closeViewerSettings">
      <section class="modal viewer-settings-modal" role="dialog" aria-modal="true" aria-label="화면 설정" data-action="stopModalClose">
        <div class="modal-head">
          <div class="modal-title">화면 설정</div>
          <button class="close-btn" type="button" data-action="closeViewerSettings">×</button>
        </div>
        <div class="viewer-settings-actions">
          <button class="btn btn-primary" type="button" data-action="enableVoice">${appState.voiceEnabled ? "음성 켜짐" : "음성 시작"}</button>
          <button class="btn btn-ghost" type="button" data-action="${changeServiceAction}">업무 변경</button>
          <button class="btn btn-ghost" type="button" data-action="${changeStoreAction}">매장 변경</button>
        </div>
      </section>
    </div>
  `;
}

function renderAdminGear() {
  return `<button class="viewer-gear admin-gear" type="button" data-action="openAdminSettings" aria-label="관리자 설정">⚙</button>`;
}

function renderAdminSettingsModal() {
  if (!appState.adminSettingsOpen) return "";
  const selectedStore = appState.selectedAdminStore;
  const selectedService = appState.selectedAdminService;
  const customerUrl = selectedStore
    ? `/customer?store_id=${selectedStore.id}${selectedService ? `&service_type=${selectedService}` : ""}`
    : "/customer";
  const pushButton = selectedStore ? renderPushControl(false) : "";

  return `
    <div class="modal-backdrop" data-action="closeAdminSettings">
      <section class="modal viewer-settings-modal admin-settings-modal" role="dialog" aria-modal="true" aria-label="관리자 설정" data-action="stopModalClose">
        <div class="modal-head">
          <div class="modal-title">관리자 설정</div>
          <button class="close-btn" type="button" data-action="closeAdminSettings">×</button>
        </div>
        <div class="viewer-settings-actions">
          ${pushButton}
          ${selectedStore ? `<a class="btn btn-ghost" href="${customerUrl}" target="_blank" rel="noopener">고객화면</a>` : ""}
          ${selectedService ? `<button class="btn btn-ghost" type="button" data-action="changeAdminService">업무 변경</button>` : ""}
          ${selectedStore ? `<button class="btn btn-ghost" type="button" data-action="changeAdminStore">매장 변경</button>` : ""}
          <button class="btn btn-ghost" type="button" data-action="openManage">매장관리</button>
          <button class="btn btn-danger" type="button" data-action="logoutAdmin">로그아웃</button>
          <button class="btn btn-ghost" type="button" data-link="/">처음으로</button>
        </div>
      </section>
    </div>
  `;
}

function renderViewerCallBody(scope, selectedService) {
  if (isIntegratedView(selectedService)) {
    return `
      <section class="integrated-service-wrap mt-2">
        ${SERVICE_ORDER.map((serviceType) => renderCustomerDisplayServiceCard(serviceType, scope)).join("")}
      </section>
    `;
  }
  return `
    <section class="single-service-wrap mt-2">
      ${renderCustomerDisplayServiceCard(selectedService, scope)}
    </section>
  `;
}

function selectedServiceMatchesCall(selectedService, callServiceType) {
  if (isIntegratedView(selectedService)) return SERVICE_ORDER.includes(callServiceType);
  return selectedService === callServiceType;
}

function renderCustomer() {
  const selected = appState.selectedCustomerStore;
  const actions = `<button class="btn btn-ghost btn-small" data-link="/">처음으로</button>`;

  if (!selected) {
    $app.innerHTML = `
      ${headerHtml("고객", "매장을 검색하고 선택하세요.", actions)}
      <section class="layout-card">
        <div class="search-row">
          <input id="customerSearch" class="input" value="${escapeHtml(appState.customerSearch)}" placeholder="매장명 또는 점코드 검색" />
          <button class="btn btn-primary" data-action="customerSearch">검색</button>
        </div>
        <div class="store-list">${renderStoreList(appState.customerStores, "selectCustomerStore")}</div>
      </section>
    `;
    return;
  }

  if (!appState.selectedCustomerService) {
    $app.innerHTML = `
      ${headerHtml(
        selected.name,
        "간단서비스와 구매문의를 따로 선택합니다.",
        `<button class="btn btn-ghost btn-small" data-action="changeCustomerStore">매장 변경</button><button class="btn btn-ghost btn-small" data-link="/">처음으로</button>`
      )}
      ${renderServiceChoice("customer", selected)}
    `;
    return;
  }

  const selectedService = appState.selectedCustomerService;
  const title = isIntegratedView(selectedService)
    ? `${selected.name} · 통합보기`
    : `${selected.name} · ${serviceMeta(selectedService).customer_label}`;
  const subtitle = isIntegratedView(selectedService)
    ? "간단서비스와 구매문의를 함께 표시합니다."
    : "관리자가 호출하면 이 화면 중앙에 크게 표시됩니다.";

  $app.innerHTML = `
    ${headerHtml(
      title,
      subtitle,
      renderViewerGear("customer"),
      "viewer-topbar"
    )}
    ${appState.voiceEnabled ? "" : `<div class="notice mt-1">호출 음성은 오른쪽 상단 톱니바퀴에서 '음성 시작'을 한 번 눌러야 안정적으로 나옵니다.</div>`}
    ${renderViewerCallBody("customer", selectedService)}
    ${renderViewerSettingsModal("customer")}
  `;
}

function renderCustomerDisplayServiceCard(serviceType, scope = "customer") {
  const meta = serviceMeta(serviceType);
  const stateSource = scope === "display" ? appState.displayState : appState.customerState;
  const serviceState = stateSource?.services?.[serviceType];
  const theme = meta.theme;
  const visibleNumber = getVisibleCallNumber(scope, serviceType);
  const waitingCount = serviceState?.waiting_count ?? 0;
  const queue = serviceState?.waiting_tickets || [];
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
        ${visibleNumber ? `<div class="current-number ${theme}">${visibleNumber}</div>` : `<div class="current-number wait">대기중</div>`}
      </div>
      <div class="state-mini">
        <div class="state-pill"><span>대기 인원</span><strong>${waitingCount}명</strong></div>
        <div class="state-pill"><span>호출 상태</span><strong>${visibleNumber ? `${visibleNumber}번` : "대기중"}</strong></div>
      </div>
      <div class="queue-title">대기 목록</div>
      ${queue.length ? `<div class="queue-list">${queue.map((ticket) => `<span class="queue-chip">${ticket.ticket_number}</span>`).join("")}</div>` : `<div class="queue-empty">대기 중인 고객이 없습니다</div>`}
    </article>
  `;
}

function renderAdmin() {
  if (!appState.adminAuthenticated) {
    clearPolling();
    $app.innerHTML = `
      ${headerHtml("관리자", "인증키를 입력하세요.", `<button class="btn btn-ghost btn-small" data-link="/">처음으로</button>`)}
      <section class="layout-card">
        <div class="search-row">
          <input id="adminKey" class="input" type="password" placeholder="인증키" autocomplete="current-password" />
          <button class="btn btn-primary" data-action="adminLogin">확인</button>
        </div>
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
        renderAdminGear(),
        "admin-topbar"
      )}
      <section class="layout-card">
        <div class="search-row">
          <input id="adminStoreSearch" class="input" value="${escapeHtml(appState.adminSearch)}" placeholder="매장명 또는 점코드 검색" />
          <button class="btn btn-primary" data-action="adminStoreSearch">검색</button>
        </div>
        <div class="store-list">${renderStoreList(appState.adminStores.filter((store) => store.is_active), "selectAdminStore")}</div>
      </section>
      ${renderAdminSettingsModal()}
      ${renderManageModal()}
    `;
    return;
  }

  if (!appState.selectedAdminService) {
    $app.innerHTML = `
      ${headerHtml(
        appState.selectedAdminStore.name,
        "갤럭시 컨설턴트와 구매상담을 따로 선택합니다.",
        renderAdminGear(),
        "admin-topbar"
      )}
      ${renderPushNotice()}
      ${renderServiceChoice("admin", appState.selectedAdminStore)}
      ${renderAdminSettingsModal()}
      ${renderManageModal()}
    `;
    return;
  }

  const isIntegratedAdmin = isIntegratedView(appState.selectedAdminService);
  const selectedAdminTitle = isIntegratedAdmin
    ? `${appState.selectedAdminStore.name} · 통합보기`
    : `${appState.selectedAdminStore.name} · ${serviceMeta(appState.selectedAdminService).admin_label}`;
  const selectedAdminSubtitle = isIntegratedAdmin
    ? "방문해 주셔서 감사합니다."
    : "방문해 주셔서 감사합니다.";
  const customerUrl = `/customer?store_id=${appState.selectedAdminStore.id}&service_type=${appState.selectedAdminService}`;
  $app.innerHTML = `
    ${headerHtml(
      selectedAdminTitle,
      selectedAdminSubtitle,
      renderAdminGear(),
      "admin-topbar"
    )}
    ${renderPushNotice()}
    ${renderAdminCallBody(appState.selectedAdminService)}
    ${renderAdminSettingsModal()}
    ${renderManageModal()}
  `;
}

function renderAdminCallBody(selectedService) {
  if (isIntegratedView(selectedService)) {
    return `
      <section class="integrated-service-wrap">
        ${SERVICE_ORDER.map((serviceType) => renderAdminServiceCard(serviceType)).join("")}
      </section>
    `;
  }
  return `
    <section class="single-service-wrap">
      ${renderAdminServiceCard(selectedService)}
    </section>
  `;
}

function renderAdminServiceCard(serviceType) {
  const meta = serviceMeta(serviceType);
  const serviceState = appState.adminState?.services?.[serviceType];
  const theme = meta.theme;
  const current = serviceState?.current_number;
  const waitingCount = serviceState?.waiting_count ?? 0;
  const nextNumber = serviceState?.next_number ?? 1;
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
    </article>
  `;
}

function isPushSupported() {
  appState.pushSupported = Boolean(
    "serviceWorker" in navigator
    && "PushManager" in window
    && "Notification" in window
  );
  return appState.pushSupported;
}

function renderPushControl(compact = true) {
  if (!appState.selectedAdminStore) return "";
  const sizeClass = compact ? " btn-small" : "";
  if (!isPushSupported()) {
    return `<button class="btn btn-ghost${sizeClass}" type="button" disabled>알림 미지원</button>`;
  }
  const label = appState.pushSubscribed ? "발급 알림 켜짐" : "발급 알림 켜기";
  const action = appState.pushSubscribed ? "disablePush" : "enablePush";
  return `<button class="btn btn-primary${sizeClass}" type="button" data-action="${action}" ${appState.pushBusy ? "disabled" : ""}>${label}</button>`;
}

function renderPushNotice() {
  if (!appState.selectedAdminStore) return "";
  const messages = [appState.pushMessage, appState.adminForegroundNotice].filter(Boolean);
  if (!messages.length) return "";
  return `<div class="notice mt-1">${messages.map((message) => escapeHtml(message)).join("<br>")}</div>`;
}

function resetAdminTicketSnapshot() {
  appState.adminTicketSnapshot = {};
  appState.adminTicketSnapshotReady = false;
  appState.adminForegroundNotice = "";
  if (appState.adminForegroundNoticeTimer) {
    window.clearTimeout(appState.adminForegroundNoticeTimer);
    appState.adminForegroundNoticeTimer = null;
  }
}

function maxWaitingTicketId(state, serviceType) {
  const tickets = state?.services?.[serviceType]?.waiting_tickets || [];
  return tickets.reduce((maxId, ticket) => Math.max(maxId, Number(ticket.id || 0)), 0);
}

function primeAdminAudio() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return null;
  if (!appState.adminAudioContext) {
    appState.adminAudioContext = new AudioContextClass();
  }
  if (appState.adminAudioContext.state === "suspended") {
    appState.adminAudioContext.resume().catch(() => null);
  }
  return appState.adminAudioContext;
}

function playAdminTicketSound() {
  try {
    const context = primeAdminAudio();
    if (!context) {
      speak("새 번호표가 발급되었습니다.");
      return;
    }

    const start = context.currentTime;
    [0, 0.18].forEach((offset, index) => {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(index === 0 ? 880 : 1046, start + offset);
      gain.gain.setValueAtTime(0.0001, start + offset);
      gain.gain.exponentialRampToValueAtTime(0.2, start + offset + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.15);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start(start + offset);
      oscillator.stop(start + offset + 0.16);
    });
  } catch (_error) {
    speak("새 번호표가 발급되었습니다.");
  }
}

function showAdminBrowserNotification(ticket, message) {
  if (!document.hidden || !("Notification" in window) || Notification.permission !== "granted") return;
  const options = {
    body: message,
    tag: `foreground-ticket-${ticket.store_id}-${ticket.service_type}`,
    renotify: true,
    silent: false,
    vibrate: [160, 80, 160],
    data: {
      url: `/admin?store_id=${ticket.store_id}&service_type=${ticket.service_type}`,
      store_id: ticket.store_id,
      service_type: ticket.service_type,
      ticket_number: ticket.ticket_number,
    },
  };

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.getRegistration("/")
      .then((registration) => registration?.showNotification("새 대기번호 발급", options))
      .catch(() => null);
    return;
  }

  try {
    new Notification("새 대기번호 발급", options);
  } catch (_error) {

  }
}

function handleAdminTicketAlerts(nextState, { initial = false } = {}) {
  const nextSnapshot = {};
  const newTickets = [];

  for (const serviceType of SERVICE_ORDER) {
    const tickets = nextState?.services?.[serviceType]?.waiting_tickets || [];
    const previousMaxId = Number(appState.adminTicketSnapshot[serviceType] || 0);
    for (const ticket of tickets) {
      const ticketId = Number(ticket.id || 0);
      if (appState.adminTicketSnapshotReady && !initial && ticketId > previousMaxId) {
        newTickets.push(ticket);
      }
    }
    nextSnapshot[serviceType] = Math.max(previousMaxId, maxWaitingTicketId(nextState, serviceType));
  }

  appState.adminTicketSnapshot = nextSnapshot;
  appState.adminTicketSnapshotReady = true;

  if (!newTickets.length) return;

  const newestTicket = newTickets[newTickets.length - 1];
  const meta = serviceMeta(newestTicket.service_type);
  const storeName = appState.selectedAdminStore?.name || nextState?.store?.name || "선택 매장";
  const message = `${storeName} · ${meta.customer_label} ${newestTicket.ticket_number}번 번호표가 발급되었습니다.`;
  appState.adminForegroundNotice = message;
  playAdminTicketSound();
  showAdminBrowserNotification(newestTicket, message);

  if (appState.adminForegroundNoticeTimer) {
    window.clearTimeout(appState.adminForegroundNoticeTimer);
  }
  appState.adminForegroundNoticeTimer = window.setTimeout(() => {
    appState.adminForegroundNotice = "";
    appState.adminForegroundNoticeTimer = null;
    if (appState.route === "/admin" && appState.selectedAdminStore) {
      renderAdmin();
    }
  }, 5000);
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replaceAll("-", "+").replaceAll("_", "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

async function getPushRegistration({ create = false } = {}) {
  if (!isPushSupported()) return null;
  let registration = await navigator.serviceWorker.getRegistration("/");
  if (!registration && create) {
    registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  }
  return registration;
}

async function getCurrentPushSubscription() {
  const registration = await getPushRegistration({ create: false });
  if (!registration) return null;
  return registration.pushManager.getSubscription();
}

async function refreshPushStatus() {
  appState.pushMessage = "";
  if (!isPushSupported() || Notification.permission !== "granted") {
    appState.pushSubscribed = false;
    return;
  }
  const subscription = await getCurrentPushSubscription();
  appState.pushSubscribed = Boolean(subscription);
}

async function savePushSubscriptionForSelectedStore(subscription) {
  if (!appState.selectedAdminStore || !subscription) return;
  await apiFetch("/api/admin/push/subscribe", {
    method: "POST",
    admin: true,
    json: {
      store_id: appState.selectedAdminStore.id,
      subscription: subscription.toJSON(),
    },
  });
}

async function syncExistingPushToSelectedStore() {
  if (!appState.selectedAdminStore || !isPushSupported() || Notification.permission !== "granted") {
    appState.pushSubscribed = false;
    return;
  }
  const subscription = await getCurrentPushSubscription();
  if (!subscription) {
    appState.pushSubscribed = false;
    return;
  }
  await savePushSubscriptionForSelectedStore(subscription);
  appState.pushSubscribed = true;
  appState.pushMessage = `${appState.selectedAdminStore.name} 번호표 발급 알림이 연결되었습니다.`;
}

async function enablePushNotifications() {
  if (!appState.selectedAdminStore) {
    throw new Error("먼저 매장을 선택하세요");
  }
  if (!isPushSupported()) {
    throw new Error("이 브라우저는 백그라운드 푸시 알림을 지원하지 않습니다");
  }

  appState.pushBusy = true;
  renderAdmin();
  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      appState.pushSubscribed = false;
      appState.pushMessage = "브라우저 알림 권한이 허용되지 않았습니다.";
      return;
    }

    const config = await apiFetch("/api/push/vapid-public-key", { admin: true });
    if (!config.enabled || !config.publicKey) {
      throw new Error(config.message || "서버 푸시 키 설정이 필요합니다");
    }

    const registration = await getPushRegistration({ create: true });
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(config.publicKey),
      });
    }

    await savePushSubscriptionForSelectedStore(subscription);
    appState.pushSubscribed = true;
    appState.pushMessage = `${appState.selectedAdminStore.name} 번호표 발급 알림이 켜졌습니다.`;
  } finally {
    appState.pushBusy = false;
    renderAdmin();
  }
}

async function disablePushNotifications({ silent = false } = {}) {
  if (!isPushSupported()) return;
  const subscription = await getCurrentPushSubscription();
  if (subscription) {
    await apiFetch("/api/admin/push/unsubscribe", {
      method: "POST",
      admin: true,
      json: { endpoint: subscription.endpoint },
    }).catch(() => null);
    await subscription.unsubscribe().catch(() => false);
  }
  appState.pushSubscribed = false;
  if (!silent) {
    appState.pushMessage = "번호표 발급 알림을 껐습니다.";
    renderAdmin();
  }
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
    <p class="sub-title">매장 관리는 관리자 인증키가 필요합니다.</p>
    <div class="search-row mt-2">
      <input id="manageKey" class="input" type="password" placeholder="인증키 재입력" />
      <button class="btn btn-primary" data-action="unlockManage">확인</button>
    </div>
  `;
}

function renderManageContent() {
  return `
    <div class="notice">삭제하면 매장 카테고리와 해당 매장의 대기번호 데이터가 삭제됩니다. 백업 ZIP은 매장 카테고리만 포함합니다.</div>

    <div class="manage-row mt-3">
      <input id="addStoreName" class="input" placeholder="새 매장명" />
      <input id="addStoreCode" class="input" placeholder="점코드" />
      <button class="btn btn-primary" data-action="addStore">매장 생성</button>
    </div>

    <div class="manage-row">
      <input id="manageSearch" class="input" value="${escapeHtml(appState.manageSearch)}" placeholder="매장명 또는 점코드 검색" />
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
        <div class="store-code">점코드 ${escapeHtml(store.code || "미등록")}</div>
        <div class="store-status">${store.is_active ? "사용중" : "비활성"}</div>
      </div>
      <div class="manage-actions">
        <button class="btn btn-ghost btn-small" data-action="editStore" data-store-id="${store.id}">수정</button>
        <button class="btn btn-danger btn-small" data-action="deleteStore" data-store-id="${store.id}">삭제</button>
      </div>
    </div>
  `;
}

function renderDisplay() {
  const selected = appState.selectedDisplayStore;
  if (!selected) {
    $app.innerHTML = `
      ${headerHtml("고객 호출 화면", "매장을 검색하고 선택하세요.", `<button class="btn btn-ghost btn-small" data-link="/">처음으로</button>`)}
      <section class="layout-card">
        <div class="search-row">
          <input id="displaySearch" class="input" value="${escapeHtml(appState.customerSearch)}" placeholder="매장명 또는 점코드 검색" />
          <button class="btn btn-primary" data-action="displaySearch">검색</button>
        </div>
        <div class="store-list">${renderStoreList(appState.customerStores, "selectDisplayStore")}</div>
      </section>
    `;
    return;
  }

  if (!appState.selectedDisplayService) {
    $app.innerHTML = `
      ${headerHtml(
        selected.name,
        "간단서비스와 구매문의를 따로 선택합니다.",
        `<button class="btn btn-ghost btn-small" data-action="changeDisplayStore">매장 변경</button>`
      )}
      ${renderServiceChoice("display", selected)}
    `;
    return;
  }

  const selectedService = appState.selectedDisplayService;
  const title = isIntegratedView(selectedService)
    ? `${selected.name} · 통합보기`
    : `${selected.name} · ${serviceMeta(selectedService).customer_label}`;
  const subtitle = isIntegratedView(selectedService)
    ? "간단서비스와 구매문의를 함께 표시합니다."
    : "관리자가 호출하면 이 화면 중앙에 크게 표시됩니다.";

  $app.innerHTML = `
    ${headerHtml(
      title,
      subtitle,
      renderViewerGear("display"),
      "viewer-topbar"
    )}
    ${appState.voiceEnabled ? "" : `<div class="notice mt-1">브라우저 정책 때문에 호출 음성은 오른쪽 상단 톱니바퀴에서 '음성 시작'을 한 번 눌러야 안정적으로 나옵니다.</div>`}
    ${renderViewerCallBody("display", selectedService)}
    ${renderViewerSettingsModal("display")}
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

async function loadAdminState(options = {}) {
  if (!appState.selectedAdminStore) return;
  const payload = await apiFetch(`/api/state/${appState.selectedAdminStore.id}`);
  appState.adminState = payload.state;
  appState.selectedAdminStore = payload.state.store;
  handleAdminTicketAlerts(payload.state, { initial: options.initial === true });
  renderAdmin();
}

async function loadCustomerDisplayState(initial = false) {
  if (!appState.selectedCustomerStore) return;
  const payload = await apiFetch(`/api/state/${appState.selectedCustomerStore.id}`);
  appState.customerState = payload.state;
  appState.selectedCustomerStore = payload.state.store;
  if (initial) {
    appState.lastCustomerCallId = payload.state.last_call_id || 0;
  }
  renderCustomer();
}

async function pollCustomerCalls() {
  if (!appState.selectedCustomerStore) return;
  const payload = await apiFetch(`/api/calls?store_id=${appState.selectedCustomerStore.id}&after_id=${appState.lastCustomerCallId}`);
  if (payload.calls.length) {
    for (const call of payload.calls) {
      appState.lastCustomerCallId = Math.max(appState.lastCustomerCallId, call.id);
      if (call.ticket_number && selectedServiceMatchesCall(appState.selectedCustomerService, call.service_type)) {
        registerVisibleCall("customer", call);
        showCallPopup(call, appState.voiceEnabled);
      }
    }
  }
  await loadCustomerDisplayState(false);
}

async function loadDisplayState(initial = false) {
  if (!appState.selectedDisplayStore) return;
  const payload = await apiFetch(`/api/state/${appState.selectedDisplayStore.id}`);
  appState.displayState = payload.state;
  appState.selectedDisplayStore = payload.state.store;
  if (initial) {
    appState.lastDisplayCallId = payload.state.last_call_id || 0;
  }
  renderDisplay();
}

async function pollDisplayCalls() {
  if (!appState.selectedDisplayStore) return;
  const payload = await apiFetch(`/api/calls?store_id=${appState.selectedDisplayStore.id}&after_id=${appState.lastDisplayCallId}`);
  if (payload.calls.length) {
    for (const call of payload.calls) {
      appState.lastDisplayCallId = Math.max(appState.lastDisplayCallId, call.id);
      if (call.ticket_number && selectedServiceMatchesCall(appState.selectedDisplayService, call.service_type)) {
        registerVisibleCall("display", call);
        showCallPopup(call, appState.voiceEnabled);
      }
    }
  }
  await loadDisplayState(false);
}

async function adminLogin(key) {
  await apiFetch("/api/admin/login", { method: "POST", json: { key } });
  appState.adminAuthenticated = true;
  await loadAdminStores();
  await refreshPushStatus();
  renderAdmin();
}

async function issueTicketForApp(serviceType) {
  if (!appState.selectedCustomerStore) return null;
  const payload = await apiFetch("/api/tickets", {
    method: "POST",
    json: {
      store_id: appState.selectedCustomerStore.id,
      service_type: serviceType,
    },
  });

  // 번호표 출력은 웹 화면에서 하지 않는다.
  // 나중에 기존 번호표 출력 앱과 하이브리드/WebView로 묶을 때 이 위치에서 네이티브 출력 기능을 호출한다.
  // 예시: window.ReactNativeWebView?.postMessage(JSON.stringify({ type: "PRINT_TICKET", ticket: payload.ticket }));
  // 예시: Android.printTicket(JSON.stringify(payload.ticket));

  return payload.ticket;
}

window.CodeNoteQueueBridge = {
  issueTicket: issueTicketForApp,
};

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
  const nameInput = document.getElementById("addStoreName");
  const codeInput = document.getElementById("addStoreCode");
  const name = nameInput?.value.trim();
  const code = codeInput?.value.trim();
  if (!name) {
    alert("매장명을 입력하세요");
    return;
  }
  if (!code) {
    alert("점코드를 입력하세요");
    return;
  }
  await apiFetch("/api/admin/stores", { method: "POST", admin: true, json: { name, code } });
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
  const code = window.prompt("수정할 점코드를 입력하세요", store.code || "");
  if (code === null) return;
  const trimmedCode = code.trim();
  if (!trimmedCode) {
    alert("점코드를 입력하세요");
    return;
  }
  const payload = await apiFetch(`/api/admin/stores/${storeId}`, { method: "PUT", admin: true, json: { name: trimmed, code: trimmedCode } });
  if (appState.selectedAdminStore?.id === storeId) {
    appState.selectedAdminStore = payload.store;
  }
  if (appState.selectedCustomerStore?.id === storeId) {
    appState.selectedCustomerStore = payload.store;
  }
  if (appState.selectedDisplayStore?.id === storeId) {
    appState.selectedDisplayStore = payload.store;
  }
  await loadAdminStores();
  renderAdmin();
}

async function deleteStore(storeId) {
  const store = appState.adminStores.find((item) => item.id === storeId);
  if (!store) return;
  const ok = window.confirm(`${store.name} 매장 카테고리를 삭제할까요?\n해당 매장의 번호표/호출/알림 데이터도 같이 삭제됩니다.`);
  if (!ok) return;
  await apiFetch(`/api/admin/stores/${storeId}`, { method: "DELETE", admin: true });
  if (appState.selectedAdminStore?.id === storeId) {
    appState.selectedAdminStore = null;
    appState.selectedAdminService = null;
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
  const response = await fetch("/api/admin/backup", { credentials: "same-origin" });
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
  const ok = window.confirm("현재 서버의 매장 카테고리가 백업 파일 기준으로 복원됩니다. 복원할까요?");
  if (!ok) return;

  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch("/api/admin/restore", {
    method: "POST",
    credentials: "same-origin",
    body: formData,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || "복원 실패");
  }
  alert("복원이 완료되었습니다");
  appState.selectedAdminStore = null;
  appState.selectedAdminService = null;
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

function getVisibleStore(scope) {
  return scope === "display" ? appState.visibleDisplayCalls : appState.visibleCustomerCalls;
}

function getVisibleCallNumber(scope, serviceType) {
  const store = getVisibleStore(scope);
  const item = store[serviceType];
  if (!item) return null;
  if (Date.now() > item.expiresAt) {
    delete store[serviceType];
    return null;
  }
  return item.ticketNumber;
}

function registerVisibleCall(scope, call) {
  const store = getVisibleStore(scope);
  store[call.service_type] = {
    ticketNumber: call.ticket_number,
    expiresAt: Date.now() + CALL_NUMBER_VISIBLE_MS,
  };

  const timerKey = `${scope}:${call.service_type}`;
  if (appState.visibleClearTimers[timerKey]) {
    window.clearTimeout(appState.visibleClearTimers[timerKey]);
  }
  appState.visibleClearTimers[timerKey] = window.setTimeout(() => {
    delete store[call.service_type];
    delete appState.visibleClearTimers[timerKey];
    if (scope === "display") {
      renderDisplay();
    } else {
      renderCustomer();
    }
  }, CALL_NUMBER_VISIBLE_MS);
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
  }, POPUP_VISIBLE_MS);
}

async function initCustomer() {
  clearPolling();
  const params = new URLSearchParams(window.location.search);
  const storeId = Number(params.get("store_id"));
  const serviceType = validCustomerViewType(params.get("service_type"));
  await loadCustomerStores();
  if (storeId) {
    appState.selectedCustomerStore = appState.customerStores.find((store) => store.id === storeId) || { id: storeId, name: "고객 호출 화면", is_active: true };
    appState.selectedCustomerService = serviceType;
    await loadCustomerDisplayState(true);
    if (appState.selectedCustomerService) {
      setPolling(pollCustomerCalls, 1000);
    }
  } else {
    renderCustomer();
  }
}

async function initAdmin() {
  clearPolling();
  try {
    const status = await apiFetch("/api/admin/status");
    appState.adminAuthenticated = Boolean(status.authenticated);
    if (appState.adminAuthenticated) {
      await loadAdminStores();
      await refreshPushStatus();
    }
  } catch (_error) {
    appState.adminAuthenticated = false;
  }
  renderAdmin();
}

async function initDisplay() {
  clearPolling();
  const params = new URLSearchParams(window.location.search);
  const storeId = Number(params.get("store_id"));
  const serviceType = validCustomerViewType(params.get("service_type"));
  await loadCustomerStores();
  if (storeId) {
    appState.selectedDisplayStore = appState.customerStores.find((store) => store.id === storeId) || { id: storeId, name: "고객 호출 화면", is_active: true };
    appState.selectedDisplayService = serviceType;
    await loadDisplayState(true);
    if (appState.selectedDisplayService) {
      setPolling(pollDisplayCalls, 1000);
    }
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
  const scope = target.dataset.scope;

  if (appState.route === "/admin") {
    primeAdminAudio();
  }

  if (action === "openAdminSettings") {
    appState.adminSettingsOpen = true;
    renderAdmin();
  }

  if (action === "closeAdminSettings") {
    appState.adminSettingsOpen = false;
    renderAdmin();
  }

  if (action === "openViewerSettings") {
    appState.viewerSettingsOpen = scope === "display" ? "display" : "customer";
    if (appState.route === "/display") {
      renderDisplay();
    } else {
      renderCustomer();
    }
  }

  if (action === "closeViewerSettings") {
    appState.viewerSettingsOpen = null;
    if (appState.route === "/display") {
      renderDisplay();
    } else {
      renderCustomer();
    }
  }

  if (action === "stopModalClose") {
    event.stopPropagation();
  }

  if (action === "customerSearch") {
    appState.customerSearch = document.getElementById("customerSearch")?.value.trim() || "";
    safeRun(async () => { await loadCustomerStores(); renderCustomer(); });
  }

  if (action === "selectCustomerStore") {
    appState.selectedCustomerStore = appState.customerStores.find((store) => store.id === storeId) || null;
    appState.selectedCustomerService = null;
    appState.viewerSettingsOpen = null;
    appState.customerState = null;
    appState.lastCustomerCallId = 0;
    safeRun(async () => {
      await loadCustomerDisplayState(true);
    });
  }

  if (action === "selectCustomerService") {
    appState.selectedCustomerService = serviceType;
    appState.viewerSettingsOpen = null;
    safeRun(async () => {
      await loadCustomerDisplayState(true);
      setPolling(pollCustomerCalls, 1000);
    });
  }

  if (action === "changeCustomerService") {
    clearPolling();
    appState.selectedCustomerService = null;
    appState.viewerSettingsOpen = null;
    appState.lastCustomerCallId = appState.customerState?.last_call_id || 0;
    renderCustomer();
  }

  if (action === "changeCustomerStore") {
    clearPolling();
    appState.selectedCustomerStore = null;
    appState.selectedCustomerService = null;
    appState.viewerSettingsOpen = null;
    appState.customerState = null;
    appState.lastCustomerCallId = 0;
    renderCustomer();
  }

  if (action === "adminLogin") {
    const key = document.getElementById("adminKey")?.value.trim() || "";
    safeRun(() => adminLogin(key));
  }

  if (action === "logoutAdmin") {
    appState.adminSettingsOpen = false;
    safeRun(async () => {
      await disablePushNotifications({ silent: true });
      await apiFetch("/api/admin/logout", { method: "POST" });
      appState.adminAuthenticated = false;
      appState.selectedAdminStore = null;
      appState.selectedAdminService = null;
      appState.adminState = null;
      appState.pushSubscribed = false;
      appState.pushMessage = "";
      resetAdminTicketSnapshot();
      renderAdmin();
    });
  }

  if (action === "adminStoreSearch") {
    appState.adminSearch = document.getElementById("adminStoreSearch")?.value.trim() || "";
    appState.manageSearch = "";
    safeRun(async () => { await loadAdminStores(); renderAdmin(); });
  }

  if (action === "selectAdminStore") {
    appState.adminSettingsOpen = false;
    appState.selectedAdminStore = appState.adminStores.find((store) => store.id === storeId) || null;
    appState.selectedAdminService = null;
    resetAdminTicketSnapshot();
    safeRun(async () => {
      await loadAdminState({ initial: true });
      await syncExistingPushToSelectedStore();
      setPolling(() => loadAdminState({ initial: false }), 2000);
      renderAdmin();
    });
  }

  if (action === "selectAdminService") {
    appState.adminSettingsOpen = false;
    appState.selectedAdminService = serviceType;
    safeRun(async () => {
      await loadAdminState({ initial: false });
      setPolling(() => loadAdminState({ initial: false }), 2000);
    });
  }

  if (action === "changeAdminService") {
    appState.adminSettingsOpen = false;
    appState.selectedAdminService = null;
    renderAdmin();
    if (appState.selectedAdminStore) {
      setPolling(() => loadAdminState({ initial: false }), 2000);
    }
  }

  if (action === "changeAdminStore") {
    appState.adminSettingsOpen = false;
    clearPolling();
    appState.selectedAdminStore = null;
    appState.selectedAdminService = null;
    appState.adminState = null;
    appState.pushMessage = "";
    resetAdminTicketSnapshot();
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

  if (action === "enablePush") {
    safeRun(enablePushNotifications);
  }

  if (action === "disablePush") {
    safeRun(() => disablePushNotifications());
  }

  if (action === "openManage") {
    appState.adminSettingsOpen = false;
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
      setPolling(() => loadAdminState({ initial: false }), 2000);
    }
  }

  if (action === "unlockManage") {
    const key = document.getElementById("manageKey")?.value.trim() || "";
    safeRun(async () => {
      await apiFetch("/api/admin/manage/login", { method: "POST", json: { key } });
      appState.managementUnlocked = true;
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
    appState.selectedDisplayStore = appState.customerStores.find((store) => store.id === storeId) || null;
    appState.selectedDisplayService = null;
    appState.viewerSettingsOpen = null;
    appState.displayState = null;
    appState.lastDisplayCallId = 0;
    safeRun(async () => {
      await loadDisplayState(true);
    });
  }

  if (action === "selectDisplayService") {
    appState.selectedDisplayService = serviceType;
    appState.viewerSettingsOpen = null;
    safeRun(async () => {
      await loadDisplayState(true);
      setPolling(pollDisplayCalls, 1000);
    });
  }

  if (action === "changeDisplayService") {
    clearPolling();
    appState.selectedDisplayService = null;
    appState.viewerSettingsOpen = null;
    appState.lastDisplayCallId = appState.displayState?.last_call_id || 0;
    renderDisplay();
  }

  if (action === "changeDisplayStore") {
    clearPolling();
    appState.selectedDisplayStore = null;
    appState.selectedDisplayService = null;
    appState.viewerSettingsOpen = null;
    appState.displayState = null;
    appState.lastDisplayCallId = 0;
    renderDisplay();
  }

  if (action === "enableVoice") {
    appState.voiceEnabled = true;
    appState.viewerSettingsOpen = null;
    localStorage.setItem(VOICE_STORAGE, "1");
    speak("호출 음성이 켜졌습니다.");
    if (appState.route === "/display") {
      renderDisplay();
    } else {
      renderCustomer();
    }
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
