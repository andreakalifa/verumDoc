# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Dati di prova del verificatore, costruiti SOLO con ``libs/catena`` (le stesse
funzioni di aip-builder e hashchain): un PdA con tre file, un registro con
più PdA, checkpoint firmati da registro e testimone, prova d'inclusione K1 e
prova di consistenza. Le chiavi sono generate a ogni esecuzione e mai salvate
nel repository.

Uso dalla riga di comando (anche per la prova della pagina web)::

    python tools/verificatore/tests/dati_prova.py <cartella>
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_RADICE = Path(__file__).resolve().parents[3]
if str(_RADICE) not in sys.path:
    sys.path.insert(0, str(_RADICE))

from libs.catena import attestazione as attestazione_lib  # noqa: E402
from libs.catena import foglie as foglie_lib  # noqa: E402
from libs.catena import nota as nota_lib  # noqa: E402
from libs.catena import rfc6962  # noqa: E402

ORIGINE = "verumdoc.it/registro/v1"              # produzione (predefinita del verificatore)
ORIGINE_SVILUPPO = "verumdoc.it/registro/v1/development"
PDA = "urn:verumdoc:pda:00000000-0000-0000-0000-000000000001:2026:000042"
ENTE = "00000000-0000-0000-0000-000000000001"


def _modulo_rdv():  # type: ignore[no-untyped-def]
    """Generatore del RdV v2 della hashchain (solo stdlib + libs/catena), caricato
    dal file senza importare il servizio: è il produttore di riferimento del formato."""
    import importlib.util

    file = _RADICE / "services" / "hashchain" / "app" / "registro" / "rdv.py"
    spec = importlib.util.spec_from_file_location("verumdoc_hashchain_rdv", file)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _checkpoint(dimensione: int, radice: bytes, firmatari: list[nota_lib.Firmatario],
                origine: str = ORIGINE) -> str:
    corpo = nota_lib.formatta(nota_lib.Checkpoint(origine, dimensione, radice))
    return nota_lib.componi(corpo, [f.firma(corpo) for f in firmatari])


#: Revisione 3 di R05 (T2): istante del rilascio delle attestazioni di prova
RILASCIATA = "2026-10-23T08:16:30Z"


def _attesta(registro: nota_lib.Firmatario, nota_cp: str, voci: list[tuple[int, bytes]],
             pda: str = PDA, stato: str = "CONSERVATO") -> str:
    """Revisione 2 di R05 (S8): attestazione del ciclo di vita come la firma la
    hashchain (``app/registro/prove.py``): stessa chiave del registro.
    Revisione 3 (T2, T3): v2 con istante del rilascio e stato del PdA."""
    cp = nota_lib.analizza(nota_cp)
    return attestazione_lib.firma(attestazione_lib.Attestazione(
        origine=cp.origine, dimensione=cp.dimensione, pda=pda, foglie=len(voci),
        elenco=attestazione_lib.impronta_elenco(voci), rilasciata=RILASCIATA, stato=stato),
        registro)


