# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Prove di infra/k8s/verifica_manifest.py (g3-backend): repository reale e values alterati."""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verifica_manifest as vm  # noqa: E402

BASE = yaml.safe_load(vm.VALUES.read_text(encoding="utf-8"))


def _valori() -> dict:
    return copy.deepcopy(BASE)


def test_repository_reale_ok() -> None:
    esito = subprocess.run([sys.executable, str(Path(vm.__file__))], capture_output=True, text=True)
    assert esito.returncode == 0, esito.stdout + esito.stderr


def test_r09_orizzonte_permanente_e_un_parametro_del_motore() -> None:
    """R09 (D04, VD-C-070): l'orizzonte non è più una variabile d'ambiente
    (RETENTION_PERMANENTE_ANNI del motore, uguale per costruzione)."""
    cfg = BASE["services"]
    for servizio in ("aip-builder", "hashchain"):
        assert "RETENTION_PERMANENTE_ORIZZONTE_ANNI" not in cfg[servizio]["config"]


@pytest.mark.parametrize("servizio", ["onboarding", "sftp-ingest"])
def test_valore_condiviso_mancante(servizio) -> None:
    v = _valori()
    del v["services"][servizio]["config"]["SFTP_RSA_BIT_MINIMI"]
    assert any("manca SFTP_RSA_BIT_MINIMI" in e for e in vm.verifica_condivisi(v))


def test_valore_condiviso_diverso_in_un_overlay() -> None:
    v = vm.unisci(_valori(), {"services": {"sftp-ingest": {"config": {
        "SFTP_RSA_BIT_MINIMI": "99"}}}})
    errori = vm.verifica_condivisi(v, "values.staging.yaml")
    assert any("diverso fra onboarding, sftp-ingest" in e for e in errori)


def test_jwt_issuer_diverso() -> None:
    v = _valori()
    v["services"]["hashchain"]["config"]["JWT_ISSUER"] = "altro"
    assert any("JWT_ISSUER diverso" in e for e in vm.verifica_condivisi(v))


@pytest.mark.parametrize("servizio,chiave", [
    ("onboarding", "API_KEY_PEPPER"), ("gateway", "API_KEY_PEPPER"),
    ("hashchain", "JWT_PUBLIC_KEY"), ("dashboard-api", "JWT_PUBLIC_KEY"),
])
def test_segreto_obbligatorio_mancante(servizio, chiave) -> None:
    v = _valori()
    v["services"][servizio]["secretKeys"].remove(chiave)
    assert any(f"{servizio}: le Settings leggono {chiave}" in e for e in vm.verifica_condivisi(v))


def test_caller_senza_url_verso_il_servizio() -> None:
    """Ingresso a hashchain aperto a un servizio che non lo chiama: errore."""
    v = _valori()
    v["services"]["hashchain"]["callers"].append("aip-builder")
    v["services"]["aip-builder"]["egress"]["services"].append("hashchain")
    errori = vm.verifica(v)
    assert any("hashchain: aip-builder è fra i callers" in e for e in errori)


def test_settings_fuori_da_config_riconosciute() -> None:
    """PdndSettings (PDND_), DatiConservatore (CONSERVATORE_), ConfigRelay (OUTBOX_)."""
    radice = Path(vm.__file__).resolve().parents[2] / "services"
    gateway = vm.campi_altre_settings(radice / "gateway" / "app")
    assert {"PDND_PUBLIC_KEY", "PDND_AUDIENCE", "OUTBOX_RELAY_ATTIVO"} <= gateway
    assert "CONSERVATORE_PEC" in vm.campi_altre_settings(radice / "dashboard-api" / "app")
    # validation non importa l'outbox: OUTBOX_* non è una sua variabile
    assert "OUTBOX_RELAY_ATTIVO" not in vm.campi_altre_settings(radice / "validation" / "app")


def test_variabile_non_letta_rifiutata() -> None:
    v = _valori()
    v["services"]["validation"]["config"]["OUTBOX_RELAY_ATTIVO"] = "true"
    assert any("validation: variabile OUTBOX_RELAY_ATTIVO non letta" in e for e in vm.verifica(v))


def test_chiave_pdnd_fra_i_segreti() -> None:
    v = _valori()
    v["services"]["gateway"]["secretKeys"].remove("PDND_PUBLIC_KEY")
    assert any("gateway: le Settings leggono PDND_PUBLIC_KEY" in e
               for e in vm.verifica_condivisi(v))


# ── R03 (i2): lavoratori, job, ingress a prefisso più lungo, limiti del corpo ──

import topologia  # noqa: E402


def test_lavoratore_env_non_letta_rifiutata() -> None:
    v = _valori()
    v["services"]["gateway"]["workers"]["verifica-caricamenti"]["env"] = {"NON_ESISTE": "1"}
    assert any(
        "gateway-verifica-caricamenti: env NON_ESISTE non letta" in e for e in vm.verifica(v))


def test_lavoratore_dedicato_richiede_interruttore_spento_nel_servizio() -> None:
    """Con il verificatore dedicato le repliche API non devono verificare anche loro."""
    v = _valori()
    v["services"]["gateway"]["config"]["CARICAMENTI_LAVORATORE_ATTIVO"] = "true"
    assert any("CARICAMENTI_LAVORATORE_ATTIVO=\"false\"" in e for e in vm.verifica(v))


def test_lavoratore_disabilitato_non_richiede_interruttore() -> None:
    v = _valori()
    v["services"]["gateway"]["workers"]["verifica-caricamenti"]["enabled"] = False
    v["services"]["gateway"]["config"]["CARICAMENTI_LAVORATORE_ATTIVO"] = "true"
    assert not [e for e in vm.verifica(v) if "CARICAMENTI_LAVORATORE_ATTIVO" in e]


def test_lavoratore_senza_http_richiede_comando() -> None:
    v = _valori()
    del v["services"]["distribution"]["workers"]["lavoratori"]["command"]
    assert any("distribution-lavoratori: senza server HTTP serve" in e for e in vm.verifica(v))


def test_caller_lavoratore_deve_avere_url_nelle_sue_variabili() -> None:
    """Il lavoratore SFTP chiama il gateway: GATEWAY_URL è nelle sue variabili."""
    v = _valori()
    del v["services"]["sftp-ingest"]["workers"]["lavoratore"]["env"]["GATEWAY_URL"]
    assert any("gateway: sftp-ingest-lavoratore è fra i callers" in e for e in vm.verifica(v))


def test_egress_lavoratore_senza_caller_simmetrico() -> None:
    v = _valori()
    v["services"]["gateway"]["callers"].remove("sftp-ingest-lavoratore")
    assert any("sftp-ingest-lavoratore → gateway: manca sftp-ingest-lavoratore fra i callers" in e
               for e in vm.verifica(v))


@pytest.mark.parametrize("rotte,atteso", [
    ([
        {"path": "/api/v1", "service": "gateway"},
        {"path": "/api/v1/eventi", "service": "notification"}],
     "l'ALB non la raggiungerebbe mai"),
    ([{"path": "/api/v1", "service": "gateway"}, {"path": "/api/v1", "service": "gateway"}],
     "duplicata"),
    ([{"path": "/", "service": "frontend"}, {"path": "/api", "service": "gateway"}],
     "l'ALB non la raggiungerebbe mai"),
])
def test_ordine_rotte_errato(rotte, atteso) -> None:
    assert any(atteso in e for e in topologia.errori_ordine_rotte(rotte))


def test_ordine_rotte_prefissi_simili_non_annidati() -> None:
    """I prefissi si confrontano per segmenti: /api/v2 non contiene /api/v20."""
    rotte = [{"path": "/api/v2", "service": "d"}, {"path": "/api/v20", "service": "x"}]
    assert topologia.errori_ordine_rotte(rotte) == []


