# app/blueprints/api/users.py

import logging
import secrets
from datetime import date, datetime, timezone, timedelta # Adicionado timedelta
from flask import Blueprint, jsonify, request, url_for, current_app
from flask_login import current_user
from flask_babel import gettext as _, format_date
from tzlocal import get_localzone
from apscheduler.jobstores.base import JobLookupError
from pydantic import ValidationError, BaseModel, Field # Adicionado BaseModel, Field
import json
import time

from ... import extensions
from ...config import load_or_create_config
from ..auth import admin_required, login_required
from .decorators import user_lookup_by_id, validate_json
from .schemas import RenewSubscriptionSchema, UpdateProfileSchema, UpdateAccountProfileSchema
from ...models import UserProfile

logger = logging.getLogger(__name__)
users_api_bp = Blueprint('users_api', __name__)

# --- NOVO: Schema para validação da extensão do teste ---
class ExtendTrialSchema(BaseModel):
    extend_minutes: int = Field(..., gt=0, description="Duração da extensão em minutos (deve ser maior que zero).")
# --- FIM NOVO ---

@users_api_bp.route('/public-profile-by-token/<string:token>')
def get_public_user_profile_by_token(token):
    profile = UserProfile.query.filter_by(payment_token=token).first()
    if not profile:
        return jsonify({"success": False, "message": _("Link de pagamento inválido ou usuário não encontrado.")}), 404

    user_thumb = None
    username = profile.username

    # Para utilizadores ativos, tenta obter os dados mais recentes do Plex
    if profile.status == 'active':
        user = extensions.plex_manager.get_user_by_id(profile.plex_user_id)
        if not user:
            logger.warning(f"Utilizador ativo '{username}' (ID: {profile.plex_user_id}) não encontrado no Plex. A tratar como inativo para a página pública.")
        else:
            user_thumb = user.get('thumb')
    # Para utilizadores inativos, confiamos nos dados armazenados na nossa base de dados.
    # O avatar pode não estar disponível, o que é uma limitação aceitável.
    else: # 'inactive' ou qualquer outro estado
        logger.info(f"A gerar perfil público para o utilizador inativo '{username}' a partir dos dados da base de dados.")

    expiration_date_formatted = None
    if profile.expiration_date:
        try:
            exp_date = datetime.fromisoformat(profile.expiration_date)
            expiration_date_formatted = exp_date.strftime('%d/%m/%Y')
        except (ValueError, TypeError): pass

    public_data = {
        "username": username,
        "thumb": user_thumb, # Será None para utilizadores inativos
        "expiration_date_formatted": expiration_date_formatted,
        "expiration_date_iso": profile.expiration_date
    }
    return jsonify({"success": True, "profile": public_data})

