import { LoginForm } from "@/components/forms/login-form"
import { authCopy } from "@/content/pages"
import { pageMeta } from "@/lib/seo"

export const metadata = pageMeta(authCopy.login.title, authCopy.login.description, false)

export default function LoginPage() {
  return (
    <div className="mx-auto grid max-w-6xl gap-10 px-4 py-14 sm:px-6 lg:grid-cols-2 lg:py-20">
      <div>
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-brand">Konto</p>
        <h1 className="mt-3 font-display text-5xl uppercase leading-[0.86] sm:text-7xl">{authCopy.login.title}</h1>
        <p className="mt-6 max-w-md text-lg leading-relaxed">{authCopy.login.lede}</p>
      </div>
      <div className="border-2 border-line bg-surface p-5 shadow-hard sm:p-8">
        <LoginForm />
      </div>
    </div>
  )
}
