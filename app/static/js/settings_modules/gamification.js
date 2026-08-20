// app/static/js/settings_modules/gamification.js
//
// Lógica exclusiva da aba "Gamificação": alternância entre as sub-abas
// (Conquistas / Sistema de XP e Níveis), o editor dinâmico de níveis de XP
// e a grade de seleção dos meses de reset periódico.

import { i18n } from './config.js';
import { setButtonLoading, restoreButton } from '../utils.js';

const MONTH_NAMES_PT = [
    'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
    'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'
];

// Nível padrão de segurança, usado apenas se o editor ficar vazio
// (nunca deixamos o admin salvar uma lista de níveis totalmente vazia).
const FALLBACK_LEVEL = { threshold: 0, name: 'Espectador Casual', icon: '🍿' };

let levelRowCounter = 0;

export function initGamificationSubtabs() {
    const nav = document.getElementById('gam-subtabs-nav');
    if (!nav) return;

    nav.addEventListener('click', (e) => {
        const btn = e.target.closest('.gam-subtab-button');
        if (!btn) return;

        nav.querySelectorAll('.gam-subtab-button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        document.querySelectorAll('.gam-subtab-content').forEach(c => c.classList.add('hidden'));
        document.getElementById(`gam-subtab-content-${btn.dataset.subtab}`)?.classList.remove('hidden');

        // 📱 Em ecrãs pequenos a barra faz scroll horizontal, por isso a aba tocada
        // pode estar apenas meio visível na borda. Trazemo-la para o centro, para
        // que fique claro qual está selecionada.
        if (typeof btn.scrollIntoView === 'function') {
            btn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        }
    });
}

// --- Editor de Níveis ---

function createLevelRow(level, index, isOnly) {
    const row = document.createElement('div');
    row.className = 'flex items-center gap-2 xp-level-row';
    row.dataset.rowId = String(levelRowCounter++);

    const isFirst = index === 0;

    row.innerHTML = `
        <span class="text-xs font-mono text-gray-400 w-6 text-center flex-shrink-0" data-role="level-number">${index + 1}</span>
        <input type="text" data-role="icon" maxlength="4" value="${level.icon || '🎬'}"
            class="w-14 text-center p-2 text-lg rounded-lg border border-gray-300 bg-white dark:bg-gray-900/50 dark:border-gray-600 flex-shrink-0">
        <input type="text" data-role="name" value="${(level.name || '').replace(/"/g, '&quot;')}" placeholder="${i18n.levelName || 'Nome do nível'}"
            class="flex-1 p-2 text-sm rounded-lg border border-gray-300 bg-white text-gray-900 dark:bg-gray-900/50 dark:border-gray-600 dark:text-white">
        <div class="flex items-center gap-1 flex-shrink-0">
            <input type="number" data-role="threshold" value="${level.threshold || 0}" min="0" ${isFirst ? 'disabled' : ''}
                title="${isFirst ? (i18n.firstLevelAlwaysZero || 'O primeiro nível começa sempre em 0 XP') : ''}"
                class="w-24 p-2 text-sm rounded-lg border border-gray-300 bg-white text-gray-900 dark:bg-gray-900/50 dark:border-gray-600 dark:text-white ${isFirst ? 'opacity-50 cursor-not-allowed' : ''}">
            <span class="text-xs text-gray-400">XP</span>
        </div>
        <button type="button" data-role="delete" ${isOnly ? 'disabled' : ''} title="${i18n.removeLevel || 'Remover nível'}"
            class="p-2 rounded-full text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors flex-shrink-0 ${isOnly ? 'opacity-30 cursor-not-allowed' : ''}">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
        </button>
    `;

    row.querySelector('[data-role="delete"]')?.addEventListener('click', () => {
        const container = document.getElementById('xp-level-editor-rows');
        if (container && container.children.length > 1) {
            row.remove();
            renumberLevelRows();
        }
    });

    return row;
}

function renumberLevelRows() {
    const container = document.getElementById('xp-level-editor-rows');
    if (!container) return;
    const rows = Array.from(container.children);
    rows.forEach((row, index) => {
        row.querySelector('[data-role="level-number"]').textContent = String(index + 1);
        const thresholdInput = row.querySelector('[data-role="threshold"]');
        const isFirst = index === 0;
        thresholdInput.disabled = isFirst;
        thresholdInput.classList.toggle('opacity-50', isFirst);
        thresholdInput.classList.toggle('cursor-not-allowed', isFirst);
        if (isFirst) thresholdInput.value = '0';
        const deleteBtn = row.querySelector('[data-role="delete"]');
        const isOnly = rows.length === 1;
        deleteBtn.disabled = isOnly;
        deleteBtn.classList.toggle('opacity-30', isOnly);
        deleteBtn.classList.toggle('cursor-not-allowed', isOnly);
    });
}

export function renderLevelEditor(levels) {
    const container = document.getElementById('xp-level-editor-rows');
    if (!container) return;
    container.innerHTML = '';

    const safeLevels = (Array.isArray(levels) && levels.length > 0) ? levels : [FALLBACK_LEVEL];
    // Garante que aparece já ordenado por XP, do mais baixo para o mais alto.
    const sorted = [...safeLevels].sort((a, b) => (a.threshold || 0) - (b.threshold || 0));

    sorted.forEach((level, index) => {
        container.appendChild(createLevelRow(level, index, sorted.length === 1));
    });
}

export function addLevelRow() {
    const container = document.getElementById('xp-level-editor-rows');
    if (!container) return;
    const rowCount = container.children.length;
    // Sugere um XP maior que o último nível, para facilitar o preenchimento.
    const lastThresholdInput = container.querySelector('.xp-level-row:last-child [data-role="threshold"]');
    const suggestedThreshold = lastThresholdInput ? (parseInt(lastThresholdInput.value, 10) || 0) + 1000 : 1000;

    container.appendChild(createLevelRow(
        { threshold: suggestedThreshold, name: '', icon: '⭐' },
        rowCount,
        false
    ));
    renumberLevelRows();
}

export function collectLevelsFromEditor() {
    const container = document.getElementById('xp-level-editor-rows');
    const msgEl = document.getElementById('xp-level-validation-msg');
    if (!container) return [FALLBACK_LEVEL];

    const rows = Array.from(container.children);
    let levels = rows.map(row => ({
        threshold: parseInt(row.querySelector('[data-role="threshold"]').value, 10) || 0,
        name: (row.querySelector('[data-role="name"]').value || '').trim() || (i18n.unnamedLevel || 'Nível sem nome'),
        icon: (row.querySelector('[data-role="icon"]').value || '⭐').trim() || '⭐'
    }));

    // Nunca deixa a lista vazia, e o primeiro nível é sempre fixado em 0 XP.
    if (levels.length === 0) levels = [FALLBACK_LEVEL];
    levels.sort((a, b) => a.threshold - b.threshold);
    levels[0].threshold = 0;

    // Remove XPs duplicados (mantém o primeiro), para não gerar níveis ambíguos.
    const seenThresholds = new Set();
    const deduped = [];
    for (const lvl of levels) {
        if (seenThresholds.has(lvl.threshold)) continue;
        seenThresholds.add(lvl.threshold);
        deduped.push(lvl);
    }

    if (msgEl) {
        if (deduped.length < levels.length) {
            msgEl.textContent = i18n.duplicateLevelXpWarning || 'Níveis com o mesmo XP mínimo foram combinados automaticamente.';
            msgEl.classList.remove('hidden');
        } else {
            msgEl.classList.add('hidden');
        }
    }

    return deduped;
}

// --- Grade de meses de reset ---

export function renderResetMonthsGrid(selectedMonths) {
    const grid = document.getElementById('xp-reset-months-grid');
    if (!grid) return;
    grid.innerHTML = '';

    const selected = new Set((selectedMonths || []).map(m => parseInt(m, 10)));

    MONTH_NAMES_PT.forEach((label, i) => {
        const monthNumber = i + 1;
        const wrapper = document.createElement('label');
        wrapper.className = 'flex items-center justify-center gap-1.5 p-2 text-xs font-semibold rounded-lg border cursor-pointer transition-colors select-none ' +
            (selected.has(monthNumber)
                ? 'bg-red-500 border-red-500 text-white'
                : 'bg-white dark:bg-gray-900/50 border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:border-red-400');
        wrapper.dataset.month = String(monthNumber);
        wrapper.innerHTML = `<input type="checkbox" class="hidden" data-role="reset-month" value="${monthNumber}" ${selected.has(monthNumber) ? 'checked' : ''}>${label}`;

        wrapper.addEventListener('click', (e) => {
            e.preventDefault();
            const checkbox = wrapper.querySelector('input');
            checkbox.checked = !checkbox.checked;
            wrapper.classList.toggle('bg-red-500', checkbox.checked);
            wrapper.classList.toggle('border-red-500', checkbox.checked);
            wrapper.classList.toggle('text-white', checkbox.checked);
            wrapper.classList.toggle('bg-white', !checkbox.checked);
            wrapper.classList.toggle('dark:bg-gray-900/50', !checkbox.checked);
            wrapper.classList.toggle('text-gray-600', !checkbox.checked);
            wrapper.classList.toggle('dark:text-gray-300', !checkbox.checked);
        });

        grid.appendChild(wrapper);
    });
}

export function collectResetMonths() {
    const grid = document.getElementById('xp-reset-months-grid');
    if (!grid) return [];
    return Array.from(grid.querySelectorAll('input[data-role="reset-month"]:checked')).map(cb => parseInt(cb.value, 10));
}

// --- Estado da temporada e reset manual ---

export async function loadSeasonStatus(urls, fetchAPI) {
    const el = document.getElementById('xp-season-status');
    if (!el || !urls.xpSeason) return;
    try {
        const result = await fetchAPI(urls.xpSeason);
        const season = result.season;
        if (!season) {
            el.textContent = i18n.seasonDisabled || 'Reset periódico desativado — o XP acumula indefinidamente.';
            return;
        }
        const template = i18n.seasonNextReset || 'Próximo reset: {date} (em {days} dias).';
        el.textContent = template.replace('{date}', season.next_reset_display).replace('{days}', season.days_remaining);
    } catch {
        el.textContent = '';
    }
}

export async function handleManualSeasonReset(urls, fetchAPI, showToast) {
    // ⚠️ Ação destrutiva: dupla confirmação, tal como no restauro de backups.
    const msg = i18n.confirmSeasonReset ||
        'ATENÇÃO: isto vai zerar o XP da temporada atual de TODOS os utilizadores e reiniciar o ranking.\n\nO XP acumulado de sempre (vitalício) será preservado.\n\nDeseja continuar?';
    if (!confirm(msg)) return;

    const word = (i18n.resetConfirmationWord || 'ZERAR').toUpperCase();
    const typed = prompt((i18n.confirmSeasonResetType || 'Para confirmar, digite "{word}":').replace('{word}', word));
    if ((typed || '').trim().toUpperCase() !== word) {
        showToast(i18n.actionCancelled || 'Ação cancelada.', 'info');
        return;
    }

    const btn = document.getElementById('xp-season-reset-btn');
    setButtonLoading(btn, i18n.resetting || 'A zerar...');

    try {
        const result = await fetchAPI(urls.xpSeasonReset, 'POST');
        showToast(result.message || (i18n.seasonResetDone || 'XP reposto com sucesso.'), 'success');
        await loadSeasonStatus(urls, fetchAPI);
    } catch (error) {
        showToast(`${i18n.errorGeneric || 'Erro'}: ${error.message}`, 'error');
    } finally {
        restoreButton(btn);
    }
}
