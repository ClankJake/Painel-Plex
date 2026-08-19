/**
 * Módulo de Configuração
 * Extrai e exporta URLs e textos de internacionalização (i18n)
 * do script tag no HTML para serem usados em toda a aplicação da página de configurações.
 */

const scriptTag = document.getElementById('settings-script');

export const urls = {};
export const i18n = {};
export const fieldMap = {
    'APP_TITLE': { type: 'text', default: 'Painel Plex' },
    'APP_BASE_URL': { type: 'text', default: 'http://127.0.0.1:5000' },
    'ENABLE_LINK_SHORTENER': { type: 'checkbox', default: true },
    'DAYS_TO_REMOVE_BLOCKED_USER': { type: 'number', default: 0 },
    'EXPIRATION_NOTIFICATION_TIME': { type: 'text', default: '09:00' },
    'BLOCK_REMOVAL_TIME': { type: 'text', default: '02:00' },
    'UNIVERSAL_EXPIRATION_ENABLED': { type: 'checkbox', default: false },
    'UNIVERSAL_EXPIRATION_TIME': { type: 'text', default: '23:59' },
    'CLEANUP_PENDING_PAYMENTS_ENABLED': { type: 'checkbox', default: true },
    'CLEANUP_PENDING_PAYMENTS_DAYS': { type: 'number', default: 3 },
    'CLEANUP_TIME': { type: 'text', default: '03:00' },
    'IMAGE_CACHE_CLEANUP_ENABLED': { type: 'checkbox', default: true },
    'IMAGE_CACHE_MAX_AGE_DAYS': { type: 'number', default: 30 },
    'IMAGE_CACHE_CLEANUP_TIME': { type: 'text', default: '04:00' },
    'SHORT_LINK_CLEANUP_ENABLED': { type: 'checkbox', default: true },
    'SHORT_LINK_MAX_AGE_DAYS': { type: 'number', default: 30 },
    'TELEGRAM_ENABLED': { type: 'checkbox', default: false },
    'DISCORD_ENABLED': { type: 'checkbox', default: false },
    'WEBHOOK_ENABLED': { type: 'checkbox', default: false },
    'WEBHOOK_URL': { type: 'text', default: '' },
    'WEBHOOK_AUTHORIZATION_HEADER': { type: 'text', default: '' },
    'WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'WEBHOOK_RENEWAL_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'WEBHOOK_REACTIVATION_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'TELEGRAM_BOT_TOKEN': { type: 'password', default: '' },
    'TELEGRAM_CHAT_ID': { type: 'text', default: '' },
    'DAYS_TO_NOTIFY_EXPIRATION': { type: 'number', default: 2 },
    'TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'TELEGRAM_RENEWAL_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'TELEGRAM_REACTIVATION_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'DISCORD_WEBHOOK_URL': { type: 'text', default: '' },
    'DISCORD_EXPIRATION_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'DISCORD_RENEWAL_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'DISCORD_REACTIVATION_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'DISCORD_TRIAL_END_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'plex_url_display': { type: 'text', readonly: true, key: 'PLEX_URL' },
    'TAUTULLI_URL': { type: 'text', default: '' },
    'TAUTULLI_API_KEY': { type: 'password', default: '' },
    'EFI_ENABLED': { type: 'checkbox', default: false },
    'MERCADOPAGO_ENABLED': { type: 'checkbox', default: false },
    'BPIX_ENABLED': { type: 'checkbox', default: false },
    'EFI_CLIENT_ID': { type: 'text', default: '' },
    'EFI_CLIENT_SECRET': { type: 'password', default: '' },
    'EFI_CERTIFICATE': { type: 'text', default: '' },
    'EFI_SANDBOX': { type: 'checkbox', default: false },
    'EFI_PIX_KEY': { type: 'text', default: '' },
    'EFI_USE_MTLS': { type: 'checkbox', default: true },
    'EFI_WEBHOOK_HMAC_SECRET': { type: 'text', default: '' },
    'MERCADOPAGO_ACCESS_TOKEN': { type: 'password', default: '' },
    'BPIX_AUTH_TOKEN': { type: 'password', default: '' },
    'RENEWAL_PRICE': { type: 'text', default: '10.00' },
    'PAYMENT_LINK_GRACE_PERIOD_DAYS': { type: 'number', default: 7 },
    'PRICE_SCREEN_1': { type: 'price', key: '1' },
    'PRICE_SCREEN_2': { type: 'price', key: '2' },
    'PRICE_SCREEN_3': { type: 'price', key: '3' },
    'PRICE_SCREEN_4': { type: 'price', key: '4' },
    'PRICE_SCREEN_5': { type: 'price', key: '5' },
    'PRICE_SCREEN_6': { type: 'price', key: '6' },
    'OVERSEERR_ENABLED': { type: 'checkbox', default: false },
    'OVERSEERR_URL': { type: 'text', default: '' },
    'OVERSEERR_API_KEY': { type: 'password', default: '' },
    'ACHIEVEMENT_MOVIE_MARATHON_BRONZE': { type: 'number', default: 5 },
    'ACHIEVEMENT_MOVIE_MARATHON_SILVER': { type: 'number', default: 10 },
    'ACHIEVEMENT_MOVIE_MARATHON_GOLD': { type: 'number', default: 20 },
    'ACHIEVEMENT_SERIES_BINGER_BRONZE': { type: 'number', default: 20 },
    'ACHIEVEMENT_SERIES_BINGER_SILVER': { type: 'number', default: 50 },
    'ACHIEVEMENT_SERIES_BINGER_GOLD': { type: 'number', default: 100 },
    'ACHIEVEMENT_TIME_TRAVELER_BRONZE': { type: 'number', default: 3 },
    'ACHIEVEMENT_TIME_TRAVELER_SILVER': { type: 'number', default: 5 },
    'ACHIEVEMENT_TIME_TRAVELER_GOLD': { type: 'number', default: 7 },
    'ACHIEVEMENT_DIRECTOR_FAN_BRONZE': { type: 'number', default: 3 },
    'ACHIEVEMENT_DIRECTOR_FAN_SILVER': { type: 'number', default: 5 },
    'ACHIEVEMENT_DIRECTOR_FAN_GOLD': { type: 'number', default: 7 },
    'ACHIEVEMENT_NIGHT_OWL_BRONZE': { type: 'number', default: 3 },
    'ACHIEVEMENT_NIGHT_OWL_SILVER': { type: 'number', default: 10 },
    'ACHIEVEMENT_NIGHT_OWL_GOLD': { type: 'number', default: 25 },
    'ACHIEVEMENT_PIONEER_BRONZE': { type: 'number', default: 1 },
    'ACHIEVEMENT_PIONEER_SILVER': { type: 'number', default: 5 },
    'ACHIEVEMENT_PIONEER_GOLD': { type: 'number', default: 15 },
    'ACHIEVEMENT_PIONEER_WINDOW_HOURS': { type: 'number', default: 48 },
    'XP_PER_MINUTE_WATCHED': { type: 'decimal', default: 1 },
    'REFERRAL_ENABLED': { type: 'checkbox', default: false },
    'REFERRAL_REWARD_TYPE': { type: 'text', default: 'days' },
    'REFERRAL_REWARD_DAYS': { type: 'number', default: 7 },
    'REFERRAL_REWARD_CREDIT': { type: 'decimal', default: 5 },
    'XP_BONUS_PER_COMPLETED_ITEM': { type: 'number', default: 20 },
    'XP_COMPLETION_THRESHOLD_PERCENT': { type: 'number', default: 90 },
    'XP_RESET_ENABLED': { type: 'checkbox', default: false },
    'TELEGRAM_BULK_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'DISCORD_BULK_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'WEBHOOK_BULK_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
    'STREAM_CHECK_INTERVAL_SECONDS': { type: 'number', default: 15 },
    'SCREEN_LIMIT_TERMINATION_STRATEGY': { type: 'text', default: 'oldest' },
    'BACKUP_ENABLED': { type: 'checkbox', default: false },
    'BACKUP_TIME': { type: 'text', default: '05:00' },
    'BACKUP_MAX_COUNT': { type: 'number', default: 7 },
    'TERMINATION_MSG_BLOCKED_MANUAL': { type: 'textarea', default: '' },
    'TERMINATION_MSG_BLOCKED_EXPIRED': { type: 'textarea', default: '' },
    'TERMINATION_MSG_BLOCKED_TRIAL_EXPIRED': { type: 'textarea', default: '' },
    'TERMINATION_MSG_SCREEN_LIMIT': { type: 'textarea', default: '' },
};

if (scriptTag) {
    for (const key in scriptTag.dataset) {
        if (key.startsWith('urls')) {
            const subKey = key.substring(4);
            const urlKey = subKey.charAt(0).toLowerCase() + subKey.slice(1).replace(/-(\w)/g, (match, letter) => letter.toUpperCase());
            urls[urlKey] = scriptTag.dataset[key];
        } else if (key.startsWith('i18n')) {
            const i18nKey = key.charAt(4).toLowerCase() + key.slice(5).replace(/-(\w)/g, (match, letter) => letter.toUpperCase());
            i18n[i18nKey] = scriptTag.dataset[key];
        }
    }
}