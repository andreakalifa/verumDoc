# Componenti di terze parti — VerumDoc

> VerumDoc è distribuito con licenza **EUPL-1.2** (`LICENSE`). Questo file elenca i componenti di terze parti usati dal progetto, con la loro licenza e la compatibilità con la EUPL-1.2 (REGOLE_SVILUPPO §20.8; decisione VD-P-190). Ultima modifica: 2026-11-14 (R18, apertura open source).
>
> Metodo: le dipendenze Python sono la **chiusura transitiva** dei `pyproject.toml` dei servizi e dell'SDK, letta dai metadati dei pacchetti installati nell'ambiente di prova (campo `License-Expression`, `License` o classificatori); le dipendenze npm sono lette da `frontend/package-lock.json` e dai `package.json` installati; Java dal `pom.xml` (gli artefatti transitivi non sono stati risolti: Maven Central non era raggiungibile dall'ambiente di R18, vedi §5). Le versioni sono quelle provate; gli intervalli ammessi sono nei file delle dipendenze.

## 1. Sintesi

| Ambito | Componenti | Licenze | Esito |
|---|---|---|---|
| Python, esercizio | 79 | MIT 33, Apache-2.0 18, BSD-3-Clause 14, PSF-2.0 3, doppie o composte permissive 8, MIT-0 1, MPL-2.0 1 (certifi), EPL-2.0 OR GPL-2.0-or-later 1 (asyncssh) | **compatibili** |
| Python, solo sviluppo | 52 | MIT 21, Apache-2.0 14, BSD-3-Clause 11, BSD-2-Clause 3, MPL-2.0 1, MIT-0 1, Apache-2.0 AND CNRI-Python 1 | compatibili (non distribuiti) |
| npm, console | 73 | MIT 58, ISC 11, BSD-3-Clause 2, MIT AND ISC 1, OFL-1.1 1 (font Archivo) | **compatibili** |
| npm, solo sviluppo | 232 | MIT 189, Apache-2.0 17, ISC 14, BSD-2-Clause 6, BSD-3-Clause 2, MPL-2.0 2, Python-2.0 1, CC-BY-4.0 1 | compatibili (non distribuiti) |
| Java (verifica-firme) | 3 dirette + BOM | LGPL-2.1 (EU DSS), Apache-2.0 (Spring Boot, Nimbus) | compatibili; transitive **da generare** (§5) |
| Binari e dati | 5 | Apache-2.0, OGL-UK-3.0, GPL-3.0-or-later OR MPL-2.0, licenza d'uso UNI | compatibili; **schema UNI non pubblicabile** (§6) |
| Immagini di container dello sviluppo locale | 13 | varie | **3 da rivedere** (§8) |

**Nessuna dipendenza del codice è incompatibile** con la EUPL-1.2 e nessuna ha licenza sconosciuta. Da rivedere, perché non open source o con termini da verificare, solo strumenti dell'**ambiente di sviluppo locale** (`docker-compose.yml`), non inclusi nel software distribuito: `hashicorp/vault:1.16` (BUSL-1.1), `redis:7-alpine` (dalla 7.4 RSALv2/SSPLv1) e `confluentinc/cp-kafka:7.6.0` (termini dell'immagine Confluent). Alternative nel §8.

## 2. Compatibilità con la EUPL-1.2

