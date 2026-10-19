import type { Metadata } from "next"
import { siteConfig } from "@/lib/siteConfig"

export function pageMeta(title: string, description: string, index = true): Metadata {
  return {
    title,
    description,
    robots: index ? { index: true, follow: true } : { index: false, follow: true },
    openGraph: {
      title: `${title} · ${siteConfig.name}`,
      description,
    },
  }
}