def test_rotte_reali_instradano_i_prefissi_r03() -> None:
    """Il primo prefisso che combacia (ordine ALB) porta al servizio giusto (VD-C-098)."""
    rotte = BASE["ingress"]["routes"]

    def servizio(percorso: str) -> str:
        for r in rotte:
            if percorso == r["path"] or percorso.startswith(r["path"].rstrip("/") + "/"):
                return r["service"]
        raise AssertionError(percorso)

    assert servizio("/api/v1/hashchain/verifica/1") == "hashchain"
    assert servizio("/api/v1/eventi") == "notification"
    assert servizio("/api/v1/webhook/abc/consegne") == "notification"
    assert servizio("/api/v1/caricamenti/x/parti/3") == "gateway"
    assert servizio("/api/v2/distribution/pdd/123") == "distribution"
    assert servizio("/api/v1/versamento/ud") == "gateway"


def test_limite_corpo_sotto_impostazione_rifiutato() -> None:
    v = _valori()
    v["services"]["gateway"]["config"]["PDND_MAX_FILE_MB"] = "60"   # base64 ≈ 81 MB > 72
    assert any(
        "ingress /api/pdnd/v1/versamento: maxBodyMB 72 sotto 81" in e for e in vm.verifica(v))


def test_limite_corpo_annidato_piu_alto_rifiutato() -> None:
    v = _valori()
    for r in v["ingress"]["routes"]:
        if r["path"] == "/api/v1/caricamenti":
            r["maxBodyMB"] = 500
    assert any("maxBodyMB di /api/v1/caricamenti (500) maggiore" in e for e in vm.verifica(v))


def test_limite_corpo_rotta_piu_specifica_prevale() -> None:
    rotte = BASE["ingress"]["routes"]
    assert topologia.limite_corpo_rotta(rotte, "/api/v1/caricamenti/x/parti/1") == 16
    assert topologia.limite_corpo_rotta(rotte, "/api/v1/versamento/ud") == 110
    assert topologia.limite_corpo_rotta(rotte, "/assets/x.js") is None


def test_internet_senza_reti_interne() -> None:
    v = _valori()
    v["network"]["internetExcept"] = ["10.0.0.0/8"]
    errori = vm.verifica(v)
    assert any("non esclude 169.254.0.0/16" in e for e in errori)
    assert any("non esclude 192.168.0.0/16" in e for e in errori)


def test_porta_sftp_coerente() -> None:
    v = _valori()
    v["services"]["sftp-ingest"]["config"]["SFTP_PORTA"] = "2200"
    assert any("publicTcp.targetPort 2222 diverso da SFTP_PORTA=2200" in e for e in vm.verifica(v))
    v = _valori()
    v["services"]["onboarding"]["config"]["SFTP_PORTA_PUBBLICA"] = "2222"
    assert any("SFTP_PORTA_PUBBLICA=2222 diverso dalla porta pubblica" in e for e in vm.verifica(v))


def test_volume_condiviso_deve_essere_rwx() -> None:
    v = _valori()
    v["services"]["sftp-ingest"]["persistentVolumes"][0]["accessModes"] = ["ReadWriteOnce"]
    assert any("non ReadWriteMany" in e for e in vm.verifica(v))


def test_segreto_in_configmap_rifiutato() -> None:
    v = _valori()
    v["services"]["notification"]["config"]["WEBHOOK_CHIAVE_CIFRATURA"] = "x"
    v["services"]["notification"]["secretKeys"].remove("WEBHOOK_CHIAVE_CIFRATURA")
    errori = vm.verifica(v)
    assert any("WEBHOOK_CHIAVE_CIFRATURA è un SecretStr" in e for e in errori)


@pytest.mark.parametrize("servizio,chiave", [
    ("notification", "WEBHOOK_CHIAVE_CIFRATURA"), ("sftp-ingest", "SFTP_CHIAVE_HOST"),
    ("gateway", "SFTP_TOKEN_CHIAVE_PUBBLICA"),
    ("distribution", "API_KEY_PEPPER"), ("dashboard-api", "API_KEY_PEPPER"),
    ("notification", "API_KEY_PEPPER"), ("notification", "JWT_PUBLIC_KEY"),
])
def test_segreti_r03_obbligatori(servizio, chiave) -> None:
    v = _valori()
    v["services"][servizio]["secretKeys"].remove(chiave)
    assert any(f"{servizio}: le Settings leggono {chiave}" in e for e in vm.verifica_condivisi(v))


def test_politica_rsa_uguale_fra_onboarding_e_sftp() -> None:
    v = _valori()
    v["services"]["sftp-ingest"]["config"]["SFTP_RSA_BIT_MINIMI"] = "2048"
    assert any("SFTP_RSA_BIT_MINIMI diverso" in e for e in vm.verifica_condivisi(v))


def test_gateway_internal_url_tolta() -> None:
    """R03 b1: GATEWAY_INTERNAL_URL non è più un campo delle Settings del gateway."""
    v = _valori()
    v["services"]["gateway"]["config"]["GATEWAY_INTERNAL_URL"] = "http://gateway:8000"
    assert any("gateway: variabile GATEWAY_INTERNAL_URL non letta" in e for e in vm.verifica(v))


def test_carichi_lavoratori_e_job() -> None:
    nomi = {c["nome"]: c for c in topologia.carichi(BASE) + topologia.cronjob(BASE)}
    assert {"gateway-verifica-caricamenti", "notification-webhook", "distribution-lavoratori",
            "sftp-ingest-lavoratore", "gateway-pulizia-orfani"} <= set(nomi)
    w = nomi["notification-webhook"]
    assert w["servizio"] == "notification" and w["callers"] == [] and not w["exposed"]
    assert topologia.config_effettiva(w)["WEBHOOK_LAVORATORE_ATTIVO"] == "true"
    assert 443 in w["egress"]["internetPorts"]
    assert 443 not in nomi["notification"]["egress"]["internetPorts"]


