# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R16 (VD-C-173): federazione OpenID completa secondo OpenID Federation 1.0.

Politiche dei metadati (operatori, combinazione fra livelli, ``metadata_policy_crit``),
vincoli (``max_path_length``, ``naming_constraints``, ``allowed_entity_types``), trust mark
(firma, emittente ammesso dalla configurazione e dal Trust Anchor, delega del proprietario,
entità e tipo, scadenza, stato/revoca), ``crit``, scadenza e ritiro delle dichiarazioni.
Domini di prova ``.example``.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import quote

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from libs.shared.security.oidc_federazione import CatenaNonValida, risolvi
from libs.shared.security.oidc_politiche import (
    PoliticaNonValida,
    applica,
    combina,
    controlla_vincoli,
)

TA, INT, OP = "https://ta.example", "https://intermedio.example", "https://op.example"
TMI = "https://marchi.example"
TIPO = "https://ta.example/marchi/spid-op"


def _jwk(k: Any, kid: str) -> dict[str, Any]:
    d = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(k.public_key()))
    d["kid"] = kid
    return d


def _firma(claims: dict[str, Any], k: Any, kid: str, typ: str = "entity-statement+jwt") -> str:
    return jwt.encode(claims, k, algorithm="ES256", headers={"kid": kid, "typ": typ})


def _chiave() -> Any:
    return ec.generate_private_key(ec.SECP256R1())


class Fed:
    """TA → INT → OP; TMI (emittente di trust mark) subordinato del TA. Ogni dichiarazione
    si costruisce dai dizionari modificabili prima di ``pagine()``."""

    def __init__(self) -> None:
        self.ora = int(time.time())
        self.k = {n: _chiave() for n in ("ta", "int", "op", "tmi", "prop")}
        self.ta_jwks = {"keys": [_jwk(self.k["ta"], "ta1")]}
        self.op_meta: dict[str, Any] = {"issuer": OP, "scopes_supported": ["openid", "profile"],
                                        "token_endpoint_auth_methods_supported":
                                            ["private_key_jwt"],
                                        "jwks": {"keys": []}}
        self.ss_int_op: dict[str, Any] = {}     # claim in più nella SS dell'intermedio sull'OP
        self.ss_ta_int: dict[str, Any] = {}     # claim in più nella SS del TA sull'intermedio
        self.ec_ta: dict[str, Any] = {}
        self.ec_op: dict[str, Any] = {}
        self.marchi: list[dict[str, Any]] = []
        self.stato = "active"
        self.ritirate: set[str] = set()

    def base(self, **kw: Any) -> dict[str, Any]:
        return {"iat": self.ora, "exp": self.ora + 3600, **kw}

    def marchio(self, *, emittente: str = TMI, sub: str = OP, tipo: str = TIPO,
                chiave: str = "tmi", kid: str = "m1", **extra: Any) -> dict[str, Any]:
        token = _firma(self.base(iss=emittente, sub=sub, trust_mark_type=tipo, **extra),
                       self.k[chiave], kid, "trust-mark+jwt")
        return {"trust_mark_type": tipo, "trust_mark": token}

    def pagine(self) -> dict[str, str]:
        p: dict[str, str] = {}

        def ec_(chi: str, chiave: str, kid: str, **kw: Any) -> str:
            return _firma(self.base(iss=chi, sub=chi, jwks={"keys": [_jwk(self.k[chiave], kid)]},
                                    **kw), self.k[chiave], kid)

        p[f"{TA}/.well-known/openid-federation"] = ec_(
            TA, "ta", "ta1", metadata={"federation_entity": {
                "federation_fetch_endpoint": f"{TA}/fetch"}}, **self.ec_ta)
        p[f"{INT}/.well-known/openid-federation"] = ec_(
            INT, "int", "i1", authority_hints=[TA],
            metadata={"federation_entity": {"federation_fetch_endpoint": f"{INT}/fetch"}})
        p[f"{OP}/.well-known/openid-federation"] = ec_(
            OP, "op", "k1", authority_hints=[INT], trust_marks=self.marchi,
            metadata={"openid_provider": self.op_meta, "federation_entity": {}}, **self.ec_op)
        p[f"{TMI}/.well-known/openid-federation"] = ec_(
            TMI, "tmi", "m1", authority_hints=[TA], metadata={"federation_entity": {
                "federation_trust_mark_status_endpoint": f"{TMI}/stato"}})

        def ss(emittente: str, chiave: str, kid: str, sub: str, sk: str, skid: str,
               extra: dict[str, Any]) -> str:
            return _firma(self.base(iss=emittente, sub=sub,
                                    jwks={"keys": [_jwk(self.k[sk], skid)]}, **extra),
                          self.k[chiave], kid)

        p[f"{TA}/fetch?sub={quote(INT, safe='')}"] = ss(TA, "ta", "ta1", INT, "int", "i1",
                                                        self.ss_ta_int)
        p[f"{TA}/fetch?sub={quote(TMI, safe='')}"] = ss(TA, "ta", "ta1", TMI, "tmi", "m1", {})
        p[f"{INT}/fetch?sub={quote(OP, safe='')}"] = ss(INT, "int", "i1", OP, "op", "k1",
                                                        self.ss_int_op)
        for u in self.ritirate:
            p.pop(u, None)
        return p

    def risolvi(self, **kw: Any) -> Any:
        pagine = self.pagine()

        async def scarica(u: str) -> str:
            if u not in pagine:
                # come il gateway: una risposta diversa da 200 invalida la catena
                raise CatenaNonValida("risposta 404 da un indirizzo di federazione")
            return pagine[u]

        async def invia(u: str, campi: Any) -> str:
            assert u == f"{TMI}/stato" and campi["trust_mark"]
            return _firma(self.base(iss=TMI, status=self.stato, trust_mark=campi["trust_mark"]),
                          self.k["tmi"], "m1", "trust-mark-status-response+jwt")

        return asyncio.run(risolvi(OP, trust_anchor=TA, chiavi_trust_anchor=self.ta_jwks,
                                   scarica=scarica, invia=invia, **kw))


