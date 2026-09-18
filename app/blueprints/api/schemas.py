# app/blueprints/api/schemas.py

import re

from pydantic import BaseModel, Field, validator
from typing import List, Optional, Literal, Union
from datetime import datetime

# ⚠️ Estas vivem em `utils` porque o backend do servidor de média também
# precisa delas (num servidor de contas locais, o resgate de um convite é
# quem cria a conta) e uma camada de baixo não pode importar de
# `app/blueprints/`. Continuam a ser exportadas daqui com os mesmos nomes.
from ...utils.validacao import (  # noqa: F401  (reexportados)
    EMAIL_RE, TELEFONE_MAX, TELEFONE_MIN, validar_email, validar_telefone,
)

# Um código personalizado vira a chave primária do convite E um segmento do URL
# público (/invite/<code>). Antes era aceite tal e qual, sem qualquer limite:
#   • um código de 1 ou 2 caracteres é adivinhável à força bruta;
#   • '/' partia a rota e gerava um link permanentemente 404;
#   • espaços, '#' e '?' produziam links que morriam ao serem partilhados.
# O alfabeto é o mesmo do `secrets.token_urlsafe`, usado nos códigos automáticos,
# por isso nenhum convite gerado pelo painel deixa de ser válido.
CUSTOM_CODE_RE = re.compile(r'^[A-Za-z0-9_-]{4,64}$')

# ⚠️ Os campos de tempo de um convite tinham `ge=0` e mais nada, e isso não
# chegava: `create_invitation` soma-os a `datetime.now()`, e o `timedelta` de
# Python não aguenta qualquer número.
#
#     >>> datetime.now(timezone.utc) + timedelta(minutes=10**12)
#     OverflowError: date value out of range
#
# O sintoma era um 500 com traceback — na rota de administração e na dos bots.
# Pior: com `trial_duration_minutes`, a criação passava e a conta só rebentava
# no RESGATE, dentro de `agendar_fim_do_teste`, na cara de quem estava a
# entrar. Cinco anos é muito mais do que qualquer convite legítimo precisa e
# está a uma distância confortável do limite do `date`.
MAX_MINUTOS = 5 * 365 * 24 * 60

# Um convite é para um grupo de pessoas, não para o mundo. Sem teto, um engano
# de digitação no formulário criava um convite com mil milhões de vagas — que é
# o mesmo que um convite público e eterno, sem que nada no painel o diga. O
# `screens` sempre teve um limite; este não tinha nenhum.
MAX_UTILIZACOES = 1000

# A nota é para caber num cartão da lista, não para guardar um texto.
MAX_NOTA = 200


def _validar_nota(v):
    """Uma nota em branco é o mesmo que nota nenhuma."""
    if v is None:
        return None
    return str(v).strip() or None


def _validar_custom_code(v):
    """Partilhado pelos dois esquemas de criação de convite."""
    if v is None:
        return None
    v = str(v).strip()
    if not v:
        return None
    if not CUSTOM_CODE_RE.match(v):
        raise ValueError(
            "O código personalizado deve ter entre 4 e 64 caracteres e usar "
            "apenas letras, números, '-' e '_'."
        )
    return v

class CreateInviteSchema(BaseModel):
    libraries: List[str] = Field(..., min_items=1, description="Pelo menos uma biblioteca deve ser selecionada.")
    screens: int = Field(0, ge=0, le=6)
    allow_downloads: bool = False
    expires_in_minutes: Optional[int] = Field(None, ge=0, le=MAX_MINUTOS)
    trial_duration_minutes: int = Field(0, ge=0, le=MAX_MINUTOS)
    overseerr_access: bool = False
    custom_code: Optional[str] = None
    max_uses: int = Field(1, ge=1, le=MAX_UTILIZACOES)
    # O contacto pré-atribuído. Os dois são opcionais e independentes.
    telegram_id: Optional[str] = None
    discord_id: Optional[str] = None
    note: Optional[str] = Field(None, max_length=MAX_NOTA)

    @validator('custom_code')
    def custom_code_valido(cls, v):
        return _validar_custom_code(v)

    @validator('note')
    def nota_limpa(cls, v):
        return _validar_nota(v)


