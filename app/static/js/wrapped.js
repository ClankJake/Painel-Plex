// app/static/js/wrapped.js
//
// Experiência "Plex Wrapped": retrospectiva anual em modo história (slides tipo
// Stories), com auto-avanço, navegação por clique/teclado/gestos e um cartão
// final partilhável gerado em canvas (download PNG, sem depender de nada externo).

import { fetchAPI, showToast } from './utils.js';

const scriptTag = document.getElementById('wrapped-script');
const i18n = {};
let wrappedUrl = '';
let currentYear = new Date().getFullYear();
let shareTextTemplate = '';

if (scriptTag) {
    for (const key in scriptTag.dataset) {
        if (key.startsWith('i18n')) {
            const k = key.charAt(4).toLowerCase() + key.slice(5).replace(/-(\w)/g, (_, l) => l.toUpperCase());
            i18n[k] = scriptTag.dataset[key];
        }
    }
    wrappedUrl = scriptTag.dataset.wrappedUrl || '';
    currentYear = parseInt(scriptTag.dataset.currentYear, 10) || currentYear;
    shareTextTemplate = scriptTag.dataset.shareTextTemplate || '';
}

const SLIDE_DURATION_MS = 6000;

const state = {
    data: null,
    year: currentYear,
    slides: [],
    index: 0,
    timerId: null,
    progressRafId: null,
    slideStartedAt: 0,
    paused: false,
};

// Paletas por slide — cada ecrã tem a sua própria identidade visual.
const GRADIENTS = [
    'linear-gradient(160deg, #6d28d9 0%, #db2777 100%)',
    'linear-gradient(160deg, #0f766e 0%, #0ea5e9 100%)',
    'linear-gradient(160deg, #b45309 0%, #f59e0b 100%)',
    'linear-gradient(160deg, #1e3a8a 0%, #6366f1 100%)',
    'linear-gradient(160deg, #7f1d1d 0%, #f43f5e 100%)',
    'linear-gradient(160deg, #14532d 0%, #22c55e 100%)',
    'linear-gradient(160deg, #581c87 0%, #a855f7 100%)',
    'linear-gradient(160deg, #0c4a6e 0%, #38bdf8 100%)',
];

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

const fmt = (n) => Number(n || 0).toLocaleString();

// ---------------------------------------------------------------------------
// CONSTRUÇÃO DOS SLIDES
// ---------------------------------------------------------------------------

