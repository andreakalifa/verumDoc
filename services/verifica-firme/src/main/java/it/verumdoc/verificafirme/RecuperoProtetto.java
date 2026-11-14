// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
package it.verumdoc.verificafirme;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Inet4Address;
import java.net.Inet6Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.net.UnknownHostException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Locale;
import javax.net.ssl.SNIHostName;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/**
 * Revisione R08 (X10): scaricamento PROTETTO degli URL AIA, CRL e OCSP che si
 * trovano nei certificati della busta (quindi scelti dal versante). Solo JDK,
 * nessuna dipendenza da EU DSS (lo adatta {@code CaricatoreProtetto}):
 * <ul>
 *   <li>solo {@code http} e {@code https}, porte 80 e 443;</li>
 *   <li>host in allow-list se configurata ({@code VERIFICA_RETE_DOMINI}: suffissi
 *       di dominio separati da virgola, es. i domini dei prestatori della Trusted
 *       List); vuota = qualunque host PUBBLICO;</li>
 *   <li>ogni indirizzo risolto deve essere pubblico: niente loopback, reti
 *       private RFC 1918, link-local, CGNAT, multicast, ULA/link-local IPv6,
 *       indirizzi IPv4 mappati in IPv6 privati (la NetworkPolicy li esclude anche
 *       a livello di rete, IPv4 e IPv6);</li>
 *   <li>nessun redirect, tempo di connessione e di risposta brevi, risposta
 *       letta al più fino a {@code VERIFICA_RETE_RISPOSTA_MAX} byte.</li>
 * </ul>
 * R12a (VD-C-144): PINNING dell'indirizzo. Prima, fra la risoluzione DNS del
 * controllo e quella del client HTTP un nome poteva cambiare indirizzo (DNS
 * rebinding). Ora la connessione si apre verso l'indirizzo GIÀ controllato
 * (socket diretto all'{@link InetAddress} verificato; per {@code https} TLS sopra
 * quel socket con SNI e verifica del nome del certificato sull'host dell'URL):
 * nessuna seconda risoluzione. Client HTTP/1.1 minimo (GET/POST, risposta 200,
 * {@code Content-Length}, {@code chunked} o fino alla chiusura), stessi limiti
 * di tempo e di dimensione; la NetworkPolicy di uscita resta una barriera in più.
 */
public final class RecuperoProtetto {
    /** URL rifiutato prima di qualunque connessione. */
    public static final class UrlNonAmmesso extends IOException {
        public UrlNonAmmesso(String motivo) {
            super(motivo);
        }
    }

    /** URL controllato e indirizzo (pubblico) verso cui connettersi: il pinning. */
    public record Destinazione(URI uri, InetAddress indirizzo) {
        public int porta() {
            return uri.getPort() != -1 ? uri.getPort()
                    : "https".equalsIgnoreCase(uri.getScheme()) ? 443 : 80;
        }
    }

    private final List<String> domini;
    private final Duration tempo;
    private final long rispostaMax;
    /** Solo per i test (codice, mai configurazione): ammette il loopback. */
    private final boolean loopbackDiProva;
    private final SSLSocketFactory tls;

    public RecuperoProtetto(String domini, long tempoMs, long rispostaMax) {
        this(domini, tempoMs, rispostaMax, false, (SSLSocketFactory) SSLSocketFactory.getDefault());
    }

    RecuperoProtetto(String domini, long tempoMs, long rispostaMax, boolean loopbackDiProva,
                     SSLSocketFactory tls) {
        this.domini = domini == null || domini.isBlank() ? List.of()
                : List.of(domini.toLowerCase(Locale.ROOT).replace(" ", "").split(","));
        this.tempo = Duration.ofMillis(tempoMs);
        this.rispostaMax = rispostaMax;
        this.loopbackDiProva = loopbackDiProva;
        this.tls = tls;
    }

    /** Controlla schema, porta, dominio e indirizzi; restituisce l'URI normalizzato. */
    public URI controlla(String url) throws UrlNonAmmesso {
        return destinazione(url).uri();
    }

    /** Come {@link #controlla} e in più l'indirizzo controllato a cui connettersi. */
    public Destinazione destinazione(String url) throws UrlNonAmmesso {
        URI uri;
        try {
            uri = URI.create(url.trim()).normalize();
        } catch (IllegalArgumentException e) {
            throw new UrlNonAmmesso("URL non valido");
        }
        String schema = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        if (!schema.equals("http") && !schema.equals("https")) {
            throw new UrlNonAmmesso("schema non ammesso: " + schema);
        }
        if (uri.getUserInfo() != null) {
            throw new UrlNonAmmesso("credenziali nell'URL non ammesse");
        }
        int porta = uri.getPort();
        if (porta != -1 && porta != 80 && porta != 443 && !loopbackDiProva) {
            throw new UrlNonAmmesso("porta non ammessa: " + porta);
        }
        String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase(Locale.ROOT);
        if (host.isEmpty()) {
            throw new UrlNonAmmesso("host mancante");
        }
        if (!domini.isEmpty() && domini.stream().noneMatch(
                d -> !d.isEmpty() && (host.equals(d) || host.endsWith("." + d)))) {
            throw new UrlNonAmmesso("host fuori dall'allow-list: " + host);
        }
        InetAddress[] indirizzi;
        try {
            indirizzi = InetAddress.getAllByName(host);
        } catch (UnknownHostException e) {
            throw new UrlNonAmmesso("host non risolvibile: " + host);
        }
        for (InetAddress indirizzo : indirizzi) {
            if (!pubblico(indirizzo) && !(loopbackDiProva && indirizzo.isLoopbackAddress())) {
                throw new UrlNonAmmesso("indirizzo non pubblico per " + host);
            }
        }
        if (indirizzi.length == 0) {
            throw new UrlNonAmmesso("host senza indirizzi: " + host);
        }
        return new Destinazione(uri, indirizzi[0]);
    }

