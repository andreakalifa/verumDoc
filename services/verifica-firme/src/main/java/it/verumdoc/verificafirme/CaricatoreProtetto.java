// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
package it.verumdoc.verificafirme;

import eu.europa.esig.dss.model.DSSException;
import eu.europa.esig.dss.spi.client.http.DataLoader;

import java.io.IOException;
import java.util.List;

/**
 * Revisione R08 (X10): {@link DataLoader} di EU DSS per AIA, CRL e OCSP che
 * passa SOLO da {@link RecuperoProtetto} (schema, porta, allow-list dei domini,
 * indirizzi pubblici, nessun redirect, tempi e dimensioni massimi). Sostituisce
 * i data loader predefiniti di {@code OnlineOCSPSource}, {@code OnlineCRLSource}
 * e {@code DefaultAIASource}.
 */
public final class CaricatoreProtetto implements DataLoader {
    private static final long serialVersionUID = 1L;
    private static final String TIPO_OCSP = "application/ocsp-request";

    private final transient RecuperoProtetto recupero;
    private String tipo;

    public CaricatoreProtetto(RecuperoProtetto recupero) {
        this.recupero = recupero;
    }

    @Override
    public byte[] get(String url) {
        try {
            return recupero.get(url);
        } catch (IOException e) {
            throw new DSSException("scaricamento non ammesso o non riuscito: " + e.getMessage());
        }
    }

    @Override
    public DataAndUrl get(List<String> urls) {
        DSSException ultimo = null;
        for (String url : urls) {
            try {
                return new DataAndUrl(url, get(url));
            } catch (DSSException e) {
                ultimo = e;
            }
        }
        throw ultimo != null ? ultimo : new DSSException("nessun URL da scaricare");
    }

    @Override
    public byte[] post(String url, byte[] contenuto) {
        try {
            return recupero.post(url, contenuto, tipo != null ? tipo : TIPO_OCSP);
        } catch (IOException e) {
            throw new DSSException("invio non ammesso o non riuscito: " + e.getMessage());
        }
    }

    @Override
    public void setContentType(String tipo) {
        this.tipo = tipo;
    }
}
