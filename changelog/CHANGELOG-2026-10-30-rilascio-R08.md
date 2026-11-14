# CHANGELOG — R08 — Firme e formati verificati (30/10/2026)

Secondo rilascio della roadmap R07–R13 (decisioni D01, D02, D22). Prerequisito: R07 applicato (migrazioni fino a `20261030_046`). Migrazione nuova: `20261031_047` (riprendibile, `lock_timeout`). Le firme dei documenti versati e degli atti del ciclo di vita si **verificano** (non più solo si rilevano) con verificatori intercambiabili; i formati si riconoscono con PRONOM e si governano da un catalogo in console.

> **Riserve del rilascio** (vedi «Residui»):
> - **EU DSS / `verifica-firme` è scritto ma NON costruito né provato**: Maven Central non era raggiungibile nell'ambiente di sviluppo (VD-C-143). Il verificatore provato è pyHanko con trust anchor configurate, senza Trusted List UE.
> - **veraPDF non è installato** nell'immagine di validation: il PDF/A è «dichiarato, non validato» (al più AVVISO).
> - Le parti Java delle correzioni **X8, X9 e X10** (`LimiteCorpo`, `TokenServizio`, `CaricatoreProtetto`), come il resto di `verifica-firme`, sono controllate **solo con `javac`** per la sintassi. Solo `RecuperoProtetto` (solo JDK) è compilato e provato.

> **Avviso operativo**
> - **Elenchi dei firmatari autorizzati vuoti = nessuno autorizzato.** `CICLO_VITA_FIRMATARI_AUTORIZZATI` e `CICLO_VITA_FIRMATARI_NULLA_OSTA` sono vuoti per impostazione predefinita: le autorizzazioni allo scarto e le richieste di annullamento firmate vengono rifiutate, e il nulla osta segue `NULLA_OSTA_FIRMA`. **Il conservatore li compila per ogni ente prima di qualunque scarto o annullamento firmato.**
> - **Livello predefinito delle firme RILEVATA.** Non va alzato a CRITTOGRAFICA prima di aver configurato le trust anchor (`FIRME_RADICI_PEM`) o EU DSS in esercizio: altrimenti ogni firma risulta INDETERMINATE e, con RGL-109 in BLOCCA, i PdV firmati vengono rifiutati.

## Per chi usa la console
- **Configurazione → «Verifica delle firme»** (gruppo `firme`):
  - livello minimo del conservatore (`FIRME_LIVELLO_VERIFICA`: NESSUNA, RILEVATA, CRITTOGRAFICA, CON_REVOCHE). L'ente lo può solo alzare, anche per classe;
  - firme INDETERMINATE ammesse con avviso (`FIRME_INDETERMINATE_EFFETTO`, solo conservatore);
  - firma del nulla osta (`NULLA_OSTA_FIRMA`), livello del ciclo di vita (`CICLO_VITA_FIRME_LIVELLO`), richiesta di annullamento firmata (`ANNULLAMENTO_RICHIESTA_FIRMATA`);
  - **elenchi dei firmatari autorizzati** (CF del titolare o impronta SHA-256 del certificato, solo conservatore).
- **Configurazione → «Catalogo dei formati»** (gruppo `formati`): esito per famiglia dell'Allegato 2 (AMMESSO, AVVISO, DA_CONVERTIRE, RIFIUTATO). La base è del conservatore e l'ente può solo restringere, anche per classe. Con `FORMATO.PDFA_NON_VALIDATO = COME_PDF` un PDF/A non validato vale come PDF.
- **Effetti delle regole del contenuto** (RGL-107, 108, 109, 113): l'ente può solo renderli più severi del conservatore (BLOCCA > AVVISA > IGNORA).
- **Validatore preventivo**: tabella dei file, con quelli che il catalogo rifiuterebbe e il livello delle firme che la verifica applicherà.
- **Pacchetti in arrivo**: esito delle firme in parole (firmatario, valida, non valida o indeterminata, motivo), mai il rapporto grezzo.
- **Pratiche**: la richiesta di annullamento può allegare il documento firmato (`documento_firmato`), che deve citare il versamento. Una firma staccata `.p7s` caricata da sola non è verificabile: va caricato il `.p7m` o il PDF firmato.

