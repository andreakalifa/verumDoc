// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
package it.verumdoc.verificafirme;

import eu.europa.esig.dss.diagnostic.CertificateRevocationWrapper;
import eu.europa.esig.dss.diagnostic.CertificateWrapper;
import eu.europa.esig.dss.diagnostic.DiagnosticData;
import eu.europa.esig.dss.diagnostic.SignatureWrapper;
import eu.europa.esig.dss.enumerations.Indication;
import eu.europa.esig.dss.enumerations.SubIndication;
import eu.europa.esig.dss.model.DSSDocument;
import eu.europa.esig.dss.model.InMemoryDocument;
import eu.europa.esig.dss.service.crl.OnlineCRLSource;
import eu.europa.esig.dss.service.ocsp.OnlineOCSPSource;
import eu.europa.esig.dss.simplereport.SimpleReport;
import eu.europa.esig.dss.spi.validation.CommonCertificateVerifier;
import eu.europa.esig.dss.spi.x509.aia.DefaultAIASource;
import eu.europa.esig.dss.validation.SignedDocumentValidator;
import eu.europa.esig.dss.validation.reports.Reports;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Verifica con EU DSS e normalizzazione dell'esito nella forma di
 * {@code libs/shared/firme/esito.py} (EsitoVerifica.come_dict).
 *
 * <p>Livelli: CRITTOGRAFICA usa le informazioni di revoca disponibili ma non le
 * pretende (una INDETERMINATE dovuta solo alla revoca resta INDETERMINATE con
 * revoca SCONOSCIUTO: il giudizio lo fa la regola RGL-109 di VerumDoc);
 * CON_REVOCHE scarica OCSP/CRL (in esercizio) e una revoca non determinabile è
 * INDETERMINATE / TRY_LATER.</p>
 */
@Service
public class ServizioVerifica {
    static final int RAPPORTO_MAX = 64 * 1024;

    private final FonteAttendibile fonte;
    private final Impostazioni impostazioni;
    private final RecuperoProtetto recupero;

    public ServizioVerifica(FonteAttendibile fonte, Impostazioni impostazioni) {
        this.fonte = fonte;
        this.impostazioni = impostazioni;
        this.recupero = new RecuperoProtetto(impostazioni.reteDomini(),
                impostazioni.reteTempoMs(), impostazioni.reteRispostaMax());
    }

    public Map<String, Object> verifica(byte[] contenuto, byte[] originale, String nomeFile,
                                        String livello) {
        CommonCertificateVerifier verificatore = new CommonCertificateVerifier();
        verificatore.setTrustedCertSources(fonte.fonte());
        if (impostazioni.revocheInRete() && !impostazioni.locale()) {
            // Revisione X10: AIA/CRL/OCSP dei certificati del versante solo con il
            // caricatore protetto (allow-list, indirizzi pubblici, limiti)
            CaricatoreProtetto caricatore = new CaricatoreProtetto(recupero);
            verificatore.setOcspSource(new OnlineOCSPSource(caricatore));
            verificatore.setCrlSource(new OnlineCRLSource(caricatore));
            verificatore.setAIASource(new DefaultAIASource(caricatore));
        } else {
            verificatore.setOcspSource(null);
            verificatore.setCrlSource(null);
            verificatore.setAIASource(null);
        }
        DSSDocument documento = new InMemoryDocument(contenuto, nomeFile);
        String adesso = Instant.now().toString();
        SignedDocumentValidator validatore;
        try {
            validatore = SignedDocumentValidator.fromDocument(documento);
        } catch (Exception e) {
            // Revisione X6: DSS non distingue «non firmato» da «busta non
            // riconosciuta» (p7m corrotto, base64, PEM): lo dice il motivo e il
            // client Python confronta con la rilevazione locale (busta riconosciuta
            // = TOTAL_FAILED / FORMAT_FAILURE, mai «non firmato»)
            return Map.of("formato", "NESSUNO", "livello", livello, "verificatore", nome(),
                    "verificato_il", adesso, "firme", List.of(),
                    "motivo", "documento non firmato o formato non riconosciuto da EU DSS",
                    "riconosciuto", false, "ripiego", false, "note", List.of());
        }
        validatore.setCertificateVerifier(verificatore);
        if (originale != null) {
            validatore.setDetachedContents(List.of(new InMemoryDocument(originale)));
        }
        Reports rapporti = validatore.validateDocument();
        return Mappatura.normalizza(rapporti, livello, nome(), adesso);
    }

    String nome() {
        return "eu-dss " + Mappatura.versioneDss();
    }

    /** Conversione da DSS alla forma normalizzata (funzioni pure). */
    static final class Mappatura {
        private Mappatura() {
        }

        static String versioneDss() {
            Package p = SignedDocumentValidator.class.getPackage();
            return p.getImplementationVersion() == null ? "6.2" : p.getImplementationVersion();
        }

