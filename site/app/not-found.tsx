import { Button } from "@/components/ui/button"
import Link from "next/link"

export default function NotFound() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
      <p className="inline-block bg-signal px-2 py-1 text-sm font-bold uppercase tracking-wide text-charcoal">404</p>
      <h1 className="mt-4 max-w-3xl font-display text-5xl uppercase leading-[0.86] sm:text-7xl">
        Diese Seite ist nicht im Rat.
      </h1>
      <p className="mt-6 max-w-xl text-lg leading-relaxed">
        Der Link führt ins Leere. Geh zurück zum Start, oder lies die Regeln, wenn du suchst, wie wir miteinander umgehen.
      </p>
      <div className="mt-8 flex flex-col gap-3 sm:flex-row">
        <Button asChild>
          <Link href="/">Zum Start</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/regeln">Regeln</Link>
        </Button>
      </div>
    </div>
  )
}