## Per chi gestisce il servizio
- Servizio nuovo **`verifica-firme`** (EU DSS, Java 21/Spring Boot, solo interno, profilo compose `firme`, Helm `enabled`), **da costruire e provare prima dell'uso** (avvertenza 1 del README di rilascio). Finché non è pronto: `FIRME_VERIFICATORE=pyhanko` con `FIRME_RADICI_PEM`, oppure livello RILEVATA.
- **Siegfried 1.11.1** nell'immagine di validation (SHA-256 fissati). Se Siegfried non risponde, il passo 10 dà un errore di servizio (ritentativi, poi DLQ), non un ripiego silenzioso.
- Verifica: 11 passi. Firme verificate entro `FIRME_DIMENSIONE_MAX_MB` (60) e `FIRME_TEMPO_MAX_S` (120), al più `FIRME_CONCORRENZA` (2) per processo. Oltre questi limiti l'esito è «firme non verificate»: nessun rifiuto a RILEVATA, rifiuto da CRITTOGRAFICA. Un file non firmato non è mai un rilievo.
- Ripiego pyHanko solo sugli errori di connessione e solo con esito netto, disattivato in Helm (`FIRME_RIPIEGO_PDF: "false"`). 401, 403 e 5xx del servizio sono errori ritentabili; 413 dà «firme non verificate».
- Zip del versante (rilevazione ASiC, PdD ricevuti nel riversamento): solo membri STORED o DEFLATE, entro limiti di dimensione e di rapporto. Il `mimetype` ASiC deve essere STORED.
- Rapporto di verifica nel WORM (`rapporto_verifica_firme.json`), impronta nel manifest e in `aip.rapporto_firme_sha256`, elemento `VerificaFirme` nel RdV.

## Contenuto
- **Verificatori di firma intercambiabili (D01)** — `libs/shared/firme`:
  - un'interfaccia e un esito normalizzato: firmatario (con CF), certificato, crittografia, catena, revoca, formato CAdES/PAdES/XAdES/ASiC, indicazione eIDAS con sotto-indicazione e motivo in parole, rapporto grezzo;
  - realizzazioni: client di `verifica-firme` (EU DSS, Trusted List UE dalla LOTL, OCSP/CRL, modo «locale» per il collaudo, token di servizio), **pyHanko** (CAdES e PAdES) e rilevazione.
- **Livello per classe e regole (D01)** — `FIRME_LIVELLO_VERIFICA` con verso sicuro e `LIMITE.FIRME_LIVELLO_VERIFICA` per ente. **RGL-109** (fase VERIFICA, BLOCCA) applicata dal passo 11 (`verifica_firme`). **RGL-113** (AVVISA) per le INDETERMINATE ammesse dal conservatore. Rapporto nel PdA e nel RdV.
- **Formati (D02)** — `libs/shared/formati`:
  - **Siegfried** (PRONOM) e **veraPDF** dietro un'interfaccia, più la firma binaria (`libs/shared/formati/firma_binaria.py`, ex `gateway/app/services/formato_reale.py`);
  - **catalogo dei formati** come dati versionati (`libs/verumdoc_rules/dati/formati/allegato2.v1.json`) e voci `FORMATO.<ID>.ESITO`;
  - **RGL-107** (BLOCCA) e **RGL-108** (AVVISA) al versamento (MIME) e nel passo 10 (`formato_riconosciuto`: PUID e PDF/A);
  - catalogo **3** delle regole.
- **Validatore preventivo (D02)** — `POST /api/v1/configurazione/valida` e `tools/validatore/valida_pdv.py`: campi `file`, `file_rifiutati` e `firme`. L'anteprima delle bozze conta anche i PdV che il catalogo rifiuterebbe.
- **Ciclo di vita (D22)** — autorizzazione allo scarto, nulla osta e richiesta di annullamento firmata (allegato `RICHIESTA_ANNULLAMENTO`) vengono verificati al caricamento e di nuovo dal **lavoratore** sui byte del WORM, con i parametri attuali dell'ente:
  - solo TOTAL_PASSED;
  - firmatario negli elenchi del conservatore;
  - riferimento al versamento cercato solo nel contenuto firmato dai firmatari autorizzati.
