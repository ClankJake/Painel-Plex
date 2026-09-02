# tests/test_financial_export.py
"""
Testes da exportação do relatório financeiro em CSV.

Cobrem o que antes corria mal em silêncio: fórmulas injetadas através do nome de
utilizador, datas em UTC a divergirem do dashboard, períodos malformados aceites
sem erro e o rodapé de resumo escrito fora do contexto do pedido.
"""

import csv
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True)


@pytest.fixture()
def admin(client, configurada):
    """Sessão de administrador — é como o Flask-Login reconstrói o utilizador."""
    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": 1, "username": "admin", "role": "admin"}
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True
    return client


def _criar_pagamento(db_session, **campos):
    from app.models import PixPayment, UserProfile

    plex_user_id = campos.pop("user_plex_id", 1)
    if not UserProfile.query.get(plex_user_id):
        db_session.add(UserProfile(
            plex_user_id=plex_user_id,
            username=campos.get("username", f"utilizador{plex_user_id}"),
        ))
        db_session.flush()

    valores = {
        "txid": f"tx{plex_user_id}-{campos.get('created_at', '')}",
        "user_plex_id": plex_user_id,
        "username": "ana",
        "value": 25.0,
        "status": "CONCLUIDA",
        "provider": "EFI",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "screens": 1,
    }
    valores.update(campos)
    pagamento = PixPayment(**valores)
    db_session.add(pagamento)
    db_session.commit()
    return pagamento


def _exportar(admin, inicio, fim):
    return admin.get(f"/api/payments/financial/export-csv?start_date={inicio}&end_date={fim}")


def _linhas(resposta):
    texto = resposta.get_data(as_text=True).lstrip("﻿")
    return list(csv.reader(StringIO(texto), delimiter=";"))


class TestValidacaoDoPeriodo:
    def test_datas_em_falta(self, admin):
        assert admin.get("/api/payments/financial/export-csv").status_code == 400

    def test_data_malformada_e_recusada(self, admin):
        # Antes, o filtro comparava texto: uma data inválida devolvia 200 com um
        # relatório vazio, como se não houvesse transações no período.
        resposta = _exportar(admin, "31-12-2026", "2026-12-31")

        assert resposta.status_code == 400
        assert "AAAA-MM-DD" in resposta.get_json()["message"]

    def test_fim_antes_do_inicio(self, admin):
        assert _exportar(admin, "2026-05-10", "2026-05-01").status_code == 400

    def test_periodo_demasiado_longo(self, admin):
        assert _exportar(admin, "2020-01-01", "2026-01-01").status_code == 400

    def test_exige_administrador(self, client, configurada):
        assert _exportar(client, "2026-01-01", "2026-01-31").status_code in (302, 401, 403)


class TestConteudoDoRelatorio:
    def test_cabecalho_e_transacao(self, admin, db_session):
        _criar_pagamento(
            db_session, txid="tx-abc", username="ana", value=25.5,
            description="Renovacao", created_at="2026-05-10T12:00:00+00:00",
        )

        resposta = _exportar(admin, "2026-05-01", "2026-05-31")
        linhas = _linhas(resposta)

        assert resposta.status_code == 200
        assert linhas[0][:4] == ["Data", "Utilizador", "Descricao", "Valor (R$)"]
        assert linhas[1][1] == "ana"
        assert linhas[1][3] == "25,50"
        assert linhas[1][-1] == "tx-abc"

    def test_nome_de_ficheiro_usa_as_datas_validadas(self, admin):
        resposta = _exportar(admin, "2026-05-01", "2026-05-31")

        assert "relatorio_2026-05-01_a_2026-05-31.csv" in resposta.headers["Content-Disposition"]

    def test_resumo_final_soma_valores_e_creditos(self, admin, db_session):
        _criar_pagamento(db_session, txid="t1", value=10.0, created_at="2026-06-02T10:00:00+00:00")
        _criar_pagamento(db_session, txid="t2", value=15.0, referral_credit_used=5.0,
                         created_at="2026-06-03T10:00:00+00:00")

        linhas = [l for l in _linhas(_exportar(admin, "2026-06-01", "2026-06-30")) if l]
        resumo = {l[2]: l[3] for l in linhas if len(l) > 3 and l[0] == ""}

        # O rodapé é escrito no fim do streaming: se o gerador corresse fora do
        # contexto do pedido, estas traduções nem sequer existiriam.
        assert resumo["Total Arrecadado"] == "25,00"
        assert resumo["Crédito de Indicações Usado"] == "5,00"
        assert resumo["Total de Transações"] == "2"

    def test_marca_upgrades_pro_rata(self, admin, db_session):
        _criar_pagamento(db_session, txid="t-pro", is_proration=True,
                         created_at="2026-07-05T10:00:00+00:00")

        linhas = _linhas(_exportar(admin, "2026-07-01", "2026-07-31"))

        assert linhas[1][5] == "Upgrade pro-rata"

    def test_apenas_transacoes_concluidas(self, admin, db_session):
        _criar_pagamento(db_session, txid="t-ok", created_at="2026-08-05T10:00:00+00:00")
        _criar_pagamento(db_session, txid="t-pendente", status="ATIVA",
                         created_at="2026-08-06T10:00:00+00:00")

        linhas = [l for l in _linhas(_exportar(admin, "2026-08-01", "2026-08-31")) if l and l[0]]

        assert [l[-1] for l in linhas[1:]] == ["t-ok"]


