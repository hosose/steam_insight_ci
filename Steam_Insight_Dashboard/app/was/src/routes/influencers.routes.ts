import { Router } from "express";
import { getInfluencers } from "../services/steam.service.js";

export const influencersRouter = Router();

influencersRouter.get("/", async (_req, res) => {
  const influencers = await getInfluencers();
  res.json({ influencers });
});
