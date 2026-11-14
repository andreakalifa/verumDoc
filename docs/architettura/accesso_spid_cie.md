# Accesso con SPID e CIE tramite aggregatore (R13b, VD-C-069)

Stato: codice completo e provato con il simulatore `tools/simulatore_oidc`; manca il
contratto con un aggregatore (decisione commerciale D08). Questo documento dice che cosa
fa l'adattatore, che cosa prevedono le regole pubbliche note e che cosa resta **da
verificare** con l'aggregatore scelto. Nessun requisito qui sotto è inventato: dove la
fonte non è stata letta integralmente da questo ambiente è scritto «da verificare».

## 1. Modello

VerumDoc non aderisce direttamente alla federazione SPID/CIE: usa un **aggregatore** che
è l'entità della federazione verso i gestori d'identità e verso VerumDoc si comporta da
OpenID Provider. Il gateway (`services/gateway/app/auth/spid.py`, `oidc.py`) è il cliente
OIDC; la configurazione (senza segreti) si registra in console come fornitore d'identità
`SPID_CIE` (onboarding, `fornitori_identita`), i segreti stanno nell'ambiente del gateway.

## 2. Che cosa fa l'adattatore

| Aspetto | Comportamento | Dove |
|---|---|---|
| Flusso | Authorization Code con `state` monouso legato al cookie del browser, `nonce`, ritorno al frontend con codice monouso nel frammento | `spid.py` (R02, VD-C-006) |
| PKCE | **sempre** `code_challenge` S256 e `code_verifier` allo scambio (RFC 7636) | `oidc.coppia_pkce` |
| Autenticazione del cliente | `client_secret_post` (predefinito), `client_secret_basic`, `private_key_jwt` (RFC 7523: iss = sub = client_id, aud = endpoint del token, `jti` monouso, durata 300 s) | `spid.scambia_codice`, `oidc.asserzione_cliente` |
| Request object | facoltativo (console `request_object`): JWT firmato RFC 9101 con tutti i parametri, `typ` `oauth-authz-req+jwt` | `oidc.oggetto_richiesta` |
| `prompt` | facoltativo (`login`, `consent`, `consent login`) | console |
| Livelli | `acr_values` = `https://www.spid.gov.it/SpidL2` o `L3` (mai sotto il minimo della console, mai L1); il livello che conta è l'`acr` dell'id_token verificato (assente → SPID-009) | `spid.livello_da_acr` (R02) |
| id_token | firma con chiave del JWKS scelta per `kid` (RS256, PS256, ES256), `iss`, `aud`, `exp`, `iat`, `sub`, `nonce` | `verifica_id_token` |
| Attributi | codice fiscale dai claim `https://attributes.eid.gov.it/fiscal_number` (o forme brevi), prefisso `TINIT-` tolto e validato; con `attributi_da_userinfo` si chiede con `claims` (essenziale) e si legge dallo **UserInfo** | `codice_fiscale_dai_claim`, `leggi_userinfo` |
| UserInfo | JSON, JWS firmato dall'OP o JWE (RSA-OAEP/RSA-OAEP-256, ECDH-ES; A128CBC-HS256, A256CBC-HS512, AES-GCM) che contiene il JWS; `iss`, `aud` e **`sub` uguale a quello dell'id_token** (OIDC Core §5.3.2) | `oidc.decodifica_userinfo` |
| Federazione OpenID | facoltativa: chiavi dell'OP dalla **trust chain** verificata fino al Trust Anchor, con le chiavi del Trust Anchor FISSATE in console (mai scaricate). A ogni livello le chiavi di federazione di un'entità sono SOLO quelle del Subordinate Statement del superiore: la sua configurazione e le dichiarazioni che emette si verificano con quelle, confrontate per thumbprint RFC 7638 (non per solo `kid`). Indirizzi di federazione solo https verso `SPID_HOST_AMMESSI` (obbligatorio fuori sviluppo), niente redirect, al più 64 KiB per risposta | `libs/shared/security/oidc_federazione.py` |
| Segreti legati agli host | `SPID_CLIENT_SECRET`, le asserzioni e l'access token vanno SOLO agli host di `SPID_HOST_AMMESSI` (fissati al deploy; in produzione il gateway non parte con un segreto e l'elenco vuoto) | `oidc.host_ammesso`, `oidc.problemi_avvio` |

