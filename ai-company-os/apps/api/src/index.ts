import cors from "@fastify/cors";
import Fastify from "fastify";
import { buildContainer } from "./bootstrap.js";
import { AppError } from "./platform/errors.js";
import { registerRoutes } from "./http/routes.js";

async function main(): Promise<void> {
  const container = buildContainer();
  const app = Fastify({ logger: { level: "info", transport: undefined } });

  await app.register(cors, { origin: true });

  app.setErrorHandler((err, _req, reply) => {
    if (err instanceof AppError) {
      return reply.status(err.statusCode).send({ error: err.code, message: err.message });
    }
    app.log.error(err);
    return reply.status(500).send({ error: "internal_error", message: "Erro interno." });
  });

  registerRoutes(app, container);

  const port = container.env.port;
  await app.listen({ port, host: "0.0.0.0" });
  app.log.info(`AI Company OS API rodando na porta ${port} · LLM: ${container.llm.activeProvider}`);
}

main().catch((err) => {
  console.error("Falha ao iniciar a API:", err);
  process.exit(1);
});
