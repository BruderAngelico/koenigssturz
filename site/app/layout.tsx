import type { Metadata } from "next"
import { Anton, Space_Grotesk } from "next/font/google"
import { AgeGate } from "@/components/layout/age-gate"
import { Footer } from "@/components/layout/footer"
import { Header } from "@/components/layout/header"
import { SkipLink } from "@/components/layout/skip-link"
import { siteConfig } from "@/lib/siteConfig"
import "./globals.css"

const anton = Anton({
  weight: "400",
  subsets: ["latin", "latin-ext"],
  variable: "--font-anton",
  display: "swap",
})

const space = Space_Grotesk({
  subsets: ["latin", "latin-ext"],
  variable: "--font-space",
  display: "swap",
})

const themeBoot = `(function(){try{var k=${JSON.stringify(siteConfig.themeStorageKey)};var t=localStorage.getItem(k);var dark=t==="dark"||(t!=="light"&&window.matchMedia("(prefers-color-scheme: dark)").matches);if(dark)document.documentElement.classList.add("dark");}catch(e){}})();`

export const metadata: Metadata = {
  metadataBase: new URL(siteConfig.url),
  title: {
    default: `${siteConfig.name} — ${siteConfig.claim}`,
    template: `%s · ${siteConfig.name}`,
  },
  description: siteConfig.description,
  applicationName: siteConfig.name,
  openGraph: {
    type: "website",
    locale: siteConfig.locale,
    siteName: siteConfig.name,
    title: `${siteConfig.name} — ${siteConfig.claim}`,
    description: siteConfig.description,
  },
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="de" className={`${anton.variable} ${space.variable}`} suppressHydrationWarning>
      <body className="antialiased">
        <script dangerouslySetInnerHTML={{ __html: themeBoot }} />
        <SkipLink />
        <Header />
        <main id="inhalt" tabIndex={-1} className="outline-none">
          <noscript>
            <div className="border-b-4 border-charcoal bg-signal px-4 py-3 text-center text-sm font-semibold text-charcoal">
              Hinweis: Dieser Auftritt richtet sich an Erwachsene ab 18 Jahren.
            </div>
          </noscript>
          {children}
        </main>
        <Footer />
        <AgeGate />
      </body>
    </html>
  )
}