@users_api_bp.route('/status')
@login_required
@admin_required
def get_status():
    """
    Obtém uma lista consolidada de utilizadores, usando a base de dados local como fonte da verdade
    e enriquecendo-a com dados do Plex. Também auto-corrige status inconsistentes e cria perfis básicos
    para utilizadores encontrados no Plex mas ausentes localmente.
    """
    if not extensions.plex_manager.conn.plex:
        return jsonify({"error": _("Plex não configurado.")}), 500

    force_refresh = request.args.get('force', 'false').lower() == 'true'

    # 1. Obter dados do Plex para enriquecimento e verificação
    all_plex_users_list = extensions.plex_manager.get_all_plex_users(force_refresh=force_refresh)
    if all_plex_users_list is None:
        logger.warning("Não foi possível obter a lista de utilizadores do Plex. A mostrar dados da base de dados local.")
        all_plex_users_list = []

    config = load_or_create_config()
    admin_username = config.get('ADMIN_USER')

    # Mapas/conjuntos para acesso rápido
    plex_user_details = {u['id']: u for u in all_plex_users_list}
    plex_user_ids = set(plex_user_details.keys())

    # 2. Obter todos os perfis da base de dados local
    all_profiles_from_db = extensions.data_manager.get_all_user_profiles()
    blocked_users_data = extensions.data_manager.get_blocked_users_dict()
    local_profile_ids = {p.get('plex_user_id') for p in all_profiles_from_db} # Get IDs from local DB

    all_users_to_return = []
    profiles_to_update = []
    profiles_to_create = [] # Lista para rastrear perfis que precisam ser criados

    # --- INÍCIO DA NOVA LÓGICA: Verifica utilizadores no Plex mas não localmente ---
    for plex_id, plex_data in plex_user_details.items():
        if plex_id not in local_profile_ids and plex_data['username'] != admin_username:
            # Utilizador existe no Plex mas não na nossa BD, e não é o admin
            logger.info(f"Utilizador '{plex_data['username']}' (ID: {plex_id}) encontrado no Plex mas sem perfil local. A criar perfil básico.")
            # Prepara dados básicos do perfil
            new_profile_data = {
                'plex_user_id': plex_id,
                'username': plex_data['username'],
                'email': plex_data.get('email'),
                'screen_limit': 0, # Limite de ecrãs padrão
                'status': 'active', # Assume como ativo inicialmente
                # Adiciona outros padrões necessários se o modelo UserProfile exigir
                'hide_from_leaderboard': False,
                'overseerr_access': False,
                # 'payment_token' será gerado por set_user_profile se necessário
            }
            profiles_to_create.append(new_profile_data)
            # Adiciona este utilizador à lista a ser retornada imediatamente, assumindo 'active' por agora
            all_users_to_return.append({
                'id': plex_id,
                'username': plex_data['username'],
                'email': plex_data.get('email'),
                'thumb': plex_data.get('thumb'),
                'is_blocked': False, # Assume não bloqueado inicialmente
                'status': 'active',
                'screen_limit': 0,
                'expiration_date': None,
                'trial_end_date': None,
                'is_on_trial': False,
                'payment_token': None # Será gerado mais tarde
            })
            local_profile_ids.add(plex_id) # Adiciona ao conjunto para evitar processamento duplicado abaixo
    # --- FIM DA NOVA LÓGICA ---

    # 3. Iterar sobre os perfis da base de dados local (utilizadores existentes)
    for profile in all_profiles_from_db:
        plex_user_id = profile.get('plex_user_id')
        username = profile.get('username')

        # Pula se já processado acima, é admin, ou não está mais no Plex
        if not plex_user_id or username == admin_username or plex_user_id not in plex_user_ids:
            # Autocorreção: Lida com utilizadores apenas na BD local (inativos)
            if profile.get('status') == 'active':
                 logger.info(f"Utilizador '{username}' (ID: {plex_user_id}) está ativo localmente mas não foi encontrado no Plex. A marcar como inativo.")
                 profile['status'] = 'inactive'
                 profiles_to_update.append({'id': plex_user_id, 'data': {'status': 'inactive'}})
            # Adiciona utilizador inativo à lista se não for admin
            if username != admin_username:
                 all_users_to_return.append({
                     'id': plex_user_id,
                     'username': username,
                     'email': profile.get('email'),
                     'thumb': None, # Sem miniatura se não estiver no Plex
                     'is_blocked': plex_user_id in blocked_users_data,
                     'status': 'inactive',
                     'screen_limit': profile.get('screen_limit', 0),
                     'expiration_date': profile.get('expiration_date'),
                     'trial_end_date': profile.get('trial_end_date'),
                     'is_on_trial': False, # Utilizadores inativos não podem estar em teste
                     'payment_token': profile.get('payment_token')
                 })
            continue # Pula o processamento adicional para utilizadores não encontrados no Plex

        is_on_plex = True # Sabemos que estão no plex se chegarmos aqui
        plex_data = plex_user_details.get(plex_user_id, {}) # Deveria sempre existir aqui

        # Autocorreção: Se o nome de utilizador não corresponder, atualiza o nome local
        if profile.get('username') != plex_data.get('username'):
              logger.warning(f"Inconsistência de username detectada para ID {plex_user_id}. Plex: '{plex_data.get('username')}', Local: '{profile.get('username')}'. A atualizar localmente.")
              profile['username'] = plex_data.get('username')
              profiles_to_update.append({'id': plex_user_id, 'data': {'username': plex_data.get('username')}})
              # Atualiza o nome de utilizador para o resto desta execução da função
              username = plex_data.get('username')

        is_blocked = plex_user_id in blocked_users_data
        final_status = profile.get('status', 'inactive') # Padrão para inativo se o status estiver em falta de alguma forma

        # Autocorreção: Se o status for inativo mas o utilizador for encontrado no Plex, marca como ativo (a menos que esteja bloqueado)
        if final_status == 'inactive' and not is_blocked:
              logger.info(f"Utilizador '{username}' (ID: {plex_user_id}) está inativo localmente mas foi encontrado no Plex e não está bloqueado. A marcar como ativo.")
              final_status = 'active'
              profiles_to_update.append({'id': plex_user_id, 'data': {'status': 'active'}})


        is_on_trial = False
        if trial_end_date_str := profile.get('trial_end_date'):
            try:
                # Modificação: Usa timezone.utc para comparação consistente
                if datetime.fromisoformat(trial_end_date_str) > datetime.now(timezone.utc):
                    is_on_trial = True
            except (ValueError, TypeError): pass

        # 4. Construir o objeto do utilizador, combinando dados
        user_data = {
            'id': plex_user_id,
            'username': username, # Usa nome de utilizador potencialmente atualizado
            'email': plex_data.get('email', profile.get('email')), # Prefere email do Plex
            'thumb': plex_data.get('thumb'),
            'is_blocked': is_blocked,
            'status': final_status, # Usa status potencialmente atualizado
            'screen_limit': profile.get('screen_limit', 0),
            'expiration_date': profile.get('expiration_date'),
            'trial_end_date': profile.get('trial_end_date'),
            'is_on_trial': is_on_trial,
            'payment_token': profile.get('payment_token')
        }
        # Verifica se o utilizador já foi adicionado durante a fase de 'criação', se sim, atualiza em vez de anexar
        existing_user_index = next((i for i, u in enumerate(all_users_to_return) if u['id'] == plex_user_id), -1)
        if existing_user_index != -1:
            all_users_to_return[existing_user_index] = user_data
        else:
            all_users_to_return.append(user_data) # Anexa apenas se não foi adicionado durante a fase de criação


    # --- INÍCIO DA NOVA LÓGICA: Cria perfis ausentes ---
    if profiles_to_create:
        logger.info(f"A criar {len(profiles_to_create)} perfil(s) de utilizador ausente(s).")
        for new_profile in profiles_to_create:
            # Gera token de pagamento aqui se necessário, ou deixa set_user_profile lidar com isso
            extensions.data_manager.set_user_profile(new_profile['plex_user_id'], new_profile)
    # --- FIM DA NOVA LÓGICA ---

    # 5. Executar as atualizações de autocorreção, se necessário
    if profiles_to_update:
        logger.info(f"A executar {len(profiles_to_update)} atualização(ões) de status/username de autocorreção.")
        for update in profiles_to_update:
            extensions.data_manager.set_user_profile(update['id'], update['data'])

    return jsonify({
        'users': sorted(all_users_to_return, key=lambda u: u['username'].lower()),
        'libraries': extensions.plex_manager.conn.get_libraries()
    })


