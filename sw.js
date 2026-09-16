// Service Worker —— 考研打卡 PWA 离线缓存
var CACHE_VER = 'kaoyan-v20260916i';
var CORE_FILES = [
  './',
  './index.html',
  './style.css?v=20260916i',
  './app.js?v=20260916i',
  './data.js?v=20260916i',
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

// 请求拦截
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
  // HTML 文档：网络优先（确保用户始终拿到最新版本号引用，离线时回退缓存）
  if (e.request.mode === 'navigate' || url.endsWith('index.html') || url.endsWith('/')) {
    e.respondWith(
      fetch(e.request).then(function (resp) {
        if (resp && resp.status === 200) {
          var clone = resp.clone();
          caches.open(CACHE_VER).then(function (cache) { cache.put(e.request, clone); });
        }
        return resp;
      }).catch(function () {
        return caches.match(e.request).then(function (c) {
          return c || caches.match('./index.html');
        });
      })
    );
    return;
  }
  // 版本化静态资源（app.js?v=xxx 等）：缓存优先，每个版本独立缓存
  e.respondWith(
    caches.match(e.request).then(function (cached) {
      if (cached) return cached;
      return fetch(e.request).then(function (resp) {
        if (resp && resp.status === 200 && url.indexOf('http') === 0) {
          var respClone = resp.clone();
          caches.open(CACHE_VER).then(function (cache) {
            cache.put(e.request, respClone);
          });
        }
        return resp;
      });
    })
  );
});
