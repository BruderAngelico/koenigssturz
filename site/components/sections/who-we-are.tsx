import Link from "next/link"
import { Section, SectionHeading } from "@/components/layout/section"
import { Button } from "@/components/ui/button"
import { home } from "@/content/home"

export function WhoWeAre() {
  return (
    <Section id="wer">
      <div className="grid gap-8 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <SectionHeading eyebrow={home.who.eyebrow} title={home.who.title} />
        </div>
        <div className="space-y-4 lg:col-span-7">
          {home.who.paragraphs.map((paragraph) => (
            <p key={paragraph} className="max-w-prose text-lg leading-relaxed">
              {paragraph}
            </p>
          ))}
          <Button asChild variant="outline">
            <Link href="/wer-wir-sind">{home.who.link}</Link>
          </Button>
        </div>
      </div>
    </Section>
  )
}
