// app/static/js/settings_modules/api_keys.js

/**
 * Chaves por integração: listar, criar e revogar.
 *
 * ⚠️ A chave GRANDE das Configurações é uma só para tudo — o endpoint de
 * convites e o webhook do Seerr partilham-na. Regenerá-la porque um bot foi
 * comprometido derruba também o Seerr, e o painel não dá nenhuma forma de
 * saber qual das integrações a estava a usar. Estas são o caminho novo.
 *
 * 🛡️ **A chave criada aparece UMA vez.** O painel guarda só um resumo dela e
 * não a consegue mostrar de novo — quem lesse a base de dados, ou um ZIP de
 * backup, ficava com uma porta aberta por cada integração ligada.
 */

import { i18n, urls } from './config.js';
import { fetchAPI, showToast, escapeHTML, copyToClipboard, formatarDataHora } from '../utils.js';

function lista() {
    return document.getElementById('apiKeysList');
}

/**
 * 🐛 A caixa da chave nova tem um contentor SÓ DELA, e não é um pormenor de
 * arrumação: `carregarChavesDeApi()` reescreve o `innerHTML` do `#apiKeysList`,
 * e a criação recarrega a lista logo a seguir a mostrar a chave. Enquanto a
 * caixa era acrescentada lá dentro, a recarga apagava-a — a chave aparecia e
 * desaparecia no mesmo instante, e ela só é mostrada UMA vez.
 */
function caixaDaChaveNova() {
    return document.getElementById('chaveDeApiNova');
}

function cartao(chave) {
    const revogada = Boolean(chave.revoked_at);

    const usoHtml = chave.last_used_at
        ? `${escapeHTML(i18n.apiKeyLastUsed || 'Usada pela última vez em')} ${escapeHTML(formatarDataHora(chave.last_used_at))}`
        : escapeHTML(i18n.apiKeyNeverUsed || 'Nunca usada');

    const permissoes = (chave.escopos || [])
        .map(e => `<span class="px-2 py-0.5 text-xs rounded-full bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300">${escapeHTML(e)}</span>`)
        .join(' ');

    const botao = revogada
        ? `<span class="text-xs text-gray-400">${escapeHTML(i18n.apiKeyRevoked || 'Revogada')}</span>`
        : `<button type="button" data-revogar="${chave.id}" class="text-xs px-3 py-1 rounded-md text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/30 transition-colors">${escapeHTML(i18n.apiKeyRevoke || 'Revogar')}</button>`;

    return `
    <div class="flex items-center justify-between gap-3 p-3 rounded-lg border border-gray-200 dark:border-gray-700/60 ${revogada ? 'opacity-50' : ''}">
        <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
                <span class="font-semibold text-sm text-gray-900 dark:text-white truncate">${escapeHTML(chave.nome)}</span>
                <span class="font-mono text-xs text-gray-500">${escapeHTML(chave.prefixo)}…</span>
                ${permissoes}
            </div>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">${usoHtml}</p>
        </div>
        ${botao}
    </div>`;
}

export async function carregarChavesDeApi() {
    const alvo = lista();
    if (!alvo) return;

    try {
        const resposta = await fetchAPI(urls.apiKeys);
        const chaves = resposta.keys || [];

        alvo.innerHTML = chaves.length
            ? chaves.map(cartao).join('')
            : `<p class="text-sm text-gray-500 dark:text-gray-400 italic">${escapeHTML(i18n.apiKeysNone || 'Nenhuma chave por integração criada ainda.')}</p>`;

        alvo.querySelectorAll('[data-revogar]').forEach(botao => {
            botao.onclick = () => revogar(botao.dataset.revogar);
        });
    } catch (erro) {
        alvo.innerHTML = `<p class="text-sm text-red-500">${escapeHTML(erro.message)}</p>`;
    }
}

async function revogar(id) {
    if (!confirm(i18n.apiKeyConfirmRevoke || 'Revogar esta chave?')) return;

    try {
        const resposta = await fetchAPI(`${urls.apiKeys}/${encodeURIComponent(id)}`, 'DELETE');
        showToast(resposta.message || 'OK', 'success');
        await carregarChavesDeApi();
    } catch (erro) {
        showToast(erro.message, 'error');
    }
}

