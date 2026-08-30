// Origination PWA — 빌드 단위 캐시.
// 껍데기(index.html)는 네트워크 우선이다. 캐시 우선으로 두면 배포해도 폰이
// 계속 옛 화면을 보여준다 — 실제로 그렇게 물렸다.
// 데이터는 URL 에 ?v=BUILD 가 붙어 새 빌드면 자동으로 새 주소가 되므로
// 캐시 우선이어도 안전하고, 오프라인에서는 마지막 것이 그대로 뜬다.
const V = '20260830_2032';
const CACHE = 'orig-' + V;
const ASSETS = ['./', './index.html', './data.json', './entities.json', './deals.json', './seoul.json',
                './geo.json', './manifest.json', './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

const isShell = u => u.mode === 'navigate' ||
  /\/(index\.html)?(\?|$)/.test(new URL(u.url).pathname + new URL(u.url).search);

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;   // firebase CDN 등은 건드리지 않는다

  if (isShell(e.request)) {                     // 껍데기 — 네트워크 우선
    e.respondWith(
      fetch(e.request).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
        return res;
      }).catch(() => caches.match(e.request).then(h => h || caches.match('./index.html')))
    );
    return;
  }
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request).then(res => {
    if (res && res.status === 200 && res.type === 'basic') {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy));
    }
    return res;
  })));
});
