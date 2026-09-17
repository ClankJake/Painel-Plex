# tests/test_ddi_do_telefone_na_conta.py
"""O DDI aparecia duas vezes na "Minha Conta", e a gravação seguinte gravava-o.

🐛 REGRESSÃO REAL: o painel guarda o telefone **só com dígitos**
(`UserProfile._normalizar_telefone` e `validar_telefone`), por isso o que está
na base de dados é `5521999999999` — sem o `+`. O formulário de contactos
procurava o país com `numero.startsWith('+55')`, comparando o número guardado
com o código TAL COMO ele aparece na lista de países. Isso é sempre falso, e o
ramo de recurso punha o número INTEIRO dentro do campo nacional: quem abria a
página via `5521999999999` ao lado de uma caixa a dizer "Brasil (+55)".

E não ficava pela aparência. Gravar outra vez juntava o `+55` da caixa ao que
estava no campo: `555521999999999`, quinze dígitos — dentro do limite do
`validar_telefone`, portanto aceite sem uma queixa. Como o destinatário do
WhatsApp é `{phone_number}@s.whatsapp.net`, a pessoa deixava de receber
qualquer aviso de vencimento e o painel continuava a dizer que tinha enviado.

⚠️ E cortar o prefixo à bruta não chegava: nem todo o número guardado TEM
código de país (o campo do administrador é uma caixa de texto solta, e o
`normalize_phone` só acrescenta o DDI no ENVIO, sem reescrever o perfil).
`11999999999` teria passado por um número dos EUA e ficaria truncado. A
separação usa por isso a MESMA heurística conservadora do backend, que é o que
os casos abaixo fixam nos dois sentidos — o que se mostra e o que se regrava.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
ACCOUNT_JS = RAIZ / 'app' / 'static' / 'js' / 'account.js'
ACCOUNT_HTML = RAIZ / 'app' / 'templates' / 'account.html'

precisa_de_node = pytest.mark.skipif(
    shutil.which('node') is None, reason="precisa do Node para correr o JavaScript"
)

# Os países da caixa, como o `initContactForm` os declara.
CODIGOS = ['55', '351', '244', '258', '238', '1', '44', '34', '33', '49']

# (número como está gravado, DDI esperado na caixa, parte nacional no campo)
CASOS = [
    # O caso reportado: guardado com o DDI, que não pode reaparecer no campo.
    ('5521999999999', '55', '21999999999'),
    ('5511988887777', '55', '11988887777'),
    # Legado sem DDI: fica inteiro no campo, e é o padrão que o completa.
    ('11999999999', '55', '11999999999'),
    ('1133334444', '55', '1133334444'),
    # ⚠️ Um telemóvel de Santa Maria começa por "55" e NÃO leva DDI a mais.
    ('55999999999', '55', '55999999999'),
    # Internacionais: o prefixo mais longo ganha, e o `+1` não apanha o Brasil.
    ('15551234567', '1', '5551234567'),
    ('351912345678', '351', '912345678'),
    ('', '55', ''),
]


def _correr_no_node(corpo, *argumentos):
    """Corre o bloco REAL do `account.js`, não uma cópia das regras aqui.

    ⚠️ Uma segunda implementação em Python passaria a testar-se a si própria —
    é a armadilha que o CLAUDE.md já regista sobre os duplos de backend com a
    sua própria cópia das regras. O que se extrai é o bloco puro do ficheiro,
    entre o `soDigitos` e o `initContactForm` (que já toca no DOM).
    """
    fonte = ACCOUNT_JS.read_text(encoding='utf-8')
    inicio = fonte.index('const soDigitos')
    fim = fonte.index('const initContactForm')
    script = fonte[inicio:fim] + corpo

    resultado = subprocess.run(
        ['node', '--input-type=module', '-e', script, '--', *argumentos],
        capture_output=True, text=True, check=True, cwd=RAIZ,
    )
    return json.loads(resultado.stdout)


def test_o_codigo_do_pais_nao_e_comparado_com_o_sinal_de_mais():
    """O guarda que corre sempre, mesmo sem Node.

    O número guardado nunca tem `+`: comparar com ele é o bug original, e é um
    `startsWith` que não dá erro nenhum — só devolve sempre falso.
    """
    fonte = ACCOUNT_JS.read_text(encoding='utf-8')
    assert 'startsWith(c.code)' not in fonte, (
        "O código do país está a ser comparado com o número guardado, que só "
        "tem dígitos — compare dígitos com dígitos."
    )
    assert not re.search(r"code:\s*'\+", fonte), (
        "Os códigos de país da caixa de contactos não devem trazer o '+': ele "
        "é decoração do rótulo, e não faz parte do que se compara nem do que "
        "se grava."
    )


def test_o_ddi_padrao_das_configuracoes_chega_ao_formulario():
    """O mesmo código do país que o envio de WhatsApp usa.

    Duas respostas diferentes para "de que país é este número" no mesmo painel
    foi exatamente o que pôs o DDI duas vezes à vista.
    """
    html = ACCOUNT_HTML.read_text(encoding='utf-8')
    assert 'data-config-default-country-code' in html
    assert 'WHATSAPP_DEFAULT_COUNTRY_CODE' in html
    assert 'state.config.defaultCountryCode' in ACCOUNT_JS.read_text(encoding='utf-8')


@precisa_de_node
def test_a_separacao_do_ddi_devolve_o_que_esta_gravado():
    obtido = _correr_no_node(
        """
