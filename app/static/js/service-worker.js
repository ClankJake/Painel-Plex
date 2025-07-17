const CACHE_NAME = 'plex-panel-cache-v1';
const urlsToCache = [
  '/',
  '/static/js/index.js',
  '/static/js/settings.js',
  '/static/js/account.js',
  '/static/js/setup.js',
  '/static/js/statistics.js',
  '/static/js/login.js',
  '/static/js/invite.js',
  // URLs de origem cruzada que precisam de tratamento especial
  'https://cdn.tailwindcss.com',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap'
];

// Instalação do Service Worker e caching dos recursos
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('Cache aberto');
        
        // Mapeia as URLs para promessas de cache individuais para lidar com CORS
        const cachePromises = urlsToCache.map(urlToCache => {
          // Verifica se o pedido é para uma URL de origem cruzada
          if (urlToCache.startsWith('http')) {
            // Para pedidos de origem cruzada, usa o modo 'no-cors'.
            // Isto resulta numa resposta "opaca", que é adequada para fazer cache de recursos como CSS e fontes.
            const request = new Request(urlToCache, { mode: 'no-cors' });
            return fetch(request)
              .then(response => cache.put(urlToCache, response))
              .catch(err => {
                // Regista um erro se o cache de um recurso específico falhar, mas não impede que os outros sejam cacheados.
                console.error(`Falha ao fazer cache de ${urlToCache}:`, err);
              });
          } else {
            // Para pedidos da mesma origem, o cache.add() funciona perfeitamente.
            return cache.add(urlToCache);
          }
        });

        return Promise.all(cachePromises);
      })
      .catch(err => {
        console.error("Falha ao abrir o cache durante a instalação:", err);
      })
  );
});

// Interceta os pedidos de rede
self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        // Se o recurso estiver no cache, retorna-o
        if (response) {
          return response;
        }
        // Caso contrário, faz o pedido à rede
        return fetch(event.request).catch(err => {
            console.error("Falha no fetch de rede:", event.request.url, err);
            // Opcional: Retornar uma página de fallback offline aqui
        });
      }
    )
  );
});

// Limpa caches antigos
self.addEventListener('activate', event => {
  const cacheWhitelist = [CACHE_NAME];
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheWhitelist.indexOf(cacheName) === -1) {
            console.log("A apagar cache antigo:", cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});
