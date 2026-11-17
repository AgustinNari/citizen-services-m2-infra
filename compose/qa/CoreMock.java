import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.*;
import com.networknt.schema.*;
import com.rabbitmq.client.*;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import javax.net.ssl.*;
import java.io.*;
import java.net.InetSocketAddress;
import java.nio.file.*;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.util.*;
import java.util.concurrent.Executors;

/** Local QA transport fixture, NOT an implementation of the shared Core API.
 * Schemas come from the exact Backend image. No synthetic M1 identity is promoted.
 * Outgoing events are validated, recorded as metadata and ACKed after persistence.
 * /qa/publish injects a valid external updateTicketStatus; it never changes M2 DB.
 * Full Core subscriptions, authorization, retry chain and DLQ API are out of scope.
 */
public class CoreMock {
    static final Path STATE = Path.of("/state");
    static final ObjectMapper JSON = new ObjectMapper(JsonFactory.builder()
            .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION).build())
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
    static final Map<String, JsonSchema> SCHEMAS = schemas();
    static Connection connection;
    static Channel consumer;

    static Map<String, JsonSchema> schemas() {
        String base = "https://municipalidad-uade.example/m2/events/v1.78/";
        var factory = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012,
                b -> b.schemaMappers(m -> m.mapPrefix(base, "file:///core/BOOT-INF/classes/events/v1.78/")));
        var config = SchemaValidatorsConfig.builder().formatAssertionsEnabled(true).build();
        Map<String, JsonSchema> result = new HashMap<>();
        for (String type : List.of("ticketCreated", "ticketUpdated", "updateTicketStatus"))
            result.put(type, factory.getSchema(SchemaLocation.of(base + type + ".schema.json"), config));
        return Map.copyOf(result);
    }

    static void validate(JsonNode event, boolean incoming) {
        String type = event.path("eventType").asText();
        JsonSchema schema = SCHEMAS.get(type);
        if (schema == null || !schema.validate(event).isEmpty()
                || !"1.0".equals(event.path("eventVersion").asText("1.0"))) throw new IllegalArgumentException();
        if (incoming) {
            if (!type.equals("updateTicketStatus") || !Set.of("obras", "ambiente", "transito",
                    "desarrollo-social", "habilitaciones", "rentas").contains(event.path("sourceModule").asText()))
                throw new IllegalArgumentException();
        } else if (type.equals("updateTicketStatus")
                || !event.path("sourceModule").asText().equals("atencion-ciudadana")) throw new IllegalArgumentException();
    }

    static Path receipt(JsonNode event) {
        return STATE.resolve(UUID.fromString(event.path("eventId").asText()).toString() + ".json");
    }

    static synchronized void record(JsonNode event, String direction) throws IOException {
        var metadata = JSON.createObjectNode();
        for (String key : List.of("eventId", "eventType", "correlationId", "causationId"))
            if (event.has(key)) metadata.set(key, event.get(key));
        metadata.put("ticketId", event.path("data").path("ticketId").asText());
        metadata.put("direction", direction);
        metadata.put("effects", 1);
        metadata.put("deliveries", Files.exists(receipt(event))
                ? JSON.readTree(Files.readAllBytes(receipt(event))).path("deliveries").asInt(1) + 1 : 1);
        if (event.path("data").has("updateType")) metadata.put("updateType", event.path("data").path("updateType").asText());
        Path temporary = Files.createTempFile(STATE, "receipt-", ".tmp");
        try {
            Files.write(temporary, JSON.writeValueAsBytes(metadata));
            Files.move(temporary, receipt(event), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally { Files.deleteIfExists(temporary); }
    }

    static ConnectionFactory factory() throws Exception {
        KeyStore trust = KeyStore.getInstance(KeyStore.getDefaultType());
        trust.load(null, null);
        try (InputStream ca = Files.newInputStream(Path.of("/certificates/ca.crt"))) {
            trust.setCertificateEntry("qa-root", CertificateFactory.getInstance("X.509").generateCertificate(ca));
        }
        var managers = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        managers.init(trust);
        SSLContext tls = SSLContext.getInstance("TLS");
        tls.init(null, managers.getTrustManagers(), null);
        var factory = new ConnectionFactory();
        factory.setHost("rabbitmq"); factory.setPort(5671); factory.setVirtualHost("core");
        factory.setUsername(System.getenv("QA_RABBIT_USER"));
        factory.setPassword(System.getenv("QA_RABBIT_PASSWORD"));
        factory.useSslProtocol(tls); factory.enableHostnameVerification();
        factory.setConnectionTimeout(5000); factory.setHandshakeTimeout(5000);
        factory.setRequestedHeartbeat(10); factory.setNetworkRecoveryInterval(2000);
        return factory;
    }

    static void connect() throws Exception {
        connection = factory().newConnection("m2-qa-core");
        consumer = connection.createChannel();
        consumer.exchangeDeclare("muni.inbox", "fanout", true);
        consumer.exchangeDeclare("qa.rejected", "fanout", true);
        consumer.queueDeclare("q.qa.rejected", true, false, false, null);
        consumer.queueBind("q.qa.rejected", "qa.rejected", "");
        Map<String, Object> rejected = Map.of("x-dead-letter-exchange", "qa.rejected");
        consumer.queueDeclare("q.qa.ingress", true, false, false, rejected);
        consumer.queueBind("q.qa.ingress", "muni.inbox", "");
        consumer.queueDeclare("q.atencion-ciudadana", true, false, false, rejected);
        consumer.basicQos(1);
        consumer.basicConsume("q.qa.ingress", false, (tag, delivery) -> {
            try {
                if (delivery.getBody().length > 1048576) throw new IllegalArgumentException();
                JsonNode event = JSON.readTree(delivery.getBody());
                validate(event, false);
                record(event, "M2_TO_CORE");
                consumer.basicAck(delivery.getEnvelope().getDeliveryTag(), false);
            } catch (IllegalArgumentException | com.fasterxml.jackson.core.JsonProcessingException invalid) {
                consumer.basicNack(delivery.getEnvelope().getDeliveryTag(), false, false);
                System.err.println("QA_CORE_EVENT_REJECTED");
            } catch (Exception unavailable) {
                // No ACK on persistence/connection failure. Throttle storage failures
                // before requeueing, so a read-only/full volume cannot create a hot loop.
                System.err.println("QA_CORE_PERSISTENCE_OR_CHANNEL_FAILURE");
                // Do not abort the autorecovering channel on a transient broker disconnect.
                // If persistence fails on an open channel, requeue the unacknowledged delivery.
                try {
                    Thread.sleep(5000);
                    if (consumer.isOpen()) consumer.basicNack(delivery.getEnvelope().getDeliveryTag(), false, true);
                }
                catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
                catch (Exception ignored) { }
            }
        }, tag -> {});
    }

    static boolean healthy() {
        return connection != null && connection.isOpen() && consumer != null && consumer.isOpen();
    }

    static synchronized boolean publish(JsonNode event) throws Exception {
        validate(event, true);
        if (Files.exists(receipt(event))) return false;
        try (Channel channel = connection.createChannel()) {
            channel.confirmSelect();
            var properties = new AMQP.BasicProperties.Builder().contentType("application/json")
                    .contentEncoding("UTF-8").deliveryMode(2).messageId(event.path("eventId").asText())
                    .type("updateTicketStatus").userId(System.getenv("QA_RABBIT_USER")).build();
            channel.basicPublish("", "q.atencion-ciudadana", properties, JSON.writeValueAsBytes(event));
            channel.waitForConfirmsOrDie(5000);
            record(event, "CORE_TO_M2");
        }
        return true;
    }

    static void reply(HttpExchange request, int status, Object body) throws IOException {
        byte[] bytes = JSON.writeValueAsBytes(body);
        request.getResponseHeaders().set("Content-Type", "application/json");
        request.getResponseHeaders().set("Cache-Control", "no-store");
        request.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = request.getResponseBody()) { output.write(bytes); }
    }

    static void handle(HttpExchange request) throws IOException {
        try {
            String path = request.getRequestURI().getPath();
            if (request.getRequestMethod().equals("GET") && path.equals("/health")) {
                reply(request, healthy() ? 200 : 503, Map.of("status", healthy() ? "UP" : "DOWN"));
            } else if (request.getRequestMethod().equals("GET") && path.equals("/qa/events")) {
                List<JsonNode> events = new ArrayList<>();
                try (var files = Files.list(STATE)) {
                    for (Path file : files.filter(f -> f.getFileName().toString().endsWith(".json")).sorted().toList())
                        events.add(JSON.readTree(Files.readAllBytes(file)));
                }
                reply(request, 200, Map.of("events", events));
            } else if (request.getRequestMethod().equals("POST") && path.equals("/qa/publish")) {
                byte[] bytes = request.getRequestBody().readNBytes(1048577);
                if (bytes.length > 1048576) { reply(request, 413, Map.of("code", "TOO_LARGE")); return; }
                JsonNode event;
                try { event = JSON.readTree(bytes); validate(event, true); }
                catch (Exception invalid) { reply(request, 422, Map.of("code", "INVALID_CONTRACT")); return; }
                if (!healthy()) { reply(request, 503, Map.of("code", "BROKER_UNAVAILABLE")); return; }
                boolean fresh = publish(event);
                reply(request, fresh ? 202 : 200, Map.of("eventId", event.path("eventId").asText(), "duplicate", !fresh));
            } else reply(request, 404, Map.of("code", "NOT_FOUND"));
        } catch (Exception unavailable) {
            reply(request, 503, Map.of("code", "QA_CORE_UNAVAILABLE"));
        } finally { request.close(); }
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 1 && args[0].equals("replay-int04")) {
            // Explicit local CLI only: replay already recorded events over real AMQPS.
            // No HTTP bypass of the normal /qa/publish idempotency contract.
            JsonNode batch = JSON.readTree(System.in);
            if (!batch.isArray() || batch.size() < 1 || batch.size() > 400) throw new IllegalArgumentException();
            for (JsonNode item : batch) {
                JsonNode event = item.path("event");
                String direction = item.path("direction").asText();
                if (!Set.of("M2_TO_CORE", "CORE_TO_M2").contains(direction)) throw new IllegalArgumentException();
                validate(event, direction.equals("CORE_TO_M2"));
                JsonNode known = JSON.readTree(Files.readAllBytes(receipt(event)));
                for (String key : List.of("eventId", "eventType", "correlationId", "causationId"))
                    if (!known.path(key).equals(event.path(key))) throw new IllegalArgumentException();
                if (!known.path("direction").asText().equals(direction)
                        || !known.path("ticketId").asText().equals(event.path("data").path("ticketId").asText()))
                    throw new IllegalArgumentException();
            }
            try (Connection replay = factory().newConnection("m2-qa-int04-replay"); Channel channel = replay.createChannel()) {
                channel.confirmSelect();
                for (JsonNode item : batch) {
                    JsonNode event = item.path("event");
                    boolean incoming = item.path("direction").asText().equals("CORE_TO_M2");
                    var properties = new AMQP.BasicProperties.Builder().contentType("application/json")
                            .deliveryMode(2).messageId(event.path("eventId").asText())
                            .type(event.path("eventType").asText()).userId(System.getenv("QA_RABBIT_USER")).build();
                    for (int copy = 0; copy < 2; copy++)
                        channel.basicPublish(incoming ? "" : "muni.inbox", incoming ? "q.atencion-ciudadana" : "",
                                true, properties, JSON.writeValueAsBytes(event));
                }
                channel.waitForConfirmsOrDie(5000);
            }
            System.out.println("INT04_REPLAY_CONFIRMED copies=" + batch.size() * 2);
            return;
        }
        try { connect(); }
        catch (Exception unavailable) { System.err.println("QA_CORE_STARTUP_FAILED: revisar RabbitMQ y certificados locales"); System.exit(1); }
        HttpServer server = HttpServer.create(new InetSocketAddress("0.0.0.0", 8090), 0);
        server.createContext("/", CoreMock::handle);
        server.setExecutor(Executors.newFixedThreadPool(4));
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            server.stop(0);
            try { connection.abort(); } catch (Exception ignored) { }
        }));
        server.start();
        System.out.println("QA_CORE_READY: AMQPS verificado; solo red local QA");
    }
}
