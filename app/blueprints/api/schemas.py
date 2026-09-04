# app/blueprints/api/schemas.py

import re

from pydantic import BaseModel, Field, validator
from typing import List, Optional, Literal, Union
from datetime import datetime

# Um código personalizado vira a chave primária do convite E um segmento do URL
# público (/invite/<code>). Antes era aceite tal e qual, sem qualquer limite:
#   • um código de 1 ou 2 caracteres é adivinhável à força bruta;
#   • '/' partia a rota e gerava um link permanentemente 404;
#   • espaços, '#' e '?' produziam links que morriam ao serem partilhados.
# O alfabeto é o mesmo do `secrets.token_urlsafe`, usado nos códigos automáticos,
# por isso nenhum convite gerado pelo painel deixa de ser válido.
CUSTOM_CODE_RE = re.compile(r'^[A-Za-z0-9_-]{4,64}$')


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
    expires_in_minutes: Optional[int] = Field(None, ge=0)
    trial_duration_minutes: int = Field(0, ge=0)
    overseerr_access: bool = False
    custom_code: Optional[str] = None
    max_uses: int = Field(1, ge=1)
    telegram_id: Optional[str] = None # Novo campo opcional

    @validator('custom_code')
    def custom_code_valido(cls, v):
        return _validar_custom_code(v)


class CreateInviteBotSchema(BaseModel):
    """
    Esquema do endpoint de integração para bots (POST /api/invites/bot/create).

    Diferenças em relação ao esquema usado pelo painel:
      • 'telegram_id' é OBRIGATÓRIO — é o propósito deste endpoint.
      • 'libraries' é opcional: um bot raramente conhece os nomes das bibliotecas,
        por isso, se não for indicado, o servidor usa todas as disponíveis.
    """
    # 'Union[str, int]' é deliberado: a API de bots do Telegram trata o chat_id como
    # um INTEIRO, por isso um bot envia naturalmente {"telegram_id": 123456789}.
    # Se aceitássemos apenas 'str', o Pydantic rejeitaria esses pedidos com 400 e a
    # integração falharia logo à partida. O validador abaixo converte tudo para texto.
    telegram_id: Union[str, int] = Field(..., description="ID do chat/utilizador no Telegram.")
    libraries: Optional[List[str]] = None
    screens: int = Field(0, ge=0, le=6)
    allow_downloads: bool = False
    expires_in_minutes: Optional[int] = Field(None, ge=0)
    trial_duration_minutes: int = Field(0, ge=0)
    overseerr_access: bool = False
    custom_code: Optional[str] = None
    max_uses: int = Field(1, ge=1)

    @validator('custom_code')
    def custom_code_valido(cls, v):
        return _validar_custom_code(v)

    @validator('telegram_id')
    def telegram_id_not_blank(cls, v):
        # Normaliza aqui também: o bot pode enviar o ID como número, que o Pydantic
        # converte para string, possivelmente com espaços.
        v = str(v).strip()
        if not v:
            raise ValueError("O telegram_id não pode estar vazio.")
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
    
    @validator('expiration_datetime_local')
    def validate_expiration_datetime(cls, v):
        if v is None:
            return v
        try:
            # Tenta analisar o formato esperado (YYYY-MM-DDTHH:MM)
            datetime.fromisoformat(v)
            return v
        except (ValueError, TypeError):
            raise ValueError("Formato de data/hora de expiração inválido.")

class UpdateAccountProfileSchema(BaseModel):
    name: Optional[str] = None
    telegram_user: Optional[str] = None
    discord_user_id: Optional[str] = None
    phone_number: Optional[str] = None


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
            raise ValueError("O código do cupão não pode estar vazio.")
        # Um código com espaços ou ';' seria impossível de escrever no formulário
        # de pagamento e sujaria o relatório CSV.
        if any(c.isspace() for c in codigo) or ';' in codigo:
            raise ValueError("O código do cupão não pode conter espaços nem ';'.")
        return codigo

    @validator('value')
    def validate_value(cls, v, values):
        if values.get('discount_type') == 'percentage' and v > 100:
            raise ValueError("Um desconto em percentagem não pode ser superior a 100.")
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