@users_api_bp.route('/account/details')
@login_required
def get_account_details():
    config = load_or_create_config()
    plex_user_id = int(current_user.id)
    profile = extensions.data_manager.get_user_profile(plex_user_id)

    is_blocked_info = extensions.data_manager.get_blocked_user(plex_user_id)
    is_blocked = is_blocked_info is not None
    block_reason = is_blocked_info.get('block_reason') if is_blocked_info else None

    expiration_info = {"date": None, "days_left": None, "status": "active"}
    if exp_str := profile.get('expiration_date'):
        try:
            exp_dt_aware = datetime.fromisoformat(exp_str)
            exp_dt_local = exp_dt_aware.astimezone(get_localzone())

            expiration_info["date"] = format_date(exp_dt_local.date(), 'd \'de\' MMMM \'de\' yyyy')

            now_local = datetime.now(get_localzone())

            if exp_dt_local < now_local:
                expiration_info["status"] = "expired"
            else:
                days_left = (exp_dt_local.date() - now_local.date()).days
                expiration_info["days_left"] = days_left
                if days_left < int(config.get("DAYS_TO_NOTIFY_EXPIRATION", 7)):
                    expiration_info["status"] = "expiring"
        except (ValueError, TypeError):
            pass

    join_date = _("Não disponível")
    if join_date_str := extensions.data_manager.get_user_claim_date(plex_user_id):
        try: join_date = format_date(datetime.fromisoformat(join_date_str), 'd \'de\' MMMM \'de\' yyyy')
        except (ValueError, TypeError): pass

    libraries_data = extensions.plex_manager.get_user_libraries(plex_user_id)
    watch_data = extensions.tautulli_manager.get_user_watch_details(plex_user_id=plex_user_id, current_user=current_user)

    notification_settings = {
        "telegram_enabled": config.get("TELEGRAM_ENABLED", False),
        "discord_enabled": config.get("DISCORD_ENABLED", False),
        "webhook_enabled": config.get("WEBHOOK_ENABLED", False)
    }

    # --- NOVO: Adiciona a informação se o utilizador está em trial ---
    is_on_trial = False
    trial_end_date_iso = profile.get('trial_end_date')
    if trial_end_date_iso:
        try:
            if datetime.fromisoformat(trial_end_date_iso) > datetime.now(timezone.utc):
                is_on_trial = True
        except (ValueError, TypeError): pass
    # --- FIM NOVO ---

    details = {
        "success": True, "username": current_user.username, "email": current_user.email, "thumb": current_user.thumb,
        "join_date": join_date, "screen_limit": _("%(num)d Tela(s)", num=profile.get('screen_limit', 0)) if profile.get('screen_limit', 0) > 0 else _("Ilimitado"),
        "libraries": libraries_data.get('libraries', []), "watch_stats": watch_data.get('details', {}),
        "expiration_info": expiration_info, "is_blocked": is_blocked, "block_reason": block_reason,
        "trial_end_date": trial_end_date_iso, # Alterado para enviar ISO
        "is_on_trial": is_on_trial, # NOVO: Envia a flag
        "hide_from_leaderboard": profile.get('hide_from_leaderboard', False),
        "notification_settings": notification_settings,
        "profile_details": { "name": profile.get("name"), "telegram_user": profile.get("telegram_user"), "discord_user_id": profile.get("discord_user_id"), "phone_number": profile.get("phone_number"), "overseerr_access": profile.get("overseerr_access", False) }
    }
    return jsonify(details)

@users_api_bp.route('/account/profile', methods=['POST'])
@login_required
@validate_json(UpdateAccountProfileSchema)
def update_account_profile(validated_data):
    data = validated_data.dict(exclude_unset=True)
    plex_user_id = int(current_user.id)
    profile = extensions.data_manager.get_user_profile(plex_user_id)
    profile.update(data)
    extensions.data_manager.set_user_profile(plex_user_id, profile)
    logger.info(f"Utilizador '{current_user.username}' atualizou o seu próprio perfil.")
    return jsonify({"success": True, "message": _("Perfil atualizado com sucesso.")})

@users_api_bp.route('/account/privacy', methods=['POST'])
@login_required
def update_privacy_settings():
    hide_setting = request.json.get('hide')
    if not isinstance(hide_setting, bool): return jsonify({"success": False, "message": _("Valor inválido.")}), 400
    plex_user_id = int(current_user.id)
    profile = extensions.data_manager.get_user_profile(plex_user_id)
    profile['hide_from_leaderboard'] = hide_setting
    extensions.data_manager.set_user_profile(plex_user_id, profile)
    logger.info(f"Utilizador '{current_user.username}' atualizou as suas configurações de privacidade para {'oculto' if hide_setting else 'visível'}.")
    return jsonify({"success": True, "message": _("Configuração de privacidade atualizada com sucesso.")})

@users_api_bp.route('/account/requests')
@login_required
def get_account_requests():
    filter_status = request.args.get('filter', 'all', type=str)
    if filter_status not in ['all', 'approved', 'available', 'pending', 'processing', 'declined']: filter_status = 'all'
    if not extensions.overseerr_manager.enabled: return jsonify({"success": True, "requests": [], "overseerr_disabled": True})
    return jsonify(extensions.overseerr_manager.get_user_requests(current_user.email, limit=20, filter=filter_status))