Predefiniti = comportamento di prima (§8.14): senza modifiche in console l'aggregatore già
configurato funziona come in R10, con in più PKCE.

## 3. Confronto con le regole pubbliche

Fonti pubbliche di riferimento: OpenID Connect Core 1.0; RFC 7636, 7523, 9101; OpenID
Federation 1.0; Linee guida AgID «OpenID Connect in SPID» e relativi avvisi (estremi e versione
vigente **da verificare** sul sito AgID prima dell'attivazione); regole tecniche **CIE OIDC** del
Ministero dell'Interno (manuale operativo e documentazione tecnica pubblica).

| Requisito (fonte) | Adattatore | Note |
|---|---|---|
| PKCE con S256 (SPID OIDC, CIE OIDC) | sì, sempre | — |
| `private_key_jwt` al token endpoint (SPID OIDC, CIE OIDC per i RP federati) | sì, configurabile | con l'aggregatore il metodo lo stabilisce il suo contratto: **da verificare** |
| Request object firmato nella richiesta di autenticazione (SPID OIDC) | sì, configurabile | idem, **da verificare** |
| `acr_values` SpidL2/SpidL3 e controllo dell'`acr` restituito | sì | per la CIE i valori del livello sono **da verificare** con l'aggregatore (il codice accetta gli URI SpidL1–3) |
| Attributo codice fiscale `fiscal_number` | sì | richiesta con `claims`; formato `TINIT-` gestito |
| UserInfo firmato e cifrato (SPID OIDC) | sì | algoritmi esatti di cifratura **da verificare** (supportati quelli elencati al §2) |
| Federazione: Entity Configuration, Subordinate Statement, trust chain fino al Trust Anchor | sì, facoltativa | R16 (VD-C-173, §3.1): applicati anche `metadata_policy` (con `metadata_policy_crit`), `constraints`, `crit`, `trust_marks` richiesti dalla console con stato (revoca) facoltativo, scadenze di tutte le dichiarazioni |
| Entity Configuration del RELYING PARTY pubblicata in `/.well-known/openid-federation` | **no** | serve solo all'adesione diretta (RP federato), non con l'aggregatore: fuori perimetro (VD-P-151) |
| `prompt=consent login` (SPID OIDC) | configurabile | — |
| Conservazione dei log degli accessi (Regolamento SPID) | audit e log del gateway | durata e contenuto **da verificare** con l'aggregatore (spesso li tiene lui) |

### 3.1 Federazione completa (R16, VD-C-173)

`libs/shared/security/oidc_federazione.risolvi` (chiamata dal gateway in `jwks_op`) sulla
catena già verificata (chiavi solo dal superiore, thumbprint RFC 7638, host ammessi):

- **`crit`**: un claim critico non supportato invalida la catena;
- **`constraints`** di ogni Subordinate Statement: `max_path_length` (intermedi fra chi
  pone il vincolo e la foglia), `naming_constraints` (`permitted`/`excluded` sugli host,
  `host.example` esatto, `.example` sottodomini, come RFC 5280) su tutte le entità sotto
  chi pone il vincolo, `allowed_entity_types` (il tipo dell'OP escluso = catena non valida);
- **`metadata_policy`** (`libs/shared/security/oidc_politiche.py`): voci combinate dal
  Trust Anchor verso il basso (value e default uguali, add e superset_of unione, one_of
  intersezione non vuota, subset_of intersezione, essential in «o»; combinazioni vietate
  rifiutate), applicate ai metadati dell'OP dopo i `metadata` del superiore diretto
  nell'ordine value, add, default, one_of, subset_of, superset_of, essential; operatore
  sconosciuto ignorato salvo `metadata_policy_crit`;
- **`trust_marks`** solo se la console li richiede (`federazione.trust_marks_richiesti`:
  `{"<tipo https>": ["<emittente https>", …]}`): firma (`trust-mark+jwt`) verificata con le
  chiavi della catena dell'emittente verso lo stesso Trust Anchor (o con le chiavi fissate
  se è il Trust Anchor), emittente fra quelli ammessi dalla console e, se il Trust Anchor
  li dichiara, da `trust_mark_issuers`; delega del proprietario (`trust_mark_owners`);
  `sub` uguale all'OP, tipo, `iat`, `exp`; con `federazione.verifica_stato_trust_marks`
  lo stato chiesto all'endpoint `federation_trust_mark_status_endpoint` dell'emittente deve
  essere «active» (risposta firmata dall'emittente o JSON);
- **revoca e scadenza**: `exp` di ogni dichiarazione e dei trust mark (la catena scade alla
  più vicina, la cache del gateway non la supera); un Subordinate Statement ritirato dal
  superiore (risposta diversa da 200) invalida la catena al successivo scaricamento.

Prove: `libs/shared/security/tests/test_r16_oidc_federazione.py` (44 casi, quasi tutti
negativi). Scelte: VD-P-167.

## 4. Checklist «attivazione aggregatore»

### 4.1 Da chiedere all'aggregatore
1. Documentazione tecnica dell'interfaccia OIDC verso gli aggregati: indirizzo dell'emittente
   (`issuer`), endpoint di autorizzazione, token, UserInfo, JWKS (o Entity Configuration).
2. Metodo di autenticazione del cliente (`client_secret_*` o `private_key_jwt`); se
   `private_key_jwt`, come si registra la chiave pubblica del cliente (JWKS con `kid`).
3. Se esige request object firmato e `prompt`.
4. Dove consegna il codice fiscale (id_token o UserInfo), se lo UserInfo è firmato e/o
   cifrato e con quali algoritmi.
5. Valori di `acr` per SPID e per CIE e livello minimo offerto (L2, L3).
6. Indirizzi di ritorno da registrare: `https://app.verumdoc.it/api/v1/auth/spid/callback`
   (BASE_URL del gateway) e politica di logout.
7. Host e IP da cui risponde (per `SPID_HOST_AMMESSI` e per l'uscita della NetworkPolicy).
8. Ambiente di collaudo con identità di prova SPID e CIE.

### 4.2 Parametri
- Console → «Accesso e notifiche» → fornitore SPID/CIE: `aggregatore_url`, `issuer`,
  `jwks_url` (o `federazione`), `client_id`, `livello_minimo`, `metodo_autenticazione`,
  `request_object`, `attributi_da_userinfo`, `prompt`, eventuali `authorize_url`,
  `token_url`, `userinfo_url`.
- Secret `gateway-secrets`: `SPID_CLIENT_SECRET` (se `client_secret_*`),
  `SPID_CHIAVE_PRIVATA_CLIENT` (PEM RSA o EC P-256, se `private_key_jwt`, request object o
  UserInfo cifrato).
- ConfigMap del gateway: `SPID_HOST_AMMESSI` (host dell'aggregatore), `SPID_CHIAVE_CLIENT_KID`.
- Parametro `ACCESSO_METODI_AMMESSI` con `SPID_CIE` per gli enti.

### 4.3 Prove
1. `python -m pytest services/gateway/tests/test_r13b_spid_oidc.py` (simulatore) verde.
2. In collaudo: accesso SPID L2 con identità di prova → token VerumDoc; L3 richiesto in
   console → accesso L2 rifiutato (SPID-009).
3. Accesso CIE con identità di prova.
4. Codice fiscale non associato a utenti → SPID-008; persona in due enti → scelta dell'ente.
5. Log del gateway: nessun segreto, codice fiscale mascherato.
6. Prova negativa: `token_url` in console verso un host non in `SPID_HOST_AMMESSI` →
   SPID-004 e nessuna richiesta all'host.

## 5. Simulatore

`tools/simulatore_oidc` (FastAPI): `python -c "import uvicorn; from tools.simulatore_oidc.app import SimulatoreOidc, crea_app; uvicorn.run(crea_app(SimulatoreOidc(url='http://127.0.0.1:8098', client_secret='s')), port=8098)"`.
Copre PKCE obbligatorio, tre metodi del cliente, request object, UserInfo firmato e cifrato,
federazione con Trust Anchor, e le leve negative (sub diverso, firma estranea, `acr`
assente, Trust Anchor che non attesta l'OP). Solo sviluppo e test.
