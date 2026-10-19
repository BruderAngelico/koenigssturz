"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import Link from "next/link"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { authCopy } from "@/content/pages"
import { requestPasswordReset, signIn } from "@/lib/auth"
import { loginSchema, resetSchema, type LoginValues, type ResetValues } from "@/lib/auth/schema"

export function LoginForm() {
  const [status, setStatus] = useState<string | null>(null)
  const [forgot, setForgot] = useState(false)
  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  })
  const resetForm = useForm<ResetValues>({
    resolver: zodResolver(resetSchema),
    defaultValues: { email: "" },
  })

  async function onSubmit(values: LoginValues) {
    setStatus(null)
    const result = await signIn(values)
    setStatus(result.ok ? "Abgemeldet." : result.message)
  }

  async function onReset(values: ResetValues) {
    setStatus(null)
    const result = await requestPasswordReset(values.email)
    setStatus(result.ok ? "Erledigt." : result.message)
  }

  return (
    <div className="grid gap-8">
      <Form {...form}>
        <form noValidate onSubmit={form.handleSubmit(onSubmit)} className="grid gap-4">
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
                  <Input {...field} type="password" autoComplete="current-password" />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button type="submit" className="w-full">
            Einloggen
          </Button>
        </form>
      </Form>

      <div>
        <button
          type="button"
          className="font-semibold underline decoration-2 underline-offset-4"
          aria-expanded={forgot}
          onClick={() => setForgot((value) => !value)}
        >
          {authCopy.login.forgot}
        </button>
        {forgot ? (
          <Form {...resetForm}>
            <form noValidate onSubmit={resetForm.handleSubmit(onReset)} className="mt-4 grid gap-3">
              <FormField
                control={resetForm.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>E-Mail für den Reset</FormLabel>
                    <FormControl>
                      <Input {...field} type="email" autoComplete="email" />
                    </FormControl>
                    <p className="text-sm text-muted">{authCopy.login.forgotHelp}</p>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button type="submit" variant="outline">
                Link anfordern
              </Button>
            </form>
          </Form>
        ) : null}
      </div>

      <p role="status" className="min-h-6 text-sm font-semibold">
        {status}
      </p>
      <Link href="/registrieren" className="font-semibold underline decoration-2 underline-offset-4">
        {authCopy.login.toRegister}
      </Link>
    </div>
  )
}