- **Console** — pagine «Verifica delle firme» e «Catalogo dei formati», tabella dei file nel Validatore, esito delle firme nei «Pacchetti in arrivo» (`GET /api/dashboard/versamenti`, campo `firme`).
- **Pulizia** — tolto il modulo di passaggio `services/gateway/app/services/formato_reale.py`: il gateway importa da `libs/shared/formati/firma_binaria.py`. Il file è nell'elenco di `tools/pulizia/pulisci_progetto.py`.

Strumenti nell'ambiente di sviluppo, verificati all'inizio del rilascio:
- PyPI e npm: sì;
- **Maven Central: no** (EU DSS non scaricabile);
- JDK 21 e Maven: presenti;
- **Siegfried 1.11.1**: sì (release GitHub, SHA-256 verificato, provato);
- **veraPDF**: no.

Il verificatore provato è pyHanko con asn1crypto, su una CA di prova generata a runtime, con CRL e OCSP simulati.

**Esito sul registro** (`docs/registro/criticita.md`):
- chiuse con riserve VD-C-073 e VD-C-074;
- parzialmente chiuse VD-C-120 e VD-C-141;
- nuove VD-C-143 (EU DSS non costruito né provato) e VD-C-144…147 (residui della revisione).

Totale 147: 26 aperte, 9 parzialmente chiuse, 26 chiuse con riserve, 86 chiuse.

**Fonti**:
- rapporto di sviluppo R08;
- revisione indipendente in due giri: il primo con X1–X14 (5 gravi, 9 medi), il secondo mirato sull'efficacia delle correzioni con Y1–Y3 (2 gravi, 1 medio). Tutti i rilievi sono stati corretti prima del rilascio, con test permanenti;
- prove di mutazione: 9 su 9 uccise nello sviluppo e 22 su 22 nella correzione (X1, X2, X3, X12, Y1, Y2).

---

## Revisione indipendente: rilievi e correzioni