- Le licenze **permissive** (MIT, MIT-0, BSD, ISC, Apache-2.0, PSF-2.0, Python-2.0, CNRI-Python) consentono di combinare il componente con un'opera EUPL-1.2 e di distribuire l'insieme: si conservano gli avvisi di copyright e, per Apache-2.0, i file NOTICE dei componenti.
- Le licenze **copyleft deboli** usate (MPL-2.0, LGPL-2.1, EPL-2.0) coprono i file del componente, non il resto dell'opera: VerumDoc le usa come dipendenze **senza modifiche**, installate dai registri ufficiali. MPL-2.0 e LGPL-2.1 sono anche fra le «licenze compatibili» dell'appendice della EUPL-1.2 (art. 5). Chi distribuisce immagini con questi componenti deve indicare dove trovarne il sorgente (registro PyPI o Maven Central, versione esatta).
- **asyncssh** (EPL-2.0 OR GPL-2.0-or-later): si applica EPL-2.0. La decisione D10 (VD-P-32, R07) lo accettava per un servizio erogato e non distribuito; con la pubblicazione del sorgente resta compatibile perché VerumDoc non include né modifica asyncssh (dipendenza dichiarata nel `pyproject.toml` di sftp-ingest). Se un giorno lo si modificasse, le modifiche andrebbero pubblicate sotto EPL-2.0.
- Il **font Archivo** (OFL-1.1) è incluso nella console costruita; resta sotto OFL-1.1, non diventa parte dell'opera EUPL e non può essere venduto da solo.
- Appendice della EUPL-1.2 (licenze compatibili): GPL-2.0, GPL-3.0, AGPL-3.0, OSL-2.1, OSL-3.0, EPL-1.0, CeCILL-2.0, CeCILL-2.1, MPL-2.0, LGPL-2.1, LGPL-3.0, CC BY-SA 3.0 (non per il software), EUPL-1.1, LiLiQ-R, LiLiQ-R+.

## 3. Componenti con note

| Componente | Versione | Licenza | Usato da | Note |
|---|---|---|---|---|
| asyncssh | 2.24.1 | EPL-2.0 OR GPL-2.0-or-later (si applica EPL-2.0) | sftp-ingest | Vedi §2. Alternativa scartata in R07: Paramiko (LGPL-2.1, circa 350 righe da riscrivere) |
| EU DSS (Digital Signature Service) | 6.2 (`services/verifica-firme/pom.xml`) | LGPL-2.1 | verifica-firme | R08 (D01): libreria della Commissione europea, usata senza modifiche. Non ancora costruita né provata (VD-C-143) |
| certifi | 2026.10.19 | MPL-2.0 | tutti i servizi Python (attraverso httpx e requests) | Elenco delle autorità di certificazione, non modificato |
| versitygw (Versity S3 Gateway) | 1.9.0 (binario della release, SHA-256 verificato) | Apache-2.0 | solo sviluppo e CI (storage S3 con Object Lock) | R05 (VD-C-115): sostituisce MinIO community |
| Siegfried | 1.11.1 (binario della release, SHA-256 verificato nel Dockerfile di validation) | Apache-2.0 | validation (riconoscimento PRONOM) | R08 (D02) |
| Firme PRONOM (DROID V118, `default.sig` di Siegfried) | 2024-06 | Open Government Licence v3 (The National Archives) | validation | Dato di riferimento esterno, con attribuzione |
| veraPDF | da installare | GPL-3.0-or-later OR MPL-2.0 (si sceglie MPL-2.0) | validation (PDF/A) | Non incluso: interfaccia pronta (`libs/shared/formati/pdfa.py`) |
| Schema XSD UNI 11386:2020 | da acquistare (D03, VD-N-01) | licenza d'uso UNI (norma a pagamento) | `libs/sincro/schemas/<versione>/` | **Non presente e da non pubblicare**: anche dopo l'acquisto va tenuto fuori dal repository pubblico salvo licenza UNI che ne consenta la ridistribuzione |
| defusedxml | 0.7.1 | PSF-2.0 | notification | Parser XML senza entità esterne |
| joserfc | 1.7.7 | BSD-3-Clause | gateway | Decifratura dello UserInfo SPID/CIE (JWE) |
| pyHanko, pyhanko-certvalidator, asn1crypto | 0.38.0, 0.33.0, 1.5.1 | MIT | signature, validation, ciclo-vita, notification, dashboard-api, hashchain | Verifica delle firme e delle buste CAdES |
| lxml | 6.1.3 | BSD-3-Clause | aip-builder, signature, validation, ciclo-vita | Validazione dell'IPdA contro lo schema |
| pyotp, webauthn | 2.10.0, 3.0.1 | MIT, BSD-3-Clause | gateway | Accesso con codice a tempo e passkey |
| opentelemetry-sdk, -api, -semantic-conventions | 1.45.1, 0.66b1 | Apache-2.0 | tutti i servizi Python | Tracce, spente per impostazione |
| @fontsource-variable/archivo | 5.3.0 | OFL-1.1 | frontend | Font della console (VD-DR-01) |
| @playwright/test, @axe-core/playwright | 1.56.1, 4.13.0 | Apache-2.0, MPL-2.0 | frontend, solo prove | Non inclusi nella console costruita |

