"use client"

import { Moon, Sun } from "lucide-react"
import { siteConfig } from "@/lib/siteConfig"

export function ThemeToggle() {
  function toggle() {
    const isDark = document.documentElement.classList.contains("dark")
    const next = isDark ? "light" : "dark"
    document.documentElement.classList.toggle("dark", next === "dark")
    try {
      localStorage.setItem(siteConfig.themeStorageKey, next)
    } catch {
      /* private mode */
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      className="lift inline-flex size-11 items-center justify-center border-2 border-line bg-transparent text-ink"
      aria-label="Farbschema umschalten"
    >
      <Sun className="hidden size-5 dark:block" aria-hidden />
      <Moon className="size-5 dark:hidden" aria-hidden />
    </button>
  )
}
