// app/static/js/users_modules/config.js

/**
 * Módulo de Configuração
 * * Extrai e exporta URLs e textos de internacionalização (i18n)
 * do script tag no HTML para serem usados em toda a aplicação da página de usuários.
 * Isto centraliza a configuração e facilita a sua gestão.
 */

const scriptTag = document.getElementById('users-script');

// Objeto para armazenar todos os URLs da API.
export const urls = {};
// Objeto para armazenar todos os textos traduzidos.
export const i18n = {};

if (scriptTag) {
    for (const key in scriptTag.dataset) {
        if (key.startsWith('i18n')) {
            // Converte de kebab-case (data-i18n-some-key) para camelCase (someKey).
            const i18nKey = key.charAt(4).toLowerCase() + key.slice(5).replace(/-(\w)/g, (_, letter) => letter.toUpperCase());
            i18n[i18nKey] = scriptTag.dataset[key];
        } else if (key.startsWith('url')) {
            // Converte de kebab-case (data-url-api-status) para camelCase (apiStatus).
            const urlKey = key.substring(3).charAt(0).toLowerCase() + key.substring(4).replace(/-(\w)/g, (_, letter) => letter.toUpperCase());
            urls[urlKey] = scriptTag.dataset[key];
        }
    }
}
