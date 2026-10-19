import Link from "next/link"
import { Button } from "@/components/ui/button"
import { home } from "@/content/home"

export function ClosingCta() {
  return (
    <section className="border-b-2 border-line bg-resistance text-paper" aria-labelledby="schluss-title">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-14 sm:px-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-2xl">
          <h2 id="schluss-title" className="font-display text-4xl uppercase leading-[0.9] sm:text-6xl">
            {home.closing.title}
          </h2>
          <p className="mt-4 text-lg leading-relaxed text-paper">{home.closing.text}</p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button asChild variant="inverse" size="lg">
            <Link href="/registrieren">{home.closing.primary}</Link>
          </Button>
          <Button asChild variant="outline" size="lg" className="border-paper text-paper hover:bg-paper hover:text-charcoal">
            <Link href="/regeln">{home.closing.secondary}</Link>
          </Button>
        </div>
      </div>
    </section>
  )
}
