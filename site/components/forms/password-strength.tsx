import { ratePassword } from "@/lib/password"

export function PasswordStrength({ password, id }: { password: string; id: string }) {
  const rating = ratePassword(password)
  return (
    <div id={id}>
      <div className="flex gap-1" aria-hidden>
        {[1, 2, 3].map((step) => (
          <span
            key={step}
            className={step <= rating.score ? "h-2 flex-1 bg-charcoal dark:bg-paper" : "h-2 flex-1 border-2 border-line"}
          />
        ))}
      </div>
      <p className="mt-1 text-sm font-semibold text-ink">Passwortstärke: {rating.label}</p>
    </div>
  )
}