    /** True solo per indirizzi unicast pubblici (IPv4 e IPv6). */
    public static boolean pubblico(InetAddress a) {
        if (a.isAnyLocalAddress() || a.isLoopbackAddress() || a.isLinkLocalAddress()
                || a.isSiteLocalAddress() || a.isMulticastAddress()) {
            return false;
        }
        byte[] b = a.getAddress();
        if (a instanceof Inet4Address) {
            int p0 = b[0] & 0xff;
            int p1 = b[1] & 0xff;
            return !(p0 == 0 || p0 == 10 || p0 == 127 || (p0 == 100 && p1 >= 64 && p1 <= 127)
                    || (p0 == 169 && p1 == 254) || (p0 == 172 && p1 >= 16 && p1 <= 31)
                    || (p0 == 192 && p1 == 168) || (p0 == 192 && p1 == 0 && (b[2] & 0xff) == 0)
                    || (p0 == 198 && (p1 == 18 || p1 == 19)) || p0 >= 224);
        }
        if (a instanceof Inet6Address) {
            int p0 = b[0] & 0xff;
            if ((p0 & 0xfe) == 0xfc) {           // fc00::/7 ULA
                return false;
            }
            boolean mappato = true;              // ::ffff:a.b.c.d
            for (int i = 0; i < 10; i++) {
                mappato &= b[i] == 0;
            }
            if (mappato && (b[10] & 0xff) == 0xff && (b[11] & 0xff) == 0xff) {
                try {
                    return pubblico(InetAddress.getByAddress(
                            new byte[]{b[12], b[13], b[14], b[15]}));
                } catch (UnknownHostException e) {
                    return false;
                }
            }
            return (p0 & 0xe0) == 0x20;          // solo 2000::/3 (unicast globale)
        }
        return false;
    }

    public byte[] get(String url) throws IOException {
        return scambia(destinazione(url), "GET", null, null);
    }

    public byte[] post(String url, byte[] corpo, String tipo) throws IOException {
        return scambia(destinazione(url), "POST", corpo, tipo);
    }

    /** Richiesta HTTP/1.1 sull'indirizzo GIÀ controllato (nessuna nuova risoluzione). */
    byte[] scambia(Destinazione d, String metodo, byte[] corpo, String tipo) throws IOException {
        long scadenza = System.nanoTime() + tempo.toNanos() * 3;
        int ms = (int) Math.max(1, tempo.toMillis());
        Socket socket = new Socket();
        try {
            socket.connect(new InetSocketAddress(d.indirizzo(), d.porta()), ms);
            socket.setSoTimeout(ms);
            // URI.getHost() restituisce l'IPv6 letterale CON le parentesi: per TLS si
            // usa il nome nudo e niente SNI per un indirizzo letterale (RFC 6066 §3)
            String host = d.uri().getHost();
            String nudo = host.startsWith("[") && host.endsWith("]")
                    ? host.substring(1, host.length() - 1) : host;
            boolean letterale = nudo.contains(":") || nudo.matches("[0-9.]+");
            if ("https".equalsIgnoreCase(d.uri().getScheme())) {
                SSLSocket s = (SSLSocket) tls.createSocket(socket, nudo, d.porta(), true);
                SSLParameters p = s.getSSLParameters();
                if (!letterale) {
                    p.setServerNames(List.of(new SNIHostName(nudo)));
                }
                p.setEndpointIdentificationAlgorithm("HTTPS");   // nome del certificato = host
                s.setSSLParameters(p);
                s.startHandshake();
                socket = s;
            }
            OutputStream out = socket.getOutputStream();
            String percorso = d.uri().getRawPath() == null || d.uri().getRawPath().isEmpty()
                    ? "/" : d.uri().getRawPath();
            if (d.uri().getRawQuery() != null) {
                percorso += "?" + d.uri().getRawQuery();
            }
            boolean portaStandard = d.uri().getPort() == -1;
            StringBuilder r = new StringBuilder()
                    .append(metodo).append(' ').append(percorso).append(" HTTP/1.1\r\n")
                    .append("Host: ").append(nudo.contains(":") ? "[" + nudo + "]" : nudo)
                    .append(portaStandard ? "" : ":" + d.porta()).append("\r\n")
                    .append("User-Agent: verumdoc-verifica-firme\r\n")
                    .append("Accept-Encoding: identity\r\nConnection: close\r\n");
            if (corpo != null) {
                if (tipo != null) {
                    r.append("Content-Type: ").append(tipo).append("\r\n");
                }
                r.append("Content-Length: ").append(corpo.length).append("\r\n");
            }
            out.write(r.append("\r\n").toString().getBytes(StandardCharsets.ISO_8859_1));
            if (corpo != null) {
                out.write(corpo);
            }
            out.flush();
            return risposta(socket, new BufferedInputStream(socket.getInputStream()), scadenza);
        } finally {
            socket.close();
        }
    }

