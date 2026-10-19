import Link from "next/link"
import { Section, SectionHeading } from "@/components/layout/section"
import { Button } from "@/components/ui/button"
import { home } from "@/content/home"

export function CouncilSteps() {
  return (
    <Section id="raetesystem" tone="alt">
      <SectionHeading eyebrow={home.council.eyebrow} title={home.council.title}>
        {home.council.lede}
      </SectionHeading>
      <ol className="mt-8 grid gap-4 lg:grid-cols-4">
        {home.council.steps.map((step) => (
          <li key={step.n} className="border-2 border-line bg-surface p-4 shadow-hard">
            <span className="font-display text-5xl leading-none text-brand">{step.n}</span>
            <h3 className="mt-3 font-display text-2xl uppercase leading-none">{step.title}</h3>
            <p className="mt-3 leading-relaxed">{step.text}</p>
          </li>
        ))}
      </ol>
      <div className="mt-6">
        <Button asChild variant="outline">
          <Link href="/raetesystem">{home.council.link}</Link>
        </Button>
      </div>
    </Section>
  )
}
