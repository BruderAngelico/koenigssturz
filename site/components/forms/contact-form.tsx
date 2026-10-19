"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { formCopy } from "@/content/pages"
import { contactSchema, type ContactValues } from "@/lib/forms"

export function ContactForm({ defaultTopic = "" }: { defaultTopic?: string }) {
  const [status, setStatus] = useState<string | null>(null)
  const form = useForm<ContactValues>({
    resolver: zodResolver(contactSchema),
    defaultValues: { name: "", email: "", topic: defaultTopic, message: "", notice: false },
  })

  function onSubmit() {
    setStatus("Nichts wurde gesendet. Das Formular ist noch nicht angebunden.")
    form.reset({ name: "", email: "", topic: defaultTopic, message: "", notice: false })
  }

  return (
    <Form {...form}>
      <form noValidate onSubmit={form.handleSubmit(onSubmit)} className="grid gap-4">
        <p className="border-l-4 border-resistance bg-surface-2 px-3 py-3 text-sm font-semibold">{formCopy.disconnected}</p>
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Name oder Spitzname</FormLabel>
              <FormControl>
                <Input {...field} autoComplete="nickname" />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>E-Mail</FormLabel>
              <FormControl>
                <Input {...field} type="email" autoComplete="email" />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="topic"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Thema</FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="message"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Nachricht</FormLabel>
              <FormControl>
                <Textarea {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="notice"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-start gap-3">
                <FormControl>
                  <Checkbox checked={field.value} onCheckedChange={(value) => field.onChange(value === true)} />
                </FormControl>
                <FormLabel>Ich habe gelesen, dass hier noch nichts ankommt.</FormLabel>
              </div>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit">Nachricht prüfen</Button>
        <p role="status" className="min-h-6 text-sm font-semibold">
          {status}
        </p>
      </form>
    </Form>
  )
}
