# tests/test_integridade_da_base_de_dados.py
"""
Guarda as garantias que a base de dados passou a dar.

Nenhuma destas coisas dava erro quando faltava — e é esse o problema que elas
resolvem. Um pagamento órfão, um `status` inventado, um índice que o SQLite
decide não usar, um token de pagamento eterno: tudo isto funcionava em silêncio
e só aparecia meses depois, num relatório errado ou num utilizador invisível.
Por isso cada teste aqui verifica o COMPORTAMENTO, e não a declaração: se a
coluna diz `ForeignKey` mas o SQLite não a impõe, o esquema está certo e a
garantia não existe.
"""

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


# ==========================================
# 1. CHAVES ESTRANGEIRAS
# ==========================================

class TestChavesEstrangeiras:
    """⚠️ Estavam declaradas e não eram impostas: `PRAGMA foreign_keys` vem
    DESLIGADO por omissão no SQLite e ninguém o ligava."""

    def test_o_pragma_esta_ligado_na_ligacao(self, db_session):
        assert db_session.execute(text('PRAGMA foreign_keys')).scalar() == 1

    def test_um_bloqueio_sobre_um_perfil_inexistente_e_recusado(self, db_session):
        from sqlalchemy.exc import IntegrityError
        from app.models import BlockedUser

        db_session.add(BlockedUser(media_user_id='nao-existe', username='fantasma'))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_apagar_o_perfil_leva_o_que_e_ESTADO(self, db_session, data_manager):
        from app.models import BlockedUser, Notification, UserProfile

        db_session.add(UserProfile(media_user_id='77', username='ana'))
        db_session.commit()
        db_session.add(BlockedUser(media_user_id='77', username='ana'))
        db_session.add(Notification(media_user_id='77', message='olá'))
        db_session.commit()

        data_manager.delete_user_profile('77')

        assert BlockedUser.query.get('77') is None
        assert Notification.query.filter_by(media_user_id='77').count() == 0

    def test_apagar_o_perfil_NAO_leva_o_que_e_HISTORICO(self, db_session, data_manager):
        """🛡️ Um pagamento recebido aconteceu. Apagá-lo porque a conta foi
        removida falsifica o relatório financeiro do mês em que entrou."""
        from app.models import PixPayment, StreamTerminationLog, UserProfile

        db_session.add(UserProfile(media_user_id='78', username='bruno'))
        db_session.commit()
        db_session.add(PixPayment(txid='t-78', media_user_id='78', username='bruno',
                                  value=25.0, created_at='2026-01-01T00:00:00+00:00'))
        db_session.add(StreamTerminationLog(media_user_id='78', username='bruno',
                                            media_title='Filme', reason='manual'))
        db_session.commit()

        data_manager.delete_user_profile('78')

        assert PixPayment.query.get('t-78') is not None
        assert StreamTerminationLog.query.filter_by(media_user_id='78').count() == 1

    def test_a_migracao_de_identidade_arrasta_as_filhas(self, db_session, data_manager):
        """A troca do identificador é um `ON UPDATE CASCADE`: sem ele, o SQLite
        recusa mudar a chave primária de um perfil com filhas a apontar-lhe."""
        from app.models import Notification, PixPayment, UserProfile

        db_session.add(UserProfile(media_user_id='antigo', username='carla'))
        db_session.commit()
        db_session.add(Notification(media_user_id='antigo', message='olá'))
        db_session.add(PixPayment(txid='t-mig', media_user_id='antigo', username='carla',
                                  value=10.0, created_at='2026-01-01T00:00:00+00:00'))
        db_session.commit()

        assert data_manager.migrar_identidade('antigo', 'novo') is True

        assert UserProfile.query.get('novo') is not None
        # A filha de ESTADO vai pelo cascade; a de HISTÓRICO, pelo ciclo explícito.
        assert Notification.query.filter_by(media_user_id='novo').count() == 1
        assert PixPayment.query.get('t-mig').media_user_id == 'novo'

    def test_bloquear_quem_ainda_nao_tem_perfil_cria_um(self, db_session, data_manager):
        """⚠️ O administrador pode bloquear alguém que está no servidor de média
        e nunca entrou no painel. Antes, o registo do bloqueio desaparecia pelo
        `except IntegrityError` e devolvia `None` sem uma linha de log."""
        from app.models import UserProfile

        registo = data_manager.add_blocked_user('90', 'diana', reason='manual')

        assert registo is not None
        assert registo['block_reason'] == 'manual'
        assert UserProfile.query.get('90').username == 'diana'