class CreateInviteBotSchema(BaseModel):
    """
    Esquema do endpoint de integração para bots (POST /api/invites/bot/create).

    Diferenças em relação ao esquema usado pelo painel:
      • é preciso UM contacto — 'telegram_id' ou 'discord_id' —, que é o
        propósito deste endpoint: o convite fica pré-atribuído a alguém.
      • 'libraries' é opcional: um bot raramente conhece os nomes das bibliotecas,
        por isso, se não for indicado, o servidor usa todas as disponíveis.

    ⚠️ O 'telegram_id' era OBRIGATÓRIO e passou a ser opcional. É uma folga, não
    uma quebra: os bots que já existem continuam a mandá-lo e continuam a
    funcionar. O que mudou é que um bot de Discord deixou de ser obrigado a
    inventar um Telegram ID para conseguir criar um convite.
    """
    # 'Union[str, int]' é deliberado: as APIs de bots do Telegram e do Discord
    # tratam o id como um INTEIRO, por isso um bot envia naturalmente
    # {"telegram_id": 123456789}. Se aceitássemos apenas 'str', o Pydantic
    # rejeitaria esses pedidos com 400 e a integração falhava logo à partida.
    # O validador abaixo converte tudo para texto.
    telegram_id: Optional[Union[str, int]] = Field(None, description="ID do chat/usuário no Telegram.")
    discord_id: Optional[Union[str, int]] = Field(None, description="ID do usuário no Discord.")
    libraries: Optional[List[str]] = None
    screens: int = Field(0, ge=0, le=6)
    allow_downloads: bool = False
    expires_in_minutes: Optional[int] = Field(None, ge=0, le=MAX_MINUTOS)
    trial_duration_minutes: int = Field(0, ge=0, le=MAX_MINUTOS)
    overseerr_access: bool = False
    custom_code: Optional[str] = None
    max_uses: int = Field(1, ge=1, le=MAX_UTILIZACOES)
    note: Optional[str] = Field(None, max_length=MAX_NOTA)

    @validator('custom_code')
    def custom_code_valido(cls, v):
        return _validar_custom_code(v)

    @validator('note')
    def nota_limpa(cls, v):
        return _validar_nota(v)

    @validator('telegram_id', 'discord_id')
    def contacto_limpo(cls, v):
        # Normaliza aqui também: o bot pode enviar o ID como número, que o
        # Pydantic converte para texto, possivelmente com espaços. Um valor em
        # branco é o mesmo que não o mandar.
        if v is None:
            return None
        return str(v).strip() or None

    @validator('discord_id', always=True)
    def pelo_menos_um_contacto(cls, v, values):
        # ⚠️ Corre no ÚLTIMO dos dois campos e com `always=True`, para ver o
        # telegram_id já validado em `values` e para correr mesmo quando nenhum
        # dos dois vem no pedido — sem isso, um corpo sem contacto nenhum
        # passava em silêncio e criava um convite que não fica atribuído a
        # ninguém, que é precisamente o que este endpoint não faz.
        if not v and not values.get('telegram_id'):
            raise ValueError("Informe 'telegram_id' ou 'discord_id'.")
        return v

class RenewSubscriptionSchema(BaseModel):
    months: int = Field(..., gt=0)
    base: Literal['today', 'expiry_date'] = 'today'
    base_date: Optional[str] = None
    expiration_time: Optional[str] = None

    @validator('base_date')
    def validate_base_date(cls, v):
        if v is None:
            return v
        try:
            datetime.strptime(v, '%Y-%m-%d')
            return v
        except ValueError:
            raise ValueError("O formato da data base deve ser YYYY-MM-DD")

    @validator('expiration_time')
    def validate_expiration_time(cls, v):
        if v is None:
            return v
        try:
            datetime.strptime(v, '%H:%M')
            return v
        except ValueError:
            raise ValueError("O formato da hora de expiração deve ser HH:MM")

