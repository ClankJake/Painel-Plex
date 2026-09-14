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

⚠️ **E na primeira tentativa não chegou.** Faltavam duas coisas ao diagnóstico:

* o `Thread-1` do relatório era o timer do processo MESTRE, criado pelo
  `--preload` do gunicorn ANTES do `fork`. O worker herdava-o já marcado como
  "stopped" pelo `threading._after_fork` (que também o tira do `_active`), e
  cancelar o timer ATUAL nunca lhe tocava. Isso resolve-se no Dockerfile, e o
  teste que o prende está em `test_reinicio_do_painel.py`;
* o SIGTERM que o painel manda a si próprio **não chega ao
  `shutdown_scheduler`**: o worker do gunicorn instala os handlers dele por
  cima dos nossos depois do `fork`. Por isso a limpeza não pode depender do
  sinal — é `_agendar_reinicio` quem a faz, antes de mandar terminar.
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
        # ⚠️ Este é o caminho de quem corre o painel à mão (`python run.py`) ou
        # sob systemd — NÃO o do contentor. Sob gunicorn o SIGTERM é apanhado
        # pelo handler do worker, que substitui o nosso a seguir ao `fork`; é
        # por isso que a limpeza do reinício não pode depender dele.
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


class TestOReinicioCalaTudoAntesDeMandarTerminar:
    """🐛 A correção que faltava: não depender do sinal para limpar.

    `_agendar_reinicio` manda um SIGTERM ao próprio processo. Sob gunicorn esse
    sinal é apanhado pelo worker — o `shutdown_scheduler` do painel nunca corre
    — e tudo o que ficasse em voo só era desmontado no desmonte do
    interpretador, que é exatamente onde o timer do limitador rebenta.
    """

    def test_cala_os_servicos_antes_de_mandar_o_sinal(self, app_context, monkeypatch):
        import os as _os
        import threading
        import time

        from app.blueprints.api import system

        ordem = []
        terminou = threading.Event()

        monkeypatch.setattr(time, 'sleep', lambda s: None)
        monkeypatch.setattr('app.parar_servicos_de_fundo', lambda: ordem.append('calar'))

        def _matar(pid, sig):
            ordem.append('terminar')
            terminou.set()

        monkeypatch.setattr(_os, 'kill', _matar)

        system._agendar_reinicio('teste de reinício')

        assert terminou.wait(timeout=5), "O reinício não chegou a mandar o sinal."
        assert ordem == ['calar', 'terminar'], (
            "Os serviços de fundo têm de ser calados ANTES do SIGTERM: "
            f"correu {ordem}."
        )

    def test_o_timer_do_limitador_fica_mesmo_cancelado(self, app_context, monkeypatch):
        # O mesmo, visto do lado do sintoma: é este timer que deixava o
        # `KeyError` no log logo a seguir a uma instalação bem-sucedida.
        import os as _os
        import threading
        import time

        from app import extensions
        from app.blueprints.api import system

        extensions.limiter.storage.incr('teste-reinicio', 60)
        assert _timer_do_limitador().is_alive()

        terminou = threading.Event()
        monkeypatch.setattr(time, 'sleep', lambda s: None)
        monkeypatch.setattr(_os, 'kill', lambda pid, sig: terminou.set())

        system._agendar_reinicio('teste de reinício')

        assert terminou.wait(timeout=5)
        assert not _timer_do_limitador().is_alive()