# ── catena di base ─────────────────────────────────────────────────────

def test_catena_con_intermedio_senza_politiche() -> None:
    c = Fed().risolvi()
    assert c.percorso == (OP, INT, TA) and c.metadati["issuer"] == OP


def test_dichiarazione_ritirata_dal_superiore_invalida_la_catena() -> None:
    """Revoca: il superiore non pubblica più il Subordinate Statement."""
    f = Fed()
    f.ritirate.add(f"{INT}/fetch?sub={quote(OP, safe='')}")
    with pytest.raises(CatenaNonValida, match="404"):
        f.risolvi()


def test_dichiarazione_scaduta_e_scadenza_della_catena() -> None:
    f = Fed()
    f.ss_int_op = {"exp": f.ora - 3600}
    with pytest.raises(CatenaNonValida, match="scaduta"):
        f.risolvi()
    f = Fed()
    f.ss_ta_int = {"exp": f.ora + 600}
    assert f.risolvi().scade == f.ora + 600


def test_claim_critico_non_supportato() -> None:
    f = Fed()
    f.ss_int_op = {"crit": ["regola_ignota"], "regola_ignota": 1}
    with pytest.raises(CatenaNonValida, match="critici"):
        f.risolvi()
    f = Fed()
    f.ss_int_op = {"crit": ["constraints"], "constraints": {}}
    assert f.risolvi().percorso[-1] == TA


# ── metadata_policy ───────────────────────────────────────────────────

def test_politiche_combinate_fra_livelli_e_applicate() -> None:
    f = Fed()
    f.op_meta["contacts"] = ["op@op.example"]
    f.ss_ta_int = {"metadata_policy": {"openid_provider": {
        "scopes_supported": {"subset_of": ["openid", "profile", "email"]},
        "token_endpoint_auth_methods_supported": {"one_of": ["private_key_jwt"],
                                                  "essential": True},
        "contacts": {"add": ["ta@ta.example"]},
        "id_token_signing_alg_values_supported": {"default": ["ES256"]},
    }}}
    f.ss_int_op = {"metadata_policy": {"openid_provider": {
        "scopes_supported": {"subset_of": ["openid"]},       # intersezione: solo openid
        "contacts": {"add": ["int@intermedio.example"]},
        "request_object_signing_alg_values_supported": {"value": ["ES256"]},
    }}}
    # one_of su un elenco: il valore deve essere UNO dei valori → si usa uno scalare
    f.op_meta["token_endpoint_auth_methods_supported"] = "private_key_jwt"
    m = f.risolvi().metadati
    assert m["scopes_supported"] == ["openid"]
    assert m["contacts"] == ["op@op.example", "ta@ta.example", "int@intermedio.example"]
    assert m["id_token_signing_alg_values_supported"] == ["ES256"]
    assert m["request_object_signing_alg_values_supported"] == ["ES256"]


def test_metadata_del_superiore_diretto_prevalgono() -> None:
    f = Fed()
    f.ss_int_op = {"metadata": {"openid_provider": {"issuer": OP, "organization_name":
                                                     "Nome attestato"}}}
    assert f.risolvi().metadati["organization_name"] == "Nome attestato"


