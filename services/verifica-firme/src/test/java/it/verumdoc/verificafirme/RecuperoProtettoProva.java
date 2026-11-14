// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
package it.verumdoc.verificafirme;

import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;
import javax.net.ssl.SSLSocketFactory;

/**
 * R12a (VD-C-144): prova del pinning di {@link RecuperoProtetto} con il solo JDK
 * ({@code javac} + {@code java}, senza Maven): eseguita da
 * {@code tools/dev/test_r12a_verifica_firme_pinning.py}. Termina con codice 1 al
 * primo controllo non superato.
 */
public final class RecuperoProtettoProva {
    private static void verifica(boolean condizione, String cosa) {
        if (!condizione) {
            System.err.println("FALLITO: " + cosa);
            System.exit(1);
        }
        System.out.println("ok: " + cosa);
    }

    private static boolean fallisce(Azione a, String parola) {
        try {
            a.esegui();
            return false;
        } catch (IOException e) {
            return parola == null || String.valueOf(e.getMessage()).contains(parola);
        }
    }

    private interface Azione {
        void esegui() throws IOException;
    }

    public static void main(String[] args) throws Exception {
        AtomicReference<String> hostVisto = new AtomicReference<>();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/ok", s -> {
            hostVisto.set(s.getRequestHeaders().getFirst("Host"));
            byte[] b = "contenuto".getBytes(StandardCharsets.UTF_8);
            s.sendResponseHeaders(200, b.length);
            try (OutputStream o = s.getResponseBody()) { o.write(b); }
        });
        server.createContext("/chunked", s -> {
            s.sendResponseHeaders(200, 0);   // 0 = chunked
            try (OutputStream o = s.getResponseBody()) {
                o.write("pezzo-1;".getBytes(StandardCharsets.UTF_8));
                o.flush();
                o.write("pezzo-2".getBytes(StandardCharsets.UTF_8));
            }
        });
        server.createContext("/grande", s -> {
            s.sendResponseHeaders(200, 0);
            try (OutputStream o = s.getResponseBody()) { o.write(new byte[5000]); }
        });
        server.createContext("/redirect", s -> {
            s.getResponseHeaders().add("Location", "http://169.254.169.254/");
            s.sendResponseHeaders(302, -1);
            s.close();
        });
        server.createContext("/post", s -> {
            byte[] corpo = s.getRequestBody().readAllBytes();
            String tipo = s.getRequestHeaders().getFirst("Content-Type");
            byte[] b = (tipo + ":" + corpo.length).getBytes(StandardCharsets.UTF_8);
            s.sendResponseHeaders(200, b.length);
            try (OutputStream o = s.getResponseBody()) { o.write(b); }
        });
        server.createContext("/lenta", s -> {
            // intestazione inviata un byte ogni 300 ms: deve scadere il tempo complessivo
            try {
                var out = new java.io.PrintStream(s.getResponseBody());
                s.sendResponseHeaders(200, 0);
                for (int i = 0; i < 100; i++) { out.write('x'); out.flush(); Thread.sleep(300); }
            } catch (Exception e) { /* connessione chiusa dal client */ }
        });
        server.setExecutor(java.util.concurrent.Executors.newCachedThreadPool());
        server.start();
        int porta = server.getAddress().getPort();
        try {
            RecuperoProtetto r = new RecuperoProtetto("", 3000, 1000, true,
                    (SSLSocketFactory) SSLSocketFactory.getDefault());
            String base = "http://127.0.0.1:" + porta;
            verifica(new String(r.get(base + "/ok"), StandardCharsets.UTF_8).equals("contenuto"),
                    "GET con Content-Length");
            verifica(new String(r.get(base + "/chunked"), StandardCharsets.UTF_8)
                    .equals("pezzo-1;pezzo-2"), "risposta chunked");
            verifica(new String(r.post(base + "/post", new byte[]{1, 2, 3},
                    "application/ocsp-request"), StandardCharsets.UTF_8)
                    .equals("application/ocsp-request:3"), "POST OCSP");
            verifica(fallisce(() -> r.get(base + "/grande"), "oltre"), "limite di dimensione");
            verifica(fallisce(() -> r.get(base + "/redirect"), "302"), "nessun redirect");
            RecuperoProtetto breve = new RecuperoProtetto("", 500, 100000, true,
                    (SSLSocketFactory) SSLSocketFactory.getDefault());
            long inizio = System.nanoTime();
            verifica(fallisce(() -> breve.get(base + "/lenta"), null)
                    && (System.nanoTime() - inizio) / 1_000_000L < 5000,
                    "risposta lenta interrotta entro il tempo complessivo");
            // IPv6 letterale: Host con UNA coppia di parentesi
            RecuperoProtetto.Destinazione v6 = new RecuperoProtetto.Destinazione(
                    URI.create("http://[::1]:" + porta + "/ok"), InetAddress.getByName("127.0.0.1"));
            verifica(new String(r.scambia(v6, "GET", null, null), StandardCharsets.UTF_8)
                    .equals("contenuto") && ("[::1]:" + porta).equals(hostVisto.get()),
                    "IPv6 letterale: Host senza doppie parentesi");
            // PINNING: un nome che NON si risolve; la connessione va all'indirizzo già
            // controllato e l'intestazione Host porta il nome dell'URL
            RecuperoProtetto.Destinazione d = new RecuperoProtetto.Destinazione(
                    URI.create("http://nome-non-risolvibile.invalid:" + porta + "/ok"),
                    InetAddress.getByName("127.0.0.1"));
            verifica(new String(r.scambia(d, "GET", null, null), StandardCharsets.UTF_8)
                    .equals("contenuto"), "connessione all'indirizzo controllato (nessun DNS)");
            verifica(("nome-non-risolvibile.invalid:" + porta).equals(hostVisto.get()),
                    "Host = nome dell'URL");
            // costruttore di esercizio: loopback, porte e schemi rifiutati prima di connettersi
            RecuperoProtetto esercizio = new RecuperoProtetto("", 3000, 1000);
            verifica(fallisce(() -> esercizio.get(base + "/ok"), "porta"), "porta non ammessa");
            verifica(fallisce(() -> esercizio.get("http://127.0.0.1/ok"), "non pubblico"),
                    "loopback rifiutato");
            verifica(fallisce(() -> esercizio.get("http://169.254.169.254/latest"),
                    "non pubblico"), "metadati del cloud rifiutati");
            verifica(fallisce(() -> esercizio.get("file:///etc/passwd"), "schema"),
                    "schema non ammesso");
            verifica(!RecuperoProtetto.pubblico(InetAddress.getByName("::ffff:10.0.0.1")),
                    "IPv4 privato mappato in IPv6");
        } finally {
            server.stop(0);
            ((java.util.concurrent.ExecutorService) server.getExecutor()).shutdownNow();
        }
        System.out.println("TUTTI I CONTROLLI SUPERATI");
    }
}
