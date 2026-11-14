# README RILASCIO — R18.2 — Rimozione degli adattatori di compatibilità (14/11/2026)

**Prerequisito: R18 applicato** (commit di base `537c568`). Criticità **VD-C-187** chiusa; decisione **VD-P-191** «Niente adattatori di compatibilità con interfacce di terzi senza specifica pubblica verificata» (confermata dal committente il 14/11/2026). **Migrazione nuova**: `20261114_060` (testa). Nessuna dipendenza nuova (tolta `defusedxml` dal solo gateway).
- Changelog: `changelog/CHANGELOG-2026-11-14-rilascio-R18_2.md`; sintesi pubblica: `CHANGELOG.md`.

## Cosa contiene
- Tolti dal gateway gli adattatori di compatibilità di R03: codice, rotte `/api/compat/...`, test, guide, impostazioni (due limiti e `NOTIFICATION_INTERNAL_URL`), flusso di rete gateway → notification e rotte dell'ingress (manifest Kubernetes rigenerati).
- Il modulo `corpo.py` degli adattatori, eliminato da `services/gateway/app/compat/`, è ora `services/gateway/app/services/corpo.py` (usato da REST e PDND).
- Codici `CMP-*` dismessi nel catalogo degli errori (rigenerato); OpenAPI, elenco endpoint, metriche, manuale, piano della sicurezza, guida e route rigenerati con i loro strumenti.
- Registri: VD-C-187 chiusa, VD-P-191 nuova; `STATO_PROGETTO.md` aggiornato.
- Migrazione `20261114_060`: elimina la tabella delle richieste degli adattatori. **Se la tabella contiene righe la migrazione si ferma**: esportarle se servono e rilanciare con `VD_MIGRAZIONE_060_ELIMINA_RICHIESTE=1` (PdV, documenti e RdV restano). Le migrazioni storiche sono invariate.

## Come si applica (Windows, PowerShell)

### 1. Estrarre
```powershell
git checkout -b feature/R18_2-rimozione-adattatori
Expand-Archive -Force R18_2_rimozione_adattatori.zip -DestinationPath .
```

### 2. Pulizia (da PyCharm)
Apri `tools/pulizia/pulisci_progetto.py`, tasto destro → **Run 'pulisci_progetto'** e rispondi `s` (oppure, dal terminale di PyCharm, `python tools/pulizia/pulisci_progetto.py --si`). Con questo pacchetto toglie anche i 10 file eliminati (elenco sotto), compresa la cartella `services/gateway/app/compat`.

### 3. Controlli
```powershell
python tools/opensource/controlla_pubblicazione.py      # «nessun problema»
make verifiche                                          # se hai make
git grep -il -E "\bparer\b|packer"                    # solo registri, changelog e migrazioni
```

### 4. Database e servizi
```powershell
cd services\gateway; alembic upgrade head; cd ..\..   # testa 20261114_060
docker compose up -d --build gateway notification
```
In esercizio la migrazione la esegue il Job `gateway-migrazioni`; controllo: `alembic current` = `20261114_060`.

### 5. Commit
```powershell
git add -A
git commit -s -m "fix: R18.2 rimozione adattatori di compatibilità con interfacce di terzi" -m "Refs: R18.2, VD-C-187, VD-P-191"
```

## File eliminati (10)
Tolti dallo script di pulizia (passo 2):
- `docs/integrazione/compat_packer.md` — eliminato
- `docs/integrazione/compat_parer.md` — eliminato
- `services/gateway/app/compat/__init__.py` — eliminato
- `services/gateway/app/compat/corpo.py` — eliminato
- `services/gateway/app/compat/packer.py` — eliminato
- `services/gateway/app/compat/parer.py` — eliminato
- `services/gateway/app/routers/compat_packer.py` — eliminato
- `services/gateway/app/routers/compat_parer.py` — eliminato
- `services/gateway/tests/test_compat_packer.py` — eliminato
- `services/gateway/tests/test_compat_parer.py` — eliminato

