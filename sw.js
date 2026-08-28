// Origination PWA — 빌드 단위 캐시.
// 주간 갱신이라 부분 무효화가 필요 없다. 새 빌드면 이전 캐시를 통째로 버린다.
const V = '20260828_0932';
const CACHE = 'orig-' + V;
const ASSETS = ['./', './index.html', './data.json', './manifest.json',
                './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// 캐시 우선 — 오프라인에서 열려야 한다. 네트워크가 되면 뒤에서 갱신한다.
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(caches.match(e.request).then(hit => {
    const net = fetch(e.request).then(res => {
      if (res && res.status === 200 && res.type === 'basic') {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
      }
      return res;
    }).catch(() => hit);
    return hit || net;
  }));
});
