# Runbook — conferma firmata delle decisioni sensibili (R17, VD-P-164)

Servizi: `gateway` (firma), `ciclo-vita` API (registra) e lavoratore (verifica). Codice:
`libs/shared/conferme_decisione.py`, `services/gateway/app/auth/conferme_decisione.py`,
`services/ciclo-vita/app/application/conferme.py`; migrazione `20261112_059`. Architettura:
`docs/architettura/sicurezza.md` §10.

## Che cosa protegge

Approvazione ed esecuzione di uno scarto, esecuzione di un riversamento in uscita, richiesta
e approvazione di un annullamento, rettifica, ripresa di una pratica sospesa. Ognuna porta
una conferma che il **gateway** firma con una chiave **solo sua** (Ed25519) dopo una conferma
dell'identità recente della persona in quella sessione; l'API di ciclo-vita la registra; il
**lavoratore** la riverifica con le chiavi pubbliche della propria configurazione prima di
eseguire. Un'API di ciclo-vita compromessa non può fabbricarla.

## Configurazione

| Dove | Chiave | Valore |
|---|---|---|
| Secret `gateway-secrets` | `CONFERME_CHIAVE_PRIVATA` | chiave Ed25519: PEM PKCS#8 o 32 byte in base64 |
| ConfigMap del gateway | `CONFERME_KID` | identificativo della chiave (es. `gw-1`) |
| ConfigMap del gateway | `CONFERME_DURATA_SECONDI` | validità della conferma (30–900, predefinito 300) |
| ConfigMap di ciclo-vita (API e lavoratore) | `CONFERME_CHIAVI_PUBBLICHE` | JSON `{"gw-1": "<32 byte in base64>"}` |
| ConfigMap di ciclo-vita | `CONFERME_KID_REVOCATI` | kid non più accettati (elenco separato da virgole o JSON) |
| ConfigMap di ciclo-vita | `CONFERMA_DECISIONI` | `ATTIVO` (in produzione non si spegne: l'avvio rifiuta `DISATTIVO`) |
| Database | gruppo `verumdoc_conferme_registra` | SOLO al login dell'API di ciclo-vita |

In produzione il gateway non parte senza chiave valida, ciclo-vita non parte senza chiavi
pubbliche. Generazione della coppia (una sola volta, fuori dal cluster):

```bash
python - <<'EOF'
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from libs.shared.conferme_decisione import chiave_pubblica_b64
k = Ed25519PrivateKey.generate()
seme = k.private_bytes_raw()
print("CONFERME_CHIAVE_PRIVATA =", base64.b64encode(seme).decode())  # solo nel Secret
print("pubblica =", chiave_pubblica_b64(k))
EOF
```

## Rotazione della chiave

1. Generare la coppia nuova con un kid nuovo (es. `gw-2`).
2. Aggiungere la pubblica nuova in `CONFERME_CHIAVI_PUBBLICHE` di ciclo-vita accanto alla
   vecchia (`{"gw-1": "…", "gw-2": "…"}`) e distribuire (API e lavoratore).
3. Cambiare `CONFERME_CHIAVE_PRIVATA` e `CONFERME_KID` del gateway.
4. Dopo almeno `CONFERME_DURATA_SECONDI` (e lo svuotamento delle code: nessuna pratica con
   lavoro chiesto con la chiave vecchia e non ancora preso dal lavoratore), togliere la
   pubblica vecchia o metterne il kid in `CONFERME_KID_REVOCATI`.

Le conferme già verificate e consumate dal lavoratore per un lavoro in corso restano valide
dopo la rotazione o la revoca (il contenuto si ricontrolla comunque).

## Compromissione della chiave privata del gateway

Mettere SUBITO il kid in `CONFERME_KID_REVOCATI` di ciclo-vita e ruotare (sopra). Le
decisioni registrate con quella chiave e non ancora eseguite si bloccano
(`CONFERMA_CHIAVE_REVOCATA`): si respingono e si ripetono con la chiave nuova. Controllare
l'audit `decisioni.conferma_firmata` del periodo.

## Diagnosi

- Console: «Conferma firmata della decisione richiesta» (`LC-403-CONFERMA`): la decisione è
  stata rifiutata dall'API senza scritture; `dettagli.difetti` dice perché (scaduta, già
  usata, contenuto o versione diversi). Ripetere la decisione dalla pagina aggiornata.
- `ACC-503-CONFERMA-DECISIONE` (gateway) o `LC-503-CONFERMA` (ciclo-vita): configurazione
  mancante, vedere la tabella sopra.
- Allarmi **`ScartoAutorizzazioniNonVerificate`** e **`PuntualeAutorizzazioniNonVerificate`**:
  il lavoratore ha bloccato una pratica (`ultimo_errore.codice = LC-409-AUTORIZZAZIONI`); se
  i difetti iniziano con `CONFERMA_` o con il nome di un'azione (`SCARTO_ESEGUI: …`), la
  conferma non regge: anomalia di sicurezza da trattare come una pratica fabbricata
  (`docs/runbooks/scarto_incompleto.md`, § autorizzazioni non verificate). Nessun effetto è
  stato prodotto.

```sql
SET app.ambito = 'conservatore';
-- conferme registrate di una pratica
SELECT azione, kid, versione_pratica, registrata_il, utente_id
  FROM public.conferme_decisione WHERE pratica_id = :'pratica' ORDER BY registrata_il;
-- fatti del lavoratore (verifiche e sospensioni)
SELECT evento, azione, conferma_id, created_at
  FROM public.conferme_lavoratore WHERE pratica_id = :'pratica' ORDER BY id;
```

## Pratiche aperte al deploy

La migrazione 059 scrive in `conferme_transizione_r17` le pratiche aperte con le azioni già
decise prima (per esempio uno scarto già ESEGUIBILE o un annullamento già APPROVATO): per
quelle azioni il lavoratore non pretende la conferma. Le decisioni successive la richiedono.

Revisione R17: l'esenzione vale solo per il giro di esecuzione in corso alla migrazione.
Chiuso quel giro (fatto `GIRO_CHIUSO` del lavoratore), un nuovo giro di scarto, riversamento
in uscita o rettifica vuole una conferma nuova. Le pratiche già SOSPESE prima della 059
(tentativi esauriti) ricevono dalla migrazione un fatto `SOSPESA`: la ripresa chiede la
conferma come per le sospensioni successive.

## Un giro di esecuzione = una conferma

Per scarto, riversamento in uscita e rettifica la conferma autorizza UN giro. Il lavoratore
scrive `GIRO_CHIUSO` in `conferme_lavoratore` quando il giro termina; una conferma con
`registrata_il` anteriore all'ultimo giro chiuso è rifiutata (`CONFERMA_GIRO_PRECEDENTE`,
pratica bloccata). Unica eccezione: la ripresa automatica dello scarto con eccezioni chiesta
dal lavoro periodico, che scrive il fatto `RIPRESA_SISTEMA` (lo scrive solo il lavoratore,
mai l'API): allora vale la conferma del giro precedente, già consumata per quella pratica.
Diagnosi: `SELECT evento, created_at FROM conferme_lavoratore WHERE pratica_id = …
ORDER BY id` (con il login del lavoratore o del proprietario).

## Integrazioni con chiave API

Con la conferma attiva una chiave API non chiede più annullamenti né rettifiche
(`LC-403-CONFERMA`): le decide una persona dalla console (VD-P-174).
