import { Router } from "express";
import { getGlobalTrends } from "../services/steam.service.js";

export const trendsRouter = Router();

trendsRouter.get("/global", async (_req, res) => {
  const games = await getGlobalTrends();
  res.json({ games });
});