@users_api_bp.route('/renew/<int:plex_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
@validate_json(RenewSubscriptionSchema)
def renew_user_subscription_route(user, validated_data):
    try:
        data = validated_data
        # 1. Executa a lógica principal de renovação da assinatura
        new_expiration_date = extensions.plex_manager.renew_subscription(
            user['id'], data.months, base_mode=data.base,
            base_date_str=data.base_date, expiration_time_str=data.expiration_time
        )

        # 2. Prepara dados para o registo de pagamento manual
        config = load_or_create_config()
        profile = extensions.data_manager.get_user_profile(user['id']) # Obtém perfil atualizado
        monthly_price_str = config.get("SCREEN_PRICES", {}).get(
            str(profile.get('screen_limit', 0)), config.get("RENEWAL_PRICE", "0.00")
        )
        total_value = float(monthly_price_str.replace(',', '.')) * data.months

        # 3. Adiciona registo de pagamento manual e notificação de admin numa transação
        #   Usa begin_nested para potenciais rollbacks parciais se necessário, embora commit trate da transação principal.
        with extensions.db.session.begin_nested():
            extensions.data_manager.add_manual_payment(
                user['id'], user['username'], total_value,
                f"Renovação Admin (+{data.months} mês/meses)", datetime.now().isoformat()
            )
            # Adiciona notificação para o admin sobre a renovação manual
            extensions.data_manager.create_notification(
                message=_("Renovação manual de %(username)s (%(value)s) registada.",
                          username=user['username'], value=f"R$ {total_value:.2f}"),
                category='success',
                link=url_for('main.users_page') # Link para o admin verificar a página de utilizadores
            )

        # 4. Confirma as alterações na base de dados *antes* de enviar notificação externa
        extensions.db.session.commit()
        logger.info(f"Admin '{current_user.username}' renovou a subscrição de '{user['username']}' por {data.months} mes(es). Base de dados confirmada.")

        # 5. Envia notificação para o utilizador (melhor esforço - não falha a requisição inteira se isto falhar)
        try:
            # Re-busca o perfil caso algo crítico tenha mudado, embora improvável aqui
            refreshed_profile_for_notification = extensions.data_manager.get_user_profile(user['id'])
            extensions.plex_manager.notifier_manager.send_renewal_notification(
                user, new_expiration_date, refreshed_profile_for_notification
            )
            logger.info(f"Notificação de renovação enviada para '{user['username']}'.")
        except Exception as notify_error:
            # Regista o erro mas não causa uma resposta 500, pois a renovação principal teve sucesso.
            logger.error(f"Falha ao enviar notificação de renovação para '{user['username']}': {notify_error}", exc_info=True)
            # Opcionalmente, informa o admin via mensagem flash ou outra notificação que a notificação do utilizador falhou
            # flash(_("Renovação bem-sucedida, mas falha ao enviar notificação para o usuário."), "warning")

        # 6. Retorna resposta de sucesso
        return jsonify({
            "success": True,
            "message": _("Subscrição renovada. Novo vencimento em %(date)s.",
                         date=new_expiration_date.strftime('%d/%m/%Y'))
        })

    except Exception as e:
        # Rollback em caso de qualquer erro durante o processo
        extensions.db.session.rollback()
        logger.error(f"Erro CRÍTICO ao processar renovação manual para '{user.get('username', 'ID:'+str(plex_user_id))}': {e}", exc_info=True)
        # Retorna uma mensagem de erro genérica por segurança
        return jsonify({"success": False, "message": _("Ocorreu um erro interno ao processar a renovação.")}), 500

@users_api_bp.route('/profile/<int:plex_user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def user_profile_route(plex_user_id):
    user_info = extensions.plex_manager.get_user_by_id(plex_user_id)
    if not user_info:
        return jsonify({"success": False, "message": "Utilizador não encontrado."}), 404

    username = user_info['username']

    if request.method == 'GET':
        profile = extensions.data_manager.get_user_profile(plex_user_id)
        config = load_or_create_config()
        # --- NOVO: Adiciona a flag is_on_trial ---
        is_on_trial = False
        trial_end_date_iso = profile.get('trial_end_date')
        if trial_end_date_iso:
            try:
                if datetime.fromisoformat(trial_end_date_iso) > datetime.now(timezone.utc):
                    is_on_trial = True
            except (ValueError, TypeError): pass
        # --- FIM NOVO ---
        return jsonify({
            "success": True, "profile": profile,
            "is_on_trial": is_on_trial, # NOVO: Envia a flag
            "notification_settings": {"telegram_enabled": config.get("TELEGRAM_ENABLED", False), "discord_enabled": config.get("DISCORD_ENABLED", False), "webhook_enabled": config.get("WEBHOOK_ENABLED", False)},
            "universal_expiration_settings": {"enabled": config.get("UNIVERSAL_EXPIRATION_ENABLED", False), "time": config.get("UNIVERSAL_EXPIRATION_TIME", "23:59")}
        })

    if request.method == 'POST':
        from ...extensions import scheduler
        from ...scheduler import end_subscription_job, end_trial_job # Importa end_trial_job

        try: validated_data = UpdateProfileSchema(**request.json)
        except ValidationError as e: return jsonify({"success": False, "message": "Dados inválidos.", "errors": {err['loc'][0]: err['msg'] for err in e.errors()}}), 400

        data = validated_data.dict(exclude_unset=True)
        local_datetime_str = data.pop('expiration_datetime_local', None)

        profile_to_update = extensions.data_manager.get_user_profile(plex_user_id)
        profile_to_update.update(data)

        # --- CORREÇÃO: Limpa os dados de teste se uma data de expiração regular for definida ---
        if local_datetime_str:
            if 'trial_end_date' in profile_to_update and profile_to_update['trial_end_date']:
                profile_to_update['trial_end_date'] = None
                logger.info(f"Data de fim de teste limpa para '{username}' ao definir data de expiração regular.")
            if 'trial_job_id' in profile_to_update and profile_to_update['trial_job_id']:
                try:
                    scheduler.remove_job(profile_to_update['trial_job_id'])
                    logger.info(f"Tarefa de teste '{profile_to_update['trial_job_id']}' removida para '{username}'.")
                except JobLookupError:
                    logger.warning(f"Não foi possível encontrar a tarefa de teste '{profile_to_update['trial_job_id']}' para remover para '{username}'.")
                profile_to_update['trial_job_id'] = None
        # --- FIM DA CORREÇÃO ---


        if not local_datetime_str:
            profile_to_update['expiration_date'] = None
            if old_job_id := profile_to_update.pop('expiration_job_id', None):
                try:
                    scheduler.remove_job(old_job_id)
                    logger.info(f"Tarefa de bloqueio de subscrição para '{username}' removida (ID: {old_job_id}).")
                except JobLookupError:
                    logger.warning(f"Não foi possível encontrar a tarefa de bloqueio de subscrição '{old_job_id}' para remover para '{username}'.")
        else:
            naive_dt = datetime.fromisoformat(local_datetime_str)
            config = load_or_create_config()
            if config.get("UNIVERSAL_EXPIRATION_ENABLED"):
                try:
                    time_parts = list(map(int, config.get("UNIVERSAL_EXPIRATION_TIME", "23:59").split(':')))
                    naive_dt = naive_dt.replace(hour=time_parts[0], minute=time_parts[1], second=0, microsecond=0)
                except (ValueError, IndexError): pass

            # Remove a tarefa de expiração antiga (subscrição), se existir
            if old_job_id := profile_to_update.pop('expiration_job_id', None):
                try: scheduler.remove_job(old_job_id)
                except JobLookupError: pass

            # Agenda a nova tarefa de expiração de subscrição
            new_job_id = f"sub_end_{plex_user_id}_{secrets.token_hex(4)}"
            scheduler.add_job(id=new_job_id, func=end_subscription_job, args=[plex_user_id], trigger='date', run_date=naive_dt, misfire_grace_time=3600)

            profile_to_update['expiration_date'] = naive_dt.astimezone(get_localzone()).isoformat()
            profile_to_update['expiration_job_id'] = new_job_id
            logger.info(f"Tarefa de bloqueio de subscrição para '{username}' reagendada para {naive_dt.strftime('%Y-%m-%d %H:%M:%S')} com ID '{new_job_id}'.")

        extensions.data_manager.set_user_profile(plex_user_id, profile_to_update)
        logger.info(f"Admin '{current_user.username}' atualizou o perfil de '{username}'.")

        is_blocked = extensions.data_manager.get_blocked_user(plex_user_id) is not None
        now_utc = datetime.now(timezone.utc)

        # --- Lógica de Sincronização de Status (Bloqueio/Desbloqueio) ---
        new_status = 'active' # Assume ativo por padrão

        # 1. Verifica Expiração de Assinatura (TEM PRIORIDADE)
        if exp_date_str := profile_to_update.get('expiration_date'):
            exp_date_utc = datetime.fromisoformat(exp_date_str).astimezone(timezone.utc)
            if exp_date_utc <= now_utc:
                new_status = 'expired' # Prioridade máxima se assinatura expirou

        # 2. Verifica Expiração de Teste (APENAS se não houver assinatura expirada)
        elif trial_end_str := profile_to_update.get('trial_end_date'):
            trial_end_utc = datetime.fromisoformat(trial_end_str).astimezone(timezone.utc)
            if trial_end_utc <= now_utc:
                new_status = 'trial_expired' # Prioridade se teste expirou

        # 3. Aplica a ação (Bloquear ou Desbloquear)
        if new_status != 'active': # Se expirou (assinatura ou teste)
            # Bloqueia apenas se não estiver bloqueado ou se o motivo for diferente
            current_block_info = extensions.data_manager.get_blocked_user(plex_user_id)
            if not current_block_info or current_block_info.get('block_reason') != new_status:
                logger.info(f"A bloquear utilizador '{username}' automaticamente devido a: {new_status}")
                extensions.plex_manager.block_user(plex_user_id, reason=new_status)
        elif is_blocked: # Se deveria estar ativo, mas está bloqueado
            # Desbloqueia apenas se o motivo foi 'expired' ou 'trial_expired'
            block_reason = extensions.data_manager.get_blocked_user(plex_user_id).get('block_reason')
            if block_reason in ['expired', 'trial_expired']:
                logger.info(f"A desbloquear utilizador '{username}' automaticamente.")
                extensions.plex_manager.unblock_user(plex_user_id)
            # Não desbloqueia se foi bloqueio manual ('manual')
            else:
                 logger.info(f"Utilizador '{username}' permanece bloqueado manualmente pelo admin.")

        return jsonify({"success": True, "message": _("Perfil do utilizador atualizado com sucesso.")})


# --- Endpoint para estender o período de teste ---
@users_api_bp.route('/extend-trial/<int:plex_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
@validate_json(ExtendTrialSchema)
def extend_trial_route(user, validated_data):
    from ...extensions import scheduler
    from ...scheduler import end_trial_job

    plex_user_id = user['id']
    username = user['username']
    extend_minutes = validated_data.extend_minutes

    profile = extensions.data_manager.get_user_profile(plex_user_id)
    trial_end_date_str = profile.get('trial_end_date')

    # CORREÇÃO: Permite estender mesmo se não houver data de teste (inicia a partir de agora)
    # if not trial_end_date_str:
    #     return jsonify({"success": False, "message": _("Este utilizador não tem um período de teste associado.")}), 400

    try:
        now_utc = datetime.now(timezone.utc)
        current_trial_end_utc = now_utc # Base padrão é agora

        if trial_end_date_str:
            try:
                current_trial_end_utc = datetime.fromisoformat(trial_end_date_str)
            except (ValueError, TypeError):
                 logger.warning(f"Formato inválido para trial_end_date ('{trial_end_date_str}') para {username}. A estender a partir de agora.")
                 current_trial_end_utc = now_utc

        # --- LÓGICA ALTERADA ---
        # Se o teste já expirou, a base para extensão é AGORA.
        # Se ainda está ativo, a base é a data de fim atual.
        base_time = max(current_trial_end_utc, now_utc)
        new_trial_end_utc = base_time + timedelta(minutes=extend_minutes)
        # --- FIM DA ALTERAÇÃO ---

        # Remove a tarefa antiga, se existir
        old_job_id = profile.get('trial_job_id')
        if old_job_id:
            try:
                scheduler.remove_job(old_job_id)
            except JobLookupError:
                logger.warning(f"Não foi possível encontrar a tarefa de teste antiga '{old_job_id}' para remover para o utilizador '{username}'.")

        # Agenda a nova tarefa
        new_job_id = f"trial_end_{plex_user_id}_{secrets.token_hex(4)}"
        # CORREÇÃO: Converte para o fuso horário do scheduler antes de remover tzinfo
        naive_run_date = new_trial_end_utc.astimezone(scheduler.timezone).replace(tzinfo=None)
        scheduler.add_job(id=new_job_id, func=end_trial_job, args=[plex_user_id], trigger='date', run_date=naive_run_date, replace_existing=True, misfire_grace_time=3600)

        # Atualiza o perfil
        profile['trial_end_date'] = new_trial_end_utc.isoformat()
        profile['trial_job_id'] = new_job_id
        # CORREÇÃO: Limpa a data de expiração regular, se existir, pois agora é um teste
        if 'expiration_date' in profile and profile['expiration_date']:
             profile['expiration_date'] = None
             logger.info(f"Data de expiração regular limpa para '{username}' ao estender/iniciar teste.")
        if 'expiration_job_id' in profile and profile['expiration_job_id']:
            try: scheduler.remove_job(profile['expiration_job_id'])
            except JobLookupError: pass
            profile['expiration_job_id'] = None

        extensions.data_manager.set_user_profile(plex_user_id, profile)

        # --- NOVO: Desbloqueia o utilizador se o teste estava expirado ou se estava bloqueado por expiração regular ---
        blocked_info = extensions.data_manager.get_blocked_user(plex_user_id)
        if blocked_info and blocked_info.get('block_reason') in ['trial_expired', 'expired']:
            extensions.plex_manager.unblock_user(plex_user_id)
            logger.info(f"Utilizador '{username}' foi desbloqueado automaticamente após extensão/início do teste.")
        # --- FIM NOVO ---

        logger.info(f"Admin '{current_user.username}' estendeu/iniciou o período de teste de '{username}' por {extend_minutes} minutos. Novo fim em {naive_run_date.strftime('%Y-%m-%d %H:%M:%S')}.")

        return jsonify({
            "success": True,
            "message": _("Período de teste definido/estendido com sucesso. Novo fim em %(date)s.", date=naive_run_date.strftime('%d/%m/%Y %H:%M'))
        })

    except Exception as e:
        logger.error(f"Erro ao estender/definir o período de teste para '{username}': {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Ocorreu um erro ao estender/definir o período de teste.")}), 500
# --- FIM NOVO ---

@users_api_bp.route('/reactivate', methods=['POST'])
@login_required
@admin_required
def reactivate_user_route():
    plex_user_id = request.json.get('plex_user_id')
    libraries = request.json.get('libraries')

    if not plex_user_id:
        return jsonify({"success": False, "message": _("ID do utilizador não fornecido.")}), 400

    if not libraries or not isinstance(libraries, list) or len(libraries) == 0:
        return jsonify({"success": False, "message": _("É necessário selecionar pelo menos uma biblioteca para reativar o utilizador.")}), 400

    profile = extensions.data_manager.get_user_profile(plex_user_id)
    if not profile or profile.get('status') != 'inactive':
        return jsonify({"success": False, "message": _("Utilizador não está inativo ou não foi encontrado.")}), 404

    try:
        username = profile.get('username')
        logger.info(f"Admin '{current_user.username}' reativou o utilizador '{username}'. A tentar enviar novo convite para as bibliotecas: {', '.join(libraries)}")

        identifier = profile.get('email') or username
        if not identifier:
            logger.warning(f"Não foi possível enviar convite para '{username}' (ID: {plex_user_id}) por falta de email/username.")
            return jsonify({"success": True, "message": _("Utilizador reativado, mas não foi possível enviar convite (sem email/username).")})

        invite_result = extensions.plex_manager.invites.send_plex_invite(identifier, libraries)

        if not invite_result.get('success'):
            error_message = invite_result.get('message', _('Erro desconhecido ao convidar.'))
            logger.warning(f"Tentativa de reativação para '{username}' falhou no envio do convite: {error_message}")
            return jsonify({"success": False, "message": _("Falha ao enviar novo convite: %(error)s", error=error_message)})

        logger.info(f"Aguardando 8 segundos para a API do Plex processar a reativação de '{username}'...")
        time.sleep(8) # Aumentado para 8 segundos

        # Re-confirma que o utilizador agora existe no Plex antes de atualizar o status local
        all_current_users = extensions.plex_manager.get_all_plex_users(force_refresh=True)
        if not any(u['id'] == plex_user_id for u in all_current_users):
             # Adiciona uma notificação para o admin
            extensions.data_manager.create_notification(
                message=_("Falha ao reativar %(username)s. O convite foi enviado, mas o utilizador ainda não aparece no Plex. Verifique o estado do convite no Plex ou peça ao utilizador para o aceitar manualmente.", username=username),
                category='error', link=url_for('main.users_page')
            )
            logger.error(f"Falha ao confirmar reativação de '{username}' no Plex após envio do convite.")
            # Não altera o status local, pois o acesso não foi confirmado
            return jsonify({"success": False, "message": _("Convite enviado, mas falha ao confirmar o acesso no Plex. Verifique o estado do convite no Plex.")})


        profile['status'] = 'active'
        profile['libraries'] = json.dumps(libraries)
        extensions.data_manager.set_user_profile(plex_user_id, profile)
        extensions.data_manager.remove_blocked_user(plex_user_id) # Garante que não está bloqueado

        logger.info(f"Utilizador '{username}' marcado como ativo localmente.")

        # Invalida o cache após a mudança de status
        # extensions.plex_manager.users.invalidate_user_cache() # Já feito acima na busca

        if extensions.socketio:
            extensions.socketio.emit('user_list_updated', {
                'message': _("O utilizador %(username)s foi reativado.", username=username)
            }, namespace='/dashboard')

        logger.info(f"Convite enviado com sucesso para '{identifier}' para o utilizador reativado '{username}'.")
        return jsonify({"success": True, "message": _("Utilizador reativado e um novo convite foi enviado para %(identifier)s.", identifier=identifier)})

    except Exception as e:
        logger.error(f"Erro ao reativar o utilizador {plex_user_id}: {e}", exc_info=True)
        # Reverte o status se a operação falhar no meio E se o perfil ainda existir
        profile = extensions.data_manager.get_user_profile(plex_user_id)
        if profile:
            profile['status'] = 'inactive'
            extensions.data_manager.set_user_profile(plex_user_id, profile)
        return jsonify({"success": False, "message": _("Ocorreu um erro ao reativar o utilizador.")}), 500


@users_api_bp.route('/delete-permanently', methods=['POST'])
@login_required
@admin_required
def delete_permanently_route():
    plex_user_id = request.json.get('plex_user_id')
    if not plex_user_id:
        return jsonify({"success": False, "message": _("ID do utilizador não fornecido.")}), 400

    profile = extensions.data_manager.get_user_profile(plex_user_id)
    if not profile or profile.get('status') != 'inactive':
        return jsonify({"success": False, "message": _("Apenas utilizadores inativos podem ser apagados permanentemente.")}), 400

    try:
        username = profile['username']
        extensions.data_manager.delete_user_profile(plex_user_id)
        logger.info(f"Admin '{current_user.username}' apagou permanentemente o utilizador '{username}' (ID: {plex_user_id}).")
        return jsonify({"success": True, "message": _("Utilizador apagado permanentemente.")})
    except Exception as e:
        logger.error(f"Erro ao apagar permanentemente o utilizador {plex_user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Ocorreu um erro ao apagar o utilizador.")}), 500

