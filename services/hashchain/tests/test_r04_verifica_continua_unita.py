# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifica continua (R04, r04-verifiche): regole pure, budget, foglie K1, perimetro,
cursore, lettura da storage con un client finto (casi d'errore che moto non produce).
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.verifica_continua import esiti, storage
from app.verifica_continua.budget import Budget
from app.verifica_continua.foglie import FogliePda, abbina
from app.verifica_continua.perimetro import FuoriPerimetro, perimetro_di
from app.verifica_continua.repository import (
    CursoreNonValido,
    codifica_cursore,
    decodifica_cursore,
)
from botocore.exceptions import ClientError, EndpointConnectionError

from libs.catena.foglie import ComponentePda, albero_pda

ORA = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


# ── esiti ────────────────────────────────────────────────────────────────────

def test_peggiore_ordine_di_gravita():
    """MANCANTE > DIVERSO > RETENTION_ASSENTE > ERRORE_LETTURA > OK; vuoto = OK."""
    assert esiti.peggiore([]) == esiti.OK
    assert esiti.peggiore(["OK", "ERRORE_LETTURA"]) == "ERRORE_LETTURA"
    assert esiti.peggiore(["RETENTION_ASSENTE", "ERRORE_LETTURA", "OK"]) == "RETENTION_ASSENTE"
    assert esiti.peggiore(["DIVERSO", "RETENTION_ASSENTE"]) == "DIVERSO"
    assert esiti.peggiore(["DIVERSO", "MANCANTE", None]) == "MANCANTE"


def test_prima_verifica_distribuita_uniformemente_nella_finestra():
    """10.000 PdA nuovi nello stesso istante: prime verifiche nella finestra, deterministiche,
    distribuite in modo uniforme (nessun giorno con più del doppio della media)."""
    finestra = timedelta(days=30)
    istanti = [esiti.prima_verifica(uuid.uuid4(), ORA, finestra) for _ in range(10_000)]
    assert all(ORA <= i < ORA + finestra for i in istanti)
    per_giorno: dict[int, int] = {}
    for i in istanti:
        per_giorno[(i - ORA).days] = per_giorno.get((i - ORA).days, 0) + 1
    assert len(per_giorno) == 30
    assert max(per_giorno.values()) < 2 * 10_000 / 30
    pda = uuid.uuid4()
    assert esiti.prima_verifica(pda, ORA, finestra) == esiti.prima_verifica(pda, ORA, finestra)


def test_ritardo_errore_crescente_ultimo_ripetuto():
    ritardi = [60, 300, 1500]
    assert [esiti.ritardo_errore(n, ritardi).total_seconds() for n in (1, 2, 3, 4, 9)] == \
        [60, 300, 1500, 1500, 1500]


def test_retention_valida_casi():
    """COMPLIANCE attiva e non più breve della scadenza = None; ogni altro caso ha un motivo."""
    scad = ORA + timedelta(days=3650)
    tol = timedelta(days=1)
    kw = {"adesso": ORA, "scadenza_conservazione": scad, "tolleranza": tol}
    assert esiti.retention_valida("COMPLIANCE", scad, **kw) is None
    assert "assente" in esiti.retention_valida(None, None, **kw)
    assert "GOVERNANCE" in esiti.retention_valida("GOVERNANCE", scad, **kw)
    assert "scaduta" in esiti.retention_valida("COMPLIANCE", ORA - timedelta(seconds=1), **kw)
    assert "più breve" in esiti.retention_valida("COMPLIANCE", ORA + timedelta(days=2), **kw)
    # conservazione permanente: nessuna scadenza, basta che sia attiva
    assert esiti.retention_valida("COMPLIANCE", ORA + timedelta(days=2), adesso=ORA,
                                  scadenza_conservazione=None, tolleranza=tol) is None


# ── budget ───────────────────────────────────────────────────────────────────

class _Tempo:
    def __init__(self) -> None:
        self.t = 0.0
        self.dormito: list[float] = []

    def ora(self) -> float:
        return self.t

    async def dormi(self, secondi: float) -> None:
        self.dormito.append(secondi)
        self.t += secondi


async def test_budget_rispetta_il_tasso_medio():
    """100 byte/s con scorta di 100: 1000 byte in 10 prelievi richiedono 9 s di attesa."""
    tempo = _Tempo()
    b = Budget(100, orologio=tempo.ora, dormi=tempo.dormi)
    for _ in range(10):
        await b.consuma(100)
    assert sum(tempo.dormito) == pytest.approx(9.0)
    assert b.atteso_secondi == pytest.approx(9.0)


async def test_budget_illimitato_e_ricarica():
    tempo = _Tempo()
    illimitato = Budget(0, orologio=tempo.ora, dormi=tempo.dormi)
    await illimitato.consuma(10**12)
    assert tempo.dormito == []
    b = Budget(10, orologio=tempo.ora, dormi=tempo.dormi)
    await b.consuma(10)
    tempo.t += 5  # pausa: si ricarica fino alla scorta, non oltre
    await b.consuma(10)
    await b.consuma(5)
    assert sum(tempo.dormito) == pytest.approx(0.5)


# ── foglie K1 ────────────────────────────────────────────────────────────────

def _albero(contenuti: list[bytes], ipda: bytes):
    componenti = [ComponentePda(doc=f"d{i}", file=f"f{i}.pdf",
                                sha256=hashlib.sha256(c).hexdigest(), metadati={"n": i}, ordine=i)
                  for i, c in enumerate(contenuti)]
    return albero_pda("urn:prova:1", componenti, hashlib.sha256(ipda).hexdigest())


def test_abbina_e_ricalcola_foglie():
    """Foglia ricalcolata con l'impronta giusta = foglia salvata; con un'altra no."""
    albero = _albero([b"uno", b"due"], b"<ipda/>")
    documenti = [{"id": uuid.uuid4(), "ordine": i, "nome_file": f"f{i}.pdf"} for i in range(2)]
    abbinate = abbina(FogliePda(albero.foglie, albero.radice_hex), documenti)
    assert abbinate.radice_coerente is True and abbinate.non_abbinati == ()
    foglia = abbinate.per_documento[documenti[1]["id"]]
    assert foglia.ricalcola(hashlib.sha256(b"due").hexdigest()) == foglia.hash_hex
    assert foglia.ricalcola(hashlib.sha256(b"DUE").hexdigest()) != foglia.hash_hex
    assert abbinate.ipda.impronta_registrata == hashlib.sha256(b"<ipda/>").hexdigest()


def test_abbina_nome_diverso_e_radice_incoerente():
    """Nome del file diverso: nessun abbinamento inventato; radice diversa: incoerente."""
    albero = _albero([b"uno"], b"<ipda/>")
    documenti = [{"id": uuid.uuid4(), "ordine": 0, "nome_file": "altro.pdf"}]
    abbinate = abbina(FogliePda(albero.foglie, "00" * 32), documenti)
    assert abbinate.per_documento == {} and abbinate.non_abbinati == (documenti[0]["id"],)
    assert abbinate.radice_coerente is False


# ── perimetro e cursore ─────────────────────────────────────────────────────

def test_perimetro_ente_e_conservatore():
    ente = uuid.uuid4()
    p = perimetro_di({"ruolo": "VIEWER", "tenant_id": str(ente), "user_id": "u"})
    assert p.ente_effettivo(None) == ente and not p.puo_chiedere
    with pytest.raises(FuoriPerimetro):
        p.ente_effettivo(uuid.uuid4())
    assert p.vede_richiesta(str(ente)) and not p.vede_richiesta(None)
    assert perimetro_di({"ruolo": "ADMIN_TENANT", "tenant_id": str(ente)}).puo_chiedere
    c = perimetro_di({"ruolo": "RSC_CONSERVATORE", "tenant_id": "x"})
    altro = uuid.uuid4()
    assert c.ente_effettivo(altro) == altro and c.ente_effettivo(None) is None
    with pytest.raises(FuoriPerimetro):
        perimetro_di({"ruolo": "VIEWER", "tenant_id": "non-uuid"})


def test_cursore_andata_e_ritorno_e_non_valido():
    c = codifica_cursore(ORA, 42)
    assert decodifica_cursore(c) == (ORA, 42)
    for cattivo in ("!!!", "YWJj", codifica_cursore(ORA, 1)[:-3] + "zzz"):
        with pytest.raises(CursoreNonValido):
            decodifica_cursore(cattivo)


# ── storage (client finto per gli errori che moto non simula) ────────────────

class _ClientFinto:
    def __init__(self, *, get=None, retention=None, corpo=b"abc"):
        self._get, self._retention, self._corpo = get, retention, corpo

    def get_object(self, **kw):
        if self._get:
            raise self._get
        return {"Body": io.BytesIO(self._corpo), "VersionId": "v1"}

    def get_object_retention(self, **kw):
        if isinstance(self._retention, Exception):
            raise self._retention
        return {"Retention": self._retention}


def _errore(codice: str) -> ClientError:
    return ClientError({"Error": {"Code": codice}}, "Op")


async def _leggi(client):
    return await storage.leggi_oggetto(client, "b", "k", budget_letture=Budget(0),
                                       budget_byte=Budget(0), blocco=2)


async def test_leggi_oggetto_retention_dalla_chiamata_esplicita():
    """Intestazioni di retention assenti: si chiede GetObjectRetention sulla versione."""
    fino = ORA + timedelta(days=1)
    lettura = await _leggi(_ClientFinto(retention={"Mode": "COMPLIANCE", "RetainUntilDate": fino}))
    assert lettura.stato == storage.LETTO and lettura.sha256 == hashlib.sha256(b"abc").hexdigest()
    assert (lettura.retention_modalita, lettura.retention_fino_a) == ("COMPLIANCE", fino)
    senza = await _leggi(_ClientFinto(retention=_errore("NoSuchObjectLockConfiguration")))
    assert senza.stato == storage.LETTO and senza.retention_modalita is None


async def test_leggi_oggetto_permesso_retention_mancante_errore_non_assenza():
    """Un AccessDenied su GetObjectRetention è un errore di lettura, mai RETENTION_ASSENTE."""
    lettura = await _leggi(_ClientFinto(retention=_errore("AccessDenied")))
    assert lettura.stato == storage.ERRORE and "AccessDenied" in lettura.motivo


async def test_leggi_oggetto_mancante_ed_errori():
    assert (await _leggi(_ClientFinto(get=_errore("NoSuchKey")))).stato == storage.NON_TROVATO
    assert (await _leggi(_ClientFinto(get=_errore("AccessDenied")))).stato == storage.ERRORE
    rete = EndpointConnectionError(endpoint_url="http://s3")
    assert (await _leggi(_ClientFinto(get=rete))).stato == storage.ERRORE

    class _CorpoRotto(io.BytesIO):
        def read(self, n=-1):
            raise ConnectionResetError("rete")

    class _ClientCorpoRotto(_ClientFinto):
        def get_object(self, **kw):
            return {"Body": _CorpoRotto(b"x")}

    interrotta = await _leggi(_ClientCorpoRotto())
    assert interrotta.stato == storage.ERRORE and "interrotta" in interrotta.motivo


async def test_leggi_oggetto_trattiene_solo_entro_il_limite():
    corpo = json.dumps({"a": 1}).encode()
    client = _ClientFinto(corpo=corpo, retention=_errore("NoSuchObjectLockConfiguration"))
    piccolo = await storage.leggi_oggetto(client, "b", "k", budget_letture=Budget(0),
                                          budget_byte=Budget(0), blocco=3, trattieni_max=100)
    assert piccolo.contenuto == corpo
    grande = await storage.leggi_oggetto(client, "b", "k", budget_letture=Budget(0),
                                         budget_byte=Budget(0), blocco=3, trattieni_max=4)
    assert grande.contenuto is None and grande.dimensione == len(corpo)


# ── R04 correzioni (rev4 rilievo 3): le foglie sono la fonte di cosa rileggere ──

class _CorpoProva(io.BytesIO):
    pass


class _ClientProva:
    """S3 finto con retention COMPLIANCE lontana (solo per la verifica)."""

    def __init__(self, oggetti: dict[str, bytes]) -> None:
        self.oggetti = oggetti
        self.letti: list[str] = []

    def get_object(self, Bucket, Key):  # noqa: N803 — firma di boto3
        self.letti.append(Key)
        if Key not in self.oggetti:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": _CorpoProva(self.oggetti[Key]), "ObjectLockMode": "COMPLIANCE",
                "ObjectLockRetainUntilDate": datetime.now(UTC) + timedelta(days=4000),
                "VersionId": "v1"}