## 4. Elenco completo delle dipendenze

### 4.1 Python — dipendenze di esercizio (79 pacchetti, chiusura transitiva)

| Pacchetto | Versione provata | Licenza (SPDX) | Compatibile con EUPL-1.2 | Usato da |
|---|---|---|---|---|
| aiohappyeyeballs | 2.7.1 | PSF-2.0 | sì (permissiva) | ciclo-vita, signature, validation |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT | sì (permissiva) | ciclo-vita, signature, validation |
| aiokafka | 0.14.0 | Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| aiosignal | 1.4.0 | Apache-2.0 | sì (permissiva) | ciclo-vita, signature, validation |
| aiosmtplib | 5.1.3 | MIT | sì (permissiva) | notification |
| alembic | 1.20.0 | MIT | sì (permissiva) | gateway, testimone |
| annotated-doc | 0.0.5 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| annotated-types | 0.8.0 | MIT | sì (permissiva) | tutti i servizi Python |
| anyio | 4.15.1 | MIT | sì (permissiva) | tutti i servizi Python |
| asn1crypto | 1.5.1 | MIT | sì (permissiva) | ciclo-vita, dashboard-api, hashchain, notification, signature, validation |
| async-timeout | 5.0.1 | Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| asyncpg | 0.32.0 | Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| asyncssh | 2.24.1 | EPL-2.0 OR GPL-2.0-or-later | sì (copyleft debole; si applica EPL-2.0, dipendenza non modificata) | sftp-ingest |
| attrs | 26.1.0 | MIT | sì (permissiva) | ciclo-vita, signature, validation |
| aws-msk-iam-sasl-signer-python | 1.0.2 | Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| bcrypt | 5.0.0 | Apache-2.0 | sì (permissiva) | gateway |
| boto3 | 1.43.115 | Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| botocore | 1.43.115 | Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| cbor2 | 6.1.5 | MIT | sì (permissiva) | gateway |
| certifi | 2026.10.19 | MPL-2.0 | sì (copyleft debole, in appendice EUPL; dipendenza non modificata) | tutti i servizi Python |
| cffi | 2.1.1 | MIT-0 | sì (permissiva) | ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| click | 8.5.0 | BSD-3-Clause | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | sì (permissiva) | ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| defusedxml | 0.7.1 | PSF-2.0 | sì (permissiva) | ciclo-vita, notification |
| fastapi | 0.155.0 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| frozenlist | 1.8.0 | Apache-2.0 | sì (permissiva) | ciclo-vita, signature, validation |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, hashchain, notification, onboarding, sftp-ingest, signature, validation |
| h11 | 0.16.0 | MIT | sì (permissiva) | tutti i servizi Python |
| hiredis | 3.5.0 | MIT | sì (permissiva) | ciclo-vita, dashboard-api, distribution, gateway, notification, signature |
| httpcore | 1.0.9 | BSD-3-Clause | sì (permissiva) | tutti i servizi Python |
| httptools | 0.9.0 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| httpx | 0.28.1 | BSD-3-Clause | sì (permissiva) | tutti i servizi Python |
| idna | 3.20 | BSD-3-Clause | sì (permissiva) | tutti i servizi Python |
| jmespath | 1.1.0 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| joserfc | 1.7.7 | BSD-3-Clause | sì (permissiva) | gateway |
| lxml | 6.1.3 | BSD-3-Clause | sì (permissiva) | aip-builder, ciclo-vita, signature, validation |
| Mako | 1.4.3 | MIT | sì (permissiva) | gateway, testimone |
| MarkupSafe | 3.0.4 | BSD-3-Clause | sì (permissiva) | gateway, testimone |
| multidict | 7.1.0 | Apache-2.0 | sì (permissiva) | ciclo-vita, signature, validation |
| opentelemetry-api | 1.45.1 | Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| opentelemetry-sdk | 1.45.1 | Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| opentelemetry-semantic-conventions | 0.66b1 | Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| oscrypto | 1.3.0 | MIT | sì (permissiva) | ciclo-vita, signature, validation |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| prometheus_client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| propcache | 0.5.4 | Apache-2.0 | sì (permissiva) | ciclo-vita, signature, validation |
| pyasn1 | 0.6.4 | BSD-2-Clause | sì (permissiva) | gateway |
| pyasn1_modules | 0.4.2 | BSD-3-Clause | sì (permissiva) | gateway |
| pycparser | 3.11 | BSD-3-Clause | sì (permissiva) | ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| pydantic | 2.14.1 | MIT | sì (permissiva) | tutti i servizi Python |
| pydantic_core | 2.50.1 | MIT | sì (permissiva) | tutti i servizi Python |
| pydantic-settings | 2.16.1 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| pyHanko | 0.38.0 | MIT | sì (permissiva) | ciclo-vita, signature, validation |
| pyhanko-certvalidator | 0.33.0 | MIT | sì (permissiva) | ciclo-vita, signature, validation |
| PyJWT | 2.15.1 | MIT | sì (permissiva) | ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature |
| pyOpenSSL | 26.4.0 | Apache-2.0 | sì (permissiva) | gateway |
| PyOTP | 2.10.0 | MIT | sì (permissiva) | gateway |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| python-dotenv | 1.2.4 | BSD-3-Clause | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| python-multipart | 0.0.32 | Apache-2.0 | sì (permissiva) | ciclo-vita, gateway |
| python-pkcs11 | 0.10.1 | MIT | sì (permissiva) | signature |
| PyYAML | 6.0.3 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| redis | 8.1.0 | MIT | sì (permissiva) | ciclo-vita, dashboard-api, distribution, gateway, notification, signature |
| s3transfer | 0.19.3 | Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| six | 1.17.0 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| SQLAlchemy | 2.1.5 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| starlette | 1.9.0 | BSD-3-Clause | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| structlog | 26.1.0 | MIT OR Apache-2.0 | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| typing_extensions | 4.16.0 | PSF-2.0 | sì (permissiva) | tutti i servizi Python |
| typing-inspection | 0.4.4 | MIT | sì (permissiva) | tutti i servizi Python |
| tzlocal | 5.4.4 | MIT | sì (permissiva) | ciclo-vita, signature, validation |
| uritools | 6.1.3 | MIT | sì (permissiva) | ciclo-vita, signature, validation |
| urllib3 | 2.8.0 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, validation |
| uvicorn | 0.54.0 | BSD-3-Clause | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| uvloop | 0.23.0 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| watchfiles | 1.3.0 | MIT | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| webauthn | 3.0.1 | BSD-3-Clause | sì (permissiva) | gateway |
| websockets | 17.3 | BSD-3-Clause | sì (permissiva) | aip-builder, ciclo-vita, dashboard-api, distribution, gateway, hashchain, notification, onboarding, sftp-ingest, signature, testimone, validation |
| yarl | 1.25.1 | Apache-2.0 | sì (permissiva) | ciclo-vita, signature, validation |

