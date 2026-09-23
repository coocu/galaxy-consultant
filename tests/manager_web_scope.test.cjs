// Run from the server folder: node --test tests/manager_web_scope.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../app/static/js/manager-web.js'), 'utf8');
const services = ['simple_service', 'purchase_consult'];

function fixture({ search = '?login=1', savedCode = null, delay = false, oldServer = false } = {}) {
  const stores = [{id: 1, code: 'A001', name: '매장 1', is_active: true},
                  {id: 2, code: 'B001', name: '매장 2', is_active: true}];
  const info = { installation_id: 'test-installation', token: 'test-token', firebase_project_id: 'test-project',
    store_id: 0, service_type: '', client_scope_id: '', binding_id: '', store_code: '' };
  const calls = [], saves = [], revokes = [], messages = [], deferred = [], tasks = [], timers = new Map();
  const handlers = {}, inputs = { managerCode: { value: '' }, managerKey: { value: '' } };
  const storage = new Map(savedCode ? [['gc_manager_store_code', savedCode]] : []);
  let counter = 0, authenticated = false;
  const appState = { adminAuthenticated: false, selectedAdminService: null };
  const app = { innerHTML: '', addEventListener: (event, cb) => handlers[event] = cb,
    querySelector: () => ({ disabled: false, textContent: '' }) };
  const native = {
    getInfo: () => JSON.stringify(info),
    signedOut: () => Object.assign(info, {store_id: 0, service_type: '', client_scope_id: '', binding_id: '', store_code: ''}),
    selectScope: (text) => {
      const next = JSON.parse(text);
      if (info.store_id !== next.store_id || info.service_type !== next.service_type || !info.client_scope_id) {
        native.signedOut();
        Object.assign(info, next, {client_scope_id: `scope-${++counter}`});
      }
      return info.client_scope_id;
    },
    registered: (text) => {
      const value = JSON.parse(text);
      if (value.client_scope_id !== info.client_scope_id || value.store_id !== info.store_id ||
          value.service_type !== info.service_type) return false;
      saves.push(value);
      info.binding_id = value.binding_id;
      info.store_code = value.store_code;
      return true;
    },
    showMessage: (msg) => messages.push(msg),
  };
  function makeBinding(body) {
    return { ok: true, ...body, binding_id: `binding-${++counter}`, device_secret: 'unit-secret',
      store_code: stores.find(s => s.id === body.store_id).code,
      notification_scope: oldServer ? 'both_services' : 'selected_service',
      service_type: oldServer ? undefined : body.service_type, expires_at: '2099-01-01T00:00:00Z' };
  }
  const context = {
    window: { GCManager: native, history: {replaceState: (...args) => calls.push({history: args})},
      setTimeout: (fn) => { const id = ++counter; timers.set(id, fn); return id; },
      clearTimeout: (id) => timers.delete(id) },
    appState, $app: app, URLSearchParams,
    location: {search, origin: 'https://galaxy-consultant.onrender.com'},
    document: { body: {classList: {add() {}}}, getElementById: (id) => inputs[id] },
    sessionStorage: {getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k)},
    validServiceType: value => services.includes(value) ? value : null,
    escapeHtml: value => value, clearPolling() {}, renderAdmin() {}, resetAdminTicketSnapshot() {}, startStoreEventStream() {},
    safeRun: fn => { const result = Promise.resolve().then(fn); tasks.push(result); return result; },
    async apiFetch(url, options = {}) {
      calls.push({url, body: options.json});
      if (url.startsWith('/api/stores/by-code/')) return {store: stores.find(s => s.code === decodeURIComponent(url.split('/').pop()))};
      if (url === '/api/admin/login') { authenticated = true; return {ok: true}; }
      if (url === '/api/admin/logout') { authenticated = false; return {ok: true}; }
      if (url === '/api/admin/status') return {authenticated};
      if (url === '/api/admin/stores') return {stores};
      if (url === '/api/admin/mobile/register') {
        const binding = makeBinding(options.json);
        if (delay) return new Promise(resolve => deferred.push({binding, resolve: () => resolve(binding)}));
        return binding;
      }
      if (url === '/api/mobile/unregister') { revokes.push(options.headers.Authorization); return {ok: true}; }
      if (url === '/api/admin/mobile/test') return {ok: true};
      throw new Error(`Unexpected endpoint ${url}`);
    },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  const web = context.window.ManagerWeb;
  async function verify(value) {
    const id = appState.adminAuthenticated ? 'managerKey' : app.innerHTML.includes('id="managerCode"') ? 'managerCode' : 'managerKey';
    inputs[id].value = value;
    handlers.click({target: {closest: () => ({dataset: {managerAction: 'verify'}})}});
    await Promise.all(tasks.splice(0));
  }
  async function login(code = 'A001') { await verify(code); await verify('admin-test-key'); }
  const registrations = () => calls.filter(c => c.url === '/api/admin/mobile/register');
  return {web, info, app, appState, calls, saves, revokes, messages, deferred, timers, context, login, verify, registrations};
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('normal entry always shows store login, even when an old store was remembered', async () => {
  const f = fixture({savedCode: 'B001'});
  await f.web.init();
  assert.match(f.app.innerHTML, /매장 로그인/);
  assert.match(f.app.innerHTML, /id="managerCode"/);
  assert.equal(f.calls.filter(c => c.url).length, 0);
  assert.equal(f.registrations().length, 0);
});

test('store and admin login do not subscribe before selecting a service', async () => {
  const f = fixture(); await f.web.init(); await f.login();
  assert.equal(f.appState.adminAuthenticated, true);
  assert.equal(f.appState.selectedAdminService, null);
  await f.web.syncDevice();
  assert.equal(f.registrations().length, 0);
});

for (const store of ['A001', 'B001']) for (const service of services) {
  test(`registers exactly ${store} / ${service}`, async () => {
    const f = fixture(); await f.web.init(); await f.login(store);
    f.web.openService(service); await tick();
    assert.equal(f.registrations().length, 1);
    assert.equal(f.registrations()[0].body.store_id, store === 'A001' ? 1 : 2);
    assert.equal(f.registrations()[0].body.service_type, service);
    assert.equal(f.saves.length, 1);
    await f.web.syncDevice();
    assert.equal(f.registrations().length, 1);
  });
}

test('switching service replaces the native scope and registers the new service only', async () => {
  const f = fixture(); await f.web.init(); await f.login();
  f.web.openService('simple_service'); await tick();
  const first = f.info.client_scope_id;
  f.web.openService('purchase_consult'); await tick();
  assert.notEqual(f.info.client_scope_id, first);
  assert.equal(f.info.service_type, 'purchase_consult');
  assert.equal(f.registrations().at(-1).body.service_type, 'purchase_consult');
});

test('late registration for old service is revoked and only the latest selection is saved', async () => {
  const f = fixture({delay: true}); await f.web.init(); await f.login();
  f.web.openService('simple_service');
  f.web.openService('purchase_consult');
  assert.equal(f.deferred.length, 1); // Requests are serialized, not racing on the server.
  f.deferred[0].resolve(); await tick();
  assert.equal(f.revokes.length, 1); assert.equal(f.saves.length, 0);
  assert.equal(f.deferred.length, 2);
  f.deferred[1].resolve(); await tick();
  assert.equal(f.saves.length, 1); assert.equal(f.saves[0].service_type, 'purchase_consult');
});

test('simple -> purchase -> simple still rejects the first simple response', async () => {
  const f = fixture({delay: true}); await f.web.init(); await f.login();
  f.web.openService('simple_service');
  const originalScope = f.info.client_scope_id;
  f.web.openService('purchase_consult'); f.web.openService('simple_service');
  assert.notEqual(f.info.client_scope_id, originalScope);
  f.deferred[0].resolve(); await tick();
  assert.equal(f.saves.length, 0); assert.equal(f.revokes.length, 1);
  f.deferred[1].resolve(); await tick();
  assert.equal(f.saves.length, 1); assert.equal(f.saves[0].client_scope_id, f.info.client_scope_id);
});

test('returning to service chooser blocks an in-flight registration', async () => {
  const f = fixture({delay: true}); await f.web.init(); await f.login();
  f.web.openService('simple_service'); f.web.chooseService();
  f.deferred[0].resolve(); await tick();
  assert.equal(f.info.binding_id, ''); assert.equal(f.saves.length, 0);
  assert.equal(f.registrations().length, 1); assert.equal(f.revokes.length, 1);
});

test('logout suppresses a late registration result', async () => {
  const f = fixture({delay: true}); await f.web.init(); await f.login();
  f.web.openService('simple_service'); await f.web.logout();
  f.deferred[0].resolve(); await tick();
  assert.equal(f.saves.length, 0); assert.equal(f.info.binding_id, '');
  assert.match(f.app.innerHTML, /매장 로그인/);
});

test('changing store while a register response is pending never saves the old store', async () => {
  const f = fixture({delay: true}); await f.web.init(); await f.login('A001');
  f.web.openService('simple_service'); await f.web.logout();
  await f.login('B001'); f.web.openService('purchase_consult');
  f.deferred[0].resolve(); await tick();
  assert.equal(f.saves.length, 0);
  f.deferred[1].resolve(); await tick();
  assert.equal(f.saves.length, 1); assert.equal(f.saves[0].store_id, 2);
});

test('old both-services server response is rejected rather than silently accepted', async () => {
  const f = fixture({oldServer: true}); await f.web.init(); await f.login();
  f.web.openService('simple_service'); await tick();
  assert.equal(f.saves.length, 0); assert.equal(f.revokes.length, 1);
  assert.match(f.messages.join(' '), /서버도 매장·업무별/);
});

test('Firebase token becoming ready registers only the already selected service', async () => {
  const f = fixture(); f.info.token = '';
  await f.web.init(); await f.login(); f.web.openService('purchase_consult'); await tick();
  assert.equal(f.registrations().length, 0);
  f.info.token = 'refreshed-token'; await f.web.syncDevice();
  assert.equal(f.registrations().length, 1);
  assert.equal(f.registrations()[0].body.service_type, 'purchase_consult');
});

test('test notification requires a service selection and uses its binding', async () => {
  const f = fixture(); await f.web.init(); await f.login();
  await f.web.testNotification();
  assert.equal(f.calls.filter(c => c.url === '/api/admin/mobile/test').length, 0);
  f.web.openService('purchase_consult'); await tick(); await f.web.testNotification();
  const tests = f.calls.filter(c => c.url === '/api/admin/mobile/test');
  assert.equal(tests.length, 1); assert.equal(tests[0].body.binding_id, f.info.binding_id);
});
