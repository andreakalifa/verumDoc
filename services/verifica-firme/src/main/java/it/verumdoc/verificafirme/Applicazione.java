// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
package it.verumdoc.verificafirme;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Servizio interno di verifica delle firme (R08, decisione D01): EU DSS dietro
 * l'interfaccia dei verificatori intercambiabili di VerumDoc
 * ({@code libs/shared/firme/dss.py}). Nessuna esposizione all'ingress: lo
 * chiamano solo validation e ciclo-vita con un token di servizio.
 */
@SpringBootApplication
@EnableScheduling
@ConfigurationPropertiesScan
public class Applicazione {
    public static void main(String[] args) {
        SpringApplication.run(Applicazione.class, args);
    }
}