        static Map<String, Object> normalizza(Reports rapporti, String livello, String nome,
                                              String adesso) {
            SimpleReport semplice = rapporti.getSimpleReport();
            DiagnosticData diagnostica = rapporti.getDiagnosticData();
            List<Map<String, Object>> firme = new ArrayList<>();
            String formato = "NESSUNO";
            for (String id : semplice.getSignatureIdList()) {
                SignatureWrapper firma = diagnostica.getSignatureById(id);
                formato = famiglia(String.valueOf(semplice.getSignatureFormat(id)));
                firme.add(firma(semplice, firma, id, formato, livello));
            }
            String grezzo = rapporti.getXmlSimpleReport();
            if (grezzo != null && grezzo.length() > RAPPORTO_MAX) {
                grezzo = grezzo.substring(0, RAPPORTO_MAX);
            }
            Map<String, Object> esito = new java.util.LinkedHashMap<>();
            esito.put("formato", formato);
            esito.put("livello", livello);
            esito.put("verificatore", nome);
            esito.put("verificato_il", adesso);
            esito.put("firme", firme);
            esito.put("motivo", firme.isEmpty() ? "nessuna firma nel file" : null);
            esito.put("rapporto_grezzo", grezzo);
            esito.put("ripiego", false);
            esito.put("note", List.of());
            return esito;
        }

        static String famiglia(String formatoDss) {
            if (formatoDss.startsWith("CAdES")) {
                return "CAdES";
            }
            if (formatoDss.startsWith("PAdES") || formatoDss.startsWith("PDF")) {
                return "PAdES";
            }
            if (formatoDss.startsWith("XAdES")) {
                return "XAdES";
            }
            return formatoDss.startsWith("ASiC") ? "ASiC" : "CAdES";
        }

        /** Revisione X3: codice fiscale (serialNumber del soggetto) e impronta SHA-256 vera. */
        static Map<String, Object> certificato(CertificateWrapper c) {
            Map<String, Object> m = new java.util.LinkedHashMap<>();
            m.put("soggetto", String.valueOf(c.getCertificateDN()));
            m.put("emittente", String.valueOf(c.getCertificateIssuerDN()));
            m.put("seriale", String.valueOf(c.getSerialNumber()));
            m.put("valido_dal", String.valueOf(c.getNotBefore().toInstant()));
            m.put("valido_al", String.valueOf(c.getNotAfter().toInstant()));
            m.put("impronta_sha256", impronta(c.getBinaries(), c.getId()));
            m.put("codice_fiscale", c.getSubjectSerialNumber());
            return m;
        }

        static String impronta(byte[] der, String ripiego) {
            if (der == null) {
                return ripiego;
            }
            try {
                return java.util.HexFormat.of().formatHex(
                        java.security.MessageDigest.getInstance("SHA-256").digest(der));
            } catch (java.security.NoSuchAlgorithmException e) {
                return ripiego;
            }
        }

        static Map<String, Object> firma(SimpleReport semplice, SignatureWrapper firma, String id,
                                         String formato, String livello) {
            Indication indicazione = semplice.getIndication(id);
            SubIndication sotto = semplice.getSubIndication(id);
            CertificateWrapper certificato = firma.getSigningCertificate();
            String revoca = "NON_CONTROLLATO";
            if (certificato != null && !certificato.getCertificateRevocationData().isEmpty()) {
                revoca = "BUONO";
                for (CertificateRevocationWrapper r : certificato.getCertificateRevocationData()) {
                    if (r.isRevoked()) {
                        revoca = "REVOCATO";
                    }
                }
            } else if ("CON_REVOCHE".equals(livello)) {
                revoca = "SCONOSCIUTO";
            }
            String esito = switch (indicazione) {
                case TOTAL_PASSED, PASSED -> "TOTAL_PASSED";
                case TOTAL_FAILED, FAILED -> "TOTAL_FAILED";
                default -> "INDETERMINATE";
            };
            Map<String, Object> f = new java.util.LinkedHashMap<>();
            f.put("formato", formato);
            f.put("indicazione", esito);
            f.put("sottoindicazione", sotto == null ? null : sotto.name());
            f.put("firmatario", semplice.getSignedBy(id));
            f.put("certificato", certificato == null ? null : certificato(certificato));
            f.put("crittografia_valida", firma.isSignatureIntact() && firma.isSignatureValid());
            f.put("catena_attendibile", certificato != null && certificato.isTrustedChain());
            f.put("trust_anchor", certificato == null || certificato.getTrustAnchor() == null ? null
                    : certificato.getTrustAnchor().getCertificateDN());
            f.put("revoca", revoca);
            f.put("data_firma", firma.getClaimedSigningTime() == null ? null
                    : firma.getClaimedSigningTime().toInstant().toString());
            List<String> motivi = new ArrayList<>();
            semplice.getAdESValidationErrors(id).forEach(m -> motivi.add(m.getValue()));
            f.put("motivi", motivi);
            return f;
        }
    }
}
