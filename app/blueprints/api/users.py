# app/blueprints/api/users.py

import logging
import secrets
import json
from datetime import date, datetime, timezone, timedelta 

from flask import Blueprint, jsonify, request, url_for, current_app, session
from flask_login import current_user
from flask_babel import gettext as _, format_date
from tzlocal import get_localzone
from pydantic import ValidationError, BaseModel, Field
from sqlalchemy.exc import IntegrityError
from apscheduler.jobstores.base import JobLookupError

from ... import extensions
from ...config import load_or_create_config
from ..auth import admin_required, login_required
from .decorators import user_lookup_by_id, validate_json
from .schemas import RenewSubscriptionSchema, UpdateProfileSchema, UpdateAccountProfileSchema
from ...models import UserProfile
from ...extensions import limiter
from ...services.password_reset import servidor_repoe_palavras_passe
from ...services import audit
from ...utils.identity import normalize_user_id, same_user
from ...services.data_manager import get_app_timezone
from ..auth import MAX_PALAVRA_PASSE, MIN_PALAVRA_PASSE

logger = logging.getLogger(__name__)
users_api_bp = Blueprint('users_api', __name__)

class ExtendTrialSchema(BaseModel):
    extend_minutes: int = Field(..., gt=0, description="Duração da extensão em minutos (deve ser maior que zero).")

# ==========================================
# ROTAS PÚBLICAS
# ==========================================

@users_api_bp.route('/public-profile-by-token/<string:token>')
@limiter.limit("30 per minute", override_defaults=False)
def get_public_user_profile_by_token(token):
    """O nome, o avatar e o vencimento de quem é dono deste link de pagamento.

    🔒 Rota PÚBLICA, e era a única do fluxo de pagamento sem limite nenhum —
    as irmãs `/api/payments/options` e `/api/invites/details` já o tinham. Ela
    responde 200 para um token válido e 404 para um inválido, ou seja, é um
    oráculo que diz se um token existe, e devolve dados pessoais quando
    acerta. 30/min chega para qualquer utilização legítima: a página de
    pagamento chama-a uma vez ao abrir.
    """
    profile = extensions.data_manager.perfil_por_payment_token(token)
    if not profile:
        return jsonify({"success": False, "message": _("Link de pagamento inválido ou usuário não encontrado.")}), 404

    user_thumb = None
    username = profile.username

    if profile.status == 'active':
        user = extensions.media_server.get_user_by_id(profile.media_user_id)
        if not user:
            logger.warning(f"Utilizador ativo '{username}' (ID: {profile.media_user_id}) não encontrado no Plex. A tratar como inativo para a página pública.")
        else:
            user_thumb = user.get('thumb')

    expiration_date_formatted = None
    if profile.expiration_date:
        try:
            local_tz = get_localzone()
            exp_date = datetime.fromisoformat(profile.expiration_date).astimezone(local_tz)
            expiration_date_formatted = exp_date.strftime('%d/%m/%Y')
        except (ValueError, TypeError): 
            pass

    public_data = {
        "username": username,
        "thumb": user_thumb,
        "expiration_date_formatted": expiration_date_formatted,
        "expiration_date_iso": profile.expiration_date
    }
    return jsonify({"success": True, "profile": public_data})

@users_api_bp.route('/public/finalize-reactivation', methods=['POST'])
def finalize_reactivation_route():
    """Rota pública chamada pela página de pagamento para aceitar convite via Token."""
    data = request.json
    plex_token = data.get('plex_token')
    payment_token = data.get('payment_token')

    if not plex_token or not payment_token:
        return jsonify({"success": False, "message": _("Dados incompletos.")}), 400

    result = extensions.media_server.invites.accept_invite_via_token(plex_token)
    if not result.get('success'):
        return jsonify(result), 400

    plex_user_obj = result.get('user')
    
    try:
        profile = extensions.data_manager.perfil_por_payment_token(payment_token)
        if not profile:
             return jsonify({"success": False, "message": _("Perfil local não encontrado.")}), 404

        # Segurança: Verifica se o ID do Plex que aceitou o convite é o mesmo do perfil local
        if not same_user(profile.media_user_id, plex_user_obj.id):
             logger.warning(f"Tentativa de reativação com conta incorreta. Token: {profile.media_user_id}, Login: {plex_user_obj.id}")
             return jsonify({
                "success": False, 
                "message": _("A conta Plex utilizada ('%(plex_user)s') não corresponde à conta original deste perfil. Saia do Plex e entre com a conta original (%(local_user)s).", plex_user=plex_user_obj.username, local_user=profile.username)
             }), 409

        # Verifica mudança de username/email (Evita colisões na DB)
        if profile.username != plex_user_obj.username or profile.email != plex_user_obj.email:
            existing_collision = UserProfile.query.filter_by(username=plex_user_obj.username).first()
            if existing_collision and existing_collision.media_user_id != profile.media_user_id:
                 return jsonify({
                    "success": False, 
                    "message": _("Alterou o seu nome no Plex para '%(new_name)s', mas este já existe no sistema. Contacte o suporte.", new_name=plex_user_obj.username)
                 }), 409
            
            profile.username = plex_user_obj.username
            profile.email = plex_user_obj.email

        profile.status = 'active'
        profile.pending_invite_link = None
        extensions.db.session.commit()
        extensions.media_server.invalidate_user_cache()
        
        logger.info(f"Reativação finalizada com sucesso para o utilizador público: {profile.username}")
        return jsonify({"success": True, "message": _("Conta reativada com sucesso!"), "redirect_url": url_for('main.account_page')})

    except IntegrityError:
        extensions.db.session.rollback()
        return jsonify({"success": False, "message": _("Erro de integridade ao sincronizar nome de usuário.")}), 409
    except Exception as e:
        extensions.db.session.rollback()
        logger.error(f"Erro ao finalizar reativação local: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Erro ao atualizar sistema local.")}), 500

# ==========================================
# ROTAS DO UTILIZADOR (MINHA CONTA)
# ==========================================

