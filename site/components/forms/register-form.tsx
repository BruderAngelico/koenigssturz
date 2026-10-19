"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import Link from "next/link"
import { useId, useState } from "react"
import { useForm } from "react-hook-form"
import { PasswordStrength } from "@/components/forms/password-strength"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { authCopy } from "@/content/pages"
import { signUp } from "@/lib/auth"
import { registerSchema, type RegisterValues } from "@/lib/auth/schema"

export function RegisterForm() {
  const strengthId = useId()
  const [status, setStatus] = useState<string | null>(null)
  const form = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: { name: "", email: "", password: "", age: false, rules: false },
  })
  const password = form.watch("password")
  const errors = form.formState.errors
  const errorSummary = Object.values(errors)
    .map((error) => error?.message)
    .filter((message): message is string => Boolean(message))

  async function onSubmit(values: RegisterValues) {
    setStatus(null)
    const result = await signUp(values)
    setStatus(result.ok ? "Konto angelegt." : result.message)
  }

  return (
    <Form {...form}>
      <form noValidate onSubmit={form.handleSubmit(onSubmit)} className="grid gap-4">
        {errorSummary.length > 0 ? (
          <div role="alert" className="border-l-4 border-resistance bg-surface-2 px-3 py-3">
            <p className="font-semibold">Bitte prüfe die markierten Felder.</p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
              {errorSummary.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          </div>
        ) : null}

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
                <Input {...field} type="email" autoComplete="email" inputMode="email" />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Passwort</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  type="password"
                  autoComplete="new-password"
                  aria-describedby={strengthId}
                />
              </FormControl>
              <PasswordStrength id={strengthId} password={password ?? ""} />
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="age"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-start gap-3">
                <FormControl>
                  <Checkbox checked={field.value} onCheckedChange={(value) => field.onChange(value === true)} />
                </FormControl>
                <FormLabel>Ich bin mindestens 18 Jahre alt</FormLabel>
              </div>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="rules"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-start gap-3">
                <FormControl>
                  <Checkbox checked={field.value} onCheckedChange={(value) => field.onChange(value === true)} />
                </FormControl>
                <FormLabel>Ich akzeptiere die Regeln und den Safer-Space-Kodex</FormLabel>
              </div>
              <FormMessage />
            </FormItem>
          )}
        />

        <p className="text-sm leading-relaxed text-muted">
          {authCopy.register.privacy}{" "}
          <Link href="/datenschutz" className="font-semibold text-ink underline decoration-2 underline-offset-4">
            Datenschutz
          </Link>
        </p>

        <Button type="submit">Konto anlegen</Button>
        <p role="status" className="min-h-6 text-sm font-semibold">
          {status}
        </p>
        <Link href="/login" className="font-semibold underline decoration-2 underline-offset-4">
          Schon dabei? Login
        </Link>
      </form>
    </Form>
  )
}
