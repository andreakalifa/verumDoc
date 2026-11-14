// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
package it.verumdoc.verificafirme;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Configurazione da variabili d'ambiente (values di Helm, ConfigMap e Secret).
 *
 * @param modo               "trusted-list" (esercizio: LOTL UE) o "locale" (collaudo:
 *                           trust anchor da {@code radiciPem}, nessuna rete)
 * @param lotlUrl            URL della List of Trusted Lists UE
 * @param ojUrl              URL della Gazzetta ufficiale che pubblica i certificati della LOTL
 * @param lotlKeystore       keystore PKCS12 con i certificati di firma della LOTL (Gazzetta UE)
 * @param lotlKeystorePassword password del keystore (Secret)
 * @param cacheDir           cartella della cache delle Trusted List (volume)
 * @param aggiornamentoMs    intervallo di aggiornamento della Trusted List
 * @param radiciPem          trust anchor PEM per il modo "locale"
 * @param revocheInRete      OCSP/CRL scaricati in rete (false nel modo "locale")
 * @param jwtChiavePubblica  chiave pubblica RS256 dei token di servizio (PEM)
 * @param jwtAudience        audience richiesta (verumdoc-verifica-firme)
 * @param jwtScope           scope richiesto (firme:verifica)
 * @param dimensioneMassima  byte massimi di un documento da verificare (contenuto più
 *                           originale); il corpo della richiesta è limitato di conseguenza
 * @param reteDomini         revisione X10: allow-list (suffissi di dominio separati da
 *                           virgola) degli host AIA/CRL/OCSP; vuota = qualunque host pubblico
 * @param reteTempoMs        tempo massimo di connessione e risposta di un download
 * @param reteRispostaMax    byte massimi di una risposta AIA/CRL/OCSP
 */
@ConfigurationProperties(prefix = "verifica")
public record Impostazioni(
        String modo,
        String lotlUrl,
        String ojUrl,
        String lotlKeystore,
        String lotlKeystorePassword,
        String cacheDir,
        long aggiornamentoMs,
        String radiciPem,
        boolean revocheInRete,
        String jwtChiavePubblica,
        String jwtAudience,
        String jwtScope,
        long dimensioneMassima,
        String reteDomini,
        long reteTempoMs,
        long reteRispostaMax) {

    /** Revisione X8: corpo massimo della richiesta (base64 di contenuto e originale + JSON). */
    public long corpoMassimo() {
        return dimensioneMassima / 3 * 4 + 8 + 64 * 1024;
    }

    public boolean locale() {
        return "locale".equalsIgnoreCase(modo);
    }
}