def _pda_con_foglie(alterato: bytes | None, *, togli_riga: bool, manifest_completo: bool):
    from app.verifica_continua.verificatore import DocumentoDaVerificare, PdaDaVerificare

    h = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    f1, f2, ipda = b"documento uno", b"documento due originale", b"<ipda/>"
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    albero = albero_pda("urn:pda:1", [ComponentePda(str(d1), "a.pdf", h(f1), {}, 1),
                                      ComponentePda(str(d2), "b.pdf", h(f2), {}, 2)], h(ipda))
    voci = [{"s3_key": "k/a", "hash_sha256": h(f1)}]
    if manifest_completo:
        voci.append({"s3_key": "k/b", "hash_sha256": h(f2)})
    manifest = json.dumps({"sincro_hash_sha256": h(ipda), "documenti": voci}).encode()
    client = _ClientProva({"k/pda": manifest, "k/ipda": ipda, "k/a": f1,
                           "k/b": alterato if alterato is not None else f2})
    documenti = [DocumentoDaVerificare(d1, "a.pdf", "k/a", h(f1), len(f1), 1)]
    if not togli_riga:
        documenti.append(DocumentoDaVerificare(d2, "b.pdf", "k/b", h(f2), len(f2), 2))
    pda = PdaDaVerificare(uuid.uuid4(), uuid.uuid4(), "urn:pda:1", "IN_CATENA", "b", "k/pda",
                          h(manifest), len(manifest), "k/ipda", None, None, documenti,
                          uuid.uuid4())
    return pda, client, FogliePda(albero.foglie, albero.radice_hex), d2