@users_api_bp.route('/account/details')
@login_required
def get_account_details():
    config = load_or_create_config()
    media_user_id = normalize_user_id(current_user.id)

    # 🐛 O ADMINISTRADOR NÃO TEM PERFIL LOCAL. O login dele devolve logo na
    # primeira ramificação de `_autorizar_e_iniciar_sessao`, antes da parte que
    # cria e sincroniza perfis — isso é para quem é utilizador do servidor, e o
    # administrador não é (no Plex nem aparece na lista de amigos). Toda esta
    # rota assumia um dicionário e rebentava com AttributeError assim que o
    # administrador abria a "Minha Conta".
    profile = extensions.data_manager.get_user_profile(media_user_id) or {}

    is_blocked_info = extensions.data_manager.get_blocked_user(media_user_id)

    expiration_info = _get_expiration_details(profile, config)
    
    join_date = _("Não disponível")
    if join_date_str := extensions.data_manager.get_user_claim_date(media_user_id):
        try: 
            local_tz = get_localzone()
            join_date = format_date(datetime.fromisoformat(join_date_str).astimezone(local_tz), 'd \'de\' MMMM \'de\' yyyy')
        except (ValueError, TypeError): 
            pass

    libraries_data = extensions.media_server.get_user_libraries(media_user_id)
    watch_data = extensions.stats_manager.get_user_watch_details(media_user_id=media_user_id)

    # 🐛 O avatar da sessão é uma FOTOGRAFIA do momento do login: quem colocasse
    # uma imagem de perfil no servidor depois de entrar continuava a ver o "?"
    # até voltar a autenticar-se. Esta rota já fala com o servidor, por isso
    # aproveita-se para trazer o avatar atual — e para o gravar na sessão, que é
    # de onde o cabeçalho (base.html) o lê em todas as outras páginas.
    thumb = _avatar_atualizado(media_user_id)

    is_on_trial = False
    if trial_end_date_iso := profile.get('trial_end_date'):
        try:
            if datetime.fromisoformat(trial_end_date_iso) > datetime.now(timezone.utc):
                is_on_trial = True
        except (ValueError, TypeError): 
            pass

    # A "Minha Conta" do administrador não tem assinatura, nem vencimento, nem
    # limite de telas — ele é o dono do servidor. Em vez de mostrar campos
    # vazios ou um "Não disponível", a interface identifica-o e esconde o que
    # não se lhe aplica.
    e_administrador = current_user.is_admin()

    return jsonify({
        "success": True, 
        "username": current_user.username, 
        "email": current_user.email, 
        "thumb": thumb,
        "is_admin": e_administrador,
        "join_date": join_date, 
        "screen_limit": _("%(num)d Tela(s)", num=profile.get('screen_limit', 0)) if profile.get('screen_limit', 0) > 0 else _("Ilimitado"),
        "libraries": libraries_data.get('libraries', []), 
        "watch_stats": watch_data.get('details', {}),
        "expiration_info": expiration_info, 
        "is_blocked": is_blocked_info is not None, 
        "block_reason": is_blocked_info.get('block_reason') if is_blocked_info else None,
        "trial_end_date": trial_end_date_iso,
        "is_on_trial": is_on_trial,
        "hide_from_leaderboard": profile.get('hide_from_leaderboard', False),
        "notification_settings": {
            "telegram_enabled": config.get("TELEGRAM_ENABLED", False),
            "discord_enabled": config.get("DISCORD_ENABLED", False),
            "webhook_enabled": config.get("WEBHOOK_ENABLED", False),
            "whatsapp_enabled": config.get("WHATSAPP_ENABLED", False)
        },
        "profile_details": { 
            "name": profile.get("name"), 
            "telegram_user": profile.get("telegram_user"), 
            "discord_user_id": profile.get("discord_user_id"), 
            "phone_number": profile.get("phone_number"), 
            "overseerr_access": profile.get("overseerr_access", False) 
        }
    })

@users_api_bp.route('/account/profile', methods=['POST'])
@login_required
@validate_json(UpdateAccountProfileSchema)
def update_account_profile(validated_data):
    # Compatibilidade com Pydantic v1 e v2
    data = validated_data.dict(exclude_unset=True) if hasattr(validated_data, 'dict') else validated_data.model_dump(exclude_unset=True)
    media_user_id = normalize_user_id(current_user.id)
    # Sem perfil (o caso do administrador), grava-se um novo em vez de rebentar:
    # o `username` faz falta para o perfil ser reconhecível na base de dados.
    profile = extensions.data_manager.get_user_profile(media_user_id) or {
        'media_user_id': media_user_id,
        'username': current_user.username,
        'email': current_user.email,
    }
    profile.update(data)
    extensions.data_manager.set_user_profile(media_user_id, profile)
    return jsonify({"success": True, "message": _("Perfil atualizado com sucesso.")})

@users_api_bp.route('/account/privacy', methods=['POST'])
@login_required
def update_privacy_settings():
    hide_setting = request.json.get('hide')
    if not isinstance(hide_setting, bool): 
        return jsonify({"success": False, "message": _("Valor inválido.")}), 400
    
    media_user_id = normalize_user_id(current_user.id)
    # Como em `/account/profile`: o administrador não tem perfil local.
    profile = extensions.data_manager.get_user_profile(media_user_id) or {
        'media_user_id': media_user_id,
        'username': current_user.username,
        'email': current_user.email,
    }
    profile['hide_from_leaderboard'] = hide_setting
    extensions.data_manager.set_user_profile(media_user_id, profile)
    return jsonify({"success": True, "message": _("Configuração de privacidade atualizada com sucesso.")})

@users_api_bp.route('/account/password', methods=['POST'])
@login_required
@limiter.limit("10 per minute", override_defaults=False)
def alterar_palavra_passe():
    """Muda a palavra-passe de quem está autenticado.

    ⚠️ **Não há duas palavras-passe.** A que aqui se grava é a do próprio
    servidor de média: é com ela que a pessoa entra na aplicação do servidor E
    neste painel, porque o painel autentica contra ele (ver `authenticate`).
    O painel não guarda palavra-passe nenhuma, em sítio nenhum.

    🛡️ **Pede-se a ATUAL, e confirma-se contra o servidor.** Uma sessão do
    painel esquecida aberta num computador partilhado não pode bastar para
    tomar a conta — e quem pede a mudança tem de provar que é quem diz ser. É a
    mesma verificação do login, o que também fecha a sessão que ela abre no
    servidor.
    """
    if not servidor_repoe_palavras_passe(extensions.media_server):
        return jsonify({
            "success": False,
            "message": _("Este servidor usa autenticação externa: a senha é gerenciada lá."),
        }), 400

    dados = request.get_json(silent=True) or {}
    atual = dados.get('current_password') or ''
    nova = dados.get('new_password') or ''

    if not atual or not nova:
        return jsonify({"success": False, "message": _("Preencha todos os campos.")}), 400

    if len(nova) < MIN_PALAVRA_PASSE:
        return jsonify({
            "success": False,
            "message": _("A nova senha precisa ter pelo menos %(minimo)d caracteres.",
                         minimo=MIN_PALAVRA_PASSE),
        }), 400

    # O mesmo limite do login: o pedido para aqui, antes de ir à rede.
    if len(atual) > MAX_PALAVRA_PASSE or len(nova) > MAX_PALAVRA_PASSE:
        return jsonify({"success": False, "message": _("A senha é longa demais.")}), 400

    if extensions.media_server.authenticate(current_user.username, atual) is None:
        logger.warning(f"'{current_user.username}' falhou a confirmação da palavra-passe atual.")
        return jsonify({"success": False, "message": _("A senha atual não está correta.")}), 403

    resultado = extensions.media_server.definir_palavra_passe(normalize_user_id(current_user.id), nova)
    if not resultado.get('success'):
        return jsonify({
            "success": False,
            "message": resultado.get('message') or _("Não foi possível alterar a senha."),
        }), 400

    # ⚠️ A sessão do painel NÃO cai com isto: ela é um cookie assinado pelo
    # painel e não guarda a palavra-passe. Quem está a ler esta resposta
    # continua a entrar — é na aplicação do servidor que terá de usar a nova.
    logger.info(f"'{current_user.username}' alterou a sua palavra-passe.")
    return jsonify({"success": True, "message": _("Senha alterada. Use a nova da próxima vez que entrar.")})


