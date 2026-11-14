// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
package it.verumdoc.verificafirme;

import eu.europa.esig.dss.model.x509.CertificateToken;
import eu.europa.esig.dss.spi.DSSUtils;
import eu.europa.esig.dss.spi.tsl.TrustedListsCertificateSource;
import eu.europa.esig.dss.spi.x509.CommonCertificateSource;
import eu.europa.esig.dss.spi.x509.CommonTrustedCertificateSource;
import eu.europa.esig.dss.spi.x509.KeyStoreCertificateSource;
import eu.europa.esig.dss.service.http.commons.CommonsDataLoader;
import eu.europa.esig.dss.service.http.commons.FileCacheDataLoader;
import eu.europa.esig.dss.spi.client.http.DSSCacheFileLoader;
import eu.europa.esig.dss.spi.client.http.IgnoreDataLoader;
import eu.europa.esig.dss.tsl.function.OfficialJournalSchemeInformationURI;
import eu.europa.esig.dss.tsl.job.TLValidationJob;
import eu.europa.esig.dss.tsl.source.LOTLSource;
import eu.europa.esig.dss.tsl.cache.CacheCleaner;
import eu.europa.esig.dss.spi.x509.CertificateSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;

/**
 * Trust anchor della verifica.
 *
 * <ul>
 *   <li>modo "trusted-list": Trusted List UE caricate dalla LOTL con
 *       {@link TLValidationJob}, cache su file e aggiornamento periodico
 *       ({@code VERIFICA_AGGIORNAMENTO_MS}); i certificati di firma della LOTL
 *       sono quelli pubblicati nella Gazzetta ufficiale indicata
 *       ({@code VERIFICA_OJ_URL}). Prima del primo caricamento riuscito il
 *       servizio non è pronto (readiness 503);</li>
 *   <li>modo "locale" (collaudo): trust anchor da {@code VERIFICA_RADICI_PEM},
 *       nessun accesso alla rete.</li>
 * </ul>
 */
@Component
public class FonteAttendibile {
    private static final Logger LOG = LoggerFactory.getLogger(FonteAttendibile.class);

    private final Impostazioni impostazioni;
    private final TrustedListsCertificateSource trustedList = new TrustedListsCertificateSource();
    private final CommonTrustedCertificateSource locale = new CommonTrustedCertificateSource();
    private TLValidationJob job;
    private volatile Instant ultimoAggiornamento;

    public FonteAttendibile(Impostazioni impostazioni) {
        this.impostazioni = impostazioni;
        if (impostazioni.locale()) {
            caricaRadiciLocali();
        } else {
            this.job = creaJob();
        }
    }

    public CertificateSource fonte() {
        return impostazioni.locale() ? locale : trustedList;
    }

    public boolean pronta() {
        return impostazioni.locale() || ultimoAggiornamento != null;
    }

    public Instant ultimoAggiornamento() {
        return ultimoAggiornamento;
    }

    private void caricaRadiciLocali() {
        String pem = impostazioni.radiciPem() == null ? "" : impostazioni.radiciPem();
        try {
            if (!pem.contains("-----BEGIN") && !pem.isBlank()) {
                pem = Files.readString(Path.of(pem), StandardCharsets.US_ASCII);
            }
            for (CertificateToken token : leggiPem(pem)) {
                locale.addCertificate(token);
            }
            LOG.info("trust anchor locali caricate: {}", locale.getNumberOfCertificates());
        } catch (Exception e) {
            throw new IllegalStateException("VERIFICA_RADICI_PEM non leggibile", e);
        }
    }

    static List<CertificateToken> leggiPem(String pem) {
        CommonCertificateSource fonte = new CommonCertificateSource();
        int inizio = 0;
        while ((inizio = pem.indexOf("-----BEGIN CERTIFICATE-----", inizio)) >= 0) {
            int fine = pem.indexOf("-----END CERTIFICATE-----", inizio);
            String blocco = pem.substring(inizio, fine + "-----END CERTIFICATE-----".length());
            fonte.addCertificate(DSSUtils.loadCertificate(blocco.getBytes(StandardCharsets.US_ASCII)));
            inizio = fine;
        }
        return fonte.getCertificates();
    }

    private TLValidationJob creaJob() {
        FileCacheDataLoader inRete = new FileCacheDataLoader(new CommonsDataLoader());
        inRete.setCacheExpirationTime(0);
        inRete.setFileCacheDirectory(new File(impostazioni.cacheDir()));
        FileCacheDataLoader senzaRete = new FileCacheDataLoader(new IgnoreDataLoader());
        senzaRete.setCacheExpirationTime(-1);
        senzaRete.setFileCacheDirectory(new File(impostazioni.cacheDir()));

        LOTLSource lotl = new LOTLSource();
        lotl.setUrl(impostazioni.lotlUrl());
        try {
            // certificati di firma della LOTL pubblicati nella Gazzetta ufficiale UE
            // (keystore ufficiale fornito al deploy, VERIFICA_LOTL_KEYSTORE)
            lotl.setCertificateSource(new KeyStoreCertificateSource(
                    new File(impostazioni.lotlKeystore()), "PKCS12",
                    impostazioni.lotlKeystorePassword().toCharArray()));
        } catch (Exception e) {
            throw new IllegalStateException("VERIFICA_LOTL_KEYSTORE non leggibile", e);
        }
        lotl.setSigningCertificatesAnnouncementPredicate(
                new OfficialJournalSchemeInformationURI(impostazioni.ojUrl()));
        lotl.setPivotSupport(true);

        TLValidationJob nuovo = new TLValidationJob();
        nuovo.setOnlineDataLoader(inRete);
        nuovo.setOfflineDataLoader(senzaRete);
        nuovo.setTrustedListCertificateSource(trustedList);
        nuovo.setListOfTrustedListSources(lotl);
        CacheCleaner pulizia = new CacheCleaner();
        pulizia.setCleanMemory(true);
        pulizia.setCleanFileSystem(true);
        pulizia.setDSSFileLoader((DSSCacheFileLoader) senzaRete);
        nuovo.setCacheCleaner(pulizia);
        return nuovo;
    }

    /** Aggiornamento periodico della Trusted List (prima esecuzione all'avvio). */
    @Scheduled(initialDelay = 0, fixedDelayString = "${verifica.aggiornamento-ms}")
    public void aggiorna() {
        if (job == null) {
            return;
        }
        try {
            if (ultimoAggiornamento == null) {
                job.offlineRefresh();  // dalla cache del volume, se c'è
            }
            job.onlineRefresh();
            ultimoAggiornamento = Instant.now();
            LOG.info("trusted list aggiornata: {} certificati", trustedList.getNumberOfCertificates());
        } catch (Exception e) {
            LOG.error("aggiornamento della trusted list non riuscito", e);
        }
    }
}