# ==========================================
# 2. ÍNDICES
# ==========================================

class TestIndices:
    """⚠️ Um índice declarado não é um índice USADO. Estes testes leem o plano
    de consulta do SQLite, porque os dois índices que já existiam
    (`username`, `coupons.code`) nunca eram usados: todas as consultas comparam
    `lower(...)`/`upper(...)`, e o SQLite não usa o índice de uma coluna quando
    a comparação é sobre uma expressão dela."""

    def _plano(self, db_session, sql):
        return ' | '.join(
            linha[3] for linha in db_session.execute(text('EXPLAIN QUERY PLAN ' + sql))
        )

    @pytest.mark.parametrize('nome, sql', [
        ('ix_user_profiles_username_lower',
         "SELECT 1 FROM user_profiles WHERE lower(username) = 'ana'"),
        ('ix_coupons_code_upper',
         "SELECT 1 FROM coupons WHERE upper(code) = 'PROMO'"),
        ('ix_stream_termination_logs_reason_timestamp',
         "SELECT 1 FROM stream_termination_logs WHERE reason = 'plugin_limit_blocked' "
         "ORDER BY timestamp DESC LIMIT 1"),
        ('ix_pix_payments_status_created_at',
         "SELECT 1 FROM pix_payments WHERE status = 'CONCLUIDA' AND created_at > '2026-01'"),
    ])
    def test_a_consulta_real_usa_o_indice(self, db_session, nome, sql):
        plano = self._plano(db_session, sql)
        assert nome in plano, f"o plano não usa {nome}: {plano}"

    def test_as_varreduras_diarias_usam_os_indices_parciais(self, db_session):
        """São PARCIAIS porque a maioria dos perfis tem a data a NULL: um índice
        completo indexaria sobretudo nada e o SQLite continuava a varrer."""
        for coluna in ('expiration_date', 'trial_end_date'):
            plano = self._plano(
                db_session,
                f"SELECT 1 FROM user_profiles "
                f"WHERE {coluna} IS NOT NULL AND {coluna} != ''",
            )
            assert f'ix_user_profiles_{coluna}' in plano, plano


# ==========================================
# 3. AUDITORIA
# ==========================================

