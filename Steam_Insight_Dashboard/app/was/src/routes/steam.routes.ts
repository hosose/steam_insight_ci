import { Router } from "express";
import { z } from "zod";
import { resolveSteamProfile } from "../services/steam.service.js";

export const steamRouter = Router();

const profileQuerySchema = z.object({
  q: z.string().min(1, "검색어를 입력해주세요"),
});

steamRouter.get("/profile", async (req, res) => {
  const parsed = profileQuerySchema.safeParse(req.query);

  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.flatten() });
    return;
  }

  const profile = await resolveSteamProfile(parsed.data.q);
  res.json({ profile });
});