## File del pacchetto (70)
Nuovi (4):
- `changelog/CHANGELOG-2026-11-14-rilascio-R18_2.md`
- `services/gateway/alembic/versions/20261114_060_rimozione_adattatori.py`
- `services/gateway/app/services/corpo.py`
- `services/gateway/tests/test_r18_2_migrazione_060.py`

Modificati (66):
- `CHANGELOG.md`
- `GUIDA_FUNZIONALITA.md`
- `GUIDA_TEST_COMPLETA.md`
- `METADATI_AGID_VERSAMENTO.md`
- `README.md`
- `README_RILASCIO.md`
- `STATO_PROGETTO.md`
- `THIRD_PARTY_NOTICES.md`
- `changelog/CHANGELOG-2026-10-21-rilascio-R03.md`
- `docs/architettura/PIANO_R03_R05.md`
- `docs/architettura/sicurezza.md`
- `docs/catalogo/endpoint.md`
- `docs/catalogo/errori.md`
- `docs/catalogo/metriche.md`
- `docs/catalogo/openapi.yaml`
- `docs/catalogo/route.md`
- `docs/integrazione/README.md`
- `docs/manuale/manuale_conservazione.md`
- `docs/manuale/piano_sicurezza.md`
- `docs/registro/criticita.md`
- `docs/registro/decisioni.md`
- `infra/helm/values.yaml`
- `infra/k8s/base/configmaps/gateway-config.yaml`
- `infra/k8s/base/ingress.yaml`
- `infra/k8s/base/network-policies/allow-gateway.yaml`
- `infra/k8s/base/network-policies/allow-notification.yaml`
- `infra/k8s/overlays/staging/kustomization.yaml`
- `infra/k8s/test_verifica_manifest.py`
- `infra/k8s/verifica_manifest.py`
- `libs/conformita/dati/fonti_repository.json`
- `libs/conformita/funzioni.py`
- `libs/shared/errori/_catalogo_dati.py`
- `libs/shared/errori/catalogo.yaml`
- `libs/shared/models/__init__.py`
- `libs/shared/security/quote.py`
- `libs/shared/security/token_canale.py`
- `publiccode.yml`
- `sdk/README.md`
- `services/ciclo-vita/app/domain/indice_pdd.py`
- `services/ciclo-vita/pyproject.toml`
- `services/gateway/app/auth/canali.py`
- `services/gateway/app/config.py`
- `services/gateway/app/main.py`
- `services/gateway/app/routers/caricamenti.py`
- `services/gateway/app/routers/pdnd.py`
- `services/gateway/app/routers/versamento.py`
- `services/gateway/app/services/caricamenti.py`
- `services/gateway/app/services/quota_versamento.py`
- `services/gateway/app/services/versamento_service.py`
- `services/gateway/pyproject.toml`
- `services/gateway/tests/test_b4_accesso_canali_quote.py`
- `services/gateway/tests/test_caricamenti.py`
- `services/gateway/tests/test_f1_correzioni_canali.py`
- `services/gateway/tests/test_f3_correzioni_revisione.py`
- `services/gateway/tests/test_pdnd.py`
- `services/gateway/tests/test_r03_quote_canali.py`
- `services/gateway/tests/test_r04_correzioni_registri_ruoli.py`
- `services/gateway/tests/test_r04_unione_migrazioni.py`
- `services/gateway/tests/test_r05_alembic_senza_pythonpath.py`
- `services/gateway/tests/test_r05_revisione_migrazioni_lock.py`
- `services/gateway/tests/test_r05_rls_ente.py`
- `services/gateway/tests/test_r05_unione_migrazioni.py`
- `tools/docs/genera_openapi.py`
- `tools/docs/test_genera_openapi.py`
- `tools/pulizia/pulisci_progetto.py`
- `tools/pulizia/test_pulisci_progetto.py`
