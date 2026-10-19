"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import { siteConfig } from "@/lib/siteConfig"

export function AgeGate() {
  const [open, setOpen] = useState(false)
  const [blocked, setBlocked] = useState(false)

  useEffect(() => {
    try {
      if (localStorage.getItem(siteConfig.ageStorageKey) !== "1") setOpen(true)
    } catch {
      setOpen(true)
    }
  }, [])

  function confirm() {
    try {
      localStorage.setItem(siteConfig.ageStorageKey, "1")
    } catch {
      /* still let the person continue in this tab */
    }
    setOpen(false)
  }

  if (!open) return null

  return (
    <Dialog open={open}>
      <DialogContent
        onPointerDownOutside={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
        onEscapeKeyDown={(event) => event.preventDefault()}
      >
        <p className="text-sm font-semibold uppercase tracking-[0.16em] text-brand">18+</p>
        <DialogTitle className="mt-2">Erst ab 18.</DialogTitle>
        <DialogDescription className="mt-4">
          {siteConfig.name} richtet sich an Erwachsene. Das hier ist eine politische Gemeinschaft, kein
          Shop. Die Bestätigung bleibt in diesem Browser, ohne Cookie und ohne Konto.
        </DialogDescription>
        {blocked ? (
          <p role="alert" className="mt-4 border-l-4 border-resistance bg-surface-2 px-3 py-3 text-sm font-semibold">
            Diese Seiten sind für Erwachsene. Wenn du jemanden zum Reden brauchst: Telefonseelsorge
            0800 111 0 111.
          </p>
        ) : null}
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <Button type="button" onClick={confirm}>
            Ich bin mindestens 18
          </Button>
          <Button type="button" variant="outline" onClick={() => setBlocked(true)}>
            Ich bin jünger
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
