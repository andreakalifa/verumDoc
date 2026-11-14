# Runbook — pratica sospesa dopo i tentativi esauriti (`LC-409-TENTATIVI-ESAURITI`)

R14 (VD-C-133). Servizio `ciclo-vita` (lavoratore); documentazione
`docs/architettura/ciclo_vita.md` §9; parametro d'ambiente `LAVORATORE_TENTATIVI_MASSIMI`
(predefinito 30, 0 = tentativi illimitati come prima di R14).

## Quando si usa

- Allarme **`PraticaSospesa`** (`verumdoc_ciclo_vita_pratiche_sospese > 0`, gravità pagina,
  `infra/observability/regole/verumdoc-r14.yml`).
- In console la pagina della pratica mostra «L'ultimo tentativo di esecuzione non è riuscito»
  con il messaggio «Lavoro … sospeso dopo N tentativi falliti».

## Cosa è successo

Il lavoratore ha fallito lo stesso lavoro della pratica `LAVORATORE_TENTATIVI_MASSIMI` volte
di seguito (registro delle impronte, gateway, distribution o storage che rifiutano sempre;
un difetto del codice; oppure il processo si è interrotto a metà passo e il lease è scaduto
ogni volta). Invece di ritentare all'infinito ha messo la pratica in uno **stato di errore
esplicito**:

- `pratiche.lavoro` = NULL (nessun nuovo tentativo automatico);
- `pratiche.ultimo_errore` = `{"codice": "LC-409-TENTATIVI-ESAURITI", "lavoro_sospeso":
  "<lavoro>", "tentativi": N, "ultimo": {…ultimo errore…}, "il": "…"}`;
- ultima riga di `pratiche_esecuzioni` con esito `ERRORE`; log
  `pratica_sospesa_tentativi_esauriti`.

Ciò che il lavoro aveva già fatto resta (cancellazioni con gli esiti per oggetto, foglie del
registro, atti nel WORM): la ripresa è idempotente e continua da lì. Lo stato della pratica non
cambia; nessuna transizione manuale è ammessa (se l'esecuzione non era ancora iniziata resta
possibile solo «Respingi»).

## Diagnosi

```sql
SET app.ambito = 'conservatore';
SELECT id, tipo, stato, ultimo_errore -> 'ultimo' AS ultimo_errore, ultimo_errore ->> 'tentativi'
  FROM public.pratiche
 WHERE lavoro IS NULL AND ultimo_errore ->> 'codice' = 'LC-409-TENTATIVI-ESAURITI';
SELECT tentativo, esito, errore ->> 'codice', errore ->> 'messaggio', terminata_il
  FROM public.pratiche_esecuzioni WHERE pratica_id = :'pratica' ORDER BY id DESC LIMIT 10;
```

| `ultimo.codice` | Causa probabile | Prima di riprendere |
|---|---|---|
| `LC-503-REGISTRO` | hashchain irraggiungibile o token di servizio rifiutato | stato della hashchain, `CICLO_VITA_SERVICE_TOKEN` |
| `LC-503-GATEWAY` / `LC-503-DISTRIBUTION` | servizio a valle giù o rifiuto permanente | log del servizio a valle |
| `LC-503-STORAGE` (o `ClientError`) | permessi IAM, bucket, Object Lock | `docs/runbooks/scarto_incompleto.md` |
| `LC-500-LAVORATORE` | difetto del codice o lease scaduti (processo ucciso: memoria) | log `pratica_passo_errore`, eventi OOM del pod |

## Ripresa (dopo aver corretto la causa)

**Dalla console** (revisione R14 (4)): nella pagina della pratica, con il profilo del
responsabile del servizio, «Riprendi il lavoro» chiede il motivo (almeno 20 caratteri: la
causa corretta) e rimette il lavoro sospeso con i tentativi azzerati. Equivale a
`POST /api/v1/ciclo-vita/pratiche/{id}/riprendi` con `{"versione": <versione attuale>,
"motivo": "…"}`: solo un ruolo FISSO del conservatore con il proprio accesso (permesso mai
delegabile `pratica:riprendi`; mai una chiave API né un ruolo personalizzato), pratica
bloccata e versione attesa (`LC-409-STATO-CAMBIATO` se nel frattempo è cambiata), solo una
pratica davvero sospesa (`LC-409-NON-SOSPESA` altrimenti); voce d'audit
`ciclo_vita.pratica_ripresa` con motivo e ultimo errore, avviso all'ente
(`ciclo_vita.avviso`, sottotipo `PRATICA_RIPRESA`). Provato da
`services/ciclo-vita/tests/test_r14_pratica_sospesa.py`. **R17 (VD-P-164)**: la ripresa è una
decisione sensibile: la console chiede la conferma dell'identità e manda la conferma firmata
dal gateway (azione `PRATICA_RIPRENDI`); dopo ogni sospensione il lavoratore la pretende
(`docs/runbooks/conferme_decisione.md`).

**Solo se la console non è disponibile** (emergenza, amministratore del database), lo stesso
effetto senza audit applicativo, in una transazione. R17: con la conferma delle decisioni
attiva (sempre in produzione) il lavoratore BLOCCA una pratica rimessa così dopo la
sospensione (`PRATICA_RIPRENDI: CONFERMA_ASSENTE`): il comando vale solo con
`CONFERMA_DECISIONI=DISATTIVO` (sviluppo e collaudo); in produzione la ripresa passa dalla
console.

```sql
BEGIN;
SET LOCAL app.ambito = 'conservatore';
-- ripresa
UPDATE public.pratiche SET lavoro = ultimo_errore ->> 'lavoro_sospeso', tentativi = 0, prossimo_tentativo = now(), ultimo_errore = NULL, versione = versione + 1, updated_at = now() WHERE id = :'pratica' AND lavoro IS NULL AND ultimo_errore ->> 'codice' = 'LC-409-TENTATIVI-ESAURITI'
```

Deve aggiornare **una** riga; poi `COMMIT`. Registrare la ripresa d'emergenza nel registro degli
interventi (ticket) con la causa corretta: non lascia la voce d'audit della console. Il lavoratore riprende entro
`LAVORATORE_INTERVALLO_SECONDI`; prima di ogni esecuzione riverifica da sé la catena delle
autorizzazioni (una ripresa non salta nessun controllo). Il comando è provato dal test
`services/ciclo-vita/tests/test_r14_pratica_sospesa.py`, che lo legge da questo file.

Se la causa non è correggibile e l'esecuzione non era iniziata: «Respingi» dalla console. Se
l'esecuzione era iniziata (scarto con PdA già cancellati) e la causa è definitiva, aprire una
criticità: la pratica non si chiude a mano.
