# libs/shared/errori — catalogo errori e formato unico (VD-C-100)

Ogni errore verso un client, in ogni servizio, è un `application/problem+json`
(RFC 9457) con un codice del catalogo. Il catalogo pubblicato per i clienti è
`docs/catalogo/errori.md` (generato).

## Formato

```json
{
  "type": "https://docs.verumdoc.it/errori/VRS-102",
  "title": "Impronta del file errata",
  "status": 400,
  "detail": "Atteso: ab12… | Calcolato: cd34…",
  "instance": "/api/v1/versamento/ud",
  "codice": "VRS-102",
  "esito": "NON_VALIDO",
  "ritentabile": false,
  "dettagli": {},
  "campi": [],
  "id_richiesta": "5f0c2d…",
  "messaggio": "Impronta del file errata"
}
```

- `esito`: `NON_VALIDO` (correggere e reinviare), `RIFIUTATO` (regola o policy,
  ritentare non serve), `ERRORE` (nostro o temporaneo; ritentare se `ritentabile`).
- `campi`: errori per campo `{campo, posizione, tipo, messaggio}` (validazione).
- `id_richiesta`: da `X-Request-ID` della richiesta se ben formato, altrimenti
  generato; torna anche nell'intestazione `X-Request-ID` e in ogni riga di log.
- `Retry-After` quando il codice ha `retry_after` nel catalogo o l'errore lo indica.
- `messaggio` = `title`, per i client del vecchio formato `{codice, messaggio}`.

## In un servizio

```python
from libs.shared.errori import ErroreApi, errore_registrato, installa_gestori_errori

installa_gestori_errori(app)       # main.py, DOPO gli add_middleware del servizio

raise ErroreApi("VRS-404", f"PdV {pdv_id} non trovato per questo cliente")
raise ErroreApi("VRS-112", f"Formato {mime} non ammesso", dettagli={"ammessi": ammessi})

except SQLAlchemyError as exc:     # guasto interno: eccezione solo nel log
    raise errore_registrato("DSH-503", exc, evento="lettura_versamenti") from None
```

- Titolo, stato e `Retry-After` vengono dal catalogo; `titolo=`, `status=`,
  `retry_after=` li sovrascrivono per il caso specifico.
- Un codice assente dal catalogo solleva `KeyError` alla costruzione: lo scoprono i test.
- `HTTPException(status, {"type"|"codice": ..., "title"|"messaggio": ..., ...})`
  del codice esistente è convertita dal gestore (chiavi in più → `dettagli`); un
  codice sconosciuto diventa `HTTP-<status>` con `dettagli.codice_originale` e
  una riga di log `codice_errore_fuori_catalogo`. Il codice nuovo usa `ErroreApi`.
- Errori di validazione FastAPI → `VAL-422`; eccezione non gestita →
  `INTERNAL-001` (500), stacktrace solo nel log.
- Per l'OpenAPI: `responses=RISPOSTE_ERRORE` (modello `Problema`).

## Aggiungere un codice

1. Aggiungere la voce in `catalogo.yaml` (campi spiegati in testa al file; la
   famiglia deve esistere e il codice inizia con la sua sigla). Un codice
   pubblicato non cambia significato e non si riusa.
2. `python tools/docs/genera_catalogo_errori.py` (rigenera `_catalogo_dati.py`
   e `docs/catalogo/errori.md`).
3. `python tools/docs/genera_catalogo_errori.py --codici` elenca i codici usati
   nel codice dei servizi ma assenti dal catalogo (lo esegue anche un test).