### 4.2 Python — solo sviluppo e prove (52 pacchetti, non distribuiti nelle immagini)

| Pacchetto | Versione provata | Licenza (SPDX) | Compatibile |
|---|---|---|---|
| aiosmtpd | 1.4.6 | Apache-2.0 | sì (permissiva) |
| antlr4-python3-runtime | 4.13.2 | BSD-3-Clause | sì (permissiva) |
| ast_serialize | 0.13.1 | MIT | sì (permissiva) |
| atpublic | 9.0.0 | Apache-2.0 | sì (permissiva) |
| aws-xray-sdk | 2.15.0 | Apache-2.0 | sì (permissiva) |
| bandit | — | Apache-2.0 (dai metadati pubblici; non installato nell'ambiente di prova) | sì (permissiva) |
| blinker | 1.9.0 | MIT | sì (permissiva) |
| cfn-lint | 1.57.2 | MIT-0 | sì (permissiva) |
| charset-normalizer | 3.5.2 | MIT | sì (permissiva) |
| coverage | 7.16.2 | Apache-2.0 | sì (permissiva) |
| docker | 7.2.0 | Apache-2.0 | sì (permissiva) |
| Flask | 3.1.3 | BSD-3-Clause | sì (permissiva) |
| flask-cors | 6.0.5 | MIT | sì (permissiva) |
| graphql-core | 3.3.0 | MIT | sì (permissiva) |
| iniconfig | 2.3.1 | MIT | sì (permissiva) |
| itsdangerous | 2.2.0 | BSD-3-Clause | sì (permissiva) |
| Jinja2 | 3.1.6 | BSD-3-Clause | sì (permissiva) |
| jsonpatch | 1.36 | BSD-3-Clause | sì (permissiva) |
| jsonpath-ng | 1.11.0 | Apache-2.0 | sì (permissiva) |
| jsonpointer | 3.4 | BSD-3-Clause | sì (permissiva) |
| jsonschema | 4.26.0 | MIT | sì (permissiva) |
| jsonschema-path | 0.5.0 | Apache-2.0 | sì (permissiva) |
| jsonschema-specifications | 2025.9.1 | MIT | sì (permissiva) |
| lazy-object-proxy | 1.13.0 | BSD-2-Clause | sì (permissiva) |
| librt | 0.16.0 | MIT | sì (permissiva) |
| moto | 5.2.4 | Apache-2.0 | sì (permissiva) |
| mpmath | 1.3.0 | BSD-3-Clause | sì (permissiva) |
| mypy | 2.4.0 | MIT | sì (permissiva) |
| mypy_extensions | 1.1.0 | MIT | sì (permissiva) |
| networkx | 3.7 | BSD-3-Clause | sì (permissiva) |
| openapi-schema-validator | 0.9.0 | BSD-3-Clause | sì (permissiva) |
| openapi-spec-validator | 0.9.0 | Apache-2.0 | sì (permissiva) |
| pathable | 0.6.0 | Apache-2.0 | sì (permissiva) |
| pathspec | 1.1.1 | MPL-2.0 | sì (copyleft debole, in appendice EUPL; dipendenza non modificata) |
| pluggy | 1.7.0 | MIT | sì (permissiva) |
| py-partiql-parser | 0.6.3 | MIT | sì (permissiva) |
| Pygments | 2.21.0 | BSD-2-Clause | sì (permissiva) |
| pyparsing | 3.3.3 | MIT | sì (permissiva) |
| pytest | 9.1.1 | MIT | sì (permissiva) |
| pytest-asyncio | 1.4.0 | Apache-2.0 | sì (permissiva) |
| pytest-cov | 7.1.0 | MIT | sì (permissiva) |
| referencing | 0.37.0 | MIT | sì (permissiva) |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python | sì (permissiva) |
| requests | 2.34.2 | Apache-2.0 | sì (permissiva) |
| responses | 0.26.3 | Apache-2.0 | sì (permissiva) |
| rfc3339-validator | 0.1.4 | MIT | sì (permissiva) |
| rpds-py | 2026.9.1 | MIT | sì (permissiva) |
| ruff | 0.17.1 | MIT | sì (permissiva) |
| sympy | 1.14.0 | BSD-3-Clause | sì (permissiva) |
| Werkzeug | 3.1.9 | BSD-3-Clause | sì (permissiva) |
| wrapt | 2.5.1 | BSD-2-Clause | sì (permissiva) |
| xmltodict | 1.0.4 | MIT | sì (permissiva) |

### 4.3 npm — frontend, dipendenze incluse nel pacchetto della console (73 pacchetti, da `frontend/package-lock.json`)

| Pacchetto | Versione | Licenza (SPDX) | Compatibile |
|---|---|---|---|
| @babel/runtime | 7.29.2 | MIT | sì (permissiva) |
| @fontsource-variable/archivo | 5.3.0 | OFL-1.1 | sì (font, resta sotto OFL-1.1) |
| @remix-run/router | 1.23.2 | MIT | sì (permissiva) |
| @tanstack/query-core | 5.91.0 | MIT | sì (permissiva) |
| @tanstack/react-query | 5.91.0 | MIT | sì (permissiva) |
| @types/d3-array | 3.2.2 | MIT | sì (permissiva) |
| @types/d3-color | 3.1.3 | MIT | sì (permissiva) |
| @types/d3-ease | 3.0.2 | MIT | sì (permissiva) |
| @types/d3-interpolate | 3.0.4 | MIT | sì (permissiva) |
| @types/d3-path | 3.1.1 | MIT | sì (permissiva) |
| @types/d3-scale | 4.0.9 | MIT | sì (permissiva) |
| @types/d3-shape | 3.1.8 | MIT | sì (permissiva) |
| @types/d3-time | 3.0.4 | MIT | sì (permissiva) |
| @types/d3-timer | 3.0.2 | MIT | sì (permissiva) |
| asynckit | 0.4.0 | MIT | sì (permissiva) |
| axios | 1.13.6 | MIT | sì (permissiva) |
| call-bind-apply-helpers | 1.0.2 | MIT | sì (permissiva) |
| clsx | 2.1.1 | MIT | sì (permissiva) |
| combined-stream | 1.0.8 | MIT | sì (permissiva) |
| csstype | 3.2.3 | MIT | sì (permissiva) |
| d3-array | 3.2.4 | ISC | sì (permissiva) |
| d3-color | 3.1.0 | ISC | sì (permissiva) |
| d3-ease | 3.0.1 | BSD-3-Clause | sì (permissiva) |
| d3-format | 3.1.2 | ISC | sì (permissiva) |
| d3-interpolate | 3.0.1 | ISC | sì (permissiva) |
| d3-path | 3.1.0 | ISC | sì (permissiva) |
| d3-scale | 4.0.2 | ISC | sì (permissiva) |
| d3-shape | 3.2.0 | ISC | sì (permissiva) |
| d3-time | 3.1.0 | ISC | sì (permissiva) |
| d3-time-format | 4.1.0 | ISC | sì (permissiva) |
| d3-timer | 3.0.1 | ISC | sì (permissiva) |
| decimal.js-light | 2.5.1 | MIT | sì (permissiva) |
| delayed-stream | 1.0.0 | MIT | sì (permissiva) |
| dom-helpers | 5.2.1 | MIT | sì (permissiva) |
| dunder-proto | 1.0.1 | MIT | sì (permissiva) |
| es-define-property | 1.0.1 | MIT | sì (permissiva) |
| es-errors | 1.3.0 | MIT | sì (permissiva) |
| es-object-atoms | 1.1.1 | MIT | sì (permissiva) |
| es-set-tostringtag | 2.1.0 | MIT | sì (permissiva) |
| eventemitter3 | 4.0.7 | MIT | sì (permissiva) |
| fast-equals | 5.4.0 | MIT | sì (permissiva) |
| follow-redirects | 1.15.11 | MIT | sì (permissiva) |
| form-data | 4.0.5 | MIT | sì (permissiva) |
| function-bind | 1.1.2 | MIT | sì (permissiva) |
| get-intrinsic | 1.3.0 | MIT | sì (permissiva) |
| get-proto | 1.0.1 | MIT | sì (permissiva) |
| gopd | 1.2.0 | MIT | sì (permissiva) |
| has-symbols | 1.1.0 | MIT | sì (permissiva) |
| has-tostringtag | 1.0.2 | MIT | sì (permissiva) |
| hasown | 2.0.2 | MIT | sì (permissiva) |
| internmap | 2.0.3 | ISC | sì (permissiva) |
| js-tokens | 4.0.0 | MIT | sì (permissiva) |
| lodash | 4.17.23 | MIT | sì (permissiva) |
| loose-envify | 1.4.0 | MIT | sì (permissiva) |
| math-intrinsics | 1.1.0 | MIT | sì (permissiva) |
| mime-db | 1.52.0 | MIT | sì (permissiva) |
| mime-types | 2.1.35 | MIT | sì (permissiva) |
| object-assign | 4.1.1 | MIT | sì (permissiva) |
| prop-types | 15.8.1 | MIT | sì (permissiva) |
| proxy-from-env | 1.1.0 | MIT | sì (permissiva) |
| react | 18.3.1 | MIT | sì (permissiva) |
| react-dom | 18.3.1 | MIT | sì (permissiva) |
| react-is | 16.13.1 | MIT | sì (permissiva) |
| react-is | 18.3.1 | MIT | sì (permissiva) |
| react-router | 6.30.3 | MIT | sì (permissiva) |
| react-router-dom | 6.30.3 | MIT | sì (permissiva) |
| react-smooth | 4.0.4 | MIT | sì (permissiva) |
| react-transition-group | 4.4.5 | BSD-3-Clause | sì (permissiva) |
| recharts | 2.15.4 | MIT | sì (permissiva) |
| recharts-scale | 0.4.5 | MIT | sì (permissiva) |
| scheduler | 0.23.2 | MIT | sì (permissiva) |
| tiny-invariant | 1.3.3 | MIT | sì (permissiva) |
| victory-vendor | 36.9.2 | MIT AND ISC | sì (permissiva) |

### 4.4 npm — frontend, solo sviluppo, build e prove (232 pacchetti)

| Licenza (SPDX) | Pacchetti | Compatibile | Note |
|---|---|---|---|
| MIT | 189 pacchetti (elenco completo in `frontend/package-lock.json`) | sì (permissiva) |  |
| Apache-2.0 | 17 pacchetti (elenco completo in `frontend/package-lock.json`) | sì (permissiva) |  |
| ISC | 14 pacchetti (elenco completo in `frontend/package-lock.json`) | sì (permissiva) |  |
| BSD-2-Clause | eslint-scope@8.4.0, espree@10.4.0, esrecurse@4.3.0, estraverse@5.3.0, esutils@2.0.3, uri-js@4.4.1 | sì (permissiva) |  |
| MPL-2.0 | @axe-core/playwright@4.13.0, axe-core@4.13.0 | sì (copyleft debole, in appendice EUPL; dipendenza non modificata) | non incluso nella console costruita |
| BSD-3-Clause | esquery@1.7.0, source-map-js@1.2.1 | sì (permissiva) |  |
| Python-2.0 | argparse@2.0.1 | sì (permissiva) |  |
| CC-BY-4.0 | caniuse-lite@1.0.30001780 | sì (dati, attribuzione; solo sviluppo) | non incluso nella console costruita |

## 5. Java — servizio verifica-firme

Dipendenze dirette dal `pom.xml` (versioni bloccate). Gli artefatti transitivi non sono stati risolti in R18 (Maven Central non raggiungibile dall'ambiente di lavoro): **prima della prima immagine pubblica** generare l'elenco completo con

```
cd services/verifica-firme
mvn org.codehaus.mojo:license-maven-plugin:2.4.0:add-third-party -Dlicense.excludedScopes=test,system
```

e riportarlo qui. Licenze attese dei transitivi principali (dai progetti a monte, da confermare con lo strumento): Tomcat, Jackson, Micrometer, PDFBox, Apache Santuario e Commons (Apache-2.0); Bouncy Castle (licenza MIT-like di Bouncy Castle); SLF4J (MIT); Logback (EPL-1.0 OR LGPL-2.1); API Jakarta (EPL-2.0 OR GPL-2.0 con Classpath Exception). Tutte compatibili.

| Componente | Versione | Licenza | Compatibile |
|---|---|---|---|
| org.springframework.boot (starter-web, starter-actuator, parent) | 3.3.5 | Apache-2.0 | sì (permissiva) |
| eu.europa.ec.joinup.sd-dss (dss-bom e moduli validation, cades, pades, pades-pdfbox, xades, asic-cades, asic-xades, tsl-validation, service, utils-apache-commons, crl-parser-x509crl) | 6.2 | LGPL-2.1 | sì (copyleft debole, in appendice EUPL; dipendenza non modificata) |
| com.nimbusds:nimbus-jose-jwt | 9.41.2 | Apache-2.0 | sì (permissiva) |
| spring-boot-starter-test (solo prove) | 3.3.5 | Apache-2.0 | sì (non distribuita) |

## 6. Binari, dati e font fuori dai gestori di pacchetti

| Componente | Licenza | Dove | Compatibile |
|---|---|---|---|
| Siegfried 1.11.1 (Go) | Apache-2.0 | immagine di validation | sì |
| Firme PRONOM DROID V118 | OGL-UK-3.0 | immagine di validation | sì (dato, con attribuzione a The National Archives) |
| versitygw 1.9.0 (Go) | Apache-2.0 | solo sviluppo e CI | sì |
| veraPDF (non incluso) | GPL-3.0-or-later OR MPL-2.0 | da installare | sì (MPL-2.0) |
| Schema XSD UNI 11386:2020 (non incluso) | licenza d'uso UNI | da acquistare | **non ridistribuibile**: fuori dal repository pubblico |
| Font Archivo (frontend) | OFL-1.1 | console costruita | sì |
| Font DM Mono, DM Sans, Instrument Serif (`tools/design/foto_visive`, solo foto di verifica) | OFL-1.1 | solo sviluppo | sì |
| Playwright 1.64.1 (`tools/design/foto_visive`) | Apache-2.0 | solo sviluppo | sì |

Nessun componente Go è compilato dal repository (non c'è un `go.mod`): Go serve solo, facoltativo, all'oracolo di interoperabilità `libs/catena/tests/test_oracolo_go.py`, che usa una copia locale di `golang.org/x/mod` (BSD-3-Clause) non inclusa.

## 7. Vettori di prova di terzi inclusi nei test

| File | Origine | Licenza |
|---|---|---|
| `libs/catena/tests/test_nota.py` | esempio della documentazione di `golang.org/x/mod/sumdb/note` (chiave d'esempio pubblica del progetto Go) | BSD-3-Clause, © The Go Authors |
| `libs/catena/tests/test_rfc6962.py` | radici e prove di riferimento della Certificate Transparency (RFC 6962; stessi valori di transparency-dev/merkle e google/certificate-transparency) | Apache-2.0 (dati di prova) |
| `libs/shared/security/tests/test_r04_rotazione_jwt.py`, `test_r13b_oidc_federazione.py` | chiave RSA d'esempio di RFC 7638 §3.1 / RFC 7517 | IETF Trust, componenti di codice BSD-3-Clause |

Sono dati di prova pubblici, non chiavi in uso: nessuna chiave reale è nel repository (le chiavi delle prove si generano durante l'esecuzione).

## 8. Immagini di container dell'ambiente di sviluppo locale

Usate solo da `docker-compose.yml` e `infra/observability/docker-compose.observability.yml` sul computer di chi sviluppa; non fanno parte del software distribuito né delle immagini dei servizi.

| Immagine | Licenza | Esito | Alternativa |
|---|---|---|---|
| postgres:16-alpine | PostgreSQL License | sì | — |
| python:3.12-slim (base delle immagini dei servizi) | PSF-2.0 e pacchetti Debian | sì | — |
| node:20.18-alpine (solo costruzione della console) | MIT e pacchetti Alpine | sì | — |
| nginxinc/nginx-unprivileged:1.27-alpine (console) | BSD-2-Clause | sì | — |
| eclipse-temurin:21 JRE, maven:3.9.9 (verifica-firme) | GPL-2.0 con Classpath Exception, Apache-2.0 | sì | — |
| alpine:3.20/3.21 (pgbouncer, testimone) | MIT e pacchetti Alpine (pgbouncer ISC) | sì | — |
| opensearchproject/opensearch:2.11.0 | Apache-2.0 | sì | — |
| prom/prometheus:v2.52.0 | Apache-2.0 | sì | — |
| grafana/grafana:10.4.0, grafana/loki:2.9.0, grafana/tempo:2.4.0 | AGPL-3.0 | sì: programmi separati, solo osservabilità locale, AGPL-3.0 è nell'appendice EUPL | — |
| axllent/mailpit:v1.20 | MIT | sì | — |
| **hashicorp/vault:1.16** | **BUSL-1.1** (non open source) | **da rivedere** | `openbao/openbao` (MPL-2.0), API compatibile |
| **redis:7-alpine** | dalla 7.4 **RSALv2 / SSPLv1** (non open source; dalla 8.0 anche AGPL-3.0) | **da rivedere** | fissare `redis:7.2-alpine` (BSD-3-Clause) o `valkey/valkey:8-alpine` (BSD-3-Clause) |
| **confluentinc/cp-kafka:7.6.0** | Apache Kafka è Apache-2.0; l'immagine è distribuita da Confluent con i propri termini | **da verificare** | `apache/kafka:3.7.0` (Apache-2.0) |

In esercizio Redis è il servizio gestito ElastiCache con motore 7.1 (`infra/terraform/modules/elasticache`), non distribuito con VerumDoc.

## 9. Come si aggiorna

Ogni dipendenza nuova o aggiornata si annota qui nello stesso rilascio (REGOLE_SVILUPPO §20.8), con licenza verificata e compatibilità con la EUPL-1.2. Non si aggiungono dipendenze con licenza sconosciuta, proprietaria, SSPL, BUSL o simili «source available»: in quel caso si propone un'alternativa nel registro delle decisioni.