@pytest.mark.parametrize("sopra, sotto, motivo", [
    ({"value": "a"}, {"value": "b"}, "valori diversi"),
    ({"default": "a"}, {"default": "b"}, "valori diversi"),
    ({"one_of": ["a", "b"]}, {"one_of": ["c"]}, "nessun valore comune"),
    ({"subset_of": ["a"]}, {"add": ["b"]}, "add non contenuto"),
    ({"value": "z"}, {"one_of": ["a"]}, "fuori da one_of"),
    ({"one_of": ["a"]}, {"subset_of": ["a"]}, "one_of non si combina"),
    ({"subset_of": ["a"]}, {"superset_of": ["b"]}, "superset_of non contenuto"),
])
def test_combinazioni_in_conflitto_invalidano(sopra, sotto, motivo) -> None:
    with pytest.raises(PoliticaNonValida, match=motivo):
        combina([{"p": sopra}, {"p": sotto}])


def test_il_livello_inferiore_non_allenta() -> None:
    """essential vero in alto resta vero; subset_of solo più stretto; superset_of solo più
    largo; add si somma."""
    c = combina([{"p": {"essential": True, "subset_of": ["a", "b"], "superset_of": ["a"]}},
                 {"p": {"essential": False, "subset_of": ["a", "c"], "superset_of": []}}])
    assert c["p"] == {"essential": True, "subset_of": ["a"], "superset_of": ["a"]}


@pytest.mark.parametrize("metadati, politica, motivo", [
    ({"p": "x"}, {"p": {"one_of": ["a", "b"]}}, "fuori da one_of"),
    ({"p": ["a"]}, {"p": {"superset_of": ["a", "b"]}}, "superset_of"),
    ({}, {"p": {"essential": True}}, "essenziale"),
    ({"p": ["z"]}, {"p": {"subset_of": ["a"], "essential": True}}, "essenziale"),
    ({"p": "x"}, {"p": {"value": None, "essential": True}}, "nullo"),
])
def test_applicazione_negativa(metadati, politica, motivo) -> None:
    with pytest.raises(PoliticaNonValida, match=motivo):
        applica(metadati, combina([politica]))


def test_value_nullo_toglie_il_parametro_e_operatore_ignoto() -> None:
    assert "p" not in applica({"p": 1}, combina([{"p": {"value": None}}]))
    assert applica({"p": 1}, combina([{"p": {"regex": ".*"}}])) == {"p": 1}
    with pytest.raises(PoliticaNonValida, match="critico"):
        combina([{"p": {"regex": ".*"}}], critici=["regex"])


def test_politica_della_catena_che_esclude_il_valore_dell_op() -> None:
    f = Fed()
    f.ss_ta_int = {"metadata_policy": {"openid_provider": {
        "token_endpoint_auth_methods_supported": {"superset_of": ["private_key_jwt",
                                                                  "tls_client_auth"]}}}}
    with pytest.raises(CatenaNonValida, match="superset_of"):
        f.risolvi()


def test_metadata_policy_crit_con_operatore_ignoto() -> None:
    f = Fed()
    f.ss_int_op = {"metadata_policy_crit": ["regex"], "metadata_policy": {
        "openid_provider": {"issuer": {"regex": "^https://"}}}}
    with pytest.raises(CatenaNonValida, match="critico"):
        f.risolvi()


# ── constraints ────────────────────────────────────────────────────────

def test_max_path_length() -> None:
    f = Fed()
    f.ss_ta_int = {"constraints": {"max_path_length": 0}}   # fra TA e OP c'è INT: 1 intermedio
    with pytest.raises(CatenaNonValida, match="max_path_length"):
        f.risolvi()
    f = Fed()
    f.ss_ta_int = {"constraints": {"max_path_length": 1}}
    assert f.risolvi().percorso[-1] == TA
    with pytest.raises(PoliticaNonValida):
        controlla_vincoli({"max_path_length": -1}, livello=0, sotto=[OP], tipi_foglia=[])


@pytest.mark.parametrize("vincolo, valida", [
    ({"permitted": [".example"]}, True),
    ({"permitted": ["op.example", "intermedio.example"]}, True),
    ({"permitted": ["op.example"]}, False),              # l'intermedio è sotto il TA
    ({"excluded": ["op.example"]}, False),
    ({"permitted": [".altro.example"]}, False),
    ({"excluded": [".invalid"]}, True),
])
def test_naming_constraints(vincolo, valida) -> None:
    f = Fed()
    f.ss_ta_int = {"constraints": {"naming_constraints": vincolo}}
    if valida:
        assert f.risolvi().percorso[-1] == TA
    else:
        with pytest.raises(CatenaNonValida, match="naming_constraints"):
            f.risolvi()


def test_allowed_entity_types() -> None:
    f = Fed()
    f.ss_ta_int = {"constraints": {"allowed_entity_types": ["openid_relying_party"]}}
    with pytest.raises(CatenaNonValida, match="escluso dai vincoli"):
        f.risolvi()
    f = Fed()
    f.ss_ta_int = {"constraints": {"allowed_entity_types": ["openid_provider"]}}
    assert f.risolvi().metadati["issuer"] == OP


