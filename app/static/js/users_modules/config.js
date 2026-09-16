// app/static/js/users_modules/config.js

/**
 * Módulo de Configuração
 * * Extrai e exporta URLs e textos de internacionalização (i18n)
 * do script tag no HTML para serem usados em toda a aplicação da página de usuários.
 * Isto centraliza a configuração e facilita a sua gestão.
 */

import { lerConfiguracaoDoScript } from '../utils.js';

// Os URLs da API e os textos traduzidos, lidos pelo carregador único.
export const { urls, i18n } = lerConfiguracaoDoScript('users-script');
