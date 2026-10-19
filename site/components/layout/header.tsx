"use client"

import { Menu } from "lucide-react"
import Image from "next/image"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useState } from "react"
import { ThemeToggle } from "@/components/layout/theme-toggle"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { siteConfig } from "@/lib/siteConfig"
import { cn } from "@/lib/utils"

function isCurrent(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname === href
}

export function Header() {
  const pathname = usePathname()
  const [open, setOpen] = useState(false)

  return (
    <header className="sticky top-0 z-40 border-b-4 border-line bg-surface">
      <div className="h-1.5 bg-resistance" aria-hidden />
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-2 px-4 sm:gap-3 lg:h-20">
        <Link href="/" className="flex min-w-0 items-center gap-2">
          <Image src="/brand/mark.svg" alt="" width={44} height={44} unoptimized className="size-10 shrink-0 sm:size-11" />
          <span className="font-display text-2xl uppercase leading-tight tracking-wide sm:text-3xl">
            {siteConfig.name}
          </span>
        </Link>

        <nav className="ml-4 hidden items-center gap-1 lg:flex" aria-label="Hauptnavigation">
          {siteConfig.nav.map((item) => {
            const current = isCurrent(pathname, item.href)
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={current ? "page" : undefined}
                className={cn(
                  "px-2 py-1 text-sm font-semibold",
                  current && "underline decoration-resistance decoration-4 underline-offset-8",
                )}
              >
                {item.label}
              </Link>
            )
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <ThemeToggle />
          <Button asChild variant="outline" size="sm" className="hidden md:inline-flex">
            <Link href="/login">Login</Link>
          </Button>
          <Button asChild size="sm" className="hidden md:inline-flex">
            <Link href="/registrieren">Registrieren</Link>
          </Button>

          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger
              className="lift inline-flex size-11 items-center justify-center border-2 border-line lg:hidden"
              aria-label="Menü öffnen"
            >
              <Menu aria-hidden />
            </SheetTrigger>
            <SheetContent>
              <SheetTitle>Menü</SheetTitle>
              <SheetDescription>Navigation, Login und Registrierung.</SheetDescription>
              <nav className="mt-8 flex flex-col gap-2" aria-label="Mobil">
                {siteConfig.nav.map((item) => {
                  const current = isCurrent(pathname, item.href)
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      aria-current={current ? "page" : undefined}
                      onClick={() => setOpen(false)}
                      className={cn(
                        "border-2 border-line px-3 py-3 font-display text-3xl uppercase leading-none",
                        current && "bg-signal text-charcoal",
                      )}
                    >
                      {item.label}
                    </Link>
                  )
                })}
              </nav>
              <div className="mt-6 flex flex-col gap-3">
                <Button asChild variant="outline">
                  <Link href="/login" onClick={() => setOpen(false)}>
                    Login
                  </Link>
                </Button>
                <Button asChild>
                  <Link href="/registrieren" onClick={() => setOpen(false)}>
                    Registrieren
                  </Link>
                </Button>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  )
}
