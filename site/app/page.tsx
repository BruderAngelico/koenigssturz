import { ClosingCta } from "@/components/sections/closing-cta"
import { CouncilSteps } from "@/components/sections/council-steps"
import { Faq } from "@/components/sections/faq"
import { GetInvolved } from "@/components/sections/get-involved"
import { Hero } from "@/components/sections/hero"
import { SaferSpace } from "@/components/sections/safer-space"
import { Solidarity } from "@/components/sections/solidarity"
import { ValuesBar } from "@/components/sections/values-bar"
import { Voices } from "@/components/sections/voices"
import { WhoWeAre } from "@/components/sections/who-we-are"
import { siteConfig } from "@/lib/siteConfig"
import type { Metadata } from "next"

export const metadata: Metadata = {
  title: { absolute: `${siteConfig.name} — ${siteConfig.claim}` },
  description: siteConfig.description,
  openGraph: {
    title: `${siteConfig.name} — ${siteConfig.claim}`,
    description: siteConfig.description,
  },
}

export default function HomePage() {
  return (
    <>
      <Hero />
      <ValuesBar />
      <WhoWeAre />
      <CouncilSteps />
      <Solidarity />
      <SaferSpace />
      <GetInvolved />
      <Voices />
      <Faq />
      <ClosingCta />
    </>
  )
}