def costruisci(cartella: Path) -> dict[str, str]:
    """Scrive i file di prova in ``cartella`` e restituisce i percorsi."""
    cartella.mkdir(parents=True, exist_ok=True)
    # Chiave del registro con almeno un «+» nel base64: i verificatori devono
    # separare solo i primi due «+» della verifier key (come Go note).
    priv_r, pub_r = nota_lib.genera_chiave("verumdoc-registro")
    while "+" not in pub_r.split("+", 2)[2]:
        priv_r, pub_r = nota_lib.genera_chiave("verumdoc-registro")
    priv_t, pub_t = nota_lib.genera_chiave("verumdoc-testimone")
    registro = nota_lib.analizza_firmatario(priv_r)
    testimone = nota_lib.analizza_firmatario(priv_t)

    # File originali del PdA (il secondo è quello del cliente)
    contenuti = {"lettera.pdf": b"%PDF-1.7\nlettera di prova\n",
                 "fattura.xml": "<Fattura>importo 1.234,56 € — prova</Fattura>\n".encode(),
                 "allegato.txt": b"allegato\n" * 1000}
    for nome, dati in contenuti.items():
        (cartella / nome).write_bytes(dati)
    componenti = [
        foglie_lib.ComponentePda(doc=f"doc-{i}", file=nome,
                                 sha256=hashlib.sha256(dati).hexdigest(),
                                 metadati={"oggetto": f"documento {i}", "importo": "1234.56"},
                                 ordine=i)
        for i, (nome, dati) in enumerate(contenuti.items(), start=1)
    ]
    albero = foglie_lib.albero_pda(PDA, componenti, hashlib.sha256(b"<IPdA/>").hexdigest())
    indice_doc = albero.indice_documento("doc-2")
    assert indice_doc is not None
    prova_pda = rfc6962.prova_inclusione(albero.hash_foglie(), indice_doc)

    # Registro: 5 PdA prima, il nostro al posto 5, poi altri 6 (12 foglie)
    foglie_reg: list[bytes] = []
    for i in range(5):
        foglie_reg.append(rfc6962.hash_foglia(f"pda-precedente-{i}".encode()))
    dati_reg = foglie_lib.dati_foglia_registro(
        PDA, albero.radice_hex, ENTE, datetime(2026, 10, 23, 8, 15, tzinfo=UTC))
    indice_reg = len(foglie_reg)
    foglie_reg.append(rfc6962.hash_foglia(dati_reg))
    cp_vecchio = _checkpoint(6, rfc6962.radice(foglie_reg[:6]),
                             [registro, testimone])
    for i in range(6):
        foglie_reg.append(rfc6962.hash_foglia(f"pda-successivo-{i}".encode()))
    n = len(foglie_reg)
    cp_nuovo = _checkpoint(n, rfc6962.radice(foglie_reg), [registro, testimone])
    cp_solo_registro = _checkpoint(n, rfc6962.radice(foglie_reg), [registro])

    prova = {
        "pda": PDA, "documento": "doc-2",
        "foglia_dati": albero.foglie[indice_doc],
        "prova_pda": {"foglia_indice": indice_doc, "dimensione": len(albero.foglie),
                      "percorso": rfc6962.esadecimale(prova_pda)},
        "radice_pda": albero.radice_hex,
        "foglia_registro_dati": dati_reg.decode("utf-8"),
        "prova_registro": {"foglia_indice": indice_reg, "dimensione_albero": n,
                           "percorso": rfc6962.esadecimale(
                               rfc6962.prova_inclusione(foglie_reg, indice_reg))},
        "checkpoint": cp_nuovo,
        # R05 (Q3): nessuna foglia del ciclo di vita fino a questo checkpoint
        "ciclo_vita": [],
        # Revisione 2 di R05 (S8): zero foglie, attestato dal registro
        "attestazione_ciclo_vita": _attesta(registro, cp_nuovo, []),
    }
    consistenza = {"da": 6, "a": n,
                   "percorso": rfc6962.esadecimale(rfc6962.prova_consistenza(foglie_reg, 6, n))}

    # Registro riscritto: la foglia 2 cambia DOPO il checkpoint conservato
    riscritte = list(foglie_reg)
    riscritte[2] = rfc6962.hash_foglia(b"pda sostituito")
    cp_riscritto = _checkpoint(n, rfc6962.radice(riscritte), [registro, testimone])
    consistenza_riscritta = {"da": 6, "a": n, "percorso": rfc6962.esadecimale(
        rfc6962.prova_consistenza(riscritte, 6, n))}
    # Revisione R14 (1): prova «aggiornata» dello stesso documento sul registro riscritto
    # (stessa dimensione della ricevuta, radice diversa: registro biforcato)
    prova_biforcata = dict(prova, checkpoint=cp_riscritto, prova_registro={
        "foglia_indice": indice_reg, "dimensione_albero": n,
        "percorso": rfc6962.esadecimale(rfc6962.prova_inclusione(riscritte, indice_reg))},
        attestazione_ciclo_vita=_attesta(registro, cp_riscritto, []))

    percorsi = {
        "chiave_registro": pub_r, "chiave_testimone": pub_t,
        "documento": str(cartella / "fattura.xml"),
        "documento_altro": str(cartella / "lettera.pdf"),
    }
    # RdV definitivo v2 costruito dal generatore della hashchain (K5)
    rdv = _modulo_rdv().costruisci_rdv(
        pdv_id="pdv-42",
        pda={"aip_id": "aip-42", "urn": PDA, "impronta_manifest": "ab" * 32,
             "radice_pda": albero.radice_hex, "ente": ENTE},
        documenti=[{"identificativo": c.doc, "nome_file": c.file, "sha256": c.sha256,
                    "sha512": None, "foglia": albero.foglie[i],
                    "prova_pda": {"foglia_indice": i, "dimensione": len(albero.foglie),
                                  "percorso": rfc6962.esadecimale(
                                      rfc6962.prova_inclusione(albero.hash_foglie(), i))}}
                   for i, c in enumerate(foglie_lib.ordina_componenti(componenti))],
        ipda=None, avvisi=[],
        registro={"foglia_indice": indice_reg, "foglia": dati_reg.decode("utf-8"),
                  "tipo": "PDA", "dimensione_albero": n,
                  "percorso": prova["prova_registro"]["percorso"]},
        checkpoint={"nota": cp_nuovo, "chiave_verificatore": pub_r, "dimensione": n,
                    "radice": rfc6962.radice(foglie_reg).hex(),
                    "emesso_il": "2026-10-23T08:16:00Z", "stato_sigillo": "NON_FIRMATO",
                    "motivo_sigillo": "Fornitore qualificato non configurato",
                    "stato_testimone": "CONTROFIRMATO",
                    "controfirmato_il": "2026-10-23T08:16:01Z"},
        conservato_il="2026-10-23T08:15:00Z", emesso_il="2026-10-23T08:16:02Z",
        conservatore="Conservatore di prova",
    )
    (cartella / "rdv.xml").write_bytes(rdv)
    percorsi.update(rdv=str(cartella / "rdv.xml"),
                    documento_allegato=str(cartella / "allegato.txt"))

    # Revisione 3 di R05 (T3): PdA con cancellazione INCOMPLETA (nessuna foglia di
    # scarto: il registro attesta lo stato SCARTO_PARZIALE) e PdA in scarto
    prova_scarto_parziale = dict(prova, attestazione_ciclo_vita=_attesta(
        registro, cp_nuovo, [], stato="SCARTO_PARZIALE"))
    prova_in_scarto = dict(prova, attestazione_ciclo_vita=_attesta(
        registro, cp_nuovo, [], stato="SCARTO"))
    prova_solo_registro = dict(prova, checkpoint=cp_solo_registro,
                               attestazione_ciclo_vita=_attesta(registro, cp_solo_registro, []))
    file_testo_r05 = _prove_ciclo_vita(prova, foglie_reg, indice_reg, registro, testimone)
    # R04 correzioni (rev4 rilievo 9): lo stesso registro firmato con origine di
    # SVILUPPO (chiavi valide): non deve valere come ricevuta di produzione
    cp_sviluppo = _checkpoint(n, rfc6962.radice(foglie_reg), [registro, testimone],
                              ORIGINE_SVILUPPO)
    cp_sviluppo_vecchio = _checkpoint(6, rfc6962.radice(foglie_reg[:6]), [registro, testimone],
                                      ORIGINE_SVILUPPO)
    prova_sviluppo = dict(prova, checkpoint=cp_sviluppo,
                          attestazione_ciclo_vita=_attesta(registro, cp_sviluppo, []))
    file_testo = {
        "prova": json.dumps(prova, ensure_ascii=False, indent=2),
        "prova_scarto_parziale": json.dumps(prova_scarto_parziale, ensure_ascii=False, indent=2),
        "prova_in_scarto": json.dumps(prova_in_scarto, ensure_ascii=False, indent=2),
        "prova_solo_registro": json.dumps(prova_solo_registro, ensure_ascii=False, indent=2),
        "prova_sviluppo": json.dumps(prova_sviluppo, ensure_ascii=False, indent=2),
        "prova_biforcata": json.dumps(prova_biforcata, ensure_ascii=False, indent=2),
        "checkpoint_sviluppo": cp_sviluppo, "checkpoint_sviluppo_vecchio": cp_sviluppo_vecchio,
        "checkpoint_vecchio": cp_vecchio, "checkpoint_nuovo": cp_nuovo,
        "checkpoint_solo_registro": cp_solo_registro, "checkpoint_riscritto": cp_riscritto,
        "consistenza": json.dumps(consistenza, indent=2),
        "consistenza_riscritta": json.dumps(consistenza_riscritta, indent=2),
        "chiave_registro_file": pub_r + "\n", "chiave_testimone_file": pub_t + "\n",
    }
    file_testo.update(file_testo_r05)
    estensioni = {"prova": "json", "prova_solo_registro": "json", "prova_sviluppo": "json",
                  "prova_biforcata": "json",
                  "prova_scarto_parziale": "json", "prova_in_scarto": "json",
                  "consistenza": "json",
                  "consistenza_riscritta": "json",
                  **{k: "json" for k in file_testo_r05 if not k.startswith("checkpoint_")}}
    for nome, testo in file_testo.items():
        percorso = cartella / f"{nome}.{estensioni.get(nome, 'txt')}"
        percorso.write_text(testo, encoding="utf-8")
        percorsi[nome] = str(percorso)
    return percorsi