class TestAuditoria:
    def test_regista_quem_fez_o_que(self, db_session):
        from app.services import audit

        audit.registar('teste.acao', alvo_tipo='utilizador', alvo_id='5',
                       detalhes={'antes': 1, 'depois': 2})

        entradas, total = audit.listar(limite=10)
        assert total == 1
        assert entradas[0]['acao'] == 'teste.acao'
        assert entradas[0]['alvo_id'] == '5'
        assert entradas[0]['detalhes'] == {'antes': 1, 'depois': 2}

    def test_uma_credencial_nunca_entra_na_auditoria(self):
        """🛡️ Uma auditoria que guarda segredos é mais uma cópia dos segredos —
        e ela é lida por uma rota, exportada num backup e copiada para onde quer
        que a base de dados vá."""
        from app.services import audit

        mudancas = audit.diferenca(
            {'RENEWAL_PRICE': '25', 'EFI_CLIENT_SECRET': 'antigo', 'PLEX_TOKEN': 'aaa'},
            {'RENEWAL_PRICE': '35', 'EFI_CLIENT_SECRET': 'novo', 'PLEX_TOKEN': 'bbb'},
        )

        assert mudancas['RENEWAL_PRICE'] == {'antes': '25', 'depois': '35'}
        for chave in ('EFI_CLIENT_SECRET', 'PLEX_TOKEN'):
            assert mudancas[chave] == {'antes': '(alterado)', 'depois': '(alterado)'}, chave

    @pytest.mark.parametrize('chave', [
        'DISCORD_WEBHOOK_URL',
        'WEBHOOK_URL',
        'WEBHOOK_AUTHORIZATION_HEADER',
    ])
    def test_as_credenciais_SEM_token_no_nome_tambem_sao_escondidas(self, chave):
        """🐛 A primeira versão da regra deixava estas três passar em claro.

        Nenhuma tem `TOKEN`, `KEY` ou `SECRET` no nome, e as três são
        credenciais: no `DISCORD_WEBHOOK_URL` o token que autoriza a publicar
        no canal está DENTRO do caminho, o `WEBHOOK_URL` pode trazer
        `utilizador:senha@` embutidos, e o `WEBHOOK_AUTHORIZATION_HEADER` é
        literalmente um cabeçalho de autorização. Bastava editá-las para o
        valor antigo E o novo ficarem em texto puro na tabela.
        """
        from app.services import audit

        segredo_antigo = 'https://discord.com/api/webhooks/123/SEGREDO-ANTIGO'
        segredo_novo = 'https://discord.com/api/webhooks/123/SEGREDO-NOVO'

        mudancas = audit.diferenca({chave: segredo_antigo}, {chave: segredo_novo})

        assert mudancas[chave] == {'antes': '(alterado)', 'depois': '(alterado)'}
        registado = json.dumps(mudancas)
        assert 'SEGREDO-ANTIGO' not in registado
        assert 'SEGREDO-NOVO' not in registado

    def test_um_URL_com_credenciais_e_escondido_seja_qual_for_o_nome(self):
        """⚠️ O segundo travão, sobre o VALOR e não sobre o nome.

        É esta camada que cobre a definição que alguém acrescente amanhã sem se
        lembrar de nada disto: um `https://utilizador:senha@host/` é um segredo
        chame-se a chave como se chamar.
        """
        from app.services import audit

        mudancas = audit.diferenca(
            {'ALGO_NOVO_QUALQUER': 'https://ana:senha-secreta@servidor.test/webhook'},
            {'ALGO_NOVO_QUALQUER': 'https://ana:outra-senha@servidor.test/webhook'},
        )

        assert mudancas['ALGO_NOVO_QUALQUER'] == {'antes': '(alterado)', 'depois': '(alterado)'}
        assert 'senha-secreta' not in json.dumps(mudancas)

    def test_um_URL_SEM_credenciais_continua_a_ser_auditavel(self):
        """⚠️ Esconder de mais também é um defeito: o endereço do painel ou do
        servidor de mídia é exatamente o tipo de mudança que se quer poder ver
        meses depois."""
        from app.services import audit

        mudancas = audit.diferenca(
            {'APP_BASE_URL': 'https://painel.antigo.test'},
            {'APP_BASE_URL': 'https://painel.novo.test'},
        )

        assert mudancas['APP_BASE_URL'] == {
            'antes': 'https://painel.antigo.test',
            'depois': 'https://painel.novo.test',
        }

    def test_os_modelos_de_mensagem_continuam_a_ser_auditaveis(self):
        """Um `WEBHOOK_*_MESSAGE_TEMPLATE` é um FORMATO, não um segredo — e é
        das coisas que mais interessa poder auditar, porque é o que chega ao
        telefone de quem paga."""
        from app.services import audit

        mudancas = audit.diferenca(
            {'WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE': '{"content": "antes"}'},
            {'WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE': '{"content": "depois"}'},
        )

        assert mudancas['WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE']['depois'] == '{"content": "depois"}'

    def test_nenhuma_chave_do_config_com_credencial_fica_a_descoberto(self):
        """A varredura que teria apanhado isto à primeira.

        Percorre o esquema REAL do config e exige que toda a chave que carrega
        uma credencial esteja coberta — em vez de confiar em que alguém se
        lembre de a acrescentar.
        """
        from app.config import load_or_create_config
        from app.services.audit import _e_sensivel

        # As que carregam segredo e não o dizem no nome de forma óbvia.
        devem_ser_escondidas = {
            'WEBHOOK_URL', 'DISCORD_WEBHOOK_URL', 'WEBHOOK_AUTHORIZATION_HEADER',
        }
        chaves = set(load_or_create_config())

        # O teste não vale nada se as chaves tiverem sido renomeadas.
        assert devem_ser_escondidas <= chaves, devem_ser_escondidas - chaves

        a_descoberto = sorted(k for k in devem_ser_escondidas if not _e_sensivel(k))
        assert a_descoberto == [], f"credenciais sem redação na auditoria: {a_descoberto}"

    def test_so_regista_o_que_MUDOU(self):
        from app.services import audit

        assert audit.diferenca({'a': 1, 'b': 2}, {'a': 1, 'b': 3}) == {
            'b': {'antes': 2, 'depois': 3}
        }

    def test_falhar_a_registar_nunca_derruba_a_acao(self, monkeypatch):
        """⚠️ Perder a linha de auditoria é mau; perder o pagamento que ela
        descreve é muito pior."""
        from app.services import audit

        class _EngineEmBaixo:
            def begin(self):
                raise RuntimeError('base de dados em baixo')

        monkeypatch.setattr(type(audit.db), 'engine',
                            property(lambda self: _EngineEmBaixo()))
        audit.registar('teste.falha')  # não levanta

    def test_um_valor_que_nao_serializa_nao_perde_a_linha(self, db_session):
        from app.services import audit

        audit.registar('teste.objeto', detalhes={'quando': datetime.now(timezone.utc)})

        assert audit.listar(limite=1)[0][0]['acao'] == 'teste.objeto'


