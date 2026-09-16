// app/static/js/push.js
//
// As notificações que chegam ao aparelho mesmo com o painel fechado.
//
// O que este ficheiro faz é pouco e tem uma ordem que importa:
//   1. confirmar que este navegador suporta push (nem todos suportam);
//   2. pedir permissão à pessoa, e só quando ela clica no botão — pedi-la ao
//      abrir a página faz o Chrome bloquear o pedido para sempre nesse site;
//   3. subscrever no serviço de push do navegador com a chave pública do painel;
//   4. mandar essa subscrição ao painel, que é quem a vai usar para entregar.
//
// ⚠️ **No iPhone e no iPad isto só existe depois de o painel ser adicionado à
// tela de início.** O Safari não expõe o `PushManager` numa aba normal, por
// mais permissões que se peçam — e o sintoma, sem o aviso, é um botão que
// simplesmente não aparece e ninguém sabe porquê.

import { fetchAPI, showToast } from './utils.js';

/** Converte a chave pública VAPID (base64 de URL) para os bytes que o navegador pede. */
function chaveParaBytes(base64UrlSemPreenchimento) {
    const preenchimento = '='.repeat((4 - (base64UrlSemPreenchimento.length % 4)) % 4);
    const base64 = (base64UrlSemPreenchimento + preenchimento).replace(/-/g, '+').replace(/_/g, '/');
    const bruto = window.atob(base64);
    return Uint8Array.from(bruto, (c) => c.charCodeAt(0));
}

/** Um nome curto para a pessoa reconhecer o aparelho na lista. */
function etiquetaDoAparelho() {
    const ua = navigator.userAgent || '';
    const navegador = /Edg\//.test(ua) ? 'Edge'
        : /OPR\//.test(ua) ? 'Opera'
        : /Chrome\//.test(ua) ? 'Chrome'
        : /Firefox\//.test(ua) ? 'Firefox'
        : /Safari\//.test(ua) ? 'Safari' : null;
    const sistema = /Android/.test(ua) ? 'Android'
        : /iPhone|iPad|iPod/.test(ua) ? 'iOS'
        : /Windows/.test(ua) ? 'Windows'
        : /Mac OS X/.test(ua) ? 'macOS'
        : /Linux/.test(ua) ? 'Linux' : null;
    if (navegador && sistema) return `${navegador} no ${sistema}`;
    return navegador || sistema || null;
}

/** O painel está instalado na tela de início? É o que o iOS exige para haver push. */
function estaInstalado() {
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
}

