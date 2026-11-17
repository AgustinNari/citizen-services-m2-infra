import com.fasterxml.jackson.databind.*;
import java.net.*;
import java.net.http.*;
import java.time.*;
import java.util.*;

/** Runs only inside the isolated core-mock container. Prints no tokens or payloads. */
public class VerifyG1 {
    static final ObjectMapper JSON = new ObjectMapper();
    static final HttpClient HTTP = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    static String stage = "startup";

    static JsonNode call(String method, String path, Object body, String token, int expected) throws Exception {
        String base = path.startsWith("/qa/") ? "http://127.0.0.1:8090" : "http://frontend";
        var request = HttpRequest.newBuilder(URI.create(base + path)).timeout(Duration.ofSeconds(10));
        if (token != null) request.header("Authorization", "Bearer " + token);
        request.header("Content-Type", "application/json");
        request.method(method, body == null ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofByteArray(JSON.writeValueAsBytes(body)));
        var response = HTTP.send(request.build(), HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != expected) throw new IllegalStateException("HTTP " + response.statusCode() + " expected " + expected);
        return JSON.readTree(response.body());
    }

    static String login(String username, String variable) throws Exception {
        return call("POST", "/api/auth/login", Map.of("username", username,
                "password", System.getenv(variable)), null, 200).path("token").asText();
    }

    static JsonNode routed(String ticket) throws Exception {
        for (int i = 0; i < 45; i++) {
            for (JsonNode event : call("GET", "/qa/events", null, null, 200).path("events"))
                if (event.path("ticketId").asText().equals(ticket) && event.path("updateType").asText().equals("ROUTED")
                        && event.path("direction").asText().equals("M2_TO_CORE")) return event;
            Thread.sleep(1000);
        }
        throw new IllegalStateException("No validated ROUTED event received through AMQPS");
    }