# ==========================================
# 4. PERMISSÕES DOS FICHEIROS
# ==========================================

class TestPermissoesDeFicheiro:
    """🛡️ O config.json guarda a SECRET_KEY, o token do Plex, a chave de
    administrador do Jellyfin e as credenciais dos gateways, tudo em texto
    puro. Nascia com o que o umask ditasse."""

    def test_gravar_o_config_deixa_o_ficheiro_so_para_o_dono(self, config_file):
        from app import config as config_module

        config_file(APP_TITLE='Painel')
        config_module.save_app_config(config_module.load_or_create_config())

        modo = stat.S_IMODE(os.stat(config_module.CONFIG_FILE).st_mode)
        assert modo == 0o600, oct(modo)

    def test_um_ficheiro_que_nao_existe_nao_rebenta(self, tmp_path):
        from app.utils.ficheiros import proteger_ficheiro

        assert proteger_ficheiro(str(tmp_path / 'nao-existe')) is False

    def test_o_wal_e_o_shm_tambem_sao_protegidos(self, tmp_path):
        """⚠️ Em modo WAL as escritas mais recentes vivem no `-wal`: proteger só
        o `.db` deixava à vista tudo o que ainda não tinha sido integrado."""
        from app.utils.ficheiros import proteger_base_de_dados

        base = tmp_path / 'dados.db'
        for sufixo in ('', '-wal', '-shm'):
            (tmp_path / f'dados.db{sufixo}').write_bytes(b'x')

        assert proteger_base_de_dados(str(base)) == 3
        for sufixo in ('', '-wal', '-shm'):
            caminho = tmp_path / f'dados.db{sufixo}'
            assert stat.S_IMODE(os.stat(caminho).st_mode) == 0o600, sufixo


# ==========================================
# 5. RESTRIÇÕES DE DOMÍNIO E VALIDAÇÃO
# ==========================================

