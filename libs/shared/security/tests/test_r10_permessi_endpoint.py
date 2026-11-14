# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione R10 (V3) — controllo statico: ogni modulo dei servizi che autorizza
un'azione con un elenco di ruoli che comprende OPERATORE (il ruolo base dei ruoli
personalizzati) controlla anche il permesso dell'azione, oppure è nell'elenco
delle esenzioni con il motivo. Un modulo nuovo con lo stesso schema fa fallire
il test finché non si decide.
"""
from __future__ import annotations

import re
from pathlib import Path

RADICE = Path(__file__).resolve().parents[4]
SCHEMA = re.compile(r'"OPERATORE"|UserRuolo\.OPERATORE|\bst\.OPERATORE\b')
CONTROLLI = re.compile(r"motivo_permesso_mancante|ha_permesso|\.concesso\(|richiedi_gestore|"
                       r"ruolo_personalizzato\(")

#: modulo → perché non serve un controllo del permesso qui
ESENZIONI = {
    "services/ciclo-vita/app/config.py": "solo il predefinito di RETTIFICA_RUOLI (il controllo è "
                                         "in domain/permessi.py, Identita.concesso)",
    "services/ciclo-vita/app/domain/stati.py": "costanti dei ruoli; nessuna transizione ammette "
                                               "OPERATORE",
    "services/gateway/app/auth/ruoli.py": "scope dei ruoli fissi; per i ruoli personalizzati gli "
                                          "scope vengono dai permessi (app/auth/emissione.py)",
    "services/gateway/app/routers/dev_auth.py": "login di sviluppo (mai in produzione)",
    "services/notification/app/api/autenticazione.py": "sola lettura degli eventi dell'ente "
                                                       "(archivio:consulta è sempre compreso)",
    "services/onboarding/app/services/configurazione.py": "validatore preventivo: prova a vuoto, "
                                                          "non versa né modifica nulla",
}


def test_ogni_controllo_per_ruolo_base_controlla_anche_il_permesso() -> None:
    scoperti = []
    for file in sorted((RADICE / "services").glob("*/app/**/*.py")):
        testo = file.read_text(encoding="utf-8")
        relativo = str(file.relative_to(RADICE))
        if not SCHEMA.search(testo) or relativo in ESENZIONI:
            continue
        if not CONTROLLI.search(testo):
            scoperti.append(relativo)
    assert scoperti == [], f"ruoli con OPERATORE senza controllo dei permessi: {scoperti}"


def test_esenzioni_ancora_vere() -> None:
    for relativo in ESENZIONI:
        assert SCHEMA.search((RADICE / relativo).read_text(encoding="utf-8")), relativo


# ── Secondo giro di revisione: controllo endpoint per endpoint ───────────────────
# Per ogni rotta (funzione decorata con ``<router>.get/post/put/patch/delete``) si
# segue, dentro lo stesso modulo, tutto ciò che la rotta usa per nome (funzioni
# chiamate, dipendenze ``Depends(...)``, costanti): se in quel codice c'è un elenco
# di ruoli con OPERATORE, lì deve esserci anche un controllo dei permessi. Un modulo
# che controlla il permesso in una sola rotta non copre più le altre.

import ast  # noqa: E402

_METODI = {"get", "post", "put", "patch", "delete"}


def _definizioni(albero: ast.Module) -> dict[str, ast.AST]:
    defs: dict[str, ast.AST] = {}
    for nodo in albero.body:
        if isinstance(nodo, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            defs[nodo.name] = nodo
        elif isinstance(nodo, ast.Assign | ast.AnnAssign):
            bersagli = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
            for b in bersagli:
                if isinstance(b, ast.Name):
                    defs[b.id] = nodo
    return defs


def _e_rotta(funzione: ast.AST) -> bool:
    return any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
               and d.func.attr in _METODI for d in getattr(funzione, "decorator_list", []))


def _chiusura(radice: ast.AST, defs: dict[str, ast.AST]) -> list[ast.AST]:
    visti, da_fare, nodi = set(), [radice], []
    while da_fare:
        nodo = da_fare.pop()
        if id(nodo) in visti:
            continue
        visti.add(id(nodo))
        nodi.append(nodo)
        for n in ast.walk(nodo):
            nome = n.id if isinstance(n, ast.Name) else None
            if nome in defs:
                da_fare.append(defs[nome])
    return nodi


def test_ogni_rotta_con_ruolo_base_controlla_anche_il_permesso() -> None:
    scoperte = []
    for file in sorted((RADICE / "services").glob("*/app/**/*.py")):
        relativo = str(file.relative_to(RADICE))
        if relativo in ESENZIONI:
            continue
        testo = file.read_text(encoding="utf-8")
        if not SCHEMA.search(testo):
            continue
        albero = ast.parse(testo)
        defs = _definizioni(albero)
        for nodo in ast.walk(albero):
            if not isinstance(nodo, ast.FunctionDef | ast.AsyncFunctionDef) or not _e_rotta(nodo):
                continue
            codice = "\n".join(ast.get_source_segment(testo, n) or ""
                               for n in _chiusura(nodo, defs))
            if SCHEMA.search(codice) and not CONTROLLI.search(codice):
                scoperte.append(f"{relativo}::{nodo.name}")
    assert scoperte == [], f"rotte con OPERATORE senza controllo dei permessi: {scoperte}"