    public static void main(String[] args) {
        long started = System.nanoTime();
        try {
            stage = "frontend-and-api";
            var page = HTTP.send(HttpRequest.newBuilder(URI.create("http://frontend/")).build(), HttpResponse.BodyHandlers.ofString());
            if (page.statusCode() != 200 || !page.body().contains("<html")) throw new IllegalStateException("Frontend unavailable");
            String citizen = login("citizen@example.test", "MOCK_CITIZEN_PASSWORD");
            if (!call("GET", "/api/auth/me", null, citizen, 200).path("role").asText().equals("CITIZEN"))
                throw new IllegalStateException("Unexpected citizen role");
            String agent = login("agent@example.test", "MOCK_AGENT_PASSWORD");
            String admin = login("module.admin@example.test", "MOCK_ADMIN_PASSWORD");
            stage = "catalog-and-anonymous-ticket";
            JsonNode category = call("GET", "/api/catalog/categories", null, null, 200).get(0);
            JsonNode subcategory = call("GET", "/api/catalog/categories/" + category.path("id").asText()
                    + "/subcategories", null, null, 200).get(0);
            JsonNode type = null;
            for (JsonNode candidate : call("GET", "/api/admin/catalog/subcategories/" + subcategory.path("id").asText()
                    + "/request-types", null, admin, 200)) {
                if (candidate.path("code").asText().equals("QA_G1_EXTERNAL_ANONYMOUS")) type = candidate;
            }
            if (type == null) {
                var fixture = JSON.createObjectNode().put("subcategoryId", subcategory.path("id").asLong())
                        .put("code", "QA_G1_EXTERNAL_ANONYMOUS").put("name", "Caso descartable G1")
                        .put("description", "Datos sintéticos del laboratorio QA")
                        .put("ticketType", "COMPLAINT").put("responsibleAreaId", "M3")
                        .put("minimumPriority", "LOW").put("baseRisk", "LOW")
                        .put("affectedPopulationFactor", 0).put("allowsAnonymous", true).put("requiresLocation", false);
                type = call("POST", "/api/admin/catalog/request-types", fixture, admin, 201);
            }
            if (type == null || !type.path("allowsAnonymous").asBoolean()) throw new IllegalStateException("Expected catalog fixture unavailable");
            call("PUT", "/api/admin/catalog/request-types/" + type.path("id").asText() + "/form",
                    Map.of("fields", List.of(Map.of("code", "qaSynthetic", "label", "¿Es una prueba sintética?",
                            "type", "BOOLEAN", "required", true, "allowUnknown", false, "displayOrder", 1,
                            "config", Map.of(), "riskRules", List.of()))), admin, 200);
            call("GET", "/api/catalog/request-types/" + type.path("id").asText() + "/form", null, null, 200);
            String neighborhood = call("GET", "/api/catalog/neighborhoods", null, null, 200).get(0).path("id").asText();
            Map<String, Object> body = Map.of("requestTypeId", type.path("id").asLong(),
                    "summary", "Prueba G1 " + UUID.randomUUID(), "description", "Bache de prueba local sin peligro inmediato",
                    "formData", Map.of("qaSynthetic", true),
                    "anonymousAccessPassword", UUID.randomUUID().toString(),
                    "location", Map.of("addressLine", "Dirección sintética QA", "neighborhoodId", neighborhood,
                            "latitude", -34.6037, "longitude", -58.3816));
            JsonNode created = call("POST", "/api/tickets", body, null, 201);
            String ticket = created.path("ticketId").asText();
            stage = "review-and-route";
            call("POST", "/api/tickets/" + ticket + "/review", Map.of(), agent, 200);
            JsonNode route = call("POST", "/api/tickets/" + ticket + "/route", Map.of(), agent, 200);
            if (!route.path("currentStatus").asText().equals("ROUTED")) throw new IllegalStateException("Ticket was not routed externally");
            stage = "outbox-amqps-core";
            JsonNode sent = routed(ticket);
            String producer = Map.of("M3", "obras", "M4", "habilitaciones", "M5", "rentas", "M6", "ambiente",
                    "M7", "transito", "M8", "desarrollo-social").get(route.path("responsibleAreaId").asText());
            var actor = JSON.createObjectNode().put("type", "SYSTEM").putNull("id");
            var data = JSON.createObjectNode().put("ticketId", ticket).put("updateType", "STARTED")
                    .put("publicMessage", "El área simulada inició el trabajo").put("updateOccurredAt", Instant.now().toString());
            data.set("updatedBy", actor);
            var event = JSON.createObjectNode().put("eventId", UUID.randomUUID().toString()).put("eventType", "updateTicketStatus")
                    .put("eventVersion", "1.0").put("occurredAt", Instant.now().toString()).put("sourceModule", producer)
                    .put("causationId", sent.path("eventId").asText());
            event.set("correlationId", sent.get("correlationId")); event.set("data", data);
            stage = "contract-rejection";
            var invalid = event.deepCopy(); invalid.put("undeclaredField", true);
            call("POST", "/qa/publish", invalid, null, 422);
            stage = "core-amqps-inbox";
            call("POST", "/qa/publish", event, null, 202);
            call("POST", "/qa/publish", event, null, 200);
            boolean progressed = false;
            for (int i = 0; i < 30; i++) {
                JsonNode tickets = call("GET", "/api/tickets?size=100", null, agent, 200).path("content");
                for (JsonNode row : tickets)
                    if (row.path("id").asText().equals(ticket) && row.path("currentStatus").asText().equals("IN_PROGRESS")) progressed = true;
                if (progressed) break;
                Thread.sleep(1000);
            }
            if (!progressed) throw new IllegalStateException("Inbox did not apply STARTED");
            System.out.println(JSON.writeValueAsString(Map.of("status", "PASS", "ticketId", ticket,
                    "outboundEventId", sent.path("eventId").asText(), "incomingEventId", event.path("eventId").asText(),
                    "flow", "anonymous create -> review -> ROUTED -> Outbox/AMQPS -> Core -> STARTED/AMQPS -> Inbox -> IN_PROGRESS",
                    "mockCitizenLogin", true, "mockAgentLogin", true, "invalidContractRejected", true, "coreDeduplication", true,
                    "seconds", (System.nanoTime() - started) / 1_000_000_000.0)));
        } catch (Exception failed) {
            System.err.println("G1 verification FAIL at " + stage + ": " + failed.getClass().getSimpleName());
            if (failed instanceof IllegalStateException && failed.getMessage() != null
                    && failed.getMessage().matches("HTTP [0-9]{3} expected [0-9]{3}"))
                System.err.println(failed.getMessage());
            System.exit(1);
        }
    }
}