class TestRestricoesDeDominio:
    """⚠️ O SQLite **não impõe o comprimento de um VARCHAR** e não havia um
    único CHECK: `status = 'qualquer-coisa'` passava, e a partir daí a pessoa
    não era nem ativa nem inativa — não aparecia nas listagens, não era
    bloqueada, não era removida."""

    @pytest.mark.parametrize('campos', [
        {'status': 'qualquer-coisa'},
        {'billing_day': 0},
        {'billing_day': 32},
        {'screen_limit': -1},
        {'xp': -10},
        {'referral_credit': -5.0},
    ])
    def test_um_perfil_invalido_e_recusado(self, db_session, campos):
        from sqlalchemy.exc import IntegrityError
        from app.models import UserProfile

        db_session.add(UserProfile(media_user_id='30', username='inválido', **campos))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    @pytest.mark.parametrize('campos', [
        {'status': 'INVENTADO'},
        {'value': -10.0},
    ])
    def test_um_pagamento_invalido_e_recusado(self, db_session, campos):
        """Um pagamento de valor negativo entrava no somatório do relatório
        financeiro e SUBTRAÍA da receita do mês."""
        from sqlalchemy.exc import IntegrityError
        from app.models import PixPayment

        base = {'txid': 'mau', 'media_user_id': '1', 'username': 'x',
                'value': 10.0, 'status': 'ATIVA', 'created_at': '2026-01-01'}
        db_session.add(PixPayment(**{**base, **campos}))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_um_cupao_com_tipo_desconhecido_e_recusado(self, db_session):
        from sqlalchemy.exc import IntegrityError
        from app.models import Coupon

        db_session.add(Coupon(code='X', discount_type='gratis', value=1))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_o_telefone_e_guardado_so_com_digitos(self, db_session):
        """🐛 O destinatário do WhatsApp é `{phone_number}@s.whatsapp.net`: um
        número com parênteses produzia um identificador inválido e a mensagem
        não chegava a ninguém — sem erro nenhum."""
        from app.models import UserProfile

        perfil = UserProfile(media_user_id='31', username='ana',
                             phone_number='+55 (11) 99999-9999')
        db_session.add(perfil)
        db_session.commit()

        assert perfil.phone_number == '5511999999999'

    def test_um_telefone_sem_digitos_nenhuns_fica_a_NULL(self, db_session):
        from app.models import UserProfile

        perfil = UserProfile(media_user_id='32', username='bruno', phone_number='sem número')
        db_session.add(perfil)
        db_session.commit()

        assert perfil.phone_number is None

    def test_o_email_e_normalizado_mas_nao_recusado(self, db_session):
        """⚠️ Normaliza, não recusa: grande parte dos emails vem do SERVIDOR de
        média, e recusar um partia a sincronização de perfis."""
        from app.models import UserProfile

        perfil = UserProfile(media_user_id='33', username='carla', email='  Ana@X.COM ')
        db_session.add(perfil)
        db_session.commit()

        assert perfil.email == 'ana@x.com'


class TestValidacaoNasRotas:
    """Quem ESCREVE à mão passa pelos schemas, e esses recusam."""

    @pytest.mark.parametrize('valor', ['sem-arroba', 'a@b', 'com espaço@x.com'])
    def test_um_email_mal_escrito_e_recusado(self, valor):
        from app.blueprints.api.schemas import validar_email

        with pytest.raises(ValueError):
            validar_email(valor)

    def test_um_email_valido_passa_normalizado(self):
        from app.blueprints.api.schemas import validar_email

        assert validar_email('  Ana@Exemplo.COM ') == 'ana@exemplo.com'

    @pytest.mark.parametrize('valor', ['123', '1' * 20])
    def test_um_telefone_impossivel_e_recusado(self, valor):
        from app.blueprints.api.schemas import validar_telefone

        with pytest.raises(ValueError):
            validar_telefone(valor)

    def test_um_campo_sem_digitos_nenhuns_conta_como_vazio(self):
        """O telefone é OPCIONAL: 'abc' não é um número mal escrito que valha a
        pena recusar — é o campo em branco, e recusá-lo impedia alguém de
        limpar o que lá estava."""
        from app.blueprints.api.schemas import validar_telefone

        assert validar_telefone('abc') is None

    def test_o_formato_natural_de_escrever_nao_e_um_erro(self):
        from app.blueprints.api.schemas import validar_telefone

        assert validar_telefone('+55 (11) 99999-9999') == '5511999999999'


# ==========================================
# 6. O TOKEN DE PAGAMENTO
# ==========================================