| Rilievo | Gravità | Correzione | Test |
|---|---|---|---|
| **X1** RGL-109 accettava firme INDETERMINATE a CRITTOGRAFICA (certificato scaduto, SHA-1, PDF modificato dopo la firma) | Grave | A CRITTOGRAFICA e CON_REVOCHE vale solo TOTAL_PASSED (`valuta_firme`); il client DSS porta a TOTAL_PASSED solo la INDETERMINATE dovuta alla sola revoca non disponibile, a CRITTOGRAFICA; concessione del conservatore `FIRME_INDETERMINATE_EFFETTO = AVVISA` → RGL-113 (solo PdV, mai ciclo di vita). VD-P-77 | `libs/verumdoc_rules/tests/test_r08_revisione.py`, `libs/shared/firme/tests/test_r08_revisione.py`, `ciclo-vita/tests/test_r08_revisione_firme.py` |
| **X2** L'ente poteva mettere RGL-109 e RGL-107 su IGNORA | Grave | `Regola.solo_piu_severo`: effetti di RGL-107/108/109/113 con verso BLOCCA > AVVISA > IGNORA e tetto del conservatore (`LIMITE.REGOLA.<codice>.EFFETTO`). VD-P-78 | `test_r08_revisione.py::test_x2_*` |
| **X3** Ciclo di vita: nessun controllo su chi ha firmato | Grave | Elenchi riservati al conservatore `CICLO_VITA_FIRMATARI_AUTORIZZATI` e `CICLO_VITA_FIRMATARI_NULLA_OSTA` (CF dal serialNumber «TINIT-…» o impronta del certificato), controllati all'API e dal lavoratore; elenco vuoto = nessuno autorizzato. VD-P-76 | `ciclo-vita/tests/test_r08_revisione_firme.py` |
| **X4** Zip bomb nella rilevazione ASiC | Grave | `libs/shared/zip_limiti.py`: dimensione dichiarata, rapporto, lettura limitata; `mimetype` al più 256 byte; riversamento con gli stessi limiti | `libs/shared/firme/tests/test_r08_revisione.py::test_x4_*` |
| **X5** A RILEVATA ogni file oltre 512 MB era rifiutato | Grave | Oltre il limite si rileva solo se il file è firmato (testa e coda; non firmato = nessun rilievo; firmato = «firme non verificate», rifiuto solo da CRITTOGRAFICA). VD-P-79 (a) | `validation/tests/test_r08_revisione_contenuto.py::test_x5_*` |
| **X6** EU DSS: busta illeggibile trattata come «non firmato» | Medio | Il client confronta con la rilevazione locale → TOTAL_FAILED/FORMAT_FAILURE; il servizio Java dichiara `riconosciuto: false` | `test_x6_busta_corrotta_non_diventa_non_firmato` |
| **X7** Ripiego anche per 401/413/503, con rifiuto definitivo | Medio | Ripiego solo per `httpx.TransportError` e solo con esito netto; 401/403/5xx ritentabili; 413 = «firme non verificate»; nessun ripiego senza `FIRME_RADICI_PEM`; Helm allineato (60 MB) e controllato da `verifica_manifest.errori_firme`. VD-P-79 (b) | `test_x7_*`, `infra/k8s/test_verifica_manifest.py::test_r08_revisione_firme_allineate_e_ripiego_con_radici` |
| **X8** Memoria e loop asincrono | Medio | pyHanko e rilevazione in un thread con tempo massimo; `FIRME_CONCORRENZA`; Java: filtro `LimiteCorpo` prima di Spring MVC (411/413), thread Tomcat limitati (**solo javac**) | `test_x8_pyhanko_in_thread_e_tempo_massimo` |
| **X9** Token di servizio aggirabile con il percorso codificato | Medio | `TokenServizio`: token obbligatorio ovunque tranne una allow-list esatta (GET salute e metriche) sul percorso normalizzato; URI grezzi sospetti richiedono comunque il token (**solo javac**) | — (Java non costruito, VD-C-143) |
| **X10** SSRF e DoS dagli URL AIA/OCSP/CRL | Medio | `RecuperoProtetto` (http/https, porte 80/443, niente redirect, indirizzi non pubblici IPv4/IPv6 rifiutati, 10 s, ≤ 64 MiB, allow-list facoltativa `VERIFICA_RETE_DOMINI`) e `CaricatoreProtetto` per DSS (**solo javac**) | `RecuperoProtetto` provato a parte con il JDK |
| **X11** PDF/A mai validato; ripieghi silenziosi di Siegfried | Medio | PDF/A non validato al più AVVISO o come PDF (`FORMATO.PDFA_NON_VALIDATO`); Siegfried in errore = errore di servizio; processi terminati al timeout; testo della console corretto. VD-P-79 (c) | `libs/shared/formati/tests/test_r08_riconoscimento.py`, `test_r08_revisione_contenuto.py::test_x11_*` |
| **X12** Riferimento al versamento cercato anche nella parte non firmata | Medio | PDF: solo nel `/ByteRange` dell'ultima firma, che copre l'intero file; CAdES: solo l'eContent | `test_x12_*` |
| **X13** Firme e formato verificati su una seconda lettura dello staging | Medio | La copia locale ricalcola le impronte del passo 5; se sono diverse → errore transitorio | `test_x13_staging_cambiato_dopo_il_passo_5` |
| **X14** Firme staccate mai verificabili | Medio | `<nome>.p7s` verificata con `<nome>` dello stesso PdV; senza documento, esito esplicito «firma staccata non verificabile». VD-P-79 (d) | `test_x14_firma_staccata_abbinata_al_documento`, `ciclo-vita test_x14_*` |
| **Y1** X4 aggirabile con bzip2/LZMA e dimensione dichiarata falsa | Grave | Solo membri STORED e DEFLATE (gli unici che CPython decomprime entro `max_length`); `mimetype` ASiC STORED; tutti i membri del PdD controllati prima della lettura | `test_r08_revisione.py::test_y1_*`, `ciclo-vita test_y1_riversamento_*` |
| **Y2** X12 + X3 aggirabili con una seconda firma di un estraneo | Grave | PAdES: ogni firma dev'essere di un firmatario in elenco e il riferimento vale solo nella revisione firmata da loro; CAdES: eContent con almeno un SignerInfo ammesso, buste annidate escluse | `test_y2_seconda_firma_di_un_estraneo_non_lega_il_riferimento`, `test_y2_cades_firmatario_e_buste_annidate` |
| **Y3** Oltre il limite un PDF firmato poteva risultare «non firmato» | Medio | Ricerca della firma PAdES a blocchi in tutto il file (copia locale o S3), memoria costante | `test_r08_revisione_contenuto.py::test_y3_*` |

