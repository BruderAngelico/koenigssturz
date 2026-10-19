import type { MetadataRoute } from "next"
import { siteConfig } from "@/lib/siteConfig"

const paths = [
  "",
  "/wer-wir-sind",
  "/raetesystem",
  "/regeln",
  "/mitmachen",
  "/support",
  "/login",
  "/registrieren",
  "/impressum",
  "/datenschutz",
  "/barrierefreiheit",
]

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date("2026-10-19")
  return paths.map((path) => ({
    url: `${siteConfig.url}${path || "/"}`,
    lastModified,
    changeFrequency: "monthly",
    priority: path === "" ? 1 : 0.7,
  }))
}