def _prove_ciclo_vita(prova: dict, foglie_reg: list[bytes], indice_reg: int,
                      registro: nota_lib.Firmatario, testimone: nota_lib.Firmatario
                      ) -> dict[str, str]:
    """R05 (contratto Q3): lo stesso registro cresciuto con una RETTIFICA e poi uno
    SCARTO del nostro PdA (foglie di formato 2), un checkpoint che le copre e le
    prove che il verificatore deve trattare: rettificato (verificata con nota),
    scartato (mai verificata), foglia di scarto alterata, foglia di scarto usata
    come foglia del documento, formato sconosciuto."""
    istante = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
    rettifica = foglie_lib.dati_foglia_ciclo_vita(
        foglie_lib.TIPO_RETTIFICA, foglia_id="aaaaaaaa-0000-4000-8000-000000000001", pda=PDA,
        ente=ENTE, riferimento={"pratica": "PR-2026-0007", "pda_rettifica": PDA + ":r1"},
        impronta_documento_pratica=hashlib.sha256(b"atto di rettifica").hexdigest(),
        registrato_il=istante)
    scarto = foglie_lib.dati_foglia_ciclo_vita(
        foglie_lib.TIPO_SCARTO, foglia_id="aaaaaaaa-0000-4000-8000-000000000002", pda=PDA,
        ente=ENTE, riferimento={"pratica": "PS-2026-0042"},
        impronta_documento_pratica=hashlib.sha256(b"verbale di scarto").hexdigest(),
        registrato_il=istante)
    foglie = [*foglie_reg, rfc6962.hash_foglia(rettifica), rfc6962.hash_foglia(b"altro pda"),
              rfc6962.hash_foglia(scarto)]
    n = len(foglie)
    i_rett, i_scarto = len(foglie_reg), len(foglie_reg) + 2
    cp = _checkpoint(n, rfc6962.radice(foglie), [registro, testimone])
    cp_solo_rettifica = _checkpoint(i_rett + 1, rfc6962.radice(foglie[:i_rett + 1]),
                                    [registro, testimone])

    def voce(dati: bytes, indice: int, dimensione: int) -> dict:
        return {"tipo": json.loads(dati)["tipo"], "foglia_dati": dati.decode("utf-8"),
                "prova_registro": {"foglia_indice": indice, "dimensione_albero": dimensione,
                                   "percorso": rfc6962.esadecimale(rfc6962.prova_inclusione(
                                       foglie[:dimensione], indice))}}

    def base(dimensione: int, nota: str) -> dict:
        return dict(prova, checkpoint=nota, prova_registro={
            "foglia_indice": indice_reg, "dimensione_albero": dimensione,
            "percorso": rfc6962.esadecimale(rfc6962.prova_inclusione(foglie[:dimensione],
                                                                      indice_reg))})

    h_rett, h_scarto = rfc6962.hash_foglia(rettifica), rfc6962.hash_foglia(scarto)
    rettificata = dict(base(i_rett + 1, cp_solo_rettifica),
                       ciclo_vita=[voce(rettifica, i_rett, i_rett + 1)],
                       attestazione_ciclo_vita=_attesta(registro, cp_solo_rettifica,
                                                        [(i_rett, h_rett)]))
    scartata = dict(base(n, cp), ciclo_vita=[voce(rettifica, i_rett, n),
                                             voce(scarto, i_scarto, n)],
                    attestazione_ciclo_vita=_attesta(registro, cp, [(i_rett, h_rett),
                                                                    (i_scarto, h_scarto)]))
    # Revisione 2 di R05 (S8): chi presenta la prova toglie la foglia di scarto
    # e i campi del conteggio (con o senza l'attestazione)
    scarto_tolto = {k: v for k, v in scartata.items()
                    if k not in ("ciclo_vita_totale", "ciclo_vita_completo")}
    scarto_tolto["ciclo_vita"] = scartata["ciclo_vita"][:1]
    scarto_tolto_senza = {k: v for k, v in scarto_tolto.items()
                          if k != "attestazione_ciclo_vita"}
    # ... o sostituisce l'attestazione con una di un altro checkpoint o firmata
    # da un'altra chiave
    attestazione_altrui = dict(scarto_tolto, attestazione_ciclo_vita=_attesta(
        registro, cp_solo_rettifica, [(i_rett, h_rett)]))
    estranea = nota_lib.analizza_firmatario(nota_lib.genera_chiave("verumdoc-registro")[0])
    attestazione_falsa = dict(scarto_tolto, attestazione_ciclo_vita=_attesta(
        estranea, cp, [(i_rett, h_rett)]))
    alterata = json.loads(json.dumps(scartata))
    oggetto = json.loads(alterata["ciclo_vita"][1]["foglia_dati"])
    oggetto["riferimento"]["pratica"] = "PS-ALTRA"
    alterata["ciclo_vita"][1]["foglia_dati"] = json.dumps(
        oggetto, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    come_documento = dict(scartata, foglia_registro_dati=scarto.decode("utf-8"),
                          prova_registro=voce(scarto, i_scarto, n)["prova_registro"],
                          ciclo_vita=[])
    sconosciuta = json.loads(json.dumps(scartata))
    oggetto = json.loads(sconosciuta["ciclo_vita"][1]["foglia_dati"])
    oggetto["v"] = 3
    sconosciuta["ciclo_vita"][1]["foglia_dati"] = json.dumps(
        oggetto, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    # Revisione R05 (R4): elenco troncato dal server (rettifiche oltre il limite)
    troncata = dict(rettificata, ciclo_vita_totale=51, ciclo_vita_completo=False)
    scartata_troncata = dict(scartata, ciclo_vita_totale=60, ciclo_vita_completo=False)
    totale_maggiore = dict(rettificata, ciclo_vita_totale=2, ciclo_vita_completo=True)

    def j(valore: dict) -> str:
        return json.dumps(valore, ensure_ascii=False, indent=2)

    # Revisione 4 di R05 (U3): consistenza fra il checkpoint della prova (12) e il
    # checkpoint corrente (15): vera, e calcolata su un registro riscritto
    m = len(foglie_reg)
    consistenza_corrente = {"da": m, "a": n, "percorso": rfc6962.esadecimale(
        rfc6962.prova_consistenza(foglie, m, n))}
    riscritte = list(foglie)
    riscritte[2] = rfc6962.hash_foglia(b"pda sostituito dopo il checkpoint della prova")
    consistenza_corrente_falsa = {"da": m, "a": n, "percorso": rfc6962.esadecimale(
        rfc6962.prova_consistenza(riscritte, m, n))}

    return {"checkpoint_dopo_scarto": cp,
            "consistenza_corrente": j(consistenza_corrente),
            "consistenza_corrente_falsa": j(consistenza_corrente_falsa),
            "prova_rettificata": j(rettificata), "prova_scartata": j(scartata),
            "prova_scarto_alterato": j(alterata), "prova_scarto_come_documento": j(come_documento),
            "prova_formato_sconosciuto": j(sconosciuta), "prova_troncata": j(troncata),
            "prova_scartata_troncata": j(scartata_troncata),
            "prova_totale_maggiore": j(totale_maggiore),
            "prova_scarto_tolto": j(scarto_tolto),
            "prova_scarto_tolto_senza_attestazione": j(scarto_tolto_senza),
            "prova_attestazione_altrui": j(attestazione_altrui),
            "prova_attestazione_falsa": j(attestazione_falsa)}


if __name__ == "__main__":
    risultato = costruisci(Path(sys.argv[1]))
    sys.stdout.write(json.dumps(risultato, indent=2) + "\n")
