// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
package it.verumdoc.verificafirme;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.crypto.RSASSAVerifier;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Date;
import java.util.List;

/**
 * Token di SERVIZIO obbligatorio (come /firma di signature, revisione M2): RS256
 * con la chiave pubblica condivisa, audience {@code verumdoc-verifica-firme},
 * scope {@code firme:verifica}, non scaduto.
 *
 * <p>Revisione R08 (X9): il token è obbligatorio su TUTTO tranne una allow-list
 * esatta (salute e metriche), confrontata con il percorso che il contenitore ha
 * già decodificato e normalizzato ({@code getServletPath() + getPathInfo()}),
 * lo stesso che usa Spring MVC per il mapping: {@code /api/v1/%76erifica},
 * {@code //api/v1/verifica} o {@code /api/v1/./verifica} non saltano il
 * controllo. Un percorso grezzo con segmenti codificati di separatori o punti
 * ({@code %2F}, {@code %2E}, {@code %5C}, {@code ;}) si rifiuta comunque.</p>
 */
@Component
public class TokenServizio extends OncePerRequestFilter {
    private final Impostazioni impostazioni;

    public TokenServizio(Impostazioni impostazioni) {
        this.impostazioni = impostazioni;
    }

    /** Percorsi senza token (solo GET): salute e metriche. */
    static final java.util.Set<String> SENZA_TOKEN = java.util.Set.of(
            "/api/v1/health/live", "/api/v1/health/ready", "/actuator/prometheus");
    private static final java.util.regex.Pattern GREZZO_SOSPETTO = java.util.regex.Pattern.compile(
            "(?i)%2f|%5c|%2e|%25|;|\\\\");

    static String percorso(HttpServletRequest richiesta) {
        String servlet = richiesta.getServletPath() == null ? "" : richiesta.getServletPath();
        String info = richiesta.getPathInfo() == null ? "" : richiesta.getPathInfo();
        return servlet + info;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest richiesta) {
        return "GET".equals(richiesta.getMethod())
                && !GREZZO_SOSPETTO.matcher(String.valueOf(richiesta.getRequestURI())).find()
                && SENZA_TOKEN.contains(percorso(richiesta));
    }

    @Override
    protected void doFilterInternal(HttpServletRequest richiesta, HttpServletResponse risposta,
                                    FilterChain catena) throws ServletException, IOException {
        String intestazione = richiesta.getHeader("Authorization");
        if (intestazione == null || !intestazione.startsWith("Bearer ") || !valido(intestazione.substring(7))) {
            risposta.setStatus(401);
            risposta.setContentType("application/problem+json");
            risposta.getWriter().write("{\"codice\":\"AUTH-401\",\"title\":\"Token di servizio non valido\"}");
            return;
        }
        catena.doFilter(richiesta, risposta);
    }

    boolean valido(String token) {
        try {
            SignedJWT jwt = SignedJWT.parse(token);
            if (!JWSAlgorithm.RS256.equals(jwt.getHeader().getAlgorithm())) {
                return false;
            }
            RSAKey chiave = (RSAKey) RSAKey.parseFromPEMEncodedObjects(impostazioni.jwtChiavePubblica());
            if (!jwt.verify(new RSASSAVerifier(chiave.toRSAPublicKey()))) {
                return false;
            }
            JWTClaimsSet dati = jwt.getJWTClaimsSet();
            List<String> audience = dati.getAudience();
            Object scope = dati.getClaim("scope");
            return dati.getExpirationTime() != null && dati.getExpirationTime().after(new Date())
                    && audience != null && audience.contains(impostazioni.jwtAudience())
                    && scope != null && List.of(String.valueOf(scope).split(" ")).contains(impostazioni.jwtScope());
        } catch (Exception e) {
            return false;
        }
    }
}
