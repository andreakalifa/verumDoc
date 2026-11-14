# Rotazione delle chiavi JWT (RS256) senza disconnettere gli utenti

Riferimenti: VD-C-085 (R04), REGOLE_SVILUPPO §19.1 e §19.3, `libs/shared/security/jwt_rs256.py`.

## Come funziona

- Il **gateway** firma i token di sessione con **una** chiave privata, `JWT_PRIVATE_KEY`.
  I token di servizio (`SIGNATURE_SERVICE_TOKEN` di hashchain) si emettono con la stessa
  chiave (`python -m libs.shared.security.emetti_token_servizio`).
- Ogni servizio che verifica i token (gateway, dashboard-api, distribution, onboarding,
  hashchain, notification, signature) legge `JWT_PUBLIC_KEY`, che può contenere **più
  chiavi pubbliche PEM una dopo l'altra** (il «mazzo», cioè il JWKS interno). Un token
  firmato con una qualsiasi chiave del mazzo è valido.
- Ogni chiave ha un identificativo `kid` = impronta JWK RFC 7638 (SHA-256 in base64url).
  Si **calcola** dalla chiave: non si configura e non si può sbagliare.
- `firma_token` scrive nell'intestazione del token il `kid` della chiave privata in uso;
  la verifica sceglie la chiave dal `kid`. Un `kid` che non è nel mazzo è rifiutato
  (`AUTH-001`, nel log `kid sconosciuto`). I token senza `kid` (emessi prima di R04) si
  provano con tutte le chiavi del mazzo.
- La chiave pubblica della chiave privata in uso **deve** stare nel mazzo del gateway:
  altrimenti il gateway non parte (`JWT_PRIVATE_KEY e JWT_PUBLIC_KEY non sono una coppia`).

Strumenti (non stampano mai chiavi private):

```bash
# kid di ogni chiave del mazzo, in formato JWKS (RFC 7517)
JWT_PUBLIC_KEY="$(cat mazzo.pem)" python -m libs.shared.security.jwt_rs256 --jwks
# kid della chiave privata in uso
JWT_PRIVATE_KEY="$(cat privata.pem)" python -m libs.shared.security.jwt_rs256 --kid
```

Nei Secret il PEM può stare su una riga con `\n` letterali (come oggi): il mazzo è la
concatenazione dei blocchi `-----BEGIN PUBLIC KEY----- … -----END PUBLIC KEY-----`.

## Durate da rispettare

| Token | Durata massima | Effetto sulla rotazione |
|---|---|---|
| Sessione utente (gateway) | `JWT_EXPIRY_SECONDS`, predefinito 3600 s, più 30 s di tolleranza | la vecchia chiave resta nel mazzo almeno 1 h e 1 min dopo il cambio della privata |
| Token di servizio (`SIGNATURE_SERVICE_TOKEN`) | fino a 90 giorni | va **riemesso** con la chiave nuova prima di togliere la vecchia dal mazzo |
| Token di canale SFTP | 1 h, chiave propria di sftp-ingest | non riguarda questa rotazione |

## Procedura (quattro passi, nessuno disconnesso)

1. **Generare la coppia nuova** in un ambiente sicuro (secret manager, mai nel repository):
   ```bash
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out nuova_privata.pem
   openssl pkey -in nuova_privata.pem -pubout -out nuova_pubblica.pem
   ```
2. **Pubblicare il mazzo `vecchia + nuova` su tutti i servizi** che verificano: aggiornare
   `JWT_PUBLIC_KEY` nei Secret di gateway, dashboard-api, distribution, onboarding,
   hashchain, notification e signature con le due chiavi concatenate (la vecchia per
   prima), poi riavviare i servizi uno alla volta. In questa fase il gateway firma ancora
   con la vecchia privata. Verifica: `--jwks` sul nuovo valore mostra **due** `kid`; nei
   log di avvio compare `jwt_mazzo_chiavi` con i due `kid`.
3. **Cambiare la chiave di firma del gateway**: `JWT_PRIVATE_KEY` = nuova privata (il
   mazzo resta `vecchia + nuova`), riavvio del gateway. Da qui i nuovi token portano il
   `kid` nuovo; quelli già emessi (`kid` vecchio) restano validi fino alla scadenza.
   Riemettere subito il token di servizio di hashchain con la chiave nuova e aggiornare
   `SIGNATURE_SERVICE_TOKEN`.
4. **Togliere la vecchia chiave dal mazzo** dopo almeno `JWT_EXPIRY_SECONDS` + 60 s dal
   passo 3 **e** dopo aver sostituito il token di servizio: `JWT_PUBLIC_KEY` = solo la nuova
   pubblica su tutti i servizi, riavvio uno alla volta. I token con il `kid` vecchio ora
   sono rifiutati (`AUTH-001`).

Annotare nel registro delle operazioni del conservatore data, ora e `kid` di ogni passo.

## Chiave compromessa (rotazione d'emergenza)

Si salta l'attesa: passi 1, poi `JWT_PUBLIC_KEY` = **solo la nuova** e `JWT_PRIVATE_KEY` =
nuova privata insieme su tutti i servizi, nuovo token di servizio. Tutti i token emessi
con la chiave compromessa diventano invalidi subito: gli utenti devono rientrare (è voluto).

## Verifiche dopo ogni passo

- `GET /api/health/ready` di ogni servizio risponde 200;
- un accesso nuovo funziona e il token ha nell'intestazione il `kid` atteso
  (`python -c "import jwt,sys; print(jwt.get_unverified_header(sys.argv[1]))" <token>`);
- nessun aumento di `jwt_rifiutato` con `kid sconosciuto` nei log (dopo il passo 4 ce ne
  saranno solo per token vecchi rimasti nei browser, che si risolvono con un nuovo accesso).
