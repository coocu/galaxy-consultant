self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_error) {
    payload = {};
  }

  const title = payload.title || "새 대기번호 발급";
  const options = {
    body: payload.body || "새 번호표가 발급되었습니다.",
    tag: payload.tag || "codenote-ticket",
    renotify: payload.renotify !== false,
    data: payload.data || {},
    requireInteraction: false,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || "/admin";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        try {
          const clientUrl = new URL(client.url);
          const expectedUrl = new URL(targetUrl, self.location.origin);
          if (clientUrl.origin === expectedUrl.origin) {
            client.focus();
            return client.navigate(expectedUrl.href);
          }
        } catch (_error) {

        }
      }
      return clients.openWindow(targetUrl);
    })
  );
});
