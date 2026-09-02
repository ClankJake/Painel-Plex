# tests/test_models.py
"""Modelo de utilizador da sessão e valores por omissão dos modelos da BD."""

import json

import pytest

from app.extensions import MyAnonymousUser
from app.models import Coupon, Task, User, UserProfile

pytestmark = pytest.mark.integration


class TestUser:
    def test_utilizador_comum(self):
        user = User(id=1, username="ana")

        assert user.is_admin() is False
        assert user.is_authenticated is True
        assert user.get_id() == "1"

    def test_administrador(self):
        assert User(id=1, username="ana", role="admin").is_admin() is True

    def test_to_dict(self):
        user = User(id=7, username="ana", email="ana@exemplo.com", thumb="/t.png", role="admin")

        assert user.to_dict() == {
            "id": 7,
            "username": "ana",
            "email": "ana@exemplo.com",
            "thumb": "/t.png",
            "role": "admin",
        }

    def test_to_json(self):
        user = User(id=7, username="ana")

        assert json.loads(user.to_json())["username"] == "ana"

    def test_to_dict_pode_reconstruir_o_utilizador(self):
        # É assim que o Flask-Login recarrega o utilizador a partir da sessão.
        original = User(id=7, username="ana", role="admin")
        reconstruido = User(**original.to_dict())

        assert reconstruido.to_dict() == original.to_dict()


class TestAnonimo:
    def test_visitante_nunca_e_admin(self):
        visitante = MyAnonymousUser()

        assert visitante.is_admin() is False
        assert visitante.username == "Guest"
        assert visitante.is_authenticated is False


class TestValoresPorOmissao:
    def test_tarefa_recebe_um_id_unico(self, db_session):
        primeira, segunda = Task(name="sync"), Task(name="sync")
        db_session.add_all([primeira, segunda])
        db_session.commit()

        assert primeira.id != segunda.id
        assert primeira.status == "pending"
        assert primeira.created_at is not None

    def test_cupao(self, db_session):
        cupao = Coupon(code="PROMO", discount_type="percentage", value=10.0)
        db_session.add(cupao)
        db_session.commit()

        assert cupao.is_active is True
        assert cupao.use_count == 0
        assert cupao.max_uses == 1

    def test_perfil_de_utilizador(self, db_session):
        perfil = UserProfile(plex_user_id=1, username="ana")
        db_session.add(perfil)
        db_session.commit()

        assert perfil.status == "active"
        assert perfil.screen_limit == 0
        assert perfil.xp == 0
        assert perfil.lifetime_xp == 0
        assert perfil.referral_credit == 0.0
        assert perfil.referral_rewarded is False
        assert perfil.hide_from_leaderboard is False

    def test_username_e_unico(self, db_session):
        from sqlalchemy.exc import IntegrityError

        db_session.add(UserProfile(plex_user_id=1, username="ana"))
        db_session.commit()

        db_session.add(UserProfile(plex_user_id=2, username="ana"))
        with pytest.raises(IntegrityError):
            db_session.commit()
