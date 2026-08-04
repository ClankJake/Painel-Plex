import { fetchAPI, showToast, createModal } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    // ==========================================
    // GLOBAIS E CONFIGURAÇÃO
    // ==========================================
    const scriptTag = document.getElementById('statistics-script');
    const currentUser = JSON.parse(scriptTag.dataset.currentUser);
    
    const urls = {};
    const i18n = {};
    for (const key in scriptTag.dataset) {
        if (key.startsWith('i18n')) {
            const i18nKey = key.charAt(4).toLowerCase() + key.slice(5).replace(/-(\w)/g, (_, letter) => letter.toUpperCase());
            i18n[i18nKey] = scriptTag.dataset[key];
        } else if (key.endsWith('Url')) {
             urls[key.replace(/Url$/, '')] = scriptTag.dataset[key];
        }
    }
    
    Chart.defaults.font.family = "'Inter', sans-serif";

    // ==========================================
    // ELEMENTOS DOM ESTÁTICOS
    // ==========================================
    const dom = {
        daysFilter: document.getElementById('daysFilter'),
        loadingIndicator: document.getElementById('loadingIndicator'),
        statsContainer: document.getElementById('statsContainer'),
        errorContainer: document.getElementById('errorContainer'),
        errorMessage: document.getElementById('errorMessage'),
        newlyAddedSection: document.getElementById('newly-added-section'),
        newlyAddedContainer: document.getElementById('newly-added-container'),
        scrollLeftBtn: document.getElementById('scroll-left-btn'),
        scrollRightBtn: document.getElementById('scroll-right-btn'),
        adminSummaryCards: document.getElementById('admin-summary-cards'),
        podiumContainer: document.getElementById('podiumContainer'),
        userListBody: document.getElementById('userList'),
        paginationControls: document.getElementById('paginationControls'),
        itemsPerPageSelect: document.getElementById('itemsPerPage'),
        mainBarChartCanvas: document.getElementById('mainBarChart'),
        otherUsersSection: document.getElementById('otherUsersSection'),
        personalAnalysis: document.getElementById('personal-analysis'),
        leaderboardList: document.getElementById('leaderboard-list'),
        userDetailsModal: document.getElementById('userDetailsModal')
    };

    // ==========================================
    // ESTADO DA APLICAÇÃO
    // ==========================================
    const state = {
        allUsersData: [],
        currentPage: 1,
        observers: [],
        charts: {
            mainBar: null,
            activity: null,
            contentType: null
        }
    };

    // ==========================================
    // FORMATADORES E HELPERS
    // ==========================================
    
    const getChartColors = () => {
        const isDark = document.documentElement.classList.contains('dark');
        return {
            textColor: isDark ? '#E5E7EB' : '#1F2937',
            gridColor: isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
            tooltipBg: isDark ? '#1F2937' : '#FFFFFF',
            doughnutColors: ['#3B82F6', '#8B5CF6']
        };
    };

    const formatDuration = (totalSeconds) => {
        if (!totalSeconds || isNaN(totalSeconds) || totalSeconds < 0) return '0s';
        if (totalSeconds < 60) return `${Math.round(totalSeconds)}s`;
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        return hours === 0 ? `${minutes}m` : `${hours}h ${minutes}m`;
    };

    const formatUnixToDateTime = (timestamp) => {
        if (!timestamp) return i18n.notAvailable || 'N/A';
        let date;
        // Lida tanto com ISO strings (2025-01-01T...) como com UNIX timestamp (1704067200)
        if (typeof timestamp === 'string' && isNaN(timestamp)) {
             date = new Date(timestamp);
        } else {
             date = new Date(timestamp * 1000);
        }
        if (isNaN(date.getTime())) return 'Data Inválida';
        const pad = (n) => n.toString().padStart(2, '0');
        return `${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${date.getFullYear()} - ${pad(date.getHours())}:${pad(date.getMinutes())}`;
    };

    const formatTimeAgo = (dateStringOrUnix) => {
        if (!dateStringOrUnix) return i18n.notAvailable || 'N/A';
        let dateObj;
        if (typeof dateStringOrUnix === 'number') {
            dateObj = new Date(dateStringOrUnix * 1000);
        } else {
            dateObj = new Date(dateStringOrUnix);
        }
        const seconds = Math.floor((new Date() - dateObj) / 1000);
        const intervals = [
            { label: i18n.yearsAgo, seconds: 31536000 },
            { label: i18n.monthsAgo, seconds: 2592000 },
            { label: i18n.daysAgo, seconds: 86400 },
            { label: i18n.hoursAgo, seconds: 3600 },
            { label: i18n.minutesAgo, seconds: 60 }
        ];
        for (const interval of intervals) {
            const count = Math.floor(seconds / interval.seconds);
            if (count >= 1) return interval.label.replace('{count}', count);
        }
        return i18n.justNow;
    };

    const createStatCard = (icon, label, value, colorClass) => {
        const valueClass = value.toString().length > 12 ? 'text-xl' : 'text-2xl';
        return `
            <div class="p-4 rounded-xl flex items-center gap-4 transition-all duration-300 ${colorClass}">
                <div class="p-3 bg-white/20 rounded-lg">${icon}</div>
                <div class="min-w-0 flex-1">
                    <p class="text-sm font-medium opacity-80">${label}</p>
                    <p class="${valueClass} font-bold truncate" title="${value}">${value}</p>
                </div>
            </div>
        `;
    };

    const setupHorizontalScroll = (container, leftBtn, rightBtn) => {
        if (!container || !leftBtn || !rightBtn) return;
        
        const updateScrollButtons = () => {
            leftBtn.disabled = container.scrollLeft <= 0;
            rightBtn.disabled = container.scrollLeft + container.clientWidth >= container.scrollWidth - 2;
        };

        leftBtn.onclick = () => container.scrollBy({ left: -container.clientWidth * 0.8, behavior: 'smooth' });
        rightBtn.onclick = () => container.scrollBy({ left: container.clientWidth * 0.8, behavior: 'smooth' });

        container.addEventListener('scroll', updateScrollButtons);
        
        const observer = new ResizeObserver(updateScrollButtons);
        observer.observe(container);
        state.observers.push(observer);
        
        updateScrollButtons();
    };

    // ==========================================
    // RENDERIZADORES DE UI
    // ==========================================

    const renderNewlyAdded = (media) => {
        if (!dom.newlyAddedSection || !dom.newlyAddedContainer) return;

        if (!media || media.length === 0) {
            dom.newlyAddedSection.classList.add('hidden');
            return;
        }

        dom.newlyAddedContainer.innerHTML = media.map(item => {
            const addedAgo = formatTimeAgo(item.added_at);
            let title = item.title;
            let subtitle = item.year || '';

            let s = item.parent_media_index || item.parentIndex || item.season;
            let e = item.media_index || item.index || item.episode;
            let isEpisode = item.media_type === 'episode' || item.type === 'episode' || s !== undefined || e !== undefined;

            if (isEpisode) {
                title = item.grandparent_title || item.grandparentTitle || item.show_title || item.title;
                if (s !== undefined && e !== undefined && s !== null && e !== null) {
                    subtitle = `S${String(s).padStart(2, '0')} · E${String(e).padStart(2, '0')} - ${item.title}`;
                } else if (e !== undefined && e !== null) {
                    subtitle = `E${String(e).padStart(2, '0')} - ${item.title}`;
                } else {
                    subtitle = item.title;
                }
            } else if (item.media_type === 'season' || item.type === 'season') {
                title = item.parent_title || item.show_title || item.title;
                subtitle = item.title;
            }

            return `
                <div class="flex-shrink-0 w-36 group">
                    <div class="relative group-hover:scale-105 group-hover:drop-shadow-[0_5px_15px_rgba(250,204,21,0.4)] transition-transform duration-300">
                        <img src="${item.poster_url || item.thumb}" alt="Poster" class="w-36 h-52 object-cover rounded-lg" onerror="this.onerror=null;this.src='https://placehold.co/144x208/1F2937/E5E7EB?text=${i18n.noArt}'">
                        <div class="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-2 rounded-b-lg">
                            <p class="text-white text-xs font-semibold truncate">${i18n.added || 'Add'} ${addedAgo}</p>
                        </div>
                    </div>
                    <p class="text-sm font-semibold text-gray-800 dark:text-gray-200 mt-2 truncate" title="${title}">${title}</p>
                    <p class="text-xs text-gray-500 dark:text-gray-400 truncate" title="${subtitle}">${subtitle}</p>
                </div>
            `;
        }).join('');
        
        dom.newlyAddedSection.classList.remove('hidden');
        setupHorizontalScroll(dom.newlyAddedContainer, dom.scrollLeftBtn, dom.scrollRightBtn);
    };

    const renderAdminSummary = (stats) => {
        if (!dom.adminSummaryCards) return;
        const totalDuration = stats.reduce((sum, user) => sum + (user.total_duration || 0), 0);
        const totalPlays = stats.reduce((sum, user) => sum + (user.plays || user.total_plays || 0), 0);
        const activeUsers = new Set(stats.map(user => user.username)).size;

        dom.adminSummaryCards.innerHTML = `
            ${createStatCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>', i18n.totalTimeWatched || 'Total Assistido', formatDuration(totalDuration), 'bg-blue-500 text-white')}
            ${createStatCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>', i18n.totalPlays || 'Reproduções', totalPlays.toLocaleString(), 'bg-green-500 text-white')}
            ${createStatCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" /></svg>', i18n.activeUsers || 'Ativos', activeUsers, 'bg-purple-500 text-white')}
            ${createStatCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>', i18n.periodChampion || 'Campeão', stats.length > 0 ? stats[0].username : 'N/A', 'bg-yellow-500 text-white')}
        `;
    };

    const renderPodium = (stats) => {
        if (!dom.podiumContainer) return;
        if (stats.length === 0) {
            dom.podiumContainer.innerHTML = `<p class="text-center text-gray-500 dark:text-gray-400">${i18n.noData || 'Sem Dados'}</p>`;
            return;
        }
    
        const [first, second, third] = stats;
        const podiumData = [
            { user: second, rank: 2, order: 'order-1', height: '120px', gradient: 'linear-gradient(to top, #C0C0C0, #A9A9A9)', medal: '🥈' },
            { user: first,  rank: 1, order: 'order-2', height: '150px', gradient: 'linear-gradient(to top, #FFD700, #FFA500)', medal: '🥇' },
            { user: third,  rank: 3, order: 'order-3', height: '90px',  gradient: 'linear-gradient(to top, #CD7F32, #A0522D)', medal: '🥉' },
        ].filter(item => item.user && item.user.username);
    
        dom.podiumContainer.innerHTML = podiumData.map(item => `
            <div class="flex flex-col items-center transition-transform duration-300 ease-in-out hover:scale-105 w-1/3 max-w-[220px] cursor-pointer ${item.order}" data-username="${item.user.original_username || item.user.username}" data-plex-user-id="${item.user.user_id}">
                <img src="${item.user.thumb || 'https://placehold.co/80x80/1F2937/E5E7EB?text=U'}" onerror="this.onerror=null;this.src='https://placehold.co/80x80/1F2937/E5E7EB?text=U'" class="w-20 h-20 object-cover rounded-full border-4 border-white dark:border-gray-800 -mb-10 z-10" alt="Avatar">
                <div class="w-full rounded-t-lg flex flex-col justify-end items-center p-2 pb-4 text-white shadow-lg" style="height: ${item.height}; background: ${item.gradient};">
                    <div class="pt-10 text-center">
                        <p class="font-bold text-lg truncate">${item.medal} ${item.user.username}</p>
                        <p class="text-sm font-semibold">${formatDuration(item.user.total_duration)}</p>
                    </div>
                </div>
            </div>
        `).join('');
    };

    const renderUsersTable = () => {
        if (!dom.userListBody || !dom.paginationControls || !dom.itemsPerPageSelect) return;

        const itemsPerPage = parseInt(dom.itemsPerPageSelect.value);
        const totalPages = Math.ceil(state.allUsersData.length / itemsPerPage) || 1;
        state.currentPage = Math.max(1, Math.min(state.currentPage, totalPages));
        
        const startIndex = (state.currentPage - 1) * itemsPerPage;
        const paginatedItems = state.allUsersData.slice(startIndex, startIndex + itemsPerPage);
        
        dom.userListBody.innerHTML = '';
        paginatedItems.forEach((user, index) => {
            const rank = startIndex + index + 1;
            const row = document.createElement('tr');
            row.className = 'hover:bg-gray-100 dark:hover:bg-gray-700/50 cursor-pointer';
            row.dataset.username = user.original_username || user.username;
            row.dataset.plexUserId = user.user_id;
            row.innerHTML = `
                <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-500 dark:text-gray-400">${rank}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-white">
                    <div class="flex items-center">
                        <img src="${user.thumb || 'https://placehold.co/40x40/1F2937/E5E7EB?text=U'}" onerror="this.onerror=null;this.src='https://placehold.co/40x40/1F2937/E5E7EB?text=U'" class="w-10 h-10 object-cover rounded-full mr-4" alt="Avatar">
                        <span class="font-semibold">${user.username}</span>
                    </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300 font-mono">${formatDuration(user.total_duration)}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300 font-mono">${(user.plays || user.total_plays || 0).toLocaleString()}</td>
            `;
            dom.userListBody.appendChild(row);
        });
        
        const pageOfText = (i18n.pageOf || 'Página {currentPage} / {totalPages}').replace('{currentPage}', state.currentPage).replace('{totalPages}', totalPages);
        dom.paginationControls.innerHTML = `
            <button id="prevPage" class="px-3 py-1 bg-gray-200 dark:bg-gray-600 rounded-md disabled:opacity-50" ${state.currentPage === 1 ? 'disabled' : ''}>${i18n.previous || 'Ant'}</button>
            <span>${pageOfText}</span>
            <button id="nextPage" class="px-3 py-1 bg-gray-200 dark:bg-gray-600 rounded-md disabled:opacity-50" ${state.currentPage === totalPages ? 'disabled' : ''}>${i18n.next || 'Próx'}</button>
        `;
        dom.paginationControls.querySelector('#prevPage').onclick = () => { if (state.currentPage > 1) { state.currentPage--; renderUsersTable(); }};
        dom.paginationControls.querySelector('#nextPage').onclick = () => { if (state.currentPage < totalPages) { state.currentPage++; renderUsersTable(); }};
    };

    const renderMainChart = (stats) => {
        if (!dom.mainBarChartCanvas) return;
        if (state.charts.mainBar) state.charts.mainBar.destroy();
        
        const colors = getChartColors();
        const top15Users = stats.slice(0, 15);
        
        state.charts.mainBar = new Chart(dom.mainBarChartCanvas.getContext('2d'), {
            type: 'bar',
            data: { 
                labels: top15Users.map(u => u.username), 
                datasets: [{ 
                    label: i18n.hoursWatched || 'Horas', 
                    data: top15Users.map(u => ((u.total_duration || 0) / 3600).toFixed(2)), 
                    backgroundColor: 'rgba(251, 191, 36, 0.6)', 
                    borderColor: 'rgba(251, 191, 36, 1)', 
                    borderWidth: 1, 
                    borderRadius: 4 
                }] 
            },
            options: { 
                responsive: true, maintainAspectRatio: false, 
                plugins: { legend: { display: false }, tooltip: { backgroundColor: colors.tooltipBg, titleColor: colors.textColor, bodyColor: colors.textColor, callbacks: { label: (c) => `${i18n.duration || 'Duração'}: ${c.parsed.y.toFixed(2)} ${i18n.hours || 'horas'}` } } }, 
                scales: { y: { beginAtZero: true, title: { display: true, text: i18n.hours || 'Horas', color: colors.textColor}, ticks: { color: colors.textColor }, grid: { color: colors.gridColor } }, x: { ticks: { color: colors.textColor }, grid: { display: false } } } 
            }
        });
    };

    const renderUserActivityChart = (canvas, weeklyDataArray) => {
        if (!canvas) return;
        if (state.charts.activity) state.charts.activity.destroy();
        
        const colors = getChartColors();
        state.charts.activity = new Chart(canvas.getContext('2d'), {
            type: 'bar',
            data: { 
                labels: [i18n.sun || 'Dom', i18n.mon || 'Seg', i18n.tue || 'Ter', i18n.wed || 'Qua', i18n.thu || 'Qui', i18n.fri || 'Sex', i18n.sat || 'Sáb'], 
                datasets: [{ 
                    label: i18n.hoursWatched || 'Horas', 
                    data: weeklyDataArray, 
                    backgroundColor: 'rgba(59, 130, 246, 0.6)', 
                    borderColor: 'rgba(59, 130, 246, 1)', 
                    borderWidth: 1, 
                    borderRadius: 4 
                }] 
            },
            options: { 
                responsive: true, maintainAspectRatio: false, 
                plugins: { legend: { display: false }, tooltip: { backgroundColor: colors.tooltipBg, titleColor: colors.textColor, bodyColor: colors.textColor, callbacks: { label: (c) => `${i18n.duration || 'Duração'}: ${c.parsed.y} ${i18n.hours || 'horas'}` } } }, 
                scales: { y: { beginAtZero: true, title: { display: true, text: i18n.hoursWatched || 'Horas Assistidas', color: colors.textColor }, ticks: { color: colors.textColor }, grid: { color: colors.gridColor } }, x: { ticks: { color: colors.textColor }, grid: { display: false } } } 
            }
        });
    };

    const renderUserContentTypeChart = (canvas, contentDataArray) => {
        if (!canvas) return;
        if (state.charts.contentType) state.charts.contentType.destroy();
        
        const colors = getChartColors();
        state.charts.contentType = new Chart(canvas.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: [i18n.movies || 'Filmes', i18n.episodes || 'Séries'],
                datasets: [{ data: contentDataArray, backgroundColor: colors.doughnutColors, borderColor: colors.tooltipBg, borderWidth: 4 }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { color: colors.textColor, font: { size: 14 } } },
                    tooltip: { backgroundColor: colors.tooltipBg, titleColor: colors.textColor, bodyColor: colors.textColor, callbacks: { label: (c) => `${c.label}: ${c.parsed}` } }
                }
            }
        });
    };

    // ==========================================
    // RENDERIZADOR DE ESTATÍSTICAS PESSOAIS
    // ==========================================

    const renderUserAnalysis = async (userId, username, days, containerElement) => {
        try {
            const url = urls.userStats.replace('/0', `/${userId}`);
            const data = await fetchAPI(`${url}?days=${days}`);
            const details = data.details || data; 
            
            const isOwnerViewing = username === currentUser.username;
            const isAdminViewing = currentUser.role === 'admin';
            
            const historyList = details.recent_history || details.recent || [];
            let recentHtml = `<p class="text-gray-500 dark:text-gray-400 text-center w-full">${i18n.noRecentActivity || 'Sem Atividade'}</p>`;
            
            if (historyList.length > 0) {
                recentHtml = historyList.map(item => {
                    const showTitle = item.show_title || item.grandparent_title || item.grandparentTitle || item.series;
                    let s = item.parent_media_index || item.parentIndex || item.season;
                    let e = item.media_index || item.index || item.episode;
                    
                    const isEpisode = item.type === 'episode' || item.media_type === 'episode' || s !== undefined || e !== undefined;
                    
                    let displayTitle = item.title;
                    let displaySubtitle = "";
                    let fullTooltipTitle = item.title;

                    // Lógica robusta para inserir sempre o S01 · E01 nas séries
                    if (isEpisode) {
                        displayTitle = showTitle || item.title; 
                        
                        let seString = "";
                        if (s !== undefined && e !== undefined && s !== null && e !== null) {
                            seString = `S${String(s).padStart(2, '0')} · E${String(e).padStart(2, '0')} - `;
                        } else if (e !== undefined && e !== null) {
                            seString = `E${String(e).padStart(2, '0')} - `;
                        }
                        
                        if (showTitle) {
                            displaySubtitle = `${seString}${item.title}`;
                        } else {
                            // Caso estranho onde o Plex envia S01E01 mas esconde o nome da Série
                            displayTitle = `${seString}${item.title}`;
                        }
                        
                        fullTooltipTitle = showTitle ? `${showTitle} - ${seString}${item.title}` : `${seString}${item.title}`;
                    } else if (item.full_title) {
                        fullTooltipTitle = item.full_title;
                        displaySubtitle = item.year || "";
                    } else if (item.year) {
                        displaySubtitle = item.year;
                    }

                    const formattedDate = formatUnixToDateTime(item.date || item.last_played || item.viewed_at);

                    return `
                    <div class="text-center flex-shrink-0 w-32 group" title="${fullTooltipTitle}\n${i18n.viewedOn || 'Visto em:'} ${formattedDate}">
                        <div class="relative group-hover:scale-105 group-hover:drop-shadow-[0_5px_15px_rgba(250,204,21,0.4)] transition-transform duration-300">
                            <img src="${item.poster_url || item.thumb}" alt="Poster" class="w-32 h-48 object-cover rounded-lg" onerror="this.onerror=null;this.src='https://placehold.co/200x300/1F2937/E5E7EB?text=${i18n.noArt || 'X'}'">
                        </div>
                        <p class="text-xs font-bold text-gray-800 dark:text-gray-200 mt-2 truncate">${displayTitle}</p>
                        ${displaySubtitle ? `<p class="text-[10px] text-gray-500 dark:text-gray-400 truncate" title="${displaySubtitle}">${displaySubtitle}</p>` : ''}
                    </div>
                `}).join('');
            }
            
            let achievementsHtmlForContainer = '';
            if (details.achievements && details.achievements.length > 0) {
                const achievementsTitle = isOwnerViewing ? (i18n.myAchievements || 'Conquistas') : (i18n.userAchievements || 'Conquistas de {username}').replace('{username}', `<strong>${username}</strong>`);
                achievementsHtmlForContainer = `
                    <div class="pt-6 border-t border-gray-200 dark:border-gray-700">
                        <h4 class="text-xl font-semibold mb-4 text-gray-900 dark:text-gray-100">${achievementsTitle}</h4>
                        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
                            ${details.achievements.map(ach => `
                                <div class="achievement-badge unlocked-${ach.level}">
                                    <span class="icon">${ach.icon}</span><span class="title">${ach.title}</span>
                                    <div class="tooltip">${ach.description}</div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
            }

            let totalDuration = details.total_time || 0;
            if (totalDuration === 0 && details.watch_time_stats && details.watch_time_stats[0]) {
                 totalDuration = details.watch_time_stats[0].total_time || 0;
            }
            
            let movieCount = details.movie_count || 0;
            let episodeCount = details.episode_count || 0;
            if(movieCount === 0 && episodeCount === 0 && details.stats) {
                 details.stats.forEach(s => {
                     if (s.type === 'movie') movieCount = (s.count || s.total_plays || 0);
                     if (s.type === 'episode') episodeCount = (s.count || s.total_plays || 0);
                 });
            }

            let chartsAndRecentHtml = '';
            if (isOwnerViewing || isAdminViewing) {
                chartsAndRecentHtml = `
                    <div class="grid grid-cols-1 lg:grid-cols-5 gap-6 pt-6 border-t border-gray-200 dark:border-gray-700">
                        <div class="lg:col-span-3">
                            <h4 class="text-xl font-semibold mb-4 text-gray-900 dark:text-gray-100 text-center">${i18n.activityByWeekday || 'Por Dia'}</h4>
                            <div class="w-full h-80 p-2"><canvas id="activityBarChart"></canvas></div>
                        </div>
                        <div class="lg:col-span-2">
                            <h4 class="text-xl font-semibold mb-4 text-gray-900 dark:text-gray-100 text-center">${i18n.consumedContent || 'Tipos'}</h4>
                            <div class="w-full h-80 p-2 flex items-center justify-center"><canvas id="contentTypeChart"></canvas></div>
                        </div>
                    </div>
                    <div class="pt-6 border-t border-gray-200 dark:border-gray-700 group/container relative">
                        <div class="flex justify-between items-center mb-2">
                             <h4 class="text-xl font-semibold text-gray-900 dark:text-gray-100">${i18n.mostRecentItems || 'Vistos Recentemente'}</h4>
                             <div class="flex items-center space-x-2">
                                <button id="scroll-left-recent-btn" class="scroll-button"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" /></svg></button>
                                <button id="scroll-right-recent-btn" class="scroll-button"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" /></svg></button>
                            </div>
                        </div>
                        <div id="recent-items-container" class="flex space-x-4 overflow-x-auto py-2 horizontal-scroll scroll-smooth">${recentHtml}</div>
                    </div>
                `;
            } else {
                 chartsAndRecentHtml = `
                 <div class="pt-6 border-t border-gray-200 dark:border-gray-700">
                    <h4 class="text-xl font-semibold mb-4 text-gray-900 dark:text-gray-100 text-center">${i18n.consumedContent || 'Tipos'}</h4>
                    <div class="w-full h-80 p-2 flex items-center justify-center"><canvas id="contentTypeChart"></canvas></div>
                 </div>`;
            }

            containerElement.innerHTML = `
                <div class="bg-white dark:bg-gray-800/50 p-6 rounded-xl shadow-lg border border-gray-200 dark:border-gray-700 space-y-6">
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                        ${createStatCard('🎬', i18n.movies || 'Filmes', movieCount.toLocaleString(), 'bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-200')}
                        ${createStatCard('📺', i18n.episodes || 'Séries', episodeCount.toLocaleString(), 'bg-purple-100 dark:bg-purple-900/30 text-purple-800 dark:text-purple-200')}
                        ${createStatCard('⏱️', i18n.totalTime || 'Tempo', formatDuration(totalDuration), 'bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-200')}
                        ${createStatCard('🎭', i18n.favoriteGenre || 'Género', (details.genres && details.genres[0]) ? details.genres[0].genre : (i18n.notAvailable || '-'), 'bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-200')}
                    </div>
                    ${chartsAndRecentHtml}
                    ${achievementsHtmlForContainer}
                </div>
            `;

            const canvasContent = containerElement.querySelector('#contentTypeChart');
            renderUserContentTypeChart(canvasContent, [movieCount, episodeCount]);
            
            if(isOwnerViewing || isAdminViewing) {
                const canvasActivity = containerElement.querySelector('#activityBarChart');
                
                let weeklyData = [0,0,0,0,0,0,0];
                if (details.activity_by_day) {
                    details.activity_by_day.forEach(entry => {
                        const dayNames = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];
                        const idx = dayNames.indexOf(entry.day);
                        if (idx > -1) {
                            weeklyData[idx] = ((entry.count * 40 * 60) / 3600).toFixed(2);
                        }
                    });
                }
                renderUserActivityChart(canvasActivity, weeklyData);

                setupHorizontalScroll(
                    containerElement.querySelector('#recent-items-container'),
                    containerElement.querySelector('#scroll-left-recent-btn'),
                    containerElement.querySelector('#scroll-right-recent-btn')
                );
            }

        } catch (error) {
            containerElement.innerHTML = `<p class="text-center text-red-500 dark:text-red-400">${i18n.userAnalysisError || 'Erro:'} ${error.message}</p>`;
        }
    };

    // ==========================================
    // GESTÃO DE MODAL
    // ==========================================

    const showUserDetailsModal = async (userId, username, days) => {
        if (!dom.userDetailsModal) return;
        
        dom.userDetailsModal.innerHTML = `
            <div class="modal-content !w-full !max-w-4xl transform transition-all">
                <div id="modalBody" class="modal-body dark:bg-gray-800 bg-white p-4 sm:p-6 rounded-lg">
                    <div class="text-center py-20 flex flex-col items-center justify-center">
                        <svg class="animate-spin h-8 w-8 text-yellow-500 mx-auto" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                        <p class="mt-4">${i18n.analyzingHistory || 'A Processar...'}</p>
                    </div>
                </div>
            </div>`;
        dom.userDetailsModal.classList.remove('hidden');
        
        const analysisContainer = document.createElement('div');
        await renderUserAnalysis(userId, username, days, analysisContainer);
        
        const modalBody = dom.userDetailsModal.querySelector('#modalBody');
        if(modalBody) {
            modalBody.innerHTML = `
                <div class="relative">
                    <button id="modalCloseBtn" class="absolute top-4 right-4 text-gray-400 hover:text-white text-4xl leading-none z-10">&times;</button>
                    <h3 class="text-2xl font-bold text-yellow-400 mb-4">${i18n.analysisOf || 'Estatísticas:'} ${username}</h3>
                </div>`;
            modalBody.appendChild(analysisContainer);
            dom.userDetailsModal.querySelector('#modalCloseBtn').onclick = closeModal;
        }
    };

    const closeModal = () => {
        if (!dom.userDetailsModal) return;
        if (state.charts.activity) state.charts.activity.destroy();
        if (state.charts.contentType) state.charts.contentType.destroy();
        
        state.observers.forEach(obs => obs.disconnect());
        state.observers = [];
        
        dom.userDetailsModal.classList.add('hidden');
        dom.userDetailsModal.innerHTML = ''; 
    };

    // ==========================================
    // FLUXO PRINCIPAL (MAIN FETCH)
    // ==========================================

    const mainFetch = async (days) => {
        dom.loadingIndicator.style.display = 'flex';
        dom.loadingIndicator.classList.add('justify-center', 'items-center', 'flex-col', 'w-full', 'py-12');
        
        dom.statsContainer.classList.add('hidden');
        dom.errorContainer.classList.add('hidden');
        
        try {
            const dataPromise = fetchAPI(`${urls.stats}?days=${days}`);

            if (currentUser.role !== 'admin') {
                const newlyAddedPromise = fetchAPI(`${urls.recentlyAdded}?days=${days}`);
                const [data, newlyAddedData] = await Promise.all([dataPromise, newlyAddedPromise]);
                state.allUsersData = data.stats;
                if (newlyAddedData.success) renderNewlyAdded(newlyAddedData.media);
            } else {
                const data = await dataPromise;
                state.allUsersData = data.stats;
            }

            if (currentUser.role === 'admin') {
                renderAdminSummary(state.allUsersData);
                renderPodium(state.allUsersData);
                
                if (state.allUsersData.length > 0) {
                    dom.otherUsersSection.classList.remove('hidden');
                    renderUsersTable();
                } else {
                    dom.otherUsersSection.classList.add('hidden');
                }
                renderMainChart(state.allUsersData);
            } else {
                await renderUserAnalysis(currentUser.id, currentUser.username, days, dom.personalAnalysis);

                if (dom.leaderboardList) {
                    if (state.allUsersData.length === 0) {
                        dom.leaderboardList.innerHTML = `<p class="text-center text-gray-500 dark:text-gray-400">${i18n.noOneWatched || 'Sem Visualizações'}</p>`;
                    } else {
                        dom.leaderboardList.innerHTML = state.allUsersData.map((user, index) => {
                            const isCurrentUser = (user.original_username || user.username) === currentUser.username;
                            const isPrivate = user.is_private && !isCurrentUser && !currentUser.is_admin;
                            
                            const clickableAttrs = isPrivate ? '' : `data-username="${user.original_username || user.username}" data-plex-user-id="${user.user_id}"`;
                            const cursorClass = isPrivate ? 'cursor-default' : 'cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700/50';
                            const highlightClass = isCurrentUser ? 'bg-yellow-100 dark:bg-yellow-500/20 ring-2 ring-yellow-500' : cursorClass;
                            
                            return `
                            <div class="flex items-center justify-between p-3 rounded-lg ${highlightClass}" ${clickableAttrs}>
                                <div class="flex items-center gap-3">
                                    <span class="font-bold w-8 text-gray-500 dark:text-gray-400 text-lg">${index + 1}</span>
                                    <img src="${user.thumb || 'https://placehold.co/40x40/1F2937/E5E7EB?text=U'}" onerror="this.onerror=null;this.src='https://placehold.co/40x40/1F2937/E5E7EB?text=U'" class="w-10 h-10 object-cover rounded-full" alt="Avatar">
                                    <span class="font-semibold">${user.username} ${isCurrentUser ? `(${i18n.you || 'Tu'})` : ''}</span>
                                </div>
                                <span class="font-mono text-sm">${formatDuration(user.total_duration)}</span>
                            </div>`;
                        }).join('');
                    }
                }
            }
            dom.statsContainer.classList.remove('hidden');
        } catch (error) {
            dom.errorMessage.textContent = error.message;
            dom.errorContainer.classList.remove('hidden');
            dom.statsContainer.classList.add('hidden');
        } finally {
            dom.loadingIndicator.style.display = 'none';
        }
    };

    // ==========================================
    // EVENT LISTENERS GLOBAIS
    // ==========================================

    dom.daysFilter?.addEventListener('change', () => mainFetch(dom.daysFilter.value));

    document.body.addEventListener('click', (e) => { 
        const clickable = e.target.closest('[data-plex-user-id]'); 
        // Impede que clique em nomes privados abra o Modal
        if (clickable && clickable.dataset.username !== 'U*****O') {
            showUserDetailsModal(clickable.dataset.plexUserId, clickable.dataset.username, dom.daysFilter.value); 
        } 
    });
    
    dom.userDetailsModal?.addEventListener('click', e => { 
        if (e.target.id === 'userDetailsModal') closeModal(); 
    });

    dom.itemsPerPageSelect?.addEventListener('change', () => { 
        state.currentPage = 1; 
        renderUsersTable(); 
    });
    
    window.addEventListener('themeChanged', () => {
       if(dom.statsContainer.classList.contains('hidden')) return;
       
       const colors = getChartColors();
       Object.values(state.charts).forEach(chart => {
           if (chart) {
               if(chart.options.scales && chart.options.scales.x) {
                   chart.options.scales.x.ticks.color = colors.textColor;
                   chart.options.scales.y.ticks.color = colors.textColor;
                   chart.options.scales.y.grid.color = colors.gridColor;
                   chart.options.scales.y.title.color = colors.textColor;
               }
               if(chart.options.plugins && chart.options.plugins.tooltip) {
                   chart.options.plugins.tooltip.backgroundColor = colors.tooltipBg;
                   chart.options.plugins.tooltip.titleColor = colors.textColor;
                   chart.options.plugins.tooltip.bodyColor = colors.textColor;
               }
               if(chart.options.plugins && chart.options.plugins.legend && chart.options.plugins.legend.labels) {
                    chart.options.plugins.legend.labels.color = colors.textColor;
               }
               chart.update(); 
           }
       });
    });

    if (dom.daysFilter) mainFetch(dom.daysFilter.value);
});