@users_api_bp.route('/account/requests')
@login_required
def get_account_requests():
    filter_status = request.args.get('filter', 'all', type=str)
    # ⚠️ Estes valores TÊM de corresponder aos aceites pelo Seerr. Enviar um valor
    # fora da lista faz a API responder HTTP 500 com
    # "filter must be equal to one of the allowed values".
    # 'declined' NÃO é aceite pelo Seerr — o equivalente é 'unavailable'.
    FILTROS_VALIDOS = [
        'all', 'approved', 'available', 'pending', 'processing',
        'unavailable', 'failed', 'deleted', 'completed'
    ]
    if filter_status not in FILTROS_VALIDOS: 
        filter_status = 'all'
    if not extensions.overseerr_manager.enabled: 
        return jsonify({"success": True, "requests": [], "overseerr_disabled": True})
    # Paginação: permite ao frontend oferecer "ver mais" em vez de ficar preso aos
    # primeiros 20 pedidos.
    limit = min(request.args.get('limit', 20, type=int), 50)
    skip = max(0, request.args.get('skip', 0, type=int))
    # ⚠️ O nome vai junto porque o EMAIL não é obrigatório em toda a parte: num
    # painel Jellyfin o convite pede-o como opcional, e quem não o preencheu via
    # esta aba vazia para sempre — sem erro nenhum, como se nunca tivesse pedido
    # nada. A pesquisa do Seerr cobre `jellyfinUsername`, por isso o nome chega.
    return jsonify(extensions.overseerr_manager.get_user_requests(
        current_user.email, limit=limit, filter=filter_status, skip=skip,
        username=current_user.username,
    ))

@users_api_bp.route('/account/devices')
@login_required
def get_account_devices():
    # Quem responde é o backend do servidor de média: no Plex os aparelhos são
    # deduzidos do histórico do Tautulli, no Jellyfin vêm da lista de aparelhos
    # REGISTADOS na conta. A rota não precisa de saber qual é qual.
    return jsonify(extensions.media_server.get_user_devices(normalize_user_id(current_user.id)))

# ==========================================
# ROTAS ADMIN (GERENCIAMENTO)
# ==========================================

@users_api_bp.route('/status')
@login_required
@admin_required
def get_status():
    """Rota principal do Dashboard Administrativo. Sincroniza dados e retorna a lista de utilizadores."""
    if not extensions.media_server.is_connected():
        return jsonify({"error": _("Servidor de média não configurado.")}), 500

    force_refresh = request.args.get('force', 'false').lower() == 'true'
    all_plex_users_list = extensions.media_server.get_all_users(force_refresh=force_refresh) or []
    
    config = load_or_create_config()
    all_users_to_return = _sync_plex_and_local_profiles(all_plex_users_list, config.get('ADMIN_USER'))

    return jsonify({
        'users': sorted(all_users_to_return, key=lambda u: (u.get('username') or '').lower()),
        'libraries': extensions.media_server.get_libraries(),
        # Que canais de contacto podem ser pré-atribuídos a um convite. A
        # interface esconde o campo do canal que não está ligado: pedir um
        # Discord ID num painel sem Discord configurado é pedir um dado que
        # nunca vai ser usado.
        'telegram_enabled': config.get("TELEGRAM_ENABLED", False),
        'discord_enabled': config.get("DISCORD_ENABLED", False),
    })

@users_api_bp.route('/list')
@login_required
@admin_required
def get_user_list():
    """Retorna lista simplificada para Dropdowns (apenas utilizadores com contactos)."""
    try:
        plex_users = extensions.media_server.get_all_users() or []
        user_profiles = extensions.data_manager.get_all_user_profiles()
        # 🐛 As chaves vêm da base de dados como TEXTO e o `user['id']` vem do
        # servidor de média como INTEIRO: sem normalizar, este `.get()` devolvia
        # sempre {} e a lista de contactos aparecia vazia, sem erro nenhum.
        profiles_map = {normalize_user_id(p['media_user_id']): p for p in user_profiles}

        filtered_users = []
        for user in plex_users:
            profile = profiles_map.get(normalize_user_id(user['id']), {})
            if profile.get('telegram_user') or profile.get('discord_user_id') or profile.get('phone_number'):
                filtered_users.append({
                    'id': user['id'],
                    'username': user.get('username', user.get('title', 'N/A')),
                    'email': user.get('email', '')
                })

        return jsonify({"success": True, "users": sorted(filtered_users, key=lambda x: str(x['username']).lower())})
    except Exception as e:
        logger.error(f"Erro ao listar utilizadores para seleção: {e}")
        return jsonify({"success": False, "message": _("Erro interno ao obter lista.")}), 500