function buildSlides(w, year) {
    const slides = [];

    // 1. Abertura
    slides.push(`
        <p class="text-white/70 text-lg wrapped-animate-1">${esc(w.username)}</p>
        <h1 class="text-white wrapped-big-number wrapped-animate-2">${year}</h1>
        <p class="text-white/90 text-2xl font-bold wrapped-animate-3">${esc((i18n.yourYear || 'O seu {year} no Plex').replace('{year}', year))}</p>
    `);

    // 2. Horas totais
    slides.push(`
        <p class="text-white/80 text-xl font-semibold wrapped-animate-1">${esc(i18n.hoursTitle || 'Você maratonou')}</p>
        <h2 class="text-white wrapped-big-number wrapped-animate-2">${fmt(w.total_hours)}</h2>
        <p class="text-white/90 text-2xl font-bold wrapped-animate-2">${esc(i18n.hoursWatched || 'horas assistidas')}</p>
        <p class="text-white/60 text-sm mt-4 wrapped-animate-3">${fmt(w.total_plays)} ${esc(i18n.plays || 'reproduções')} · ${fmt(w.unique_days_active)} ${esc(i18n.daysActive || 'dias ativos')}</p>
    `);

    // 3. Filmes vs episódios
    slides.push(`
        <p class="text-white/80 text-xl font-semibold mb-6 wrapped-animate-1">${esc(i18n.moviesEpisodesTitle || 'O que você assistiu')}</p>
        <div class="flex items-center gap-10 wrapped-animate-2">
            <div>
                <p class="text-6xl mb-1">🎬</p>
                <p class="text-white text-4xl font-black">${fmt(w.movie_count)}</p>
                <p class="text-white/70 text-sm">${esc(i18n.movies || 'filmes')}</p>
            </div>
            <div>
                <p class="text-6xl mb-1">📺</p>
                <p class="text-white text-4xl font-black">${fmt(w.episode_count)}</p>
                <p class="text-white/70 text-sm">${esc(i18n.episodes || 'episódios')}</p>
            </div>
        </div>
    `);

    // 4. Género favorito
    if (w.top_genre) {
        slides.push(`
            <p class="text-white/80 text-xl font-semibold wrapped-animate-1">${esc(i18n.genreTitle || 'Seu gênero favorito foi')}</p>
            <h2 class="text-white text-5xl sm:text-6xl font-black my-4 wrapped-animate-2">${esc(w.top_genre)}</h2>
            <p class="text-white/60 text-sm wrapped-animate-3">${fmt(w.unique_genres_count)} ${esc(i18n.genresExplored || 'gêneros explorados no total')}</p>
        `);
    }

    // 5. Top filme / série / realizador
    const tops = [
        w.top_movie ? { icon: '🍿', label: i18n.movieTitle || 'Filme mais assistido', value: w.top_movie } : null,
        w.top_show ? { icon: '📺', label: i18n.showTitle || 'Série mais maratonada', value: w.top_show } : null,
        w.top_director ? { icon: '🎥', label: i18n.directorTitle || 'Realizador mais visto', value: w.top_director } : null,
    ].filter(Boolean);

    if (tops.length) {
        slides.push(`
            <p class="text-white/80 text-xl font-semibold mb-6 wrapped-animate-1">${esc(i18n.yourFavourites || 'Os seus destaques')}</p>
            <div class="space-y-5 w-full max-w-md">
                ${tops.map((t, i) => `
                    <div class="wrapped-animate-${Math.min(3, i + 1)}">
                        <p class="text-white/60 text-xs uppercase tracking-wider mb-1">${t.icon} ${esc(t.label)}</p>
                        <p class="text-white text-2xl font-bold leading-tight">${esc(t.value)}</p>
                    </div>
                `).join('')}
            </div>
        `);
    }

    // 6. Dia mais épico
    if (w.busiest_day) {
        slides.push(`
            <p class="text-white/80 text-xl font-semibold wrapped-animate-1">${esc(i18n.busiestDayTitle || 'O seu dia mais épico')}</p>
            <h2 class="text-white wrapped-big-number my-3 wrapped-animate-2">${fmt(w.busiest_day.hours)}h</h2>
            <p class="text-white/90 text-xl wrapped-animate-3">${esc(i18n.busiestDaySubtitle || 'foi em')} <strong>${esc(w.busiest_day.date)}</strong></p>
            ${w.night_owl_sessions > 0 ? `<p class="text-white/60 text-sm mt-6 wrapped-animate-3">🦉 ${fmt(w.night_owl_sessions)} ${esc(i18n.nightOwlSessions || 'sessões de madrugada')}</p>` : ''}
        `);
    }

    // 7. Gráfico mensal
    const maxMonth = Math.max(...(w.monthly_hours || [0]), 1);
    const monthLabels = (i18n.monthsShort || 'J,F,M,A,M,J,J,A,S,O,N,D').split(',');
    slides.push(`
        <p class="text-white/80 text-xl font-semibold mb-8 wrapped-animate-1">${esc(i18n.monthlyTitle || 'O seu ano, mês a mês')}</p>
        <div class="flex items-end justify-center gap-2 h-48 wrapped-animate-2">
            ${(w.monthly_hours || []).map((h, i) => `
                <div class="flex flex-col items-center gap-2">
                    <div class="wrapped-bar" style="height: ${Math.max(4, (h / maxMonth) * 170)}px" title="${fmt(h)}h"></div>
                    <span class="text-white/50 text-[10px]">${esc(monthLabels[i] || '')}</span>
                </div>
            `).join('')}
        </div>
    `);

    // 8. XP e nível (integração com o sistema de gamificação)
    if (w.level_info) {
        const lvl = w.level_info;
        slides.push(`
            <p class="text-white/80 text-xl font-semibold wrapped-animate-1">${esc(i18n.xpTitle || 'A sua evolução')}</p>
            <div class="text-7xl my-4 wrapped-animate-2">${esc(lvl.level_icon)}</div>
            <p class="text-white text-3xl font-black wrapped-animate-2">${esc(lvl.level_name)}</p>
            <p class="text-white/70 text-sm mb-6 wrapped-animate-2">${esc(i18n.level || 'Nível')} ${lvl.level_number}${lvl.total_levels ? ` / ${lvl.total_levels}` : ''}</p>
            <div class="w-full max-w-xs space-y-2 wrapped-animate-3">
                ${w.year_xp != null ? `
                <div class="flex justify-between text-sm">
                    <span class="text-white/70">${esc((i18n.xpEarnedInYear || 'XP ganho em {year}').replace('{year}', state.year))}</span>
                    <span class="text-white font-bold">+${fmt(w.year_xp)}</span>
                </div>` : ''}
                ${w.lifetime_xp ? `
                <div class="flex justify-between text-sm">
                    <span class="text-white/70">${esc(i18n.lifetimeXp || 'XP acumulado de sempre')}</span>
                    <span class="text-white font-bold">${fmt(w.lifetime_xp)}</span>
                </div>` : ''}
            </div>
        `);
    }

    // 9. Personalidade + partilha (slide final)
    const p = w.personality || {};
    slides.push(`
        <p class="text-white/80 text-xl font-semibold wrapped-animate-1">${esc(i18n.personalityTitle || 'A sua personalidade do ano')}</p>
        <div class="text-7xl my-4 wrapped-animate-2">${esc(p.icon || '🎭')}</div>
        <h2 class="text-white text-4xl font-black mb-3 wrapped-animate-2">${esc(p.title || '')}</h2>
        <p class="text-white/80 max-w-md wrapped-animate-3">${esc(p.description || '')}</p>
        <div class="flex flex-col sm:flex-row gap-3 mt-10 wrapped-animate-3">
            <button id="wrapped-share-img-btn" class="btn bg-white text-gray-900 hover:bg-white/90 font-bold px-6 py-3">
                📸 ${esc(i18n.saveImage || 'Guardar Imagem')}
            </button>
            <button id="wrapped-share-btn" class="btn bg-white/20 hover:bg-white/30 text-white border border-white/40 px-6 py-3">
                🔗 ${esc(i18n.share || 'Compartilhar')}
            </button>
            <button id="wrapped-replay-btn" class="btn bg-transparent hover:bg-white/10 text-white/80 border border-white/30 px-6 py-3">
                ↺ ${esc(i18n.watchAgain || 'Ver Novamente')}
            </button>
        </div>
    `);

    return slides;
}

