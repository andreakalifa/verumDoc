# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# services/verifica-firme — EU DSS (Java 21, Spring Boot). R08, decisione D01.
# Costruzione: stadio Maven (dipendenze da Maven Central, versioni bloccate nel pom).
FROM maven:3.9.9-eclipse-temurin-21 AS builder
WORKDIR /build
COPY services/verifica-firme/pom.xml ./pom.xml
RUN mvn -q -B dependency:go-offline
COPY services/verifica-firme/src ./src
RUN mvn -q -B -DskipTests package
COPY services/verifica-firme/salute/Salute.java /build/salute/Salute.java
RUN javac -d /build/salute /build/salute/Salute.java

# Stadio finale: JRE, utente non root, filesystem in sola lettura (cache su volume)
FROM eclipse-temurin:21.0.5_11-jre AS runtime
RUN groupadd -r verumdoc && useradd -r -g verumdoc -u 1000 verumdoc \
    && mkdir -p /var/cache/verifica-firme /opt/verifica-firme \
    && chown verumdoc:verumdoc /var/cache/verifica-firme
WORKDIR /opt/verifica-firme
COPY --from=builder /build/target/verifica-firme.jar /opt/verifica-firme/app.jar
COPY --from=builder /build/salute/Salute.class /opt/verifica-firme/salute/Salute.class
USER verumdoc
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75 -Djava.io.tmpdir=/tmp"
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD ["java", "-cp", "/opt/verifica-firme/salute", "Salute"]
EXPOSE 8080
CMD ["java", "-jar", "/opt/verifica-firme/app.jar"]
