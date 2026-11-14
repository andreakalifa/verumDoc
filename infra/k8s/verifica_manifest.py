# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Verifica la coerenza fra infra/helm/values.yaml e il codice dei servizi (VD-C-038).

Controlli:
  1. probe: i percorsi health.live / health.ready esistono come rotte GET nel
     servizio (app/**/*.py) o, per il frontend, in frontend/nginx.conf;
  2. ConfigMap e Secret: ogni chiave corrisponde a un campo delle Settings del
     servizio (app/config.py, più libs/shared/db/config.py per chi usa il DB) e
     nessuna chiave sta in entrambi;
  3. Ingress: ogni rotta punta a un servizio esistente ed «exposed», e ogni
     servizio «exposed» ha almeno una rotta;
  4. rete: chiamanti e destinazioni esistono e sono simmetrici (se A chiama B,
     B elenca A fra i callers) e ogni URL interno http://<servizio>:<porta> nella
     ConfigMap ha la porta giusta e il permesso di uscita verso quel servizio;
     in più (g3-backend) un servizio ammette in ingresso SOLO chi lo chiama
     davvero: ogni caller deve avere nella ConfigMap un URL verso di lui;
  5. valori condivisi (g3-backend), su values.yaml e su ogni overlay fuso:
     - (fino a R08 RETENTION_PERMANENTE_ORIZZONTE_ANNI: da R09 è il parametro
       RETENTION_PERMANENTE_ANNI del motore, una sola fonte per costruzione);
     - JWT_ISSUER/JWT_AUDIENCE uguali in tutti i servizi che li dichiarano;
     - JWT_PUBLIC_KEY e API_KEY_PEPPER (e PDND_PUBLIC_KEY) fra i secretKeys di
       ogni servizio le cui Settings li leggono (senza, quei servizi non partono
       fuori dai test o, per PDND, rispondono 503); da R03 anche
       WEBHOOK_CHIAVE_CIFRATURA, SFTP_CHIAVE_HOST e le chiavi dei token di canale;
     - SFTP_RSA_BIT_MINIMI uguale in onboarding e sftp-ingest; dati del
       conservatore uguali (se presenti) in distribution e dashboard-api.
  6. R03 (i2): lavoratori e job (services.<s>.workers / cronJobs, vedi
     topologia.py): variabili «env» lette dalle Settings del servizio,
     «disattivaNelPadre» = "false" nella ConfigMap del servizio; rotte
     dell'ingress a prefisso più lungo per primo; limiti del corpo (maxBodyMB)
     coerenti con le impostazioni applicative (LIMITI_CORPO) e non più alti di
     quelli dei prefissi che li contengono; bilanciatore TCP (publicTcp) sulla
     porta delle Settings; volumi condivisi fra più repliche in ReadWriteMany;
     uscita su Internet senza reti interne (network.internetExcept); nessun
     campo SecretStr delle Settings nella ConfigMap.
  7. R03 f3 (REV-N2): disco temporaneo (DISCO_TEMPORANEO): /tmp del servizio
     (tmpSizeLimit, emptyDir) almeno pari al caso peggiore delle richieste che vi
     scrivono — il minore fra il budget di byte della replica e la somma
     concorrenza × dimensione massima di ogni canale — più MARGINE_TMP; il
     budget non è più piccolo della richiesta più grande ammessa (altrimenti
     quella richiesta sarebbe sempre rifiutata).
  8. R04 (infra):
     - IAM (VD-C-101): ogni ServiceAccount annotato (irsa diverso da false) ha
       il blocco «iam» da cui Terraform (modulo iam_servizi) crea il ruolo con
       lo stesso nome dell'annotazione; permessi e bucket fra quelli ammessi;
       chi nomina un bucket nella ConfigMap (S3_BUCKET_*) ha i permessi su quel
       bucket, chi usa Kafka ha i permessi Kafka; nessun ruolo per gli account
       separati (testimone);
     - KEDA: gruppo e topic dello ScaledObject uguali a quelli delle Settings
       (o della ConfigMap), topic esistente in infra/kafka/topics.yaml, repliche
       massime non oltre le partizioni; query PostgreSQL di sola lettura e
       limitate (LIMIT: mai un COUNT(*) su una tabella che cresce);
     - quota del namespace: somma dei limiti al massimo delle repliche (servizi,
       lavoratori, job, PgBouncer) sotto la ResourceQuota;
     - connessioni a PostgreSQL: PgBouncer (repliche × max_db_connections) più
       i client diretti (repliche massime × pool per processo) più le riservate
       sotto postgres.maxConnessioni; PgBouncer in modalità transaction con i
       prepared statement di protocollo (asyncpg);
     - entrypointEnv: variabili davvero lette dall'entrypoint del servizio.
  Le Settings considerate sono app/config.py, libs/shared/db/config.py per chi
  usa il DB e ogni altra classe BaseSettings del servizio o delle libs/shared
  che importa, con il suo env_prefix.
Esce con codice 1 al primo gruppo di errori, elencandoli tutti.

