// app/static/js/settings_modules/online_media.js
//
// Cartão "Fontes de Mídia Online do Plex" (aba Conexões): lista as fontes que
// a Plex oferece dentro do aplicativo (TV ao Vivo, Filmes e Programas de TV
// gratuitos, Podcasts...) e deixa o admin escolher quais são desligadas na
// conta de quem aceita o convite.
//
// A lista vem do backend, que a lê da própria conta Plex ligada ao painel —
// por isso não há aqui nenhuma lista de chaves fixa a envelhecer.

import { i18n, urls } from './config.js';
import * as api from './api.js';

const LIST_ID = 'online-media-sources-list';

// Guarda a última lista conhecida para que gravar não perca uma fonte que o
// admin tinha marcado mas que a Plex deixou de devolver entretanto.
let lastRenderedKeys = [];

function renderMessage(text) {
    const list = document.getElementById(LIST_ID);
    if (!list) return;
    list.innerHTML = `<p class="text-xs text-gray-500 dark:text-gray-400 col-span-full">${text}</p>`;
}

/**
 * Dois estados distintos, com pesos distintos:
 *
 * - Vermelho: há fontes marcadas que a lista do Plex não tem. É o único caso
 *   acionável — essas escolhas não desativam nada e o admin precisa de as
 *   trocar pelas equivalentes.
 * - Nota discreta: o Plex não devolveu lista nenhuma, portanto nada foi
 *   confirmado. Não é erro do admin, e por isso não leva alarme vermelho.
 */
function renderAccountWarning(sources, accountRead) {
    const orphans = (Array.isArray(sources) ? sources : [])
        .filter(source => source.selected && source.available === false);

    const mismatch = document.getElementById('online-media-mismatch-warning');
    if (mismatch) {
        mismatch.hidden = orphans.length === 0;
        const keys = mismatch.querySelector('[data-role="mismatch-keys"]');
        if (keys) keys.textContent = orphans.map(source => source.key).join(', ');
    }

    const unverified = document.getElementById('online-media-unverified-note');
    if (unverified) unverified.hidden = accountRead !== false;
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

/**
 * Desenha as caixas de seleção. `sources` é [{key, label, selected}].
 */
export function renderOnlineMediaSources(sources, accountRead = true) {
    const list = document.getElementById(LIST_ID);
    if (!list) return;

    renderAccountWarning(sources, accountRead);

    if (!Array.isArray(sources) || sources.length === 0) {
        lastRenderedKeys = [];
        renderMessage(i18n.onlineMediaEmpty || 'Nenhuma fonte de mídia online disponível.');
        return;
    }

    lastRenderedKeys = sources.map(s => s.key);

    // `available: false` = a conta Plex não conhece esta chave, por isso marcá-la
    // não desativaria nada. Sinalizamos em vez de a esconder: o admin vê logo
    // qual é a escolha morta e qual é a fonte verdadeira, listada ao lado.
    const unavailableBadge = i18n.onlineMediaUnavailable || 'não existe nesta conta Plex';

    list.innerHTML = sources.map(source => `
        <label class="flex items-center gap-3 p-3 rounded-xl border ${source.available === false ? 'border-amber-300 dark:border-amber-700/60' : 'border-gray-200 dark:border-gray-700/60'} bg-white dark:bg-gray-900/30 cursor-pointer hover:border-purple-400 dark:hover:border-purple-500 transition-colors">
            <input type="checkbox" data-role="online-media-source" value="${escapeHtml(source.key)}" ${source.selected ? 'checked' : ''}
                class="w-4 h-4 rounded border-gray-300 text-purple-600 focus:ring-purple-500 dark:bg-gray-800 dark:border-gray-600 flex-shrink-0">
            <span class="min-w-0">
                <span class="block text-sm font-medium text-gray-800 dark:text-gray-200 truncate">${escapeHtml(source.label)}</span>
                <span class="block text-[11px] font-mono text-gray-400 dark:text-gray-500 truncate">${escapeHtml(source.key)}</span>
                ${source.available === false ? `<span class="inline-block mt-1 text-[10px] font-semibold text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-900/40 rounded px-1.5 py-0.5">${escapeHtml(unavailableBadge)}</span>` : ''}
            </span>
        </label>
    `).join('');
}

/**
 * Carrega as fontes do backend. Chamada ao abrir a página de configurações.
 */
export async function loadOnlineMediaSources() {
    if (!document.getElementById(LIST_ID) || !urls.onlineMediaSources) return;

    try {
        const data = await api.getOnlineMediaSources();
        renderOnlineMediaSources(data.sources || [], data.account_read !== false);
    } catch (error) {
        // Sem ligação ao Plex o cartão fica informativo em vez de vazio: o resto
        // das configurações continua perfeitamente utilizável.
        lastRenderedKeys = [];
        renderAccountWarning([], true);
        renderMessage(i18n.onlineMediaLoadFailed || 'Não foi possível carregar as fontes de mídia online do Plex.');
    }
}

/**
 * Recolhe as chaves marcadas para gravação.
 * Devolve `null` quando o cartão não chegou a carregar — nesse caso a gravação
 * deve deixar a configuração atual intacta em vez de a apagar.
 */
export function collectOnlineMediaSources() {
    if (lastRenderedKeys.length === 0) return null;

    return Array.from(document.querySelectorAll('[data-role="online-media-source"]'))
        .filter(el => el.checked)
        .map(el => el.value);
}
