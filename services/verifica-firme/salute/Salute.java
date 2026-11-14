// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * HEALTHCHECK dell'immagine senza curl/wget: GET del percorso «live» dichiarato
 * in infra/helm/values.yaml (services.verifica-firme.health.live).
 */
public final class Salute {
    public static void main(String[] args) throws Exception {
        var client = java.net.http.HttpClient.newHttpClient();
        var richiesta = java.net.http.HttpRequest.newBuilder(
                java.net.URI.create("http://localhost:8080/api/v1/health/live"))
                .timeout(java.time.Duration.ofSeconds(3)).build();
        var risposta = client.send(richiesta, java.net.http.HttpResponse.BodyHandlers.discarding());
        System.exit(risposta.statusCode() == 200 ? 0 : 1);
    }
}