async def _verifica(pda, client, foglie):
    from app.verifica_continua.budget import Budget
    from app.verifica_continua.config import ImpostazioniVerifica
    from app.verifica_continua.verificatore import verifica_pda

    return await verifica_pda(pda, client=client, budget_letture=Budget(0),
                              budget_byte=Budget(0), cfg=ImpostazioniVerifica(),
                              adesso=datetime.now(UTC), foglie=foglie)


@pytest.mark.parametrize("manifest_completo", [False, True])
async def test_documento_tolto_dal_database_e_file_alterato_prova_del_revisore(
        manifest_completo):
    """La prova del revisore: la riga di ``documenti`` di b.pdf è cancellata e il
    file alterato. Prima: esito OK, b.pdf mai riletto. Ora: anomalia."""
    pda, client, foglie, d2 = _pda_con_foglie(b"documento DUE (alterato)", togli_riga=True,
                                              manifest_completo=manifest_completo)
    esito = await _verifica(pda, client, foglie)
    assert esito.peggiore in ("DIVERSO", "MANCANTE")
    orfano = [o for o in esito.oggetti if o.componente == "FILE" and o.documento_id == d2]
    assert len(orfano) == 1 and "senza documento nel database" in orfano[0].motivo
    if manifest_completo:
        assert "k/b" in client.letti and orfano[0].esito == "DIVERSO"
        assert "foglia del registro diversa" in orfano[0].motivo     # file alterato
        pda_obj = [o for o in esito.oggetti if o.componente == "PDA"][0]
        assert pda_obj.esito == "DIVERSO" and "il manifest elenca 2 file" in pda_obj.motivo
    else:
        assert orfano[0].esito == "MANCANTE"


