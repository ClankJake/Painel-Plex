# app/utils/validacao.py

"""Validações de campos que a pessoa escreve — email e telefone.

⚠️ **Estas vivem em `utils` e não nos esquemas da API** porque quem precisa
delas não é só uma rota. Num servidor de contas locais, resgatar um convite é
CRIAR a conta, e quem interpreta as credenciais é o backend do servidor de
média — que não pode importar de `app/blueprints/`: a camada de baixo a
depender da de cima é a forma mais rápida de ficar com um import circular e com
um backend que não se consegue testar sem levantar a aplicação Flask.

`app/blueprints/api/schemas.py` continua a expor os mesmos nomes, por isso nada
do que já os importava de lá precisa de mudar.
"""

import re

# ⚠️ O email e o telefone eram `Optional[str]` e mais nada: aceitavam qualquer
# coisa. Nenhum dos dois é usado para autenticar, por isso o sintoma nunca era
# um erro — era uma notificação que não chegava e uma pessoa que o Seerr não
# encontrava, meses depois, sem ninguém ligar as duas pontas.
#
# A expressão do email é PROPOSITADAMENTE larga (algo@algo.algo, sem espaços):
# validar emails a sério com uma expressão regular é um problema conhecido por
# não ter solução, e o que aqui interessa é apanhar o erro de escrita, não
# recusar um domínio exótico.
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]{2,}$')

# Só dígitos, entre 8 e 15 — o máximo do E.164. O que a pessoa escreve com
# parênteses, espaços e traços é limpo primeiro: o formato natural de escrever
# um número não pode ser um erro de validação.
TELEFONE_MIN, TELEFONE_MAX = 8, 15


def validar_email(v):
    """Aceita vazio (o email é opcional em todo o painel) ou algo com forma."""
    if v is None:
        return None
    limpo = str(v).strip().lower()
    if not limpo:
        return None
    if not EMAIL_RE.match(limpo):
        raise ValueError("Informe um e-mail válido, como nome@exemplo.com.")
    return limpo


def validar_telefone(v):
    """Devolve o número só com dígitos, que é o formato que o envio precisa.

    🐛 O destinatário do WhatsApp é `{phone_number}@s.whatsapp.net`: um número
    guardado como '(11) 99999-9999' produzia um identificador inválido e a
    mensagem não chegava — sem erro nenhum, porque o painel só sabe que
    entregou o pedido.
    """
    if v is None:
        return None
    so_digitos = re.sub(r'\D', '', str(v))
    if not so_digitos:
        return None
    if not (TELEFONE_MIN <= len(so_digitos) <= TELEFONE_MAX):
        raise ValueError(
            f"O telefone deve ter entre {TELEFONE_MIN} e {TELEFONE_MAX} dígitos, "
            "incluindo o código do país (ex.: 5511999999999)."
        )
    return so_digitos