class TestTokenDePagamento:
    """🛡️ É uma credencial PORTADORA que viaja por Telegram, Discord e WhatsApp
    e fica no histórico dessas conversas para sempre. Nunca expirava e nunca
    mudava."""

    def _perfil(self, data_manager, media_user_id='40'):
        data_manager.set_user_profile(media_user_id, {'username': f'u{media_user_id}'})
        return data_manager.get_user_profile(media_user_id)

    def test_um_token_valido_encontra_o_perfil(self, db_session, data_manager):
        perfil = self._perfil(data_manager)

        encontrado = data_manager.perfil_por_payment_token(perfil['payment_token'])

        assert encontrado is not None
        assert encontrado.media_user_id == '40'

    def test_um_token_expirado_NAO_encontra_o_perfil(self, db_session, data_manager):
        from app.models import UserProfile

        perfil = self._perfil(data_manager)
        UserProfile.query.get('40').payment_token_expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
        db_session.commit()

        assert data_manager.perfil_por_payment_token(perfil['payment_token']) is None

    def test_um_perfil_antigo_sem_validade_continua_a_valer(self, db_session, data_manager):
        """⚠️ Invalidar de repente os links que estão no telemóvel de toda a
        gente seria uma migração a cortar o acesso a quem quer pagar."""
        from app.models import UserProfile

        perfil = self._perfil(data_manager)
        UserProfile.query.get('40').payment_token_expires_at = None
        db_session.commit()

        assert data_manager.perfil_por_payment_token(perfil['payment_token']) is not None

    def test_garantir_renova_um_token_expirado(self, db_session, data_manager):
        from app.models import UserProfile

        antigo = self._perfil(data_manager)['payment_token']
        UserProfile.query.get('40').payment_token_expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
        db_session.commit()

        novo = data_manager.garantir_payment_token('40')

        assert novo != antigo
        assert data_manager.perfil_por_payment_token(novo) is not None

    def test_garantir_estende_a_validade_de_um_token_ainda_bom(self, db_session, data_manager):
        """O que conta é a data do ÚLTIMO link enviado."""
        from app.models import UserProfile

        antigo = self._perfil(data_manager)['payment_token']
        UserProfile.query.get('40').payment_token_expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1))
        db_session.commit()

        assert data_manager.garantir_payment_token('40') == antigo
        validade = UserProfile.query.get('40').payment_token_expires_at
        assert validade > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2)

    def test_rodar_invalida_o_link_anterior(self, db_session, data_manager):
        antigo = self._perfil(data_manager)['payment_token']

        novo = data_manager.rodar_payment_token('40')

        assert novo != antigo
        assert data_manager.perfil_por_payment_token(antigo) is None
        assert data_manager.perfil_por_payment_token(novo) is not None

    def test_a_rota_publica_tem_limite(self, app):
        """🔒 Era a única do fluxo de pagamento sem limite nenhum — e é um
        oráculo que diz se um token existe, devolvendo dados pessoais quando
        acerta.

        Pergunta-se ao registo do limitador, e não ao decorador: um
        `hasattr(vista, '__wrapped__')` seria verdade para qualquer rota
        decorada e o teste passava sem limite nenhum.
        """
        from app.extensions import limiter

        vista = app.view_functions['users_api.get_public_user_profile_by_token']
        registadas = set(limiter.limit_manager._decorated_limits)
        chave = f'{vista.__module__}.{vista.__name__}.{vista.__name__}'

        assert chave in registadas, f'a rota {chave} não tem rate limit'


# ==========================================
# 7. REMOÇÃO SUAVE
# ==========================================