## Cambi incompatibili
- **Catalogo delle regole 3**: i nuovi versamenti fotografano `catalogo_regole = "3"` con `FIRME_LIVELLO_VERIFICA`, `FIRME_INDETERMINATE_EFFETTO`, gli esiti `FORMATO.*` e `catalogo_formati`. I PdV dei cataloghi 1 e 2 si verificano come prima: i passi 10 e 11 sono solo informativi (REGOLE §8.10).
- **Busta CAdES illeggibile** (un `.p7m` che non è un SignedData): già a RILEVATA è un rilievo RGL-109 (BLOCCA). Nel ciclo di vita non vale come «firmato» (LC-422-FIRMA).
- **Firme INDETERMINATE**: da CRITTOGRAFICA in su bloccano, salvo la concessione del conservatore (RGL-113).
- **Ciclo di vita**: una firma non valida, revocata, INDETERMINATE o di un firmatario fuori elenco si rifiuta al caricamento (LC-422-FIRMA) e blocca il lavoratore (`ALLEGATO_FIRMA_NON_VALIDA`). **Elenchi vuoti = nessuno autorizzato**. Le autorizzazioni già caricate con firme solo «rilevate» vanno ricaricate.
- **Effetti di RGL-107/108/109/113**: un valore dell'ente meno severo del conservatore vale come il tetto.
- **Pipeline della verifica**: 11 passi (prima 9). Copia locale dei file in `/tmp` entro `VERIFICA_CONTENUTO_MAX_MB` (512), con le impronte ricontrollate.
- **Zip**: membri bzip2, LZMA o con altri metodi non sono più letti (ASiC non riconosciuto; rilievo nel PdD ricevuto).
- **Siegfried**: se non è disponibile, il passo 10 dà un errore di servizio (prima: ripiego silenzioso sulla firma binaria).
- **Gateway**: `app.services.formato_reale` non esiste più; si importa `libs.shared.formati.firma_binaria`.

## Variabili, Secret, database
| Dove | Cosa | Note |
|---|---|---|
| validation | `FIRME_VERIFICATORE` (dss\|pyhanko), `FIRME_DSS_URL`, `FIRME_DSS_TIMEOUT_S`, `FIRME_RIPIEGO_PDF` (false), `FIRME_RADICI_PEM`, `FIRME_REVOCHE_RETE`, `FIRME_DIMENSIONE_MAX_MB` (60), `FIRME_TEMPO_MAX_S` (120), `FIRME_CONCORRENZA` (2), `FORMATI_RICONOSCITORE`, `SIEGFRIED_BIN`, `SIEGFRIED_HOME`, `SIEGFRIED_FIRME`, `VERAPDF_BIN`, `VERIFICA_CONTENUTO_MAX_MB` (512) | `FIRME_DIMENSIONE_MAX_MB` = `VERIFICA_DIMENSIONE_MASSIMA` di verifica-firme (controllato da `make verifiche`) |
| ciclo-vita (API e lavoratore) | `FIRME_*` come sopra | |
| verifica-firme | `VERIFICA_MODO` (trusted-list\|locale), `VERIFICA_LOTL_URL`, `VERIFICA_OJ_URL`, `VERIFICA_LOTL_KEYSTORE`, `VERIFICA_CACHE_DIR`, `VERIFICA_AGGIORNAMENTO_MS`, `VERIFICA_RADICI_PEM`, `VERIFICA_REVOCHE_IN_RETE`, `VERIFICA_DIMENSIONE_MASSIMA` (60 MB), `VERIFICA_RICHIESTE_CONTEMPORANEE` (8), `VERIFICA_RETE_DOMINI` (vuota), `VERIFICA_RETE_RISPOSTA_MAX` (64 MiB), `JWT_AUDIENCE_SERVIZIO`, `JWT_SCOPE_SERVIZIO` | |
| Secret | `FIRME_DSS_TOKEN` (validation, ciclo-vita), `JWT_PUBLIC_KEY` e `VERIFICA_LOTL_KEYSTORE_PASSWORD` (verifica-firme) | Modello in `infra/k8s/secrets/verifica-firme-secrets.template.yaml` |
| Console (conservatore) | `CICLO_VITA_FIRMATARI_AUTORIZZATI`, `CICLO_VITA_FIRMATARI_NULLA_OSTA` | **Da compilare per ogni ente prima di scarti o annullamenti firmati** |
| Helm/K8s | servizio `verifica-firme` (Deployment, Service, HPA, NetworkPolicy), NetworkPolicy aggiornate per validation e ciclo-vita | Manifest rigenerati |

