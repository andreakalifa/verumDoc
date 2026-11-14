# Governo del progetto

VerumDoc è un progetto open source con licenza EUPL-1.2. Questo documento descrive come si prendono le decisioni; è volutamente breve e si aggiornerà con la comunità.

## Ruoli

- **Utenti e contributori**: chiunque usi il software, segnali problemi, proponga modifiche o riveda pull request. Non servono permessi.
- **Manutentori** (elencati in [`MAINTAINERS`](MAINTAINERS)): rivedono e accettano i contributi, gestiscono i rilasci, le segnalazioni di sicurezza (`SECURITY.md`) e il codice di comportamento (`CODE_OF_CONDUCT.md`). Ogni area critica (sicurezza, catena delle impronte, firme, ciclo di vita, migrazioni del database) ha almeno un manutentore di riferimento.
- **Responsabile del progetto**: uno dei manutentori, che decide quando il consenso non si raggiunge e cura il marchio «VerumDoc» (`NOTICE`).

## Decisioni

1. Le decisioni ordinarie (correzioni, miglioramenti coerenti con le regole) si prendono nella pull request con l'approvazione di un manutentore (`CONTRIBUTING.md` §6).
2. Le decisioni che cambiano il comportamento normativo, i permessi, lo schema del database, i formati di scambio o la direzione grafica si propongono in una issue e si registrano in `docs/registro/decisioni.md` (famiglie `VD-N`, `VD-P`, `VD-DR`, REGOLE_SVILUPPO §29.2), con motivo e stato («Da confermare», «Confermata»).
3. Le decisioni **normative** (`VD-N`, interpretazione delle Linee guida e del Regolamento) sono proposte nel progetto ma, per un servizio erogato, spettano al responsabile del servizio di conservazione di chi lo eroga: il progetto documenta la scelta predefinita e la rende configurabile.
4. Si cerca il consenso fra i manutentori; in mancanza decide il responsabile del progetto, motivando per iscritto nella issue.

## Nuovi manutentori

Un contributore con contributi continuativi e revisioni di qualità può essere proposto da un manutentore; diventa manutentore con il consenso dei manutentori in carica, registrato in `MAINTAINERS`. Un manutentore inattivo per 12 mesi passa fra gli emeriti.

## Trasparenza

I registri delle criticità (`docs/registro/criticita.md`) e delle decisioni (`docs/registro/decisioni.md`), lo stato del progetto (`STATO_PROGETTO.md`) e le regole di sviluppo (`REGOLE_SVILUPPO.md`) sono pubblici e si aggiornano a ogni rilascio.
