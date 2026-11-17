# Same application build stage: BuildKit can reuse it within the QA build.
# No service-image dependency or registry publication is required.
FROM eclipse-temurin:21-jdk-noble@sha256:06c1f157dacf6b68198bd9a4186dd4c34ef24e65d7bbbe2a0cf8e62571ad38c9 AS build
WORKDIR /workspace
COPY . .
RUN chmod +x mvnw \
    && ./mvnw -B clean package -DskipTests
RUN ./mvnw -B -Dtest=CorePublicationPolicyTest test

# Reuse the application's existing AMQP, Jackson and JSON Schema libraries.
FROM eclipse-temurin:21-jdk-noble@sha256:06c1f157dacf6b68198bd9a4186dd4c34ef24e65d7bbbe2a0cf8e62571ad38c9
WORKDIR /core
COPY --from=build /workspace/target/*.jar /tmp/backend.jar
RUN jar xf /tmp/backend.jar BOOT-INF/lib BOOT-INF/classes/events \
    && rm /tmp/backend.jar \
    && mkdir /state && chown 10001:10001 /state
COPY --from=qa CoreMock.java /core/CoreMock.java
COPY --from=qa VerifyG1.java /core/VerifyG1.java
USER 10001:10001
EXPOSE 8090
ENTRYPOINT ["java", "--class-path", "/core/BOOT-INF/lib/*", "/core/CoreMock.java"]