async def test_documento_tolto_dal_database_file_intatto_resta_anomalia():
    pda, client, foglie, d2 = _pda_con_foglie(None, togli_riga=True, manifest_completo=True)
    esito = await _verifica(pda, client, foglie)
    orfano = [o for o in esito.oggetti if o.documento_id == d2][0]
    assert "k/b" in client.letti and orfano.esito == "DIVERSO"
    assert orfano.foglia_attesa == orfano.foglia_trovata   # il file è intatto, la riga no


async def test_tutto_integro_ok_e_documento_senza_foglia_diverso():
    pda, client, foglie, _ = _pda_con_foglie(None, togli_riga=False, manifest_completo=True)
    assert (await _verifica(pda, client, foglie)).peggiore == "OK"
    # documento con un nome diverso dalla sua foglia: non abbinato → anomalia con motivo
    pda.documenti[1] = pda.documenti[1].__class__(
        pda.documenti[1].id, "rinominato.pdf", "k/b", pda.documenti[1].impronta,
        pda.documenti[1].dimensione, 2)
    esito = await _verifica(pda, client, foglie)
    senza = [o for o in esito.oggetti if o.documento_id == pda.documenti[1].id
             and "senza foglia" in (o.motivo or "")]
    assert esito.peggiore != "OK" and len(senza) == 1 and senza[0].esito == "DIVERSO"


def test_abbina_per_id_documento_e_foglie_orfane():
    contenuti = [b"uno", b"due"]
    ids = [uuid.uuid4(), uuid.uuid4()]
    albero = albero_pda("urn:prova:1", [
        ComponentePda(doc=str(ids[i]), file=f"f{i}.pdf", sha256=hashlib.sha256(c).hexdigest(),
                      metadati={}, ordine=i) for i, c in enumerate(contenuti)],
        hashlib.sha256(b"<ipda/>").hexdigest())
    # ordine nel database diverso da quello delle foglie: l'id decide
    documenti = [{"id": ids[1], "ordine": 0, "nome_file": "f1.pdf"}]
    abbinate = abbina(FogliePda(albero.foglie, albero.radice_hex), documenti)
    assert set(abbinate.per_documento) == {ids[1]} and abbinate.non_abbinati == ()
    assert [f.dati["doc"] for f in abbinate.orfane] == [str(ids[0])]
