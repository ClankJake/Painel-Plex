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
        observers: [], // Guarda os ResizeObservers para os poder destruir
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
            doughnutColors: ['#3B82F6', '#8B5CF6'] // Azul e Roxo
        };
    };

    const formatDuration = (totalSeconds) => {
        if (totalSeconds < 60) return `${Math.round(totalSeconds)}s`;
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        return hours === 0 ? `${minutes}m` : `${hours}h ${minutes}m`;
    };

    const formatTimeAgo = (dateString) => {
        const seconds = Math.floor((new Date() - new Date(dateString)) / 1000);
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
        state.observers.push(observer); // Regista para eventual limpeza
        
        updateScrollButtons();
    };

    // ==========================================
    // RENDERIZADORES DE UI (ADMIN E USER)
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

            if (item.media_type === 'episode') {
                title = item.grandparent_title || item.title;
                if (item.parent_media_index > 0 && item.media_index > 0) {
                    subtitle = `S${String(item.parent_media_index).padStart(2, '0')} · E${String(item.media_index).padStart(2, '0')}`;
                } else {
                    subtitle = item.title;
                }
            } else if (item.media_type === 'season') {
                title = item.parent_title || item.title;
                subtitle = item.title;
            }

            return `
                <div class="flex-shrink-0 w-36 group">
                    <div class="relative group-hover:scale-105 group-hover:drop-shadow-[0_5px_15px_rgba(250,204,21,0.4)] transition-transform duration-300">
                        <img src="${item.poster_url}" alt="Poster" class="w-36 h-52 object-cover rounded-lg" onerror="this.onerror=null;this.src='https://placehold.co/144x208/1F2937/E5E7EB?text=${i18n.noArt}'">
                        <div class="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-2 rounded-b-lg">
                            <p class="text-white text-xs font-semibold truncate">${i18n.added} ${addedAgo}</p>
                        </div>
                    </div>
                    <p class="text-sm font-semibold text-gray-800 dark:text-gray-200 mt-2 truncate" title="${title}">${title}</p>
                    <p class="text-xs text-gray-500 dark:text-gray-400 truncate">${subtitle}</p>
                </div>
            `;
        }).join('');
        
        dom.newlyAddedSection.classList.remove('hidden');
        setupHorizontalScroll(dom.newlyAddedContainer, dom.scrollLeftBtn, dom.scrollRightBtn);
    };

    const renderAdminSummary = (stats) => {
        if (!dom.adminSummaryCards) return;
        const totalDuration = stats.reduce((sum, user) => sum + user.total_duration, 0);
        const totalPlays = stats.reduce((sum, user) => sum + user.plays, 0);
        const activeUsers = new Set(stats.map(user => user.username)).size;

        dom.adminSummaryCards.innerHTML = `
            ${createStatCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>', i18n.totalTimeWatched, formatDuration(totalDuration), 'bg-blue-500 text-white')}
            ${createStatCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>', i18n.totalPlays, totalPlays.toLocaleString(), 'bg-green-500 text-white')}
            ${createStatCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" /></svg>', i18n.activeUsers, activeUsers, 'bg-purple-500 text-white')}
            ${createStatCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>', i18n.periodChampion, stats.length > 0 ? stats[0].username : 'N/A', 'bg-yellow-500 text-white')}
        `;
    };

    const renderPodium = (stats) => {
        if (!dom.podiumContainer) return;
        if (stats.length === 0) {
            dom.podiumContainer.innerHTML = `<p class="text-center text-gray-500 dark:text-gray-400">${i18n.noData}</p>`;
            return;
        }
    
        const [first, second, third] = stats;
        const podiumData = [
            { user: second, rank: 2, order: 'order-1', height: '120px', gradient: 'linear-gradient(to top, #C0C0C0, #A9A9A9)', medal: '🥈' },
            { user: first,  rank: 1, order: 'order-2', height: '150px', gradient: 'linear-gradient(to top, #FFD700, #FFA500)', medal: '🥇' },
            { user: third,  rank: 3, order: 'order-3', height: '90px',  gradient: 'linear-gradient(to top, #CD7F32, #A0522D)', medal: '🥉' },
        ].filter(item => item.user && item.user.username);
    
        dom.podiumContainer.innerHTML = podiumData.map(item => `
            <div class="flex flex-col items-center transition-transform duration-300 ease-in-out hover:scale-105 w-1/3 max-w-[220px] cursor-pointer ${item.order}" data-username="${item.user.original_username}" data-plex-user-id="${item.user.user_id}">
                <img src="${item.user.thumb || 'https://placehold.co/80x80/1F2937/E5E7EB?text=?'}" onerror="this.onerror=null;this.src='https://placehold.co/80x80/1F2937/E5E7EB?text=U'" class="w-20 h-20 object-cover rounded-full border-4 border-white dark:border-gray-800 -mb-10 z-10" alt="Avatar">
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
            row.dataset.username = user.original_username;
            row.dataset.plexUserId = user.user_id;
            row.innerHTML = `
                <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-500 dark:text-gray-400">${rank}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-white">
                    <div class="flex items-center">
                        <img src="${user.thumb || 'https://placehold.co/40x40/1F2937/E5E7EB?text=?'}" onerror="this.onerror=null;this.src='https://placehold.co/40x40/1F2937/E5E7EB?text=U'" class="w-10 h-10 object-cover rounded-full mr-4" alt="Avatar">
                        <span class="font-semibold">${user.username}</span>
                    </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300 font-mono">${formatDuration(user.total_duration)}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300 font-mono">${user.plays.toLocaleString()}</td>
            `;
            dom.userListBody.appendChild(row);
        });
        
        const pageOfText = i18n.pageOf.replace('{currentPage}', state.currentPage).replace('{totalPages}', totalPages);
        dom.paginationControls.innerHTML = `
            <button id="prevPage" class="px-3 py-1 bg-gray-200 dark:bg-gray-600 rounded-md disabled:opacity-50" ${state.currentPage === 1 ? 'disabled' : ''}>${i18n.previous}</button>
            <span>${pageOfText}</span>
            <button id="nextPage" class="px-3 py-1 bg-gray-200 dark:bg-gray-600 rounded-md disabled:opacity-50" ${state.currentPage === totalPages ? 'disabled' : ''}>${i18n.next}</button>
        `;
        dom.paginationControls.querySelector('#prevPage').onclick = () => { if (state.currentPage > 1) { state.currentPage--; renderUsersTable(); }};
        dom.paginationControls.querySelector('#nextPage').onclick = () => { if (state.currentPage < totalPages) { state.currentPage++; renderUsersTable(); }};
    };

    // ==========================================
    // RENDERIZADORES DE GRÁFICOS
    // ==========================================

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
                    label: i18n.hoursWatched, 
                    data: top15Users.map(u => (u.total_duration / 3600).toFixed(2)), 
                    backgroundColor: 'rgba(251, 191, 36, 0.6)', 
                    borderColor: 'rgba(251, 191, 36, 1)', 
                    borderWidth: 1, 
                    borderRadius: 4 
                }] 
            },
            options: { 
                responsive: true, maintainAspectRatio: false, 
                plugins: { legend: { display: false }, tooltip: { backgroundColor: colors.tooltipBg, titleColor: colors.textColor, bodyColor: colors.textColor, callbacks: { label: (c) => `${i18n.duration}: ${c.parsed.y.toFixed(2)} ${i18n.hours}` } } }, 
                scales: { y: { beginAtZero: true, title: { display: true, text: i18n.hours, color: colors.textColor}, ticks: { color: colors.textColor }, grid: { color: colors.gridColor } }, x: { ticks: { color: colors.textColor }, grid: { display: false } } } 
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
                labels: [i18n.sun, i18n.mon, i18n.tue, i18n.wed, i18n.thu, i18n.fri, i18n.sat], 
                datasets: [{ 
                    label: i18n.hoursWatched, 
                    data: weeklyDataArray, 
                    backgroundColor: 'rgba(59, 130, 246, 0.6)', 
                    borderColor: 'rgba(59, 130, 246, 1)', 
                    borderWidth: 1, 
                    borderRadius: 4 
                }] 
            },
            options: { 
                responsive: true, maintainAspectRatio: false, 
                plugins: { legend: { display: false }, tooltip: { backgroundColor: colors.tooltipBg, titleColor: colors.textColor, bodyColor: colors.textColor, callbacks: { label: (c) => `${i18n.duration}: ${c.parsed.y} ${i18n.hours}` } } }, 
                scales: { y: { beginAtZero: true, title: { display: true, text: i18n.hoursWatched, color: colors.textColor }, ticks: { color: colors.textColor }, grid: { color: colors.gridColor } }, x: { ticks: { color: colors.textColor }, grid: { display: false } } } 
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
                labels: [i18n.movies, i18n.episodes],
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
    // RENDERIZADOR DE ESTATÍSTICAS PESSOAIS (COMPLETO)
    // ==========================================

    const renderUserAnalysis = async (userId, username, days, containerElement) => {
        try {
            const url = urls.userStats.replace('/0', `/${userId}`);
            const data = await fetchAPI(`${url}?days=${days}`);
            const details = data.details;
            
            const isOwnerViewing = username === currentUser.username;
            const isAdminViewing = currentUser.role === 'admin';
            
            // Renderização de Histórico Recente
            let recentHtml = `<p class="text-gray-500 dark:text-gray-400 text-center w-full">${i18n.noRecentActivity}</p>`;
            if (details.recent && details.recent.length > 0) {
                recentHtml = details.recent.map(item => `
                    <div class="text-center flex-shrink-0 w-32 group" title="${item.type === 'movie' ? item.title : `${item.series} - ${item.title}`}\n${i18n.viewedOn} ${item.play_date}">
                        <div class="relative group-hover:scale-105 group-hover:drop-shadow-[0_5px_15px_rgba(250,204,21,0.4)] transition-transform duration-300">
                            <img src="${item.poster_url}" alt="Poster" class="w-32 h-48 object-cover rounded-lg" onerror="this.onerror=null;this.src='https://placehold.co/200x300/1F2937/E5E7EB?text=${i18n.noArt}'">
                        </div>
                        <p class="text-xs text-gray-600 dark:text-gray-300 mt-2 truncate">${item.type === 'movie' ? item.title : item.series}</p>
                    </div>
                `).join('');
            }
            
            // Renderização de Conquistas
            let achievementsHtmlForContainer = '';
            if (details.achievements && details.achievements.length > 0) {
                const achievementsTitle = isOwnerViewing ? i18n.myAchievements : i18n.userAchievements.replace('{username}', `<strong>${username}</strong>`);
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

            const totalDuration = (details.total_movie_duration || 0) + (details.total_episode_duration || 0);

            // Container Base
            let chartsAndRecentHtml = '';
            if (isOwnerViewing || isAdminViewing) {
                chartsAndRecentHtml = `
                    <div class="grid grid-cols-1 lg:grid-cols-5 gap-6 pt-6 border-t border-gray-200 dark:border-gray-700">
                        <div class="lg:col-span-3">
                            <h4 class="text-xl font-semibold mb-4 text-gray-900 dark:text-gray-100 text-center">${i18n.activityByWeekday}</h4>
                            <div class="w-full h-80 p-2"><canvas id="activityBarChart"></canvas></div>
                        </div>
                        <div class="lg:col-span-2">
                            <h4 class="text-xl font-semibold mb-4 text-gray-900 dark:text-gray-100 text-center">${i18n.consumedContent}</h4>
                            <div class="w-full h-80 p-2 flex items-center justify-center"><canvas id="contentTypeChart"></canvas></div>
                        </div>
                    </div>
                    <div class="pt-6 border-t border-gray-200 dark:border-gray-700 group/container relative">
                        <div class="flex justify-between items-center mb-2">
                             <h4 class="text-xl font-semibold text-gray-900 dark:text-gray-100">${i18n.mostRecentItems}</h4>
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
                    <h4 class="text-xl font-semibold mb-4 text-gray-900 dark:text-gray-100 text-center">${i18n.consumedContent}</h4>
                    <div class="w-full h-80 p-2 flex items-center justify-center"><canvas id="contentTypeChart"></canvas></div>
                 </div>`;
            }

            containerElement.innerHTML = `
                <div class="bg-white dark:bg-gray-800/50 p-6 rounded-xl shadow-lg border border-gray-200 dark:border-gray-700 space-y-6">
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                        ${createStatCard('🎬', i18n.movies, (details.movie_count || 0).toLocaleString(), 'bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-200')}
                        ${createStatCard('📺', i18n.episodes, (details.episode_count || 0).toLocaleString(), 'bg-purple-100 dark:bg-purple-900/30 text-purple-800 dark:text-purple-200')}
                        ${createStatCard('⏱️', i18n.totalTime, formatDuration(totalDuration), 'bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-200')}
                        ${createStatCard('🎭', i18n.favoriteGenre, details.favorite_genre || i18n.notAvailable, 'bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-200')}
                    </div>
                    ${chartsAndRecentHtml}
                    ${achievementsHtmlForContainer}
                </div>
            `;

            const canvasContent = containerElement.querySelector('#contentTypeChart');
            renderUserContentTypeChart(canvasContent, [details.movie_count || 0, details.episode_count || 0]);
            
            if(isOwnerViewing || isAdminViewing) {
                const canvasActivity = containerElement.querySelector('#activityBarChart');
                const weeklyData = (details.weekly_activity_js || []).map(s => (s / 3600).toFixed(2));
                renderUserActivityChart(canvasActivity, weeklyData);

                setupHorizontalScroll(
                    containerElement.querySelector('#recent-items-container'),
                    containerElement.querySelector('#scroll-left-recent-btn'),
                    containerElement.querySelector('#scroll-right-recent-btn')
                );
            }

        } catch (error) {
            containerElement.innerHTML = `<p class="text-center text-red-500 dark:text-red-400">${i18n.userAnalysisError} ${error.message}</p>`;
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
                        <p class="mt-4">${i18n.analyzingHistory}</p>
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
                    <h3 class="text-2xl font-bold text-yellow-400 mb-4">${i18n.analysisOf} ${username}</h3>
                </div>`;
            modalBody.appendChild(analysisContainer);
            dom.userDetailsModal.querySelector('#modalCloseBtn').onclick = closeModal;
        }
    };

    const closeModal = () => {
        if (!dom.userDetailsModal) return;
        
        // Destruição segura de gráficos
        if (state.charts.activity) state.charts.activity.destroy();
        if (state.charts.contentType) state.charts.contentType.destroy();
        
        // Limpeza CORRIGIDA de Observers (Memory Leak fix)
        state.observers.forEach(obs => obs.disconnect());
        state.observers = [];
        
        dom.userDetailsModal.classList.add('hidden');
        dom.userDetailsModal.innerHTML = ''; 
    };

    // ==========================================
    // FLUXO PRINCIPAL (MAIN FETCH)
    // ==========================================

    const mainFetch = async (days) => {
        // 🛡️ CORREÇÃO DE LAYOUT: Forçar display Flex e classes de centralização Tailwind para o spinner
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
                        dom.leaderboardList.innerHTML = `<p class="text-center text-gray-500 dark:text-gray-400">${i18n.noOneWatched}</p>`;
                    } else {
                        dom.leaderboardList.innerHTML = state.allUsersData.map((user, index) => {
                            const isCurrentUser = user.original_username === currentUser.username;
                            const isPrivate = user.is_private && !isCurrentUser && !currentUser.is_admin;
                            
                            const clickableAttrs = isPrivate ? '' : `data-username="${user.original_username}" data-plex-user-id="${user.user_id}"`;
                            const cursorClass = isPrivate ? 'cursor-default' : 'cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700/50';
                            const highlightClass = isCurrentUser ? 'bg-yellow-100 dark:bg-yellow-500/20 ring-2 ring-yellow-500' : cursorClass;
                            
                            return `
                            <div class="flex items-center justify-between p-3 rounded-lg ${highlightClass}" ${clickableAttrs}>
                                <div class="flex items-center gap-3">
                                    <span class="font-bold w-8 text-gray-500 dark:text-gray-400 text-lg">${index + 1}</span>
                                    <img src="${user.thumb || 'https://placehold.co/40x40/1F2937/E5E7EB?text=?'}" onerror="this.onerror=null;this.src='https://placehold.co/40x40/1F2937/E5E7EB?text=U'" class="w-10 h-10 object-cover rounded-full" alt="Avatar">
                                    <span class="font-semibold">${user.username} ${isCurrentUser ? `(${i18n.you})` : ''}</span>
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
            // Volta a esconder garantindo que não estraga outros layouts
            dom.loadingIndicator.style.display = 'none';
        }
    };

    // ==========================================
    // EVENT LISTENERS GLOBAIS
    // ==========================================

    dom.daysFilter?.addEventListener('change', () => mainFetch(dom.daysFilter.value));

    document.body.addEventListener('click', (e) => { 
        const clickable = e.target.closest('[data-plex-user-id]'); 
        if (clickable) {
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