@users_api_bp.route('/profile/<media_user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def user_profile_route(media_user_id):
    """Consulta ou edita diretamente as informações de um utilizador específico (Admin)."""
    user_info = extensions.media_server.get_user_by_id(media_user_id)
    if not user_info:
        return jsonify({"success": False, "message": _("Usuário não encontrado no Plex.")}), 404

    username = user_info['username']

    if request.method == 'GET':
        profile = extensions.data_manager.get_user_profile(media_user_id)
        config = load_or_create_config()
        
        is_on_trial = False
        if trial_end_date_iso := profile.get('trial_end_date'):
            try:
                if datetime.fromisoformat(trial_end_date_iso) > datetime.now(timezone.utc):
                    is_on_trial = True
            except (ValueError, TypeError): pass

        return jsonify({
            "success": True, "profile": profile, "is_on_trial": is_on_trial,
            "notification_settings": {
                "telegram_enabled": config.get("TELEGRAM_ENABLED", False), 
                "discord_enabled": config.get("DISCORD_ENABLED", False), 
                "webhook_enabled": config.get("WEBHOOK_ENABLED", False),
                "whatsapp_enabled": config.get("WHATSAPP_ENABLED", False)
            },
            "universal_expiration_settings": {
                "enabled": config.get("UNIVERSAL_EXPIRATION_ENABLED", False), 
                "time": config.get("UNIVERSAL_EXPIRATION_TIME", "23:59")
            }
        })

    # Tratamento de POST (Atualização de Perfil)
    try: 
        validated_data = UpdateProfileSchema(**request.json)
    except ValidationError as e: 
        return jsonify({"success": False, "message": _("Dados inválidos."), "errors": {err['loc'][0]: err['msg'] for err in e.errors()}}), 400

    # Compatibilidade Pydantic v1 / v2
    data = validated_data.dict(exclude_unset=True) if hasattr(validated_data, 'dict') else validated_data.model_dump(exclude_unset=True)
    local_datetime_str = data.pop('expiration_datetime_local', None)
    
    profile_to_update = extensions.data_manager.get_user_profile(media_user_id)
    # Uma CÓPIA, porque `profile_to_update` é mutado logo a seguir: sem ela, a
    # auditoria comparava o dicionário com ele próprio e nunca via mudança
    # nenhuma — uma trilha que diz sempre "nada mudou" é pior do que não haver.
    perfil_antes = dict(profile_to_update or {})
    profile_to_update.update(data)

    _update_manual_expiration_job(media_user_id, username, profile_to_update, local_datetime_str)
    extensions.data_manager.set_user_profile(media_user_id, profile_to_update)
    _enforce_user_status_by_date(media_user_id, username, profile_to_update)

    logger.info(f"Admin '{current_user.username}' atualizou o perfil de '{username}'.")
    audit.registar('utilizador.editar_perfil', alvo_tipo='utilizador', alvo_id=media_user_id,
                   detalhes={'username': username,
                             'campos': audit.diferenca(perfil_antes, profile_to_update)})
    return jsonify({"success": True, "message": _("Perfil do usuário atualizado com sucesso.")})

@users_api_bp.route('/extend-trial/<media_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
@validate_json(ExtendTrialSchema)
def extend_trial_route(user, validated_data):
    from ...extensions import scheduler
    from ...scheduler import end_trial_job

    media_user_id = user['id']
    username = user['username']
    extend_minutes = validated_data.extend_minutes
    profile = extensions.data_manager.get_user_profile(media_user_id)
    
    try:
        now_utc = datetime.now(timezone.utc)
        current_trial_end_utc = now_utc

        if trial_end_date_str := profile.get('trial_end_date'):
            try:
                current_trial_end_utc = datetime.fromisoformat(trial_end_date_str)
            except (ValueError, TypeError):
                current_trial_end_utc = now_utc

        base_time = max(current_trial_end_utc, now_utc)
        new_trial_end_utc = base_time + timedelta(minutes=extend_minutes)

        if old_job_id := profile.get('trial_job_id'):
            try: scheduler.remove_job(old_job_id)
            except JobLookupError: pass

        new_job_id = f"trial_end_{media_user_id}_{secrets.token_hex(4)}"
        naive_run_date = new_trial_end_utc.astimezone(scheduler.timezone).replace(tzinfo=None)
        scheduler.add_job(id=new_job_id, func=end_trial_job, args=[media_user_id], trigger='date', run_date=naive_run_date, replace_existing=True, misfire_grace_time=3600)

        profile['trial_end_date'] = new_trial_end_utc.isoformat()
        profile['trial_job_id'] = new_job_id
        
        # Limpa expiração regular
        if profile.get('expiration_date'):
             profile['expiration_date'] = None
        if old_exp_job := profile.get('expiration_job_id'):
            try: scheduler.remove_job(old_exp_job)
            except JobLookupError: pass
            profile['expiration_job_id'] = None

        extensions.data_manager.set_user_profile(media_user_id, profile)

        blocked_info = extensions.data_manager.get_blocked_user(media_user_id)
        if blocked_info and blocked_info.get('block_reason') in ['trial_expired', 'expired']:
            extensions.media_server.unblock_user(media_user_id)

        logger.info(f"Admin '{current_user.username}' estendeu/iniciou o período de teste de '{username}' por {extend_minutes} minutos.")

        # 🔔 Do outro lado, isto era uma mudança silenciosa: a pessoa continuava
        # a contar com a data antiga, e o único aviso que o painel lhe mandava
        # sobre o teste era o do FIM. Avisar aqui é a metade que faltava.
        #
        # ⚠️ E nunca derruba a rota: o teste já está estendido e a tarefa já foi
        # remarcada quando isto corre. Um canal fora do ar não pode fazer o
        # administrador pensar que a extensão falhou — e, por isso mesmo, a
        # mensagem dele diz se o aviso saiu ou não.
        entrega = {'sent': [], 'failed': []}
        try:
            entrega = extensions.notifier_manager.send_trial_extended_notification(
                user, profile, new_trial_end_utc) or entrega
        except Exception as e:
            logger.warning(f"Não foi possível avisar '{username}' da extensão do teste: {e}")
            entrega = {'sent': [], 'failed': [('', str(e))]}

        fim = naive_run_date.strftime('%d/%m/%Y %H:%M')
        if entrega['sent']:
            mensagem = _("Período de teste estendido/definido. Fim a %(date)s. O usuário foi avisado.", date=fim)
        elif entrega['failed']:
            mensagem = _("Período de teste estendido/definido. Fim a %(date)s. Não foi possível avisar o usuário.", date=fim)
        else:
            mensagem = _("Período de teste estendido/definido. Fim a %(date)s. O usuário não tem contato para ser avisado.", date=fim)

        return jsonify({"success": True, "message": mensagem, "notificado": bool(entrega['sent'])})
    except Exception as e:
        logger.error(f"Erro ao estender teste para '{username}': {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Ocorreu um erro interno ao estender o teste.")}), 500

@users_api_bp.route('/reactivate', methods=['POST'])
@login_required
@admin_required
def reactivate_user_route():
    media_user_id = request.json.get('media_user_id')
    libraries = request.json.get('libraries')

    if not media_user_id or not libraries:
        return jsonify({"success": False, "message": _("Dados incompletos fornecidos.")}), 400

    profile = extensions.data_manager.get_user_profile(media_user_id)
    if not profile or profile.get('status') != 'inactive':
        return jsonify({"success": False, "message": _("Apenas usuários inativos podem ser reativados.")}), 404

    username = profile.get('username')

    try:
        logger.info(f"Admin '{current_user.username}' a iniciar reativação manual de '{username}'.")

        # ⚠️ Repor o acesso NÃO é "enviar um convite": é o que esta rota chamava,
        # e num servidor de contas locais "enviar convite" quer dizer CRIAR uma
        # conta — com o email por nome, e com uma palavra-passe que ninguém veria.
        # Quem sabe o que é preciso fazer é o backend (ver `restaurar_acesso`).
        restauro = extensions.media_server.restaurar_acesso(media_user_id, profile, libraries=libraries)

        if not restauro.get('success'):
            logger.error(f"Falha ao repor o acesso de '{username}': {restauro.get('message')}")
            return jsonify({"success": False, "message": restauro.get('message') or _("Erro ao restaurar o acesso.")})

        # ⚠️ A identidade pode ter MUDADO: num servidor de contas locais, uma
        # conta apagada e recriada volta com um identificador novo, e o perfil
        # foi migrado para ele. Gravar no antigo criaria um perfil fantasma.
        media_user_id = restauro.get('media_user_id') or media_user_id

        extensions.data_manager.set_user_profile(media_user_id, {
            'status': 'active',
            'libraries': json.dumps(libraries),
            'pending_invite_link': restauro.get('link_pendente'),
        })
        extensions.data_manager.remove_blocked_user(media_user_id)

        mensagem = _("Usuário reativado com sucesso.")

        # 🛡️ A conta teve de ser criada de novo: a palavra-passe é outra e só o
        # painel a conhece. Sai por notificação e não fica no log nem na
        # resposta desta rota.
        if credenciais := restauro.get('credenciais'):
            perfil_atual = extensions.data_manager.get_user_profile(media_user_id) or {}
            extensions.media_server.notifier_manager.send_credentials_notification(
                {'id': media_user_id, 'username': username}, perfil_atual,
                credenciais, link=restauro.get('link'),
            )
            mensagem = _("Conta criada de novo no servidor. As credenciais foram enviadas ao usuário.")

        if extensions.socketio:
            extensions.socketio.emit('user_list_updated', {'message': _("O usuário %(username)s foi reativado.", username=username)}, namespace='/dashboard')

        return jsonify({"success": True, "message": mensagem})

    except Exception as e:
        logger.error(f"Erro interno ao reativar {media_user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Erro interno ao processar reativação.")}), 500