# ── trust mark ────────────────────────────────────────────────────────

def test_trust_mark_richiesto_valido_con_stato_attivo() -> None:
    f = Fed()
    f.marchi = [f.marchio()]
    c = f.risolvi(trust_marks_richiesti={TIPO: [TMI]}, verifica_stato_trust_marks=True)
    assert c.trust_marks == (TIPO,)


def test_trust_mark_non_richiesto_non_serve() -> None:
    assert Fed().risolvi().trust_marks == ()


@pytest.mark.parametrize("caso, motivo", [
    ("assente", "assente"),
    ("emittente_non_ammesso", "non ammesso"),
    ("firma_altrui", "firma"),
    ("altra_entita", "altra entità"),
    ("altro_tipo", "assente"),
    ("scaduto", "scaduto"),
    ("emittente_fuori_federazione", "404"),
])
def test_trust_mark_negativi(caso, motivo) -> None:
    f = Fed()
    richiesti = {TIPO: [TMI]}
    if caso == "emittente_non_ammesso":
        f.marchi = [f.marchio()]
        richiesti = {TIPO: ["https://altro.example"]}
    elif caso == "firma_altrui":
        f.marchi = [f.marchio(chiave="op")]      # stesso kid, chiave dell'OP
    elif caso == "altra_entita":
        f.marchi = [f.marchio(sub=INT)]
    elif caso == "altro_tipo":
        f.marchi = [f.marchio(tipo="https://ta.example/marchi/altro")]
    elif caso == "scaduto":
        f.marchi = [f.marchio(exp=f.ora - 3600)]
    elif caso == "emittente_fuori_federazione":
        f.marchi = [f.marchio()]
        f.ritirate.add(f"{TA}/fetch?sub={quote(TMI, safe='')}")
    with pytest.raises(CatenaNonValida, match=motivo):
        f.risolvi(trust_marks_richiesti=richiesti)


def test_trust_mark_revocato_all_endpoint_di_stato() -> None:
    f = Fed()
    f.marchi = [f.marchio()]
    f.stato = "revoked"
    with pytest.raises(CatenaNonValida, match="revocato"):
        f.risolvi(trust_marks_richiesti={TIPO: [TMI]}, verifica_stato_trust_marks=True)
    # senza la verifica dello stato configurata il marchio vale per firma e scadenza
    assert f.risolvi(trust_marks_richiesti={TIPO: [TMI]}).trust_marks == (TIPO,)


def test_emittente_non_autorizzato_dal_trust_anchor() -> None:
    f = Fed()
    f.marchi = [f.marchio()]
    f.ec_ta = {"trust_mark_issuers": {TIPO: ["https://altro.example"]}}
    with pytest.raises(CatenaNonValida, match="Trust Anchor"):
        f.risolvi(trust_marks_richiesti={TIPO: [TMI]})
    f.ec_ta = {"trust_mark_issuers": {TIPO: [TMI]}}
    assert f.risolvi(trust_marks_richiesti={TIPO: [TMI]}).trust_marks == (TIPO,)


def test_trust_mark_emesso_dal_trust_anchor() -> None:
    f = Fed()
    f.marchi = [f.marchio(emittente=TA, chiave="ta", kid="ta1")]
    assert f.risolvi(trust_marks_richiesti={TIPO: [TA]}).trust_marks == (TIPO,)


def test_delega_del_proprietario() -> None:
    f = Fed()
    proprietario = {"sub": "https://proprietario.example",
                    "jwks": {"keys": [_jwk(f.k["prop"], "p1")]}}
    f.ec_ta = {"trust_mark_owners": {TIPO: proprietario}}
    f.marchi = [f.marchio()]
    with pytest.raises(CatenaNonValida, match="delega"):
        f.risolvi(trust_marks_richiesti={TIPO: [TMI]})
    delega = _firma(f.base(iss=proprietario["sub"], sub=TMI, trust_mark_type=TIPO),
                    f.k["prop"], "p1", "trust-mark-delegation+jwt")
    f.marchi = [f.marchio(delegation=delega)]
    assert f.risolvi(trust_marks_richiesti={TIPO: [TMI]}).trust_marks == (TIPO,)
    falsa = _firma(f.base(iss=proprietario["sub"], sub=TMI, trust_mark_type=TIPO),
                   f.k["tmi"], "p1", "trust-mark-delegation+jwt")
    f.marchi = [f.marchio(delegation=falsa)]
    with pytest.raises(CatenaNonValida, match="firma"):
        f.risolvi(trust_marks_richiesti={TIPO: [TMI]})


def test_scadenza_della_catena_comprende_il_trust_mark() -> None:
    f = Fed()
    f.marchi = [f.marchio(exp=f.ora + 120)]
    assert f.risolvi(trust_marks_richiesti={TIPO: [TMI]}).scade == f.ora + 120
