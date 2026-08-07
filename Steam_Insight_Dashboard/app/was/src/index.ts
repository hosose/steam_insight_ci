import cors from "cors";
import express from "express";
import helmet from "helmet";
import { env } from "./config/env.js";
import { healthRouter } from "./routes/health.routes.js";
import { influencersRouter } from "./routes/influencers.routes.js";
import { steamRouter } from "./routes/steam.routes.js";
import { trendsRouter } from "./routes/trends.routes.js";

const app = express();

app.use(helmet());
app.use(
  cors({
    origin: env.CORS_ORIGIN.split(",").map((origin) => origin.trim()),
    credentials: true,
  }),
);
app.use(express.json());

app.use("/health", healthRouter);
app.use("/api/steam", steamRouter);
app.use("/api/trends", trendsRouter);
app.use("/api/influencers", influencersRouter);

app.use((_req, res) => {
  res.status(404).json({ error: "Not Found" });
});

app.listen(env.PORT, () => {
  console.log(`[sinsa-backend] running on http://localhost:${env.PORT}`);
  console.log(`[sinsa-backend] CORS origin: ${env.CORS_ORIGIN}`);
  console.log(
    `[sinsa-backend] Steam API key: ${env.STEAM_API_KEY ? "configured" : "not set (mock mode)"}`,
  );
});