def test_manifest_generati_allineati(tmp_path, monkeypatch) -> None:
    """I manifest nel repository sono quelli che genera_manifest.py produce ora."""
    import filecmp
    import shutil

    import genera_manifest as gm

    copia = tmp_path / "k8s"
    shutil.copytree(gm.K8S, copia, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(gm, "K8S", copia)
    gm.main()

    def differenze(c: filecmp.dircmp) -> list[str]:
        d = [f"{c.left}/{x}" for x in c.diff_files + c.left_only + c.right_only
             if x != "__pycache__"]
        for sotto in c.subdirs.values():
            d += differenze(sotto)
        return d

    assert differenze(filecmp.dircmp(Path(vm.__file__).parent, copia)) == []


# ── R03 (i3): quote condivise, Redis del limitatore, pulizia dello staging ──

def test_settings_delle_librerie_indirette_riconosciute() -> None:
    """QUOTE_* (libs.shared.security.quote) arrivano a distribution tramite
    libs.shared.limiti_richieste: import indiretto, ora riconosciuto."""
    radice = Path(vm.__file__).resolve().parents[2] / "services"
    campi = vm.campi_altre_settings(radice / "distribution" / "app")
    assert {"QUOTE_RICHIESTE_MINUTO", "LIMITI_RICHIESTE_ATTIVI"} <= campi
    # validation non usa il limitatore
    assert "LIMITI_RICHIESTE_ATTIVI" not in vm.campi_altre_settings(radice / "validation" / "app")


@pytest.mark.parametrize("servizio", ["distribution", "dashboard-api", "notification"])
def test_quote_uguali_al_gateway(servizio) -> None:
    v = _valori()
    v["services"][servizio]["config"]["QUOTE_RICHIESTE_MINUTO"] = '{"starter": 1}'
    assert any("QUOTE_RICHIESTE_MINUTO diverso" in e for e in vm.verifica_condivisi(v))


@pytest.mark.parametrize("servizio", ["distribution", "dashboard-api", "notification"])
def test_redis_obbligatorio_per_il_limitatore(servizio) -> None:
    v = _valori()
    v["services"][servizio]["secretKeys"].remove("REDIS_URL")
    v["services"][servizio]["egress"]["data"].remove("redis")
    errori = vm.verifica_condivisi(v)
    assert any(f"{servizio}: usa il limite" in e and "REDIS_URL" in e for e in errori)
    assert any(f"{servizio}: usa il limite" in e and "egress.data" in e for e in errori)


def test_ritardo_visibilita_eventi_tolto() -> None:
    v = _valori()
    v["services"]["notification"]["config"]["EVENTI_RITARDO_VISIBILITA_SECONDI"] = "2"
    assert any("EVENTI_RITARDO_VISIBILITA_SECONDI non letta" in e for e in vm.verifica(v))


def test_corpo_multipart_oltre_l_ingress_rifiutato() -> None:
    v = _valori()
    v["services"]["gateway"]["config"]["VERSAMENTO_REST_MAX_CORPO_MB"] = "500"
    assert any("VERSAMENTO_REST_MAX_CORPO_MB" in e for e in vm.verifica(v))


def test_pulizia_staging_come_cronjob() -> None:
    """La pulizia dello staging gira nel CronJob, non nei processi lunghi."""
    job = {c["nome"]: c for c in topologia.cronjob(BASE)}["gateway-pulizia-staging"]
    assert job["command"] == ["python", "-m", "app.services.pulizia_staging"]
    assert job["servizio"] == "gateway" and 443 in job["egress"]["internetPorts"]
    assert BASE["services"]["gateway"]["config"]["STAGING_PULIZIA_ATTIVO"] == "false"
    carichi = {c["nome"]: c for c in topologia.carichi(BASE)}
    assert topologia.config_effettiva(
        carichi["gateway-verifica-caricamenti"])["STAGING_PULIZIA_ATTIVO"] == "false"


# ── R03 f3 (REV-N2): /tmp del gateway coerente con budget e concorrenza ──

def test_tmp_del_gateway_coerente_nei_manifest() -> None:
    import genera_manifest as gm

    [gateway] = [c for c in vm.topologia.carichi(_valori()) if c["nome"] == "gateway"]
    pod = gm._pod(gateway)
    [tmp] = [v for v in pod["volumes"] if v["name"] == "tmp"]
    assert tmp["emptyDir"]["sizeLimit"] == BASE["services"]["gateway"]["tmpSizeLimit"]
    assert vm.errori_disco_temporaneo(_valori()["services"]) == []


def test_tmp_troppo_piccolo_per_il_budget_e_la_concorrenza() -> None:
    """Come prima di REV-N2: /tmp da 1 GiB con 8 multipart da 110 MB e 8 PDND da 50 MB."""
    v = _valori()
    v["services"]["gateway"]["tmpSizeLimit"] = "1Gi"
    errori = vm.verifica(v)
    assert any("tmpSizeLimit 1Gi (1024 MiB) sotto" in e and "sfratterebbe" in e for e in errori)
    # ridotti i limiti di conseguenza, 1 GiB torna sufficiente
    v["services"]["gateway"]["config"]["DISCO_TEMPORANEO_BUDGET_MB"] = "900"
    assert vm.errori_disco_temporaneo(v["services"]) == []
    v["services"]["gateway"]["config"]["DISCO_TEMPORANEO_BUDGET_MB"] = "4000"
    v["services"]["gateway"]["config"]["VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX"] = "4"
    v["services"]["gateway"]["config"]["PDND_RICHIESTE_CONTEMPORANEE_MAX"] = "4"
    assert vm.errori_disco_temporaneo(v["services"]) == []      # 4×110 + 4×50 = 640


def test_tmp_impostazioni_mancanti_o_budget_sotto_una_richiesta() -> None:
    v = _valori()
    del v["services"]["gateway"]["config"]["VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX"]
    assert any("VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX assente" in e
               for e in vm.errori_disco_temporaneo(v["services"]))
    v = _valori()
    v["services"]["gateway"]["config"]["DISCO_TEMPORANEO_BUDGET_MB"] = "100"
    assert any("sempre rifiutata" in e and "VERSAMENTO_REST_MAX_CORPO_MB=110" in e
               for e in vm.errori_disco_temporaneo(v["services"]))
    v = _valori()
    v["services"]["gateway"]["tmpSizeLimit"] = "tanto"
    assert any("non è una quantità valida" in e for e in vm.errori_disco_temporaneo(v["services"]))


@pytest.mark.parametrize("quantita, attesi", [("2Gi", 2048), ("1536Mi", 1536), ("512Ki", 0.5),
                                              ("1G", 1000 ** 3 / 1024 ** 2), ("abc", None)])
def test_quantita_kubernetes_in_mib(quantita, attesi) -> None:
    assert vm.mib(quantita) == attesi


# ── R04 (infra): IAM per servizio, KEDA, PgBouncer, quota, testimone ──

import genera_manifest as gm  # noqa: E402

TESTIMONE = gm.carica_values("testimone")


def test_r04_ogni_annotazione_irsa_ha_il_suo_ruolo() -> None:
    """VD-C-101: un ServiceAccount annotato senza blocco «iam» non ha ruolo in Terraform."""
    v = _valori()
    del v["services"]["dashboard-api"]["iam"]
    assert any("dashboard-api: ServiceAccount annotato per IRSA ma senza blocco «iam»" in e
               for e in vm.verifica(v))
    v = _valori()
    v["services"]["signature"]["iam"] = {"kafka": ["consumo"]}
    assert any("signature: blocco «iam» con irsa: false" in e for e in vm.verifica(v))


def test_r04_annotazioni_dei_service_account_con_il_nome_del_ruolo_terraform() -> None:
    """Annotazione = arn:aws:iam::<account>:role/<progetto>-<ambienteIam>-<servizio>,
    il nome che il modulo iam_servizi dà al ruolo (tofu test lo controlla dall'altra parte)."""
    sa = {d["metadata"]["name"]: d for d in yaml.safe_load_all(
        (Path(vm.__file__).parent / "base" / "service-accounts.yaml").read_text(
            encoding="utf-8")) if d}
    voci = {n: iam for n, iam, irsa in vm.topologia.account_di_servizio(BASE) if irsa}
    con_ruolo = set(voci)
    assert con_ruolo == {n for n, d in sa.items() if (d["metadata"].get("annotations") or {})}
    # R04: la verifica continua ha un ruolo proprio in sola lettura
    assert "hashchain-verifica" in con_ruolo
    assert voci["hashchain-verifica"]["s3"] == {"aip": ["lettura", "lettura_retention"]}
    for nome in con_ruolo:
        arn = sa[nome]["metadata"]["annotations"]["eks.amazonaws.com/role-arn"]
        assert arn.endswith(f":role/verumdoc-prod-{nome}")
        assert voci[nome]
    staging = yaml.safe_load((
        Path(vm.__file__).parent / "overlays" / "staging" / "kustomization.yaml")
                             .read_text(encoding="utf-8"))
    patch = [p for p in staging["patches"] if p["target"]["kind"] == "ServiceAccount"]
    assert {p["target"]["name"] for p in patch} == con_ruolo
    assert all("verumdoc-staging-" in p["patch"] for p in patch)


@pytest.mark.parametrize("modifica,atteso", [
    (
        lambda v: v["services"]["gateway"]["iam"]["s3"].update(archivio=["lettura"]),
        "bucket sconosciuto"),
    (
        lambda v: v["services"]["gateway"]["iam"]["s3"]["aip"].append("tutto"),
        "permesso «tutto» sconosciuto"),
    (lambda v: v["services"]["dashboard-api"]["iam"]["s3"].pop("aip"),
     "S3_BUCKET_AIP nella ConfigMap ma nessun permesso iam.s3.aip"),
    (
        lambda v: v["services"]["validation"]["iam"].pop("kafka"),
        "usa Kafka (KAFKA_BOOTSTRAP) ma iam.kafka"),
    # R04 correzioni (rev4 rilievo 7): permessi Kafka coerenti con topics.yaml
    (lambda v: v["services"]["notification"]["iam"].update(kafka=["consumo"]),
     "notification: iam.kafka senza «produzione» ma alcuni topic"),
    (lambda v: v["services"]["dashboard-api"]["iam"].update(kafka=["produzione"]),
     "dashboard-api: iam.kafka senza «consumo» ma alcuni topic"),
    (lambda v: v["services"]["validation"]["iam"].pop("gruppi_kafka"),
     "validation: iam.gruppi_kafka deve elencare"),
    (lambda v: v["services"]["aip-builder"]["iam"].update(gruppi_kafka=["*"]),
     "nomi di gruppo non validi"),
])
def test_r04_iam_errori(modifica, atteso) -> None:
    v = _valori()
    modifica(v)
    assert any(atteso in e for e in vm.verifica(v)), vm.verifica(v)


def test_r04_testimone_nel_solo_rilascio_del_suo_account() -> None:
    """K3: il testimone (account separato) non compare nel rilascio principale e
    il suo rilascio contiene solo lui, senza PgBouncer, con ingress su host proprio."""
    principali = {c["nome"] for c in vm.topologia.carichi(BASE)}
    assert "testimone" not in principali and "gateway" in principali
    assert {c["nome"] for c in vm.topologia.carichi(TESTIMONE)} == {"testimone"}
    ing = gm.ingress(TESTIMONE)
    assert [r["host"] for r in ing["spec"]["rules"]] == ["testimone.verumdoc.it"]
    assert [p["path"] for p in ing["spec"]["rules"][0]["http"]["paths"]] == ["/api/v1/testimone"]
    assert not gm.usa_pgbouncer(TESTIMONE) and gm.usa_pgbouncer(BASE)
    assert vm.verifica(TESTIMONE) == []
    v = copy.deepcopy(TESTIMONE)
    v["services"]["testimone"]["irsa"] = True
    v["services"]["testimone"]["iam"] = {"kafka": ["consumo"]}
    assert any("account separato con IRSA" in e for e in vm.verifica(v))


def test_r04_scaled_object_al_posto_dell_hpa() -> None:
    carichi = {c["nome"]: c for c in vm.topologia.carichi(BASE)}
    so = gm.scaled_object(carichi["validation"], BASE)
    kafka = [t for t in so["spec"]["triggers"] if t["type"] == "kafka"]
    assert kafka[0]["metadata"]["consumerGroup"] == "validation-service"
    assert kafka[0]["metadata"]["topic"] == "pdv.received"
    assert kafka[0]["metadata"]["sasl"] == "aws_msk_iam"
    assert kafka[0]["authenticationRef"]["name"] == "keda-msk-iam"
    assert so["spec"]["triggers"][-1]["type"] == "cpu"
    base = Path(vm.__file__).parent / "base"
    assert (base / "keda" / "validation-scaledobject.yaml").exists()
    assert not (base / "hpa" / "validation-hpa.yaml").exists()   # KEDA crea il suo HPA
    assert (base / "hpa" / "gateway-hpa.yaml").exists()           # HPA dove KEDA non serve
    pg = gm.scaled_object(carichi["distribution-lavoratori"], BASE)["spec"]["triggers"][0]
    assert pg["type"] == "postgresql" and "LIMIT" in pg["metadata"]["query"]


@pytest.mark.parametrize("modifica,atteso", [
    (lambda v: v["services"]["validation"]["autoscaling"]["keda"][
        "kafka"][0].update(gruppo="altro"),
     "il servizio legge KAFKA_GROUP_ID='validation-service'"),
    (lambda v: v["services"]["aip-builder"]["autoscaling"].update(maxReplicas=60),
     "oltre le 48 partizioni di pdv.validated"),
    (lambda v: v["services"]["validation"]["autoscaling"]["keda"]["kafka"][
        0].update(topic="inesistente"),
     "topic inesistente assente"),
    (lambda v: v["services"]["notification"]["workers"]["webhook"][
        "autoscaling"]["keda"]["postgresql"][0]
     .update(query="SELECT count(*) FROM public.webhook_consegne"), "query senza LIMIT"),
])
def test_r04_keda_errori(modifica, atteso) -> None:
    v = _valori()
    modifica(v)
    assert any(atteso in e for e in vm.verifica(v)), vm.verifica(v)


def test_r04_quota_del_namespace() -> None:
    """La somma dei limiti al massimo delle repliche deve stare nella ResourceQuota."""
    assert vm.errori_quota(BASE) == []
    v = _valori()
    v["services"]["gateway"]["autoscaling"]["maxReplicas"] = 200
    assert any("limits.cpu al massimo delle repliche" in e for e in vm.errori_quota(v))


def test_r04_connessioni_e_pgbouncer() -> None:
    assert vm.errori_connessioni(BASE) == []
    v = _valori()
    # diretto, 20 repliche
    v["services"]["gateway"]["egress"]["data"] = ["postgres", "redis", "kafka"]
    assert any("oltre postgres.maxConnessioni" in e for e in vm.errori_connessioni(v))
    v = _valori()
    v["pgbouncer"]["impostazioni"]["max_prepared_statements"] = "0"
    v["pgbouncer"]["impostazioni"]["pool_mode"] = "session"
    errori = vm.errori_connessioni(v)
    assert any("max_prepared_statements" in e for e in errori)
    assert any("pool_mode deve essere transaction" in e for e in errori)


def test_r04_pgbouncer_ini_e_rete() -> None:
    ini = gm.pgbouncer_ini(BASE)
    assert "pool_mode = transaction" in ini and "max_prepared_statements = 200" in ini
    assert "verumdoc = host=DA_TERRAFORM:rds_endpoint port=5432 dbname=verumdoc" in ini
    assert "verumdoc_lettura = host=DA_TERRAFORM:rds_reader_endpoint" in ini
    politica = gm.politica_pgbouncer(BASE)
    ammessi = {
        f["podSelector"]["matchLabels"]["app"] for f in politica["spec"]["ingress"][0]["from"]}
    assert {"gateway", "gateway-pulizia-staging", "validation", "sftp-ingest-lavoratore"} <= ammessi
    assert "hashchain" not in ammessi   # lock di sessione: connessione diretta
    gateway = gm._politica(BASE, next(
        c for c in vm.topologia.carichi(BASE) if c["nome"] == "gateway"),
                           {"notification": 8000})
    assert {"to": [{"podSelector": {"matchLabels": {"app": "pgbouncer"}}}],
            "ports": [{"port": 6432, "protocol": "TCP"}]} in gateway["spec"]["egress"]


def test_r04_job_migrazioni_prima_dei_servizi() -> None:
    """Il Job delle migrazioni non dipende da risorse del rilascio e usa l'URL diretto."""
    job = gm.job(next(c for c in vm.topologia.jobs(BASE) if c["nome"] == "gateway-migrazioni"))
    pod = job["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert c["command"] == ["python", "-m", "alembic", "upgrade", "head"]
    assert "envFrom" not in c and pod["serviceAccountName"] == "default"
    assert {"name": "DATABASE_URL", "valueFrom": {"secretKeyRef": {
        "name": "gateway-migrazioni", "key": "DATABASE_URL"}}} in c["env"]
    dep = gm.deployment(next(c for c in vm.topologia.carichi(BASE) if c["nome"] == "gateway"))
    assert {"name": "VD_SALTA_MIGRAZIONI", "value": "1"} in dep["spec"]["template"][
        "spec"]["containers"][0]["env"]


def test_r04_entrypoint_env_letta_dallo_script() -> None:
    v = _valori()
    v["services"]["gateway"]["entrypointEnv"]["VD_INVENTATA"] = "1"
    assert any("entrypointEnv VD_INVENTATA non letta" in e for e in vm.verifica(v))


def test_r04_modulo_terraform_iam_servizi(tmp_path) -> None:
    """Esegue `tofu test` sul modulo iam_servizi (ruoli dai values reali, permessi
    minimi, errori); saltato solo se OpenTofu non è installato (in CI c'è)."""
    import os
    import shutil

    tofu = os.environ.get("VD_TOFU") or shutil.which("tofu")
    if not tofu:
        pytest.skip("OpenTofu non installato (VD_TOFU o tofu nel PATH)")
    modulo = Path(vm.__file__).resolve().parents[1] / "terraform" / "modules" / "iam_servizi"
    env = {**os.environ, "TF_DATA_DIR": str(tmp_path / "tf")}
    lock_presente = (modulo / ".terraform.lock.hcl").exists()
    try:
        init = subprocess.run([tofu, "init", "-backend=false", "-input=false", "-no-color"],
                              cwd=modulo, env=env, capture_output=True, text=True, timeout=600)
        assert init.returncode == 0, init.stdout + init.stderr
        esito = subprocess.run([tofu, "test", "-no-color"], cwd=modulo, env=env,
                               capture_output=True, text=True, timeout=600)
    finally:
        if not lock_presente:   # init non lascia file nel repository
            (modulo / ".terraform.lock.hcl").unlink(missing_ok=True)
    assert esito.returncode == 0 and "0 failed" in esito.stdout, esito.stdout + esito.stderr



def test_formato_segreti_del_registro_e_del_testimone_nei_modelli() -> None:
    """R04: le chiavi del registro e del testimone sono signed note, NON PEM; il
    formato è scritto nel modello del Secret generato e la nota deve riferirsi a
    una chiave letta dal servizio."""
    servizi = BASE["services"]
    assert "NON PEM" in servizi["hashchain"]["formatoSegreti"]["CATENA_CHIAVE_PRIVATA"]
    assert "NON PEM" in servizi["testimone"]["formatoSegreti"]["TESTIMONE_CHIAVE_PRIVATA"]
    for nome, chiave in (("hashchain", "CATENA_CHIAVE_PRIVATA"),
                         ("testimone", "TESTIMONE_CHIAVE_PRIVATA")):
        modello = (
            Path(vm.__file__).parent / "secrets" / f"{nome}-secrets.template.yaml").read_text(
            encoding="utf-8")
        assert f"# {chiave}: chiave Ed25519 signed note" in modello
        assert "PKCS" not in modello
    assert vm.errori_formato_segreti(servizi) == []
    v = _valori()
    v["services"]["testimone"]["formatoSegreti"]["NON_LETTA"] = "x"
    v["services"]["hashchain"]["formatoSegreti"]["CATENA_CHIAVE_PRIVATA"] = "a\nb"
    errori = vm.errori_formato_segreti(v["services"])
    assert any("NON_LETTA non è fra le secretKeys" in e for e in errori)
    assert any("CATENA_CHIAVE_PRIVATA: nota vuota o su più righe" in e for e in errori)



def test_kafka_msk_iam_per_ogni_servizio_con_kafka() -> None:
    """R04: ogni servizio con KAFKA_BOOTSTRAP si collega a MSK con IAM."""
    con_kafka = [n for n, s in BASE["services"].items()
                 if "KAFKA_BOOTSTRAP" in (s.get("config") or {})]
    assert len(con_kafka) >= 7
    assert vm.errori_kafka_msk(BASE["services"]) == []
    v = _valori()
    del v["services"][con_kafka[0]]["config"]["KAFKA_AUTENTICAZIONE"]
    v["services"][con_kafka[1]]["config"]["KAFKA_REGIONE"] = ""
    errori = vm.errori_kafka_msk(v["services"])
    assert any(e.startswith(f"{con_kafka[0]}: KAFKA_BOOTSTRAP senza") for e in errori)
    assert any(e.startswith(f"{con_kafka[1]}: KAFKA_AUTENTICAZIONE: iam senza") for e in errori)


def test_r04_correzioni_permessi_kafka_coerenti_con_topics_yaml() -> None:
    """Ogni servizio con permessi Kafka è producer/consumer in topics.yaml, e un
    servizio che non è producer di aip.conserved/catena.checkpoint non ha
    «produzione» per loro (la policy IAM elenca i topic, non topic/*)."""
    scrive, legge = vm._produttori_consumatori()
    assert scrive["hashchain"] >= {"aip.conserved", "catena.checkpoint"}
    for servizio, topic in scrive.items():
        if servizio != "hashchain":
            assert not topic & {"aip.conserved", "catena.checkpoint"}, servizio
    assert "notification.dlq" in scrive["notification"]
    for nome, s in BASE["services"].items():
        assert vm.errori_kafka_topic(nome, s.get("iam") or {}) == [], nome


def test_r04_secondo_giro_ruoli_del_database_separati() -> None:
    """Rilievo 5 (secondo giro): ruoli separati DAVVERO in esercizio e in sviluppo.
    Esercizio: il Secret dell'amministratore (gateway-migrazioni) solo nel Job
    delle migrazioni; la manutenzione con il proprio Secret (db-manutenzione).
    Sviluppo (compose): i servizi entrano come vd_app, le migrazioni come
    amministratore, la manutenzione come vd_manutenzione, ruoli creati da db-ruoli."""
    import json

    manutenzione = gm.cronjob(next(c for c in vm.topologia.cronjob(BASE)
                                   if c["nome"] == "gateway-db-manutenzione"))
    env = manutenzione["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"]
    assert {"name": "DATABASE_URL_MANUTENZIONE", "valueFrom": {"secretKeyRef": {
        "name": "db-manutenzione", "key": "DATABASE_URL"}}} in env
    for c in vm.topologia.carichi(BASE):
        assert "gateway-migrazioni" not in json.dumps(gm.deployment(c)), c["nome"]
    for c in vm.topologia.cronjob(BASE):
        assert "gateway-migrazioni" not in json.dumps(gm.cronjob(c)), c["nome"]

    radice = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((radice / "docker-compose.yml").read_text(encoding="utf-8"))
    servizi = compose["services"]
    con_db = {n: s for n, s in servizi.items()
              if "verumdoc_dev" in str((s.get("environment") or {}).get("DATABASE_URL", ""))}
    assert len(con_db) >= 10
    for nome, s in con_db.items():
        # Revisione 3 di R05 (T5): ciclo-vita in sviluppo fa girare il lavoratore
        # nel processo: login del lavoratore (verumdoc_ciclo_vita_lavoratore)
        # R13a: il servizio di firma legge solo il registro dei fornitori (vd_firma)
        atteso = {"ciclo-vita": "//vd_ciclo_vita_lavoratore:",
                  "signature": "//vd_firma:"}.get(nome, "//vd_app:")
        assert atteso in s["environment"]["DATABASE_URL"], nome
        assert s["depends_on"]["db-ruoli"] == {"condition": "service_completed_successfully"}, nome
    gateway = servizi["gateway"]["environment"]
    assert "//vd_user:" in gateway["DATABASE_URL_MIGRAZIONI"]
    assert "//vd_manutenzione:" in gateway["DATABASE_URL_MANUTENZIONE"]
    ruoli = (radice / "infra/postgres/ruoli_sviluppo.sql").read_text(encoding="utf-8")
    assert "CREATE ROLE vd_app LOGIN PASSWORD 'vd_app_dev_only' IN ROLE verumdoc_applicazione" \
        in ruoli
    assert "IN ROLE verumdoc_manutenzione, verumdoc_applicazione" in ruoli
    assert "IN ROLE verumdoc_applicazione, verumdoc_ciclo_vita_lavoratore" in ruoli
    utenti = (radice / "infra/pgbouncer/userlist.dev.txt").read_text(encoding="utf-8")
    assert '"vd_app"' in utenti and '"vd_user"' not in utenti


def test_r04_terzo_giro_registro_sospeso_mai_nei_values() -> None:
    """T5: CATENA_REGISTRO_SOSPESO si imposta solo a mano durante un ripristino;
    nei values (anche di un overlay) fermerebbe il registro a ogni rilascio."""
    assert vm.verifica_condivisi(BASE) == []
    v = _valori()
    v["services"]["hashchain"]["config"]["CATENA_REGISTRO_SOSPESO"] = "true"
    assert any("CATENA_REGISTRO_SOSPESO si imposta solo a mano" in e
               for e in vm.verifica_condivisi(v))


def test_r04_quarto_giro_chi_elenca_un_bucket_ha_il_permesso() -> None:
    """F1: la hashchain elenca il WORM (controllo di coerenza) e deve avere «elenco»
    sul bucket dei PdA, limitato a registro/; chi non elenca non lo ha."""
    assert vm.errori_elenco_s3(vm.topologia.servizi_attivi(BASE)) == []
    assert any("coerenza.py" in f for f in vm.file_che_elencano("hashchain"))
    assert BASE["services"]["hashchain"]["iam"]["elenco_prefissi"] == {"aip": ["registro/"]}
    v = _valori()
    v["services"]["hashchain"]["iam"]["s3"]["aip"] = ["lettura", "scrittura", "worm"]
    errori = vm.errori_elenco_s3(vm.topologia.servizi_attivi(v))
    assert any("hashchain: elenca un bucket S3" in e for e in errori)
    assert any("elenco_prefissi.aip senza il permesso" in e for e in errori)
    v = _valori()
    v["services"]["dashboard-api"]["iam"]["s3"]["aip"] = ["lettura", "elenco"]
    v["services"]["hashchain"]["iam"]["elenco_prefissi"]["aip"] = ["registro/*"]
    errori = vm.errori_elenco_s3(vm.topologia.servizi_attivi(v))
    assert any("dashboard-api: permesso «elenco»" in e for e in errori)
    assert any("prefisso 'registro/*' non valido" in e for e in errori)


def test_r05_revisione_scarto_solo_lavoratore_e_prefissi() -> None:
    """Revisione R05 (R3): la cancellazione nel WORM (permesso «scarto») ce l'ha
    SOLO il lavoratore di ciclo-vita, con ServiceAccount proprio e limitata alle
    chiavi dei PdA; l'API esposta scrive solo sotto pratiche/, senza legal hold;
    registro/ negato a ciclo-vita e aip-builder."""
    servizi = vm.topologia.servizi_attivi(BASE)
    voci = {n: iam for n, iam, irsa in vm.topologia.account_di_servizio(BASE) if irsa}
    con_scarto = {n for n, iam in voci.items()
                  if any("scarto" in (p or []) for p in (iam.get("s3") or {}).values())}
    assert con_scarto == {"ciclo-vita-lavoratore"}
    api = servizi["ciclo-vita"]["iam"]
    assert api["s3"]["aip"] == ["lettura", "scrittura", "retention"]
    assert "worm" not in api["s3"]["aip"]  # niente s3:PutObjectLegalHold
    assert api["prefissi_oggetti"]["aip"] == {"lettura": ["pratiche/"], "scrittura": ["pratiche/"],
                                              "retention": ["pratiche/"]}
    lavoratore = voci["ciclo-vita-lavoratore"]
    assert lavoratore["prefissi_oggetti"]["aip"]["scarto"] == [
        "????????-????-????-????-????????????/"]
    for nome in ("ciclo-vita", "ciclo-vita-lavoratore", "aip-builder"):
        assert "registro/" in voci[nome]["nega_prefissi"]["aip"], nome
    assert vm.errori_iam(servizi) == []
    # ServiceAccount del lavoratore nel manifest generato
    lav = yaml.safe_load((Path(vm.__file__).parent / "base" / "deployments" /
                          "ciclo-vita-lavoratore-deployment.yaml").read_text(encoding="utf-8"))
    assert lav["spec"]["template"]["spec"]["serviceAccountName"] == "ciclo-vita-lavoratore"


def test_r05_revisione_errori_dei_prefissi_iam() -> None:
    v = _valori()
    iam = v["services"]["ciclo-vita"]["iam"]
    iam["s3"]["aip"].append("scarto")                       # scarto sull'API esposta
    iam["prefissi_oggetti"]["aip"]["multipart"] = ["pratiche/"]  # permesso assente
    iam["prefissi_oggetti"]["aip"]["lettura"] = ["pratiche/*"]   # jolly
    iam["nega_prefissi"]["aip"] = ["registro/*"]
    errori = vm.errori_iam(vm.topologia.servizi_attivi(v))
    assert any("«scarto» (cancellazione nel WORM) su un servizio esposto" in e for e in errori)
    assert any("«scarto» su aip senza prefissi_oggetti" in e for e in errori)
    assert any("prefissi_oggetti.aip.multipart senza il permesso" in e for e in errori)
    assert any("prefisso 'pratiche/*' non valido" in e for e in errori)
    assert any("nega_prefissi.aip: prefisso 'registro/*' non valido" in e for e in errori)


def _deployment(nome: str) -> dict:
    return yaml.safe_load((Path(vm.__file__).parent / "base" / "deployments" /
                           f"{nome}-deployment.yaml").read_text(encoding="utf-8"))


def _secret_ref(dep: dict) -> list[str]:
    c = dep["spec"]["template"]["spec"]["containers"][0]
    return [e["secretRef"]["name"] for e in c.get("envFrom") or [] if "secretRef" in e]


def test_r05_revisione2_credenziali_di_ciclo_vita_solo_al_lavoratore() -> None:
    """Revisione 2 di R05 (S2): il pod API di ciclo-vita (esposto) NON riceve il
    token del registro, la chiave dei token di canale né il token di
    distribution: stanno nel Secret proprio del lavoratore."""
    servizi = vm.topologia.servizi_attivi(BASE)
    cv = servizi["ciclo-vita"]
    for chiave in vm.CREDENZIALI_SOLO_LAVORATORE["ciclo-vita"]:
        assert chiave not in cv["secretKeys"]
        assert chiave in cv["workers"]["lavoratore"]["secretKeys"]
    assert _secret_ref(_deployment("ciclo-vita")) == ["ciclo-vita-secrets"]
    assert _secret_ref(_deployment("ciclo-vita-lavoratore")) == [
        "ciclo-vita-secrets", "ciclo-vita-lavoratore-secrets"]
    modello = yaml.safe_load((Path(vm.__file__).parent / "secrets" /
                              "ciclo-vita-lavoratore-secrets.template.yaml").read_text(
                                  encoding="utf-8"))
    # + DATABASE_URL del login proprio del lavoratore (revisione 3 di R05, T5)
    assert sorted(modello["stringData"]) == sorted(
        [*vm.CREDENZIALI_SOLO_LAVORATORE["ciclo-vita"], "DATABASE_URL"])
    servizio = yaml.safe_load((Path(vm.__file__).parent / "secrets" /
                               "ciclo-vita-secrets.template.yaml").read_text(encoding="utf-8"))
    assert not set(servizio["stringData"]) & set(vm.CREDENZIALI_SOLO_LAVORATORE["ciclo-vita"])
    assert vm.errori_credenziali_lavoratore(servizi) == []


def test_r05_revisione2_credenziale_nel_servizio_rifiutata() -> None:
    v = _valori()
    cv = v["services"]["ciclo-vita"]
    cv["secretKeys"].append("HASHCHAIN_TOKEN_REGISTRO")
    cv["workers"]["lavoratore"]["secretKeys"].remove("DISTRIBUTION_TOKEN_SERVIZIO")
    errori = vm.errori_credenziali_lavoratore(vm.topologia.servizi_attivi(v))
    assert any("HASHCHAIN_TOKEN_REGISTRO nel Secret del servizio" in e for e in errori)
    assert any("DISTRIBUTION_TOKEN_SERVIZIO non è nel Secret di nessun lavoratore" in e
               for e in errori)
    # chiave del lavoratore non letta dalle Settings
    v = _valori()
    v["services"]["ciclo-vita"]["workers"]["lavoratore"]["secretKeys"].append("NON_LETTA")
    assert any("secretKeys NON_LETTA non letta" in e for e in vm.verifica(v))


def test_r05_revisione3_login_db_del_lavoratore_distinto_da_quello_dell_api() -> None:
    """Revisione 3 di R05 (T5): il lavoratore di ciclo-vita entra nel database con
    un login PROPRIO (DATABASE_URL del Secret ciclo-vita-lavoratore-secrets,
    membro di verumdoc_ciclo_vita_lavoratore), preso esplicitamente da lì e non
    dall'envFrom del Secret del servizio; l'API ha solo il suo (ciclo-vita-secrets)."""
    servizi = vm.topologia.servizi_attivi(BASE)
    assert vm.errori_login_db_lavoratore(servizi) == []
    contenitore = _deployment("ciclo-vita-lavoratore")["spec"]["template"]["spec"][
        "containers"][0]
    assert {"name": "DATABASE_URL", "valueFrom": {"secretKeyRef": {
        "name": "ciclo-vita-lavoratore-secrets", "key": "DATABASE_URL"}}} in contenitore["env"]
    api = _deployment("ciclo-vita")["spec"]["template"]["spec"]["containers"][0]
    assert not any(e["name"] == "DATABASE_URL" for e in api.get("env") or [])
    assert "ciclo-vita-lavoratore-secrets" not in str(api)
    # senza l'envSegreti: due envFrom con DATABASE_URL, vale l'ordine → rifiutato
    v = _valori()
    del v["services"]["ciclo-vita"]["workers"]["lavoratore"]["envSegreti"]
    errori = vm.verifica(v)
    assert any("envSegreti.DATABASE_URL deve venire da ciclo-vita-lavoratore-secrets" in e
               for e in errori)
    assert any("DATABASE_URL sia nel Secret del servizio sia in quello del lavoratore" in e
               for e in errori)
    # senza DATABASE_URL nel Secret del lavoratore: login dell'API anche al lavoratore
    v = _valori()
    lav = v["services"]["ciclo-vita"]["workers"]["lavoratore"]
    lav["secretKeys"].remove("DATABASE_URL")
    del lav["envSegreti"]
    assert any("DATABASE_URL non è nel Secret del lavoratore" in e
               for e in vm.errori_login_db_lavoratore(vm.topologia.servizi_attivi(v)))


@pytest.mark.parametrize("percorso", ["/interno", "/interno/v1", "/"])
def test_r05_revisione2_rotta_verso_le_api_interne_rifiutata(percorso) -> None:
    """S2: nessuna rotta dell'ingress verso un servizio raggiunge ``/interno/``."""
    v = _valori()
    v["ingress"]["routes"].insert(0, {"path": percorso, "service": "hashchain", "maxBodyMB": 1})
    assert any("raggiunge le API interne" in e for e in vm.verifica(v))
    assert vm.errori_rotte_interne(BASE["ingress"]["routes"]) == []


def test_r05_revisione2_hashchain_scrive_solo_registro_e_rdv() -> None:
    """S4: la hashchain scrive solo sotto registro/ e sui RdV, legal hold solo sui RdV.
    R13a: in più le buste sigillate del PdA (Sigillo_*: IPdA, RdV, rimarcature),
    con la stessa retention e lo stesso legal hold del PdA."""
    iam = vm.topologia.servizi_attivi(BASE)["hashchain"]["iam"]
    pda = "????????-????-????-????-????????????/????????-????-????-????-????????????/"
    rdv, sigillo = pda + "RdV_Definitiv", pda + "Sigillo_"
    assert iam["prefissi_oggetti"]["aip"] == {"scrittura": ["registro/", rdv, sigillo],
                                              "retention": ["registro/", rdv, sigillo],
                                              "worm": [rdv, sigillo]}
    v = _valori()
    v["services"]["hashchain"]["iam"]["prefissi_oggetti"]["aip"]["worm"] = ["registro/*"]
    assert any("prefisso 'registro/*' non valido" in e
               for e in vm.errori_iam(vm.topologia.servizi_attivi(v)))


def test_r06_chiave_dei_token_sftp_solo_al_lavoratore() -> None:
    """R06 (VD-C-126): il server SFTP (esposto con l'NLB) non riceve
    SFTP_TOKEN_CHIAVE_PRIVATA, che sta nel Secret proprio del lavoratore; l'API
    di ciclo-vita non ha permessi sullo staging né S3_BUCKET_PDV nella ConfigMap."""
    servizi = vm.topologia.servizi_attivi(BASE)
    assert "SFTP_TOKEN_CHIAVE_PRIVATA" not in servizi["sftp-ingest"]["secretKeys"]
    assert _secret_ref(_deployment("sftp-ingest")) == ["sftp-ingest-secrets"]
    assert _secret_ref(_deployment("sftp-ingest-lavoratore")) == [
        "sftp-ingest-secrets", "sftp-ingest-lavoratore-secrets"]
    cartella = Path(vm.__file__).parent / "secrets"
    modello = yaml.safe_load((cartella / "sftp-ingest-lavoratore-secrets.template.yaml")
                             .read_text(encoding="utf-8"))
    assert sorted(modello["stringData"]) == ["SFTP_TOKEN_CHIAVE_PRIVATA"]
    servizio = yaml.safe_load((cartella / "sftp-ingest-secrets.template.yaml")
                              .read_text(encoding="utf-8"))
    assert "SFTP_TOKEN_CHIAVE_PRIVATA" not in servizio["stringData"]
    cv = servizi["ciclo-vita"]
    assert "staging" not in (cv.get("iam") or {}).get("s3", {})
    assert "S3_BUCKET_PDV" not in (cv.get("config") or {})
    assert "staging" in cv["workers"]["lavoratore"]["iam"]["s3"]
    assert "S3_BUCKET_PDV" in cv["workers"]["lavoratore"]["env"]


def test_r06_chiave_dei_token_sftp_nel_servizio_rifiutata() -> None:
    v = _valori()
    sftp = v["services"]["sftp-ingest"]
    sftp["secretKeys"].append("SFTP_TOKEN_CHIAVE_PRIVATA")
    errori = vm.errori_credenziali_lavoratore(vm.topologia.servizi_attivi(v))
    assert any("sftp-ingest: SFTP_TOKEN_CHIAVE_PRIVATA nel Secret del servizio" in e
               for e in errori)
    v = _valori()
    v["services"]["sftp-ingest"]["workers"]["lavoratore"]["secretKeys"].remove(
        "SFTP_TOKEN_CHIAVE_PRIVATA")
    assert any("sftp-ingest: le Settings leggono SFTP_TOKEN_CHIAVE_PRIVATA" in e
               for e in vm.verifica_condivisi(v))


def test_r06_cronjob_giornaliero_verifica_audit() -> None:
    """R06 (VD-C-130): CronJob giornaliero della hashchain che esegue
    verifica-audit (codice d'uscita ≠ 0 = Job fallito = allarme), senza
    esecuzioni sovrapposte; R09 (D27): ServiceAccount proprio e modo automatico."""
    cron = yaml.safe_load((Path(vm.__file__).parent / "base" / "cronjobs" /
                           "hashchain-verifica-audit-cronjob.yaml").read_text(encoding="utf-8"))
    spec = cron["spec"]
    minuto, ora, giorno, mese, settimana = spec["schedule"].split()
    assert (giorno, mese, settimana) == ("*", "*", "*") and minuto.isdigit() and ora.isdigit()
    assert spec["concurrencyPolicy"] == "Forbid"
    pod = spec["jobTemplate"]["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["serviceAccountName"] == "hashchain-verifica-audit"
    contenitore = pod["containers"][0]
    assert contenitore["command"] == ["python", "-m", "app.registro.operatore", "verifica-audit",
                                      "--automatica"]
    assert vm.verifica(_valori()) == []


def test_r06_revisione_cronjob_verifica_audit_minimo() -> None:
    """Revisione R06 (V3): nessun tentativo ripetuto (l'esito DIFFERENZE non è un
    errore transitorio e un tentativo fallito non deve restare come Job fallito
    dopo un successo), nessun Secret della hashchain (chiave privata della
    catena, token verso firma e testimone), nessun token del ServiceAccount."""
    cron = yaml.safe_load((Path(vm.__file__).parent / "base" / "cronjobs" /
                           "hashchain-verifica-audit-cronjob.yaml").read_text(encoding="utf-8"))
    job = cron["spec"]["jobTemplate"]["spec"]
    assert job["backoffLimit"] == 0
    pod = job["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    contenitore = pod["containers"][0]
    riferimenti = [e.get("secretRef", {}).get("name") for e in contenitore["envFrom"]]
    assert "hashchain-secrets" not in riferimenti
    assert "hashchain-verifica-audit-secrets" in riferimenti
    modello = yaml.safe_load((Path(vm.__file__).parent / "secrets" /
                              "hashchain-verifica-audit-secrets.template.yaml"
                              ).read_text(encoding="utf-8"))
    # R09 (D27, VD-C-142): login PROPRIO in sola lettura (verumdoc_auditor) nel Secret
    # del job e ServiceAccount (ruolo IRSA) proprio: lettura ed elenco del solo registro/
    assert set(modello["stringData"]) == {"S3_ACCESS_KEY", "S3_SECRET_KEY", "DATABASE_URL"}
    variabili = {e["name"]: e for e in contenitore["env"]}
    assert variabili["DATABASE_URL"]["valueFrom"]["secretKeyRef"]["name"] == \
        "hashchain-verifica-audit-secrets"
    assert pod["serviceAccountName"] == "hashchain-verifica-audit"
    assert contenitore["command"][-1] == "--automatica"
    import topologia

    valori = yaml.safe_load((Path(vm.__file__).parents[1] / "helm" / "values.yaml")
                            .read_text(encoding="utf-8"))
    iam = {n: i for n, i, _ in topologia.account_di_servizio(valori)}["hashchain-verifica-audit"]
    assert iam["s3"] == {"aip": ["lettura", "elenco"]}
    assert iam["elenco_prefissi"] == {"aip": ["registro/"]}
    assert iam["prefissi_oggetti"] == {"aip": {"lettura": ["registro/"]}}
    for vietata in ("CATENA_CHIAVE_PRIVATA", "SIGNATURE_SERVICE_TOKEN",
                    "TESTIMONE_SERVICE_TOKEN", "JWT_PUBLIC_KEY"):
        assert vietata not in variabili


def test_r08_revisione_firme_allineate_e_ripiego_con_radici() -> None:
    """Revisione R08 (X7, X8): limite dei chiamanti ≤ limite di verifica-firme;
    nessun ripiego pyHanko senza trust anchor."""
    v = _valori()
    v["services"]["validation"]["config"]["FIRME_DIMENSIONE_MAX_MB"] = "512"
    v["services"]["ciclo-vita"]["config"]["FIRME_RIPIEGO_PDF"] = "true"
    errori = vm.verifica(v)
    assert any("validation: FIRME_DIMENSIONE_MAX_MB=512 oltre" in e for e in errori)
    assert any("ciclo-vita: FIRME_RIPIEGO_PDF senza FIRME_RADICI_PEM" in e for e in errori)


@pytest.mark.parametrize("modifica,atteso", [
    # R10 (revisione V4): sorgenti fidate del PROXY protocol = subnet dell'NLB, mai la VPC
    (lambda v: v["services"]["sftp-ingest"]["config"].update(
        SFTP_PROXY_SORGENTI_FIDATE="10.0.0.0/16"), "SFTP_PROXY_SORGENTI_FIDATE"),
    # R10 (revisione V1): proxy fidati del gateway = subnet dell'ALB
    (lambda v: v["services"]["gateway"]["config"].update(PROXY_FIDATI='["10.0.0.0/16"]'),
     "PROXY_FIDATI"),
])
def test_r10_reti_fidate_uguali_alle_subnet_dei_bilanciatori(modifica, atteso) -> None:
    v = _valori()
    assert not [e for e in vm.verifica(v) if atteso in e]
    modifica(v)
    assert any(atteso in e for e in vm.verifica(v)), vm.verifica(v)


# ── R12b (VD-C-161): invio della posta nel solo lavoratore notification-posta ──

def test_r12b_chiave_della_posta_solo_al_lavoratore_della_posta() -> None:
    """Il pod esposto di notification (API eventi, webhook, campanella) e il lavoratore
    dei webhook NON ricevono la chiave privata della posta né le credenziali SMTP:
    stanno nel Secret proprio di notification-posta, senza HTTP, senza chiamanti,
    con la sola uscita SMTP; il servizio ha POSTA_INVIO_ATTIVO=false."""
    servizi = vm.topologia.servizi_attivi(BASE)
    n = servizi["notification"]
    posta = n["workers"]["posta"]
    for chiave in vm.CREDENZIALI_SOLO_LAVORATORE["notification"]:
        assert chiave not in n["secretKeys"]
        assert chiave in posta["secretKeys"]
    assert n["config"]["POSTA_INVIO_ATTIVO"] == "false"
    assert posta["env"]["POSTA_INVIO_ATTIVO"] == "true"
    assert posta["http"] is False and posta["command"][-1] == "app.lavoratore_posta"
    # R13b: 993 per la lettura IMAP delle ricevute PEC
    assert sorted(posta["egress"]["internetPorts"]) == [465, 587, 993]
    assert n["egress"]["internetPorts"] == []
    assert 587 not in n["workers"]["webhook"]["egress"]["internetPorts"]
    assert _secret_ref(_deployment("notification")) == ["notification-secrets"]
    assert _secret_ref(_deployment("notification-webhook")) == ["notification-secrets"]
    # revisione R12b: solo il proprio Secret (niente chiave dei webhook, pepper, Redis)
    assert _secret_ref(_deployment("notification-posta")) == ["notification-posta-secrets"]
    env = {e["name"]: e for e in _deployment("notification-posta")["spec"]["template"]["spec"][
        "containers"][0]["env"]}
    assert env["DATABASE_URL"]["valueFrom"]["secretKeyRef"] == {
        "name": "notification-posta-secrets", "key": "DATABASE_URL"}
    cartella = Path(vm.__file__).parent / "secrets"
    modello = yaml.safe_load((cartella / "notification-posta-secrets.template.yaml")
                             .read_text(encoding="utf-8"))
    assert sorted(modello["stringData"]) == sorted(
        [*vm.CREDENZIALI_SOLO_LAVORATORE["notification"], "DATABASE_URL"])
    servizio = yaml.safe_load((cartella / "notification-secrets.template.yaml")
                              .read_text(encoding="utf-8"))
    assert not set(servizio["stringData"]) & set(vm.CREDENZIALI_SOLO_LAVORATORE["notification"])
    politica = yaml.safe_load((Path(vm.__file__).parent / "base" / "network-policies" /
                               "allow-notification-posta.yaml").read_text(encoding="utf-8"))
    assert politica["spec"]["ingress"] == []  # nessuno lo chiama, nessuna rotta
    assert vm.errori_credenziali_lavoratore(servizi) == []


def test_r12b_chiave_della_posta_nel_servizio_rifiutata() -> None:
    v = _valori()
    v["services"]["notification"]["secretKeys"].append("POSTA_CHIAVE_PRIVATA")
    errori = vm.errori_credenziali_lavoratore(vm.topologia.servizi_attivi(v))
    assert any("notification: POSTA_CHIAVE_PRIVATA nel Secret del servizio" in e
               for e in errori)
    v = _valori()
    v["services"]["notification"]["config"]["POSTA_INVIO_ATTIVO"] = "true"
    assert any("POSTA_INVIO_ATTIVO=\"false\"" in e for e in vm.verifica(v))
    v = _valori()
    v["services"]["notification"]["workers"]["posta"]["enabled"] = False
    errori = vm.errori_credenziali_lavoratore(vm.topologia.servizi_attivi(v))
    assert any("POSTA_CHIAVE_PRIVATA non è nel Secret di nessun lavoratore" in e
               for e in errori)



def test_r12b_revisione_lavoratore_della_posta_senza_il_secret_del_servizio() -> None:
    assert vm.errori_segreti_propri(vm.topologia.servizi_attivi(BASE)) == []
    v = _valori()
    del v["services"]["notification"]["workers"]["posta"]["segretiServizio"]
    errori = vm.errori_segreti_propri(vm.topologia.servizi_attivi(v))
    assert any("segretiServizio: false" in e for e in errori)
    v = _valori()
    del v["services"]["notification"]["workers"]["posta"]["envSegreti"]
    assert any("envSegreti.DATABASE_URL" in e
               for e in vm.errori_segreti_propri(vm.topologia.servizi_attivi(v)))
