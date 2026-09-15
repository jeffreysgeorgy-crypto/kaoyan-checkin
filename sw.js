// Service Worker —— 考研打卡 PWA 离线缓存
var CACHE_VER = 'kaoyan-v20260915l';
var CORE_FILES = [
  './',
  './index.html',
  './style.css?v=20260915l',
  './app.js?v=20260915l',
  './data.js?v=20260915l',
  './manifest.json',
  './icon.svg',
  'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js'
];

// 安装：预缓存核心文件
self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE_VER).then(function (cache) {
      // CDN 可能跨域失败，逐个 add 不阻塞整体
      return Promise.allSettled(CORE_FILES.map(function (f) {
        return cache.add(f);
      }));
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

// 激活：清理旧缓存
self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) {
        return k !== CACHE_VER;
      }).map(function (k) {
        return caches.delete(k);
      }));
    }).then(function () {
      return self.clients.claim();
    })
  );
});

// 请求拦截：缓存优先，回退到网络（Chart.js CDN 等动态资源走网络优先）
self.addEventListener('fetch', function (e) {
  var url = e.request.url;
  // CDN 资源：网络优先（加载失败时用缓存降级）
  if (url.indexOf('cdn.jsdelivr.net') >= 0) {
    e.respondWith(
      fetch(e.request).catch(function () {
        return caches.match(e.request);
      })
    );
    return;
  }
  // 本地文件：缓存优先
  e.respondWith(
    caches.match(e.request).then(function (cached) {
      if (cached) return cached;
      return fetch(e.request).then(function (resp) {
        // 动态缓存新请求的文件
        if (resp && resp.status === 200 && url.indexOf('http') === 0) {
          var respClone = resp.clone();
          caches.open(CACHE_VER).then(function (cache) {
            cache.put(e.request, respClone);
          });
        }
        return resp;
      }).catch(function () {
        // 离线时返回缓存首页
        if (e.request.mode === 'navigate') {
          return caches.match('./index.html');
        }
      });
    })
  );
});
