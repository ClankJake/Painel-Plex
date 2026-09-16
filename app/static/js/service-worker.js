// app/static/js/service-worker.js
//
// O service worker do painel. Faz duas coisas, e a segunda é a razão de ele
// existir: serve os ficheiros estáticos com cache, e RECEBE AS NOTIFICAÇÕES
// PUSH — que chegam com o painel fechado, e por isso não podem ser tratadas por
// nenhum script da página.
//
// ⚠️ **Ele é servido da RAIZ ('/service-worker.js'), não de '/static/js/'.** O
// alcance de um service worker é a pasta de onde ele vem: em '/static/js/' ele
// não controlava a 'start_url' do manifesto ('/'), o que fazia o Android não
// oferecer instalar o painel e impedia uma notificação clicada de encontrar a
// aba já aberta. A rota `serve_sw` existe precisamente para isto.

const CACHE_NAME = 'painel-estaticos-v2';

// ⚠️ **Só se trata do que está em '/static/'.** Quando o alcance passou a ser
// a raiz, a estratégia antiga — procurar TUDO no cache primeiro — passaria a
// servir páginas e respostas da API guardadas: o painel mostraria a lista de
// usuários de ontem, e um pagamento confirmado nunca apareceria. Tudo o que não
// for estático segue para a rede sem este ficheiro se meter.
function ehEstatico(url) {
    return url.origin === self.location.origin && url.pathname.startsWith('/static/');
}

self.addEventListener('install', (event) => {
    // Não há pré-carregamento: os nomes dos ficheiros mudam com o build e uma
    // lista escrita à mão fica desatualizada sem ninguém dar por isso (a antiga
    // pedia '/static/js/index.js', que não existe). O cache enche-se sozinho.
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const nomes = await caches.keys();
        await Promise.all(nomes.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)));
        // Assume o controlo das abas já abertas, para o push funcionar sem
        // obrigar a pessoa a recarregar a página.
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;

    let url;
    try {
        url = new URL(event.request.url);
    } catch (e) {
        return;
    }
    if (!ehEstatico(url)) return;

    // Devolve já o que está no cache e vai buscar a versão nova em silêncio:
    // o painel abre depressa e a próxima visita já tem o ficheiro atualizado.
    event.respondWith((async () => {
        const cache = await caches.open(CACHE_NAME);
        const guardado = await cache.match(event.request);
        const daRede = fetch(event.request).then((resposta) => {
            // Uma resposta de erro nunca entra no cache: guardá-la fixava um
            // 404 até alguém limpar os dados do navegador.
            if (resposta && resposta.ok) cache.put(event.request, resposta.clone());
            return resposta;
        }).catch(() => guardado);
        return guardado || daRede;
    })());
});

// --- Notificações push -----------------------------------------------------

self.addEventListener('push', (event) => {
    let dados = {};
    try {
        dados = event.data ? event.data.json() : {};
    } catch (e) {
        // Uma notificação sem corpo JSON não é motivo para não mostrar nada: o
        // navegador exige que TODA a mensagem recebida seja mostrada, e não
        // mostrar nenhuma faz o Chrome cortar as notificações deste site.
        dados = { body: event.data ? event.data.text() : '' };
    }

    const titulo = dados.title || 'Painel';
    const opcoes = {
        body: dados.body || '',
        icon: '/static/icons/icon-192x192.png',
        badge: '/static/icons/icon-192x192.png',
        data: { url: dados.url || '/' },
    };
    if (dados.tag) {
        // Mesma etiqueta = substitui a anterior em vez de empilhar. O
        // `renotify` faz o aparelho avisar mesmo assim (som/vibração); sem ele,
        // a substituição era silenciosa e passava despercebida.
        opcoes.tag = dados.tag;
        opcoes.renotify = true;
    }

    event.waitUntil(self.registration.showNotification(titulo, opcoes));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const destino = (event.notification.data && event.notification.data.url) || '/';

    event.waitUntil((async () => {
        let alvo;
        try {
            alvo = new URL(destino, self.location.origin);
        } catch (e) {
            alvo = new URL('/', self.location.origin);
        }

        // Reaproveita uma aba do painel já aberta em vez de abrir a quarta.
        // `includeUncontrolled` é preciso para apanhar as abas que foram
        // abertas antes deste service worker assumir o controlo.
        const janelas = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        for (const janela of janelas) {
            if (new URL(janela.url).origin !== alvo.origin) continue;
            if ('navigate' in janela && janela.url !== alvo.href) {
                const focada = await janela.navigate(alvo.href);
                return focada ? focada.focus() : janela.focus();
            }
            return janela.focus();
        }
        if (self.clients.openWindow) return self.clients.openWindow(alvo.href);
    })());
});

// 🐛 **O navegador pode trocar a subscrição sozinho** (o serviço de push
// renova-a, o utilizador limpa dados do site). Quando isso acontece o painel
// fica com um endereço morto e as notificações param — sem erro nenhum do lado
// de cá. Aqui volta-se a subscrever com a mesma chave do painel e grava-se a
// nova; a antiga é apagada pelo próprio painel, ao levar com um 410.
self.addEventListener('pushsubscriptionchange', (event) => {
    event.waitUntil((async () => {
        const antiga = event.oldSubscription || await self.registration.pushManager.getSubscription();
        const chave = antiga && antiga.options && antiga.options.applicationServerKey;
        if (!chave) return;

        const nova = await self.registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: chave,
        });
        const dados = nova.toJSON();
        await fetch('/api/notifications/push/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // O pedido parte do service worker, fora de qualquer página: sem
            // isto não leva o cookie da sessão e o painel responde 401.
            credentials: 'include',
            body: JSON.stringify({
                endpoint: dados.endpoint,
                keys: { p256dh: dados.keys.p256dh, auth: dados.keys.auth },
            }),
        });
    })());
});