class TestRemocaoSuave:
    """🛡️ As três tabelas que mais custa perder tinham um DELETE direto, sem
    confirmação e sem volta."""

    def _pagamento(self, db_session, txid='t-soft'):
        from app.models import PixPayment, UserProfile

        if not UserProfile.query.get('50'):
            db_session.add(UserProfile(media_user_id='50', username='ana'))
            db_session.commit()
        db_session.add(PixPayment(txid=txid, media_user_id='50', username='ana',
                                  value=25.0, status='CONCLUIDA',
                                  created_at='2026-01-15T12:00:00+00:00'))
        db_session.commit()
        return txid

    def test_apagar_tira_da_vista_mas_a_linha_fica(self, db_session, data_manager):
        from app.models import PixPayment

        txid = self._pagamento(db_session)

        assert data_manager.delete_pix_payment(txid) is True

        assert data_manager.get_pix_payment(txid) is None
        assert PixPayment.query.get(txid) is not None
        assert data_manager.get_pix_payment(txid, incluir_apagados=True) is not None

    def test_sai_de_TODAS_as_leituras(self, db_session, data_manager):
        """Bastava esquecer um sítio para um pagamento apagado continuar a
        aparecer — e o sítio esquecido não dava erro."""
        txid = self._pagamento(db_session)
        data_manager.delete_pix_payment(txid)

        assert data_manager.get_payments_by_user('50') == []
        assert data_manager.get_latest_completed_payment('50') is None
        assert data_manager.user_has_completed_payment('50') is False
        resumo = data_manager.get_financial_summary(2026, 1)
        assert all(p['txid'] != txid for p in resumo.get('payments', []))

    def test_restaurar_devolve_o_pagamento_as_contas(self, db_session, data_manager):
        """É a outra metade: sem uma forma de voltar atrás, `deleted_at` era só
        um DELETE mais lento."""
        txid = self._pagamento(db_session)
        data_manager.delete_pix_payment(txid)

        assert data_manager.restaurar_pix_payment(txid) is True

        assert data_manager.get_pix_payment(txid) is not None
        assert data_manager.get_latest_completed_payment('50') is not None

    def test_apagar_um_cupao_NAO_apaga_quem_o_usou(self, db_session, data_manager):
        """🛡️ O DELETE de antes arrastava `coupon_usages` por cascade — e é esse
        registo que impede a mesma pessoa de usar o cupão outra vez. Apagar um
        cupão para o recriar dava a toda a gente um segundo desconto."""
        from app.models import CouponUsage, UserProfile

        db_session.add(UserProfile(media_user_id='51', username='bruno'))
        db_session.commit()
        cupao = data_manager.create_coupon({'code': 'PROMO', 'discount_type': 'percentage',
                                            'value': 10, 'max_uses': 100})
        assert data_manager.record_coupon_usage('PROMO', '51') is True

        assert data_manager.delete_coupon(cupao['id']) is True

        assert data_manager.get_coupon_by_code('PROMO') is None
        assert data_manager.get_all_coupons() == []
        assert CouponUsage.query.filter_by(coupon_id=cupao['id']).count() == 1

    def test_limpar_a_auditoria_de_cortes_nao_apaga_nada(self, db_session, data_manager):
        from app.models import StreamTerminationLog, UserProfile

        db_session.add(UserProfile(media_user_id='52', username='carla'))
        db_session.commit()
        data_manager.log_stream_termination('52', 'carla', 'Filme', 'Web', 'manual')

        data_manager.clear_all_stream_termination_logs()

        assert data_manager.get_stream_termination_logs() == []
        assert StreamTerminationLog.query.count() == 1

    def test_a_marca_de_agua_da_importacao_conta_os_apagados(self, db_session, data_manager):
        """⚠️ Ela não mostra nada: serve para não reimportar o que já foi
        importado. Ignorar os apagados fazia a importação seguinte reler tudo
        desde o início e duplicar o que tinha sido escondido."""
        from app.models import UserProfile

        db_session.add(UserProfile(media_user_id='53', username='diana'))
        db_session.commit()
        data_manager.log_stream_termination('53', 'diana', '-', '-', 'plugin_limit_blocked')
        data_manager.clear_all_stream_termination_logs()

        assert data_manager.get_last_termination_timestamp('plugin_limit_blocked') is not None

    def test_as_cobrancas_abandonadas_continuam_a_ser_APAGADAS(self, db_session, data_manager):
        """⚠️ Continua certo: uma cobrança nunca concluída e abandonada há
        semanas não é histórico financeiro — é lixo de QR codes que ninguém
        chegou a pagar."""
        from app.models import PixPayment, UserProfile

        db_session.add(UserProfile(media_user_id='54', username='eva'))
        db_session.commit()
        antiga = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        db_session.add(PixPayment(txid='t-velha', media_user_id='54', username='eva',
                                  value=25.0, status='ATIVA', created_at=antiga))
        db_session.commit()

        data_manager.delete_old_pending_payments(30)

        assert PixPayment.query.get('t-velha') is None