class UpdateProfileSchema(BaseModel):
    name: Optional[str] = None
    telegram_user: Optional[str] = None
    discord_user_id: Optional[str] = None
    phone_number: Optional[str] = None
    expiration_datetime_local: Optional[str] = None

    _telefone = validator('phone_number', allow_reuse=True)(validar_telefone)
    
    @validator('expiration_datetime_local')
    def validate_expiration_datetime(cls, v):
        """Aceita `YYYY-MM-DDTHH:MM`, com ou sem deslocamento.

        ⚠️ **Com deslocamento é a forma boa** (`2026-09-05T23:59:00-03:00`): é
        ela que diz QUE INSTANTE a pessoa escolheu. Sem ele o painel tem de
        adivinhar o fuso, e adivinhava o do servidor — num contentor sem `TZ`
        isso é UTC, e um vencimento marcado para as 23:59 no Brasil ficava três
        horas mais cedo, a deslizar outras três a cada gravação (ver
        `_momento_do_vencimento`, em `api/users.py`).

        A forma sem deslocamento CONTINUA a ser aceite, e é lida no fuso do
        painel: é o que chega de um navegador com o JavaScript antigo em cache,
        e recusá-la trocaria um erro de três horas por um erro a gravar.
        """
        if v is None:
            return v
        try:
            datetime.fromisoformat(v)
            return v
        except (ValueError, TypeError):
            raise ValueError("Formato de data/hora de expiração inválido.")

class UpdateAccountProfileSchema(BaseModel):
    name: Optional[str] = None
    telegram_user: Optional[str] = None
    discord_user_id: Optional[str] = None
    phone_number: Optional[str] = None

    _telefone = validator('phone_number', allow_reuse=True)(validar_telefone)


class ContactosDoResgateSchema(BaseModel):
    """O que a página de convite envia depois de um resgate bem-sucedido.

    ⚠️ Os nomes aqui são os dos CANAIS (`whatsapp`, `telegram`, `discord`) e
    não os das colunas do perfil (`phone_number`, `telegram_user`,
    `discord_user_id`). A tradução é do `CANAIS_DO_RESGATE` — a mesma razão de
    o `CONTACTOS` existir: os dois lados nunca se chamaram igual, e já houve
    código a ler uma coluna que não existe e a parecer funcionar por causa de
    um `or` à frente.

    Todos são opcionais: preencher só um canal é uma escolha legítima, e o que
    não vier fica como está (ver `guardar_contactos`).
    """

    name: Optional[str] = None
    whatsapp: Optional[str] = None
    telegram: Optional[str] = None
    discord: Optional[str] = None

    # O mesmo validador dos outros formulários: só dígitos, 8 a 15, com o
    # código do país. Aqui ele serve para o 400 sair com a frase certa em vez
    # de o número ser gravado num formato que o envio não entende.
    _telefone = validator('whatsapp', allow_reuse=True)(validar_telefone)


class CreateCouponSchema(BaseModel):
    """
    Validação da criação de cupões.

    Antes, a rota aceitava qualquer coisa que passasse por 'float()': um cupão
    de -50% multiplicava o preço por 1,5 e um 'discount_type' desconhecido era
    aceite, anunciado como "aplicado com sucesso" e não descontava nada.
    """
    code: str = Field(..., min_length=1, max_length=64)
    discount_type: Literal['percentage', 'fixed']
    # 'gt=0': um desconto de zero (ou negativo) não é um desconto.
    value: float = Field(..., gt=0)
    # 0 = sem limite de utilizações, que é o que a lista de cupões sempre mostrou
    # ('∞') e o que o formulário envia quando o campo fica vazio.
    max_uses: int = Field(1, ge=0)
    is_active: bool = True
    # Apenas a data (YYYY-MM-DD): a hora é fixada no fim do dia, no fuso do painel.
    expires_at: Optional[str] = None

    @validator('code')
    def validate_code(cls, v):
        codigo = (v or '').strip().upper()
        if not codigo:
            raise ValueError("O código do cupom não pode estar vazio.")
        # Um código com espaços ou ';' seria impossível de escrever no formulário
        # de pagamento e sujaria o relatório CSV.
        if any(c.isspace() for c in codigo) or ';' in codigo:
            raise ValueError("O código do cupom não pode conter espaços nem ';'.")
        return codigo

    @validator('value')
    def validate_value(cls, v, values):
        if values.get('discount_type') == 'percentage' and v > 100:
            raise ValueError("Um desconto em porcentagem não pode ser superior a 100.")
        return round(float(v), 2)

    @validator('expires_at')
    def validate_expires_at(cls, v):
        if v is None or v == '':
            return None
        try:
            datetime.strptime(v, '%Y-%m-%d')
            return v
        except (ValueError, TypeError):
            raise ValueError("O formato da data de expiração deve ser YYYY-MM-DD")


