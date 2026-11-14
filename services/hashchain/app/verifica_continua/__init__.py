# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifica continua dell'integrità dell'archivio (R04, piano R03–R05 §5 punto 4).

Ogni PdA conservato si rilegge da storage (manifest, IPdA, file, RdV
definitivo) e si confronta con le impronte registrate e, quando la catena le
salva, con le foglie del registro a due livelli (contratto K1), almeno una volta
ogni ``VERIFICA_CICLO_GIORNI``, con una rotazione uniforme; in più verifiche su
richiesta. Gli esiti stanno nel registro delle verifiche (append-only,
partizionato per mese) consultabile via API.

Moduli:
  - ``config``: impostazioni ``VERIFICA_*``;
  - ``esiti``: regole pure (gravità, calendario, retention);
  - ``budget``: budget di letture e byte al secondo;
  - ``storage``: rilettura in streaming con impronta a blocchi e retention;
  - ``foglie``: punto d'estensione per il confronto con le foglie K1;
  - ``verificatore``: verifica di un PdA;
  - ``repository``: programmazione, registro, richieste, riepilogo;
  - ``lavoratore``: ciclo per più repliche; ``__main__``: processo separato;
  - ``router``: API ``/api/v1/hashchain/verifiche`` (inclusione in main.py a
    cura del coordinatore); ``perimetro``: chi vede e chiede che cosa.
"""
