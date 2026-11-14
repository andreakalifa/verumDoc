# Sicurezza — segnalazione responsabile delle vulnerabilità

VerumDoc conserva documenti con valore giuridico: una vulnerabilità può compromettere l'integrità o la riservatezza di archivi pubblici. Ti chiediamo di segnalarla **in privato** e di darci il tempo di correggerla prima di renderla pubblica.

## Come segnalare

- **Non aprire una issue pubblica**, una discussione o una pull request che descriva la vulnerabilità.
- Usa uno di questi canali:
  1. la funzione **«Report a vulnerability»** (segnalazione privata delle vulnerabilità) della pagina *Security* del repository su GitHub, se attiva;
  2. l'indirizzo dedicato: **DA COMPILARE** (indirizzo del progetto, non personale; fornisce: manutentori).
- Se vuoi cifrare la segnalazione: chiave OpenPGP **DA COMPILARE** (impronta pubblicata qui dai manutentori).

Indica, per quanto possibile: componente e versione (commit), descrizione e impatto, passi per riprodurre o una prova di concetto **con dati fittizi**, eventuale correzione proposta, se e come vuoi essere citato.

Non usare dati personali reali né documenti di enti reali nelle prove. Non eseguire prove su installazioni di altri senza il loro consenso scritto: questa politica riguarda il codice del progetto, non i servizi erogati da terzi con VerumDoc (per quelli rivolgiti al conservatore che li eroga).

## Cosa ti garantiamo

| Fase | Tempo indicativo |
|---|---|
| Conferma di ricezione | entro 5 giorni lavorativi |
| Prima valutazione (gravità, componenti) | entro 15 giorni lavorativi |
| Correzione o mitigazione per le vulnerabilità gravi | di norma entro 90 giorni |
| Pubblicazione coordinata (avviso di sicurezza, CVE se pertinente) | concordata con chi segnala |

Chi segnala in buona fede, nel rispetto di questa politica, non sarà oggetto di azioni da parte dei manutentori; sarà ringraziato nell'avviso, se lo desidera.

## Versioni supportate

Il progetto non ha ancora una versione stabile: le correzioni di sicurezza si applicano al ramo `main`. Lo stato dei rilasci è in [`CHANGELOG.md`](CHANGELOG.md).

## Ambito

Rientrano in particolare: autenticazione e sessioni (gateway, SPID/CIE, passkey, codici a tempo), isolamento fra enti (Row Level Security), integrità della catena delle impronte e del WORM, firme e conferme delle decisioni, trattamento dei metadati personali, adattatori dei canali (REST, SFTP, PDND, compatibilità), dipendenze.

Non rientrano: segreti dell'ambiente di sviluppo locale generati da `tools/dev/genera_segreti_dev.py` (sono per definizione locali e di prova), simulatori di sviluppo in `tools/simulatore_*`, configurazioni di esempio documentate come non sicure per la produzione.

## Segreti e chiavi

Nessuna chiave reale è nel repository; le chiavi dei test si generano durante l'esecuzione. Una chiave JWT di sviluppo è stata esposta nella cronologia privata precedente alla pubblicazione: **è compromessa e non deve mai essere riusata**; il repository pubblico parte da una storia nuova che non la contiene. Se trovi un segreto nel repository, segnalalo con questa procedura.
