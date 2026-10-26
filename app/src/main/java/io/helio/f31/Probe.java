package io.helio.f31;

import jakarta.servlet.annotation.WebServlet;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/** The digest is computed from installed WAR bytes, never from an expected-value input. */
@WebServlet(urlPatterns = {"/health", "/version", "/artifact-digest"})
public final class Probe extends HttpServlet {
    @Override protected void doGet(HttpServletRequest request, HttpServletResponse response) throws IOException {
        response.setContentType("text/plain;charset=UTF-8");
        response.setHeader("Cache-Control", "no-store");
        switch (request.getServletPath()) {
            case "/health" -> {
                response.setContentType("application/json");
                response.getWriter().print("{\"status\":\"UP\"}");
            }
            case "/version" -> {
                try (var input = getClass().getResourceAsStream("/version.txt")) {
                    if (input == null) { response.sendError(503); return; }
                    response.getWriter().print(new String(input.readAllBytes(), StandardCharsets.UTF_8).trim());
                }
            }
            case "/artifact-digest" -> {
                try (var input = Files.newInputStream(Path.of(System.getProperty("catalina.base"), "webapps", "ROOT.war"))) {
                    var digest = MessageDigest.getInstance("SHA-256");
                    var buffer = new byte[8192];
                    for (int count; (count = input.read(buffer)) >= 0;) digest.update(buffer, 0, count);
                    response.getWriter().print("sha256:" + HexFormat.of().formatHex(digest.digest()));
                } catch (NoSuchAlgorithmException exception) { throw new IOException(exception); }
            }
            default -> response.sendError(404);
        }
    }
}
