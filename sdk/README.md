# VerumDoc SDK Python 3

Client Python ufficiale delle API di [VerumDoc](https://verumdoc.it), servizio di
conservazione digitale a norma AgID. Sincrono (`VerumDoc`) e asincrono
(`VerumDocAsincrono`), su `httpx` e `pydantic` 2.

> **Versione 3.0.0: cambio incompatibile con la 2.x.** Nomi dei metodi, modelli ed
> eccezioni sono stati riscritti sull'API della release R03 (vedi «Dalla 2.x» in fondo).

Contratto completo delle API: [`docs/catalogo/openapi.yaml`](../docs/catalogo/openapi.yaml)
(OpenAPI 3.1 unica) ed [`endpoint.md`](../docs/catalogo/endpoint.md); codici d'errore in
[`errori.md`](../docs/catalogo/errori.md).

## Installazione

```bash
pip install verumdoc-sdk          # Python ≥ 3.11
```

## Autenticazione

```python
from verumdoc_sdk import VerumDoc, ChiaveApi, TokenBearer

# Chiave API del sistema versante (mostrata una volta nel portale): X-Api-Key
vd = VerumDoc("https://api.verumdoc.it", credenziali=ChiaveApi("vd_<id>_<segreto>"))

# Oppure identificativo e segreto separati (X-Api-Key-Id + X-Api-Key-Secret)
vd = VerumDoc(credenziali=ChiaveApi(identificativo="vd_<id>", segreto="<segreto>"))

# Token JWT di un utente; una funzione permette di rinnovarlo a ogni richiesta
vd = VerumDoc(credenziali=TokenBearer(lambda: sessione.token_valido()))
```

Senza `credenziali` l'SDK legge `VERUMDOC_API_KEY` (chiave completa) o `VERUMDOC_TOKEN`;
senza `base_url` legge `VERUMDOC_URL` (predefinito `https://api.verumdoc.it`).

Per sapere a quale ente e a quale piano corrispondono le credenziali:

```python
io = vd.identita()                 # GET /api/v1/me
print(io.tenant_id, io.piano, io.auth_method, io.ente.ragione_sociale)
```

La chiave API vale per versare, leggere lo stato, caricare file, leggere gli eventi,
cercare, elencare i versamenti e chiedere un PdD. **La gestione dei webhook richiede il
token di un amministratore dell'ente.**

## Versare un'unità documentaria

```python
from verumdoc_sdk import VerumDoc, ChiaveApi

indice = {
    "classe_documentale": "fattura",
    "chiave": {"registro": "FATTURE", "anno": "2026", "numero": "FT-001"},
    "documenti": [{
        "id": "doc-1", "tipo": "PRINCIPALE", "nome_file": "fattura.pdf",
        "formato": "application/pdf",
        "parametri": {                                   # metadati minimi Allegato 5
            "identificativo_documento": "FT-001", "modalita_formazione": "a",
            "tipologia_documentale": "fattura", "data_documento": "2026-03-19",
            "soggetto_produttore_id": "12345678901",
            "soggetto_produttore_nome": "Acme S.r.l.", "oggetto": "Fattura marzo",
        },
        # "hash" si può omettere: l'SDK calcola lo SHA-256 dai byte del file
    }],
}

with VerumDoc(credenziali=ChiaveApi("vd_<id>_<segreto>")) as vd:
    esito = vd.versa(indice, file={"fattura.pdf": "/dati/fattura.pdf"})
    print(esito.pdv_id, esito.risposta.stato, esito.risposta.avvisi)
    print("chiave:", esito.chiave_idempotenza, "ripetuto:", esito.ripetuto)
```

- Allegati, annessi e annotazioni vanno dichiarati come voci di `documenti` con il loro
  `tipo`: il campo annidato `allegati` di un documento non è usato dal servizio.
- `file` associa a ogni `nome_file` dell'indice una sorgente: percorso (`str`/`Path`),
  `bytes`, oppure `Sorgente` (vedi sotto).
- **Idempotency-Key**: l'SDK ne genera una per ogni `versa` e la riusa in ogni
  ritentativo. Per riprendere dopo un crash, salvare `esito.chiave_idempotenza` (o
  passarne una propria con `chiave_idempotenza=`) e richiamare `versa` con la stessa
  chiave: lo stesso versamento restituisce lo stesso PdV (`esito.ripetuto` vero,
  HTTP 200), senza duplicati.
- Durante la transizione (fino al 31/03/2027) un versamento senza chiave è ancora
  accettato con l'intestazione `Deprecation`: l'SDK manda sempre la chiave.

### File grandi: sessioni di caricamento

`versa` sceglie da solo (`modo="auto"`):
- **multipart** (una richiesta) per file rileggibili fino a `soglia_multipart`
  (predefinito 32 MB);
- **per riferimento** oltre la soglia o per flussi non rileggibili: ogni file passa da
  una sessione di caricamento (`POST /api/v1/caricamenti`), con le parti inviate **in
  parallelo** (`concorrenza_parti`, predefinito 4) e lette in ordine dalla sorgente,
  ognuna con `X-Impronta-Parte`; poi completamento, attesa della verifica del
  servizio e versamento JSON `{"indice", "file": {nome: caricamento_id}}`.

L'SDK non tiene mai un file intero in memoria: al più `concorrenza_parti + 1` parti
(la dimensione della parte la decide il servizio, 8 MB predefiniti). Lo SHA-256
calcolato sui byte inviati si confronta con quello del servizio
(`ErroreIntegrita` se diverso) e finisce nell'indice.

```python
from verumdoc_sdk import Sorgente

# Da un flusso (es. una risposta HTTP o un oggetto S3): la dimensione è obbligatoria
s = Sorgente.da_flusso(risposta.iter_bytes(), dimensione=4_500_000_000, nome="video.mp4")
esito = vd.versa(indice_video, file={"video.mp4": s})

# Ripresa: stessa chiave di versamento → stesse sessioni, si reinviano solo le parti mancanti
esito = vd.versa(indice_video, file={"video.mp4": "/dati/video.mp4"},
                 chiave_idempotenza="erp-2026-000123")
```

Le sessioni si possono usare anche a mano:

```python
fatto = vd.carica("/dati/video.mp4", chiave_idempotenza="video-123-v1")  # riprendibile
print(fatto.caricamento_id, fatto.sha256, fatto.stato.formato_rilevato)
esito = vd.versa_per_riferimento(indice_con_hash, {"video.mp4": fatto.caricamento_id})
# oppure: apri_caricamento, invia_parte, completa_caricamento, stato_caricamento,
#         attendi_caricamento, annulla_caricamento
```

### Stato, attesa e rapporto

```python
stato = vd.stato_versamento(esito.pdv_id)
stato = vd.attendi_versamento(esito.pdv_id)        # fino a CONSERVATO, ERRORE o ANNULLATO
if stato.stato == "CONSERVATO":
    vd.scarica_rapporto(esito.pdv_id, "rdv.xml")   # verifica X-Impronta-SHA256
```

`attendi_*` interroga con intervalli crescenti (`PoliticaAttesa(intervallo_iniziale=1,
fattore=1.5, intervallo_massimo=30, timeout=3600)`) e solleva `TempoScaduto` oltre il
limite. Per molti versamenti conviene leggere gli **eventi** invece di interrogare.

Dalla 3.2 (R05, ciclo di vita): `stato.annullamento` (pratica, data, stato precedente)
per un PdV annullato e `stato.rettifica` (`rettifica_di` sul PdV di rettifica,
`rettificato_da` sull'originale) quando il PdV è coinvolto in una rettifica; negli
altri casi sono `None` e la risposta del servizio è quella di prima.

## Errori

Ogni errore dell'API è `application/problem+json` con un codice del catalogo; l'SDK
lo trasforma in un'eccezione secondo l'`esito`:

| Eccezione | Esito | Cosa fare |
|---|---|---|
| `NonValido` | NON_VALIDO | correggere richiesta o pacchetto e reinviare |
| `Rifiutato` | RIFIUTATO | regola o policy: ripetere non serve |
| `ErroreTemporaneo` | ERRORE, `ritentabile` | l'SDK ha già ritentato: riprovare più tardi |
| `ErroreServizio` | ERRORE, non ritentabile | segnalare con `id_richiesta` |

Tutte hanno `codice`, `status`, `titolo`, `dettaglio`, `dettagli`, `campi` (errori
per campo), `id_richiesta`, `retry_after` e `problema` (il corpo completo).
Altre eccezioni: `ErroreRete`, `RispostaNonValida`, `ErroreIntegrita`, `TempoScaduto`,
`CaricamentoNonUtilizzabile`; tutte derivano da `ErroreVerumDoc`.

```python
from verumdoc_sdk import NonValido, Rifiutato

try:
    vd.versa(indice, file={"fattura.pdf": "fattura.pdf"})
except NonValido as e:
    print(e.codice, e.titolo)                    # es. VRS-130 Metadati non conformi
    for r in e.dettagli.get("rilievi", []):
        print(r)
except Rifiutato as e:
    if e.codice == "VRS-124":
        print("Chiave UD già usata dal PdV", e.dettagli["pdv_id_esistente"])
    elif e.codice == "VRS-127":                  # stessa richiesta di un PdV rifiutato
        print("PdV", e.dettagli["pdv_id_esistente"], "è", e.dettagli["stato_esistente"])
```

Codici da conoscere (R03; elenco completo in `docs/catalogo/errori.md`):

| Codice | Eccezione | Quando |
|---|---|---|
| VRS-114 | `NonValido` (400) | `allegati` annidati non vuoti: usare documenti piatti con `tipo` ALLEGATO/ANNESSO/ANNOTAZIONE |
| VRS-124 | `Rifiutato` (409) | la chiave UD (registro, anno, numero) è di un altro PdV attivo con contenuto diverso (prima VRS-121) |
| VRS-127 | `Rifiutato` (409) | la richiesta ripetuta (stessa `Idempotency-Key` o stesso contenuto) corrisponde a un PdV rifiutato, in errore o annullato: per riversare correggere contenuto e chiave |
| QTA-001 / QTA-002 | `ErroreTemporaneo` (429) | limite di richieste al minuto o di PdV al giorno del piano (o il valore proprio dell'ente); `retry_after` dice quando riprovare (per QTA-002 la mezzanotte) |
| PDND-503-OCCUPATO | `ErroreTemporaneo` (503) | troppe richieste contemporanee su PDND: l'SDK ritenta col `Retry-After` |

**Ritentativi automatici** (`PoliticaRitentativi(tentativi=4, attesa_iniziale=0.5,
fattore=2, attesa_massima=30)`): solo per errori con `ritentabile` vero (o errori di
rete) e solo per richieste che si possono ripetere senza effetti doppi: letture,
PUT/DELETE, versamenti, aperture di sessione, richieste di PdD e creazione di webhook
(hanno la `Idempotency-Key`, generata dall'SDK se non la si passa con
`chiave_idempotenza=`). Il `Retry-After` del servizio prevale. Solo la riprova di una
consegna webhook non si ritenta mai da sola.

Dalla 3.1 (R04): `richiedi_pdd` e `crea_webhook` mandano la `Idempotency-Key`. Stessa
chiave e stessa richiesta entro 24 ore → lo **stesso** PdD o la **stessa**
sottoscrizione (risposta 200 con l'intestazione `Idempotent-Replayed: true`; per il
webhook anche lo stesso segreto); stessa chiave con un corpo diverso → `IDEM-409`
(`Conflitto`); richiesta identica ancora in corso → `IDEM-409-IN-CORSO`, ritentata
dall'SDK dopo il `Retry-After`.

## Eventi

```python
cursore = carica_cursore()                       # 0 la prima volta
for evento in vd.itera_eventi(dopo=cursore, tipi=["versamento.conservato"]):
    if gia_gestito(evento.id):                   # id = identità (uguale ai webhook)
        continue
    gestisci(evento)                             # evento.tipo, evento.risorsa.id, evento.dati
    salva_cursore(evento.posizione)              # il cursore è la POSIZIONE, non l'id
```

Dalla R03 il cursore è `posizione` (assegnata quando l'evento diventa visibile,
sempre crescente): un evento registrato con un commit lento compare dopo, con una
posizione più alta, e non si perde. Chi salvava `evento.id` deve passare a
`evento.posizione` (o al `cursore` della pagina); per gli eventi già presenti prima
del rilascio posizione e id coincidono, quindi il cursore salvato resta valido.

## Webhook

```python
# Solo amministratore dell'ente (TokenBearer)
w = vd.crea_webhook("https://erp.example.org/verumdoc", ["versamento.conservato",
                    "versamento.rifiutato"], descrizione="ERP")
conserva_in_segreto(w.segreto)                   # mostrato solo ora
vd.modifica_webhook(w.id, attiva=False)
fallite = vd.elenca_consegne(w.id, stato="FALLITA")
vd.riprova_consegna(w.id, fallite.consegne[0].id)
```

### Verificare la firma di una consegna ricevuta

`verifica_firma` è una funzione pura (nessun I/O): firma
`VerumDoc-Firma: t=<unix>,v1=<hex HMAC-SHA256(segreto, t + "." + corpo)>` e tolleranza
di 5 minuti contro le ripetizioni.

```python
from verumdoc_sdk import verifica_firma, leggi_evento

# Esempio verificabile (docs/integrazione/webhook.md)
corpo = (b'{"creato_il":"2026-10-21T10:00:00+00:00","dati":{"pdv_id":'
         b'"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90"},"id":42,"risorsa":{"id":'
         b'"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90","tipo":"versamento"},'
         b'"tipo":"versamento.ricevuto"}')
firma = "t=1792576800,v1=362a779b910d922709231ed641f88061f03db35fa8a4a1bc939f4afe65b228ad"
assert verifica_firma("whsec_esempio-di-segreto-non-usare", firma, corpo, adesso=1792576800)

# In un'applicazione (es. FastAPI): firma sui byte ricevuti, poi 2xx subito
@app.post("/verumdoc")
async def ricevi(request: Request):
    corpo = await request.body()
    if not verifica_firma(SEGRETO, request.headers.get("VerumDoc-Firma"), corpo):
        return Response(status_code=401)
    if gia_visto(request.headers["VerumDoc-Id-Consegna"]):   # consegna almeno una volta
        return Response(status_code=200)
    accoda(leggi_evento(corpo))
    return Response(status_code=200)
```

## Ricerca, elenco versamenti, pacchetti di distribuzione

L'elenco dei versamenti non ha un totale (a un milione di versamenti al giorno un
conteggio a ogni pagina non regge): `altri` dice se c'è una pagina successiva e
`cursore`, opaco, la apre. Un cursore alterato dà `NonValido` DSH-104.

La ricerca testuale trova **parole intere** (tutte quelle indicate, analizzatore
italiano), non parti di parola; `campi_testo` dice in quali campi cerca. `total` è
contato fino a un tetto (`total_esatto` falso se i risultati sono di più): per
scorrere si segue il cursore. I metadati riservati non sono cercabili.

```python
for doc in vd.itera_ricerca("fattura marzo", classe_documentale="fattura", anno=2026):
    print(doc.aip_id, doc.urn, doc.stato)

# Elenco dei versamenti dal più recente, a cursore (senza tenant_id: l'ente dell'identità)
for v in vd.itera_versamenti(stato="ERRORE"):     # segue il cursore pagina per pagina
    print(v.pdv_id, v.motivo_rifiuto)

pagina = vd.elenca_versamenti(limite=50)          # una pagina sola
if pagina.altri:
    seguente = vd.elenca_versamenti(limite=50, cursore=pagina.cursore)

pdd = vd.richiedi_pdd(aip_id, "ESIBIZIONE", "Richiesta dell'ufficio legale")
if pdd.stato != "PRONTO":                        # consegna ZIP: preparato fuori richiesta
    pdd = vd.attendi_pdd(pdd.pdd_id)
for d in pdd.documenti:
    print(d.nome_file, d.signed_url, d.impronta_verificata)
```

## Client asincrono

Stessi metodi con `await`; i flussi possono essere iterabili asincroni o oggetti con
`async read(n)`; le parti viaggiano in parallelo come task.

```python
import asyncio
from verumdoc_sdk import VerumDocAsincrono, ChiaveApi, Sorgente

async def main():
    async with VerumDocAsincrono(credenziali=ChiaveApi("vd_<id>_<segreto>")) as vd:
        s = Sorgente.da_flusso(oggetto_s3.iter_chunks(), dimensione=dim, nome="scan.tiff")
        esito = await vd.versa(indice, file={"scan.tiff": s})
        stato = await vd.attendi_versamento(esito.pdv_id)
        async for evento in vd.itera_eventi(dopo=0):
            print(evento.tipo)

asyncio.run(main())
```

## Sviluppo locale senza ingress

```python
vd = VerumDoc("http://localhost:8000", basi={
    "/api/v1/eventi": "http://localhost:8006", "/api/v1/webhook": "http://localhost:8006",
    "/api/v2": "http://localhost:8008", "/api/dashboard": "http://localhost:8009",
})
```

## Dalla 2.x

| 2.x | 3.0 |
|---|---|
| `VerumDocClient` (solo asincrono) | `VerumDoc` (sincrono) e `VerumDocAsincrono` |
| `api_key_id=`, `api_key_secret=`, `jwt_token=` | `credenziali=ChiaveApi(...)` / `TokenBearer(...)` |
| `versa_file(path, classe)` | `versa(indice, file={...})` con metadati e `Idempotency-Key` |
| `stato_versamento` → `StatoPdvResponse` | `stato_versamento` → `StatoVersamento` (campi reali) |
| `cerca_aip(...)` → `dict` | `cerca(...)` → `RisultatoRicerca` con cursore |
| `richiedi_pdd(RichiestaDistribuzione)` | `richiedi_pdd(aip_id, tipo, motivo, ...)` |
| `AuthError`, `ValidationError`, … per stato HTTP | `NonValido`, `Rifiutato`, `ErroreTemporaneo`, … per `esito` |
| `elenca_versamenti(tenant_id, skip=…)` → `total`, `skip` | `elenca_versamenti(cursore=…)` → `cursore`, `altri`; `itera_versamenti` |
| `get_aip`, `esibizione`, `ping` | rimossi (usare `cerca`, `richiedi_pdd(..., "ESIBIZIONE", ...)`) |

## Test

```bash
cd sdk && python -m pytest -q          # server finto (httpx.MockTransport)
```

Il contratto con il gateway vero (app FastAPI in-process, PostgreSQL migrato, S3 moto)
è in `services/gateway/tests/test_sdk_contratto.py`; la corrispondenza dei modelli con
l'OpenAPI unica in `sdk/tests/test_modelli_openapi.py`.
