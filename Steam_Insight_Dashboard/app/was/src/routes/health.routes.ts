import { Router } from "express";
import { checkSteamApiHealth } from "../services/steam.service.js";
import { env } from "../config/env.js";

export const healthRouter = Router();

healthRouter.get("/", async (_req, res) => {
  const steam = await checkSteamApiHealth();

  res.json({
    status: "ok",
    service: "sinsa-backend",
    environment: env.NODE_ENV,
    timestamp: new Date().toISOString(),
    steam,
  });
});