// ---------------------------------------------------------------------------
// MOTOR DE SLIDES
// ---------------------------------------------------------------------------

function renderSlides() {
    const container = document.getElementById('wrapped-slides-container');
    const progress = document.getElementById('wrapped-progress-container');
    if (!container || !progress) return;

    container.innerHTML = state.slides.map((html, i) => `
        <section class="wrapped-slide" data-slide="${i}" style="background: ${GRADIENTS[i % GRADIENTS.length]}">
            ${html}
        </section>
    `).join('');

    progress.innerHTML = state.slides.map((_, i) => `
        <div class="wrapped-progress-track"><div class="wrapped-progress-fill" data-fill="${i}"></div></div>
    `).join('');
}

function stopTimers() {
    if (state.timerId) { clearTimeout(state.timerId); state.timerId = null; }
    if (state.progressRafId) { cancelAnimationFrame(state.progressRafId); state.progressRafId = null; }
}

function animateProgress() {
    const fill = document.querySelector(`[data-fill="${state.index}"]`);
    if (!fill) return;

    const step = () => {
        if (state.paused) { state.progressRafId = requestAnimationFrame(step); return; }
        const elapsed = performance.now() - state.slideStartedAt;
        const pct = Math.min(100, (elapsed / SLIDE_DURATION_MS) * 100);
        fill.style.width = `${pct}%`;
        if (pct < 100) state.progressRafId = requestAnimationFrame(step);
    };
    state.progressRafId = requestAnimationFrame(step);
}

function goToSlide(index) {
    if (index < 0) return;
    if (index >= state.slides.length) return; // fica no último

    stopTimers();
    state.index = index;

    document.querySelectorAll('.wrapped-slide').forEach((el, i) => {
        el.classList.toggle('active', i === index);
        // Reinicia as animações de entrada do slide ativo
        if (i === index) {
            el.querySelectorAll('[class*="wrapped-animate-"]').forEach(node => {
                node.style.animation = 'none';
                void node.offsetWidth;
                node.style.animation = '';
            });
        }
    });

    document.querySelectorAll('.wrapped-progress-fill').forEach((el, i) => {
        el.classList.toggle('filled', i < index);
        if (i > index) el.style.width = '0%';
        if (i < index) el.style.width = '100%';
        if (i === index) el.style.width = '0%';
    });

    const isLast = index === state.slides.length - 1;
    if (isLast) {
        bindFinalSlideButtons();
        const fill = document.querySelector(`[data-fill="${index}"]`);
        if (fill) fill.style.width = '100%';
        return; // o último slide não avança sozinho
    }

    state.slideStartedAt = performance.now();
    animateProgress();
    state.timerId = setTimeout(() => goToSlide(state.index + 1), SLIDE_DURATION_MS);
}

