import Link from "next/link"
import { Section } from "@/components/layout/section"
import { Button } from "@/components/ui/button"
import { home } from "@/content/home"

export function SaferSpace() {
  return (
    <Section id="safer" tone="ink">
      <div className="grid gap-6 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-signal">{home.safer.eyebrow}</p>
          <h2 className="mt-2 font-display text-4xl uppercase leading-[0.9] text-paper sm:text-5xl">
            {home.safer.title}
          </h2>
        </div>
        <div className="space-y-4 lg:col-span-7">
          {home.safer.paragraphs.map((paragraph) => (
            <p key={paragraph} className="text-lg leading-relaxed text-paper">
              {paragraph}
            </p>
          ))}
          <Button asChild variant="inverse">
            <Link href="/regeln">{home.safer.link}</Link>
          </Button>
        </div>
      </div>
    </Section>
  )
}
