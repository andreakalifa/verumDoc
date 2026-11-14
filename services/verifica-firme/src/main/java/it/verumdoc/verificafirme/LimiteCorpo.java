// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
package it.verumdoc.verificafirme;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * Revisione R08 (X8): il corpo della richiesta si limita PRIMA che Spring MVC lo
 * legga e lo decodifichi (in MVC {@code spring.codec.max-in-memory-size} non
 * vale). Una POST senza Content-Length (chunked) si rifiuta con 411; oltre
 * {@link Impostazioni#corpoMassimo()} con 413. Insieme a
 * {@code server.tomcat.threads.max} limita la memoria del pod.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class LimiteCorpo extends OncePerRequestFilter {
    private final Impostazioni impostazioni;

    public LimiteCorpo(Impostazioni impostazioni) {
        this.impostazioni = impostazioni;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest richiesta) {
        return "GET".equals(richiesta.getMethod()) || "HEAD".equals(richiesta.getMethod());
    }

    @Override
    protected void doFilterInternal(HttpServletRequest richiesta, HttpServletResponse risposta,
                                    FilterChain catena) throws ServletException, IOException {
        long lunghezza = richiesta.getContentLengthLong();
        if (lunghezza < 0) {
            rifiuta(risposta, 411, "FIRME-411", "Content-Length obbligatorio");
            return;
        }
        if (lunghezza > impostazioni.corpoMassimo()) {
            rifiuta(risposta, 413, "FIRME-413", "documento troppo grande");
            return;
        }
        catena.doFilter(richiesta, risposta);
    }

    private static void rifiuta(HttpServletResponse risposta, int stato, String codice,
                                String titolo) throws IOException {
        risposta.setStatus(stato);
        risposta.setContentType("application/problem+json");
        risposta.getWriter().write("{\"codice\":\"" + codice + "\",\"title\":\"" + titolo + "\"}");
    }
}
