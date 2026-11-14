// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
package it.verumdoc.verificafirme;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.Base64;
import java.util.Map;
import java.util.Set;

/**
 * API interna (contratto in {@code libs/shared/firme/dss.py}):
 * <pre>
 * POST /api/v1/verifica  {"contenuto": b64, "originale": b64|null, "nome_file": str, "livello": str}
 *   → 200 esito normalizzato | 400 richiesta non valida | 401 token
 *   | 413 oltre VERIFICA_DIMENSIONE_MASSIMA (contenuto + originale; il corpo
 *     oltre il limite si rifiuta PRIMA della lettura, filtro LimiteCorpo)
 * GET  /api/v1/health/live   processo vivo
 * GET  /api/v1/health/ready  503 finché la Trusted List non è caricata (modo trusted-list)
 * </pre>
 */
@RestController
public class VerificaController {
    private static final Set<String> LIVELLI = Set.of("CRITTOGRAFICA", "CON_REVOCHE");

    private final ServizioVerifica servizio;
    private final FonteAttendibile fonte;
    private final Impostazioni impostazioni;

    public VerificaController(ServizioVerifica servizio, FonteAttendibile fonte,
                              Impostazioni impostazioni) {
        this.servizio = servizio;
        this.fonte = fonte;
        this.impostazioni = impostazioni;
    }

    public record Richiesta(String contenuto, String originale, String nome_file, String livello) {
    }

    @PostMapping("/api/v1/verifica")
    public ResponseEntity<?> verifica(@RequestBody Richiesta r) {
        if (r == null || r.contenuto() == null || r.livello() == null || !LIVELLI.contains(r.livello())) {
            return ResponseEntity.badRequest().body(Map.of("codice", "FIRME-400",
                    "title", "Richiesta non valida: contenuto e livello (CRITTOGRAFICA, CON_REVOCHE)"));
        }
        if (!fonte.pronta()) {
            return ResponseEntity.status(503).body(Map.of("codice", "FIRME-503",
                    "title", "Trusted List non ancora caricata"));
        }
        byte[] contenuto;
        byte[] originale;
        try {
            contenuto = Base64.getDecoder().decode(r.contenuto());
            originale = r.originale() == null ? null : Base64.getDecoder().decode(r.originale());
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("codice", "FIRME-400", "title", "base64 non valido"));
        }
        if (contenuto.length + (originale == null ? 0 : originale.length) > impostazioni.dimensioneMassima()) {
            return ResponseEntity.status(413).body(Map.of("codice", "FIRME-413", "title", "documento troppo grande"));
        }
        String nome = r.nome_file() == null ? "documento" : r.nome_file();
        return ResponseEntity.ok(servizio.verifica(contenuto, originale, nome, r.livello()));
    }

    @GetMapping("/api/v1/health/live")
    public Map<String, String> vivo() {
        return Map.of("stato", "ok");
    }

    @GetMapping("/api/v1/health/ready")
    public ResponseEntity<Map<String, Object>> pronto() {
        boolean pronta = fonte.pronta();
        Map<String, Object> corpo = Map.of("stato", pronta ? "ok" : "trusted_list_non_caricata",
                "modo", impostazioni.modo(),
                "trusted_list_aggiornata_il", String.valueOf(fonte.ultimoAggiornamento()));
        return ResponseEntity.status(pronta ? 200 : 503).body(corpo);
    }
}
