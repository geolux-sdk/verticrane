/*
 * Pi Wi-Fi 설정 — 서비스워커
 * 앱 셸(정적 파일)을 캐시해서 오프라인에서도 페이지가 뜨게 한다.
 * BLE 통신 자체는 런타임 기능이라 캐시와 무관.
 */
// 캐시된 정적 파일(index.html 등)을 바꾸면 이 버전 문자열을 올린다.
// 그래야 서비스워커가 새로 설치되며 옛 캐시를 비우고 새 파일을 받는다.
const CACHE = "pi-ble-v31";
const SHELL = [
  "./index.html",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-512-maskable.png",
];

// 설치: 앱 셸 캐시
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

// 페이지가 "지금 이 페이지를 제어하는 워커의 캐시 이름" 을 직접 물어볼 수 있게
// 한다. 페이지에서 caches.keys() 로 추정하면 설치 중인 새 캐시와 아직 안 지워진
// 옛 캐시가 같이 보여, 갱신됐는데도 옛 이름이 표시된다(여기서 헤맸다).
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "cache-name") {
    event.source.postMessage({ type: "cache-name", cache: CACHE });
  }
});

// 활성화: 옛 버전 캐시 정리
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// fetch 전략은 두 가지다.
//  - 페이지(HTML): 네트워크 우선. 서버가 켜져 있으면 항상 최신 화면이 뜬다.
//    캐시 우선으로 두면 새 버전을 받아 놓고도 화면은 옛 버전이라, 갱신됐는지
//    아닌지 알 수 없는 상태가 된다(실제로 여기서 헤맸다). 서버가 없으면
//    캐시로 폴백하므로 오프라인 실행은 그대로 된다.
//  - 나머지(폰트·아이콘·manifest): 캐시 우선. 거의 안 바뀌고 용량이 크다.
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) {
    return;
  }

  if (req.mode === "navigate" || req.destination === "document") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req).then((c) => c || caches.match("./index.html")))
    );
    return;
  }

  event.respondWith(
    caches.match(req).then((cached) => {
      const fetched = fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => cached);
      return cached || fetched;
    })
  );
});