function openOverlay() {
    document.getElementById('wrapped-intro')?.classList.add('hidden');
    const overlay = document.getElementById('wrapped-overlay');
    overlay?.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    goToSlide(0);
}

function closeOverlay() {
    stopTimers();
    document.getElementById('wrapped-overlay')?.classList.add('hidden');
    document.getElementById('wrapped-intro')?.classList.remove('hidden');
    document.body.style.overflow = '';
}

// ---------------------------------------------------------------------------
// CARTÃO PARTILHÁVEL (canvas -> PNG)
// ---------------------------------------------------------------------------

function buildShareText() {
    const w = state.data;
    if (!shareTextTemplate || !w) return '';
    return shareTextTemplate
        .replace('{year}', state.year)
        .replace('{hours}', fmt(w.total_hours))
        .replace('{movies}', fmt(w.movie_count))
        .replace('{episodes}', fmt(w.episode_count))
        .replace('{personality}', w.personality?.title || '')
        .replace('{icon}', w.personality?.icon || '');
}

function downloadShareCard() {
    const w = state.data;
    if (!w) return;

    // 1080x1350: proporção 4:5, o formato mais alto permitido na maioria das redes.
    const canvas = document.createElement('canvas');
    canvas.width = 1080;
    canvas.height = 1350;
    const ctx = canvas.getContext('2d');

    const grad = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
    grad.addColorStop(0, '#6d28d9');
    grad.addColorStop(1, '#db2777');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.textAlign = 'center';
    const F = (size, weight = '700') => `${weight} ${size}px ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`;

    ctx.fillStyle = 'rgba(255,255,255,0.75)';
    ctx.font = F(38, '600');
    ctx.fillText(w.username || '', canvas.width / 2, 130);

    ctx.fillStyle = '#ffffff';
    ctx.font = F(150, '900');
    ctx.fillText(String(state.year), canvas.width / 2, 280);

    ctx.font = F(34, '600');
    ctx.fillStyle = 'rgba(255,255,255,0.85)';
    ctx.fillText((i18n.summaryTitle || 'O meu resumo no Plex'), canvas.width / 2, 340);

    // Blocos de estatísticas
    const stats = [
        [fmt(w.total_hours), i18n.hoursWatched || 'horas assistidas'],
        [fmt(w.movie_count), i18n.movies || 'filmes'],
        [fmt(w.episode_count), i18n.episodes || 'episódios'],
        [fmt(w.unique_days_active), i18n.daysActive || 'dias ativos'],
    ];
    let y = 470;
    stats.forEach(([value, label], i) => {
        const x = i % 2 === 0 ? canvas.width / 4 : (canvas.width / 4) * 3;
        if (i === 2) y += 220;
        ctx.fillStyle = '#ffffff';
        ctx.font = F(88, '900');
        ctx.fillText(value, x, y);
        ctx.fillStyle = 'rgba(255,255,255,0.7)';
        ctx.font = F(30, '500');
        ctx.fillText(label, x, y + 48);
    });

    // Destaques
    y = 960;
    const highlights = [
        w.top_genre ? [(i18n.genreTitle || 'Gênero favorito'), w.top_genre] : null,
        w.top_show ? [(i18n.showTitle || 'Série mais maratonada'), w.top_show] : null,
        w.top_movie ? [(i18n.movieTitle || 'Filme mais assistido'), w.top_movie] : null,
    ].filter(Boolean).slice(0, 2);

    highlights.forEach(([label, value]) => {
        ctx.fillStyle = 'rgba(255,255,255,0.6)';
        ctx.font = F(26, '500');
        ctx.fillText(label, canvas.width / 2, y);
        ctx.fillStyle = '#ffffff';
        ctx.font = F(46, '800');
        // Corta títulos muito longos para não estourarem a imagem
        let text = String(value);
        while (ctx.measureText(text).width > canvas.width - 120 && text.length > 4) {
            text = text.slice(0, -2);
        }
        if (text !== String(value)) text += '…';
        ctx.fillText(text, canvas.width / 2, y + 52);
        y += 130;
    });

    // Personalidade
    if (w.personality) {
        ctx.font = F(90, '400');
        ctx.fillText(w.personality.icon || '🎭', canvas.width / 2, 1230);
        ctx.fillStyle = '#ffffff';
        ctx.font = F(44, '900');
        ctx.fillText(w.personality.title || '', canvas.width / 2, 1300);
    }

    canvas.toBlob((blob) => {
        if (!blob) return;
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `plex-wrapped-${state.year}.png`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        showToast(i18n.imageSaved || 'Imagem guardada!', 'success');
    }, 'image/png');
}

