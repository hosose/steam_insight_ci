import { env } from "../config/env.js";

const STEAM_API_BASE = "https://api.steampowered.com";

export type SteamProfileResult = {
  steamId: string;
  personaName: string;
  avatarUrl: string;
  profileUrl: string;
  country?: string;
  level?: number;
  gameCount?: number;
  totalPlaytimeHours?: number;
  achievementRate?: number;
};

export type TrendGame = {
  name: string;
  genre: string;
  currentPlayers: number;
  changePercent: number;
};

const MOCK_PROFILE: SteamProfileResult = {
  steamId: "76561197960287930",
  personaName: "Gaben",
  avatarUrl: "https://avatars.steamstatic.com/fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb_full.jpg",
  profileUrl: "https://steamcommunity.com/id/gaben",
  country: "United States",
  level: 47,
  gameCount: 247,
  totalPlaytimeHours: 1842,
  achievementRate: 68,
};

const MOCK_TRENDS: TrendGame[] = [
  { name: "Counter-Strike 2", genre: "FPS", currentPlayers: 1418229, changePercent: 12.4 },
  { name: "Dota 2", genre: "MOBA", currentPlayers: 712451, changePercent: 5.8 },
  { name: "PUBG: BATTLEGROUNDS", genre: "BATTLE ROYALE", currentPlayers: 644938, changePercent: 9.1 },
  { name: "Rust", genre: "SURVIVAL", currentPlayers: 159082, changePercent: -3.2 },
];

const MOCK_INFLUENCERS = [
  { id: "01", name: "Anomaly", country: "Sweden", publicFriends: 3411, initials: "AN" },
  { id: "02", name: "shroud", country: "Canada", publicFriends: 2917, initials: "SH" },
  { id: "03", name: "S1mple", country: "Ukraine", publicFriends: 3086, initials: "S1" },
  { id: "04", name: "Ninja", country: "United States", publicFriends: 3842, initials: "NI" },
];

async function steamFetch<T>(path: string): Promise<T | null> {
  if (!env.STEAM_API_KEY) return null;

  try {
    const url = `${STEAM_API_BASE}${path}&key=${env.STEAM_API_KEY}`;
    const response = await fetch(url);
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export async function resolveSteamProfile(query: string): Promise<SteamProfileResult> {
  if (!env.STEAM_API_KEY) {
    return { ...MOCK_PROFILE, personaName: query || MOCK_PROFILE.personaName };
  }

  // TODO: Steam API 연동 - vanity URL resolve, GetPlayerSummaries 등
  return { ...MOCK_PROFILE, personaName: query || MOCK_PROFILE.personaName };
}

export async function getGlobalTrends(): Promise<TrendGame[]> {
  if (!env.STEAM_API_KEY) {
    return MOCK_TRENDS;
  }

  // TODO: Steam Store API / concurrent players API 연동
  return MOCK_TRENDS;
}

export async function getInfluencers() {
  return MOCK_INFLUENCERS;
}

export async function checkSteamApiHealth(): Promise<{ configured: boolean; reachable: boolean }> {
  if (!env.STEAM_API_KEY) {
    return { configured: false, reachable: false };
  }

  const result = await steamFetch<{ response: { server_time: number } }>(
    "/ISteamWebAPIUtil/GetServerInfo/v1/?format=json",
  );

  return {
    configured: true,
    reachable: result !== null,
  };
}