@users_api_bp.route('/renew/<media_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
@validate_json(RenewSubscriptionSchema)
def renew_user_subscription_route(user, validated_data):
    try:
        data = validated_data
        
        logger.info(f"Admin '{current_user.username}' solicitou a renovação manual de '{user['username']}' (ID: {user['id']}) por {data.months} mês/meses.")

        new_expiration_date = extensions.media_server.renew_subscription(
            user['id'], data.months, base_mode=data.base,
            base_date_str=data.base_date, expiration_time_str=data.expiration_time
        )

        config = load_or_create_config()
        profile = extensions.data_manager.get_user_profile(user['id'])
        
        # --- LÓGICA DE FALLBACK INTELIGENTE DE PREÇO ---
        current_screens = profile.get('screen_limit', 0)
        screen_prices = config.get("SCREEN_PRICES", {})
        renewal_price = config.get("RENEWAL_PRICE")
        
        # 1. Tenta o preço exato do limite atual
        monthly_price_str = screen_prices.get(str(current_screens))
        
        # 2. Tenta o fallback geral
        if not monthly_price_str or not str(monthly_price_str).strip():
            monthly_price_str = renewal_price
            
        # 3. Fallback inteligente (procura o menor valor ativo)
        if not monthly_price_str or not str(monthly_price_str).strip():
            valid_prices = []
            for v in screen_prices.values():
                try:
                    if v and str(v).strip():
                        valid_prices.append(float(str(v).replace(',', '.')))
                except ValueError:
                    continue
            monthly_price_str = str(min(valid_prices)) if valid_prices else "0.00"

        # 4. Conversão à prova de falhas
        try:
            clean_price = str(monthly_price_str).replace(',', '.').strip()
            base_price = float(clean_price) if clean_price else 0.0
        except (ValueError, TypeError):
            base_price = 0.0
            
        total_value = base_price * data.months
        # --- FIM DA LÓGICA DE PREÇO ---

        with extensions.db.session.begin_nested():
            # Cria a entrada no histórico de pagamentos (financeiro)
            extensions.data_manager.add_manual_payment(
                user['id'], user['username'], total_value,
                f"Renovação Admin (+{data.months} mês/meses)", datetime.now(timezone.utc).isoformat()
            )
            # Cria a notificação para o Admin (sino vermelho)
            extensions.data_manager.create_notification(
                message=_("Renovação manual de %(username)s (%(value)s) registrada.", username=user['username'], value=f"R$ {total_value:.2f}"),
                category='success', link=url_for('main.users_page')
            )
        extensions.db.session.commit()
        
        # Acende a luz do sino em tempo real
        if extensions.socketio:
            extensions.socketio.emit('new_notification', namespace='/')
            
        logger.info(f"Renovação manual processada. Nova data de expiração para '{user['username']}': {new_expiration_date.strftime('%Y-%m-%d')}. Valor registado: R$ {total_value:.2f}")
        
        try:
            extensions.media_server.notifier_manager.send_renewal_notification(user, new_expiration_date, profile)
            logger.info(f"Notificação de renovação enviada para '{user['username']}'.")
        except Exception as notify_error:
            logger.error(f"Falha ao enviar notificação de renovação para '{user['username']}': {notify_error}")

        return jsonify({"success": True, "message": _("Assinatura renovada. Novo vencimento em %(date)s.", date=new_expiration_date.strftime('%d/%m/%Y'))})
    except Exception as e:
        extensions.db.session.rollback()
        logger.error(f"Erro crítico durante a renovação manual de '{user['username']}': {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Ocorreu um erro interno ao processar a renovação.")}), 500

@users_api_bp.route('/delete-permanently', methods=['POST'])
@login_required
@admin_required
def delete_permanently_route():
    media_user_id = request.json.get('media_user_id')
    profile = extensions.data_manager.get_user_profile(media_user_id)
    if not profile or profile.get('status') != 'inactive':
        return jsonify({"success": False, "message": _("Apenas usuários inativos podem ser apagados permanentemente.")}), 400

    try:
        username = profile.get('username', 'Desconhecido')
        # Registado ANTES de apagar: depois já não há perfil de onde tirar o
        # nome, e a auditoria sem nome não serve a quem a for ler.
        audit.registar('utilizador.apagar_permanentemente', alvo_tipo='utilizador',
                       alvo_id=media_user_id, detalhes={'perfil': profile})
        extensions.data_manager.delete_user_profile(media_user_id)
        logger.info(f"Admin '{current_user.username}' apagou permanentemente o utilizador '{username}' (ID: {media_user_id}).")
        return jsonify({"success": True, "message": _("Usuário apagado permanentemente.")})
    except Exception as e:
        logger.error(f"Erro ao apagar utilizador {media_user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Erro interno ao excluir o usuário.")}), 500