@users_api_bp.route('/notify/<int:plex_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def notify_user_route(user):
    profile = extensions.data_manager.get_user_profile(user['id'])
    expiration_date_str = profile.get('expiration_date')

    if not expiration_date_str:
        return jsonify({"success": False, "message": _("Este utilizador não tem uma data de vencimento definida.")})

    try:
        exp_date = datetime.fromisoformat(expiration_date_str).date()
        days_left = (exp_date - date.today()).days
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": _("Formato de data de expiração inválido no perfil do utilizador.")})

    extensions.plex_manager.notifier_manager.send_expiration_notification(user, days_left, profile)

    logger.info(f"Admin '{current_user.username}' enviou uma notificação manual para '{user['username']}'.")
    return jsonify({"success": True, "message": _("Notificação de vencimento enviada para %(username)s.", username=user['username'])})


@users_api_bp.route('/libraries/<int:plex_user_id>')
@login_required
@admin_required
@user_lookup_by_id
def get_user_libraries_route(user):
    return jsonify(extensions.plex_manager.get_user_libraries(user['id']))

@users_api_bp.route('/update-libraries', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def update_libraries_route(user):
    result = extensions.plex_manager.update_user_libraries(user['id'], request.json.get('libraries', []))
    if result.get('success'):
        logger.info(f"Admin '{current_user.username}' atualizou as bibliotecas de '{user['username']}'.")
    return jsonify(result)

@users_api_bp.route('/update-all-libraries', methods=['POST'])
@login_required
@admin_required
def update_all_libraries_route():
    result = extensions.plex_manager.update_all_users_libraries(request.json.get('libraries'))
    if result.get('success'):
        logger.info(f"Admin '{current_user.username}' iniciou a atualização de bibliotecas para todos os utilizadores.")
    return jsonify(result)

@users_api_bp.route('/remove', methods=['POST'])
@login_required
@admin_required
def remove_user_route():
    plex_user_id = request.json.get('plex_user_id')
    if not plex_user_id:
        return jsonify({"success": False, "message": _("ID do utilizador não fornecido.")}), 400

    try:
        plex_user_id = int(plex_user_id)
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": _("ID do utilizador inválido.")}), 400

    result = extensions.plex_manager.remove_user(plex_user_id)

    if result.get('success'):
        logger.info(f"Admin '{current_user.username}' removeu/desativou o utilizador '{result.get('username', f'ID: {plex_user_id}')}'.")

    return jsonify(result)

@users_api_bp.route('/block', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def block_user_route(user):
    result = extensions.plex_manager.block_user(user['id'], reason='manual')
    if result.get('success'):
        logger.info(f"Admin '{current_user.username}' bloqueou o utilizador '{user['username']}'.")
    return jsonify(result)

@users_api_bp.route('/unblock', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def unblock_user_route(user):
    result = extensions.plex_manager.unblock_user(user['id'])
    if result.get('success'):
        logger.info(f"Admin '{current_user.username}' desbloqueou o utilizador '{user['username']}'.")
    return jsonify(result)

@users_api_bp.route('/update-limit', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def update_limit_route(user):
    screens = request.json.get('screens', 0)
    profile = extensions.data_manager.get_user_profile(user['id'])
    profile['screen_limit'] = screens
    extensions.data_manager.set_user_profile(user['id'], profile)
    logger.info(f"Admin '{current_user.username}' atualizou o limite de telas de '{user['username']}' para {screens}.")
    return jsonify({"success": True, "message": _("Limite de %(screens)d tela(s) aplicado.", screens=screens) if screens > 0 else _("Limite removido.")})

@users_api_bp.route('/update-all-limits', methods=['POST'])
@login_required
@admin_required
def update_all_limits_route():
    screens = request.json.get('screens', -1)
    final_limit = 0 if screens < 0 else screens
    all_users = extensions.plex_manager.get_all_plex_users()
    if all_users:
        for user in all_users:
            # Não aplica limite ao admin
            if user['id'] == int(current_user.id):
                continue
            profile = extensions.data_manager.get_user_profile(user['id'])
            # Só atualiza se o perfil existir
            if profile:
                profile['screen_limit'] = final_limit
                extensions.data_manager.set_user_profile(user['id'], profile)
    logger.info(f"Admin '{current_user.username}' atualizou o limite de telas para todos os utilizadores para {final_limit}.")
    return jsonify({"success": True, "message": _("Limite de %(screens)d tela(s) aplicado para todos.", screens=final_limit) if final_limit > 0 else _("Limites removidos de todos.")})


@users_api_bp.route('/toggle-overseerr', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def toggle_overseerr_access_route(user):
    access = request.json.get('access', False)
    result = extensions.plex_manager.toggle_overseerr_access(user['id'], access)
    if result.get('success'):
        action = 'concedeu' if access else 'removeu'
        logger.info(f"Admin '{current_user.username}' {action} o acesso ao Overseerr para '{user['username']}'.")
    return jsonify(result)

# --- FUNÇÃO get_user_list AJUSTADA ---
@users_api_bp.route('/list')
@login_required
@admin_required
def get_user_list():
    """Retorna uma lista de utilizadores com detalhes básicos e perfil, filtrados por contacto."""
    try:
        plex_users = extensions.plex_manager.get_all_plex_users()
        if plex_users is None:
            return jsonify({"success": False, "message": _("Falha ao obter utilizadores do Plex.")}), 500

        user_profiles = extensions.data_manager.get_all_user_profiles()
        profiles_map = {p['plex_user_id']: p for p in user_profiles}

        # **LÓGICA DE FILTRAGEM APLICADA AQUI:**
        filtered_users = []
        for user in plex_users:
            profile = profiles_map.get(user['id'], {})
            has_contact = (
                profile.get('telegram_user') or
                profile.get('discord_user_id') or
                profile.get('phone_number')
            )
            if has_contact:
                # Adiciona apenas os campos necessários para o modal (id, username, email)
                filtered_users.append({
                    'id': user['id'],
                    'username': user.get('username', user.get('title', 'N/A')), # Garante que username existe
                    'email': user.get('email', '') # Garante que email existe
                })

        # Ordena alfabeticamente pelo nome de utilizador
        filtered_users.sort(key=lambda x: x['username'].lower())

        return jsonify({"success": True, "users": filtered_users})

    except Exception as e:
        logger.error(f"Erro ao listar utilizadores para seleção: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Erro interno ao obter lista de utilizadores.")}), 500
# --- FIM DO AJUSTE ---

@users_api_bp.route('/payments/<int:plex_user_id>')
@login_required
def get_user_payments_history(plex_user_id):
    if not current_user.is_admin and int(current_user.id) != plex_user_id:
        return jsonify({"success": False, "message": _("Acesso não autorizado.")}), 403
    return jsonify({"success": True, "payments": extensions.data_manager.get_payments_by_user(plex_user_id)})

@users_api_bp.route('/account/devices')
@login_required
def get_account_devices():
    return jsonify(extensions.tautulli_manager.get_user_devices(int(current_user.id)))