class ChavesDaSubscricaoPush(BaseModel):
    """As duas chaves que o navegador entrega ao subscrever.

    🛡️ Validam-se AQUI, e não no momento do envio: uma chave malformada gravada
    na tabela só dava erro semanas depois, a cada notificação, e o log dizia
    apenas que a entrega tinha falhado. Recusar à entrada faz o navegador tentar
    outra vez.
    """

    # A chave pública do aparelho: um ponto não comprimido da curva P-256, que
    # em base64 de URL dá sempre 87 ou 88 caracteres.
    p256dh: str = Field(..., min_length=80, max_length=255)
    # O segredo de autenticação: 16 bytes, 22 caracteres em base64 de URL.
    auth: str = Field(..., min_length=16, max_length=64)

    @staticmethod
    def _bytes_da_chave(valor, nome, tamanho):
        from ...services.web_push import de_b64url

        try:
            bruto = de_b64url(valor)
        except Exception:
            raise ValueError(f"A chave '{nome}' não está em base64 de URL.")
        if len(bruto) != tamanho:
            raise ValueError(
                f"A chave '{nome}' devia ter {tamanho} bytes e tem {len(bruto)}.")
        return bruto

    @validator('p256dh')
    def validar_p256dh(cls, v):
        bruto = cls._bytes_da_chave(v, 'p256dh', 65)
        if bruto[0] != 0x04:
            raise ValueError("A chave pública do navegador não é um ponto não comprimido.")
        return v.strip()

    @validator('auth')
    def validar_auth(cls, v):
        cls._bytes_da_chave(v, 'auth', 16)
        return v.strip()


class SubscricaoPushSchema(BaseModel):
    """O que o navegador devolve de `pushManager.subscribe()`."""

    # 🛡️ O endereço é para onde o painel vai fazer POST às escuras, a partir do
    # servidor — por isso não basta ser HTTPS: **tem de ser de um serviço de
    # push conhecido** (`SERVICOS_DE_PUSH`). Só com o esquema verificado, quem
    # tivesse sessão registava um aparelho a apontar para um endereço INTERNO e
    # usava o painel para lhe bater de dentro da rede, com a rota `/push/test`
    # por gatilho. A coluna tem 512 caracteres.
    endpoint: str = Field(..., min_length=12, max_length=512)
    keys: ChavesDaSubscricaoPush
    # Como a pessoa reconhece este aparelho na lista ("Chrome no Android").
    device_label: Optional[str] = Field(None, max_length=120)

    @validator('endpoint')
    def validar_endpoint(cls, v):
        from urllib.parse import urlsplit

        from ...services.web_push import endpoint_permitido

        endereco = (v or '').strip()
        partes = urlsplit(endereco)
        if partes.scheme != 'https' or not partes.netloc:
            raise ValueError("O endereço de entrega tem de ser um URL https.")
        if not endpoint_permitido(endereco):
            raise ValueError(
                "O endereço de entrega não é de um serviço de push conhecido.")
        return endereco


class RemocaoDeSubscricaoPushSchema(BaseModel):
    endpoint: str = Field(..., min_length=12, max_length=512)