function bindFinalSlideButtons() {
    document.getElementById('wrapped-replay-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        goToSlide(0);
    }, { once: true });

    document.getElementById('wrapped-share-img-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        downloadShareCard();
    }, { once: true });

    document.getElementById('wrapped-share-btn')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        const text = buildShareText();
        // Usa a partilha nativa do dispositivo quando disponível (mobile),
        // com fallback para copiar o texto para a área de transferência.
        if (navigator.share) {
            try {
                await navigator.share({ title: `Plex Wrapped ${state.year}`, text });
                return;
            } catch { /* utilizador cancelou — cai no fallback */ }
        }
        try {
            await navigator.clipboard.writeText(text);
            showToast(i18n.shareCopied || 'Resumo copiado!', 'success');
        } catch {
            showToast(text, 'info');
        }
    }, { once: true });
}

// ---------------------------------------------------------------------------
// CARREGAMENTO E ARRANQUE
// ---------------------------------------------------------------------------

async function startWrapped() {
    const yearSelect = document.getElementById('wrapped-year-select');
    const loading = document.getElementById('wrapped-loading');
    const noData = document.getElementById('wrapped-no-data');
    const startBtn = document.getElementById('wrapped-start-btn');

    state.year = parseInt(yearSelect?.value, 10) || currentYear;

    noData?.classList.add('hidden');
    loading?.classList.remove('hidden');
    if (startBtn) startBtn.disabled = true;

    try {
        const result = await fetchAPI(`${wrappedUrl}?year=${state.year}`);
        if (!result.has_data || !result.wrapped) {
            noData?.classList.remove('hidden');
            return;
        }
        state.data = result.wrapped;
        state.slides = buildSlides(result.wrapped, state.year);
        renderSlides();
        openOverlay();
    } catch (error) {
        showToast(`${i18n.loadingFailed || 'Falha ao carregar a sua retrospectiva.'} ${error.message}`, 'error');
    } finally {
        loading?.classList.add('hidden');
        if (startBtn) startBtn.disabled = false;
    }
}

function initYearSelect() {
    const select = document.getElementById('wrapped-year-select');
    if (!select) return;
    // Oferece o ano atual e os 3 anteriores.
    for (let y = currentYear; y >= currentYear - 3; y--) {
        const opt = document.createElement('option');
        opt.value = String(y);
        opt.textContent = String(y);
        select.appendChild(opt);
    }
}

function initNavigation() {
    document.getElementById('wrapped-nav-next')?.addEventListener('click', () => goToSlide(state.index + 1));
    document.getElementById('wrapped-nav-prev')?.addEventListener('click', () => goToSlide(state.index - 1));
    document.getElementById('wrapped-close-btn')?.addEventListener('click', closeOverlay);

    // Teclado: setas para navegar, espaço para pausar, Esc para sair.
    document.addEventListener('keydown', (e) => {
        const overlay = document.getElementById('wrapped-overlay');
        if (!overlay || overlay.classList.contains('hidden')) return;

        if (e.key === 'ArrowRight') goToSlide(state.index + 1);
        else if (e.key === 'ArrowLeft') goToSlide(state.index - 1);
        else if (e.key === 'Escape') closeOverlay();
        else if (e.key === ' ') {
            e.preventDefault();
            state.paused = !state.paused;
            if (state.paused) {
                if (state.timerId) { clearTimeout(state.timerId); state.timerId = null; }
            } else {
                // Retoma o tempo restante do slide atual
                const elapsed = performance.now() - state.slideStartedAt;
                const remaining = Math.max(0, SLIDE_DURATION_MS - elapsed);
                state.timerId = setTimeout(() => goToSlide(state.index + 1), remaining);
            }
        }
    });

    // Gestos de swipe em ecrãs táteis
    let touchStartX = 0;
    const overlay = document.getElementById('wrapped-overlay');
    overlay?.addEventListener('touchstart', (e) => { touchStartX = e.changedTouches[0].screenX; }, { passive: true });
    overlay?.addEventListener('touchend', (e) => {
        const delta = e.changedTouches[0].screenX - touchStartX;
        if (Math.abs(delta) < 50) return;
        goToSlide(delta < 0 ? state.index + 1 : state.index - 1);
    }, { passive: true });
}

document.addEventListener('DOMContentLoaded', () => {
    initYearSelect();
    initNavigation();
    document.getElementById('wrapped-start-btn')?.addEventListener('click', startWrapped);
});
