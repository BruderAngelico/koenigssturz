"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { formCopy } from "@/content/pages"
import { newsletterSchema, type NewsletterValues } from "@/lib/forms"

export function NewsletterForm() {
  const [status, setStatus] = useState<string | null>(null)
  const form = useForm<NewsletterValues>({
    resolver: zodResolver(newsletterSchema),
    defaultValues: { email: "" },
  })

  function onSubmit() {
    setStatus("Noch nicht angebunden. Die Adresse wurde nicht gespeichert.")
    form.reset()
  }

  return (
    <Form {...form}>
      <form noValidate onSubmit={form.handleSubmit(onSubmit)} className="grid gap-3">
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel className="text-paper">E-Mail für den Newsletter</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  type="email"
                  autoComplete="email"
                  placeholder="name@example.org"
                  className="bg-paper text-charcoal placeholder:text-[#3a342c]"
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <p className="text-sm text-paper/90">{formCopy.newsletter}</p>
        <Button type="submit" variant="inverse" className="w-full sm:w-auto">
          Eintragen
        </Button>
        <p role="status" className="min-h-5 text-sm font-semibold text-paper">
          {status}
        </p>
      </form>
    </Form>
  )
}