Uso:  python infra/k8s/verifica_manifest.py
"""

from __future__ import annotations

import ast
import copy
import ipaddress
import math
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import topologia  # noqa: E402

RADICE = Path(__file__).resolve().parents[2]
VALUES = RADICE / "infra" / "helm" / "values.yaml"
OVERLAY = ("staging", "prod")
#: chiavi che devono avere lo stesso valore in più servizi
UGUALI_FRA = {
    # Politica delle chiavi SSH: onboarding le accetta, sftp-ingest le ricontrolla
    "SFTP_RSA_BIT_MINIMI": ("onboarding", "sftp-ingest"),
    # Quote per piano (R03 i3): il limitatore dei servizi usa le stesse impostazioni
    # del gateway (libs.shared.security.quote), una sola fonte
    **dict.fromkeys(("QUOTE_RICHIESTE_MINUTO", "QUOTE_PIANO_PREDEFINITO", "QUOTE_FINESTRA_SECONDI",
                     "QUOTE_PIANO_CACHE_SECONDI"),
                    ("gateway", "distribution", "dashboard-api", "notification")),
}
#: Servizi con il limite di richieste al minuto (libs.shared.limiti_richieste, R03 f2):
#: in produzione non partono senza Redis → REDIS_URL fra i segreti e uscita verso redis.
MODULO_LIMITATORE = "libs.shared.limiti_richieste"
#: chiavi che, se presenti in più servizi, devono avere lo stesso valore (anche vuoto)
UGUALI_SE_PRESENTI = {
    "CONSERVATORE_DENOMINAZIONE": ("distribution", "dashboard-api"),
    "CONSERVATORE_CODICE_FISCALE": ("distribution", "dashboard-api"),
}
UGUALI_OVUNQUE = ("JWT_ISSUER", "JWT_AUDIENCE")
#: segreti obbligatori per chi li legge dalle Settings
SEGRETI_OBBLIGATORI = ("JWT_PUBLIC_KEY", "API_KEY_PEPPER", "WEBHOOK_CHIAVE_CIFRATURA",
                       "SFTP_CHIAVE_HOST", "SFTP_TOKEN_CHIAVE_PRIVATA",
                       "SFTP_TOKEN_CHIAVE_PUBBLICA")
#: Limiti del corpo all'ingress (R03, REGOLE §27.3): (servizio, percorso, impostazione
#: in MB della ConfigMap, descrizione, funzione → MB minimi del limite all'ingress).
#: Il margine di 1 MB copre indice, intestazioni multipart e campi JSON.
LIMITI_CORPO = (
    ("gateway", "/api/v1/versamento/ud", "VERSAMENTO_REST_MAX_FILE_MB", "multipart REST",
     lambda mb: mb + 1),
    # R03 f1 (REV-09): limite applicativo dell'intero corpo multipart
    ("gateway", "/api/v1/versamento/ud", "VERSAMENTO_REST_MAX_CORPO_MB", "corpo multipart REST",
     lambda mb: mb),
    (
        "gateway", "/api/v1/versamento/ud", "VERSAMENTO_JSON_MAX_MB",
        "versamento JSON per riferimento",
     lambda mb: mb),
    ("gateway", "/api/v1/caricamenti/x/parti/1", "CARICAMENTI_DIMENSIONE_PARTE_MB",
     "parte di una sessione di caricamento", lambda mb: mb + 1),
    ("gateway", "/api/pdnd/v1/versamento", "PDND_MAX_FILE_MB", "file base64 PDND",
     lambda mb: math.ceil(mb * 4 / 3) + 1),
)
#: Disco temporaneo (R03 f3, REV-N2): servizio → impostazione del budget in MB e,
#: per ogni canale che scrive su /tmp, (richieste contemporanee, MB per richiesta).
DISCO_TEMPORANEO = {
    "gateway": {
        "budget": "DISCO_TEMPORANEO_BUDGET_MB",
        "canali": (
            # multipart REST: Starlette scrive su /tmp le parti oltre 1 MB
            ("VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX", "VERSAMENTO_REST_MAX_CORPO_MB"),
            # PDND: file decodificato in un file temporaneo
            ("PDND_RICHIESTE_CONTEMPORANEE_MAX", "PDND_MAX_FILE_MB"),
        ),
    },
}
#: Margine di /tmp oltre il caso peggiore (librerie, file temporanei d'altro tipo)
MARGINE_TMP = 0.10
_UNITA_MIB = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024,
              "k": 1000 / 1024 ** 2, "M": 1000 ** 2 / 1024 ** 2, "G": 1000 ** 3 / 1024 ** 2}
#: Reti che l'uscita verso Internet deve sempre escludere
RETI_INTERNE = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
#: (servizio che mostra la porta, impostazione) → servizio con publicTcp
PORTE_PUBBLICHE = {("onboarding", "SFTP_PORTA_PUBBLICA"): "sftp-ingest"}
#: (servizio, volume) → impostazione con il percorso di montaggio
VOLUMI_SETTINGS = {("sftp-ingest", "aree"): "SFTP_RADICE"}
#: R04 (VD-C-101): permessi del blocco «iam» (stessi del modulo Terraform iam_servizi)
PERMESSI_S3 = frozenset({"lettura", "scrittura", "eliminazione", "elenco", "multipart", "worm",
                         "lettura_retention", "retention", "scarto"})
PERMESSI_KAFKA = frozenset({"produzione", "consumo"})
#: bucket logici creati da Terraform (chiavi di bucket_arn negli ambienti)
BUCKET_TERRAFORM = ("staging", "aip", "pdd")
#: impostazione della ConfigMap con il nome di un bucket → bucket logico
BUCKET_DA_IMPOSTAZIONE = {
    "S3_BUCKET_PDV": "staging", "S3_BUCKET_AIP": "aip", "S3_BUCKET_PDD": "pdd"}
#: ResourceQuota scritta a mano del namespace principale
QUOTA_NAMESPACE = RADICE / "infra" / "k8s" / "base" / "namespace.yaml"
TOPICS = RADICE / "infra" / "kafka" / "topics.yaml"
#: rilasci verificati oltre a values.yaml: ambienti e account del testimone
RILASCI = (("staging",), ("prod",), ("testimone",))


def campi_settings(file: Path) -> set[str]:
    albero = ast.parse(file.read_text(encoding="utf-8"))
    campi: set[str] = set()
    for classe in (n for n in albero.body if isinstance(n, ast.ClassDef)):
        for n in classe.body:
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                campi.add(n.target.id.upper())
    return campi


def campi_segreti(cartella: Path) -> set[str]:
    """Campi ``SecretStr`` delle classi Settings del servizio (con env_prefix)."""
    segreti: set[str] = set()
    for f in cartella.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        albero = ast.parse(f.read_text(encoding="utf-8"))
        for classe in (n for n in albero.body if isinstance(n, ast.ClassDef)):
            if not any(getattr(b, "id", "") == "BaseSettings" for b in classe.bases):
                continue
            prefisso = _prefisso_env(classe)
            for n in classe.body:
                if (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                        and "SecretStr" in ast.unparse(n.annotation)):
                    segreti.add(prefisso + n.target.id.upper())
    return segreti


def rotte_get(cartella: Path) -> set[str]:
    """Percorsi completi delle rotte GET (prefisso APIRouter + percorso)."""
    rotte: set[str] = set()
    for file in cartella.rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        albero = ast.parse(file.read_text(encoding="utf-8"))
        prefissi = {}
        for n in ast.walk(albero):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
                if getattr(n.value.func, "id", "") == "APIRouter":
                    for kw in n.value.keywords:
                        if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                            for t in n.targets:
                                if isinstance(t, ast.Name):
                                    prefissi[t.id] = kw.value.value
        for n in ast.walk(albero):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in n.decorator_list:
                    if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                            and d.func.attr == "get" and d.args
                            and isinstance(d.args[0], ast.Constant)):
                        oggetto = getattr(d.func.value, "id", "")
                        rotte.add(prefissi.get(oggetto, "") + d.args[0].value)
    return rotte


def _prefisso_env(classe: ast.ClassDef) -> str:
    """env_prefix di ``model_config = SettingsConfigDict(env_prefix=...)`` ("" se assente)."""
    for n in classe.body:
        if (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "model_config"
                                              for t in n.targets)
                and isinstance(n.value, ast.Call)):
            for kw in n.value.keywords:
                if kw.arg == "env_prefix" and isinstance(kw.value, ast.Constant):
                    return str(kw.value.value).upper()
    return ""


def campi_altre_settings(cartella: Path) -> set[str]:
    """Variabili lette da classi BaseSettings fuori da app/config.py (g3-backend):
    nel servizio (es. PdndSettings con prefisso PDND_, DatiConservatore con
    CONSERVATORE_) e nei moduli di libs/shared che importa, anche indirettamente
    (es. ConfigRelay dell'outbox, prefisso OUTBOX_; ImpostazioniQuote, prefisso
    QUOTE_, tramite libs.shared.limiti_richieste). Prima queste variabili non si potevano
    mettere nei values (il controllo le rifiutava) e restavano non configurate."""
    file = [f for f in cartella.rglob("*.py") if "__pycache__" not in f.parts
            and f.name != "config.py"]
    # R03 i3: anche le librerie importate dalle librerie (es. limiti_richieste →
    # security.quote, impostazioni QUOTE_*), finché ce ne sono di nuove.
    librerie: set[Path] = set()
    da_leggere = list(file)
    while da_leggere:
        f = da_leggere.pop()
        for modulo in re.findall(r"^\s*from (libs\.shared[\w.]*) import", f.read_text(
            encoding="utf-8"),
                                 re.M):
            percorso = RADICE.joinpath(*modulo.split("."))
            for candidato in (percorso.with_suffix(".py"), percorso / "__init__.py"):
                if candidato.is_file() and candidato.parent.name != "db" \
                        and candidato not in librerie:
                    librerie.add(candidato)
                    da_leggere.append(candidato)
    campi: set[str] = set()
    for f in [*file, *sorted(librerie)]:
        albero = ast.parse(f.read_text(encoding="utf-8"))
        for classe in (n for n in albero.body if isinstance(n, ast.ClassDef)):
            if not any(getattr(b, "id", "") == "BaseSettings" for b in classe.bases):
                continue
            prefisso = _prefisso_env(classe)
            for n in classe.body:
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) \
                        and n.target.id != "model_config":
                    campi.add(prefisso + n.target.id.upper())
    return campi


def usa_db(cartella: Path) -> bool:
    return any("libs.shared.db" in f.read_text(encoding="utf-8") for f in cartella.rglob("*.py")
               if "__pycache__" not in f.parts)


def _campi_servizio(nome: str, campi_db: set[str]) -> set[str]:
    app = RADICE / "services" / nome / "app"
    campi = campi_settings(app / "config.py") | campi_altre_settings(app)
    if usa_db(app):
        campi |= campi_db
    return campi


def _mb(valore: object) -> int | None:
    try:
        return int(str(valore))
    except ValueError:
        return None


def mib(quantita: object) -> float | None:
    """Quantità Kubernetes (``2Gi``, ``1536Mi``, ``2G``…) in MiB; None se non valida."""
    testo = str(quantita).strip()
    for unita in sorted(_UNITA_MIB, key=len, reverse=True):
        if testo.endswith(unita):
            numero = testo[: -len(unita)]
            try:
                return float(numero) * _UNITA_MIB[unita]
            except ValueError:
                return None
    try:
        return float(testo) / 1024 ** 2
    except ValueError:
        return None


def errori_disco_temporaneo(servizi: dict) -> list[str]:
    """Disco temporaneo coerente con budget e concorrenza delle richieste (REV-N2)."""
    errori: list[str] = []
    for nome, regola in DISCO_TEMPORANEO.items():
        if nome not in servizi:
            continue
        config = servizi[nome].get("config") or {}
        dimensione = mib(servizi[nome].get("tmpSizeLimit") or topologia.TMP_PREDEFINITO)
        if dimensione is None:
            errori.append(f"{nome}: tmpSizeLimit {servizi[nome].get('tmpSizeLimit')!r} non è "
                          "una quantità valida")
            continue
        valori: dict[str, int] = {}
        for chiave in (regola["budget"], *(k for coppia in regola["canali"] for k in coppia)):
            valore = _mb(config.get(chiave))
            if valore is None:
                errori.append(f"{nome}: {chiave} assente o non intera nella ConfigMap: serve per "
                              "dimensionare /tmp (tmpSizeLimit)")
            else:
                valori[chiave] = valore
        if len(valori) < 1 + 2 * len(regola["canali"]):
            continue
        budget = valori[regola["budget"]]
        concorrenza = sum(valori[n] * valori[mb] for n, mb in regola["canali"])
        peggiore = min(budget, concorrenza)
        richiesto = math.ceil(peggiore * (1 + MARGINE_TMP))
        if dimensione < richiesto:
            dichiarata = servizi[nome].get("tmpSizeLimit") or topologia.TMP_PREDEFINITO
            errori.append(
                f"{nome}: tmpSizeLimit {dichiarata} ({dimensione:.0f} MiB) sotto {richiesto} MiB: "
                f"caso peggiore su /tmp "
                f"min({regola['budget']}={budget}, "
                + " + ".join(f"{n}×{mb}={valori[n] * valori[mb]}" for n, mb in regola["canali"])
                + f") più il {MARGINE_TMP:.0%} (kubelet sfratterebbe il pod)")
        for _, mb in regola["canali"]:
            if budget < valori[mb]:
                errori.append(f"{nome}: {regola['budget']}={budget} sotto {mb}={valori[mb]}: una "
                              "richiesta alla dimensione massima sarebbe sempre rifiutata")
    return errori


def errori_servizio_java(nome: str, s: dict) -> list[str]:
    """R08: servizio Java (Spring Boot). Le probe sono rotte dichiarate nei
    controller; ogni chiave di ConfigMap e Secret è letta da application.yml
    (``${CHIAVE``) o è una variabile standard della JVM."""
    errori: list[str] = []
    radice = RADICE / "services" / nome
    sorgenti = " ".join(p.read_text(encoding="utf-8") for p in radice.rglob("*.java"))
    for tipo, percorso in (s.get("health") or {}).items():
        if f'"{percorso}"' not in sorgenti:
            errori.append(f"{nome}: probe {tipo}={percorso} non è una rotta del servizio Java")
    yml = radice / "src" / "main" / "resources" / "application.yml"
    testo = yml.read_text(encoding="utf-8") if yml.is_file() else ""
    for k in sorted(set(s.get("config") or {}) | set(s.get("secretKeys") or [])):
        if "${" + k + ":" not in testo and "${" + k + "}" not in testo:
            errori.append(f"{nome}: variabile {k} non letta da {yml.relative_to(RADICE)}")
    return errori


def verifica(valori: dict) -> list[str]:
    errori: list[str] = []
    servizi = topologia.servizi_attivi(valori)
    campi_db = (campi_settings(RADICE / "libs" / "shared" / "db" / "config.py") | CAMPI_DB_AMBIENTE
                | CAMPI_KAFKA_CONNESSIONE)
    carichi = {c["nome"]: c for c in topologia.carichi(valori) + topologia.cronjob(valori)
               + topologia.jobs(valori)}
    campi_di: dict[str, set[str]] = {}

    for nome, s in servizi.items():
        # 1. probe
        if nome == "frontend":
            conf = (RADICE / "frontend" / "nginx.conf")
            testo = conf.read_text(encoding="utf-8") if conf.exists() else ""
            for p in set(s["health"].values()):
                if not re.search(rf"location\s*=\s*{re.escape(p)}\s*\{{", testo):
                    errori.append(f"{nome}: probe {p} non servita da frontend/nginx.conf")
            continue
        if (RADICE / "services" / nome / "pom.xml").is_file():
            errori += errori_servizio_java(nome, s)  # R08: verifica-firme (EU DSS)
            continue
        app = RADICE / "services" / nome / "app"
        if not app.is_dir():
            errori.append(f"{nome}: services/{nome}/app inesistente")
            continue
        rotte = rotte_get(app)
        for tipo, p in s["health"].items():
            if p not in rotte:
                errori.append(f"{nome}: probe {tipo}={p} non è una rotta GET del servizio")
        # 2. chiavi ConfigMap / Secret
        campi = campi_di[nome] = _campi_servizio(nome, campi_db)
        config, segreti = set(s.get("config") or {}), set(s.get("secretKeys") or [])
        for k in sorted((config | segreti) - campi):
            errori.append(f"{nome}: variabile {k} non letta dalle Settings ({app / 'config.py'})")
        for k in sorted(config & segreti):
            errori.append(f"{nome}: {k} sia nella ConfigMap sia nel Secret")
        for k in sorted(config & campi_segreti(app)):
            errori.append(
                f"{nome}: {k} è un SecretStr delle Settings: va nel Secret, non nella ConfigMap")

    # 6a. lavoratori e job: variabili proprie e interruttori nel servizio
    for c in carichi.values():
        if not c["lavoratore"]:
            continue
        campi = campi_di.get(c["servizio"], set())
        for k in sorted(set(c["env"]) - campi):
            errori.append(f"{c['nome']}: env {k} non letta dalle Settings di {c['servizio']}")
        for k in sorted(set(c["env"]) & set(c["secretKeys"])):
            errori.append(f"{c['nome']}: env {k} è un segreto: va nel Secret del servizio")
        # Revisione 2 di R05 (S2): Secret proprio del lavoratore
        for k in sorted(set(c.get("secretKeysPropri") or []) - campi):
            errori.append(f"{c['nome']}: secretKeys {k} non letta dalle Settings di "
                          f"{c['servizio']}")
        # Revisione 3 di R05 (T5): una chiave in entrambi i Secret è ammessa solo
        # se il lavoratore la prende ESPLICITAMENTE dal proprio (envSegreti): con
        # due envFrom l'ordine deciderebbe quale valore vale
        propri = f"{c['nome']}-secrets"
        sovrascritte = {k for k, r in (c.get("envSegreti") or {}).items()
                        if r.get("secret") == propri and r.get("key") == k}
        for k in sorted((set(c.get("secretKeysPropri") or []) & set(c["secretKeys"]))
                        - sovrascritte):
            errori.append(f"{c['nome']}: {k} sia nel Secret del servizio sia in quello "
                          "del lavoratore")
        for k in sorted(set(c.get("secretKeysPropri") or []) & set(c["env"])):
            errori.append(f"{c['nome']}: env {k} è un segreto del lavoratore: va nel suo Secret")
        if c["http"] and not c["health"]:
            errori.append(f"{c['nome']}: http senza percorsi di salute nel servizio")
        if not c["http"] and not c["command"]:
            errori.append(f"{c['nome']}: senza server HTTP serve «command» (processo dedicato)")
        chiave = c.get("disattivaNelPadre")
        if chiave:
            valore = str((servizi[c["servizio"]].get("config") or {}).get(chiave, "")).lower()
            if valore != "false":
                errori.append(f"{c['nome']}: con il lavoratore dedicato {c['servizio']} deve avere "
                              f"{chiave}=\"false\" nella ConfigMap (oggi {valore or 'assente'}): "
                              "altrimenti il lavoro gira due volte")
            if c["env"].get(chiave) is not None and str(c["env"][chiave]).lower() != "true":
                errori.append(f"{c['nome']}: env {chiave} deve valere \"true\"")

    # 4. rete (servizi, lavoratori e job)
    for nome, c in carichi.items():
        eg = c["egress"]
        for dest in eg.get("services", []):
            if dest not in servizi:
                errori.append(f"{nome}: egress verso servizio inesistente {dest}")
            elif nome not in servizi[dest].get("callers", []):
                errori.append(f"{nome} → {dest}: manca {nome} fra i callers di {dest}")
        for chiamante in c["callers"]:
            if chiamante not in carichi:
                errori.append(f"{nome}: caller inesistente {chiamante}")
            elif nome not in carichi[chiamante]["egress"].get("services", []):
                errori.append(f"{chiamante} → {nome}: manca {nome} nell'egress di {chiamante}")
        # URL interni: la ConfigMap per i servizi, le variabili proprie per i lavoratori
        # (la ConfigMap condivisa non obbliga un lavoratore a chiamare tutti)
        proprie = c["env"] if c["lavoratore"] else {**c["config"], **c["env"]}
        for chiave, val in proprie.items():
            url = urlparse(str(val))
            if url.scheme == "http" and url.hostname in servizi:
                if url.port != servizi[url.hostname]["port"]:
                    errori.append(
                        f"{nome}: {chiave}={val} porta diversa da {servizi[url.hostname]['port']}")
                if url.hostname not in eg.get("services", []):
                    errori.append(
                        f"{nome}: {chiave} punta a {url.hostname} ma l'egress non lo consente")

        # 4b. ingresso solo da chi chiama davvero (URL nella sua configurazione)
        for chiamante in c["callers"]:
            if chiamante == nome or chiamante not in carichi:
                continue
            urls = [
                urlparse(str(v)) for v in topologia.config_effettiva(carichi[chiamante]).values()]
            if not any(u.scheme == "http" and u.hostname == nome for u in urls):
                errori.append(f"{nome}: {chiamante} è fra i callers ma non ha nella ConfigMap "
                              f"un URL http://{nome}:<porta> (ingresso non necessario)")

    # 6b. uscita su Internet: mai verso le reti interne
    net = valori["network"]
    escluse = [ipaddress.ip_network(x) for x in net.get("internetExcept") or []]
    for richiesta in (*RETI_INTERNE, net["vpcCidr"]):
        rete = ipaddress.ip_network(richiesta)
        if not any(rete.subnet_of(e) for e in escluse if e.version == rete.version):
            errori.append(f"network.internetExcept non esclude {richiesta}: l'uscita su Internet "
                          "raggiungerebbe reti interne")

    # 6c. bilanciatori TCP e volumi
    for nome, s in servizi.items():
        p = s.get("publicTcp")
        if p:
            impostazione = p.get("portSetting")
            valore = (s.get("config") or {}).get(impostazione) if impostazione else None
            if impostazione and str(valore) != str(p["targetPort"]):
                errori.append(f"{nome}: publicTcp.targetPort {p['targetPort']} diverso da "
                              f"{impostazione}={valore} della ConfigMap")
            if not net.get("nlbSourceCidrs"):
                errori.append(f"{nome}: publicTcp senza network.nlbSourceCidrs (NetworkPolicy)")
            # R10 (revisione V4): le sorgenti fidate del PROXY protocol sono ESATTAMENTE
            # le subnet del bilanciatore ammesse dalla NetworkPolicy (mai la VPC)
            fidate = str((s.get("config") or {}).get("SFTP_PROXY_SORGENTI_FIDATE") or "")
            elenco = sorted(x.strip() for x in fidate.split(",") if x.strip())
            if elenco and elenco != sorted(net.get("nlbSourceCidrs") or []):
                errori.append(f"{nome}: SFTP_PROXY_SORGENTI_FIDATE {elenco} diverse da "
                              f"network.nlbSourceCidrs {net.get('nlbSourceCidrs')}")
        # R10 (revisione V1): proxy fidati del gateway = subnet dell'ALB
        proxy = (s.get("config") or {}).get("PROXY_FIDATI")
        if proxy:
            import json as _json

            try:
                reti = sorted(_json.loads(proxy))
            except ValueError:
                reti = None
            if reti != sorted(net.get("albSourceCidrs") or []):
                errori.append(f"{nome}: PROXY_FIDATI {proxy} diverso da network.albSourceCidrs")
        vols = s.get("persistentVolumes") or []
        condiviso = (s.get("replicas", 1) > 1 or (s.get("autoscaling") or {}).get("enabled")
                     or topologia.lavoratori(nome, s))
        for v in vols:
            if condiviso and "ReadWriteMany" not in v.get("accessModes", []):
                errori.append(f"{nome}: volume {v['name']} montato da più pod ma non ReadWriteMany")
            if v.get("storageClassName") not in (valori.get("storageClasses") or {}):
                errori.append(
                    f"{nome}: volume {v['name']}: storageClass {v.get('storageClassName')} "
                              "non dichiarata in storageClasses")
            impostazione = VOLUMI_SETTINGS.get((nome, v["name"]))
            if impostazione and (s.get("config") or {}).get(impostazione) != v["mountPath"]:
                errori.append(f"{nome}: volume {v['name']} montato in {v['mountPath']} ma "
                              f"{impostazione}={(s.get('config') or {}).get(impostazione)}")
    for (mostra, impostazione), con_tcp in PORTE_PUBBLICHE.items():
        if mostra in servizi and con_tcp in servizi and servizi[con_tcp].get("publicTcp"):
            valore = (servizi[mostra].get("config") or {}).get(impostazione)
            if str(valore) != str(servizi[con_tcp]["publicTcp"]["port"]):
                errori.append(f"{mostra}: {impostazione}={valore} diverso dalla porta pubblica di "
                              f"{con_tcp} ({servizi[con_tcp]['publicTcp']['port']})")

    # 3. ingress
    rotte = valori["ingress"]["routes"]
    instradati = set()
    for r in rotte:
        dest = r["service"]
        instradati.add(dest)
        if dest not in servizi:
            errori.append(f"ingress {r['path']}: servizio inesistente {dest}")
        elif not servizi[dest].get("exposed"):
            errori.append(
                f"ingress {r['path']}: {dest} non è exposed (la NetworkPolicy blocca l'ALB)")
    for nome, s in servizi.items():
        if s.get("exposed") and nome not in instradati:
            errori.append(f"{nome}: exposed ma senza rotte nell'ingress")
    errori += topologia.errori_ordine_rotte(rotte)
    errori += topologia.errori_limiti_annidati(rotte)
    for servizio, percorso, impostazione, cosa, minimo in LIMITI_CORPO:
        if servizio not in servizi:
            continue
        valore = _mb((servizi[servizio].get("config") or {}).get(impostazione))
        if valore is None:
            errori.append(
                f"{servizio}: {impostazione} assente o non intera nella ConfigMap: serve per "
                          f"allineare il limite del corpo all'ingress ({cosa})")
            continue
        limite = topologia.limite_corpo_rotta(rotte, percorso)
        if limite is None or limite < minimo(valore):
            errori.append(f"ingress {percorso}: maxBodyMB {limite} sotto {minimo(valore)} MB "
                          f"richiesti da {impostazione}={valore} ({cosa})")
    errori += errori_disco_temporaneo(servizi)
    errori += errori_iam(servizi)
    errori += errori_keda(valori, list(carichi.values()), campi_di)
    errori += errori_entrypoint(servizi)
    errori += errori_quota(valori)
    errori += errori_connessioni(valori)
    errori += errori_formato_segreti(servizi)
    errori += errori_kafka_msk(servizi)
    errori += errori_elenco_s3(servizi)
    errori += errori_credenziali_lavoratore(servizi)
    errori += errori_segreti_propri(servizi)  # revisione R12b
    errori += errori_login_db_lavoratore(servizi)
    errori += errori_rotte_interne(rotte)
    errori += errori_firme(servizi)
    return errori


def errori_firme(servizi: dict) -> list[str]:
    """Revisione R08 (X7, X8): chi chiama verifica-firme non invia file più grandi
    di quanto il servizio accetta (FIRME_DIMENSIONE_MAX_MB ≤
    VERIFICA_DIMENSIONE_MASSIMA: altrimenti 413 = «firme non verificate» per file
    che il chiamante crede verificabili) e non attiva il ripiego pyHanko senza
    trust anchor (FIRME_RADICI_PEM)."""
    errori: list[str] = []
    servizio = servizi.get("verifica-firme")
    if not servizio:
        return errori
    massimo = int((servizio.get("config") or {}).get("VERIFICA_DIMENSIONE_MASSIMA", 62914560))
    for nome, s in servizi.items():
        config = s.get("config") or {}
        if str(config.get("FIRME_VERIFICATORE", "")).lower() != "dss":
            continue
        mb = int(config.get("FIRME_DIMENSIONE_MAX_MB", 60))
        if mb * 1024 * 1024 > massimo:
            errori.append(f"{nome}: FIRME_DIMENSIONE_MAX_MB={mb} oltre "
                          f"VERIFICA_DIMENSIONE_MASSIMA={massimo} di verifica-firme")
        ripiego = str(config.get("FIRME_RIPIEGO_PDF", "true")).lower() in ("1", "true", "si")
        radici = config.get("FIRME_RADICI_PEM") or "FIRME_RADICI_PEM" in (
            s.get("secretKeys") or [])
        if ripiego and not radici:
            errori.append(f"{nome}: FIRME_RIPIEGO_PDF senza FIRME_RADICI_PEM (il ripiego "
                          "pyHanko darebbe solo esiti indeterminati)")
    return errori


#: Revisione 2 di R05 (S2): credenziali verso altri servizi che SOLO il
#: lavoratore dedicato usa. Il servizio (esposto all'ingress) non le riceve:
#: stanno nel Secret proprio di un suo lavoratore (workers.<n>.secretKeys).
CREDENZIALI_SOLO_LAVORATORE: dict[str, tuple[str, ...]] = {
    "ciclo-vita": ("HASHCHAIN_TOKEN_REGISTRO", "CICLO_VITA_TOKEN_CHIAVE_PRIVATA",
                   "DISTRIBUTION_TOKEN_SERVIZIO"),
    # R06 (VD-C-126): il server SFTP (esposto con l'NLB) non firma token di canale
    "sftp-ingest": ("SFTP_TOKEN_CHIAVE_PRIVATA",),
    # R12b (VD-C-161): la chiave privata della posta (apre i collegamenti d'invito
    # sigillati) e le credenziali SMTP solo al lavoratore notification-posta
    "notification": ("POSTA_CHIAVE_PRIVATA", "POSTA_CHIAVI_PRIVATE_PRECEDENTI",
                     "SMTP_UTENTE", "SMTP_PASSWORD",
                     # R13b (VD-C-078): password delle caselle PEC
                     "PEC_SEGRETI_JSON"),
}
#: Prefisso delle API interne fra servizi (revisione 2 di R05, S2): nessuna
#: rotta dell'ingress verso un servizio applicativo lo deve coprire.
PREFISSO_INTERNO = "/interno/"


#: Revisione 3 di R05 (T5): lavoratori con un login del database PROPRIO,
#: diverso da quello dell'API (ruolo verumdoc_ciclo_vita_lavoratore, migrazione
#: 043): DATABASE_URL nel loro Secret e preso da lì (envSegreti).
LOGIN_DB_PROPRIO: dict[str, str] = {"ciclo-vita": "lavoratore"}


def errori_login_db_lavoratore(servizi: dict) -> list[str]:
    errori: list[str] = []
    for nome, lavoratore in LOGIN_DB_PROPRIO.items():
        s = servizi.get(nome)
        if s is None:
            continue
        w = dict(topologia.lavoratori(nome, s)).get(f"{nome}-{lavoratore}")
        if w is None:
            errori.append(f"{nome}: lavoratore {lavoratore} assente o disattivato")
            continue
        secret = f"{nome}-{lavoratore}-secrets"
        if "DATABASE_URL" not in (w.get("secretKeys") or []):
            errori.append(f"{nome}-{lavoratore}: DATABASE_URL non è nel Secret del lavoratore "
                          f"({secret}): serve un login del database distinto da quello "
                          "dell'API")
        rif = (w.get("envSegreti") or {}).get("DATABASE_URL") or {}
        if rif != {"secret": secret, "key": "DATABASE_URL"}:
            errori.append(f"{nome}-{lavoratore}: envSegreti.DATABASE_URL deve venire da "
                          f"{secret} (altrimenti vale il login dell'API di ciclo-vita-secrets)")
    return errori


#: Revisione R12b: lavoratori che NON ricevono il Secret del servizio (segreti che non
#: usano: chiave dei webhook, pepper, Redis): solo il proprio, con DATABASE_URL da lì.
SOLO_SEGRETI_PROPRI: dict[str, str] = {"notification": "posta"}


def errori_segreti_propri(servizi: dict) -> list[str]:
    errori: list[str] = []
    for nome, lavoratore in SOLO_SEGRETI_PROPRI.items():
        s = servizi.get(nome)
        if s is None:
            continue
        w = dict(topologia.lavoratori(nome, s)).get(f"{nome}-{lavoratore}")
        if w is None:
            errori.append(f"{nome}: lavoratore {lavoratore} assente o disattivato")
            continue
        secret = f"{nome}-{lavoratore}-secrets"
        if w.get("segretiServizio", True) is not False:
            errori.append(f"{nome}-{lavoratore}: deve avere segretiServizio: false (riceverebbe "
                          f"{nome}-secrets, con segreti che non usa)")
        if "DATABASE_URL" not in (w.get("secretKeys") or []):
            errori.append(f"{nome}-{lavoratore}: DATABASE_URL non è nel Secret del lavoratore "
                          f"({secret})")
        rif = (w.get("envSegreti") or {}).get("DATABASE_URL") or {}
        if rif != {"secret": secret, "key": "DATABASE_URL"}:
            errori.append(f"{nome}-{lavoratore}: envSegreti.DATABASE_URL deve venire da {secret}")
    return errori


def errori_credenziali_lavoratore(servizi: dict) -> list[str]:
    errori: list[str] = []
    for nome, chiavi in CREDENZIALI_SOLO_LAVORATORE.items():
        s = servizi.get(nome)
        if s is None:
            continue
        nel_servizio = set(s.get("secretKeys") or [])
        nei_lavoratori = {k for _n, w in topologia.lavoratori(nome, s)
                          for k in (w.get("secretKeys") or [])}
        for k in chiavi:
            if k in nel_servizio:
                errori.append(f"{nome}: {k} nel Secret del servizio (lo riceve il pod "
                              f"{'esposto ' if s.get('exposed') else ''}dell'API): va solo nel "
                              "Secret del lavoratore (workers.<nome>.secretKeys)")
            if k not in nei_lavoratori:
                errori.append(f"{nome}: {k} non è nel Secret di nessun lavoratore abilitato")
    return errori


def errori_rotte_interne(rotte: list[dict]) -> list[str]:
    """Nessuna rotta dell'ingress verso un servizio applicativo raggiunge le API
    interne (``/interno/…``): la rotta «/» resta ammessa solo per il frontend."""
    errori: list[str] = []
    for r in rotte:
        if r["service"] == "frontend":
            continue
        percorso = r["path"].rstrip("/") + "/"
        if PREFISSO_INTERNO.startswith(percorso) or percorso.startswith(PREFISSO_INTERNO):
            errori.append(f"ingress {r['path']} → {r['service']}: raggiunge le API interne "
                          f"({PREFISSO_INTERNO}), che solo i servizi chiamano")
    return errori


def errori_kafka_msk(servizi: dict) -> list[str]:
    """R04: in esercizio Kafka è MSK, che accetta solo TLS + IAM. Un servizio con
    ``KAFKA_BOOTSTRAP`` nella ConfigMap deve avere ``KAFKA_AUTENTICAZIONE: iam`` e
    una regione, altrimenti non si collega (PLAINTEXT resta solo per il compose)."""
    errori: list[str] = []
    for nome, s in servizi.items():
        config = s.get("config") or {}
        if "KAFKA_BOOTSTRAP" not in config:
            continue
        if str(config.get("KAFKA_AUTENTICAZIONE", "")).lower() != "iam":
            errori.append(f"{nome}: KAFKA_BOOTSTRAP senza KAFKA_AUTENTICAZIONE: iam (MSK "
                          "accetta solo TLS con IAM)")
        elif not str(config.get("KAFKA_REGIONE", "")).strip():
            errori.append(f"{nome}: KAFKA_AUTENTICAZIONE: iam senza KAFKA_REGIONE")
    return errori


def errori_formato_segreti(servizi: dict) -> list[str]:
    """R04: ``formatoSegreti`` documenta il formato di chiavi ESISTENTI del Secret
    (finiscono come commento nel modello generato); una nota su una chiave che il
    servizio non legge è un errore (nota rimasta dopo un cambio di nome)."""
    errori: list[str] = []
    for nome, s in servizi.items():
        formati = s.get("formatoSegreti") or {}
        if not isinstance(formati, dict):
            errori.append(f"{nome}: formatoSegreti deve essere una mappa chiave → nota")
            continue
        for chiave, nota in formati.items():
            if chiave not in (s.get("secretKeys") or []):
                errori.append(f"{nome}: formatoSegreti.{chiave} non è fra le secretKeys")
            if not isinstance(nota, str) or not nota.strip() or "\n" in nota:
                errori.append(f"{nome}: formatoSegreti.{chiave}: nota vuota o su più righe")
    return errori


#: Variabili lette da libs/shared/db direttamente dall'ambiente (R04, r04-dati):
#: replica di sola lettura facoltativa (os.environ, non una classe Settings).
CAMPI_DB_AMBIENTE = frozenset({"DATABASE_URL_LETTURA",
                               # R04 correzioni: connessione diretta e ruolo della manutenzione
                               "DATABASE_URL_DIRETTA", "DATABASE_URL_MANUTENZIONE"})

#: Connessione Kafka condivisa (R04 integrazione, MSK con IAM): lette da
#: libs/shared/kafka/connessione.py (ImpostazioniKafka, prefisso KAFKA_) in ogni
#: servizio che usa Kafka.
CAMPI_KAFKA_CONNESSIONE = frozenset({"KAFKA_AUTENTICAZIONE", "KAFKA_REGIONE", "KAFKA_CA_FILE"})


def errori_iam(servizi: dict) -> list[str]:
    """Ruoli IRSA (VD-C-101): annotazione ⇔ blocco «iam» ⇔ ruolo di Terraform."""
    errori: list[str] = []
    voci = dict(servizi)
    for nome, s in servizi.items():
        for nome_w, w in topologia.lavoratori(nome, s):
            if w.get("iam"):
                # Lavoratore con ServiceAccount e ruolo propri: stessi controlli, niente ConfigMap
                voci[nome_w] = {"iam": w["iam"], "account": s.get("account"), "config": {}}
    for nome, s in voci.items():
        irsa = s.get("irsa", True) is not False
        iam = s.get("iam") or {}
        s3, kafka = iam.get("s3") or {}, iam.get("kafka") or []
        config = s.get("config") or {}
        if irsa and not (s3 or kafka):
            errori.append(
                f"{nome}: ServiceAccount annotato per IRSA ma senza blocco «iam»: Terraform "
                          f"(modulo iam_servizi) non crea il ruolo dell'annotazione (VD-C-101); "
                          "aggiungere iam oppure irsa: false")
        if not irsa and (s3 or kafka):
            errori.append(f"{nome}: blocco «iam» con irsa: false: il ruolo esisterebbe ma i pod "
                          "non lo userebbero")
        account = str(s.get("account") or topologia.ACCOUNT_PRINCIPALE)
        if irsa and account != topologia.ACCOUNT_PRINCIPALE:
            errori.append(
                f"{nome}: servizio di un account separato con IRSA: nessun ruolo nell'account "
                          "principale, impostare irsa: false")
        for bucket, permessi in s3.items():
            if bucket not in BUCKET_TERRAFORM:
                errori.append(f"{nome}: iam.s3.{bucket}: bucket sconosciuto (ammessi "
                              f"{', '.join(BUCKET_TERRAFORM)})")
            for p in set(permessi or []) - PERMESSI_S3:
                errori.append(f"{nome}: iam.s3.{bucket}: permesso «{p}» sconosciuto "
                              f"(ammessi {', '.join(sorted(PERMESSI_S3))})")
            if not permessi:
                errori.append(f"{nome}: iam.s3.{bucket} senza permessi")
        for p in set(kafka) - PERMESSI_KAFKA:
            errori.append(f"{nome}: iam.kafka: permesso «{p}» sconosciuto "
                          f"(ammessi {', '.join(sorted(PERMESSI_KAFKA))})")
        for impostazione, bucket in BUCKET_DA_IMPOSTAZIONE.items():
            if impostazione in config and bucket not in s3:
                errori.append(
                    f"{nome}: {impostazione} nella ConfigMap ma nessun permesso iam.s3.{bucket}: "
                              "l'accesso al bucket fallirebbe con IRSA")
        errori += errori_prefissi_iam(nome, s, iam)
        if "KAFKA_BOOTSTRAP" in config and not kafka:
            errori.append(f"{nome}: usa Kafka (KAFKA_BOOTSTRAP) ma iam.kafka è vuoto: MSK con "
                          "autenticazione IAM rifiuterebbe il servizio")
        errori += errori_kafka_topic(nome, iam)
    return errori


#: Prefissi di chiave dei permessi sugli oggetti («?» = un carattere, mai «*»):
#: cartelle con la barra finale, eventualmente seguite dall'inizio del nome di
#: un file (revisione 2 di R05, S4: «<ente>/<pda>/RdV_Definitiv» della hashchain)
_PREFISSO_OGGETTI = re.compile(r"^([a-z0-9_?-]+/)+([A-Za-z0-9_-]+)?$")


def errori_prefissi_iam(nome: str, s: dict, iam: dict) -> list[str]:
    """Revisione R05 (R3): ``prefissi_oggetti`` (permessi sugli oggetti limitati a
    prefissi di chiave) e ``nega_prefissi`` (Deny esplicito) del modulo Terraform
    iam_servizi; la cancellazione dello scarto mai a un servizio esposto
    all'ingress e sempre limitata ai prefissi dichiarati (le chiavi dei PdA)."""
    errori: list[str] = []
    s3 = iam.get("s3") or {}
    prefissi = iam.get("prefissi_oggetti") or {}
    for bucket, permessi in prefissi.items():
        for permesso, lista in (permessi or {}).items():
            if permesso not in (s3.get(bucket) or []):
                errori.append(f"{nome}: iam.prefissi_oggetti.{bucket}.{permesso} senza il "
                              "permesso nel blocco s3 dello stesso bucket")
            if not lista:
                errori.append(f"{nome}: iam.prefissi_oggetti.{bucket}.{permesso} senza prefissi")
            for prefisso in lista or []:
                if not _PREFISSO_OGGETTI.match(str(prefisso)):
                    errori.append(f"{nome}: iam.prefissi_oggetti.{bucket}.{permesso}: prefisso "
                                  f"{prefisso!r} non valido (es. «pratiche/», «?» per un "
                                  "carattere, mai «*»; dopo l'ultima barra al più l'inizio "
                                  "di un nome di file)")
    for bucket, lista in (iam.get("nega_prefissi") or {}).items():
        if bucket not in BUCKET_TERRAFORM:
            errori.append(f"{nome}: iam.nega_prefissi.{bucket}: bucket sconosciuto")
        for prefisso in lista or []:
            if not _PREFISSO_ELENCO.match(str(prefisso)):
                errori.append(f"{nome}: iam.nega_prefissi.{bucket}: prefisso {prefisso!r} "
                              "non valido (es. «registro/», senza caratteri jolly)")
    for bucket, permessi in s3.items():
        if "scarto" not in (permessi or []):
            continue
        if s.get("exposed"):
            errori.append(f"{nome}: permesso «scarto» (cancellazione nel WORM) su un servizio "
                          "esposto all'ingress: solo un lavoratore con ServiceAccount proprio")
        if not ((prefissi.get(bucket) or {}).get("scarto")):
            errori.append(f"{nome}: permesso «scarto» su {bucket} senza prefissi_oggetti "
                          "(cancellazione limitata alle chiavi dei PdA)")
    return errori


#: Chiamate S3 che richiedono s3:ListBucket, cioè il permesso «elenco» del blocco
#: iam (R04 quarto giro, F1: la hashchain elencava il WORM senza permesso)
_ELENCO_S3 = re.compile(r"\blist_objects(_v2)?\b")
_PREFISSO_ELENCO = re.compile(r"^[a-z0-9_-]+(/[a-z0-9_-]+)*/$")


def file_che_elencano(nome: str) -> list[str]:
    """File del servizio (codice, non test) che elencano un bucket S3."""
    app = RADICE / "services" / nome / "app"
    if not app.is_dir():
        return []
    return sorted(str(f.relative_to(RADICE)) for f in app.rglob("*.py")
                  if "tests" not in f.parts and _ELENCO_S3.search(f.read_text(encoding="utf-8")))


def errori_elenco_s3(servizi: dict) -> list[str]:
    """Chi elenca un bucket nel codice ha «elenco» nel blocco iam, e solo lui (permessi
    minimi); ``elenco_prefissi`` limita l'elenco a prefissi (condizione s3:prefix)."""
    errori: list[str] = []
    for nome, s in servizi.items():
        iam = s.get("iam") or {}
        s3 = iam.get("s3") or {}
        prefissi = iam.get("elenco_prefissi") or {}
        con_elenco = sorted(b for b, p in s3.items() if "elenco" in (p or []))
        usano = file_che_elencano(nome)
        if usano and s.get("irsa", True) is not False and not con_elenco:
            errori.append(f"{nome}: elenca un bucket S3 ({', '.join(usano)}) ma il blocco iam non "
                          "ha il permesso «elenco» (s3:ListBucket): su S3 vero AccessDenied")
        if con_elenco and not usano:
            errori.append(f"{nome}: permesso «elenco» su {', '.join(con_elenco)} ma nessuna "
                          "chiamata list_objects nel codice: permesso da togliere")
        for bucket, lista in prefissi.items():
            if bucket not in con_elenco:
                errori.append(f"{nome}: iam.elenco_prefissi.{bucket} senza il permesso «elenco» "
                              "sullo stesso bucket")
            for prefisso in lista or []:
                if not _PREFISSO_ELENCO.match(str(prefisso)):
                    errori.append(f"{nome}: iam.elenco_prefissi.{bucket}: prefisso {prefisso!r} "
                                  "non valido (es. «registro/», senza caratteri jolly)")
    return errori


def _produttori_consumatori() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(topic scritti, topic letti) per servizio, da infra/kafka/topics.yaml."""
    dati = yaml.safe_load(TOPICS.read_text(encoding="utf-8")) or {}
    scrive: dict[str, set[str]] = {}
    legge: dict[str, set[str]] = {}
    for t in dati.get("topics") or []:
        for s in t.get("producers") or []:
            scrive.setdefault(str(s), set()).add(str(t["name"]))
        for s in t.get("consumers") or []:
            legge.setdefault(str(s), set()).add(str(t["name"]))
    return scrive, legge


def errori_kafka_topic(nome: str, iam: dict) -> list[str]:
    """R04 correzioni (rev4 rilievo 7): i permessi Kafka del ruolo IAM sono per
    topic (modulo iam_servizi): «produzione» ⇔ il servizio è producer di almeno un
    topic in topics.yaml, «consumo» ⇔ consumer di almeno uno, con i suoi gruppi
    di consumo (``gruppi_kafka``); un permesso senza topic, o topic senza permesso,
    è un errore (WriteData/ReadData negati, o concessi senza motivo)."""
    errori: list[str] = []
    kafka = set(iam.get("kafka") or [])
    gruppi = iam.get("gruppi_kafka") or []
    scrive, legge = _produttori_consumatori()
    if ("produzione" in kafka) != bool(scrive.get(nome)):
        errori.append(f"{nome}: iam.kafka {'con' if 'produzione' in kafka else 'senza'} "
                      f"«produzione» ma {'nessun' if not scrive.get(nome) else 'alcuni'} topic con "
                      f"{nome} fra i producers di infra/kafka/topics.yaml "
                      f"({', '.join(sorted(scrive.get(nome, ()))) or '-'})")
    if ("consumo" in kafka) != bool(legge.get(nome)):
        errori.append(f"{nome}: iam.kafka {'con' if 'consumo' in kafka else 'senza'} «consumo» "
                      f"ma {'nessun' if not legge.get(nome) else 'alcuni'} topic con {nome} fra i "
                      "consumers di infra/kafka/topics.yaml "
                      f"({', '.join(sorted(legge.get(nome, ()))) or '-'})")
    if ("consumo" in kafka) != bool(gruppi):
        errori.append(f"{nome}: iam.gruppi_kafka deve elencare i gruppi di consumo se e solo se "
                      "iam.kafka contiene «consumo»")
    if not isinstance(gruppi, list) or any(not isinstance(g, str) or not g or "*" in g
                                           for g in gruppi):
        errori.append(f"{nome}: iam.gruppi_kafka: nomi di gruppo non validi "
                      "(niente caratteri jolly)")
    return errori


def _topic_partizioni() -> dict[str, int]:
    dati = yaml.safe_load(TOPICS.read_text(encoding="utf-8")) or {}
    voci = dati.get("topics", dati) if isinstance(dati, dict) else dati
    return {str(v["name"]): int(v["partitions"]) for v in voci or [] if isinstance(
        v, dict) and "name" in v}


def valore_settings(servizio: str, chiave: str, config: dict) -> str | None:
    """Valore effettivo di un'impostazione: ConfigMap, altrimenti predefinito delle Settings."""
    if chiave in config:
        return str(config[chiave])
    file = RADICE / "services" / servizio / "app" / "config.py"
    if not file.exists():
        return None
    albero = ast.parse(file.read_text(encoding="utf-8"))
    for classe in (n for n in albero.body if isinstance(n, ast.ClassDef)):
        for n in classe.body:
            if (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                    and n.target.id.upper() == chiave and isinstance(n.value, ast.Constant)):
                return str(n.value.value)
    return None


def errori_keda(valori: dict, carichi: list[dict], campi_di: dict[str, set[str]]) -> list[str]:
    """ScaledObject KEDA coerenti con codice, topic e partizioni (R04)."""
    errori: list[str] = []
    partizioni = _topic_partizioni()
    con_keda = [c for c in carichi if (c.get("autoscaling") or {}).get("keda")]
    if con_keda and not all(k in (valori.get("keda") or {}) for k in ("kafka", "postgresql")):
        errori.append("keda: mancano le autenticazioni globali (keda.kafka, keda.postgresql)")
    for c in con_keda:
        a = c["autoscaling"]
        k = a["keda"]
        nome = c["nome"]
        if not a.get("enabled"):
            errori.append(f"{nome}: autoscaling.keda con autoscaling disattivato")
        if not (k.get("kafka") or k.get("postgresql")):
            errori.append(f"{nome}: autoscaling.keda senza attivatori kafka o postgresql")
        config = topologia.config_effettiva(c)
        for kt in k.get("kafka") or []:
            if "KAFKA_BOOTSTRAP" not in config:
                errori.append(f"{nome}: attivatore kafka senza KAFKA_BOOTSTRAP nella ConfigMap")
            for campo, valore in (
                ("gruppoSetting", kt.get("gruppo")), ("topicSetting", kt.get("topic"))):
                impostazione = kt.get(campo)
                if not impostazione:
                    continue
                if impostazione not in campi_di.get(c["servizio"], set()):
                    errori.append(
                        f"{nome}: keda {campo}={impostazione} non è un'impostazione del servizio")
                    continue
                reale = valore_settings(c["servizio"], impostazione, config)
                if reale != str(valore):
                    errori.append(
                        f"{nome}: keda usa {valore!r} ma il servizio legge {impostazione}="
                                  f"{reale!r}: KEDA misurerebbe il ritardo di un altro "
                                  f"gruppo/topic")
            topic = str(kt.get("topic"))
            if topic not in partizioni:
                errori.append(f"{nome}: keda topic {topic} assente da infra/kafka/topics.yaml")
            elif int(a.get("maxReplicas", 10)) > partizioni[topic]:
                errori.append(
                    f"{nome}: maxReplicas {a.get('maxReplicas')} oltre le {partizioni[topic]} "
                              f"partizioni di {topic}: le repliche in più resterebbero senza "
                              f"partizioni")
            if int(kt.get("lagThreshold", 0)) <= 0:
                errori.append(f"{nome}: keda lagThreshold deve essere positivo")
        for q in k.get("postgresql") or []:
            testo = " ".join(str(q.get("query", "")).split())
            maiuscolo = testo.upper()
            if not maiuscolo.startswith("SELECT ") or ";" in testo:
                errori.append(f"{nome}: keda query PostgreSQL non è una sola SELECT: {testo[:80]}")
            if " LIMIT " not in maiuscolo:
                errori.append(f"{nome}: keda query senza LIMIT: il conteggio deve essere limitato "
                              "(mai COUNT(*) su una tabella che cresce)")
            if int(q.get("targetQueryValue", 0)) <= 0:
                errori.append(f"{nome}: keda targetQueryValue deve essere positivo")
    return errori


def errori_entrypoint(servizi: dict) -> list[str]:
    """entrypointEnv: ogni variabile è letta dall'entrypoint dell'immagine del servizio."""
    errori: list[str] = []
    for nome, s in servizi.items():
        variabili = s.get("entrypointEnv") or {}
        if not variabili:
            continue
        script = RADICE / "services" / nome / "entrypoint.sh"
        testo = script.read_text(encoding="utf-8") if script.exists() else ""
        for k in variabili:
            if not re.search(rf"\$\{{?{re.escape(k)}\b", testo):
                errori.append(
                    f"{nome}: entrypointEnv {k} non letta da services/{nome}/entrypoint.sh")
    return errori


def _core(valore: object) -> float:
    testo = str(valore or "0")
    return float(testo[:-1]) / 1000 if testo.endswith("m") else float(testo)


def _risorse_carico(c: dict, repliche: int) -> dict[str, float]:
    r = c.get("resources") or {}
    lim, req = r.get("limits") or {}, r.get("requests") or {}
    return {"limits.cpu": _core(lim.get("cpu")) * repliche,
            "limits.memory": (mib(lim.get("memory", "0")) or 0) * repliche,
            "requests.cpu": _core(req.get("cpu")) * repliche,
            "requests.memory": (mib(req.get("memory", "0")) or 0) * repliche}


def risorse_massime(valori: dict) -> dict[str, float]:
    """Somma di limiti e richieste (CPU in core, memoria in MiB) al massimo delle repliche:
    servizi e lavoratori al massimo dell'HPA/KEDA, un pod per job, PgBouncer."""
    totale = dict.fromkeys(("limits.cpu", "limits.memory", "requests.cpu", "requests.memory"), 0.0)
    voci: list[tuple[dict, int]] = []
    for c in topologia.carichi(valori):
        a = c.get("autoscaling") or {}
        voci.append(
            (c, int(a.get("maxReplicas", 10)) if a.get("enabled") else int(c.get("replicas") or 1)))
    voci += [(c, 1) for c in topologia.cronjob(valori) + topologia.jobs(valori)]
    pb = valori.get("pgbouncer") or {}
    if pb.get("enabled") and topologia.account(valori) == topologia.ACCOUNT_PRINCIPALE:
        voci.append(({"resources": pb.get("resources")}, int(pb.get("replicas", 1))))
    for c, repliche in voci:
        for k, v in _risorse_carico(c, repliche).items():
            totale[k] += v
    return totale


def quota_namespace(valori: dict) -> dict | None:
    if topologia.account(valori) != topologia.ACCOUNT_PRINCIPALE:
        return valori.get("quotaNamespace")
    for doc in yaml.safe_load_all(QUOTA_NAMESPACE.read_text(encoding="utf-8")):
        if doc and doc.get("kind") == "ResourceQuota":
            return doc["spec"]["hard"]
    return None


def errori_quota(valori: dict) -> list[str]:
    """Limiti e richieste al massimo delle repliche entro la ResourceQuota: oltre,
    KEDA/HPA chiedono pod che il namespace rifiuta in silenzio sotto carico."""
    hard = quota_namespace(valori)
    if not hard:
        return [f"[{topologia.account(valori)}] ResourceQuota del namespace non trovata"]
    errori: list[str] = []
    for chiave, valore in risorse_massime(valori).items():
        if chiave not in hard:
            errori.append(f"quota del namespace senza {chiave}")
            continue
        tetto = _core(hard[chiave]) if chiave.endswith("cpu") else (mib(hard[chiave]) or 0)
        if valore > tetto:
            mostra = f"{valore:.1f} core" if chiave.endswith("cpu") else f"{valore / 1024:.1f} GiB"
            errori.append(
                f"[{topologia.account(valori)}] quota del namespace: {chiave} al massimo delle "
                          f"repliche {mostra} oltre {hard[chiave]}")
    return errori


def errori_connessioni(valori: dict) -> list[str]:
    """Connessioni verso l'istanza primaria di PostgreSQL entro max_connections (R04)."""
    if topologia.account(valori) != topologia.ACCOUNT_PRINCIPALE:
        return []
    pg = valori.get("postgres") or {}
    pb = valori.get("pgbouncer") or {}
    errori: list[str] = []
    totale = int(pg.get("connessioniRiservate", 0))
    dettaglio = [f"riservate {totale}"]
    if pb.get("enabled"):
        imp = pb.get("impostazioni") or {}
        if imp.get("pool_mode") != "transaction":
            errori.append("pgbouncer: pool_mode deve essere transaction (piano §4.2)")
        if int(imp.get("max_prepared_statements", 0)) <= 0:
            errori.append(
                "pgbouncer: max_prepared_statements deve essere > 0 (asyncpg usa i prepared "
                          "statement di protocollo: senza, errori «prepared statement does not "
                          "exist»)")
        if "default_transaction_read_only" not in str(imp.get("track_extra_parameters", "")):
            errori.append(
                "pgbouncer: track_extra_parameters deve comprendere default_transaction_read_only "
                          "(connessioni alla replica di libs/shared/db)")
        per_db = int(imp.get("max_db_connections", 0))
        if per_db <= 0:
            errori.append("pgbouncer: max_db_connections deve limitare le connessioni per database")
        n = int(pb.get("replicas", 1)) * per_db
        totale += n
        dettaglio.append(f"pgbouncer {n}")
    per_processo = int(pg.get("poolPerProcesso", 30))
    singoli = {c["nome"] for c in topologia.cronjob(valori) + topologia.jobs(valori)}
    for c in topologia.carichi(valori) + topologia.cronjob(valori) + topologia.jobs(valori):
        if "postgres" not in (c["egress"].get("data") or []):
            continue
        a = c.get("autoscaling") or {}
        if c["nome"] in singoli:   # job: un pod alla volta (concurrencyPolicy Forbid)
            repliche = 1
        else:
            repliche = int(a.get("maxReplicas", 10)) if a.get("enabled") else int(
                c.get("replicas") or 1)
        totale += repliche * per_processo
        dettaglio.append(f"{c['nome']} {repliche}×{per_processo}")
    massimo = int(pg.get("maxConnessioni", 0))
    if totale > massimo:
        errori.append(f"connessioni a PostgreSQL {totale} oltre postgres.maxConnessioni {massimo} "
                      f"({', '.join(dettaglio)}): passare da PgBouncer o ridurre le repliche "
                      f"dirette")
    return errori


def unisci(a: dict, b: dict) -> dict:
    """Fusione delle mappe come Helm (-f values.yaml -f values.<amb>.yaml)."""
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            unisci(a[k], v)
        else:
            a[k] = copy.deepcopy(v)
    return a


#: Interruttori d'emergenza che si impostano SOLO a mano durante una procedura
#: (kubectl set env), mai nei values: dimenticati lì fermerebbero il servizio a
#: ogni rilascio (R04 terzo giro, T5: registro delle impronte sospeso).
SOLO_A_MANO = ("CATENA_REGISTRO_SOSPESO",)


def _chiavi_annidate(valore: object, percorso: str = "") -> list[tuple[str, str]]:
    if isinstance(valore, dict):
        trovate = []
        for k, v in valore.items():
            trovate.append((str(k), f"{percorso}.{k}".lstrip(".")))
            trovate += _chiavi_annidate(v, f"{percorso}.{k}")
        return trovate
    if isinstance(valore, list):
        return [t for i, v in enumerate(valore) for t in _chiavi_annidate(v, f"{percorso}[{i}]")]
    return []


def verifica_condivisi(valori: dict, etichetta: str = "values.yaml") -> list[str]:
    errori: list[str] = []
    for chiave, percorso in _chiavi_annidate(valori):
        if chiave in SOLO_A_MANO:
            errori.append(f"[{etichetta}] {percorso}: {chiave} si imposta solo a mano durante "
                          "il ripristino (runbook ripristino_registro_da_backup.md), "
                          "mai nei values")
    servizi = topologia.servizi_attivi(valori)
    for chiave, nomi in UGUALI_FRA.items():
        if not any(n in servizi for n in nomi):
            continue   # rilascio di un altro account (es. testimone)
        trovati = {n: (servizi.get(n, {}).get("config") or {}).get(chiave) for n in nomi}
        for n, v in trovati.items():
            if v in (None, ""):
                errori.append(f"[{etichetta}] {n}: manca {chiave} "
                              f"(stesso valore in {', '.join(nomi)})")
        if len({str(v) for v in trovati.values()}) > 1:
            errori.append(f"[{etichetta}] {chiave} diverso fra {', '.join(nomi)}: {trovati}")
    for chiave, nomi in UGUALI_SE_PRESENTI.items():
        trovati = {n: (servizi.get(n, {}).get("config") or {}).get(chiave) for n in nomi
                   if chiave in (servizi.get(n, {}).get("config") or {})}
        if len({str(v) for v in trovati.values()}) > 1:
            errori.append(f"[{etichetta}] {chiave} diverso fra {', '.join(nomi)}: {trovati}")
    for chiave in UGUALI_OVUNQUE:
        valori_k = {n: (s.get("config") or {}).get(chiave) for n, s in servizi.items()
                    if chiave in (s.get("config") or {})}
        if len(set(valori_k.values())) > 1:
            errori.append(f"[{etichetta}] {chiave} diverso fra i servizi: {valori_k}")
    for nome, s in servizi.items():
        config = RADICE / "services" / nome / "app" / "config.py"
        if nome == "frontend" or not config.exists():
            continue
        campi = campi_settings(config) | campi_altre_settings(config.parent)
        # R06 (VD-C-126): vale anche il Secret proprio di un lavoratore
        disponibili = set(s.get("secretKeys") or []) | {
            k for _n, w in topologia.lavoratori(nome, s) for k in (w.get("secretKeys") or [])}
        for chiave in SEGRETI_OBBLIGATORI + ("PDND_PUBLIC_KEY",):
            if chiave in campi and chiave not in disponibili:
                errori.append(f"[{etichetta}] {nome}: le Settings leggono {chiave} "
                              "ma non è fra i secretKeys")
        if usa_limitatore(config.parent):
            if "REDIS_URL" not in (s.get("secretKeys") or []):
                errori.append(f"[{etichetta}] {nome}: usa il limite di richieste al minuto "
                              "ma REDIS_URL non è fra i secretKeys (in produzione non parte)")
            if "redis" not in ((s.get("egress") or {}).get("data") or []):
                errori.append(f"[{etichetta}] {nome}: usa il limite di richieste al minuto "
                              "ma l'uscita verso redis non è ammessa (egress.data)")
    return errori


def usa_limitatore(cartella: Path) -> bool:
    """Il servizio importa il limitatore di richieste (``libs.shared.limiti_richieste``)."""
    return any(MODULO_LIMITATORE in f.read_text(encoding="utf-8")
               for f in cartella.rglob("*.py") if "__pycache__" not in f.parts)


def main() -> int:
    base = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    errori = verifica(base) + verifica_condivisi(base)
    for rilascio in RILASCI:
        fusi = copy.deepcopy(base)
        for amb in rilascio:
            extra = yaml.safe_load(
                (VALUES.parent / f"values.{amb}.yaml").read_text(encoding="utf-8"))
            fusi = unisci(fusi, extra or {})
        errori += [e for e in verifica(fusi) if e not in errori]
        errori += verifica_condivisi(fusi, "+".join(f"values.{a}.yaml" for a in rilascio))
    for e in errori:
        print("ERRORE:", e)
    if errori:
        return 1
    print("OK: values.yaml (e overlay staging/prod, account del testimone) coerente con rotte di "
          "salute, Settings, flussi di rete, valori condivisi, ruoli IAM, KEDA, quota e "
          "connessioni.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