const casos = JSON.parse(process.argv[1]);
const codigos = JSON.parse(process.argv[2]);
console.log(JSON.stringify(casos.map(([guardado]) => {
    const r = separarCodigoDoPais(guardado, codigos, '55');
    return [r.codigo, r.numero];
})));
""",
        json.dumps(CASOS),
        json.dumps(CODIGOS),
    )

    for (guardado, ddi, nacional), (ddi_obtido, nacional_obtido) in zip(CASOS, obtido):
        assert [ddi_obtido, nacional_obtido] == [ddi, nacional], (
            f"{guardado!r} foi separado em (+{ddi_obtido}, {nacional_obtido!r})"
        )


@precisa_de_node
def test_abrir_a_pagina_e_gravar_sem_tocar_em_nada_nao_muda_o_numero():
    """A ida e volta que estava partida.

    Mostrar `5521999999999` no campo nacional e voltar a gravar dava
    `555521999999999`.
    """
    regravado = _correr_no_node(
        """
const codigos = JSON.parse(process.argv[2]);
console.log(JSON.stringify(JSON.parse(process.argv[1]).map(([guardado]) => {
    const r = separarCodigoDoPais(guardado, codigos, '55');
    // A mesma proteção do `saveContactDetails`: o campo pode já trazer o DDI.
    let nacional = r.numero;
    if (nacional.startsWith(r.codigo) && !temFormatoNacional(nacional, r.codigo)) {
        nacional = nacional.slice(r.codigo.length);
    }
    return nacional ? `${r.codigo}${nacional}` : '';
})));
""",
        json.dumps(CASOS),
        json.dumps(CODIGOS),
    )

    assert regravado == [
        # Quem já tinha o DDI volta exatamente como estava.
        '5521999999999',
        '5511988887777',
        # Quem não tinha ganha-o UMA vez — que é o que o envio já assumia.
        '5511999999999',
        '551133334444',
        '5555999999999',
        '15551234567',
        '351912345678',
        '',
    ]


@precisa_de_node
def test_um_numero_colado_com_o_ddi_nao_fica_com_ele_a_dobrar():
    """Quem escreve `5521999999999` no campo nacional não grava `555521...`.

    É o que a página mostrava antes desta correção, e o resultado tinha quinze
    dígitos — dentro do limite do `validar_telefone`, portanto aceite.
    """
    escritos = ['5521999999999', '21999999999', '(21) 99999-9999', '55999999999', '']
    assert _correr_no_node(
        """
console.log(JSON.stringify(JSON.parse(process.argv[1]).map((escrito) => {
    const ddi = '55';
    let nacional = soDigitos(escrito);
    if (nacional.startsWith(ddi) && !temFormatoNacional(nacional, ddi)) {
        nacional = nacional.slice(ddi.length);
    }
    return nacional ? `${ddi}${nacional}` : '';
})));
""",
        json.dumps(escritos),
    ) == [
        '5521999999999',
        '5521999999999',
        # O formato natural de escrever também chega ao mesmo sítio.
        '5521999999999',
        # ⚠️ DDD 55 é Santa Maria: aqui o "55" da frente é o DDD, não o DDI.
        '5555999999999',
        '',
    ]
