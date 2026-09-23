/* /manager 전용. 일반 브라우저 /admin, /customer의 기존 동작은 변경하지 않는다. */
window.ManagerWeb = (() => {
  let store = null;
  let busy = false;
  let registering = false;
  let resyncRequested = false;
  let registeredToken = "";
  let bindingId = "";
  let pendingService = null;
  let warned = "";
  let selectionVersion = 0;
  let retryTimer = null;
  const STORAGE = "gc_manager_store_code";

  function native(method, value) {
    try {
      const bridge = window.GCManager;
      if (!bridge || typeof bridge[method] !== "function") return null;
      return value === undefined ? bridge[method]() : bridge[method](value);
    } catch (_error) { return null; }
  }
  function deviceInfo() {
    try { return JSON.parse(native("getInfo") || "{}"); } catch (_error) { return {}; }
  }
  function toast(message) { native("showMessage", String(message).slice(0, 240)); }
  function cancelRetry() {
    if (retryTimer !== null) window.clearTimeout(retryTimer);
    retryTimer = null;
  }
  function clearDeviceScope() {
    selectionVersion += 1;
    registeredToken = "";
    bindingId = "";
    warned = "";
    cancelRetry();
    // 네이티브 저장소는 동기적으로 해제. 서버 해지는 네이티브 worker가 재시도한다.
    native("signedOut");
  }
  function setDeviceScope(service) {
    selectionVersion += 1;
    registeredToken = "";
    bindingId = "";
    warned = "";
    cancelRetry();
    native("selectScope", JSON.stringify({ store_id: store.id, service_type: service }));
  }
  function currentSelection() {
    const service = validServiceType(appState.selectedAdminService);
    return appState.adminAuthenticated && store && service ? { storeId: store.id, service } : null;
  }

  function renderEntry() {
    clearPolling();
    appState.adminSettingsOpen = false;
    $app.innerHTML = `
      <main class="manager-entry">
        <section class="manager-entry-card">
          <h1>${store ? "관리자 인증" : "매장 로그인"}</h1>
          <p>${store ? escapeHtml(store.name) : "점코드를 입력하세요"}</p>
          <input class="input" id="${store ? "managerKey" : "managerCode"}"
            type="${store ? "password" : "text"}" autocomplete="${store ? "current-password" : "off"}"
            aria-label="${store ? "관리자 인증키 입력" : "점코드 입력"}"
            placeholder="${store ? "관리자 인증키 입력" : "점코드 입력"}" />
          <button class="btn btn-primary" data-manager-action="verify" ${busy ? "disabled" : ""}>${busy ? "인증 중…" : "인증하기"}</button>
          ${store ? '<button class="btn btn-ghost" data-manager-action="changeStore">점코드 다시 입력</button>' : ""}
        </section>
      </main>`;
  }

  async function authenticate() {
    if (busy) return;
    const input = document.getElementById(store ? "managerKey" : "managerCode");
    const value = (input?.value || "").trim();
    if (!value) throw new Error(store ? "관리자 인증키를 입력하세요" : "점코드를 입력하세요");
    busy = true;
    const button = $app.querySelector('[data-manager-action="verify"]');
    if (button) { button.disabled = true; button.textContent = "인증 중…"; }
    try {
      if (!store) {
        const result = await apiFetch(`/api/stores/by-code/${encodeURIComponent(value)}`);
        store = result.store;
        sessionStorage.setItem(STORAGE, store.code);
        renderEntry();
      } else {
        await apiFetch("/api/admin/login", { method: "POST", json: { key: value } });
        input.value = ""; // 인증키 자체는 JS 저장소나 네이티브에 저장하지 않는다.
        await enterAdmin();
      }
    } finally {
      busy = false;
      const current = $app.querySelector('[data-manager-action="verify"]');
      if (current) { current.disabled = false; current.textContent = "인증하기"; }
    }
  }

  async function enterAdmin() {
    const result = await apiFetch("/api/admin/stores");
    const selected = result.stores.find((item) => item.id === store?.id && item.is_active);
    if (!selected) throw new Error("현재 사용할 수 없는 매장입니다");
    store = selected;
    appState.adminAuthenticated = true;
    appState.adminStores = result.stores;
    appState.selectedAdminStore = selected;
    appState.selectedAdminService = validServiceType(pendingService);
    pendingService = null;
    if (appState.selectedAdminService) setDeviceScope(appState.selectedAdminService);
    else clearDeviceScope(); // 업무 선택 전에는 어떤 업무의 알림도 등록하지 않는다.
    resetAdminTicketSnapshot();
    renderAdmin();
    startStoreEventStream("admin", selected.id);
    await syncDevice();
  }

  async function revokeResult(result) {
    if (!result?.binding_id || !result?.device_secret) return;
    await apiFetch("/api/mobile/unregister", { method: "POST", headers: {
      Authorization: `Bearer ${result.binding_id}.${result.device_secret}`,
    } }).catch(() => null);
  }

  async function syncDevice() {
    if (!currentSelection()) return;
    if (registering) { resyncRequested = true; return; }
    const selection = currentSelection();
    let info = deviceInfo();
    if (!info.token || !info.installation_id) return; // 토큰 준비 후 Android가 다시 호출.
    if (Number(info.store_id) !== selection.storeId || info.service_type !== selection.service || !info.client_scope_id) {
      native("selectScope", JSON.stringify({ store_id: selection.storeId, service_type: selection.service }));
      info = deviceInfo();
    }
    if (Number(info.store_id) !== selection.storeId || info.service_type !== selection.service || !info.client_scope_id) {
      if (warned !== "app-version") toast("매장·업무별 알림을 사용하려면 수정한 매니저 앱을 설치해 주세요.");
      warned = "app-version";
      return;
    }
    if (registeredToken === info.token && bindingId && info.binding_id === bindingId) return;
    cancelRetry();
    registering = true;
    resyncRequested = false;
    const version = selectionVersion;
    let result = null;
    const stillCurrent = () => {
      const current = currentSelection();
      return version === selectionVersion && current?.storeId === selection.storeId &&
        current?.service === selection.service && deviceInfo().client_scope_id === info.client_scope_id;
    };
    try {
      result = await apiFetch("/api/admin/mobile/register", { method: "POST", json: {
        installation_id: info.installation_id, token: info.token,
        store_id: selection.storeId, service_type: selection.service,
        firebase_project_id: info.firebase_project_id || "",
      } });
      if (!stillCurrent()) {
        await revokeResult(result);
        resyncRequested = true;
        return;
      }
      if (Number(result.store_id) !== selection.storeId || result.service_type !== selection.service ||
          result.notification_scope !== "selected_service") {
        await revokeResult(result);
        throw new Error("서버도 매장·업무별 알림 수정본으로 배포해 주세요.");
      }
      const saved = native("registered", JSON.stringify({ ...result, origin: location.origin,
        client_scope_id: info.client_scope_id }));
      if (saved !== true) {
        await revokeResult(result);
        throw new Error("단말 알림 등록 정보를 저장하지 못했습니다. 업무를 다시 선택해 주세요.");
      }
      bindingId = result.binding_id;
      registeredToken = info.token;
      warned = "";
    } catch (error) {
      if (stillCurrent()) {
        if (warned !== error.message) { toast(`앱 알림: ${error.message}`); warned = error.message; }
        retryTimer = window.setTimeout(() => { retryTimer = null; syncDevice(); }, 15000);
      }
    } finally {
      registering = false;
      const retryLatest = resyncRequested || version !== selectionVersion;
      resyncRequested = false;
      if (retryLatest && currentSelection()) await syncDevice();
    }
  }

  function sessionExpired() {
    clearPolling();
    appState.adminAuthenticated = false;
    appState.selectedAdminStore = null;
    appState.selectedAdminService = null;
    appState.adminState = null;
    clearDeviceScope();
    renderEntry();
  }

  async function logout() {
    store = null;
    pendingService = null;
    sessionStorage.removeItem(STORAGE);
    sessionExpired(); // 로그아웃 응답 전에 인증 상태/기기 수신부터 차단한다.
    await apiFetch("/api/admin/logout", { method: "POST" });
  }

  function chooseService() {
    appState.adminSettingsOpen = false;
    appState.selectedAdminService = null;
    clearDeviceScope();
    renderAdmin();
    if (store) startStoreEventStream("admin", store.id);
  }

  function openService(serviceType) {
    const service = validServiceType(serviceType);
    if (!service) return;
    if (!appState.adminAuthenticated) { pendingService = service; return; }
    appState.selectedAdminService = service;
    appState.adminSettingsOpen = false;
    setDeviceScope(service); // 이전 업무를 해지한 뒤 선택한 업무 하나만 등록한다.
    renderAdmin();
    startStoreEventStream("admin", appState.selectedAdminStore.id);
    syncDevice();
  }

  async function testNotification() {
    if (!appState.adminAuthenticated) { toast("먼저 관리자 인증을 진행하세요."); return; }
    if (!currentSelection()) { toast("알림을 받을 간단서비스 또는 구매상담을 먼저 선택하세요."); return; }
    await syncDevice();
    if (!bindingId) { toast("앱 알림 등록이 완료되지 않았습니다. 잠시 후 다시 시도하세요."); return; }
    try {
      await apiFetch("/api/admin/mobile/test", { method: "POST", json: { binding_id: bindingId } });
      toast("선택한 매장·업무의 테스트 알림 전송을 요청했습니다.");
    } catch (error) { toast(error.message); }
  }

  async function init() {
    document.body.classList.add("manager-app");
    $app.addEventListener("click", (event) => {
      const action = event.target.closest("[data-manager-action]")?.dataset.managerAction;
      if (action === "verify") safeRun(authenticate);
      if (action === "changeStore" && !busy) {
        clearDeviceScope();
        store = null;
        pendingService = null;
        sessionStorage.removeItem(STORAGE);
        renderEntry();
      }
    });
    $app.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.isComposing && ["managerKey", "managerCode"].includes(event.target.id)) {
        event.preventDefault(); safeRun(authenticate);
      }
    });
    const query = new URLSearchParams(location.search);
    if (query.get("login") === "1") {
      // 연결 방식 화면에서 들어온 경우 이전 쿠키/저장된 매장으로 인증을 건너뛰지 않는다.
      store = null;
      pendingService = null;
      sessionStorage.removeItem(STORAGE);
      sessionExpired();
      window.history.replaceState(null, "", "/manager");
      return;
    }
    const info = deviceInfo();
    pendingService = validServiceType(query.get("service_type")) ||
      (info.binding_id ? validServiceType(info.service_type) : null);
    const code = sessionStorage.getItem(STORAGE) || info.store_code;
    if (code) {
      try {
        store = (await apiFetch(`/api/stores/by-code/${encodeURIComponent(code)}`)).store;
        const status = await apiFetch("/api/admin/status");
        if (status.authenticated) { await enterAdmin(); return; }
        clearDeviceScope();
      } catch (error) {
        clearDeviceScope();
        if (error.status === 404) { store = null; sessionStorage.removeItem(STORAGE); }
        else toast("서버 연결을 확인한 뒤 다시 인증하세요.");
      }
    }
    renderEntry();
  }
  return { init, renderEntry, syncDevice, sessionExpired, logout, chooseService, openService, testNotification };
})();