document.addEventListener('DOMContentLoaded', () => {
    const scriptTag = document.getElementById('push-script');
    if (!scriptTag) return;

    const i18n = {
        ativar: scriptTag.dataset.i18nAtivar || 'Ativar notificações neste aparelho',
        desativar: scriptTag.dataset.i18nDesativar || 'Desativar notificações neste aparelho',
        ativando: scriptTag.dataset.i18nAtivando || 'Ativando...',
        ativadas: scriptTag.dataset.i18nAtivadas || 'Este aparelho vai avisar sobre pagamentos e pedidos.',
        bloqueadas: scriptTag.dataset.i18nBloqueadas || 'As notificações estão bloqueadas nas configurações do navegador.',
        falhou: scriptTag.dataset.i18nFalhou || 'Não foi possível ativar as notificações. Tente de novo.',
        instalar: scriptTag.dataset.i18nInstalar || 'No iPhone, adicione o painel à tela de início para receber notificações.',
    };
    const urls = {
        subscribe: scriptTag.dataset.urlSubscribe,
        unsubscribe: scriptTag.dataset.urlUnsubscribe,
    };
    const chavePublica = scriptTag.dataset.chavePublica || '';
    const ativoNoPainel = scriptTag.dataset.ativo === 'true';

    const linha = document.getElementById('push-row');
    const botao = document.getElementById('push-toggle');
    const rotulo = document.getElementById('push-toggle-text');
    const dica = document.getElementById('push-hint');
    if (!linha || !botao || !rotulo) return;

    const suportado = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

    // Sem suporte, o botão não aparece — menos no iPhone por instalar, onde a
    // ausência TEM explicação e a explicação é acionável.
    if (!ativoNoPainel || !chavePublica) return;
    if (!suportado) {
        const iOS = /iPhone|iPad|iPod/.test(navigator.userAgent || '');
        if (iOS && !estaInstalado()) {
            linha.classList.remove('hidden');
            botao.classList.add('hidden');
            if (dica) dica.textContent = i18n.instalar;
        }
        return;
    }

    linha.classList.remove('hidden');

    function desenhar(subscrito, mensagem) {
        rotulo.textContent = subscrito ? i18n.desativar : i18n.ativar;
        botao.disabled = false;
        if (dica) dica.textContent = mensagem || (subscrito ? i18n.ativadas : '');
    }

    async function registoDoServiceWorker() {
        // `ready` só resolve quando há um service worker ATIVO a controlar a
        // página. Numa primeira visita ele ainda está a instalar, e subscrever
        // antes disso rejeita sem explicação.
        return navigator.serviceWorker.ready;
    }

    async function subscricaoAtual() {
        const registo = await registoDoServiceWorker();
        return registo.pushManager.getSubscription();
    }

    /**
     * Manda a subscrição ao painel.
     *
     * É chamada também no arranque, quando já há permissão: a subscrição vive
     * no navegador e a linha vive na base de dados do painel. Um restauro de
     * backup apagava a segunda e deixava a primeira — o navegador convencido
     * de que estava tudo ligado, o painel sem para onde entregar.
     */
    async function guardarNoPainel(subscricao) {
        const dados = subscricao.toJSON();
        await fetchAPI(urls.subscribe, 'POST', {
            endpoint: dados.endpoint,
            keys: { p256dh: dados.keys.p256dh, auth: dados.keys.auth },
            device_label: etiquetaDoAparelho(),
        });
        marcarSincronizado(dados.endpoint);
    }

    // ⚠️ A reconciliação é BARATA mas não é grátis: um pedido por página vista
    // gastava o limite de ritmo da rota a meio de uma sessão de trabalho normal,
    // e a partir daí respondia 429. Uma vez por dia por aparelho chega para
    // apanhar uma base de dados restaurada.
    const CHAVE_SINCRONIA = 'push:ultima-sincronia';
    const INTERVALO_DA_SINCRONIA = 24 * 60 * 60 * 1000;

    function precisaDeSincronizar(endpoint) {
        try {
            const guardado = JSON.parse(localStorage.getItem(CHAVE_SINCRONIA) || 'null');
            if (!guardado || guardado.endpoint !== endpoint) return true;
            return (Date.now() - guardado.quando) > INTERVALO_DA_SINCRONIA;
        } catch (e) {
            // Sem localStorage (janela anónima, dados bloqueados) sincroniza-se
            // sempre: é o comportamento correto, só menos eficiente.
            return true;
        }
    }

    function marcarSincronizado(endpoint) {
        try {
            localStorage.setItem(CHAVE_SINCRONIA, JSON.stringify({ endpoint, quando: Date.now() }));
        } catch (e) {
            /* não há onde guardar; volta a sincronizar na próxima página */
        }
    }

    async function ativar() {
        botao.disabled = true;
        rotulo.textContent = i18n.ativando;

        const permissao = await Notification.requestPermission();
        if (permissao !== 'granted') {
            desenhar(false, i18n.bloqueadas);
            return;
        }

        const registo = await registoDoServiceWorker();
        const subscricao = await registo.pushManager.subscribe({
            // Sem isto o Chrome recusa a subscrição: ele exige o compromisso de
            // que toda a mensagem entregue vai ser mostrada à pessoa.
            userVisibleOnly: true,
            applicationServerKey: chaveParaBytes(chavePublica),
        });
        await guardarNoPainel(subscricao);
        desenhar(true);
        showToast(i18n.ativadas, 'success');
    }

    async function desativar() {
        botao.disabled = true;
        const subscricao = await subscricaoAtual();
        if (subscricao) {
            // Primeiro o painel, depois o navegador: se a ordem fosse ao
            // contrário e o pedido falhasse, ficava uma linha a entregar para
            // uma subscrição que já não existe — e ninguém a podia apagar,
            // porque o endereço tinha-se perdido com ela.
            await fetchAPI(urls.unsubscribe, 'POST', { endpoint: subscricao.endpoint });
            await subscricao.unsubscribe();
        }
        desenhar(false);
    }

    botao.addEventListener('click', async () => {
        try {
            const subscricao = await subscricaoAtual();
            if (subscricao) {
                await desativar();
            } else {
                await ativar();
            }
        } catch (erro) {
            console.error('Falha ao mudar o estado das notificações push:', erro);
            desenhar(false, i18n.falhou);
            showToast(i18n.falhou, 'error');
        }
    });

    // Estado inicial, e a reconciliação silenciosa com o painel.
    (async () => {
        try {
            if (Notification.permission === 'denied') {
                desenhar(false, i18n.bloqueadas);
                botao.disabled = true;
                return;
            }
            const subscricao = await subscricaoAtual();
            desenhar(Boolean(subscricao));
            if (subscricao && precisaDeSincronizar(subscricao.endpoint)) {
                // Uma falha aqui não é da pessoa e não a deve incomodar: fica
                // no console, e o botão continua a dizer a verdade.
                guardarNoPainel(subscricao).catch((e) => console.warn('Push não reconciliado:', e));
            }
        } catch (erro) {
            console.warn('Não foi possível ler o estado das notificações push:', erro);
        }
    })();
});
