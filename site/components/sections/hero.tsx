import { Button } from "@/components/ui/button"
import { home } from "@/content/home"
import Image from "next/image"
import Link from "next/link"

export function Hero() {
  return (
    <section className="overflow-hidden border-b-2 border-line" aria-labelledby="hero-title">
      <div className="mx-auto grid max-w-6xl items-center gap-10 px-4 py-12 sm:px-6 lg:grid-cols-12 lg:py-16">
        <div className="lg:col-span-7">
          <p className="inline-block bg-signal px-2 py-1 text-sm font-bold uppercase tracking-wider text-charcoal">
            {home.hero.eyebrow}
          </p>
          <h1
            id="hero-title"
            className="mt-5 flex flex-col items-start font-display text-[2.55rem] uppercase leading-none sm:text-6xl xl:text-7xl"
          >
            <span>{home.hero.titleLead}</span>
            <span>{home.hero.titleMid}</span>
            <span className="mt-1 bg-hemp px-1 text-paper">{home.hero.titleAccent}</span>
            <span className="mt-1 bg-resistance px-1 text-paper">{home.hero.titleEnd}</span>
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed">{home.hero.sub}</p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Button asChild size="lg">
              <Link href="/registrieren">{home.hero.primary}</Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link href="#werte">{home.hero.secondary}</Link>
            </Button>
          </div>
        </div>
        <div className="lg:col-span-5">
          <div className="border-2 border-line bg-paper shadow-hard">
            <Image
              src="/brand/poster.svg"
              alt="Plakatmotiv: Hanfblatt, erhobene Faust, Zahnrad, Ähren, Stern und eine Menschenkette."
              width={480}
              height={640}
              priority
              unoptimized
              sizes="(min-width: 1024px) 40vw, 100vw"
              className="h-auto w-full"
            />
          </div>
        </div>
      </div>
      <div className="border-t-2 border-line bg-resistance text-paper">
        <p className="mx-auto max-w-6xl px-4 py-3 font-display text-xl uppercase tracking-wide sm:px-6 sm:text-2xl">
          {home.hero.ribbon}
        </p>
      </div>
    </section>
  )
}