async function criar() {
    const campoNome = document.getElementById('novaChaveNome');
    const nome = (campoNome?.value || '').trim();
    if (!nome) {
        showToast(i18n.apiKeyNameRequired || 'Dê um nome à chave.', 'error');
        return;
    }

    const escopos = ['escopoConvites', 'escopoWebhooks']
        .map(id => document.getElementById(id))
        .filter(campo => campo && campo.checked)
        .map(campo => campo.value);

    if (!escopos.length) {
        showToast(i18n.apiKeyScopeRequired || 'Escolha pelo menos uma permissão.', 'error');
        return;
    }

    try {
        const resposta = await fetchAPI(urls.apiKeys, 'POST', { nome, escopos });
        if (campoNome) campoNome.value = '';

        // A lista é recarregada PRIMEIRO: ela reescreve o contentor dela e nada
        // do que corra a seguir pode ser apagado por essa reescrita.
        await carregarChavesDeApi();

        // 🛡️ A ÚNICA vez que a chave existe fora de quem a copiar. Fica no
        // ecrã até alguém a fechar, de propósito: um toast que desaparece ao
        // fim de três segundos perdia-a para sempre.
        mostrarAChaveNova(resposta.key);
    } catch (erro) {
        showToast(erro.message, 'error');
    }
}

function mostrarAChaveNova(chave) {
    const alvo = caixaDaChaveNova();
    if (!alvo) return;

    // Só a última criada fica à vista — criar outra substitui a caixa em vez
    // de empilhar chaves antigas que ninguém mais vai copiar.
    alvo.innerHTML = '';

    const caixa = document.createElement('div');
    caixa.className = 'p-3 rounded-lg border border-green-300 bg-green-50 dark:bg-green-900/20 dark:border-green-800/60 space-y-2';
    caixa.innerHTML = `
        <p class="text-xs font-semibold text-green-800 dark:text-green-400">${escapeHTML(i18n.apiKeyCreated || 'Copie a chave agora — ela não volta a ser mostrada:')}</p>
        <div class="flex gap-2">
            <input type="text" readonly class="flex-1 min-w-0 p-2 text-xs font-mono rounded border border-green-300 bg-white dark:bg-gray-900 dark:border-green-800 dark:text-white">
            <button type="button" data-copiar class="btn bg-green-600 hover:bg-green-500 text-white px-3 py-1.5 text-xs font-semibold flex-shrink-0"></button>
        </div>
        <button type="button" data-fechar class="text-xs text-green-800/70 hover:text-green-900 dark:text-green-400/70 dark:hover:text-green-300 underline"></button>`;

    // ⚠️ O valor entra pela PROPRIEDADE e não pelo HTML: a chave é gerada pelo
    // servidor, mas interpolá-la num atributo é a mesma armadilha de sempre e
    // uma aspa no meio partiria o campo em silêncio.
    const campo = caixa.querySelector('input');
    campo.value = chave;

    const botao = caixa.querySelector('[data-copiar]');
    botao.textContent = i18n.apiKeyCopy || 'Copiar';
    botao.onclick = async () => {
        const certo = await copyToClipboard(chave);
        showToast(certo ? (i18n.apiKeyCopied || 'Chave copiada!') : (i18n.errorGeneric || 'Erro'),
                  certo ? 'success' : 'error');
    };

    // Fechar é uma decisão de quem copiou, nunca do painel: enquanto ninguém
    // clicar aqui, a caixa fica onde está.
    const fechar = caixa.querySelector('[data-fechar]');
    fechar.textContent = i18n.apiKeyDismiss || 'Já copiei, pode fechar';
    fechar.onclick = () => { alvo.innerHTML = ''; };

    alvo.appendChild(caixa);
    campo.select();
}

export function initApiKeys() {
    document.getElementById('criarChaveDeApi')?.addEventListener('click', criar);
    carregarChavesDeApi();
}