@users_api_bp.route('/notify/<media_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def notify_user_route(user):
    profile = extensions.data_manager.get_user_profile(user['id'])
    if not profile.get('expiration_date'):
        return jsonify({"success": False, "message": _("Usuário sem data de vencimento.")})
    
    local_tz = get_localzone()
    exp_date = datetime.fromisoformat(profile['expiration_date']).astimezone(local_tz).date()
    days_left = (exp_date - datetime.now(local_tz).date()).days
    extensions.media_server.notifier_manager.send_expiration_notification(user, days_left, profile)
    
    logger.info(f"Admin '{current_user.username}' disparou uma notificação manual de vencimento para '{user['username']}'.")
    return jsonify({"success": True, "message": _("Notificação de vencimento enviada.")})

@users_api_bp.route('/libraries/<media_user_id>')
@login_required
@admin_required
@user_lookup_by_id
def get_user_libraries_route(user): return jsonify(extensions.media_server.get_user_libraries(user['id']))

@users_api_bp.route('/update-libraries', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def update_libraries_route(user): 
    libs = request.json.get('libraries', [])
    allow_sync = request.json.get('allow_sync')
    
    res = extensions.media_server.update_user_libraries(user['id'], libs, allow_sync=allow_sync)
    if res.get('success'): 
        logger.info(f"Admin '{current_user.username}' atualizou as bibliotecas e permissões de '{user['username']}'.")
    return jsonify(res)

@users_api_bp.route('/update-all-libraries', methods=['POST'])
@login_required
@admin_required
def update_all_libraries_route(): 
    logger.info(f"Admin '{current_user.username}' iniciou a atualização em massa de bibliotecas.")
    return jsonify(extensions.media_server.update_all_users_libraries(request.json.get('libraries')))

@users_api_bp.route('/remove', methods=['POST'])
@login_required
@admin_required
def remove_user_route(): 
    media_user_id = request.json.get('media_user_id')
    res = extensions.media_server.remove_user(media_user_id)
    if res.get('success'):
        logger.info(f"Admin '{current_user.username}' removeu/inativou um utilizador com sucesso.")
        audit.registar('utilizador.remover', alvo_tipo='utilizador', alvo_id=media_user_id)
    return jsonify(res)

@users_api_bp.route('/block', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def block_user_route(user): 
    res = extensions.media_server.block_user(user['id'], reason='manual')
    if res.get('success'):
        logger.info(f"Admin '{current_user.username}' bloqueou manualmente '{user['username']}'.")
        audit.registar('utilizador.bloquear', alvo_tipo='utilizador', alvo_id=user['id'],
                       detalhes={'username': user['username'], 'motivo': 'manual'})
    return jsonify(res)

@users_api_bp.route('/unblock', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def unblock_user_route(user): 
    res = extensions.media_server.unblock_user(user['id'])
    if res.get('success'):
        logger.info(f"Admin '{current_user.username}' desbloqueou manualmente '{user['username']}'.")
        audit.registar('utilizador.desbloquear', alvo_tipo='utilizador', alvo_id=user['id'],
                       detalhes={'username': user['username']})
    return jsonify(res)

@users_api_bp.route('/update-limit', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def update_limit_route(user):
    screens = request.json.get('screens', 0)
    # 🐛 Isto gravava só o perfil local. Nos servidores que sabem impor o limite
    # (o `MaxActiveSessions` do Jellyfin), o servidor ficava com o valor da data
    # do convite para sempre — e a única defesa que um leitor não pode ignorar
    # continuava a apontar para o plano antigo.
    anterior = (extensions.data_manager.get_user_profile(user['id']) or {}).get('screen_limit')
    extensions.media_server.update_screen_limit(user['id'], screens)
    logger.info(f"Admin '{current_user.username}' alterou limite de telas de '{user['username']}' para {screens}.")
    audit.registar('utilizador.limite_de_telas', alvo_tipo='utilizador', alvo_id=user['id'],
                   detalhes={'username': user['username'], 'antes': anterior, 'depois': screens})
    return jsonify({"success": True, "message": _("Limite aplicado.")})

@users_api_bp.route('/update-all-limits', methods=['POST'])
@login_required
@admin_required
def update_all_limits_route():
    screens = max(0, request.json.get('screens', -1))
    all_users = extensions.media_server.get_all_users() or []
    for user in all_users:
        if not same_user(user['id'], current_user.id):
            if extensions.data_manager.get_user_profile(user['id']):
                extensions.media_server.update_screen_limit(user['id'], screens)
    logger.info(f"Admin '{current_user.username}' aplicou limite global de {screens} telas para todos.")
    audit.registar('utilizador.limite_de_telas_global', alvo_tipo='utilizador',
                   detalhes={'depois': screens, 'abrangidos': len(all_users)})
    return jsonify({"success": True, "message": _("Limites atualizados para todos.")})

@users_api_bp.route('/toggle-overseerr', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def toggle_overseerr_access_route(user): 
    access = request.json.get('access', False)
    res = extensions.media_server.toggle_overseerr_access(user['id'], access)
    if res.get('success'):
        logger.info(f"Admin '{current_user.username}' alterou acesso Overseerr de '{user['username']}' para {access}.")
        audit.registar('utilizador.acesso_pedidos', alvo_tipo='utilizador', alvo_id=user['id'],
                       detalhes={'username': user['username'], 'depois': access})
    return jsonify(res)

@users_api_bp.route('/payments/<media_user_id>')
@login_required
def get_user_payments_history(media_user_id):
    # 🐛 CORREÇÃO DE SEGURANÇA: a verificação anterior era `not current_user.is_admin`.
    # Como `is_admin` é um MÉTODO, a expressão avaliava o objeto do método — sempre
    # verdadeiro — e a condição nunca era satisfeita. Na prática, qualquer utilizador
    # autenticado conseguia ler o histórico de pagamentos de qualquer outra pessoa.
    if not current_user.is_admin() and str(current_user.id) != str(media_user_id):
        return jsonify({"success": False, "message": _("Acesso não autorizado.")}), 403
    return jsonify({"success": True, "payments": extensions.data_manager.get_payments_by_user(media_user_id)})

# ==========================================
# FUNÇÕES AUXILIARES (HELPERS PRIVADOS)
# ==========================================

def _avatar_atualizado(media_user_id):
    """O avatar que o servidor tem AGORA, e atualiza a sessão se tiver mudado.

    Devolve o da sessão quando o servidor não sabe responder — é melhor um
    avatar antigo do que nenhum.
    """
    atual = current_user.thumb
    try:
        utilizador = extensions.media_server.get_user_by_id(media_user_id)
    except Exception as e:
        logger.debug(f"Não foi possível atualizar o avatar de {media_user_id}: {e}")
        return atual

    novo = (utilizador or {}).get('thumb')
    if not utilizador or novo == atual:
        return atual

    # A sessão é a fonte do cabeçalho em TODAS as páginas: sem a atualizar, a
    # "Minha Conta" mostrava a imagem nova e o resto do painel a antiga.
    detalhes = dict(session.get('user_details') or {})
    if detalhes:
        detalhes['thumb'] = novo
        session['user_details'] = detalhes

    return novo


def _get_expiration_details(profile, config):
    expiration_info = {"date": None, "days_left": None, "status": "active"}
    if exp_str := profile.get('expiration_date'):
        try:
            local_tz = get_localzone()
            exp_dt_aware = datetime.fromisoformat(exp_str)
            exp_dt_local = exp_dt_aware.astimezone(local_tz)
            expiration_info["date"] = format_date(exp_dt_local.date(), 'd \'de\' MMMM \'de\' yyyy')
            
            now_local = datetime.now(local_tz)
            if exp_dt_local < now_local:
                expiration_info["status"] = "expired"
            else:
                days_left = (exp_dt_local.date() - now_local.date()).days
                expiration_info["days_left"] = days_left
                if days_left < int(config.get("DAYS_TO_NOTIFY_EXPIRATION", 7)):
                    expiration_info["status"] = "expiring"
        except (ValueError, TypeError): 
            pass
    return expiration_info

def _sync_plex_and_local_profiles(all_plex_users_list, admin_username):
    """Junta a lista do servidor de média aos perfis locais, para os cartões.

    ⚠️ **As duas metades falam da mesma pessoa em formatos diferentes**: o Plex
    identifica as contas por um inteiro e a base de dados devolve a identidade
    sempre como texto (o tipo `UserId`). Juntá-las sem normalizar dá sempre
    falso, e sem erro nenhum: o painel conclui que cada utilizador do servidor é
    NOVO (cartão sem nome, sem vencimento, com zero telas e sem link de
    pagamento) e que cada perfil guardado já não está no servidor (marca-o
    `inactive` e reescreve-lhe o limite de telas). É o que se vê depois de
    restaurar o backup de um painel só-Plex, em que os IDs eram inteiros dos
    dois lados.

    A fachada já normaliza o que devolve; aqui normaliza-se outra vez porque é
    esta função que decide desativar perfis, e isso não pode depender de o
    backend se portar bem.
    """
    plex_user_details = {}
    for u in all_plex_users_list:
        if (media_user_id := normalize_user_id(u.get('id'))) is None:
            continue
        plex_user_details[media_user_id] = {**u, 'id': media_user_id}

    media_user_ids = set(plex_user_details.keys())
    all_profiles_from_db = extensions.data_manager.get_all_user_profiles()
    blocked_users_data = {normalize_user_id(k): v
                          for k, v in extensions.data_manager.get_blocked_users_dict().items()}
    local_profile_ids = {normalize_user_id(p.get('media_user_id')) for p in all_profiles_from_db}

    all_users_to_return = []
    profiles_to_create = []

    for plex_id, plex_data in plex_user_details.items():
        if plex_id not in local_profile_ids and plex_data['username'] != admin_username:
            new_profile_data = {
                'media_user_id': plex_id, 'username': plex_data['username'], 'email': plex_data.get('email'),
                'screen_limit': 0, 'status': 'active', 'hide_from_leaderboard': False, 'overseerr_access': False
            }
            profiles_to_create.append(new_profile_data)
            all_users_to_return.append({
                'id': plex_id, 'username': plex_data['username'], 'name': None, 'email': plex_data.get('email'),
                'thumb': plex_data.get('thumb'), 'is_blocked': False, 'status': 'active', 'screen_limit': 0,
                'expiration_date': None, 'trial_end_date': None, 'is_on_trial': False, 'payment_token': None
            })

    if profiles_to_create:
        for new_profile in profiles_to_create:
            extensions.data_manager.set_user_profile(new_profile['media_user_id'], new_profile)

    for profile in all_profiles_from_db:
        media_user_id = normalize_user_id(profile.get('media_user_id'))
        username = profile.get('username')

        if not media_user_id or username == admin_username or media_user_id not in media_user_ids:
            if profile.get('status') == 'active':
                 profile['status'] = 'inactive'
                 extensions.data_manager.set_user_profile(media_user_id, {'status': 'inactive'})
            if username != admin_username:
                 all_users_to_return.append({
                     'id': media_user_id, 'username': username, 'name': profile.get('name'), 
                     'email': profile.get('email'), 'thumb': None, 'is_blocked': media_user_id in blocked_users_data,
                     'status': 'inactive', 'screen_limit': profile.get('screen_limit', 0),
                     'expiration_date': profile.get('expiration_date'), 'trial_end_date': profile.get('trial_end_date'),
                     'is_on_trial': False, 'payment_token': profile.get('payment_token')
                 })
            continue

        plex_data = plex_user_details.get(media_user_id, {})
        if profile.get('username') != plex_data.get('username'):
              profile['username'] = plex_data.get('username')
              extensions.data_manager.set_user_profile(media_user_id, {'username': plex_data.get('username')})
              username = plex_data.get('username')

        is_blocked = media_user_id in blocked_users_data
        final_status = profile.get('status', 'inactive')
        
        if final_status == 'inactive' and not is_blocked:
              final_status = 'active'
              extensions.data_manager.set_user_profile(media_user_id, {'status': 'active'})

        is_on_trial = False
        if trial_end_date_str := profile.get('trial_end_date'):
            try:
                if datetime.fromisoformat(trial_end_date_str) > datetime.now(timezone.utc):
                    is_on_trial = True
            except (ValueError, TypeError): pass

        # 🐛 Um perfil vindo de um painel antigo pode não ter `payment_token`: a
        # coluna nasceu depois dele e só é preenchida quando o perfil é gravado.
        # Sem ele, o "copiar link de pagamento" responde que a pessoa não tem
        # link nenhum. Gravar o perfil gera-o (é o `set_user_profile` que o faz),
        # e isto acontece uma vez só — na visita seguinte já lá está.
        payment_token = profile.get('payment_token')
        if not payment_token:
            atualizado = extensions.data_manager.set_user_profile(media_user_id, {}) or {}
            payment_token = atualizado.get('payment_token')

        user_data = {
            'id': media_user_id, 'username': username, 'name': profile.get('name'),
            'email': plex_data.get('email', profile.get('email')), 'thumb': plex_data.get('thumb'),
            'is_blocked': is_blocked, 'status': final_status, 'screen_limit': profile.get('screen_limit', 0),
            'expiration_date': profile.get('expiration_date'), 'trial_end_date': profile.get('trial_end_date'),
            'is_on_trial': is_on_trial, 'payment_token': payment_token
        }
        
        existing_index = next((i for i, u in enumerate(all_users_to_return) if same_user(u['id'], media_user_id)), -1)
        if existing_index != -1:
            all_users_to_return[existing_index] = user_data
        else:
            all_users_to_return.append(user_data)

    return all_users_to_return

def _momento_do_vencimento(local_datetime_str, config):
    """O instante que o administrador escolheu, lido no fuso DELE.

    🐛 **O fuso de quem escolhe não é o do servidor.** O formulário manda a
    hora de parede (`2026-09-05T23:59`) e isto fazia
    `datetime.fromisoformat(...)`, que devolve uma data INGÉNUA: o
    `astimezone(timezone.utc)` que vinha a seguir assume o fuso do SISTEMA.
    Num contentor sem `TZ` definido — o padrão do Docker — isso é UTC, e um
    administrador no Brasil que escolhesse as 23:59 ficava com o vencimento
    às 20:59 dele.

    E deslizava a cada gravação, sempre no mesmo sentido: ao reabrir, o modal
    faz `new Date(expiration_date)` e mostra as 20:59 (o navegador lê o
    `+00:00` e converte para o fuso de quem olha); gravar outra vez sem tocar
    em nada escrevia as 17:59. Medido, com o servidor em UTC e o painel aberto
    no Brasil:

        volta 1: guardado 23:59Z  ->  o campo mostra 20:59
        volta 2: guardado 20:59Z  ->  o campo mostra 17:59
        volta 3: guardado 17:59Z  ->  o campo mostra 14:59

    Hoje o navegador manda o deslocamento (`comDeslocamentoLocal`, em
    `utils.js`) e o instante é inequívoco — deixa de depender de o `TZ` do
    contentor coincidir com o de quem está a clicar.

    ⚠️ **Uma data SEM deslocamento continua a ser aceite**, e continua a ser
    lida no fuso do painel: é o que chega de um navegador com o JavaScript
    antigo em cache, e recusá-la trocaria um erro de três horas por um erro
    a gravar.
    """
    fuso_do_painel = get_app_timezone()
    escolhido = datetime.fromisoformat(local_datetime_str)

    if escolhido.tzinfo is None:
        # ⚠️ `pytz` não se usa com `tzinfo=`: ali ele dá o deslocamento da
        # ÉPOCA (LMT), que em São Paulo são -03:06. `localize` é a porta.
        escolhido = fuso_do_painel.localize(escolhido)

    if config.get("UNIVERSAL_EXPIRATION_ENABLED"):
        try:
            hora, minuto = (int(p) for p in config.get("UNIVERSAL_EXPIRATION_TIME", "23:59").split(':'))
        except (ValueError, IndexError):
            pass
        else:
            # ⚠️ A hora universal é do PAINEL, não de quem está a clicar: é a
            # mesma que os `CronTrigger` do agendador usam, e a definição é
            # "bloquear toda a gente às 23:59" — uma hora só, não uma por
            # administrador. O DIA continua a ser o escolhido, que é a única
            # coisa que o formulário deixa escolher quando isto está ligado
            # (o campo da hora aparece desativado).
            escolhido = fuso_do_painel.localize(
                datetime(escolhido.year, escolhido.month, escolhido.day, hora, minuto))

    return escolhido


def _update_manual_expiration_job(media_user_id, username, profile_to_update, local_datetime_str):
    from ...extensions import scheduler
    from ...scheduler import end_subscription_job

    if local_datetime_str:
        if profile_to_update.get('trial_end_date'):
            profile_to_update['trial_end_date'] = None
        if profile_to_update.get('trial_job_id'):
            try: scheduler.remove_job(profile_to_update['trial_job_id'])
            except JobLookupError: pass
            profile_to_update['trial_job_id'] = None

    if not local_datetime_str:
        profile_to_update['expiration_date'] = None
        if old_job_id := profile_to_update.pop('expiration_job_id', None):
            try: scheduler.remove_job(old_job_id)
            except JobLookupError: pass
    else:
        escolhido = _momento_do_vencimento(local_datetime_str, load_or_create_config())
        instante_utc = escolhido.astimezone(timezone.utc)

        if old_job_id := profile_to_update.pop('expiration_job_id', None):
            try: scheduler.remove_job(old_job_id)
            except JobLookupError: pass

        new_job_id = f"sub_end_{media_user_id}_{secrets.token_hex(4)}"
        # O agendador recebe uma data INGÉNUA no fuso dele, como o
        # `extend_trial_route` já fazia: a conversão acontece aqui, uma vez,
        # a partir de um instante que já não é ambíguo.
        run_date = instante_utc.astimezone(scheduler.timezone).replace(tzinfo=None)
        scheduler.add_job(id=new_job_id, func=end_subscription_job, args=[media_user_id], trigger='date', run_date=run_date, misfire_grace_time=3600)

        profile_to_update['expiration_date'] = instante_utc.isoformat()
        profile_to_update['expiration_job_id'] = new_job_id

        # 🐛 CORREÇÃO: ao definir a data de vencimento MANUALMENTE, é preciso mover
        # também a âncora do dia de faturação. Caso contrário, a âncora antiga
        # continuava a mandar nas renovações seguintes e "puxava" o vencimento de
        # volta para o dia antigo — por exemplo, definir 05/09 e, ao renovar, obter
        # 26/10 em vez de 05/10, porque o billing_day ainda era 26.
        # Uma data definida à mão pelo administrador é uma decisão explícita e deve
        # passar a ser a nova referência.
        #
        # ⚠️ O dia é o que a PESSOA escolheu, não o que dá no fuso do painel.
        # Um vencimento a 05/09 às 23:59 no Brasil é 06/09 às 02:59 em UTC:
        # ancorar a faturação no 6 mudava o dia da cobrança de toda a gente que
        # escolhesse uma hora depois das 21:00.
        profile_to_update['billing_day'] = escolhido.day

def _enforce_user_status_by_date(media_user_id, username, profile_to_update):
    is_blocked = extensions.data_manager.get_blocked_user(media_user_id) is not None
    now_utc = datetime.now(timezone.utc)
    new_status = 'active'

    if exp_date_str := profile_to_update.get('expiration_date'):
        exp_date_utc = datetime.fromisoformat(exp_date_str).astimezone(timezone.utc)
        if exp_date_utc <= now_utc: new_status = 'expired'
    elif trial_end_str := profile_to_update.get('trial_end_date'):
        trial_end_utc = datetime.fromisoformat(trial_end_str).astimezone(timezone.utc)
        if trial_end_utc <= now_utc: new_status = 'trial_expired'

    if new_status != 'active':
        current_block_info = extensions.data_manager.get_blocked_user(media_user_id)
        if not current_block_info or current_block_info.get('block_reason') != new_status:
            extensions.media_server.block_user(media_user_id, reason=new_status)
    elif is_blocked:
        block_reason = extensions.data_manager.get_blocked_user(media_user_id).get('block_reason')
        if block_reason in ['expired', 'trial_expired']:
            extensions.media_server.unblock_user(media_user_id)


# ==========================================
# SISTEMA DE REFERÊNCIA ("INDIQUE E GANHE")
# ==========================================

@users_api_bp.route('/referral/me', methods=['GET'])
@login_required
def get_my_referral_info():
    """
    Devolve o código, o link e as estatísticas de indicação do utilizador
    autenticado. O código é gerado na primeira vez que esta rota é chamada.
    """
    config = load_or_create_config()
    if not config.get("REFERRAL_ENABLED", False):
        return jsonify({"success": True, "enabled": False})

    try:
        media_user_id = normalize_user_id(current_user.id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": _("Usuário inválido.")}), 400

    code = extensions.referral_manager.get_or_create_code(media_user_id)
    stats = extensions.referral_manager.get_referral_stats(media_user_id)

    # O link aponta para a página pública de convite, com o código anexado.
    base_url = (config.get("APP_BASE_URL") or request.host_url).rstrip('/')
    stats['link'] = f"{base_url}/r/{code}" if code else None

    return jsonify({"success": True, **stats})


@users_api_bp.route('/referral/claim', methods=['POST'])
@login_required
@limiter.limit("10 per hour", override_defaults=False)
def claim_referral_code():
    """
    Regista que o utilizador autenticado foi indicado por alguém. Só tem efeito
    uma vez por utilizador; a recompensa só é paga quando ele efetuar a primeira
    compra (ver ReferralManager.reward_referrer_on_payment).
    """
    data = request.json or {}
    code = str(data.get('code', '')).strip()

    try:
        media_user_id = normalize_user_id(current_user.id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": _("Usuário inválido.")}), 400

    result = extensions.referral_manager.register_referral(media_user_id, code)
    return jsonify(result), (200 if result.get('success') else 400)
