# tests/test_erros_engolidos.py
"""Uma falha que ninguém regista é uma falha que ninguém corrige.

Estes testes não verificam o caminho FELIZ — verificam que, quando algo falha
por baixo, fica rasto. Cada um cobre um `except` que apanhava a exceção e a
deitava fora, deixando o painel a responder como se estivesse tudo bem.

São testes sobre o LOG, e isso é deliberado: em ambos os casos o comportamento
visível não muda (nem deve mudar — derrubar o pedido seria pior). O que estava
errado era só o silêncio, por isso é o silêncio que se testa.
"""

import logging

import pytest

from app import extensions


# ==========================================================================
# A consulta ao gateway, na rota que a página de pagamento faz polling
# ==========================================================================

class GatewayQueCai:
    """Um gateway em baixo: responde a tudo levantando."""

    def detail_pix_charge(self, txid):
        raise ConnectionError("gateway inacessível")

    def get_payment_details(self, txid):
        raise ConnectionError("gateway inacessível")


@pytest.mark.integration
def test_falha_do_gateway_no_polling_fica_no_log(client, data_manager, config_file, monkeypatch, caplog):
    """🐛 REGRESSÃO: era um `except Exception as e: pass`.

    A rota respondia o estado guardado — "aguardando pagamento" — sem uma linha
    no log. Quem está parado no QR code vê exatamente o mesmo que veria se não
    tivesse pago, e uma falha sistemática do gateway (credencial expirada,
    mudança de API) era indistinguível de um cliente que ainda não pagou.
    """
    config_file(IS_CONFIGURED=True)
    data_manager.set_user_profile("77", {"username": "ana"})
    data_manager.create_pix_payment(
        txid="TXID-DE-TESTE", media_user_id="77", username="ana", value=10.0,
        provider="EFI", screens=1, external_reference="ref",
    )
    extensions.db.session.commit()

    monkeypatch.setattr(extensions, "efi_manager", GatewayQueCai())

    with caplog.at_level(logging.WARNING):
        resposta = client.get("/api/payments/status/TXID-DE-TESTE")

    # O comportamento visível NÃO muda: o polling continua, e é o webhook que
    # confirma. Derrubar o pedido por o gateway estar em baixo seria pior.
    assert resposta.status_code == 200
    assert resposta.get_json()["success"] is True

    assert any(
        "gateway" in r.message.lower() and "EFI" in r.message
        for r in caplog.records if r.levelno >= logging.WARNING
    ), (
        'A falha do gateway voltou a ser engolida em silêncio. A rota responde '
        '"aguardando pagamento" na mesma, e agora sem ninguém saber porquê.\n'
        f'Registos vistos: {[r.message for r in caplog.records]}'
    )


@pytest.mark.integration
def test_o_txid_no_log_vai_mascarado(client, data_manager, config_file, monkeypatch, caplog):
    """🛡️ O txid é o identificador da cobrança e não entra em claro no log.

    É a mesma regra que o resto do módulo já segue (`mask_token`), e o log vai
    dentro do ZIP de backup.
    """
    config_file(IS_CONFIGURED=True)
    data_manager.set_user_profile("78", {"username": "bruno"})
    data_manager.create_pix_payment(
        txid="TXID-SECRETO-COMPLETO", media_user_id="78", username="bruno",
        value=10.0, provider="EFI", screens=1, external_reference="ref",
    )
    extensions.db.session.commit()

    monkeypatch.setattr(extensions, "efi_manager", GatewayQueCai())

    with caplog.at_level(logging.WARNING):
        client.get("/api/payments/status/TXID-SECRETO-COMPLETO")

    tudo = "\n".join(r.message for r in caplog.records)
    assert "TXID-SECRETO-COMPLETO" not in tudo, (
        "O txid apareceu em claro no log. Use `mask_token()`, como o resto do módulo."
    )


# ==========================================================================
# A classe inteira: nenhum `except:` nu em app/
# ==========================================================================

def test_nenhum_except_nu_no_codigo_da_aplicacao():
    """⚠️ Um `except:` nu não apanha só os erros — apanha TUDO.

    Sob gevent, que é como este painel corre, isso inclui o `GreenletExit`: é
    assim que um greenlet é morto, e engoli-lo faz o worker deixar de conseguir
    encerrar aquele pedido. Inclui também o `KeyboardInterrupt` e o
    `SystemExit`, que nunca são nossos para tratar — e o painel reinicia-se a si
    próprio por sinal (ver `_agendar_reinicio`).

    Havia quatro, todos à volta de um `int()` ou de um `fromisoformat()`: o que
    se queria apanhar cabia sempre em `(ValueError, TypeError)`. Escrever a
    lista custa dez segundos e é a diferença entre apanhar um engano de escrita
    e apanhar a ordem de encerramento do processo.
    """
    import ast
    from pathlib import Path

    nus = []
    for ficheiro in sorted(Path('app').rglob('*.py')):
        try:
            arvore = ast.parse(ficheiro.read_text(encoding='utf-8'))
        except SyntaxError:  # pragma: no cover - não deve acontecer
            continue
        for no in ast.walk(arvore):
            if isinstance(no, ast.ExceptHandler) and no.type is None:
                nus.append(f'{ficheiro.as_posix()}:{no.lineno}')

    assert not nus, (
        'Voltou a haver `except:` nu em app/:\n  ' + '\n  '.join(nus) +
        '\n\nNomeie o que quer apanhar — quase sempre (ValueError, TypeError). '
        'Se precisa mesmo de tudo, escreva `except Exception:`, que já deixa '
        'passar o GreenletExit, o KeyboardInterrupt e o SystemExit.'
    )
