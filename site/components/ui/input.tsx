import * as React from "react"
import { cn } from "@/lib/utils"

function Input({ className, type = "text", ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      className={cn(
        "h-11 w-full border-2 border-line bg-surface px-3 text-base text-ink shadow-hard-sm placeholder:text-muted",
        className,
      )}
      {...props}
    />
  )
}

export { Input }
