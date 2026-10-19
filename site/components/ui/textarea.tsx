import * as React from "react"
import { cn } from "@/lib/utils"

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        "min-h-32 w-full border-2 border-line bg-surface px-3 py-2 text-base text-ink shadow-hard-sm placeholder:text-muted",
        className,
      )}
      {...props}
    />
  )
}

export { Textarea }