    /** Una riga d'intestazione: il tempo complessivo si controlla a ogni byte e il
     *  timeout di lettura è il tempo residuo (un server che manda un byte ogni tanto
     *  non tiene aperta la connessione oltre la scadenza). */
    private String riga(Socket socket, InputStream in, long scadenza) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int c;
        while (true) {
            tempoScaduto(scadenza);
            residuo(socket, scadenza);
            c = in.read();
            if (c == -1 || c == '\n') {
                break;
            }
            if (b.size() > 8192) {
                throw new IOException("intestazione oltre 8 KB");
            }
            if (c != '\r') {
                b.write(c);
            }
        }
        if (c == -1 && b.size() == 0) {
            throw new IOException("risposta interrotta");
        }
        return b.toString(StandardCharsets.ISO_8859_1);
    }

    private static void residuo(Socket socket, long scadenza) throws IOException {
        long ms = (scadenza - System.nanoTime()) / 1_000_000L;
        socket.setSoTimeout((int) Math.max(1, Math.min(ms, Integer.MAX_VALUE)));
    }

    private void tempoScaduto(long scadenza) throws IOException {
        if (System.nanoTime() > scadenza) {
            throw new IOException("tempo massimo dello scaricamento superato");
        }
    }

    private byte[] risposta(Socket socket, InputStream in, long scadenza) throws IOException {
        String stato = riga(socket, in, scadenza);
        String[] parti = stato.split(" ", 3);
        if (parti.length < 2 || !parti[0].startsWith("HTTP/1.")) {
            throw new IOException("risposta non HTTP/1.x");
        }
        if (!parti[1].equals("200")) {
            throw new IOException("risposta " + parti[1]);
        }
        long lunghezza = -1;
        boolean chunked = false;
        int intestazioni = 0;
        for (String h = riga(socket, in, scadenza); !h.isEmpty(); h = riga(socket, in, scadenza)) {
            if (++intestazioni > 100) {
                throw new IOException("troppe intestazioni");
            }
            int i = h.indexOf(':');
            if (i <= 0) {
                continue;
            }
            String nome = h.substring(0, i).trim().toLowerCase(Locale.ROOT);
            String valore = h.substring(i + 1).trim();
            if (nome.equals("content-length")) {
                try {
                    lunghezza = Long.parseLong(valore);
                } catch (NumberFormatException e) {
                    throw new IOException("Content-Length non valido");
                }
            } else if (nome.equals("transfer-encoding")) {
                chunked = valore.toLowerCase(Locale.ROOT).contains("chunked");
            }
        }
        if (lunghezza > rispostaMax) {
            throw new IOException("risposta oltre " + rispostaMax + " byte");
        }
        ByteArrayOutputStream uscita = new ByteArrayOutputStream();
        if (chunked) {
            while (true) {
                tempoScaduto(scadenza);
                String dimensione = riga(socket, in, scadenza).split(";", 2)[0].trim();
                long n;
                try {
                    n = Long.parseLong(dimensione, 16);
                } catch (NumberFormatException e) {
                    throw new IOException("blocco chunked non valido");
                }
                if (n == 0) {
                    return uscita.toByteArray();
                }
                copia(socket, in, uscita, n, scadenza);
                riga(socket, in, scadenza);   // CRLF dopo il blocco
            }
        }
        copia(socket, in, uscita, lunghezza, scadenza);
        return uscita.toByteArray();
    }

    /** Copia {@code n} byte (o fino alla chiusura con n = -1) entro il limite di dimensione. */
    private void copia(Socket socket, InputStream in, ByteArrayOutputStream uscita, long n,
                       long scadenza)
            throws IOException {
        byte[] blocco = new byte[64 * 1024];
        long restanti = n;
        while (n < 0 || restanti > 0) {
            tempoScaduto(scadenza);
            residuo(socket, scadenza);
            int max = n < 0 ? blocco.length : (int) Math.min(blocco.length, restanti);
            int letti = in.read(blocco, 0, max);
            if (letti == -1) {
                if (n < 0) {
                    return;
                }
                throw new IOException("risposta interrotta");
            }
            if (uscita.size() + (long) letti > rispostaMax) {
                throw new IOException("risposta oltre " + rispostaMax + " byte");
            }
            uscita.write(blocco, 0, letti);
            restanti -= letti;
        }
    }
}