class TestProtecaoContraFormulas:
    @pytest.mark.parametrize("nome", [
        "=1+1", "+SOMA(A1)", "-2+3", "@SUM(A1)",
    ])
    def test_nomes_perigosos_sao_neutralizados(self, admin, db_session, nome):
        # O nome vem do Plex, não do administrador: sem a plica à frente, o Excel
        # executava a fórmula ao abrir o relatório de contabilidade.
        _criar_pagamento(db_session, txid=f"tx-{nome}", username=nome,
                         created_at="2026-09-10T10:00:00+00:00")

        linhas = _linhas(_exportar(admin, "2026-09-01", "2026-09-30"))

        assert linhas[1][1] == "'" + nome

    def test_descricao_e_cupao_tambem_sao_neutralizados(self, admin, db_session):
        _criar_pagamento(db_session, txid="tx-desc", description="=HYPERLINK(1)",
                         coupon_code="=EVIL", created_at="2026-09-11T10:00:00+00:00")

        linhas = _linhas(_exportar(admin, "2026-09-01", "2026-09-30"))

        assert linhas[1][2] == "'=HYPERLINK(1)"
        assert linhas[1][7] == "'=EVIL"

    def test_texto_normal_nao_e_alterado(self, admin, db_session):
        _criar_pagamento(db_session, txid="tx-normal", username="ana maria",
                         created_at="2026-09-12T10:00:00+00:00")

        linhas = _linhas(_exportar(admin, "2026-09-01", "2026-09-30"))

        assert linhas[1][1] == "ana maria"


class TestFusoHorario:
    def test_o_periodo_segue_o_fuso_do_painel(self, admin, db_session, monkeypatch):
        import pytz
        import app.blueprints.api.payments as payments

        # Painel em UTC-3: uma transação às 01:00 UTC do dia 2 ainda pertence ao
        # dia 1 para quem vê o dashboard, e é isso que o relatório tem de refletir.
        monkeypatch.setattr(payments, "get_app_timezone", lambda: pytz.timezone("America/Sao_Paulo"))
        _criar_pagamento(db_session, txid="tx-madrugada", created_at="2026-10-02T01:00:00+00:00")

        do_dia_1 = _linhas(_exportar(admin, "2026-10-01", "2026-10-01"))
        do_dia_2 = _linhas(_exportar(admin, "2026-10-02", "2026-10-02"))

        assert [l[-1] for l in do_dia_1 if l and l[0]][1:] == ["tx-madrugada"]
        assert [l[-1] for l in do_dia_2 if l and l[0]][1:] == []

    def test_a_hora_mostrada_e_a_hora_local(self, admin, db_session, monkeypatch):
        import pytz
        import app.blueprints.api.payments as payments

        monkeypatch.setattr(payments, "get_app_timezone", lambda: pytz.timezone("America/Sao_Paulo"))
        _criar_pagamento(db_session, txid="tx-hora", created_at="2026-10-15T18:30:00+00:00")

        linhas = _linhas(_exportar(admin, "2026-10-15", "2026-10-15"))

        assert linhas[1][0] == "2026-10-15 15:30:00"


class TestStreamingPorLotes:
    def test_relatorio_grande_sai_completo(self, admin, db_session):
        base = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        for i in range(120):
            _criar_pagamento(
                db_session, txid=f"t-lote-{i}", value=1.0,
                created_at=(base + timedelta(minutes=i)).isoformat(),
            )

        linhas = [l for l in _linhas(_exportar(admin, "2026-11-01", "2026-11-30")) if l and l[0]]

        assert len(linhas) == 121  # cabeçalho + 120 transações


class TestFalhaAMeioDaGeracao:
    def test_relatorio_interrompido_e_marcado_como_incompleto(self, admin, db_session, monkeypatch):
        # A resposta já começou a ser enviada quando o erro acontece: não há como
        # devolver um 500. O ficheiro tem de dizer que está incompleto, em vez de
        # terminar a meio e parecer bom.
        def a_meio(*args, **kwargs):
            yield {
                "created_at": "2026-12-01T10:00:00+00:00", "username": "ana", "value": 10.0,
                "description": "Renovacao", "provider": "EFI", "txid": "tx-ok",
                "referral_credit_used": 0, "is_proration": False,
            }
            raise RuntimeError("base de dados indisponível")

        monkeypatch.setattr(
            type(__import__("app").extensions.data_manager),
            "iter_payments_for_export",
            lambda self, *a, **k: a_meio(),
        )

        texto = _exportar(admin, "2026-12-01", "2026-12-31").get_data(as_text=True)

        assert "tx-ok" in texto
        assert "RELATÓRIO INCOMPLETO" in texto
        assert "Total Arrecadado" not in texto
