/**
 * Ponto de Entrada Principal para a Página de Configurações
 * Orquestra a inicialização da página, importando os módulos necessários,
 * configurando os manipuladores de eventos e iniciando o carregamento
 * inicial dos dados de configuração.
 */

import * as ui from './settings_modules/ui.js';
import * as handlers from './settings_modules/handlers.js';

document.addEventListener('DOMContentLoaded', () => {
    // 1. Carrega as configurações e preenche o formulário
    ui.loadSettings();

    // 2. Inicializa todos os event listeners da página
    handlers.initializeEventListeners();
});

