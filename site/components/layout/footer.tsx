import Image from "next/image"
import Link from "next/link"
import { NewsletterForm } from "@/components/forms/newsletter-form"
import { siteConfig } from "@/lib/siteConfig"

function Column({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h2 className="font-display text-xl uppercase tracking-wide text-paper">{title}</h2>
      <div className="mt-3">{children}</div>
    </div>
  )
}

export function Footer() {
  const year = new Date().getFullYear()

  return (
    <footer className="border-t-4 border-resistance bg-charcoal text-paper">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-12 sm:px-6 md:grid-cols-2 lg:grid-cols-12">
        <div className="lg:col-span-4">
          <Link href="/" className="inline-flex items-center gap-2">
            <Image src="/brand/mark.svg" alt="" width={44} height={44} unoptimized />
            <span className="font-display text-3xl uppercase">{siteConfig.name}</span>
          </Link>
          <p className="mt-4 max-w-xs leading-relaxed text-paper">{siteConfig.claim}</p>
        </div>

        <div className="lg:col-span-2">
          <Column title="Navigation">
            <ul className="space-y-2">
              {siteConfig.nav.map((item) => (
                <li key={item.href}>
                  <Link href={item.href} className="underline decoration-2 underline-offset-4">
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </Column>
        </div>

        <div className="lg:col-span-2">
          <Column title="Gemeinschaft">
            <ul className="space-y-2">
              {siteConfig.communityNav.map((item) => (
                <li key={item.href}>
                  <Link href={item.href} className="underline decoration-2 underline-offset-4">
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </Column>
        </div>

        <div className="lg:col-span-4">
          <Column title="Kontakt">
            <p>
              Platzhalter:{" "}
              <a className="underline decoration-2 underline-offset-4" href={`mailto:${siteConfig.email}`}>
                {siteConfig.email}
              </a>
            </p>
            <ul className="mt-3 space-y-2">
              {siteConfig.social.map((item) => (
                <li key={item.label}>
                  <a href={item.href} className="underline decoration-2 underline-offset-4" rel="noopener noreferrer">
                    {item.label}
                    <span className="text-signal"> · Platzhalter</span>
                  </a>
                </li>
              ))}
            </ul>
          </Column>
        </div>

        <div className="border-t-2 border-paper/30 pt-8 md:col-span-2 lg:col-span-12">
          <div className="grid gap-8 lg:grid-cols-2">
            <div>
              <h2 className="font-display text-2xl uppercase">Newsletter</h2>
              <div className="mt-4 max-w-md">
                <NewsletterForm />
              </div>
            </div>
            <div>
              <h2 className="font-display text-2xl uppercase">Rechtliches</h2>
              <ul className="mt-4 space-y-2">
                {siteConfig.legalNav.map((item) => (
                  <li key={item.href}>
                    <Link href={item.href} className="underline decoration-2 underline-offset-4">
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
      <div className="border-t-2 border-paper/30">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-4 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <p>
            © {year} {siteConfig.name}. Platzhalter-Auftritt.
          </p>
          <p className="font-display text-lg uppercase tracking-wide text-signal">{siteConfig.slogan}</p>
        </div>
      </div>
    </footer>
  )
}
