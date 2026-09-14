# tests/test_encerramento_limpo.py

"""O que fica a acordar depois de o painel ser mandado terminar.

🐛 REGRESSÃO REPORTADA: logo a seguir a concluir o assistente de instalação num
contentor Docker, o log recebia isto — com a instalação a ter corrido BEM:

    File "threading.py", line 1111, in _delete
      del _active[get_ident()]
    KeyError: 271255370948160
    <Greenlet ...: <bound method Thread._bootstrap of
     <Timer(Thread-1, stopped 271255370948160)>>> failed with KeyError

O `Thread-1` é a pista: é o PRIMEIRO thread do processo. Vem do armazenamento
em memória do Flask-Limiter (`limits.storage.MemoryStorage`), que arranca um
`threading.Timer` no construtor para varrer as contagens expiradas — e volta a
marcá-lo a CADA pedido que passe pelo limitador.

O assistente manda-se reiniciar a si próprio (`os.kill(SIGTERM)`, dois segundos
depois de gravar), por isso há sempre um timer em voo quando o processo morre.
Sob gevent ele é um greenlet embrulhado na contabilidade do `threading`: acorda
com o `threading._active` já desmontado e rebenta.

Não se perde trabalho nenhum — mas parece uma falha, e não é.
"""

import pytest

pytestmark = pytest.mark.integration


def _timer_do_limitador():
    from app import extensions

    return getattr(getattr(extensions.limiter, 'storage', None), 'timer', None)


class TestOTimerDoLimitador:
    def test_existe_e_e_um_threading_Timer(self, app_context):
        # Se um dia deixar de existir, este teste avisa — e o
        # `_parar_o_limitador` passa a ser código morto.
        #
        # ⚠️ Não se prende o NOME. No relatório ele era `Thread-1` (o primeiro
        # thread do processo, porque o limitador arranca cedo), mas numa suíte
        # completa já há outros antes — prender o número fazia o teste falhar
        # conforme a ordem em que corresse.
        import threading

        assert isinstance(_timer_do_limitador(), threading.Timer)

    def test_um_pedido_limitado_volta_a_marca_lo(self, app_context):
        # É por isto que há sempre um em voo: cada `incr` reagenda.
        from app import extensions

        extensions.limiter.storage.incr('teste-encerramento', 60)

        assert _timer_do_limitador().is_alive()


class TestOEncerramentoOCancela:
    def test_parar_servicos_de_fundo_cancela_o_timer(self, app_context):
        from app import extensions, parar_servicos_de_fundo

        extensions.limiter.storage.incr('teste-encerramento', 60)
        assert _timer_do_limitador().is_alive()

        parar_servicos_de_fundo()

        assert not _timer_do_limitador().is_alive()

    def test_o_handler_de_sinal_tambem(self, app_context):
        # É este o caminho do SIGTERM que o próprio assistente dispara.
        from app import extensions, shutdown_scheduler

        extensions.limiter.storage.incr('teste-encerramento', 60)
        assert _timer_do_limitador().is_alive()

        # Sem `signum`: queremos a limpeza, não que o teste se mate a si próprio.
        shutdown_scheduler()

        assert not _timer_do_limitador().is_alive()

    def test_nao_rebenta_se_o_limitador_nao_tiver_armazenamento(self, app_context, monkeypatch):
        # O `timer` não é API pública do `limits`: o dia em que desaparecer, o
        # encerramento não pode ir abaixo por causa disso.
        from app import _parar_o_limitador, extensions

        monkeypatch.setattr(extensions, 'limiter', object())
        _parar_o_limitador()