## Migrazione `20261031_047` (head, dopo la 046)
- `aip.rapporto_firme_key`, `aip.rapporto_firme_sha256` (nullable, vincolo `ck_aip_rapporto_firme` NOT VALID + VALIDATE).
- `pratiche_allegati.tipo` ammette `RICHIESTA_ANNULLAMENTO`.
- Riprendibile, `lock_timeout`. Downgrade rifiutato se esistono richieste firmate o PdA con rapporto delle firme.

## Codici d'errore nuovi o cambiati
`VLD-1000`, `VLD-1001`, `VLD-1002`, `VLD-1100`, `VLD-1101`, `RGL-107`, `RGL-108`, `RGL-109`, `RGL-113`, `LC-422-RICHIESTA-FIRMATA`, `LC-503-FIRME`; `LC-422-FIRMA` ora significa «Firma del documento non accettabile» (firma verificata).

## File eliminati o spostati
- `services/gateway/app/services/formato_reale.py` → `libs/shared/formati/firma_binaria.py`. Il modulo di passaggio è tolto e il file è nell'elenco di `tools/pulizia/pulisci_progetto.py`.

## Scelte da confermare
VD-P-70…79 in `docs/registro/decisioni.md`. VD-P-70…75 vengono dallo sviluppo, VD-P-76…79 dalla correzione della revisione. VD-P-77 supera la prima frase di VD-P-71.

## Residui
- **VD-C-143**: `verifica-firme` (EU DSS) da costruire dove Maven Central è raggiungibile, poi da provare con una CA di prova (`VERIFICA_MODO=locale`) e con la LOTL reale. Vanno verificate anche le API DSS 6.2 usate dalle correzioni Java (X6, X8, X9, X10). Ingresso dal namespace `monitoring` ancora ammesso, ma solo per salute e metriche senza token.
- **veraPDF** non installato: PDF/A «dichiarato, non validato» (VD-C-074, VD-P-79 c).
- **VD-C-144**: DNS rebinding fra il controllo di `RecuperoProtetto` e la connessione; allow-list dei domini vuota per impostazione predefinita.
- **VD-C-145**: riuso di un'autorizzazione o di un nulla osta autentici di un'altra pratica (il riferimento si controlla solo per la richiesta di annullamento).
- **VD-C-146**: oltre il limite, le letture di testa, coda e a blocchi dallo staging non sono legate a VersionId o ETag.
- **VD-C-147**: un thread pyHanko oltre il tempo massimo non si interrompe.
- Nulla osta non firmato accettato con AVVISO (predefinito); firme dei PdD ricevuti in riversamento ancora solo rilevate; ODF, OOXML, TXT ed EML sono nel catalogo ma non ancora nel registro delle estensioni del versamento (VD-P-72).

## Test eseguiti
Corsa completa (`r08/esegui.sh`) dopo la correzione X1–X14:
- migrazione a `20261031_047`;
- PostgreSQL reale, storage S3 con Object Lock, Siegfried reale;
- frontend typecheck, lint, build ed e2e (**44**);
- `make verifiche` ok.

Gateway: 3 test end-to-end aggiornati (riconoscitore esplicito senza Siegfried) e suite rieseguita. Un test di concorrenza dei contatori è intermittente per un deadlock di PostgreSQL, non legato a R08: rieseguito 3 volte, sempre verde.

Dopo il secondo giro Y1–Y3 sono state rieseguite intere libs/shared (339), validation (172), ciclo-vita (161) e distribution (162).

Totale **3.815 passati, 0 falliti** (1 saltato facoltativo). Mutazioni: 9 su 9 nello sviluppo e 22 su 22 nella correzione.

Alla chiusura sono stati rieseguiti solo i test toccati dalla pulizia: `gateway/tests/test_formato_reale.py` (21) e `tools/pulizia` (4).
